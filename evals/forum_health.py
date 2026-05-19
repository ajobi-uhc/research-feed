"""Health check for the Alignment Forum fetch.

Calls sources.fetch_forum() against the current window and validates:
  • items come back at all
  • each item has karma + comment count
  • the two passes (by-date, by-activity) both contribute
  • karma threshold is respected

Run:
    uv run python -m evals.forum_health
"""

from __future__ import annotations
import asyncio
import sys
from datetime import date, timedelta

from src import sources
from src.config import TODAY, WINDOW_DAYS, AF_MIN_KARMA


async def main():
    end = date.fromisoformat(TODAY)
    start = (end - timedelta(days=WINDOW_DAYS - 1)).isoformat()
    end_s = end.isoformat()

    print(f"\nForum health check — window {start} → {end_s}, min karma {AF_MIN_KARMA}\n")

    try:
        items = await sources.fetch_forum(start, end_s)
    except Exception as e:
        print(f"  ✗ fetch_forum raised: {e!r}")
        sys.exit(1)

    print(f"Total items returned: {len(items)}\n")
    if not items:
        print("  ✗ FAIL — got 0 items. Either GreaterWrong layout changed, "
              "rate-limited, or window is genuinely empty.")
        sys.exit(1)

    failures = []
    by_pass = {"forum:date": 0, "forum:activity": 0, "other": 0}
    no_karma = []
    no_url = []
    out_of_window = []
    karma_below = []

    for it in items:
        # by-date items must be in window
        pass_kind = "other"
        if it.discovered_via == "forum:date":
            pass_kind = "forum:date"
            if not (start <= it.date <= end_s):
                out_of_window.append(it)
        elif it.discovered_via == "forum:activity":
            pass_kind = "forum:activity"
            # activity-pass items can be out of window (older posts with new comments)
        by_pass[pass_kind] = by_pass.get(pass_kind, 0) + 1
        if it.af_karma is None:
            no_karma.append(it)
        elif it.af_karma < AF_MIN_KARMA:
            karma_below.append(it)
        if not it.url.startswith("http"):
            no_url.append(it)

    # ── Per-pass counts
    print(f"By pass:")
    print(f"  forum:date     → {by_pass['forum:date']}  (publication-date pass)")
    print(f"  forum:activity → {by_pass['forum:activity']}  (recent-comment-activity pass)")
    if by_pass['other']:
        print(f"  other          → {by_pass['other']}  (unexpected — should be 0)")
        failures.append("some items missing discovered_via tag")
    print()

    # ── Karma + url checks
    if no_karma:
        failures.append(f"{len(no_karma)} items missing karma (parser regression?)")
        print(f"  ✗ {len(no_karma)} items have NULL karma. Examples:")
        for it in no_karma[:3]:
            print(f"      {it.title[:70]} ({it.url})")
    else:
        print(f"  ✓ all items have karma")

    if karma_below:
        failures.append(f"{len(karma_below)} items below karma threshold {AF_MIN_KARMA}")
        print(f"  ✗ {len(karma_below)} items below threshold — parser bug")
    else:
        print(f"  ✓ no items below karma threshold")

    if out_of_window:
        failures.append(f"{len(out_of_window)} by-date items out of window")
        print(f"  ✗ {len(out_of_window)} by-date items out of window")
    else:
        print(f"  ✓ all by-date items in window")

    if no_url:
        failures.append(f"{len(no_url)} items have invalid URL")

    # ── Top items by karma
    print(f"\nTop 5 by karma:")
    for it in sorted(items, key=lambda i: -(i.af_karma or 0))[:5]:
        comments = it.af_comments or 0
        recent = f", {it.recent_comment_count} recent" if it.recent_comment_count else ""
        print(f"  ⬆{it.af_karma:>4} ({comments} cmts{recent})  {it.title[:70]}")
        print(f"        {it.date}  {it.url}")

    # ── If activity pass returned no items, suspicious
    print()
    if by_pass['forum:activity'] == 0:
        print(f"  ⚠ forum:activity pass returned 0 items.")
        print(f"    Could be: GreaterWrong layout changed for `sortedBy=topComments`, "
              f"OR no AF posts had recent comment activity (unlikely).")
        failures.append("forum:activity pass returned 0 items")
    elif by_pass['forum:activity'] < 3:
        print(f"  ⚠ forum:activity pass returned only {by_pass['forum:activity']} items.")

    # ── Summary
    print()
    if failures:
        print(f"FAIL — {len(failures)} issues:")
        for f in failures:
            print(f"  • {f}")
        sys.exit(1)
    print("PASS — AF fetch healthy.")


if __name__ == "__main__":
    asyncio.run(main())
