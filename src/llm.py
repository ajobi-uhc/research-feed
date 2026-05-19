"""5 named LLM functions. Each: format prompt → call model → parse JSON.

  draft_profile()       — Sonnet via Agent SDK (needs WebFetch on seed papers)
  generate_queries()    — Haiku, plain completion
  cheap_filter()        — Haiku, plain completion
  rank_batch()          — Sonnet, plain completion (batched by caller)

Model assignments come from config.MODELS (env-overridable).
Prompt strings come from prompts.py.
"""

from __future__ import annotations
import json
import re
from pathlib import Path

from anthropic import AsyncAnthropic
from claude_agent_sdk import query as agent_query, ClaudeAgentOptions, AssistantMessage, TextBlock

from .config import MODELS, ROOT, PROFILE_PATH, load_dotenv
from .prompts import PROFILE_DRAFTER, QUERY_GEN, CHEAP_FILTER, HTML_EXTRACT, RANKER
from . import progress

load_dotenv()
_client = AsyncAnthropic()


# ─── helpers ─────────────────────────────────────────────────────────
def _parse_json(text: str) -> dict:
    """Tolerant JSON parsing — strips ```json fences, salvages embedded blobs."""
    text = re.sub(r"^```(?:json)?\n?", "", text.strip()).rstrip("`\n ")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if not m:
            raise
        return json.loads(m.group(0))


async def _completion(role: str, prompt: str, max_tokens: int = 4000) -> str:
    """Plain Messages-API call. Returns the model's text response."""
    resp = await _client.messages.create(
        model=MODELS[role], max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}],
    )
    return "".join(b.text for b in resp.content if hasattr(b, "text"))


# ─── 1. Profile drafter ─────────────────────────────────────────────
async def draft_profile(
    seed_papers: list[str],
    scholar_url: str | None,
    followed_authors: list[str],
    current_question: str,
    filter_outs: list[str],
) -> dict:
    """Run the Agent SDK with WebFetch. Agent reads seed paper abstracts and writes
    profile JSON to PROFILE_PATH (an absolute path passed in the prompt)."""
    PROFILE_PATH.parent.mkdir(parents=True, exist_ok=True)
    if PROFILE_PATH.exists():
        PROFILE_PATH.unlink()

    prompt = PROFILE_DRAFTER.format(
        seed_papers="\n".join(f"- {url}" for url in seed_papers) or "(none provided)",
        scholar_url=scholar_url or "(none)",
        followed_authors=", ".join(followed_authors) or "(none)",
        current_question=current_question or "(none)",
        filter_outs=", ".join(filter_outs) or "(none)",
        output_path=str(PROFILE_PATH),
    )

    progress.log("draft_profile: launching agent (Sonnet + WebFetch)…", phase="onboard")
    options = ClaudeAgentOptions(
        allowed_tools=["WebFetch", "Write"],
        permission_mode="bypassPermissions",
        cwd=str(ROOT),
        max_turns=20,
        model=MODELS["profile_drafter"],
    )
    turn = 0
    async for msg in agent_query(prompt=prompt, options=options):
        if isinstance(msg, AssistantMessage):
            turn += 1
            for block in msg.content:
                if isinstance(block, TextBlock):
                    text = block.text.strip()
                    if text and not _is_noise(text):
                        progress.log(f"draft_profile t{turn}: {text[:200]}")
                elif hasattr(block, "name") and hasattr(block, "input"):
                    line = _summarize_tool(block.name, block.input or {})
                    if line:
                        progress.log(f"draft_profile: {line}")
    progress.log(f"draft_profile: finished after {turn} turns")

    # Recover if agent wrote to home dir
    if not PROFILE_PATH.exists():
        for alt in [Path.home() / "data" / "profile.json"]:
            if alt.exists():
                PROFILE_PATH.write_text(alt.read_text()); alt.unlink(); break
        else:
            raise RuntimeError(f"profile_drafter did not write {PROFILE_PATH}")

    profile = json.loads(PROFILE_PATH.read_text())
    # Preserve raw onboarding inputs (the drafter doesn't write these)
    profile["seed_papers"] = seed_papers
    profile["scholar_url"] = scholar_url
    return profile


def _is_noise(text: str) -> bool:
    t = text.strip()
    if len(t) < 6:
        return True
    noise_pats = (r"directory exists", r"checking the (target |path)",
                    r"writing (the )?(json|file|profile)", r"i'll write")
    return any(re.search(p, t, re.I) for p in noise_pats)


def _summarize_tool(name: str, input_: dict) -> str | None:
    if name == "WebFetch":
        u = (input_.get("url") or "").strip()
        return f"📄 fetch: {u[:120]}" if u else None
    if name == "WebSearch":
        q = (input_.get("query") or "").strip()
        return f"🔍 search: {q[:100]}" if q else None
    if name == "Write":
        p = (input_.get("file_path") or "").strip()
        return f"💾 write: {p[-60:]}" if p else None
    return None


# ─── 2. Query generator ─────────────────────────────────────────────
async def generate_queries(profile: dict) -> list[str]:
    """One Haiku call. Profile → list of S2 search queries."""
    profile_lite = {
        "user_summary": profile.get("user_summary", ""),
        "tags": profile.get("tags", []),
        "research_areas": profile.get("research_areas", []),
        "current_question": profile.get("current_question", ""),
    }
    progress.log("generate_queries: Haiku call…", phase="queries")
    text = await _completion("query_gen",
                              QUERY_GEN.format(profile_json=json.dumps(profile_lite, indent=2)))
    data = _parse_json(text)
    queries = [q for q in data.get("queries", []) if isinstance(q, str) and q.strip()]
    progress.log(f"generate_queries: {len(queries)} queries")
    return queries


# ─── 3. Cheap filter ────────────────────────────────────────────────
async def cheap_filter(profile: dict, items: list[dict], keep_target: int = 200) -> set[str]:
    """One Haiku call. Returns the set of item ids to keep."""
    if not items:
        return set()
    progress.log(f"cheap_filter: Haiku over {len(items)} items…", phase="filter")
    light = [{
        "id": it["id"],
        "title": it["title"],
        "authors": (it.get("authors") or [])[:5],
        "abstract": (it.get("description") or "")[:250],
        "venue": it.get("venue"),
        "date": it.get("date"),
        "publication_venue": it.get("publication_venue"),
    } for it in items]
    text = await _completion("cheap_filter", CHEAP_FILTER.format(
        profile_summary=profile.get("user_summary", ""),
        filter_outs=", ".join(profile.get("filter_outs", [])) or "(none)",
        n_items=len(light),
        items_json=json.dumps(light, indent=1),
        keep_target=keep_target,
    ), max_tokens=8000)
    try:
        data = _parse_json(text)
    except Exception as e:
        progress.log(f"cheap_filter: parse failed ({e!r}); keeping all")
        return {it["id"] for it in items}
    keep = {str(x) for x in data.get("keep", [])}
    progress.log(f"cheap_filter: kept {len(keep)} of {len(items)} — {data.get('notes', '')[:100]}")
    return keep


# ─── 4. HTML extractor (Haiku fallback for feedless sources) ───────
async def extract_items_from_html(
    slug: str, name: str, base_url: str, html: str, ws: str, we: str,
) -> list[dict]:
    """Single Haiku call. Given HTML of an index page, extract in-window items."""
    if not html.strip():
        return []
    MAX = 50_000
    truncated = html[:MAX]
    text = await _completion("html_extract", HTML_EXTRACT.format(
        site_name=name,
        base_url=base_url,
        window_start=ws,
        window_end=we,
        max_chars=MAX,
        html=truncated,
    ), max_tokens=3000)
    try:
        data = _parse_json(text)
    except Exception as e:
        progress.log(f"html_extract: {slug} parse failed: {e!r}")
        return []
    items = data.get("items", []) or []
    return items


# ─── 5. Structured ranker (batched) ────────────────────────────────
async def rank_batch(profile: dict, items: list[dict]) -> list[dict]:
    """One Sonnet call on a batch (~15 items). Returns per-item ranker output."""
    if not items:
        return []
    text = await _completion("ranker", RANKER.format(
        profile_json=json.dumps(profile, indent=2),
        n_items=len(items),
        items_json=json.dumps(items, indent=1),
    ), max_tokens=4500)
    try:
        data = _parse_json(text)
    except Exception as e:
        progress.log(f"rank_batch: parse failed ({e!r})")
        return []
    return data.get("items", [])
