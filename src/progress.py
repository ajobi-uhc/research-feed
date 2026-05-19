"""Single-user progress tracker + agent message streamer.

Two responsibilities, kept together because they're both about "what's the
agent doing right now":

  - A shared in-memory state dict that the UI polls via /api/progress.
  - An async helper that consumes Claude SDK messages and writes filtered
    summaries to the log (tool calls + reasoning, minus filesystem noise).
"""

from __future__ import annotations
import re
import time
from threading import Lock


# ─── Shared state ────────────────────────────────────────────────────
_state = {
    "status": "idle",        # idle | running | done | error
    "phase": None,
    "message": "",
    "log": [],
    "started_at": None,
    "finished_at": None,
    "task": None,
    "redirect_to": "/",
}
_lock = Lock()


def _ts() -> str:
    return time.strftime("%H:%M:%S")


def start(task: str, redirect_to: str = "/month") -> None:
    with _lock:
        _state.update(
            status="running", phase="init",
            message=f"Starting {task}...",
            log=[f"[{_ts()}] Starting {task}..."],
            started_at=time.time(), finished_at=None,
            task=task, redirect_to=redirect_to,
        )


def log(message: str, phase: str | None = None) -> None:
    line = f"[{_ts()}] {message}"
    with _lock:
        _state["log"].append(line)
        _state["message"] = message
        if phase:
            _state["phase"] = phase
        if len(_state["log"]) > 500:
            _state["log"] = _state["log"][-400:]
    # Mirror to stdout so CLI / eval runs see progress in real time
    print(f"  {line}", flush=True)


def done(redirect_to: str | None = None) -> None:
    with _lock:
        _state["status"] = "done"
        _state["finished_at"] = time.time()
        _state["message"] = "Done"
        _state["log"].append(f"[{_ts()}] Done.")
        if redirect_to:
            _state["redirect_to"] = redirect_to


def error(message: str) -> None:
    with _lock:
        _state["status"] = "error"
        _state["message"] = message
        _state["finished_at"] = time.time()
        _state["log"].append(f"[{_ts()}] ERROR: {message}")


def snapshot() -> dict:
    with _lock:
        return dict(_state)


# ─── Agent message stream ────────────────────────────────────────────
# Filesystem mechanics we don't want to surface to the user.
_NOISE_PATTERNS = [
    re.compile(r"\b(directory|target dir|target path|absolute path) (exists|missing)\b", re.I),
    re.compile(r"\bchecking the (target |path)", re.I),
    re.compile(r"^let me check the", re.I),
    re.compile(r"^writing (the )?(json|file|profile)", re.I),
    re.compile(r"^i'll write", re.I),
]


def _is_noise(text: str) -> bool:
    t = text.strip()
    if len(t) < 6:
        return True
    return any(p.search(t) for p in _NOISE_PATTERNS)


def _summarize_tool(name: str, input_: dict) -> str | None:
    if name == "WebSearch":
        q = (input_.get("query") or "").strip()
        return f"🔍 search: {q[:120]}" if q else None
    if name == "WebFetch":
        u = (input_.get("url") or "").strip()
        return f"📄 fetch: {u[:140]}" if u else None
    if name == "Bash":
        cmd = (input_.get("command") or "").strip()
        if cmd.startswith("curl"):
            return f"⌨  curl: {cmd[:120].replace(chr(10), ' ')}"
        return None
    if name == "Write":
        p = (input_.get("file_path") or "").strip()
        return f"💾 write: {p[-80:]}" if p else None
    return None


async def stream(agent_iter, label: str = "agent") -> int:
    """Consume agent messages, log filtered output. Returns turn count."""
    from claude_agent_sdk import AssistantMessage, TextBlock
    turn = 0
    async for msg in agent_iter:
        if not isinstance(msg, AssistantMessage):
            continue
        turn += 1
        for block in msg.content:
            if isinstance(block, TextBlock):
                t = block.text.strip()
                if t and not _is_noise(t):
                    snippet = t[:200]
                    print(f"  [{label} t{turn}] {snippet}")
                    log(f"{label} t{turn}: {snippet}")
            elif hasattr(block, "name") and hasattr(block, "input"):
                summary = _summarize_tool(block.name, block.input or {})
                if summary:
                    print(f"  [{label}] {summary}")
                    log(f"{label}: {summary}")
    return turn
