"""Curator — Opus 4.7, one call over the deduped candidate union.

Produces the final digest: lead + themes + kept + rejection ledger + gaps + coverage.
No tools — pure reasoning over the JSON payload.
"""
from __future__ import annotations

from ..config import MODEL_OPUS
from ..prompts.curator import PROMPT
from .runner import run_agent


async def run_curator_agent(
    *,
    profile: dict,
    window: dict,
    candidates: list[dict],
    subagent_drops: list[dict],
    subagent_reports: dict,
    log_path=None,
) -> tuple[dict, dict]:
    return await run_agent(
        system_prompt=PROMPT,
        user_message={
            "profile": profile,
            "window": window,
            "candidates": candidates,
            "subagent_drops": subagent_drops,
            "subagent_reports": subagent_reports,
        },
        model=MODEL_OPUS,
        allowed_tools=[],   # pure reasoning, no tools
        max_turns=2,
        max_budget_usd=10.0,
        thinking_budget=16000,
        label="curator",
        log_path=log_path,
    )
