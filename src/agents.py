"""Agent-driven workflows: onboarding + discovery.

Two entry points:
  - build_profile(user_text)        → writes data/active_profile.json
  - discover(profile, days, wipe)   → writes items to DB

Pipelines are intentionally flat and top-to-bottom readable. Prompts live
inline (they're the contract — best read where used).
"""

from __future__ import annotations
import asyncio
import json
import re
import urllib.parse
from datetime import date, datetime, timedelta
from pathlib import Path

import httpx
from claude_agent_sdk import query, ClaudeAgentOptions
from rapidfuzz import fuzz

from . import progress, store
from .config import ROOT, DATA, PROFILE_PATH, MODEL, TRIAGE_MODEL, S2_API_KEY, SAFETY_VOCAB, load_dotenv
from .models import Item, Profile, Source

load_dotenv()


# ─────────────────────────────────────────────────────────────────────
# 1. ONBOARDING — agent builds the user's Profile from free-text input
# ─────────────────────────────────────────────────────────────────────
ONBOARDING_PROMPT = """You are an onboarding agent for a single-user AI-safety research-discovery tool. Take the user's description and build a *context map* — a JSON profile listing the orgs, sources, researchers, and keywords this user should track. Use REAL web research; do not just rely on training knowledge.

## User profile

```
{user_text}
```

## Required process (do not skip)

1. Parse the user text. Note: name, stated affiliation(s), URLs (Scholar/arXiv/ORCID), people named, subfields mentioned, things they don't want.

2. If a Google Scholar / arXiv / ORCID URL is in the text, WebFetch it. Extract their recent papers (titles + co-authors) and most-frequent collaborators.

3. Run ≥5 WebSearch calls to discover the landscape:
   - "<research area> leading researchers 2026"
   - "<research area> labs publishing 2026"
   - "<named collaborator> coauthors"

4. Build the SOURCE LIST yourself (not from a fixed menu). Each entry: slug, name, url, trust (0-50), rationale, and an optional `feed_url` (RSS/Atom). 10-25 sources. The user's own org → 50. Orgs verified to publish in their stated areas → 30-45. Adjacent → 15-25. Forums (alignment_forum, arxiv_standalone) → 25-40.

   IMPORTANT: distinguish what the user WORKS ON from who they COLLABORATE WITH. Subfield weights and source weights should reflect the user's stated research focus, not their collaborators' focus. If user says "I work with X at OrgY," that's a signal to add X to trusted_authors but NOT to inflate OrgY's source trust unless OrgY broadly publishes in user's areas.

   FEED DISCOVERY: when picking a source, if it's quick, try `<source-url>/feed`, `/rss`, `/feed.xml`, or check the page HTML for `<link rel="alternate" type="application/rss+xml">`. If you find a working feed URL, include it as `feed_url`. Otherwise leave it null — the system will fall back to HTML parsing.

   DO NOT add arXiv categories (cs.AI, cs.LG, etc.) or Alignment Forum / LessWrong as `sources` entries — those have dedicated gatherers in the pipeline. You CAN reference them in `research_notes` if relevant. The `sources` list is for lab/org blogs only.

5. Trusted authors: 10-40 names. Each = {{ name, affiliation, why }}. Include everyone the user named + close coauthors found in step 2 + leading researchers in their stated areas.

6. Keywords: 6-15 SPECIFIC arxiv-searchable phrases (not single noisy words like "alignment"). E.g. "sparse autoencoder", "AI control protocol", "deceptive alignment".

7. Dislikes: short list of phrases to actively de-rank if matched in title/abstract.

8. Subfield weights: use the 11 fixed subfields below. Score 15-20 for stated priorities, 8-12 adjacent, 3-5 far. Don't zero anything.

9. Write JSON to: {profile_path}

## Schema

```json
{{
  "user_summary": "<one paragraph>",
  "user_identity": {{ "name": ..., "affiliations": [...], "scholar_url": ... }},
  "research_areas": ["3-6 short tags"],
  "keywords": ["6-15 specific phrases"],
  "trusted_authors": [{{ "name": ..., "affiliation": ..., "why": ... }}],
  "sources": [{{ "slug": ..., "name": ..., "url": ..., "trust": <int>, "rationale": ..., "feed_url": "<URL or null>" }}],
  "subfield_weights": {{
    "mech_interp": 0-20, "evals": 0-20, "control": 0-20, "alignment_training": 0-20,
    "scalable_oversight": 0-20, "deceptive_alignment": 0-20, "agent_safety": 0-20,
    "model_organisms": 0-20, "governance": 0-20, "strategy": 0-20, "other": 0-20
  }},
  "dislikes": ["e.g. 'product launch', 'governance press release'"],
  "research_notes": "<3-5 sentences explaining your choices>"
}}
```

After writing, final message: "Profile written." Nothing else.
"""


async def build_profile(user_text: str) -> Profile:
    PROFILE_PATH.parent.mkdir(parents=True, exist_ok=True)
    if PROFILE_PATH.exists():
        PROFILE_PATH.unlink()

    prompt = ONBOARDING_PROMPT.format(
        user_text=user_text,
        profile_path=str(PROFILE_PATH),
    )
    progress.log("Onboarding agent invoked (Sonnet 4.6)…", phase="onboarding")
    turns = await progress.stream(
        query(prompt=prompt, options=ClaudeAgentOptions(
            allowed_tools=["WebSearch", "WebFetch", "Write", "Read"],
            permission_mode="bypassPermissions",
            cwd=str(ROOT), max_turns=20, model=MODEL,
        )),
        label="onboarding",
    )
    progress.log(f"Onboarding finished after {turns} turns.")

    # Agent sometimes writes to $HOME/data — recover if so.
    if not PROFILE_PATH.exists():
        for alt in [Path.home() / "data" / "active_profile.json",
                    Path.cwd() / "data" / "active_profile.json"]:
            if alt.exists():
                PROFILE_PATH.write_text(alt.read_text())
                alt.unlink()
                break
        else:
            raise RuntimeError(f"Agent did not write {PROFILE_PATH}")
    return Profile.from_dict(json.loads(PROFILE_PATH.read_text()))


# ─────────────────────────────────────────────────────────────────────
# 2. DISCOVERY — gather candidates from arXiv + AF + lab pages, then curate
# ─────────────────────────────────────────────────────────────────────
# ─────────────────────────────────────────────────────────────────
# gather_arxiv — Semantic Scholar backed
#
# Two parallel streams hit S2's bulk-search and per-author endpoints:
#   • keyword search → date-filtered server-side
#   • author search → resolve trusted_author names to S2 IDs, fetch papers
# Merge, dedup by S2 paperId, run Haiku triage on the result.
#
# We keep the same Item shape downstream (venue="arxiv_standalone") so the
# rest of the pipeline (dedup, curation, scoring) is unchanged.
# ─────────────────────────────────────────────────────────────────

S2_BASE = "https://api.semanticscholar.org/graph/v1"
S2_PAPER_FIELDS = "paperId,title,abstract,authors,externalIds,publicationDate,year,venue,citationCount"
S2_AUTHOR_FIELDS = "authorId,name,paperCount,affiliations"

# Rate limiting: 1 req/sec unauthed; with API key go to ~10/sec.
_S2_RATE_DELAY = 0.15 if S2_API_KEY else 1.1
_s2_sem = asyncio.Semaphore(1)  # serialize requests through the bucket


def _s2_headers() -> dict:
    h = {"Accept": "application/json"}
    if S2_API_KEY:
        h["x-api-key"] = S2_API_KEY
    return h


async def _s2_get(client: httpx.AsyncClient, url: str, params: dict | None = None) -> dict | None:
    """Rate-limited GET against S2. Returns parsed JSON or None on failure."""
    async with _s2_sem:
        try:
            r = await client.get(url, params=params, headers=_s2_headers(), timeout=30)
            await asyncio.sleep(_S2_RATE_DELAY)
        except Exception as e:
            progress.log(f"s2: GET {url} failed: {e!r}")
            return None
        if r.status_code == 429:
            ra = r.headers.get("Retry-After")
            wait = float(ra) if ra and ra.isdigit() else 5.0
            progress.log(f"s2: 429, waiting {wait:.0f}s")
            await asyncio.sleep(wait)
            return None
        if r.status_code != 200:
            progress.log(f"s2: status={r.status_code} url={url} body={r.text[:120]}")
            return None
        try:
            return r.json()
        except Exception as e:
            progress.log(f"s2: bad JSON: {e!r}")
            return None


async def gather_arxiv(profile: Profile, window_start: str, window_end: str) -> list[Item]:
    """Search Semantic Scholar by keywords + by authors in parallel,
    merge, dedup, triage with Haiku."""
    progress.log(
        f"s2: searching window {window_start} → {window_end} "
        f"({len(profile.keywords)} keywords, {len(profile.trusted_authors)} authors)",
        phase="gather-arxiv",
    )

    async with httpx.AsyncClient() as client:
        by_kw, by_au = await asyncio.gather(
            _s2_search_keywords(client, profile.keywords, window_start, window_end),
            _s2_papers_by_authors(client, profile.trusted_author_names(), window_start, window_end),
        )

    progress.log(f"s2: {len(by_kw)} keyword hits + {len(by_au)} author hits")

    # Merge, dedup. Two layers: S2 paperId (catches duplicate S2 records) +
    # canonical URL (catches different S2 records pointing to the same arxiv preprint).
    # Author results take priority.
    seen_pid: set[str] = set()
    seen_url: set[str] = set()
    candidates: list[dict] = []
    for p in by_au + by_kw:
        pid = p.get("paperId")
        if not pid or pid in seen_pid:
            continue
        seen_pid.add(pid)
        # Compute canonical URL once for URL-level dedup
        arxiv_id_raw = (p.get("externalIds") or {}).get("ArXiv")
        url = f"https://arxiv.org/abs/{arxiv_id_raw}" if arxiv_id_raw else f"https://www.semanticscholar.org/paper/{pid}"
        if url in seen_url:
            continue
        seen_url.add(url)
        candidates.append(p)
    progress.log(f"s2: {len(candidates)} unique papers after dedup")
    if not candidates:
        return []

    # Convert to Items
    items_pre = [it for it in (_s2_to_item(p) for p in candidates) if it]

    if len(items_pre) <= 100:
        progress.log(f"s2: skipping triage ({len(items_pre)} candidates, threshold 100)")
        return items_pre

    # Cap before triage to fit Haiku's 200k context.
    # Prioritize: items with trusted-author match first, then everything else by date.
    trusted_last = [n.lower().split()[-1] for n in profile.trusted_author_names()]
    def has_trust(it: Item) -> bool:
        return any(t in a.lower() for t in trusted_last for a in it.authors)
    items_pre.sort(key=lambda it: (0 if has_trust(it) else 1, it.date), reverse=False)
    items_pre.reverse()  # most recent first within each priority bucket
    TRIAGE_CAP = 300
    if len(items_pre) > TRIAGE_CAP:
        progress.log(f"s2: capping triage input to {TRIAGE_CAP} (had {len(items_pre)})")
        items_pre = items_pre[:TRIAGE_CAP]

    keep_ids = await _triage_papers(items_pre, profile)
    keep_set = set(keep_ids)
    items = [it for it in items_pre if it.id in keep_set]
    progress.log(f"s2: {len(items)} survived triage (from {len(items_pre)})")
    return items


async def _s2_search_keywords(
    client: httpx.AsyncClient, keywords: list[str], ws: str, we: str,
) -> list[dict]:
    """For each keyword, S2 bulk paper search filtered to the window."""
    out: list[dict] = []
    for kw in keywords[:15]:  # cap to 15 keyword groups
        params = {
            "query": kw,
            "publicationDateOrYear": f"{ws}:{we}",
            "fields": S2_PAPER_FIELDS,
        }
        data = await _s2_get(client, f"{S2_BASE}/paper/search/bulk", params)
        if not data:
            continue
        hits = data.get("data", []) or []
        progress.log(f"s2: '{kw}' → {len(hits)} hits")
        out.extend(hits)
    return out


async def _s2_papers_by_authors(
    client: httpx.AsyncClient, author_names: list[str], ws: str, we: str,
) -> list[dict]:
    """For each trusted author name, resolve to S2 author ID, fetch papers, filter to window."""
    out: list[dict] = []
    for name in author_names[:50]:  # cap to 50 trusted authors
        # Resolve name → author ID. S2's author search isn't perfect, pick the most-prolific match.
        sdata = await _s2_get(client, f"{S2_BASE}/author/search", {
            "query": name, "fields": S2_AUTHOR_FIELDS, "limit": 3,
        })
        if not sdata:
            continue
        authors = sdata.get("data", []) or []
        if not authors:
            continue
        # Heuristic: pick the entry with the most papers.
        author = max(authors, key=lambda a: (a.get("paperCount") or 0))
        author_id = author.get("authorId")
        if not author_id:
            continue

        # Fetch their papers (S2's author/papers endpoint doesn't support date filter; filter client-side)
        pdata = await _s2_get(client, f"{S2_BASE}/author/{author_id}/papers", {
            "fields": S2_PAPER_FIELDS, "limit": 50,
        })
        if not pdata:
            continue
        papers = pdata.get("data", []) or []
        in_window = [p for p in papers if ws <= (p.get("publicationDate") or "") <= we]
        if in_window:
            progress.log(f"s2: {name} → {len(in_window)}/{len(papers)} in window")
            out.extend(in_window)
    return out


def _s2_to_item(p: dict) -> Item | None:
    """Convert an S2 paper record to our Item. Returns None if unparseable."""
    title = (p.get("title") or "").strip()
    if not title:
        return None

    arxiv_id_raw = (p.get("externalIds") or {}).get("ArXiv")
    arxiv_id = arxiv_id_raw if arxiv_id_raw else None

    # One bucket for academic papers; the actual publication name (NeurIPS / ICML /
    # journal title / "arXiv preprint") goes into publication_venue as a quality signal.
    if arxiv_id:
        url = f"https://arxiv.org/abs/{arxiv_id}"
        arxiv_url = url
    else:
        url = f"https://www.semanticscholar.org/paper/{p['paperId']}"
        arxiv_url = None

    publication_venue = (p.get("venue") or "").strip() or None

    authors = [a.get("name", "") for a in (p.get("authors") or []) if a.get("name")]
    affiliations: list[str] = []
    for a in (p.get("authors") or []):
        for aff in (a.get("affiliations") or []):
            if aff and aff not in affiliations:
                affiliations.append(aff)

    date_str = p.get("publicationDate") or ""
    if not date_str and p.get("year"):
        date_str = f"{p['year']}-01-01"

    return Item(
        id=Item.make_id(url),
        title=title,
        url=url,
        venue="arxiv_standalone",  # internal slug for "academic paper"; pub venue carries the real name
        date=date_str,
        authors=authors,
        description=(p.get("abstract") or "")[:400],
        subfield="other",
        arxiv_id=arxiv_id,
        arxiv_url=arxiv_url,
        affiliations=affiliations,
        publication_venue=publication_venue,
    )


# ─── Haiku triage ────────────────────────────────────────────────────
TRIAGE_PROMPT = """You are a relevance triage agent for an AI-safety researcher's feed. You see a batch of recent papers; your job is to keep the ones this specific user is most likely to want to read, and drop the rest.

## User profile

```json
{profile_json}
```

## Process

For each paper, judge:
  • Author match: is any author in the user's trusted_authors? → strong keep signal
  • Affiliation match: is the paper from a known safety/alignment org? Recognized labs include Anthropic, Google DeepMind, OpenAI safety, METR, Apollo Research, UK AISI, US AISI / NIST CAISI, Redwood Research, Goodfire, FAR AI, MATS, Palisade, Truthful AI / Owain Evans group, Timaeus, Gray Swan, AE Studio. Also recognize specific safety research groups embedded in big labs (e.g. Nanda's GDM interp team).
  • Topic match: does the title/abstract align with the user's research_areas / keywords?
  • Subfield: prefer items in subfields with high subfield_weights in their profile.
  • Publication venue: if `publication_venue` is a top-tier ML/AI venue (NeurIPS, ICML, ICLR, JMLR, TMLR, AAAI, COLT), that's a slop-reduction signal — moderately bias toward keeping. If it's a random workshop or non-ML venue (e.g. an engineering or medical conference), that's a slop signal — bias against keeping unless author/topic match is strong.
  • Dislikes: if the paper matches any phrase in profile.dislikes, reject.

Be permissive on borderline cases — curation downstream will refine further. But drop papers that are clearly off-topic (generic ML, capabilities-only work, unrelated subfields).

Aim to keep roughly {target_keep} papers. Going slightly above or below is fine; quality matters more than hitting the exact count.

## Papers ({n_papers})

```json
{papers_json}
```

## Output

Return ONLY a JSON object, no surrounding prose:

```json
{{
  "keep": ["<id>", "<id>", ...],
  "notes": "<one sentence: anything notable about this batch>"
}}
```

Output the JSON directly. No markdown fences, no preamble.
"""


async def _triage_papers(items: list[Item], profile: Profile) -> list[str]:
    """Single Haiku call: read item metadata, return a list of Item.id values to keep."""
    if not items:
        return []
    target = max(30, min(120, int(len(items) * 0.20)))  # ~20% retention (S2 is precleaner than raw arxiv)
    progress.log(f"s2: triage on {len(items)} papers (target ≈{target})", phase="gather-arxiv-triage")

    light = [{
        "id": it.id,
        "title": it.title,
        "authors": it.authors[:6],
        "affiliations": it.affiliations[:3],
        "publication_venue": it.publication_venue,
        "abstract": (it.description or "")[:300],
        "date": it.date,
    } for it in items]

    profile_view = {
        "research_areas": profile.research_areas,
        "keywords": profile.keywords,
        "trusted_authors": [a.name for a in profile.trusted_authors],
        "subfield_weights": profile.subfield_weights,
        "dislikes": profile.dislikes,
    }

    prompt = TRIAGE_PROMPT.format(
        profile_json=json.dumps(profile_view, indent=2),
        target_keep=target,
        n_papers=len(light),
        papers_json=json.dumps(light, indent=1),
    )

    from anthropic import AsyncAnthropic
    client = AsyncAnthropic()
    resp = await client.messages.create(
        model=TRIAGE_MODEL,
        max_tokens=4000,
        messages=[{"role": "user", "content": prompt}],
    )
    text = "".join(b.text for b in resp.content if hasattr(b, "text"))
    text = re.sub(r"^```(?:json)?\n", "", text.strip()).rstrip("`\n ")
    try:
        out = json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if not m:
            progress.log("s2: triage parse failed; keeping all candidates")
            return [it.id for it in items]
        out = json.loads(m.group(0))

    if note := out.get("notes"):
        progress.log(f"s2 triage notes: {note[:160]}")
    return [str(x) for x in out.get("keep", [])]


async def gather_forum(window_start: str, window_end: str, min_karma: int = 25) -> list[Item]:
    """Pull AF posts via GreaterWrong (alignmentforum.org's GraphQL is blocked)."""
    progress.log(f"forum: fetching AF posts (karma ≥ {min_karma})", phase="gather-forum")
    items: list[Item] = []
    url = (
        f"https://www.greaterwrong.com/index?view=alignment-forum"
        f"&sortedBy=top&after={window_start}&before={window_end}"
    )
    async with httpx.AsyncClient(timeout=30, headers={"User-Agent": "Mozilla/5.0 (safety-feed)"}) as c:
        try:
            r = await c.get(url, follow_redirects=True)
            r.raise_for_status()
        except Exception as e:
            print(f"  [forum] fetch failed: {e}")
            return []
        blocks = re.findall(
            r'<div class="post-listing"(.*?)(?=<div class="post-listing"|<footer)',
            r.text, re.DOTALL,
        )
        for block in blocks[:50]:
            m_title = re.search(r'<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', block, re.DOTALL)
            if not m_title:
                continue
            relurl, title = m_title.group(1), re.sub(r"<[^>]+>", "", m_title.group(2)).strip()
            karma_m = re.search(r"karma[^0-9-]*(-?\d+)", block, re.I)
            karma = int(karma_m.group(1)) if karma_m else None
            if karma is not None and karma < min_karma:
                continue
            comments_m = re.search(r"(\d+)\s*comments?", block, re.I)
            date_m = re.search(r"(\d{4}-\d{2}-\d{2})", block)
            date_str = date_m.group(1) if date_m else window_end
            if not (window_start <= date_str <= window_end):
                continue
            author_m = re.search(r'class="author"[^>]*>([^<]+)<', block) or re.search(r'>by\s+([^<]+)<', block)
            authors = [author_m.group(1).strip()] if author_m else []

            af_url = f"https://www.alignmentforum.org{relurl}" if relurl.startswith("/") else relurl
            items.append(Item(
                id=Item.make_id(af_url),
                title=title, url=af_url, venue="alignment_forum",
                date=date_str, authors=authors, description="",
                subfield="other",
                af_karma=karma,
                af_comments=int(comments_m.group(1)) if comments_m else None,
                af_url=af_url,
            ))
    progress.log(f"forum: {len(items)} AF posts kept")
    return items


# ─────────────────────────────────────────────────────────────────
# gather_sources: feeds-first + per-source Haiku fallback
#   - For each profile source (skip arxiv/AF — they have own gatherers):
#       If source.feed_url known → fetch feed, parse via feedparser.
#       Else → fetch HTML, truncate, single Haiku call extracts items.
#         (The discovered feed_url is NOT auto-saved back to the profile —
#          the user can do that or onboarding can pre-fill it.)
# ─────────────────────────────────────────────────────────────────

SKIP_SOURCE_SLUGS = {"arxiv_standalone", "alignment_forum", "lesswrong"}
SKIP_SOURCE_HOSTS = ("arxiv.org", "alignmentforum.org", "lesswrong.com",
                       "greaterwrong.com", "forum.effectivealtruism.org")


def _is_arxiv_or_forum(url: str) -> bool:
    from urllib.parse import urlparse
    host = (urlparse(url).netloc or "").lower()
    return any(h in host for h in SKIP_SOURCE_HOSTS)


async def gather_sources(profile: Profile, window_start: str, window_end: str) -> list[Item]:
    # arXiv + AF + LW have dedicated gatherers; skip by slug OR by URL host
    # (the onboarding agent sometimes invents slugs like "arxiv_cs_ai" that
    # bypass the slug-based skip list — host check catches those.)
    targets = [
        s for s in profile.sources
        if s.trust >= 25
        and s.slug not in SKIP_SOURCE_SLUGS
        and not _is_arxiv_or_forum(s.url)
    ]
    targets.sort(key=lambda s: -s.trust)
    targets = targets[:12]
    if not targets:
        return []

    progress.log(f"sources: fetching {len(targets)} lab/org sites", phase="gather-sources")
    results = await asyncio.gather(
        *[_fetch_source(s, window_start, window_end) for s in targets],
        return_exceptions=True,
    )
    items: list[Item] = []
    for src, res in zip(targets, results):
        if isinstance(res, Exception):
            progress.log(f"sources: {src.slug} failed: {res}")
            continue
        items.extend(res)
    progress.log(f"sources: {len(items)} items across {len(targets)} sites")
    return items


async def _fetch_source(src: Source, window_start: str, window_end: str) -> list[Item]:
    """Return Items from one source. Feed-first; HTML+Haiku fallback."""
    if src.feed_url:
        items = await _fetch_via_feed(src, src.feed_url, window_start, window_end)
        if items:
            return items
        progress.log(f"sources: {src.slug} feed empty, falling back to HTML")

    # Try common feed URLs before going to HTML extraction
    feed_candidate = await _discover_feed(src.url)
    if feed_candidate:
        progress.log(f"sources: {src.slug} discovered feed at {feed_candidate}")
        items = await _fetch_via_feed(src, feed_candidate, window_start, window_end)
        if items:
            return items

    # Fallback: fetch HTML, ask Haiku
    return await _fetch_via_html(src, window_start, window_end)


# ── feed path ────────────────────────────────────────────────────────
import feedparser


async def _fetch_via_feed(src: Source, feed_url: str, ws: str, we: str) -> list[Item]:
    progress.log(f"sources: {src.slug} via feed: {feed_url}")
    async with httpx.AsyncClient(timeout=20, follow_redirects=True,
                                    headers={"User-Agent": "safety-feed/0.1"}) as c:
        try:
            r = await c.get(feed_url)
            r.raise_for_status()
        except Exception as e:
            progress.log(f"sources: {src.slug} feed fetch failed: {e}")
            return []

    parsed = feedparser.parse(r.content)
    items = []
    for entry in parsed.entries:
        title = (entry.get("title") or "").strip()
        link = (entry.get("link") or "").strip()
        if not title or not link:
            continue
        # Date parsing — feedparser normalizes most fields
        d = entry.get("published_parsed") or entry.get("updated_parsed")
        date_str = ""
        if d:
            date_str = f"{d.tm_year:04d}-{d.tm_mon:02d}-{d.tm_mday:02d}"
        if not date_str or not (ws <= date_str <= we):
            continue
        summary = (entry.get("summary") or "").strip()
        # Strip HTML
        summary = re.sub(r"<[^>]+>", "", summary)[:300]
        authors = [a.get("name", "") for a in entry.get("authors", []) if a.get("name")]
        items.append(Item(
            id=Item.make_id(link),
            title=title, url=link, venue=src.slug, date=date_str,
            authors=authors, description=summary,
            subfield="other",
        ))
    return items


async def _discover_feed(page_url: str) -> str | None:
    """Try common feed paths + parse <link rel='alternate'> for one URL."""
    # Common direct paths to try
    base = page_url.rstrip("/")
    candidates = [
        f"{base}/feed", f"{base}/rss", f"{base}/feed.xml", f"{base}/atom.xml",
        f"{base}/rss.xml", f"{base}/index.xml",
    ]
    # Also derive site root if URL is /research or /blog
    from urllib.parse import urlparse
    parsed = urlparse(page_url)
    if parsed.path and parsed.path != "/":
        root = f"{parsed.scheme}://{parsed.netloc}"
        candidates += [f"{root}/feed", f"{root}/rss", f"{root}/feed.xml"]
    async with httpx.AsyncClient(timeout=10, follow_redirects=True,
                                    headers={"User-Agent": "safety-feed/0.1"}) as c:
        for url in candidates:
            try:
                r = await c.head(url)
                if r.status_code == 200 and any(t in r.headers.get("content-type", "").lower()
                                                  for t in ("xml", "rss", "atom")):
                    return url
            except Exception:
                continue
        # Parse <link rel="alternate"> from the page itself
        try:
            r = await c.get(page_url)
            r.raise_for_status()
            html = r.text[:80_000]
            m = re.search(
                r'<link[^>]+rel=["\']alternate["\'][^>]+type=["\'](?:application/(?:rss|atom)\+xml)["\'][^>]+href=["\']([^"\']+)',
                html, re.I,
            )
            if not m:
                m = re.search(
                    r'<link[^>]+type=["\'](?:application/(?:rss|atom)\+xml)["\'][^>]+href=["\']([^"\']+)',
                    html, re.I,
                )
            if m:
                href = m.group(1)
                if href.startswith("/"):
                    href = f"{parsed.scheme}://{parsed.netloc}{href}"
                return href
        except Exception:
            pass
    return None


# ── HTML+Haiku path ──────────────────────────────────────────────────
HTML_EXTRACT_PROMPT = """You'll receive HTML from a research/blog index page from an AI-safety lab. Extract items published between {ws} and {we} (inclusive). Skip product launches, hiring posts, generic announcements.

Output strict JSON only, no preamble, no fences:
```json
{{
  "items": [{{
    "title": "...",
    "url": "<absolute URL — if relative in HTML, expand using {base_url}>",
    "date": "YYYY-MM-DD",
    "authors": ["..."],
    "description": "<1 short sentence>",
    "subfield": "<one of: mech_interp, evals, control, alignment_training, scalable_oversight, deceptive_alignment, agent_safety, model_organisms, governance, strategy, other>"
  }}]
}}
```

If the page has no items in the window, output: {{"items": []}}.

## HTML (truncated)

{html}
"""


async def _fetch_via_html(src: Source, ws: str, we: str) -> list[Item]:
    progress.log(f"sources: {src.slug} via HTML+Haiku: {src.url}")
    async with httpx.AsyncClient(timeout=20, follow_redirects=True,
                                    headers={"User-Agent": "safety-feed/0.1"}) as c:
        try:
            r = await c.get(src.url)
            r.raise_for_status()
        except Exception as e:
            progress.log(f"sources: {src.slug} HTML fetch failed: {e}")
            return []
    # Truncate aggressively — the article list is usually near the top
    html = r.text[:50_000]
    prompt = HTML_EXTRACT_PROMPT.format(ws=ws, we=we, base_url=src.url, html=html)

    from anthropic import AsyncAnthropic
    client = AsyncAnthropic()
    try:
        resp = await client.messages.create(
            model=TRIAGE_MODEL,
            max_tokens=2000,
            messages=[{"role": "user", "content": prompt}],
        )
    except Exception as e:
        progress.log(f"sources: {src.slug} Haiku extract failed: {e}")
        return []
    text = "".join(b.text for b in resp.content if hasattr(b, "text"))
    text = re.sub(r"^```(?:json)?\n", "", text.strip()).rstrip("`\n ")
    try:
        out = json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if not m:
            progress.log(f"sources: {src.slug} extract parse failed; skipping")
            return []
        out = json.loads(m.group(0))

    items = []
    for it in out.get("items", []):
        d = it.get("date", "")
        if not (ws <= d <= we):
            continue
        items.append(Item(
            id=Item.make_id(it["url"]),
            title=it["title"], url=it["url"], venue=src.slug, date=d,
            authors=it.get("authors", []), description=it.get("description", ""),
            subfield=it.get("subfield", "other"),
        ))
    return items


# ─── Dedup cross-posts ───────────────────────────────────────────────
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


def dedup(items: list[Item], profile: Profile) -> list[Item]:
    """Cluster cross-posts. The cluster's primary uses the highest-trust venue."""
    clusters: list[list[Item]] = []
    for it in items:
        for cluster in clusters:
            if _same_work(it, cluster[0]):
                cluster.append(it); break
        else:
            clusters.append([it])

    out = []
    for cluster in clusters:
        cluster.sort(key=lambda i: -profile.venue_trust(i.venue))
        primary = cluster[0]
        seen = {primary.url}
        for c in cluster[1:]:
            if c.url in seen:
                continue
            primary.extra_venues.append({"venue": c.venue, "url": c.url, "date": c.date})
            seen.add(c.url)
            # bubble up AF data + arxiv if present
            if c.af_karma and not primary.af_karma:
                primary.af_karma, primary.af_comments, primary.af_url = c.af_karma, c.af_comments, c.af_url
            if c.arxiv_id and not primary.arxiv_id:
                primary.arxiv_id, primary.arxiv_url = c.arxiv_id, c.arxiv_url
        out.append(primary)
    return out


# ─── Curation agent ──────────────────────────────────────────────────
CURATION_PROMPT = """You are a curation agent for an AI-safety research feed. Given the user profile and a candidate set (from multiple sources: arXiv, AF, lab blogs), pick what this user actually wants to see, in ranked order, with a short reason per item.

## Profile

```json
{profile_json}
```

## What the user has previously hidden (with their reasons)

```json
{hidden_json}
```

Use these as semantic patterns to avoid in this batch. If a candidate matches the gist of one of these (e.g. user hid "image-segmentation SAE" with reason "not LLM-focused", don't surface similar vision-only papers), reject it.

## Candidates ({n} items from arXiv + AF + lab/org pages, {window_start} → {window_end})

```json
{candidates_json}
```

## Principles

1. **Quality over balance.** Do not include items just to have representation from each source. If arXiv this week is all noise, surface zero arXiv items.
2. **Quality over quantity.** If only 3 things are worth surfacing, output 3. If 15, output 15. Target 5-15 but treat it as soft.
3. **Author match is a strong signal**, not an automatic keep. A paper by a trusted author on an irrelevant topic is still irrelevant.
4. **Affiliation matters.** Papers from known safety labs are higher signal even from new authors.
5. **Publication venue matters.** When a candidate has a `publication_venue` (e.g. "NeurIPS 2026", "ICML", "TMLR"), use it as a slop-reduction signal — top-tier ML venues (NeurIPS / ICML / ICLR / JMLR / TMLR / AAAI / COLT) raise confidence; obscure workshops or non-ML venues lower it.
6. **Reject dislikes + hidden patterns.** Anything matching profile.dislikes phrases OR the gist of `hidden_with_reason` items → reject.
6. **Flag slop.** If a source's candidates this week look mostly slop, say so in `quality_notes`.
7. **(Optional)** Up to 3 WebSearch calls if you see real gaps. Don't use them just because.

## For each kept item

Give a 1-2 line `relevance_reason` written for *this user*: name the specific reason ("matches your work on character training" / "by Greenblatt — you follow him for control work" / "Anthropic interp team — your stated focus").

Rank from 1 (most important) downward. Don't ties.

## Profile growth

Suggest additions only when supported by ≥2 of your picked items:
- Authors who appeared in multiple high-rank picks but aren't in trust list
- Technical phrases that surfaced in multiple high-rank abstracts but aren't in profile.keywords

## Output — write JSON to: {out_path}

```json
{{
  "items": [{{
    "title": "...", "url": "...", "venue": "...", "date": "YYYY-MM-DD",
    "authors": [...], "description": "...", "subfield": "...",
    "af_karma": <int|null>, "af_comments": <int|null>, "af_url": "...",
    "arxiv_id": "...|null", "arxiv_url": "...|null",
    "affiliations": [...],
    "extra_venues": [...],
    "relevance_reason": "<1-2 lines, specific to this user>",
    "curation_rank": <1-based int>
  }}],
  "rejected_count": <int>,
  "added_via_search": <int>,
  "quality_notes": "<honest read on this batch>",
  "suggested_keywords": [...],
  "suggested_authors": [...],
  "agent_notes": "<2-3 sentences>"
}}
```

After writing, final message: "Curation complete: N items."
"""


async def curate(profile: Profile, candidates: list[Item], window_start: str, window_end: str) -> list[Item]:
    progress.log(f"curate: agent reviewing {len(candidates)} candidates", phase="curate")
    out_path = DATA / "curated_items.json"
    if out_path.exists():
        out_path.unlink()

    # Pass candidates as plain dicts (LLM-friendly)
    cand_dicts = [{
        "title": c.title, "url": c.url, "venue": c.venue, "date": c.date,
        "authors": c.authors, "description": c.description, "subfield": c.subfield,
        "af_karma": c.af_karma, "af_comments": c.af_comments, "af_url": c.af_url,
        "arxiv_id": c.arxiv_id, "arxiv_url": c.arxiv_url,
        "publication_venue": c.publication_venue,
        "extra_venues": c.extra_venues,
    } for c in candidates]

    prompt = CURATION_PROMPT.format(
        profile_json=json.dumps(profile.to_dict(), indent=2),
        hidden_json=json.dumps(profile.hidden_with_reason[-30:], indent=2),  # last 30 to keep prompt size bounded
        n=len(candidates),
        window_start=window_start, window_end=window_end,
        candidates_json=json.dumps(cand_dicts, indent=2),
        out_path=str(out_path),
    )

    await progress.stream(
        query(prompt=prompt, options=ClaudeAgentOptions(
            allowed_tools=["WebSearch", "WebFetch", "Write"],
            permission_mode="bypassPermissions",
            cwd=str(ROOT), max_turns=12, model=MODEL,
        )),
        label="curate",
    )

    if not out_path.exists():
        for alt in [Path.home() / "data" / "curated_items.json"]:
            if alt.exists():
                out_path.write_text(alt.read_text()); alt.unlink(); break
        else:
            raise RuntimeError("curation agent didn't produce output")

    out = json.loads(out_path.read_text())
    items = [Item.from_raw(it) for it in out.get("items", [])]
    progress.log(f"curate: {len(items)} chosen. {out.get('quality_notes', '')[:160]}")

    # Save profile-growth suggestions for the UI to surface.
    suggestions = {
        "suggested_keywords": out.get("suggested_keywords", []),
        "suggested_authors": out.get("suggested_authors", []),
        "quality_notes": out.get("quality_notes", ""),
        "agent_notes": out.get("agent_notes", ""),
        "rejected_count": out.get("rejected_count", 0),
    }
    (DATA / "profile_suggestions.json").write_text(json.dumps(suggestions, indent=2))
    if suggestions["suggested_keywords"] or suggestions["suggested_authors"]:
        progress.log(
            f"profile suggestions: +{len(suggestions['suggested_keywords'])} keywords, "
            f"+{len(suggestions['suggested_authors'])} authors"
        )
    return items


# ─── Orchestrator ────────────────────────────────────────────────────
async def discover(window_days: int = 30, wipe: bool = True) -> int:
    """Full pipeline: gather → dedup → curate → write to DB. Returns count."""
    profile = store.load_profile()
    if profile is None:
        raise RuntimeError("No active profile. Build one first.")

    end_d = date.fromisoformat(_today_iso())
    start = (end_d - timedelta(days=window_days - 1)).isoformat()
    end = end_d.isoformat()
    print(f"\n═══ Discovery: window {start} → {end}\n")

    # Gather in parallel (sources = lab/org pages; replaces gather_labs)
    arxiv_items, forum_items, source_items = await asyncio.gather(
        gather_arxiv(profile, start, end),
        gather_forum(start, end),
        gather_sources(profile, start, end),
    )
    raw = arxiv_items + forum_items + source_items
    progress.log(f"merged {len(arxiv_items)} arxiv + {len(forum_items)} forum + {len(source_items)} sources = {len(raw)}")

    deduped = dedup(raw, profile)
    progress.log(f"dedup: {len(deduped)} unique candidates")

    if not deduped:
        progress.log("no candidates, skipping curation")
        return 0

    curated = await curate(profile, deduped, start, end)
    progress.log(f"writing {len(curated)} items to DB", phase="write")

    from . import scoring
    total = len(curated)
    n = 0
    with store.conn() as c:
        if wipe:
            c.execute("DELETE FROM items"); c.execute("DELETE FROM items_fts")
        for it in curated:
            # curation_rank and relevance_reason were set in Item.from_raw inside curate()
            it.score = scoring.score_from_rank(it.curation_rank, total)
            store.save_item(it, c)
            n += 1
    progress.log(f"discovery complete — {n} items in DB")
    return n


def _today_iso() -> str:
    from .config import TODAY
    return TODAY
