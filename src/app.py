"""FastAPI routes — thin wrappers around pipeline + store."""

from __future__ import annotations
import asyncio
import json
from datetime import date, timedelta
from pathlib import Path

from fastapi import FastAPI, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import store, pipeline, progress
from .config import ROOT, TODAY, DISPLAY_WINDOW_DAYS, BUCKETS, PULL_SOURCES
from .models import Profile, Author

templates = Jinja2Templates(directory=str(ROOT / "templates"))
app = FastAPI(title="Safety Feed")
app.mount("/static", StaticFiles(directory=str(ROOT / "static")), name="static")
store.init()


def ctx(request: Request, **extra) -> dict:
    p = store.load_profile()
    base = {
        "request": request, "today": TODAY,
        "active_profile": p.to_dict() if p else None,
        "active_name": (p.user_summary[:60] + "…") if p else None,
        "buckets": BUCKETS,
        "sources": PULL_SOURCES,
    }
    base.update(extra)
    return base


# ─── Landing ─────────────────────────────────────────────────────────
@app.get("/", response_class=HTMLResponse)
def root():
    if store.load_profile() is None:
        return RedirectResponse("/profile")
    return RedirectResponse("/weeks")


# ─── Onboarding + profile ────────────────────────────────────────────
@app.get("/profile", response_class=HTMLResponse)
def profile_view(request: Request):
    return templates.TemplateResponse(request, "profile.html", ctx(request, view="profile"))


async def _onboard_then_discover(
    seed_papers, scholar_url, followed_authors, current_question, filter_outs
):
    try:
        progress.log("Step 1/2 — building your profile from seed papers…")
        await pipeline.onboard(
            seed_papers=seed_papers,
            scholar_url=scholar_url,
            followed_authors=followed_authors,
            current_question=current_question,
            filter_outs=filter_outs,
        )
        progress.log("Step 2/2 — discovery: fetching + ranking…")
        await pipeline.discover()
        progress.done(redirect_to="/feed")
    except Exception as e:
        import traceback; traceback.print_exc()
        progress.error(str(e))


def _split_lines(s: str | None) -> list[str]:
    if not s:
        return []
    return [line.strip() for line in s.splitlines() if line.strip()]


def _split_csv(s: str | None) -> list[str]:
    if not s:
        return []
    return [x.strip() for x in s.split(",") if x.strip()]


@app.post("/profile")
async def profile_create(
    seed_papers: str = Form(""),
    scholar_url: str = Form(""),
    followed_authors: str = Form(""),
    current_question: str = Form(""),
    filter_outs: str = Form(""),
):
    seeds = _split_lines(seed_papers)
    if not seeds and not followed_authors.strip() and not current_question.strip():
        return RedirectResponse("/profile", status_code=303)
    progress.start("onboard+discover", redirect_to="/feed")
    asyncio.create_task(_onboard_then_discover(
        seed_papers=seeds,
        scholar_url=scholar_url.strip() or None,
        followed_authors=_split_lines(followed_authors),
        current_question=current_question.strip(),
        filter_outs=_split_csv(filter_outs),
    ))
    return RedirectResponse("/running", status_code=303)


@app.post("/profile/edit")
async def profile_edit(request: Request):
    """Save inline edits, then trigger rerank (no re-fetch)."""
    form = await request.form()
    profile = store.load_profile()
    if not profile:
        return RedirectResponse("/profile", status_code=303)

    if (s := form.get("user_summary")) is not None:
        profile.user_summary = s.strip()
    if (s := form.get("current_question")) is not None:
        profile.current_question = s.strip()
    if (s := form.get("notes")) is not None:
        profile.notes = s.strip()

    if (s := form.get("tags")) is not None:
        profile.tags = _split_csv(s)
    if (s := form.get("research_areas")) is not None:
        profile.research_areas = _split_csv(s)
    if (s := form.get("filter_outs")) is not None:
        profile.filter_outs = _split_csv(s)
    if (s := form.get("followed_authors")) is not None:
        names = _split_lines(s)
        # Preserve affiliations if a row already exists
        prev = {a.name.lower(): a for a in profile.followed_authors}
        profile.followed_authors = [
            prev.get(n.lower(), Author(name=n)) for n in names
        ]

    store.save_profile(profile)

    # If profile version actually changed, kick off a rerank
    new_hash = profile.version_hash  # save_profile recomputed it
    progress.start("rerank", redirect_to="/feed")

    async def _bg():
        try:
            await pipeline.rerank()
            progress.done(redirect_to="/feed")
        except Exception as e:
            progress.error(str(e))

    asyncio.create_task(_bg())
    return RedirectResponse("/running", status_code=303)


@app.post("/discover")
async def discover_only():
    progress.start("discover", redirect_to="/feed")

    async def _bg():
        try:
            await pipeline.discover()
            progress.done(redirect_to="/feed")
        except Exception as e:
            progress.error(str(e))

    asyncio.create_task(_bg())
    return RedirectResponse("/running", status_code=303)


# ─── Feed views ──────────────────────────────────────────────────────
@app.get("/weeks", response_class=HTMLResponse)
def weeks_view(request: Request, ending: str | None = None):
    """4 weeks stacked. Each week → click into /week?ending=…"""
    profile = store.load_profile()
    if profile is None:
        return RedirectResponse("/profile")
    end = date.fromisoformat(ending or TODAY)
    def _label(i: int) -> str:
        return {0: "this week", 1: "last week"}.get(i, f"{i} weeks ago")

    def _fmt_range(s_iso: str, e_iso: str) -> str:
        s = date.fromisoformat(s_iso); e = date.fromisoformat(e_iso)
        if s.year == e.year and s.month == e.month:
            return f"{s.strftime('%b')} {s.day}–{e.day}"
        return f"{s.strftime('%b %d')} – {e.strftime('%b %d')}"

    weeks = []
    for i in range(4):
        wk_end = (end - timedelta(days=7 * i)).isoformat()
        wk_start = (date.fromisoformat(wk_end) - timedelta(days=6)).isoformat()
        full_rows = store.list_items_ranked_simple(
            profile.version_hash, wk_start, wk_end, limit=25,
        )
        weeks.append({
            "label": _label(i),
            "range": _fmt_range(wk_start, wk_end),
            "start": wk_start, "end": wk_end,
            "rows": full_rows[:5],
            "total": len(full_rows),
        })
    return templates.TemplateResponse(request, "weeks.html", ctx(
        request, view="weeks", weeks=weeks,
    ))


@app.get("/week", response_class=HTMLResponse)
def week_view(request: Request, ending: str | None = None):
    """Single week, full ranked list."""
    profile = store.load_profile()
    if profile is None:
        return RedirectResponse("/profile")
    end = date.fromisoformat(ending or TODAY)
    start = (end - timedelta(days=6)).isoformat()
    rows = store.list_items_ranked(
        profile.version_hash,
        date_start=start, date_end=end.isoformat(),
        limit=100, keep_buckets=("core", "adjacent"),
    )
    transparency = store.transparency_for(profile.version_hash, start, end.isoformat())
    return templates.TemplateResponse(request, "week.html", ctx(
        request, view="week", rows=rows,
        window_start=start, window_end=end.isoformat(),
        transparency=transparency,
    ))


# Backwards-compat alias: /feed → /weeks
@app.get("/feed", response_class=HTMLResponse)
def feed_redirect():
    return RedirectResponse("/weeks", status_code=303)


@app.get("/item/{item_id}", response_class=HTMLResponse)
def item_view(request: Request, item_id: str):
    item = store.get_item(item_id)
    if not item:
        return HTMLResponse("Not found", status_code=404)
    profile = store.load_profile()
    # Get ranking for current profile version, if any
    ranking = None
    if profile:
        with store.conn() as c:
            r = c.execute(
                "SELECT * FROM rankings WHERE item_id = ? AND profile_version = ?",
                (item_id, profile.version_hash),
            ).fetchone()
            if r:
                from .models import Ranking
                ranking = Ranking(
                    item_id=r["item_id"], profile_version=r["profile_version"],
                    bucket=r["bucket"],
                    reasons=json.loads(r["reasons_json"]) if r["reasons_json"] else [],
                    why=r["why"] or "",
                    novelty=r["novelty"],
                    ranked_at=r["ranked_at"] or "",
                )
    return templates.TemplateResponse(request, "item.html", ctx(
        request, view="item", item=item, ranking=ranking,
    ))


# Feedback intentionally not implemented yet. The buttons / log mechanism
# is removed — we'll revisit how to capture user signal later.


# ─── Progress page ──────────────────────────────────────────────────
@app.get("/running", response_class=HTMLResponse)
def running_view(request: Request):
    return templates.TemplateResponse(request, "running.html", ctx(request, view="running"))


@app.get("/api/progress")
def api_progress():
    return JSONResponse(progress.snapshot())


@app.get("/api/profile")
def api_profile():
    p = store.load_profile()
    return JSONResponse(p.to_dict() if p else {"error": "no profile"})


@app.get("/api/stats")
def api_stats():
    return JSONResponse(store.stats())
