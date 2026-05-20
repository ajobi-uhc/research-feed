"""Deterministic forum fetch via the LessWrong/AF GraphQL API — the recall
backbone for the forum lane.

LessWrong's GraphQL endpoint (which Alignment Forum mirrors) returns posts
date-windowed with karma/comments/excerpt/author attached — no HTML scraping, no
agent. `af:true` selects Alignment Forum; `af:false` is the broader LW firehose,
which we karma-gate. Same (ws, we) -> (candidates, queries_run) shape as the
papers backbone, so the forum lane stays a thin wrapper.
"""
from __future__ import annotations
import asyncio
from datetime import date, timedelta

import httpx

from ..models import hash12

GRAPHQL = "https://www.lesswrong.com/graphql"
HEADERS = {"User-Agent": "research-feed/0.1 (research prototype)", "Content-Type": "application/json"}


async def fetch_forum_window(
    ws: str, we: str,
    *, af_limit: int = 120, lw_limit: int = 200, lw_karma_floor: int = 25, lw_cap: int = 50,
) -> tuple[list[dict], list[dict]]:
    """Returns (candidates, queries_run).

    AF posts in the window are kept wholesale (curated, on-topic by construction);
    the broader LW pull is karma-gated and capped. Deduped by post id (AF wins).
    """
    before = (date.fromisoformat(we) + timedelta(days=1)).isoformat()  # make `we` inclusive
    async with httpx.AsyncClient(timeout=30) as c:
        af, lw = await asyncio.gather(
            _posts(c, af=True, after=ws, before=before, limit=af_limit),
            _posts(c, af=False, after=ws, before=before, limit=lw_limit),
            return_exceptions=True,
        )
    af = [] if isinstance(af, Exception) else af
    lw = [] if isinstance(lw, Exception) else lw

    out: dict[str, dict] = {}
    for p in af:
        if ws <= p["date"] <= we:
            out[p["id"]] = p
    lw_kept = sorted(
        [p for p in lw if ws <= p["date"] <= we and (p.get("karma") or 0) >= lw_karma_floor],
        key=lambda p: p.get("karma") or 0, reverse=True,
    )[:lw_cap]
    for p in lw_kept:
        out.setdefault(p["id"], p)

    queries_run = [
        {"query": f"AF posts {ws}..{we}", "results_count": len([p for p in af if ws <= p['date'] <= we])},
        {"query": f"LW posts {ws}..{we} (karma>={lw_karma_floor})", "results_count": len(lw_kept)},
    ]
    return list(out.values()), queries_run


_QUERY = (
    'query {{ posts(input: {{terms: {{view: "new", af: {af}, '
    'after: "{after}", before: "{before}", limit: {limit}}}}}) {{ results {{ '
    '_id title postedAt baseScore commentCount pageUrl user {{ displayName }} '
    'contents {{ plaintextDescription }} }} }} }}'
)


async def _posts(c: httpx.AsyncClient, *, af: bool, after: str, before: str, limit: int) -> list[dict]:
    query = _QUERY.format(af=str(af).lower(), after=after, before=before, limit=limit)
    for attempt in range(3):
        try:
            r = await c.post(GRAPHQL, json={"query": query}, headers=HEADERS)
        except httpx.HTTPError:
            await asyncio.sleep(2 * (attempt + 1))
            continue
        if r.status_code == 200:
            results = ((r.json().get("data") or {}).get("posts") or {}).get("results") or []
            venue = "alignment_forum" if af else "lesswrong"
            via = "forum:af" if af else "forum:lw"
            return [_to_candidate(p, venue, via) for p in results]
        if r.status_code == 429:
            await asyncio.sleep(3 * (attempt + 1))
            continue
        return []
    return []


def _to_candidate(p: dict, venue: str, via: str) -> dict:
    url = p.get("pageUrl") or ""
    pid = p.get("_id") or hash12(url)
    user = p.get("user") or {}
    return {
        "id": "i_forum_" + pid,
        "title": (p.get("title") or "").strip(),
        "url": url,
        "venue": venue,
        "date": (p.get("postedAt") or "")[:10],
        "authors": [user["displayName"]] if user.get("displayName") else [],
        "summary": ((p.get("contents") or {}).get("plaintextDescription") or "")[:600],
        "karma": p.get("baseScore"),
        "comments": p.get("commentCount"),
        "discovered_via": via,
    }
