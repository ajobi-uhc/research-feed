"""Source adapters. Each fetch_* returns list[Item]. No LLMs in here.

  fetch_papers(queries, ws, we)             — S2 paper search
  fetch_followed_authors(names, ws, we)     — S2 author search → papers
  fetch_forum(ws, we)                       — Alignment Forum / LessWrong (GreaterWrong)
                                              two passes: by-date + by-recent-activity
  fetch_lab_blogs(ws, we)                   — RSS feeds from PULL_SOURCES
  dedup(items)                              — cluster cross-posts
"""

from __future__ import annotations
import asyncio
import re
import urllib.parse
from typing import Optional

import httpx
import feedparser
from rapidfuzz import fuzz

from .config import S2_API_KEY, PULL_SOURCES, AF_MIN_KARMA
from .models import Item, Source
from . import progress


# ════════════════════════════════════════════════════════════════════
# Semantic Scholar — papers + authors
# ════════════════════════════════════════════════════════════════════
S2_BASE = "https://api.semanticscholar.org/graph/v1"
S2_PAPER_FIELDS = ("paperId,title,abstract,authors,externalIds,publicationDate,"
                   "year,venue,citationCount")
S2_AUTHOR_FIELDS = "authorId,name,paperCount,affiliations"

_S2_DELAY = 0.15 if S2_API_KEY else 1.1   # 1 req/sec unauthed
_s2_sem = asyncio.Semaphore(1)


def _s2_headers() -> dict:
    h = {"Accept": "application/json"}
    if S2_API_KEY:
        h["x-api-key"] = S2_API_KEY
    return h


async def _s2_get(client: httpx.AsyncClient, url: str, params: dict | None = None) -> dict | None:
    async with _s2_sem:
        try:
            r = await client.get(url, params=params, headers=_s2_headers(), timeout=30)
            await asyncio.sleep(_S2_DELAY)
        except Exception as e:
            progress.log(f"s2: {url} failed: {e!r}")
            return None
        if r.status_code == 429:
            ra = r.headers.get("Retry-After")
            wait = float(ra) if ra and ra.isdigit() else 5.0
            progress.log(f"s2: 429, waiting {wait:.0f}s")
            await asyncio.sleep(wait)
            return None
        if r.status_code != 200:
            progress.log(f"s2: status={r.status_code} body={r.text[:100]}")
            return None
        try:
            return r.json()
        except Exception:
            return None


def _s2_paper_to_item(p: dict, discovered_via: str) -> Item | None:
    title = (p.get("title") or "").strip()
    if not title:
        return None
    arxiv_id = (p.get("externalIds") or {}).get("ArXiv")
    if arxiv_id:
        url = f"https://arxiv.org/abs/{arxiv_id}"
    else:
        url = f"https://www.semanticscholar.org/paper/{p['paperId']}"
    pub_venue = (p.get("venue") or "").strip() or None
    authors = [a.get("name", "") for a in (p.get("authors") or []) if a.get("name")]
    affs: list[str] = []
    for a in (p.get("authors") or []):
        for aff in (a.get("affiliations") or []):
            if aff and aff not in affs:
                affs.append(aff)
    date_str = p.get("publicationDate") or (f"{p['year']}-01-01" if p.get("year") else "")
    return Item(
        id=Item.make_id(url),
        title=title, url=url, venue="arxiv_standalone",
        date=date_str,
        authors=authors,
        description=(p.get("abstract") or "")[:500],
        citation_count=p.get("citationCount"),
        arxiv_id=arxiv_id, arxiv_url=url if arxiv_id else None,
        publication_venue=pub_venue,
        affiliations=affs,
        discovered_via=discovered_via,
    )


async def fetch_papers(queries: list[str], ws: str, we: str) -> list[Item]:
    """Run each query against S2's bulk paper search. Date-filtered server-side."""
    if not queries:
        return []
    progress.log(f"papers: {len(queries)} S2 keyword queries", phase="fetch-papers")
    out: dict[str, Item] = {}
    async with httpx.AsyncClient() as client:
        for q in queries[:15]:
            data = await _s2_get(client, f"{S2_BASE}/paper/search/bulk", {
                "query": q,
                "publicationDateOrYear": f"{ws}:{we}",
                "fields": S2_PAPER_FIELDS,
            })
            if not data:
                continue
            hits = data.get("data", []) or []
            kept = 0
            for p in hits:
                pid = p.get("paperId")
                if not pid:
                    continue
                item = _s2_paper_to_item(p, discovered_via=f"kw:{q}")
                if item and item.id not in out:
                    out[item.id] = item
                    kept += 1
            progress.log(f"papers: '{q}' → {len(hits)} hits, {kept} new")
    progress.log(f"papers: {len(out)} unique keyword candidates")
    return list(out.values())


async def fetch_followed_authors(names: list[str], ws: str, we: str) -> list[Item]:
    """For each author name: S2 author-search → papers → window filter."""
    if not names:
        return []
    progress.log(f"papers: {len(names)} followed-author lookups", phase="fetch-authors")
    out: dict[str, Item] = {}
    async with httpx.AsyncClient() as client:
        for name in names[:50]:
            sdata = await _s2_get(client, f"{S2_BASE}/author/search", {
                "query": name, "fields": S2_AUTHOR_FIELDS, "limit": 3,
            })
            if not sdata:
                continue
            authors = sdata.get("data", []) or []
            if not authors:
                continue
            author = max(authors, key=lambda a: (a.get("paperCount") or 0))
            author_id = author.get("authorId")
            if not author_id:
                continue
            pdata = await _s2_get(client, f"{S2_BASE}/author/{author_id}/papers", {
                "fields": S2_PAPER_FIELDS, "limit": 50,
            })
            if not pdata:
                continue
            in_window = [p for p in (pdata.get("data") or [])
                         if ws <= (p.get("publicationDate") or "") <= we]
            kept = 0
            for p in in_window:
                item = _s2_paper_to_item(p, discovered_via=f"author:{name}")
                if item and item.id not in out:
                    out[item.id] = item
                    kept += 1
            if in_window:
                progress.log(f"papers: {name} → {len(in_window)} in window, {kept} new")
    progress.log(f"papers: {len(out)} unique author candidates")
    return list(out.values())


# ════════════════════════════════════════════════════════════════════
# Alignment Forum / LessWrong (GreaterWrong)
#
# Two queries:
#   1. by-date: posts published in window with karma >= threshold
#   2. by-recent-activity: posts with recent comment activity, regardless of
#      publication date (catches resurfaced older posts)
# ════════════════════════════════════════════════════════════════════
GREATERWRONG_BASE = "https://www.greaterwrong.com"


async def fetch_forum(ws: str, we: str, min_karma: int = AF_MIN_KARMA) -> list[Item]:
    progress.log(f"forum: AF posts (karma ≥ {min_karma})", phase="fetch-forum")
    items: list[Item] = []
    seen_ids: set[str] = set()

    async with httpx.AsyncClient(
        timeout=30, follow_redirects=True,
        headers={"User-Agent": "safety-feed/0.1"},
    ) as c:
        # Pass 1: by publication date
        items.extend(await _fetch_gw_pass(
            c, label="by-date",
            url=f"{GREATERWRONG_BASE}/index?view=alignment-forum"
                f"&sortedBy=top&after={ws}&before={we}",
            ws=ws, we=we, min_karma=min_karma,
            discovered_via="forum:date",
            seen_ids=seen_ids,
        ))

        # Pass 2: by recent comment activity (no date filter — older posts with new comments)
        items.extend(await _fetch_gw_pass(
            c, label="by-activity",
            url=f"{GREATERWRONG_BASE}/index?view=alignment-forum"
                f"&sortedBy=topComments&offset=0",
            ws=None, we=None, min_karma=min_karma,
            discovered_via="forum:activity",
            seen_ids=seen_ids,
        ))

    progress.log(f"forum: {len(items)} items total (both passes)")
    return items


async def _fetch_gw_pass(
    c: httpx.AsyncClient, *, label: str, url: str,
    ws: str | None, we: str | None, min_karma: int,
    discovered_via: str, seen_ids: set[str],
) -> list[Item]:
    """One GreaterWrong fetch + parse, with diagnostic logging.

    If the parse returns 0 items we log enough to tell whether it's:
      • HTTP error
      • short body (rate-limit or empty response)
      • parser regression (anchors present but parser failed)
      • filter dropped everything (karma below threshold or out of window)
    """
    try:
        r = await c.get(url)
    except Exception as e:
        progress.log(f"forum: {label} fetch raised: {e!r}")
        return []
    if r.status_code != 200:
        progress.log(f"forum: {label} got HTTP {r.status_code} (body={len(r.text)} bytes)")
        return []
    body_len = len(r.text)
    anchor_count = len(re.findall(r'<h1 class="listing', r.text))
    parsed = _parse_gw_listing(r.text, ws, we, min_karma, discovered_via)
    new = [it for it in parsed if it.id not in seen_ids]
    for it in new:
        seen_ids.add(it.id)
    if not parsed:
        progress.log(
            f"forum: {label} returned 0 items "
            f"(HTTP 200, body={body_len} bytes, post anchors found={anchor_count}). "
            f"{'Parser issue — anchors present.' if anchor_count > 0 else 'Response looks empty/blocked.'}"
        )
    else:
        progress.log(f"forum: {label} → {len(parsed)} parsed, {len(new)} new (anchors in HTML: {anchor_count})")
    return new


import html as _html_lib
from datetime import datetime, timezone


def _clean_title(s: str) -> str:
    """Strip tags, soft-hyphens, non-breaking spaces; decode HTML entities."""
    s = re.sub(r"<[^>]+>", "", s)
    s = _html_lib.unescape(s)
    return s.replace("\xad", "").replace("\xa0", " ").strip()


def _parse_gw_listing(html: str, ws: str | None, we: str | None,
                       min_karma: int, discovered_via: str) -> list[Item]:
    """Parse GreaterWrong's listing HTML.

    Each post is rendered as:
        <h1 class="listing">
          <a class="post-title-link" href="/posts/...">Title</a>
        </h1>
        <div class="post-meta">
          <a class="author">name</a>
          <span class="date" data-js-date=UNIX_MS>...</span>
          <div class="karma ..."><span class="karma-value">NUMBER<span> points</span></span></div>
          <a class="comment-count">N<span> comments</span></a>
          ...
        </div>

    We split on the `<h1 class="listing...">` boundary, then search within each
    part directly for the karma / comments / date / authors. No nested-block
    extraction — too fragile.
    """
    out: list[Item] = []
    parts = re.split(r'<h1 class="listing[^"]*">', html)

    for part in parts[1:80]:  # cap at 79 posts; parts[0] is the pre-h1 page header
        m_title = re.search(
            r'<a class="post-title-link"[^>]+href="([^"]+)"[^>]*>(.*?)</a>',
            part, re.DOTALL,
        )
        if not m_title:
            continue
        relurl = m_title.group(1)
        title = _clean_title(m_title.group(2))
        if not title or not relurl:
            continue

        # Bound the search window: the post-meta lives within ~3000 chars of the title.
        # Beyond that we risk grabbing the next post's data if the next h1 boundary somehow
        # ended up inside this part (it shouldn't, but be defensive).
        window = part[:3000]

        authors = [_clean_title(a) for a in re.findall(
            r'<a class="author"[^>]*>([^<]+)</a>', window,
        )]
        karma_m = re.search(r'<span class="karma-value"[^>]*>(-?\d+)', window)
        karma = int(karma_m.group(1)) if karma_m else None
        comments_m = re.search(r'<a class="comment-count"[^>]*>(\d+)', window)
        comments = int(comments_m.group(1)) if comments_m else None

        # Prefer the exact data-js-date unix-ms timestamp; fall back to text.
        date_str = ""
        ts_m = re.search(r"data-js-date=(\d+)", window)
        if ts_m:
            try:
                ts = int(ts_m.group(1)) / 1000.0
                date_str = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")
            except (ValueError, OSError):
                pass
        if not date_str:
            txt_m = re.search(r'>(\d{1,2}) (\w+) (\d{4})', window)
            if txt_m:
                try:
                    date_str = datetime.strptime(
                        f"{txt_m.group(1)} {txt_m.group(2)} {txt_m.group(3)}", "%d %b %Y",
                    ).strftime("%Y-%m-%d")
                except ValueError:
                    pass

        # Filters
        if karma is not None and karma < min_karma:
            continue
        if ws and we and date_str and not (ws <= date_str <= we):
            continue
        if not date_str:
            date_str = we or "2026-01-01"

        af_url = f"https://www.alignmentforum.org{relurl}" if relurl.startswith("/") else relurl
        out.append(Item(
            id=Item.make_id(af_url),
            title=title, url=af_url, venue="alignment_forum",
            date=date_str,
            authors=authors,
            description="",
            af_karma=karma, af_comments=comments,
            recent_comment_count=(comments if discovered_via.endswith("activity") else None),
            discovered_via=discovered_via,
        ))
    return out


# ════════════════════════════════════════════════════════════════════
# Lab blog feeds (RSS) — static registry, deterministic
# ════════════════════════════════════════════════════════════════════
async def fetch_lab_blogs(ws: str, we: str) -> list[Item]:
    progress.log(f"labs: {sum(1 for s in PULL_SOURCES if s.get('feed'))} feed sources", phase="fetch-labs")
    results = await asyncio.gather(
        *[_fetch_feed_for_source(src, ws, we) for src in PULL_SOURCES if src.get("feed")],
        return_exceptions=True,
    )
    out: list[Item] = []
    for src, res in zip([s for s in PULL_SOURCES if s.get("feed")], results):
        if isinstance(res, Exception):
            progress.log(f"labs: {src['slug']} failed: {res}")
            continue
        out.extend(res)
    progress.log(f"labs: {len(out)} items from feeds")

    # Discover feeds for sources without configured feed URL
    feedless = [s for s in PULL_SOURCES if not s.get("feed")]
    if feedless:
        progress.log(f"labs: probing {len(feedless)} feedless sources for RSS")
        results = await asyncio.gather(
            *[_try_discover_and_fetch(src, ws, we) for src in feedless],
            return_exceptions=True,
        )
        for src, res in zip(feedless, results):
            if isinstance(res, Exception) or not res:
                continue
            out.extend(res)

    return out


async def _fetch_feed_for_source(src: dict, ws: str, we: str) -> list[Item]:
    return await _parse_feed(src["slug"], src["feed"], ws, we)


async def _try_discover_and_fetch(src: dict, ws: str, we: str) -> list[Item]:
    """For sources without configured RSS: try to discover a feed; otherwise
    fall back to HTML+Haiku extraction (one Haiku call per source per run)."""
    base = src["url"].rstrip("/")
    candidates = [f"{base}/feed", f"{base}/rss", f"{base}/feed.xml",
                   f"{base}/atom.xml", f"{base}/rss.xml", f"{base}/index.xml"]
    async with httpx.AsyncClient(timeout=15, follow_redirects=True,
                                    headers={"User-Agent": "safety-feed/0.1"}) as c:
        for url in candidates:
            try:
                r = await c.head(url)
                ct = r.headers.get("content-type", "").lower()
                if r.status_code == 200 and any(t in ct for t in ("xml", "rss", "atom")):
                    items = await _parse_feed(src["slug"], url, ws, we)
                    if items:
                        progress.log(f"labs: discovered feed at {url} for {src['slug']}")
                        return items
            except Exception:
                continue

        # Final fallback: fetch the index HTML and use Haiku to extract items
        try:
            r = await c.get(src["url"])
            r.raise_for_status()
        except Exception as e:
            progress.log(f"labs: {src['slug']} HTML fetch failed: {e!r}")
            return []

    from . import llm  # local import to avoid circular dep at module level
    raw = await llm.extract_items_from_html(
        slug=src["slug"], name=src["name"], base_url=src["url"],
        html=r.text, ws=ws, we=we,
    )
    items = []
    for it in raw:
        d = it.get("date", "")
        if not (ws <= d <= we):
            continue
        url = (it.get("url") or "").strip()
        title = (it.get("title") or "").strip()
        if not (url and title):
            continue
        items.append(Item(
            id=Item.make_id(url),
            title=title, url=url, venue=src["slug"], date=d,
            authors=list(it.get("authors", [])),
            description=(it.get("description") or "")[:400],
            discovered_via=f"html:{src['slug']}",
        ))
    if items:
        progress.log(f"labs: {src['slug']} HTML-extracted {len(items)} items")
    return items


async def _parse_feed(slug: str, feed_url: str, ws: str, we: str) -> list[Item]:
    async with httpx.AsyncClient(timeout=20, follow_redirects=True,
                                    headers={"User-Agent": "safety-feed/0.1"}) as c:
        try:
            r = await c.get(feed_url)
            r.raise_for_status()
        except Exception as e:
            progress.log(f"labs: {slug} feed fetch failed: {e}")
            return []
    parsed = feedparser.parse(r.content)
    out: list[Item] = []
    for entry in parsed.entries:
        title = (entry.get("title") or "").strip()
        link = (entry.get("link") or "").strip()
        if not (title and link):
            continue
        d = entry.get("published_parsed") or entry.get("updated_parsed")
        if not d:
            continue
        date_str = f"{d.tm_year:04d}-{d.tm_mon:02d}-{d.tm_mday:02d}"
        if not (ws <= date_str <= we):
            continue
        summary = re.sub(r"<[^>]+>", "", entry.get("summary", ""))[:400]
        authors = [a.get("name", "") for a in entry.get("authors", []) if a.get("name")]
        out.append(Item(
            id=Item.make_id(link),
            title=title, url=link, venue=slug, date=date_str,
            authors=authors, description=summary,
            discovered_via=f"feed:{slug}",
        ))
    if out:
        progress.log(f"labs: {slug}: {len(out)} in-window")
    return out


# ════════════════════════════════════════════════════════════════════
# Cross-source dedup
# ════════════════════════════════════════════════════════════════════
def _norm_title(t: str) -> str:
    t = re.sub(r"[^\w\s]", " ", t.lower()).strip()
    return re.sub(r"\s+", " ", t)


def _same_work(a: Item, b: Item) -> bool:
    ta, tb = _norm_title(a.title), _norm_title(b.title)
    if ta == tb:
        return True
    if fuzz.ratio(ta, tb) / 100.0 >= 0.85:
        a_last = {n.lower().split()[-1] for n in a.authors if n}
        b_last = {n.lower().split()[-1] for n in b.authors if n}
        return bool(a_last & b_last)
    return False


# Venue trust order for picking the cluster's primary
_VENUE_PRIORITY = {
    "anthropic_alignment_science": 50, "transformer_circuits": 50,
    "anthropic_research": 35, "openai_safety": 30, "deepmind_safety": 30,
    "metr": 35, "apollo": 30, "uk_aisi": 30, "us_aisi": 20,
    "redwood": 30, "goodfire": 25, "mats": 20, "far_ai": 20,
    "alignment_forum": 25, "arxiv_standalone": 15,
}


def dedup(items: list[Item]) -> list[Item]:
    """Cluster cross-posts. Primary = highest-trust venue. Activity signals
    bubble up; cross-post URLs collect into extra_urls."""
    clusters: list[list[Item]] = []
    for it in items:
        placed = False
        for cluster in clusters:
            if _same_work(it, cluster[0]):
                cluster.append(it); placed = True; break
        if not placed:
            clusters.append([it])

    out = []
    for cluster in clusters:
        cluster.sort(key=lambda i: -_VENUE_PRIORITY.get(i.venue, 0))
        primary = cluster[0]
        seen_urls = {primary.url}
        for c in cluster[1:]:
            if c.url not in seen_urls:
                primary.extra_urls.append({"venue": c.venue, "url": c.url, "date": c.date})
                seen_urls.add(c.url)
            # Bubble up signals
            if c.af_karma is not None and primary.af_karma is None:
                primary.af_karma = c.af_karma
                primary.af_comments = c.af_comments
                primary.recent_comment_count = c.recent_comment_count
            if c.arxiv_id and not primary.arxiv_id:
                primary.arxiv_id = c.arxiv_id; primary.arxiv_url = c.arxiv_url
            if c.citation_count is not None and primary.citation_count is None:
                primary.citation_count = c.citation_count
            if c.publication_venue and not primary.publication_venue:
                primary.publication_venue = c.publication_venue
        out.append(primary)
    return out
