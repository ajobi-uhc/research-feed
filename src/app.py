"""FastAPI routes — kept thin. All real logic lives in agents/store/scoring."""

from __future__ import annotations
import asyncio
import json
from datetime import date, timedelta

from fastapi import FastAPI, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from pathlib import Path

from . import agents, progress, scoring, store
from .config import ROOT, DATA, TODAY, SUBFIELDS
from .models import Author, Profile, Source

SUGGESTIONS_PATH = DATA / "profile_suggestions.json"

templates = Jinja2Templates(directory=str(ROOT / "templates"))
app = FastAPI(title="Safety Feed")
app.mount("/static", StaticFiles(directory=str(ROOT / "static")), name="static")
store.init()


def ctx(request: Request, **extra) -> dict:
    p = store.load_profile()
    name = (p.user_identity.get("name") if p else None)
    base = {
        "request": request, "today": TODAY,
        "active_profile": p.to_dict() if p else None,
        "active_name": name, "subfields": SUBFIELDS,
        "suggestions": _load_suggestions(),
    }
    base.update(extra)
    return base


def _load_suggestions() -> dict | None:
    if not SUGGESTIONS_PATH.exists():
        return None
    try:
        return json.loads(SUGGESTIONS_PATH.read_text())
    except (json.JSONDecodeError, OSError):
        return None


# ── Landing ──────────────────────────────────────────────────────────
@app.get("/", response_class=HTMLResponse)
def root():
    if store.load_profile() is None:
        return RedirectResponse("/profile")
    return RedirectResponse("/month")


# ── Onboarding + Discovery ───────────────────────────────────────────
async def _onboard_then_discover(user_text: str, days: int):
    try:
        progress.log("Step 1/2 — Building context map (~2-3 min)…")
        await agents.build_profile(user_text)
        scoring.rescore_all()
        progress.log("Step 2/2 — Discovery (last 30 days)…")
        await agents.discover(window_days=days, wipe=True)
        progress.done(redirect_to="/month")
    except Exception as e:
        progress.error(str(e))


@app.post("/profile")
async def profile_create(user_profile: str = Form(...)):
    if not user_profile.strip():
        return RedirectResponse("/profile", status_code=303)
    progress.start("onboarding+discovery", redirect_to="/month")
    asyncio.create_task(_onboard_then_discover(user_profile, days=30))
    return RedirectResponse("/running", status_code=303)


@app.post("/profile/reonboard")
async def profile_reonboard(user_profile: str = Form(...)):
    if not user_profile.strip():
        return RedirectResponse("/profile", status_code=303)
    progress.start("re-onboard", redirect_to="/profile")

    async def _run():
        try:
            await agents.build_profile(user_profile)
            scoring.rescore_all()
            progress.done(redirect_to="/profile")
        except Exception as e:
            progress.error(str(e))
    asyncio.create_task(_run())
    return RedirectResponse("/running", status_code=303)


async def _discover_bg(days: int, wipe: bool):
    try:
        await agents.discover(window_days=days, wipe=wipe)
        progress.done(redirect_to="/month")
    except Exception as e:
        progress.error(str(e))


@app.post("/discover")
async def discover(days: int = Form(30), fresh: int = Form(1)):
    progress.start("discovery", redirect_to="/month")
    asyncio.create_task(_discover_bg(days, bool(fresh)))
    return RedirectResponse("/running", status_code=303)


# ── Suggestions (profile growth from curation) ──────────────────────
@app.post("/profile/accept-keyword")
def accept_keyword(value: str = Form(...)):
    profile = store.load_profile()
    if not profile:
        return RedirectResponse("/profile", status_code=303)
    v = value.strip()
    if v and v not in profile.keywords:
        profile.keywords.append(v)
        store.save_profile(profile)
    _remove_suggestion("suggested_keywords", v)
    return RedirectResponse("/profile", status_code=303)


@app.post("/profile/accept-author")
def accept_author(value: str = Form(...)):
    profile = store.load_profile()
    if not profile:
        return RedirectResponse("/profile", status_code=303)
    v = value.strip()
    if v and v.lower() not in [a.name.lower() for a in profile.trusted_authors]:
        profile.trusted_authors.append(Author(name=v, affiliation=None,
                                                why="suggested by curation agent"))
        store.save_profile(profile)
        scoring.rescore_all()  # author trust impacts existing scores
    _remove_suggestion("suggested_authors", v)
    return RedirectResponse("/profile", status_code=303)


@app.post("/profile/dismiss-suggestions")
def dismiss_suggestions():
    if SUGGESTIONS_PATH.exists():
        SUGGESTIONS_PATH.unlink()
    return RedirectResponse("/profile", status_code=303)


def _remove_suggestion(key: str, value: str) -> None:
    s = _load_suggestions()
    if not s:
        return
    s[key] = [v for v in s.get(key, []) if v.strip() != value.strip()]
    SUGGESTIONS_PATH.write_text(json.dumps(s, indent=2))


# ── Profile view + inline edit ───────────────────────────────────────
@app.get("/profile", response_class=HTMLResponse)
def profile_view(request: Request):
    return templates.TemplateResponse(request, "profile.html", ctx(request, view="profile"))


@app.post("/profile/edit")
async def profile_edit(request: Request):
    """Save inline edits + rescore. No agent involved."""
    profile = store.load_profile()
    if not profile:
        return RedirectResponse("/profile", status_code=303)

    form = await request.form()

    # Notes
    if (notes := form.get("notes")) is not None:
        profile.notes = notes.strip()

    # Sources
    if (sj := form.get("sources_json")):
        try:
            profile.sources = [
                Source(slug=s["slug"].strip(), name=s.get("name", s["slug"]).strip(),
                        url=s["url"].strip(),
                        trust=max(0, min(50, int(s.get("trust", 25)))),
                        rationale=s.get("rationale", "").strip())
                for s in json.loads(sj)
                if s.get("slug") and s.get("url")
            ]
        except (json.JSONDecodeError, ValueError, TypeError):
            pass

    # Authors
    if (aj := form.get("authors_json")):
        try:
            profile.trusted_authors = [
                Author(name=a["name"].strip(),
                        affiliation=(a.get("affiliation") or "").strip() or None,
                        why=(a.get("why") or "").strip() or None)
                for a in json.loads(aj)
                if isinstance(a, dict) and a.get("name")
            ]
        except (json.JSONDecodeError, TypeError):
            pass

    # Keywords
    if (kj := form.get("keywords_json")):
        try:
            profile.keywords = [k.strip() for k in json.loads(kj) if isinstance(k, str) and k.strip()]
        except (json.JSONDecodeError, TypeError):
            pass

    # Dislikes
    if (dj := form.get("dislikes_json")):
        try:
            profile.dislikes = [d.strip() for d in json.loads(dj) if isinstance(d, str) and d.strip()]
        except (json.JSONDecodeError, TypeError):
            pass


    # Subfield weights
    for sf in SUBFIELDS:
        v = form.get(f"subfield_{sf}")
        if v is not None:
            try:
                profile.subfield_weights[sf] = max(0, min(20, int(v)))
            except ValueError:
                pass

    store.save_profile(profile)
    scoring.rescore_all()
    return RedirectResponse("/profile", status_code=303)


# ── Feed views ───────────────────────────────────────────────────────
@app.get("/month", response_class=HTMLResponse)
def month_view(request: Request, ending: str | None = None, limit: int = 40):
    end = ending or TODAY
    items = store.items_in_month(end, limit=limit)
    start = (date.fromisoformat(end) - timedelta(days=29)).isoformat()
    return templates.TemplateResponse(request, "month.html", ctx(
        request, view="month", items=items, month_start=start, month_end=end,
    ))


@app.get("/weeks", response_class=HTMLResponse)
def weeks_view(request: Request, ending: str | None = None):
    end = ending or TODAY
    end_d = date.fromisoformat(end)
    weeks = []
    for i in range(4):
        wk_end = (end_d - timedelta(days=7 * i)).isoformat()
        wk_start = (date.fromisoformat(wk_end) - timedelta(days=6)).isoformat()
        weeks.append({
            "start": wk_start, "end": wk_end,
            "cards": store.items_in_week(wk_end, limit=10),
        })
    return templates.TemplateResponse(request, "weeks.html", ctx(request, view="weeks", weeks=weeks))


@app.get("/week", response_class=HTMLResponse)
def week_view(request: Request, ending: str | None = None):
    end = ending or TODAY
    items = store.items_in_week(end, limit=30)
    start = (date.fromisoformat(end) - timedelta(days=6)).isoformat()
    return templates.TemplateResponse(request, "week.html", ctx(
        request, view="week", items=items, week_start=start, week_end=end,
    ))


@app.get("/item/{item_id}", response_class=HTMLResponse)
def item_view(request: Request, item_id: str):
    item = store.get_item(item_id)
    if not item:
        return HTMLResponse("Not found", status_code=404)
    return templates.TemplateResponse(request, "item.html", ctx(request, view="item", item=item))


@app.post("/item/{item_id}/read")
def mark_read(item_id: str):
    store.set_read(item_id, True)
    return RedirectResponse(f"/item/{item_id}", status_code=303)


@app.post("/item/{item_id}/star")
def star(item_id: str):
    starred = store.toggle_starred(item_id)
    if starred:
        item = store.get_item(item_id)
        profile = store.load_profile()
        if item and profile:
            existing = [a.name.lower() for a in profile.trusted_authors]
            added = False
            for a in item.authors:
                if a.lower() not in existing:
                    profile.trusted_authors.append(
                        Author(name=a, affiliation=None, why="auto-added from a starred item")
                    )
                    added = True
            if added:
                store.save_profile(profile)
                scoring.rescore_all()
    return RedirectResponse(f"/item/{item_id}", status_code=303)


@app.post("/item/{item_id}/hide")
def hide(item_id: str, reason: str = Form("")):
    """User feedback: this item isn't relevant. Record optional reason; the
    next curation run will see it and avoid semantically similar items."""
    item = store.get_item(item_id)
    profile = store.load_profile()
    if not item or not profile:
        return RedirectResponse("/month", status_code=303)
    profile.hidden_with_reason.append({
        "item_title": item.title,
        "venue": item.venue,
        "date": item.date,
        "reason": reason.strip(),
    })
    # Keep the list bounded — older entries get dropped
    if len(profile.hidden_with_reason) > 200:
        profile.hidden_with_reason = profile.hidden_with_reason[-200:]
    store.set_read(item_id, True)
    store.save_profile(profile)
    return RedirectResponse("/month", status_code=303)


# ── Progress + APIs ──────────────────────────────────────────────────
@app.get("/running", response_class=HTMLResponse)
def running_view(request: Request):
    return templates.TemplateResponse(request, "running.html", ctx(request, view="running"))


@app.get("/api/progress")
def api_progress():
    return JSONResponse(progress.snapshot())


@app.get("/api/profile")
def api_profile():
    p = store.load_profile()
    return JSONResponse(p.to_dict() if p else {"summary": "no active profile"})


@app.get("/api/items")
def api_items(
    on: str | None = None, week_ending: str | None = None, month_ending: str | None = None,
    qstr: str | None = None, venue: str | None = None, subfield: str | None = None,
    author: str | None = None, date_start: str | None = None, date_end: str | None = None,
    limit: int = 500,
):
    if month_ending:
        items = store.items_in_month(month_ending, limit=limit)
    elif week_ending:
        items = store.items_in_week(week_ending, limit=limit)
    elif on:
        items = store.list_items(date_start=on, date_end=on, limit=limit)
    else:
        items = store.list_items(
            qstr=qstr, venue=venue, subfield=subfield, author=author,
            date_start=date_start, date_end=date_end, limit=limit,
        )
    return JSONResponse({"items": [i.__dict__ for i in items], "count": len(items)})


@app.get("/api/item/{item_id}")
def api_item(item_id: str):
    item = store.get_item(item_id)
    return JSONResponse(item.__dict__ if item else {"error": "not found"}, status_code=200 if item else 404)


@app.get("/api/stats")
def api_stats():
    return JSONResponse(store.stats())
