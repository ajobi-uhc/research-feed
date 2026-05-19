"""End-to-end test: gather_forum (Alignment Forum via GreaterWrong)."""

from __future__ import annotations
import asyncio
from datetime import date, timedelta

from src import agents
from src.config import TODAY
from ._common import TestResult, check


async def run() -> TestResult:
    r = TestResult("forum: gather_forum (AF, 30-day window)")
    end = date.fromisoformat(TODAY)
    start = (end - timedelta(days=29)).isoformat()
    end_str = end.isoformat()
    print(f"  window: {start} → {end_str}  (min_karma=25)")

    try:
        items = await agents.gather_forum(start, end_str, min_karma=25)
    except Exception as e:
        r.fail(f"gather_forum raised: {e!r}")
        return r

    if not items:
        r.warn("0 items — either no AF posts in window or GreaterWrong format changed")
        return r

    r.ok(f"got {len(items)} items")

    bad = [it for it in items if not (it.title and it.url and it.venue == "alignment_forum")]
    check(r, not bad, "all items have title+url and venue=alignment_forum",
          f"{len(bad)} items malformed")

    no_karma = [it for it in items if it.af_karma is None]
    check(r, not no_karma, "all items have af_karma",
          f"{len(no_karma)} items missing karma")

    below_threshold = [it for it in items if it.af_karma is not None and it.af_karma < 25]
    check(r, not below_threshold, "no items below karma threshold of 25",
          f"{len(below_threshold)} items below threshold (parser bug)")

    out_of_window = [it for it in items if not (start <= it.date <= end_str)]
    check(r, not out_of_window, f"all items in window",
          f"{len(out_of_window)} out of window")

    print("  sample top 3 by karma:")
    for it in sorted(items, key=lambda i: -(i.af_karma or 0))[:3]:
        print(f"    · {it.title[:80]}  ⬆{it.af_karma}  {it.date}")

    return r


if __name__ == "__main__":
    res = asyncio.run(run())
    res.print()
