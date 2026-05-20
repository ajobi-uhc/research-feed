"""Onboarding — turns raw user inputs into a Profile."""
from __future__ import annotations

from ..config import MODEL_SONNET
from ..models import Profile
from ..prompts.onboarding import PROMPT
from .runner import run_agent


async def create_profile(
    *,
    seed_papers: list[str],
    scholar_url: str = "",
    followed_authors: list[str] = (),
    current_question: str = "",
    filter_outs: list[str] = (),
    freeform: str = "",
    log_path=None,
) -> tuple[Profile, dict]:
    parsed, meta = await run_agent(
        system_prompt=PROMPT,
        user_message={
            "seed_papers": list(seed_papers),
            "scholar_url": scholar_url,
            "followed_authors": list(followed_authors),
            "current_question": current_question,
            "filter_outs": list(filter_outs),
            "freeform": freeform,
        },
        model=MODEL_SONNET,
        allowed_tools=["WebSearch", "WebFetch"],
        max_turns=40,
        max_budget_usd=4.0,
        thinking_budget=6000,
        label="onboard",
        log_path=log_path,
    )
    return Profile.from_dict(parsed), meta
