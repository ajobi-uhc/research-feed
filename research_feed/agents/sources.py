"""Sources subagent — lab/org content. Known URLs are starting points; it can
also search/follow the web for other relevant lab/org work and surface new finds."""
from __future__ import annotations

from ..config import MODEL_SONNET
from ..prompts.sources import PROMPT
from .runner import run_agent

# AF/LW/GreaterWrong are covered deterministically by the forum lane — keep them
# out of the sources agent so it doesn't redundantly (and 429-prone) re-fetch them.
_FORUM_DOMAINS = ("alignmentforum.org", "lesswrong.com", "greaterwrong.com")


def _is_forum(url: str) -> bool:
    u = (url or "").lower()
    return any(d in u for d in _FORUM_DOMAINS)


async def run_sources_agent(
    *, profile: dict, window: dict, already_seen_titles: list[str], log_path=None,
) -> tuple[dict, dict]:
    sources = [s for s in profile.get("sources", []) if not _is_forum(s.get("url", ""))]
    # `why` is passed through now so the agent can use the rationale for relevance.
    return await run_agent(
        system_prompt=PROMPT,
        user_message={
            "profile": slim_profile(profile),
            "known_sources": [{"name": s.get("name", ""), "url": s.get("url", ""),
                               "why": s.get("why", "")} for s in sources],
            "window": window,
            "already_seen_titles": list(already_seen_titles),
        },
        model=MODEL_SONNET,
        allowed_tools=["WebSearch", "WebFetch"],
        max_turns=max(20, 3 * len(sources) + 6),
        max_budget_usd=3.0,
        thinking_budget=4000,
        label="sources",
        log_path=log_path,
    )


def slim_profile(profile: dict) -> dict:
    """Fields each subagent actually needs to judge relevance.

    Shared across sources/forum/arxiv so they all see the same view.
    """
    return {
        "user_summary": profile.get("user_summary", ""),
        "current_question": profile.get("current_question", ""),
        "interests": profile.get("interests", []),
        "authors": [a.get("name", "") for a in profile.get("authors", [])],
        "filter_outs": profile.get("filter_outs", []),
    }
