"""Registrar — post-curator agent that PROPOSES registry/profile updates.

Pure reasoning over the digest + current registry (no tools). Sonnet — it's a
compact judgment task, not a search task.
"""
from __future__ import annotations

from ..config import MODEL_SONNET
from ..prompts.registrar import PROMPT
from .runner import run_agent


async def run_registrar_agent(
    *,
    kept: list[dict],
    current_sources: list[str],
    current_authors: list[str],
    discovered_sources: list[dict],
    subagent_reports: dict,
    profile: dict,
    log_path=None,
) -> tuple[dict, dict]:
    return await run_agent(
        system_prompt=PROMPT,
        user_message={
            "kept": [{"title": k.get("title"), "url": k.get("url"), "venue": k.get("venue"),
                      "authors": k.get("authors", []), "discovered_via": k.get("discovered_via")}
                     for k in kept],
            "current_sources": current_sources,
            "current_authors": current_authors,
            "discovered_sources": discovered_sources,
            "subagent_reports": subagent_reports,
            "profile": {
                "user_summary": profile.get("user_summary", ""),
                "interests": profile.get("interests", []),
                "filter_outs": profile.get("filter_outs", []),
            },
        },
        model=MODEL_SONNET,
        allowed_tools=[],     # pure reasoning
        max_turns=2,
        max_budget_usd=1.0,
        thinking_budget=4000,
        label="registrar",
        log_path=log_path,
    )
