"""End-to-end test: gather_arxiv against the mech-interp fixture profile.

Hits the real arXiv API. Skips gracefully if rate-limited.
"""

from __future__ import annotations
import asyncio
from datetime import date, timedelta

from src import agents
from src.config import TODAY
from ._common import TestResult, load_profile, check


async def run() -> TestResult:
    r = TestResult("arxiv: gather_arxiv (mech-interp fixture)")
    profile = load_profile("profile_mech_interp")

    # 30-day window ending TODAY
    end = date.fromisoformat(TODAY)
    start = (end - timedelta(days=29)).isoformat()
    end_str = end.isoformat()

    print(f"  window: {start} → {end_str}")
    print(f"  keywords: {profile.keywords[:4]}…")
    print(f"  trusted_authors: {[a.name for a in profile.trusted_authors[:4]]}…")

    # Run the pipeline
    try:
        items = await agents.gather_arxiv(profile, start, end_str)
    except Exception as e:
        r.fail(f"gather_arxiv raised: {e!r}")
        return r

    if not items:
        # Distinguish "rate limited" from "no results"
        r.warn("gather_arxiv returned 0 items (likely rate-limited or no in-window matches)")
        return r

    # ── Functional assertions ─────────────────────────
    r.ok(f"got {len(items)} items back")

    # All items have minimum required metadata.
    # Note: arxiv_id may be None for S2 papers that aren't on arXiv (workshop, journal, etc.) — that's OK.
    bad = [it for it in items if not (it.title and it.url)]
    check(r, not bad, "all items have title + url",
          f"{len(bad)} items missing title or url: {[it.title for it in bad[:3]]}")
    has_arxiv = sum(1 for it in items if it.arxiv_id)
    print(f"  with arxiv_id: {has_arxiv}/{len(items)} ({100*has_arxiv/len(items):.0f}%)")

    # All in window
    out_of_window = [it for it in items if not (start <= it.date <= end_str)]
    check(r, not out_of_window, f"all items dated in window {start}..{end_str}",
          f"{len(out_of_window)} items out of window: {[(it.title[:40], it.date) for it in out_of_window[:3]]}")

    # No duplicate item ids (different items must have different Item.id)
    item_ids = [it.id for it in items]
    check(r, len(item_ids) == len(set(item_ids)), "no duplicate item ids",
          f"found {len(item_ids) - len(set(item_ids))} duplicate ids")
    # No duplicate arxiv_ids among items that DO have one
    arxiv_ids = [it.arxiv_id for it in items if it.arxiv_id]
    check(r, len(arxiv_ids) == len(set(arxiv_ids)),
          "no duplicate arxiv_ids (among papers with one)",
          f"found {len(arxiv_ids) - len(set(arxiv_ids))} duplicate arxiv_ids")

    bad_venue = [it for it in items if it.venue != "arxiv_standalone"]
    check(r, not bad_venue, "all items tagged venue=arxiv_standalone",
          f"{len(bad_venue)} have wrong venue: {[(it.title[:30], it.venue) for it in bad_venue[:3]]}")
    # publication_venue is metadata
    with_pv = [it for it in items if it.publication_venue]
    print(f"  with publication_venue: {len(with_pv)}/{len(items)}")
    if with_pv:
        sample_venues = sorted({it.publication_venue for it in with_pv})
        print(f"  sample pub venues: {sample_venues[:6]}")

    # ── Quality assertions (soft) ─────────────────────
    # For a mech-interp profile, at least some items should be obviously on-topic.
    on_topic_terms = [
        "interpret", "sparse autoencoder", "sae", "feature", "circuit",
        "activation", "polysemanticity", "superposition",
    ]
    on_topic = [
        it for it in items
        if any(t in (it.title + " " + it.description).lower() for t in on_topic_terms)
    ]
    on_topic_pct = 100 * len(on_topic) / len(items)
    print(f"  on-topic (mech-interp terms in title/abs): {len(on_topic)}/{len(items)} ({on_topic_pct:.0f}%)")
    if on_topic_pct >= 30:
        r.ok(f"≥30% items match mech-interp vocab ({on_topic_pct:.0f}%)")
    elif on_topic_pct >= 15:
        r.warn(f"only {on_topic_pct:.0f}% match mech-interp vocab (target ≥30%)")
    else:
        r.fail(f"only {on_topic_pct:.0f}% match mech-interp vocab — triage is letting noise through")

    # At least one item should be by a trusted author (if profile has any in-field authors active)
    trusted_last = [a.name.lower().split()[-1] for a in profile.trusted_authors]
    by_trusted = [
        it for it in items
        if any(t in author.lower() for t in trusted_last for author in it.authors)
    ]
    if by_trusted:
        r.ok(f"{len(by_trusted)} items by trusted-list authors (e.g. {by_trusted[0].authors[0]})")
    else:
        r.warn("no items by trusted-list authors — may be a quiet month or triage missed them")

    # Sample output for visual sanity
    print("  sample top 3:")
    for it in items[:3]:
        authors_str = ", ".join(it.authors[:2]) + (f" +{len(it.authors)-2}" if len(it.authors) > 2 else "")
        print(f"    · {it.title[:80]}")
        print(f"      {it.date} · {authors_str} · {it.arxiv_id}")

    return r


if __name__ == "__main__":
    res = asyncio.run(run())
    res.print()
