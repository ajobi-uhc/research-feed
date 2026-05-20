"""Shared claude-agent-sdk invocation. Each subagent calls run_agent().

Two outputs:
  1. Filtered streaming progress on stdout (one line per significant event)
  2. Full-fidelity log file (system prompt, user msg, every block, every tool
     result, final JSON) when log_path is given — used by evals for debugging.
"""
from __future__ import annotations
import json
import re
import time
from pathlib import Path

from claude_agent_sdk import (
    query, ClaudeAgentOptions, AssistantMessage, UserMessage, TextBlock,
    ToolUseBlock, ToolResultBlock, ThinkingBlock, ResultMessage,
)

# Tools blocked across every agent. Web tools are allow-listed per-agent.
_BLOCKED = [
    "Bash", "Write", "Edit", "Read", "Glob", "Grep",
    "NotebookEdit", "TodoWrite",
    "TaskCreate", "TaskUpdate", "TaskGet", "TaskList",
    "ExitPlanMode",
]


async def run_agent(
    *,
    system_prompt: str,
    user_message: str | dict,
    model: str,
    allowed_tools: list[str],
    max_turns: int = 30,
    max_budget_usd: float = 3.0,
    thinking_budget: int = 8000,
    label: str = "agent",
    log_path: Path | str | None = None,
) -> tuple[dict, dict]:
    """Run an agent end-to-end. Returns (parsed_json_output, meta).

    Streams milestones to stdout (with [label] prefix) and, if log_path is set,
    appends full-fidelity entries (system prompt, every tool input/result,
    every thinking/text block, final JSON) to that file. Multiple agents can
    safely share the same log file — entries are atomic single writes.
    """
    if isinstance(user_message, dict):
        user_message = json.dumps(user_message, indent=2)
    if log_path is not None:
        log_path = Path(log_path)

    options = ClaudeAgentOptions(
        tools={"type": "preset", "preset": "claude_code"},
        allowed_tools=allowed_tools,
        disallowed_tools=[t for t in _BLOCKED if t not in allowed_tools],
        system_prompt=system_prompt,
        model=model,
        max_turns=max_turns,
        max_budget_usd=max_budget_usd,
        permission_mode="bypassPermissions",
        setting_sources=[],
        thinking={"type": "enabled", "budget_tokens": thinking_budget} if thinking_budget else None,
    )

    last_text, cost, turns, error = "", 0.0, 0, None
    t0 = time.time()
    log_milestone(label, f"start (model={model.split('-')[1] if '-' in model else model}, "
                         f"tools={','.join(allowed_tools) or 'none'}, max_turns={max_turns})")
    log_file(log_path, label, "START",
              f"model={model}\ntools={allowed_tools}\nmax_turns={max_turns}\n"
              f"max_budget_usd={max_budget_usd}\nthinking_budget={thinking_budget}")
    log_file(log_path, label, "SYSTEM_PROMPT", system_prompt)
    log_file(log_path, label, "USER_MESSAGE", user_message)

    async for msg in query(prompt=user_message, options=options):
        if isinstance(msg, AssistantMessage):
            last_text = _log_assistant(msg, label, log_path) or last_text
        elif isinstance(msg, UserMessage):
            _log_tool_results(msg, label, log_path)
        elif isinstance(msg, ResultMessage):
            cost = msg.total_cost_usd or 0.0
            turns = msg.num_turns or 0
            error = (msg.stop_reason or "agent errored") if msg.is_error else error

    elapsed = time.time() - t0
    suffix = f" ERROR={error}" if error else ""
    log_milestone(label, f"done: {turns} turns, ${cost:.2f}, {elapsed:.0f}s{suffix}")
    log_file(log_path, label, "DONE",
              f"turns={turns}\ncost_usd={cost}\nelapsed_s={elapsed:.0f}\nerror={error}")

    return parse_json(last_text), {
        "turns": turns, "cost_usd": cost, "last_text": last_text, "error": error,
    }


def structural_drop(candidates: list[dict], already_seen_titles: list[str],
                    filter_outs: list[str]) -> tuple[list[dict], int]:
    """Free pre-filter shared by the lanes: drop already-seen titles and
    filter_out matches (title or summary). Returns (survivors, n_dropped)."""
    seen = {t.lower() for t in already_seen_titles}
    outs = [f.lower() for f in filter_outs]
    kept, dropped = [], 0
    for c in candidates:
        title = c["title"].lower()
        if title in seen or any(f in title or f in c.get("summary", "").lower() for f in outs):
            dropped += 1
            continue
        kept.append(c)
    return kept, dropped


# ── lane-report helpers (shared by the arxiv/forum lanes) ───────────────
def empty_report(queries_run: list[dict], *, interpretation: str, coverage: str) -> tuple[dict, dict]:
    """The (report, meta) a lane returns when its fetch found nothing in-window."""
    return ({"profile_interpretation": interpretation,
             "searches_performed": queries_run, "kept": [],
             "considered_but_excluded": [], "excluded_aggregate": "",
             "coverage_notes": coverage},
            {"turns": 0, "cost_usd": 0.0})


def finalize_lane_report(report: dict, queries_run: list[dict], drop_prefix: str) -> dict:
    """Backfill the keys the curator expects and prepend the deterministic drop
    trace to whatever the model wrote in `excluded_aggregate`."""
    report.setdefault("kept", [])
    report.setdefault("considered_but_excluded", [])
    report["searches_performed"] = queries_run
    report["excluded_aggregate"] = (drop_prefix + report.get("excluded_aggregate", "")).strip()
    return report


# ── log helpers ────────────────────────────────────────────────────────
# Optional sink for milestone lines — the web app sets this so the /running
# page can stream live progress. Single-run-at-a-time (RUN_STATE guards it),
# so a module global is fine.
_PROGRESS_SINK = None
_STAGE_SINK = None


def set_progress_sink(fn) -> None:
    """fn(line: str) is called for each milestone line, or None to clear."""
    global _PROGRESS_SINK
    _PROGRESS_SINK = fn


def set_stage_sink(fn) -> None:
    """fn(name, status, detail) is called on each stage transition, or None to clear."""
    global _STAGE_SINK
    _STAGE_SINK = fn


def log_stage(name: str, status: str, detail: str = "") -> None:
    """Mark a pipeline stage (papers/forum/sources/curating/done/onboarding)
    as pending|running|done|error, for the UI's progress stepper."""
    if _STAGE_SINK is not None:
        try:
            _STAGE_SINK(name, status, detail)
        except Exception:
            pass


def log_milestone(label: str, msg: str) -> None:
    """Print a milestone line and forward it to the progress sink (if set).
    Used by run_agent and by the orchestrator (digest.py)."""
    line = f"[{label:8}] {msg}"
    print(line, flush=True)
    if _PROGRESS_SINK is not None:
        try:
            _PROGRESS_SINK(line)
        except Exception:
            pass


def _log_assistant(msg, label: str, log_path) -> str:
    """Log each block of an assistant turn; return the latest text block (the
    agent's final JSON usually lands here)."""
    last = ""
    for block in msg.content:
        if isinstance(block, ToolUseBlock):
            log_milestone(label, _fmt_tool_use(block))
            log_file(log_path, label, f"TOOL_USE {block.name}", json.dumps(block.input, indent=2))
        elif isinstance(block, ThinkingBlock):
            head = (block.thinking or "").strip().split("\n", 1)[0]
            if head:
                log_milestone(label, f"thinking: {_truncate(head, 160)}")
            log_file(log_path, label, "THINKING", block.thinking or "")
        elif isinstance(block, TextBlock) and block.text.strip():
            last = block.text
            head = block.text.strip().split("\n", 1)[0]
            if not head.startswith("{"):
                log_milestone(label, f"text: {_truncate(head, 160)}")
            log_file(log_path, label, "TEXT", block.text)
    return last


def _log_tool_results(msg, label: str, log_path) -> None:
    """Tool results arrive as ToolResultBlock(s) on a UserMessage."""
    for block in msg.content if isinstance(msg.content, list) else []:
        if not isinstance(block, ToolResultBlock):
            continue
        content = block.content
        if isinstance(content, list):
            content = json.dumps(content, indent=2)
        elif not isinstance(content, str):
            content = str(content)
        err = " (is_error=True)" if block.is_error else ""
        log_file(log_path, label, f"TOOL_RESULT{err}", content or "")


_SEP = "─" * 72


def log_file(path: Path | None, label: str, kind: str, content: str) -> None:
    """Append one entry to the shared log file. One write() per call = atomic
    across asyncio coroutines (single-threaded; no preemption mid-write)."""
    if path is None:
        return
    ts = time.strftime("%H:%M:%S")
    block = f"\n{_SEP}\n[{ts}] [{label}] {kind}\n{_SEP}\n{content}\n"
    with path.open("a", encoding="utf-8") as f:
        f.write(block)


def _truncate(s: str, n: int) -> str:
    s = s.replace("\n", " ").strip()
    return s if len(s) <= n else s[: n - 1] + "…"


def _fmt_tool_use(block: ToolUseBlock) -> str:
    name = block.name
    inp = block.input or {}
    if name == "WebSearch":
        q = _truncate(str(inp.get("query", "")), 100)
        dom = inp.get("allowed_domains") or []
        dom_s = f" [{','.join(dom[:3])}]" if dom else ""
        return f"WebSearch '{q}'{dom_s}"
    if name == "WebFetch":
        url = _truncate(str(inp.get("url", "")), 80)
        return f"WebFetch {url}"
    keys = list(inp.keys())[:2]
    summary = ", ".join(f"{k}={_truncate(str(inp.get(k, '')), 40)}" for k in keys)
    return f"{name}({summary})"


def parse_json(text: str) -> dict:
    """Pull JSON out of the agent's final text. Tolerates ```json fences."""
    if not text:
        return {}
    s = text.strip()
    m = re.search(r"```(?:json)?\s*(\{.*\})\s*```", s, re.DOTALL)
    if m:
        s = m.group(1)
    if not s.startswith("{"):
        i, j = s.find("{"), s.rfind("}")
        if i >= 0 and j > i:
            s = s[i:j + 1]
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        return {}
