"""Health check for the static source registry.

For each entry in config.PULL_SOURCES:
  1. Try the configured feed URL (if any) — does it parse as RSS/Atom?
  2. If no feed configured OR feed broken: try feed discovery (/feed, /rss, etc).
  3. Report items-in-window count.

Run:
    uv run python -m evals.sources_health
"""

from __future__ import annotations
import asyncio
import sys
from datetime import date, timedelta

import httpx
import feedparser

from src.config import PULL_SOURCES, TODAY, WINDOW_DAYS

# What the row reports for each source
PASS = "✓"
FAIL = "✗"
WARN = "⚠"


async def _try_url_as_feed(client: httpx.AsyncClient, url: str) -> dict | None:
    """Fetch URL; return parsed feed info if it's a real feed, else None."""
    try:
        r = await client.get(url)
    except Exception as e:
        return {"error": f"fetch failed: {e}"}
    if r.status_code != 200:
        return {"error": f"HTTP {r.status_code}"}
    ct = r.headers.get("content-type", "").lower()
    body = r.text[:200].strip()
    # Heuristic: must be xml/rss/atom content type OR look like XML
    looks_xml = ("xml" in ct or "rss" in ct or "atom" in ct
                  or body.startswith("<?xml") or body.startswith("<rss")
                  or body.startswith("<feed"))
    if not looks_xml:
        return {"error": f"returned HTML (content-type={ct.split(';')[0]})"}
    parsed = feedparser.parse(r.content)
    if not parsed.entries:
        return {"error": "feedparser returned 0 entries"}
    return {"entries": parsed.entries, "title": parsed.feed.get("title", "")}


def _count_in_window(entries, ws: str, we: str) -> int:
    n = 0
    for e in entries:
        d = e.get("published_parsed") or e.get("updated_parsed")
        if not d:
            continue
        date_str = f"{d.tm_year:04d}-{d.tm_mon:02d}-{d.tm_mday:02d}"
        if ws <= date_str <= we:
            n += 1
    return n


async def probe_source(src: dict, ws: str, we: str) -> dict:
    """Returns {slug, status, items_in_window, total_entries, mode, error}."""
    out = {"slug": src["slug"], "name": src["name"], "url": src["url"],
            "configured_feed": src.get("feed"),
            "found_feed": None, "mode": None,
            "items_in_window": 0, "total_entries": 0, "error": None}
    async with httpx.AsyncClient(timeout=15, follow_redirects=True,
                                    headers={"User-Agent": "safety-feed/0.1"}) as c:
        # 1) Try configured feed if present
        if src.get("feed"):
            result = await _try_url_as_feed(c, src["feed"])
            if result and "entries" in result:
                out["mode"] = "configured_feed"
                out["found_feed"] = src["feed"]
                out["total_entries"] = len(result["entries"])
                out["items_in_window"] = _count_in_window(result["entries"], ws, we)
                return out
            out["error"] = result.get("error") if result else "unknown"

        # 2) Try common feed paths
        base = src["url"].rstrip("/")
        candidates = [f"{base}/feed", f"{base}/rss", f"{base}/feed.xml",
                       f"{base}/atom.xml", f"{base}/rss.xml", f"{base}/index.xml"]
        # Also try at root if URL has a path
        from urllib.parse import urlparse
        u = urlparse(src["url"])
        if u.path and u.path != "/":
            root = f"{u.scheme}://{u.netloc}"
            candidates.extend([f"{root}/feed", f"{root}/rss",
                                f"{root}/feed.xml", f"{root}/atom.xml"])
        for candidate in candidates:
            result = await _try_url_as_feed(c, candidate)
            if result and "entries" in result:
                out["mode"] = "feed_discovered"
                out["found_feed"] = candidate
                out["total_entries"] = len(result["entries"])
                out["items_in_window"] = _count_in_window(result["entries"], ws, we)
                return out

        # 3) Parse HTML for <link rel="alternate" type="application/rss+xml">
        try:
            r = await c.get(src["url"])
            if r.status_code == 200:
                import re
                m = re.search(
                    r'<link[^>]+rel=["\']alternate["\'][^>]+type=["\']application/(?:rss|atom)\+xml["\'][^>]+href=["\']([^"\']+)',
                    r.text[:80_000], re.I,
                )
                if not m:
                    m = re.search(
                        r'<link[^>]+type=["\']application/(?:rss|atom)\+xml["\'][^>]+href=["\']([^"\']+)',
                        r.text[:80_000], re.I,
                    )
                if m:
                    href = m.group(1)
                    if href.startswith("/"):
                        href = f"{u.scheme}://{u.netloc}{href}"
                    result = await _try_url_as_feed(c, href)
                    if result and "entries" in result:
                        out["mode"] = "html_link_rel"
                        out["found_feed"] = href
                        out["total_entries"] = len(result["entries"])
                        out["items_in_window"] = _count_in_window(result["entries"], ws, we)
                        return out
        except Exception:
            pass

        out["mode"] = "no_feed"
        if not out["error"]:
            out["error"] = "no feed found via configured URL, discovery, or HTML link-rel"
        return out


async def main():
    end = date.fromisoformat(TODAY)
    ws = (end - timedelta(days=WINDOW_DAYS - 1)).isoformat()
    we = end.isoformat()
    print(f"\nSource health check — window {ws} → {we}")
    print(f"Testing {len(PULL_SOURCES)} sources from config.PULL_SOURCES\n")

    results = await asyncio.gather(*[probe_source(s, ws, we) for s in PULL_SOURCES])

    # Per-source line
    print(f"{'STATUS':<7} {'SLUG':<32} {'MODE':<18} {'IN-WIN':>7} {'TOTAL':>6}  DETAIL")
    print("-" * 110)
    n_pass = n_fail = n_warn = 0
    for r in results:
        if r["mode"] in ("configured_feed", "feed_discovered", "html_link_rel") and r["total_entries"] > 0:
            if r["items_in_window"] > 0:
                marker = PASS; n_pass += 1
            else:
                marker = WARN; n_warn += 1  # feed works but no in-window content
        else:
            marker = FAIL; n_fail += 1
        detail = ""
        if r["found_feed"] and r["found_feed"] != r["configured_feed"]:
            detail = f"discovered: {r['found_feed']}"
        elif r["error"]:
            detail = r["error"]
        print(f"  {marker:<5} {r['slug']:<32} {r['mode'] or '?':<18} {r['items_in_window']:>7} {r['total_entries']:>6}  {detail}")

    print("-" * 110)
    print(f"\n  {PASS} {n_pass} sources have a working feed with in-window items")
    print(f"  {WARN} {n_warn} sources have a working feed but no in-window items")
    print(f"  {FAIL} {n_fail} sources have no working feed — these will silently return 0 items in production\n")

    if n_fail:
        print("FAIL — sources without a working feed will drop silently. Fixes:")
        print("  1. Look up the real feed URL and update config.PULL_SOURCES")
        print("  2. OR implement an HTML-extraction fallback (Haiku) for feedless sources")
        print("  3. OR remove these from the registry if they're not worth tracking")

    sys.exit(n_fail)


if __name__ == "__main__":
    asyncio.run(main())
