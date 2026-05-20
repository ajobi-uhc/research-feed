"""Forum lane — LW/AF GraphQL fetch → Sonnet propose.

The fetch is deterministic (no agent, no WebFetch): the LessWrong GraphQL API
returns a complete, date-exact, karma-bearing candidate set. A no-tool Sonnet pass
does the relevance judgment (the LW firehose is noisy) and emits the lane's report.
Same signature as the other lanes, so digest.py is unchanged.
"""
from __future__ import annotations
import json

from ..config import MODEL_SONNET
from ..fetch.lesswrong import fetch_forum_window
from ..prompts.forum import FORUM_PROPOSE
from .runner import (run_agent, log_milestone, log_file, structural_drop,
                     empty_report, finalize_lane_report)
from .sources import slim_profile


async def run_forum_agent(
    *, profile: dict, window: dict, already_seen_titles: list[str], log_path=None,
) -> tuple[dict, dict]:
    ws, we = window["start"], window["end"]

    # 1. Deterministic fetch — AF (all) + karma-gated LW, date-windowed.
    log_milestone("forum", "fetching AF + LW via GraphQL (deterministic, windowed)")
    candidates, queries_run = await fetch_forum_window(ws, we)
    log_file(log_path, "forum", "FETCH",
              f"{len(candidates)} in-window posts\n" + json.dumps(queries_run, indent=2))

    # Structural drop: already-seen + filter_outs (free, no model).
    pre, struct_dropped = structural_drop(candidates, already_seen_titles, profile.get("filter_outs", []))
    log_milestone("forum", f"fetched {len(candidates)} posts "
                           f"({struct_dropped} dropped: seen/filter-out)")

    if not pre:
        return empty_report(queries_run,
            interpretation="No in-window forum posts after fetch.",
            coverage="No AF/LW posts matched the window.")

    # 2. Sonnet propose — relevance judgment over the fetched posts (no tools).
    report, meta = await run_agent(
        system_prompt=FORUM_PROPOSE,
        user_message={
            "profile": slim_profile(profile),
            "window": window,
            "candidates": pre,
        },
        model=MODEL_SONNET,
        allowed_tools=[],          # pure reasoning over the given set — no fetch
        max_turns=2,
        max_budget_usd=1.5,
        thinking_budget=4000,
        label="forum",
        log_path=log_path,
    )

    report = finalize_lane_report(report, queries_run,
        f"fetched {len(candidates)} in-window posts; {struct_dropped} dropped (seen/filter-out); ")
    return report, meta
