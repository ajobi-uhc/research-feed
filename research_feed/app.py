"""FastAPI JSON API. Thin layer over agents + store; the UI is the React app in web/.

All data routes live under /api. Live run progress streams over SSE. In a built
deployment the compiled SPA in web/dist is served for everything else; in dev the
Vite server proxies /api here.
"""
from __future__ import annotations
import asyncio
import json
from datetime import datetime

from fastapi import FastAPI, Response
from fastapi.responses import StreamingResponse, FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .agents import onboarding
from .agents.runner import set_progress_sink, set_stage_sink, log_stage
from .config import ROOT, DATA
from .models import Author, Profile, Source, short_id
from . import store, briefing


app = FastAPI(title="Research Feed API")
store.init()

RUNS_DIR = DATA / "runs"
RUNS_DIR.mkdir(exist_ok=True)


# Single in-flight run. Single-user app, so a module-global is fine.
RUN_STATE: dict = {"kind": None, "status": "idle", "result": None, "error": None,
                   "run_id": None, "progress": [], "queue": None, "stages": {}}


def _running() -> bool:
    return RUN_STATE["status"] == "running"


def _new_run_id(kind: str) -> str:
    return short_id("r", kind, datetime.utcnow().isoformat())


def _wake() -> None:
    q = RUN_STATE.get("queue")
    if q is not None:
        try:
            q.put_nowait(1)
        except Exception:
            pass


def _on_progress(line: str) -> None:
    RUN_STATE["progress"].append(line)
    _wake()


def _on_stage(name: str, status: str, detail: str = "") -> None:
    RUN_STATE["stages"][name] = {"status": status, "detail": detail}
    _wake()


def _begin_run(kind: str, window_start: str = "", window_end: str = ""):
    run_id = _new_run_id(kind)
    log_path = RUNS_DIR / f"{run_id}.log"
    log_path.write_text(f"RUN {run_id} — {kind} — started {datetime.utcnow().isoformat()}\n")
    RUN_STATE.update({"kind": kind, "status": "running", "result": None, "error": None,
                      "run_id": run_id, "progress": [], "queue": asyncio.Queue(), "stages": {}})
    set_progress_sink(_on_progress)
    set_stage_sink(_on_stage)
    store.start_run(run_id, kind, window_start, window_end)
    return run_id, log_path


def _end_run(run_id: str, log_path, *, status: str, meta: dict | None = None,
             digest_id: str = "", error: str = "") -> None:
    set_progress_sink(None)
    set_stage_sink(None)
    try:
        log_text = log_path.read_text()
    except Exception:
        log_text = ""
    store.finish_run(run_id, status=status, log=log_text, meta=meta,
                     digest_id=digest_id, error=error)
    q = RUN_STATE.get("queue")
    if q is not None:
        try:
            q.put_nowait(None)
        except Exception:
            pass


# ── Profile ──────────────────────────────────────────────────────────
class AuthorIn(BaseModel):
    name: str
    affiliation: str = ""
    why: str = ""


class SourceIn(BaseModel):
    name: str = ""
    url: str
    why: str = ""


class ProfileIn(BaseModel):
    user_summary: str = ""
    current_question: str = ""
    interests: list[str] = []
    authors: list[AuthorIn] = []
    sources: list[SourceIn] = []
    filter_outs: list[str] = []
    notes: str = ""


@app.get("/api/profile")
async def get_profile():
    p = store.load_profile()
    if not p:
        return None
    return {**p.to_dict(), "origin": store.profile_origin()}


@app.put("/api/profile")
async def put_profile(body: ProfileIn):
    existing = store.load_profile()
    p = Profile(
        user_summary=body.user_summary.strip(),
        current_question=body.current_question.strip(),
        interests=[s.strip() for s in body.interests if s.strip()],
        authors=[Author(name=a.name.strip(), affiliation=a.affiliation.strip(), why=a.why.strip())
                 for a in body.authors if a.name.strip()],
        sources=[Source(name=s.name.strip(), url=s.url.strip(), why=s.why.strip())
                 for s in body.sources if s.url.strip()],
        filter_outs=[s.strip() for s in body.filter_outs if s.strip()],
        seed_papers=existing.seed_papers if existing else [],
        notes=body.notes.strip(),
    )
    store.save_profile(p)   # origin defaults to 'user'
    return {**store.load_profile().to_dict(), "origin": store.profile_origin()}


# ── Sample researchers (load eval-generated profiles + feeds to explore) ──
_SAMPLES_DIR = ROOT / "evals" / "runs" / "full"
_SAMPLE_LABELS = {
    "mara": "SAEs & mech-interp",
    "david": "governance & evals",
    "priya": "adversarial robustness",
    "arya": "character / persona training",
}


@app.get("/api/samples")
async def list_samples():
    out = []
    for persona, label in _SAMPLE_LABELS.items():
        d = _SAMPLES_DIR / persona
        if (d / "onboarded_profile.json").exists():
            out.append({"persona": persona, "label": label,
                        "has_feed": bool(list(d.glob("digest_*.json")))})
    return out


@app.post("/api/samples/{persona}")
async def load_sample(persona: str):
    if _running():
        return JSONResponse({"error": "a run is in progress"}, status_code=409)
    d = _SAMPLES_DIR / persona
    pf = d / "onboarded_profile.json"
    if not pf.exists():
        return JSONResponse({"error": "unknown sample"}, status_code=404)
    store.reset_feed()
    store.save_profile(Profile.from_dict(json.loads(pf.read_text())), origin="sample")
    for dj in sorted(d.glob("digest_*.json")):
        store.save_digest(json.loads(dj.read_text()))
    return {"ok": True}


# ── Proposals (registrar's suggested profile/registry updates) ────────
@app.get("/api/proposals")
async def get_proposals():
    return store.list_proposals("pending")


@app.post("/api/proposals/{proposal_id}/accept")
async def proposal_accept(proposal_id: str):
    store.accept_proposal(proposal_id)
    return Response(status_code=204)


@app.post("/api/proposals/{proposal_id}/reject")
async def proposal_reject(proposal_id: str):
    store.reject_proposal(proposal_id)
    return Response(status_code=204)


class ProposeIn(BaseModel):
    kind: str  # 'source' | 'author' | 'profile'
    payload: dict
    rationale: str = ""


@app.post("/api/propose")
async def propose(body: ProposeIn):
    """Per-item feedback becomes a proposed profile edit (no hidden state)."""
    if body.kind not in ("source", "author", "profile"):
        return JSONResponse({"error": "bad kind"}, status_code=400)
    store.add_proposal(body.kind, body.payload, body.rationale)
    return Response(status_code=204)


class NoteIn(BaseModel):
    text: str


@app.post("/api/note")
async def add_note(body: NoteIn):
    """Append a line to the profile notes — used by 'less like this' feedback so
    the reason becomes durable, visible guidance the curator reads."""
    p = store.load_profile()
    if not p:
        return JSONResponse({"error": "no profile"}, status_code=400)
    line = body.text.strip()
    if line:
        p.notes = f"{p.notes}\n{line}".strip() if p.notes else line
        store.save_profile(p)
    return Response(status_code=204)


# ── Runs (start onboarding / discovery; live state + stream) ──────────
class OnboardingIn(BaseModel):
    seed_papers: list[str] = []
    scholar_url: str = ""
    followed_authors: list[str] = []
    current_question: str = ""
    filter_outs: list[str] = []
    freeform: str = ""


class BriefingIn(BaseModel):
    range: str = "since_last"
    window_start: str = ""
    window_end: str = ""


@app.post("/api/onboarding")
async def start_onboarding(body: OnboardingIn):
    if _running():
        return JSONResponse({"error": "a run is already in progress"}, status_code=409)
    run_id, log_path = _begin_run("onboarding")
    asyncio.create_task(_run_onboarding(
        run_id, log_path,
        seed_papers=[s.strip() for s in body.seed_papers if s.strip()],
        scholar_url=body.scholar_url.strip(),
        followed_authors=[s.strip() for s in body.followed_authors if s.strip()],
        current_question=body.current_question.strip(),
        filter_outs=[s.strip() for s in body.filter_outs if s.strip()],
        freeform=body.freeform.strip(),
    ))
    return {"run_id": run_id}


async def _run_onboarding(run_id, log_path, **kwargs):
    """Onboard, then chain straight into a first discovery run — one continuous
    run so the user lands on a briefing (not an empty profile). The discovery
    pass also enriches the source/author set (surfaced as proposals)."""
    log_stage("onboarding", "running")
    try:
        profile, om = await onboarding.create_profile(log_path=log_path, **kwargs)
        store.save_profile(profile, origin="onboarding")
        log_stage("onboarding", "done")

        # Chain discovery for the last month using the freshly-built profile.
        profile = store.load_profile()  # capped/reconciled view
        ws, we = briefing.window_for_range("month")
        digest, dm = await briefing.generate_briefing(profile, ws, we, log_path=log_path)

        RUN_STATE.update({"status": "done", "result": "/"})
        _end_run(run_id, log_path, status="done",
                 meta={"onboarding": om, "discovery": dm}, digest_id=digest.id)
    except Exception as e:
        log_stage("onboarding", "error")
        RUN_STATE.update({"status": "error", "error": str(e)})
        _end_run(run_id, log_path, status="error", error=str(e))


@app.post("/api/briefing")
async def start_briefing(body: BriefingIn):
    if _running():
        return JSONResponse({"error": "a run is already in progress"}, status_code=409)
    p = store.load_profile()
    if not p:
        return JSONResponse({"error": "no profile yet"}, status_code=400)
    if body.window_start and body.window_end:
        ws, we = body.window_start, body.window_end
    else:
        ws, we = briefing.window_for_range(body.range)
    run_id, log_path = _begin_run("discovery", ws, we)
    asyncio.create_task(_run_briefing(run_id, log_path, p, ws, we))
    return {"run_id": run_id, "window_start": ws, "window_end": we}


async def _run_briefing(run_id, log_path, profile, ws, we):
    try:
        digest, meta = await briefing.generate_briefing(profile, ws, we, log_path=log_path)
        RUN_STATE.update({"status": "done", "result": f"/briefings/{digest.id}"})
        _end_run(run_id, log_path, status="done", meta=meta, digest_id=digest.id)
    except Exception as e:
        RUN_STATE.update({"status": "error", "error": str(e)})
        _end_run(run_id, log_path, status="error", error=str(e))


@app.get("/api/run")
async def get_run_state():
    return {k: RUN_STATE[k] for k in ("kind", "status", "result", "error", "run_id", "progress", "stages")}


@app.get("/api/run/stream")
async def run_stream():
    """SSE: each agent milestone line, then a terminal done/failed event."""
    async def gen():
        i = 0
        last_stages = None
        while True:
            lines = RUN_STATE.get("progress", [])
            while i < len(lines):
                safe = lines[i].replace("\r", " ").replace("\n", " ")
                yield f"data: {safe}\n\n"
                i += 1
            cur = json.dumps(RUN_STATE.get("stages", {}))
            if cur != last_stages:
                yield f"event: stage\ndata: {cur}\n\n"
                last_stages = cur
            if RUN_STATE.get("status") != "running":
                if RUN_STATE.get("error"):
                    yield f"event: failed\ndata: {RUN_STATE['error']}\n\n"
                else:
                    yield f"event: done\ndata: {RUN_STATE.get('result') or '/'}\n\n"
                return
            q = RUN_STATE.get("queue")
            try:
                if q is not None:
                    await asyncio.wait_for(q.get(), timeout=15)
                else:
                    await asyncio.sleep(1)
            except asyncio.TimeoutError:
                yield ": heartbeat\n\n"
    return StreamingResponse(gen(), media_type="text/event-stream")


# ── Briefings (the feed) + saved runs (traces) ────────────────────────
@app.get("/api/briefings")
async def list_briefings():
    return store.list_digests()


@app.get("/api/briefings/{digest_id}")
async def get_briefing(digest_id: str):
    d = store.get_digest(digest_id)
    if not d:
        return JSONResponse({"error": "not found"}, status_code=404)
    return d


@app.get("/api/runs")
async def list_runs():
    return store.list_runs()


@app.get("/api/runs/{run_id}")
async def get_run(run_id: str):
    r = store.get_run(run_id)
    if not r:
        return JSONResponse({"error": "not found"}, status_code=404)
    return r


# ── Serve the built SPA (dev uses Vite; this only kicks in after a build) ──
_DIST = ROOT / "web" / "dist"
if _DIST.exists():
    app.mount("/assets", StaticFiles(directory=str(_DIST / "assets")), name="assets")

    @app.get("/{full_path:path}")
    async def spa(full_path: str):
        if full_path.startswith("api"):
            return JSONResponse({"error": "not found"}, status_code=404)
        return FileResponse(_DIST / "index.html")
