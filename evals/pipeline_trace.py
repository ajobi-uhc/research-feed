"""Step through the discovery pipeline against the current profile, printing
intermediate state at each stage. Real LLM calls — costs ~$0.50 per full run.

Useful for: 'why isn't paper X in my feed?' and 'is the cheap filter doing its job?'

Run:
    uv run python -m evals.pipeline_trace                # full pipeline (~3 min, ~$0.50)
    uv run python -m evals.pipeline_trace --fetch-only   # skip LLM steps (~90s, ~$0)
    uv run python -m evals.pipeline_trace --no-rank      # cheap_filter only, skip ranker
"""

from __future__ import annotations
import argparse
import asyncio
import sys
from datetime import date, timedelta

from src import store, sources, llm, pipeline
from src.config import TODAY, WINDOW_DAYS, RANKER_BATCH_SIZE


def hr(s: str = ""):
    print(f"\n{'═'*72}")
    if s:
        print(f"  {s}")
        print(f"{'═'*72}")


def section(s: str):
    print(f"\n── {s} ───────────────────────────────────────────")


async def main(args):
    profile = store.load_profile()
    if not profile:
        print("No profile. Run onboarding at http://localhost:8765/profile first.")
        sys.exit(1)

    end = date.fromisoformat(TODAY)
    start = (end - timedelta(days=WINDOW_DAYS - 1)).isoformat()
    end_s = end.isoformat()

    hr(f"Pipeline trace — window {start} → {end_s}")
    print(f"Profile v{profile.version_hash}")
    print(f"  summary: {profile.user_summary[:140]}…")
    print(f"  tags ({len(profile.tags)}): {', '.join(profile.tags[:6])}…")
    print(f"  followed authors ({len(profile.followed_authors)}): "
          f"{', '.join(a.name for a in profile.followed_authors[:5])}")

    queries = store.load_queries(profile.version_hash) or []
    section(f"Queries cached for this profile ({len(queries)})")
    for q in queries:
        print(f"  • {q}")
    if not queries:
        print("  ⚠ no queries cached — generating on the fly")
        queries = await llm.generate_queries(profile.to_dict())

    # ── Fetch each source ─────────────────────────────────────────
    section("Fetch (parallel: S2 keyword + S2 author + forum + lab blogs)")
    papers, by_authors, forum, labs = await asyncio.gather(
        sources.fetch_papers(queries, start, end_s),
        sources.fetch_followed_authors(profile.followed_author_names(), start, end_s),
        sources.fetch_forum(start, end_s),
        sources.fetch_lab_blogs(start, end_s),
    )

    section("Stream summaries")
    print(f"  S2 keyword search: {len(papers)} papers")
    for p in sorted(papers, key=lambda i: i.date or "", reverse=True)[:3]:
        print(f"      {p.date}  {p.title[:75]}")

    print(f"\n  S2 by-followed-author: {len(by_authors)} papers")
    for p in by_authors[:3]:
        print(f"      {p.date}  {p.title[:75]}  ({p.discovered_via})")

    print(f"\n  Alignment Forum: {len(forum)} posts")
    for p in sorted(forum, key=lambda i: -(i.af_karma or 0))[:3]:
        print(f"      ⬆{p.af_karma}  {p.title[:75]}")

    print(f"\n  Lab blogs (17 sources, mixed RSS + HTML+Haiku): {len(labs)} items")
    by_venue: dict[str, int] = {}
    for it in labs:
        by_venue[it.venue] = by_venue.get(it.venue, 0) + 1
    for v, n in sorted(by_venue.items(), key=lambda x: -x[1]):
        print(f"      {v}: {n}")

    section("Merge + dedup")
    all_items = papers + by_authors + forum + labs
    deduped = sources.dedup(all_items)
    print(f"  {len(all_items)} raw → {len(deduped)} unique")

    if args.fetch_only:
        print("\n[--fetch-only: stopping before LLM steps]")
        return

    # ── Cheap filter ──────────────────────────────────────────────
    section("Cheap filter (Haiku) — drops obviously off-topic")
    author_items = [it for it in deduped
                     if it.discovered_via and it.discovered_via.startswith("author:")]
    other_items = [it for it in deduped
                    if not (it.discovered_via and it.discovered_via.startswith("author:"))]
    print(f"  {len(author_items)} followed-author items bypass filter")
    print(f"  {len(other_items)} items go to Haiku")

    if other_items:
        filter_dicts = [{
            "id": it.id, "title": it.title,
            "authors": (it.authors or [])[:5],
            "description": (it.description or "")[:250],
            "venue": it.venue, "date": it.date,
            "publication_venue": it.publication_venue,
        } for it in other_items]
        keep_set = await llm.cheap_filter(profile.to_dict(), filter_dicts, keep_target=200)
    else:
        keep_set = set()

    kept = [it for it in other_items if it.id in keep_set]
    dropped = [it for it in other_items if it.id not in keep_set]
    print(f"  → kept {len(kept)} of {len(other_items)}")
    print(f"  → dropped {len(dropped)}")

    print(f"\n  Sample DROPPED items (should look obviously off-topic):")
    for it in dropped[:6]:
        v_extra = f" ({it.publication_venue})" if it.publication_venue else ""
        print(f"    ✗ {it.title[:78]}{v_extra}")
        print(f"        venue={it.venue}, authors={it.authors[:2]}")

    print(f"\n  Sample KEPT items (should be plausibly relevant):")
    for it in kept[:6]:
        v_extra = f" ({it.publication_venue})" if it.publication_venue else ""
        print(f"    ✓ {it.title[:78]}{v_extra}")
        print(f"        venue={it.venue}, authors={it.authors[:2]}")

    if args.no_rank:
        print("\n[--no-rank: stopping before ranker]")
        return

    # ── Ranker ────────────────────────────────────────────────────
    to_rank = kept + author_items
    section(f"Ranker (Sonnet) — {len(to_rank)} items, batched {RANKER_BATCH_SIZE}")

    bucket_totals = {"core": 0, "adjacent": 0, "peripheral": 0, "off-topic": 0}
    samples: dict[str, list] = {"core": [], "adjacent": [], "peripheral": [], "off-topic": []}
    n_batches = (len(to_rank) + RANKER_BATCH_SIZE - 1) // RANKER_BATCH_SIZE

    for i in range(0, len(to_rank), RANKER_BATCH_SIZE):
        batch = to_rank[i:i + RANKER_BATCH_SIZE]
        batch_dicts = [pipeline._item_to_ranker_dict(it) for it in batch]
        try:
            results = await llm.rank_batch(profile.to_dict(), batch_dicts)
        except Exception as e:
            print(f"  batch {i//RANKER_BATCH_SIZE+1} failed: {e!r}")
            continue
        batch_dist = {"core": 0, "adjacent": 0, "peripheral": 0, "off-topic": 0}
        for r in results:
            bucket = r.get("relevance", "off-topic")
            bucket_totals[bucket] = bucket_totals.get(bucket, 0) + 1
            batch_dist[bucket] = batch_dist.get(bucket, 0) + 1
            it = next((b for b in batch if b.id == r.get("id")), None)
            if it and len(samples[bucket]) < 3:
                samples[bucket].append((it, r))
        dist = f"{batch_dist['core']}c/{batch_dist['adjacent']}a/{batch_dist['peripheral']}p/{batch_dist['off-topic']}o"
        print(f"  batch {i//RANKER_BATCH_SIZE+1}/{n_batches} → {dist}")

    surfaced = bucket_totals["core"] + bucket_totals["adjacent"]
    print(f"\n  Totals: {bucket_totals['core']} core · {bucket_totals['adjacent']} adjacent · "
          f"{bucket_totals['peripheral']} peripheral · {bucket_totals['off-topic']} off-topic")
    print(f"  Feed will show {surfaced} items (core + adjacent).")

    # Per-bucket examples — show why decisions were made
    for bucket in ("core", "adjacent", "peripheral", "off-topic"):
        if not samples[bucket]:
            continue
        section(f"Sample {bucket}")
        for it, r in samples[bucket]:
            print(f"  • {it.title[:80]}")
            print(f"      via={it.discovered_via}, venue={it.venue}")
            why = r.get("why", "").strip()
            if why:
                print(f"      why: {why[:200]}")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--fetch-only", action="store_true",
                    help="Stop after fetch. No LLM calls. ~90s, ~$0.")
    p.add_argument("--no-rank", action="store_true",
                    help="Run cheap_filter but skip ranker. ~2 min, ~$0.20.")
    asyncio.run(main(p.parse_args()))
