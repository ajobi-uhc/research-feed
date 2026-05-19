"""End-to-end test: gather_sources (lab/org pages, feed-first + HTML fallback)."""

from __future__ import annotations
import asyncio
from datetime import date, timedelta

from src import agents
from src.config import TODAY
from ._common import TestResult, load_profile, check


async def run() -> TestResult:
    r = TestResult("sources: gather_sources (mech-interp fixture, 30-day window)")
    profile = load_profile("profile_mech_interp")
    end = date.fromisoformat(TODAY)
    start = (end - timedelta(days=29)).isoformat()
    end_str = end.isoformat()
    print(f"  window: {start} → {end_str}")
    print(f"  sources: {[s.slug for s in profile.sources if s.trust >= 25]}")

    try:
        items = await agents.gather_sources(profile, start, end_str)
    except Exception as e:
        r.fail(f"gather_sources raised: {e!r}")
        return r

    if not items:
        r.warn("0 items returned — sources may be down or HTML format changed")
        return r

    r.ok(f"got {len(items)} items across sources")

    # No items should be from arxiv/AF domains (those should be filtered)
    from urllib.parse import urlparse
    bad_hosts = ["arxiv.org", "alignmentforum.org", "lesswrong.com"]
    leaked = [it for it in items
               if any(h in (urlparse(it.url).netloc or "").lower() for h in bad_hosts)]
    check(r, not leaked, "no arxiv/AF/LW items leaked into sources",
          f"{len(leaked)} items from skip-list domains: {[it.url for it in leaked[:3]]}")

    # All in window
    out_of_window = [it for it in items if not (start <= it.date <= end_str)]
    check(r, not out_of_window, "all items dated in window",
          f"{len(out_of_window)} out of window")

    # Group by venue for visibility
    by_venue: dict[str, int] = {}
    for it in items:
        by_venue[it.venue] = by_venue.get(it.venue, 0) + 1
    print("  items per source:")
    for v, n in sorted(by_venue.items(), key=lambda x: -x[1]):
        print(f"    · {v}: {n}")

    # At least 2 sources contributed something (otherwise the multi-source pipeline isn't really working)
    if len(by_venue) >= 2:
        r.ok(f"items came from {len(by_venue)} distinct sources")
    else:
        r.warn(f"only {len(by_venue)} source contributed — likely many sites failed")

    return r


if __name__ == "__main__":
    res = asyncio.run(run())
    res.print()
