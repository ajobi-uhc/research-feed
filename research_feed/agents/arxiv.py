"""Papers lane — OpenAlex date-windowed fetch → Haiku coarse filter → Sonnet propose.

OpenAlex gives a deterministic, complete, date-windowed candidate set (the recall
backbone) — free, no auth, no rate-limit throttle, and with authors/abstracts/venue
attached; Haiku cuts volume cheaply; a no-tool Sonnet pass does the relevance
judgment and emits the lane's structured report.
Same signature as the other lanes, so digest.py is unchanged.
"""
from __future__ import annotations
import asyncio
import json

from anthropic import AsyncAnthropic

from ..fetch.openalex import fetch_papers_window
from ..config import ANTHROPIC_API_KEY, MODEL_HAIKU, MODEL_SONNET
from ..prompts.arxiv import ARXIV_FILTER, ARXIV_PROPOSE
from .runner import (run_agent, parse_json, log_milestone, log_file,
                     structural_drop, empty_report, finalize_lane_report)

_FILTER_BATCH = 50
_PROPOSE_CAP = 70   # max candidates handed to the Sonnet propose pass


async def run_arxiv_agent(
    *, profile: dict, window: dict, already_seen_titles: list[str], log_path=None,
) -> tuple[dict, dict]:
    # Build the query set from `interests`. current_question rides along as ONE
    # extra query — a soft lean, not a filter on every result.
    queries, _seen = [], set()
    terms = list(profile.get("interests", []))
    cq = (profile.get("current_question") or "").strip()
    if cq:
        terms.append(cq)
    for q in terms:
        ql = (q or "").strip().lower()
        if ql and ql not in _seen:
            _seen.add(ql)
            queries.append(q.strip())
    ws, we = window["start"], window["end"]

    # 1. Deterministic fetch — the recall backbone (OpenAlex, date-windowed).
    log_milestone("arxiv", f"fetching papers via OpenAlex: {len(queries)} queries "
                           f"(interests + soft current-question lean), windowed")
    candidates, queries_run = await fetch_papers_window(queries, ws, we)
    log_file(log_path, "arxiv", "FETCH",
              f"{len(candidates)} in-window candidates\n" + json.dumps(queries_run, indent=2))

    # Structural drop: already-seen + filter_outs (free, no model).
    pre, struct_dropped = structural_drop(candidates, already_seen_titles, profile.get("filter_outs", []))
    log_milestone("arxiv", f"fetched {len(candidates)} candidates "
                           f"({struct_dropped} dropped: seen/filter-out)")

    if not pre:
        return empty_report(queries_run,
            interpretation="No in-window paper candidates after fetch.",
            coverage="No papers matched the profile's research areas in the window.")

    # 2. Haiku coarse filter — cut volume cheaply.
    survivors, haiku_dropped = await _haiku_filter(pre, profile)
    # Cap what the propose agent sees (most recent first) so its call stays bounded
    # regardless of window size. 7-day windows rarely hit this; 30-day evals can.
    capped = 0
    if len(survivors) > _PROPOSE_CAP:
        survivors.sort(key=lambda c: c.get("date", ""), reverse=True)
        capped = len(survivors) - _PROPOSE_CAP
        survivors = survivors[:_PROPOSE_CAP]
    log_milestone("arxiv", f"haiku filter: {len(pre)} → {len(survivors)} candidates"
                           + (f" (capped {capped} oldest)" if capped else ""))
    log_file(log_path, "arxiv", "HAIKU_FILTER",
              f"kept {len(survivors)} of {len(pre)}; dropped {len(haiku_dropped)}\n" +
              "\n".join(f"  drop: {d.get('reason','')[:80]}" for d in haiku_dropped[:40]))

    # 3. Sonnet propose — judgment + structured report over the survivors.
    report, meta = await run_agent(
        system_prompt=ARXIV_PROPOSE,
        user_message={
            "profile": {k: profile.get(k) for k in
                        ("user_summary", "interests", "current_question", "filter_outs")},
            "window": window,
            "candidates": survivors,
        },
        model=MODEL_SONNET,
        allowed_tools=[],          # pure reasoning over the given set — no search
        max_turns=2,
        max_budget_usd=2.0,
        thinking_budget=6000,
        label="arxiv",
        log_path=log_path,
    )

    # Assemble the lane's report: deterministic trace + Sonnet judgment.
    report = finalize_lane_report(report, queries_run,
        f"fetched {len(candidates)} in-window; {struct_dropped} dropped (seen/filter-out), "
        f"{len(haiku_dropped)} dropped by coarse filter; ")
    return report, meta


# ── Haiku coarse filter (batched, parallel) ────────────────────────────
async def _haiku_filter(candidates: list[dict], profile: dict) -> tuple[list[dict], list[dict]]:
    batches = [candidates[i:i + _FILTER_BATCH] for i in range(0, len(candidates), _FILTER_BATCH)]
    results = await asyncio.gather(*[_haiku_batch(b, profile) for b in batches])

    keep_ids: set[str] = set()
    drop_map: dict[str, str] = {}
    for kept, dropped in results:
        keep_ids |= kept
        drop_map.update(dropped)

    survivors, dropped = [], []
    for c in candidates:
        cid = c["id"]
        if cid in drop_map and cid not in keep_ids:
            dropped.append({"title": c["title"], "url": c["url"], "reason": drop_map[cid]})
        else:
            survivors.append(c)        # default-keep when unclassified (recall-preserving)
    return survivors, dropped


async def _haiku_batch(batch: list[dict], profile: dict) -> tuple[set[str], dict[str, str]]:
    items = [{"id": c["id"], "title": c["title"], "summary": c.get("summary", "")[:300],
              "categories": c.get("categories", []), "venue_detail": c.get("venue_detail", "")}
             for c in batch]
    prompt = ARXIV_FILTER.format(
        profile_summary=profile.get("user_summary", ""),
        interests=json.dumps(profile.get("interests", [])),
        filter_outs=json.dumps(profile.get("filter_outs", [])),
        n_items=len(items),
        items_json=json.dumps(items, indent=2),
    )
    client = AsyncAnthropic(api_key=ANTHROPIC_API_KEY)
    msg = await client.messages.create(
        model=MODEL_HAIKU, max_tokens=3000,
        messages=[{"role": "user", "content": prompt}],
    )
    parsed = parse_json(msg.content[0].text if msg.content else "")
    keep_ids = set(parsed.get("keep", []))
    drop_map = {d["id"]: d.get("reason", "off-profile") for d in parsed.get("drop", []) if d.get("id")}
    return keep_ids, drop_map
