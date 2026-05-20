"""Deterministic paper fetch via OpenAlex — the recall backbone for the papers lane.

OpenAlex indexes arxiv (+ crossref etc.), refreshed ~daily, free, no auth (a
mailto just puts us in the faster polite pool). A date-windowed search returns the
matching set pageable — not a hard relevance-capped slice — so specific papers
don't get buried the way neural search buries them. Bonus over neural search: it
returns authors, abstracts, and the host venue (conference/journal) directly.

Same (queries, ws, we) -> (candidates, queries_run) signature as before, so the
papers lane is unchanged apart from the import.
"""
from __future__ import annotations
import asyncio
import re

import httpx

from ..config import OPENALEX_MAILTO

API = "https://api.openalex.org/works"
_SELECT = ("id,doi,title,display_name,publication_date,authorships,"
           "abstract_inverted_index,primary_location,locations")
# Matches an arxiv id in either the DOI form (10.48550/arxiv.2605.12874) or the
# landing/pdf URL form (arxiv.org/abs/2605.12874) — OpenAlex uses both, and very
# recent papers often have the URL but a null DOI.
_ARXIV_RE = re.compile(r"(?:arxiv\.org/(?:abs|pdf)/|arxiv\.)(\d{4}\.\d{4,5})", re.I)


async def fetch_papers_window(
    queries: list[str], ws: str, we: str,
    *, per_query: int = 200, max_queries: int = 16,
) -> tuple[list[dict], list[dict]]:
    """Returns (candidates, queries_run). One date-windowed search per query;
    results unioned + deduped by arxiv id (falling back to the OpenAlex id)."""
    queries = [q.strip() for q in queries if q and q.strip()][:max_queries]
    if not queries:
        return [], []
    async with httpx.AsyncClient(timeout=30) as c:
        results = await asyncio.gather(
            *[_search_one(c, q, ws, we, per_query) for q in queries],
            return_exceptions=True,
        )
    out: dict[str, dict] = {}
    queries_run: list[dict] = []
    for q, res in zip(queries, results):
        if isinstance(res, Exception):
            queries_run.append({"query": q, "error": repr(res), "results_count": 0})
            continue
        kept = 0
        for cand in res:
            key = cand["arxiv_id"] or cand["id"]
            if key not in out:
                out[key] = cand
                kept += 1
        queries_run.append({"query": q, "results_count": kept})
    return list(out.values()), queries_run


async def _search_one(c: httpx.AsyncClient, query: str, ws: str, we: str, per_page: int) -> list[dict]:
    # `search` is a separate param from `filter`, so commas/colons in the query
    # don't collide with OpenAlex's comma-delimited filter syntax.
    r = await c.get(API, params={
        "search": query,
        "filter": f"from_publication_date:{ws},to_publication_date:{we}",
        "per-page": min(per_page, 200),
        "select": _SELECT,
        "mailto": OPENALEX_MAILTO,
    })
    r.raise_for_status()
    return [_to_candidate(w, query) for w in r.json().get("results", [])]


def _to_candidate(w: dict, query: str) -> dict:
    doi = w.get("doi") or ""
    loc = w.get("primary_location") or {}
    landing = loc.get("landing_page_url") or ""
    m = (_ARXIV_RE.search(doi) or _ARXIV_RE.search(landing)
         or _ARXIV_RE.search(loc.get("pdf_url") or ""))
    aid = m.group(1) if m else None
    title = (w.get("title") or w.get("display_name") or "").strip()
    authors = [a.get("author", {}).get("display_name", "") for a in w.get("authorships", [])]
    authors = [a for a in authors if a][:12]
    source = (loc.get("source") or {}).get("display_name") or ""
    published = _published_venue(w)
    if aid:
        cid = "i_arxiv_" + aid.replace(".", "")
        url = f"https://arxiv.org/abs/{aid}"
        venue, venue_detail = "arxiv", published   # e.g. also accepted at NeurIPS
    else:
        cid = "i_oa_" + (w.get("id") or "").rsplit("/", 1)[-1]
        url = landing or doi or w.get("id") or ""   # real source page, not the bare OpenAlex id
        venue, venue_detail = source, published or source
    return {
        "id": cid,
        "title": title,
        "url": url,
        "venue": venue,
        "date": w.get("publication_date") or "",
        "authors": authors,
        "summary": _abstract(w.get("abstract_inverted_index"))[:600],
        "arxiv_id": aid,
        "categories": [],
        "venue_detail": venue_detail,
        "discovered_via": f"openalex:{query[:40]}",
    }


# Preprint servers / indexes that OpenAlex lists as sources but aren't real venues.
_NON_VENUE = ("arxiv", "biorxiv", "medrxiv", "ssrn", "research square", "preprint", "pubmed", "zenodo")


def _published_venue(w: dict) -> str:
    """If the work is also published/accepted at a real journal or conference
    (NeurIPS/ICML/a journal), return that venue's name — else "". Excludes preprint
    mirrors so a bare arxiv paper isn't mislabeled as 'published'. """
    for loc in w.get("locations") or []:
        src = loc.get("source") or {}
        name = (src.get("display_name") or "").strip()
        if not name or src.get("type") not in ("conference", "journal"):
            continue
        if any(bad in name.lower() for bad in _NON_VENUE):
            continue
        return name
    return ""


def _abstract(inv: dict | None) -> str:
    """OpenAlex stores abstracts as an inverted index {word: [positions]} to dodge
    copyright. Rebuild the running text by sorting words back into position order."""
    if not inv:
        return ""
    pairs = sorted((pos, word) for word, positions in inv.items() for pos in positions)
    return " ".join(word for _, word in pairs)
