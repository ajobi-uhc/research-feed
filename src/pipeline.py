"""Orchestration: onboard, discover, rerank.

Each is a self-contained async function. Triggers come from app.py routes
(or future CLI). Progress messages stream to the running page via progress.log.
"""

from __future__ import annotations
import asyncio
import json
from datetime import date, datetime, timedelta

from .config import (
    TODAY, WINDOW_DAYS, CHEAP_FILTER_KEEP_TARGET, RANKER_BATCH_SIZE,
)
from .models import Profile, Item, Ranking
from . import store, sources, llm, progress


# Feedback mechanism intentionally not implemented. See app.py note.


# ─── Onboarding ──────────────────────────────────────────────────────
async def onboard(
    seed_papers: list[str],
    scholar_url: str | None,
    followed_authors: list[str],
    current_question: str,
    filter_outs: list[str],
) -> Profile:
    """Build the profile, save it, generate + cache queries.
    Caller can immediately call discover() afterwards."""
    progress.log("onboard: starting", phase="onboard")
    profile_dict = await llm.draft_profile(
        seed_papers=seed_papers,
        scholar_url=scholar_url,
        followed_authors=followed_authors,
        current_question=current_question,
        filter_outs=filter_outs,
    )
    p = Profile.from_dict(profile_dict)
    store.save_profile(p)
    progress.log(f"onboard: profile v{p.version_hash} saved")

    # Pre-generate queries so the first discover() doesn't pay this cost
    queries = await llm.generate_queries(p.to_dict())
    store.save_queries(p.version_hash, queries)
    return p


# ─── Discovery ───────────────────────────────────────────────────────
async def discover() -> int:
    """Full discovery: fetch → dedup → save items → cheap filter → rank → save rankings."""
    store.init()
    profile = store.load_profile()
    if profile is None:
        raise RuntimeError("No profile — run /onboard first")

    queries = store.load_queries(profile.version_hash)
    if not queries:
        progress.log("discover: query cache miss, regenerating")
        queries = await llm.generate_queries(profile.to_dict())
        store.save_queries(profile.version_hash, queries)

    end = date.fromisoformat(TODAY)
    start = (end - timedelta(days=WINDOW_DAYS - 1)).isoformat()
    end_s = end.isoformat()
    progress.log(f"discover: window {start} → {end_s}", phase="fetch")

    # Fetch four streams in parallel
    papers, by_authors, forum, labs = await asyncio.gather(
        sources.fetch_papers(queries, start, end_s),
        sources.fetch_followed_authors(profile.followed_author_names(), start, end_s),
        sources.fetch_forum(start, end_s),
        sources.fetch_lab_blogs(start, end_s),
    )
    all_items = papers + by_authors + forum + labs
    progress.log(f"discover: {len(papers)} papers + {len(by_authors)} by-author + "
                  f"{len(forum)} forum + {len(labs)} labs = {len(all_items)} raw")

    if not all_items:
        progress.log("discover: nothing fetched, stopping")
        return 0

    deduped = sources.dedup(all_items)
    progress.log(f"discover: {len(deduped)} after dedup")

    # Persist items (immutable)
    with store.conn() as c:
        for it in deduped:
            store.upsert_item(it, c)

    return await _filter_and_rank(profile, deduped)


# ─── Re-rank (no fetch) ──────────────────────────────────────────────
async def rerank() -> int:
    """Re-run filter+rank over items already in DB for the current profile_version.
    Triggered after a profile edit — no re-fetching."""
    profile = store.load_profile()
    if profile is None:
        raise RuntimeError("No profile")

    end = date.fromisoformat(TODAY)
    start = (end - timedelta(days=WINDOW_DAYS - 1)).isoformat()
    items = store.items_in_window(start, end.isoformat(), limit=2000)
    progress.log(f"rerank: {len(items)} items in window to re-rank", phase="rerank")
    if not items:
        return 0
    return await _filter_and_rank(profile, items)


# ─── Shared filter + rank ───────────────────────────────────────────
async def _filter_and_rank(profile: Profile, items: list[Item]) -> int:
    """Run cheap filter (with followed-author bypass), then batched ranker.
    Writes Ranking rows for the current profile_version."""
    profile_dict = profile.to_dict()

    # Followed-author items skip the cheap filter
    author_items = [it for it in items if it.discovered_via and it.discovered_via.startswith("author:")]
    other_items = [it for it in items if not (it.discovered_via and it.discovered_via.startswith("author:"))]

    keep_set = set()
    if other_items:
        item_dicts = [_item_to_filter_dict(it) for it in other_items]
        keep_set = await llm.cheap_filter(profile_dict, item_dicts,
                                            keep_target=CHEAP_FILTER_KEEP_TARGET)

    survivors = [it for it in other_items if it.id in keep_set] + author_items
    progress.log(f"_filter_and_rank: {len(survivors)} survive (cheap_filter + bypass)")

    # Cap if needed; prioritize author items
    if len(survivors) > CHEAP_FILTER_KEEP_TARGET:
        survivors.sort(
            key=lambda it: (
                0 if (it.discovered_via or "").startswith("author:") else 1,
                it.date or "",
            ),
            reverse=False,
        )
        survivors.sort(
            key=lambda it: (it.discovered_via or "").startswith("author:"),
            reverse=True,
        )
        survivors = survivors[:CHEAP_FILTER_KEEP_TARGET]

    # Rank in batches
    progress.log(f"_filter_and_rank: ranking {len(survivors)} in batches of {RANKER_BATCH_SIZE}",
                  phase="rank")
    ranked_rows: list[Ranking] = []
    now = datetime.utcnow().isoformat(timespec="seconds")
    n_batches = (len(survivors) + RANKER_BATCH_SIZE - 1) // RANKER_BATCH_SIZE
    bucket_totals = {"core": 0, "adjacent": 0, "peripheral": 0, "off-topic": 0}
    for i in range(0, len(survivors), RANKER_BATCH_SIZE):
        batch = survivors[i:i + RANKER_BATCH_SIZE]
        batch_dicts = [_item_to_ranker_dict(it) for it in batch]
        try:
            results = await llm.rank_batch(profile_dict, batch_dicts)
        except Exception as e:
            progress.log(f"_filter_and_rank: batch {i//RANKER_BATCH_SIZE+1} failed: {e!r}")
            continue
        batch_buckets = {"core": 0, "adjacent": 0, "peripheral": 0, "off-topic": 0}
        for r in results:
            iid = r.get("id")
            if not iid:
                continue
            bucket = r.get("relevance", "off-topic")
            batch_buckets[bucket] = batch_buckets.get(bucket, 0) + 1
            bucket_totals[bucket] = bucket_totals.get(bucket, 0) + 1
            ranked_rows.append(Ranking(
                item_id=iid,
                profile_version=profile.version_hash,
                bucket=bucket,
                reasons=r.get("reasons", []) or [],
                why=r.get("why", ""),
                novelty=r.get("novelty"),
                ranked_at=now,
            ))
        dist = f"{batch_buckets['core']}c/{batch_buckets['adjacent']}a/{batch_buckets['peripheral']}p/{batch_buckets['off-topic']}o"
        progress.log(f"_filter_and_rank: batch {i//RANKER_BATCH_SIZE+1}/{n_batches} → {dist}")

    with store.conn() as c:
        for r in ranked_rows:
            store.upsert_ranking(r, c)
    surf = bucket_totals["core"] + bucket_totals["adjacent"]
    progress.log(
        f"_filter_and_rank: done — {bucket_totals['core']} core, "
        f"{bucket_totals['adjacent']} adjacent, {bucket_totals['peripheral']} peripheral, "
        f"{bucket_totals['off-topic']} off-topic. Feed will show {surf} items."
    )
    return len(ranked_rows)


def _item_to_filter_dict(it: Item) -> dict:
    return {
        "id": it.id, "title": it.title,
        "authors": it.authors, "description": it.description,
        "venue": it.venue, "date": it.date,
        "publication_venue": it.publication_venue,
    }


def _item_to_ranker_dict(it: Item) -> dict:
    return {
        "id": it.id, "title": it.title,
        "abstract": (it.description or "")[:500],
        "authors": it.authors[:8],
        "affiliations": (it.affiliations or [])[:4],
        "venue": it.venue, "date": it.date,
        "publication_venue": it.publication_venue,
        "citation_count": it.citation_count,
        "af_karma": it.af_karma, "af_comments": it.af_comments,
        "recent_comment_count": it.recent_comment_count,
        "discovered_via": it.discovered_via,
    }
