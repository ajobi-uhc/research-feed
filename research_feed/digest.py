"""Main orchestrator. THIS is the entry point for generating a digest.

Flow:
  1. Load the profile, recent feedback, and titles already shown.
  2. Run three discovery lanes in parallel:
       papers (OpenAlex)  ·  forum (LW/AF GraphQL)  ·  sources (lab/org WebFetch agent)
  3. Dedupe the candidate union across lanes.
  4. Run the curator (Opus) — one call over everything — to produce the digest:
       lead synthesis · themes · kept items · rejection ledger · gaps · coverage
  5. Persist the digest.

Web app calls `generate_digest(profile, ws, we)`. Everything else is plumbing.
"""
from __future__ import annotations
import asyncio
from datetime import datetime, date

from .agents.arxiv import run_arxiv_agent
from .agents.curator import run_curator_agent
from .agents.forum import run_forum_agent
from .agents.registrar import run_registrar_agent
from .agents.runner import log_milestone, log_stage
from .agents.sources import run_sources_agent
from .models import Digest, Item, Profile, short_id
from . import store


def _bm(msg: str) -> None:
    log_milestone("digest", msg)


async def _staged(name: str, coro):
    """Run a lane, marking its stage running -> done/error for the UI stepper."""
    log_stage(name, "running")
    try:
        r = await coro
        log_stage(name, "done")
        return r
    except Exception:
        log_stage(name, "error")
        raise


async def generate_digest(
    profile: Profile, window_start: str, window_end: str,
    *,
    persist: bool = True,
    already_seen: list[str] | None = None,
    log_path=None,
    run_registrar: bool | None = None,
) -> tuple[Digest, dict]:
    """Run discovery + curation end-to-end. Returns (digest, meta).

    persist=False skips writing to feed.db (used by evals).
    already_seen override skips the store lookup (used by evals to keep runs
    clean and reproducible across profiles).
    run_registrar defaults to `persist` — the post-run registrar proposes
    registry/profile updates only on real runs, not evals.
    """
    if run_registrar is None:
        run_registrar = persist
    profile_d = profile.to_dict()
    window = {"start": window_start, "end": window_end}
    if already_seen is None:
        already_seen = _already_seen_titles()

    _bm(f"window {window_start} → {window_end}, "
        f"{len(profile_d.get('sources', []))} sources, "
        f"{len(profile_d.get('authors', []))} followed authors, already_seen={len(already_seen)}")
    _bm("launching 3 subagents in parallel (followed authors woven in as a boost)...")

    # 1. Three discovery lanes in parallel. Followed authors aren't a search lane —
    #    their papers are caught by arxiv's au: clauses, their posts by sources/forum,
    #    and matches get boosted below before curation.
    results = await asyncio.gather(
        _staged("sources", run_sources_agent(profile=profile_d, window=window,
                          already_seen_titles=already_seen, log_path=log_path)),
        _staged("forum", run_forum_agent(profile=profile_d, window=window,
                        already_seen_titles=already_seen, log_path=log_path)),
        _staged("papers", run_arxiv_agent(profile=profile_d, window=window,
                        already_seen_titles=already_seen, log_path=log_path)),
        return_exceptions=True,
    )

    names = ["sources", "forum", "arxiv"]
    candidates: list[dict] = []
    subagent_drops: list[dict] = []
    subagent_reports: dict[str, dict] = {}   # the trace: interpretation + searches + coverage
    discovered_sources: list[dict] = []      # new lab/org sources the sources agent roamed to
    meta_per: dict[str, dict] = {}

    for name, result in zip(names, results):
        if isinstance(result, Exception):
            subagent_reports[name] = {"error": f"subagent crashed: {result!r}"}
            meta_per[name] = {"error": repr(result)}
            continue
        out, m = result
        meta_per[name] = m
        for it in out.get("kept", []):
            it.setdefault("id", Item.make_id(it.get("url", it.get("title", ""))))
            candidates.append(it)
        for d in out.get("considered_but_excluded", []):
            subagent_drops.append({**d, "dropped_by_subagent": name})
        discovered_sources.extend(out.get("discovered_sources", []))
        # The trace each subagent leaves — fed to the curator AND surfaced for debugging.
        subagent_reports[name] = {
            "profile_interpretation": out.get("profile_interpretation", ""),
            "searches_performed": out.get("searches_performed", []),
            "sources_checked": out.get("sources_checked", []),
            "excluded_aggregate": out.get("excluded_aggregate", ""),
            "coverage_notes": out.get("coverage_notes", ""),
            "n_kept": len(out.get("kept", [])),
            "n_close_call_excludes": len(out.get("considered_but_excluded", [])),
        }

    # 2. Dedupe, then weave in the followed-author boost: tag any candidate whose
    #    authors match the followed list, so the curator treats it as high-signal.
    deduped = _dedupe_candidates(candidates)
    n_boosted = _tag_followed_authors(deduped, profile_d.get("authors", []))
    _bm(f"subagents returned {len(candidates)} candidates "
        f"({len(deduped)} after dedup, {len(subagent_drops)} close-call excludes, "
        f"{n_boosted} by followed authors)")
    _bm("running curator (Opus) over deduped candidates...")
    log_stage("curating", "running")

    # 3. Curator — Opus, one call over everything
    curator_out, curator_meta = await run_curator_agent(
        profile=profile_d,
        window=window,
        candidates=deduped,
        subagent_drops=subagent_drops,
        subagent_reports=subagent_reports,
        log_path=log_path,
    )

    # 4. Build digest; persist if requested
    digest = _build_digest(profile_d, window_start, window_end, curator_out)
    log_stage("curating", "done")
    _bm(f"digest built: {len(digest.kept)} kept (ranked), {len(digest.dropped)} in rejection ledger")
    if persist:
        store.save_digest(digest)

    # 5. Registrar — propose (never auto-apply) registry/profile updates.
    registrar_out: dict = {}
    if run_registrar:
        _bm("running registrar (proposes source/author/profile updates)...")
        registrar_out, _rmeta = await run_registrar_agent(
            kept=[k.__dict__ if hasattr(k, "__dict__") else k for k in digest.kept],
            current_sources=[s.get("url", "") for s in profile_d.get("sources", [])],
            current_authors=[a.get("name", "") for a in profile_d.get("authors", [])],
            discovered_sources=discovered_sources,
            subagent_reports=subagent_reports,
            profile=profile_d,
            log_path=log_path,
        )
        n = _store_proposals(registrar_out, digest.id) if persist else _count_proposals(registrar_out)
        _bm(f"registrar proposed {n} change(s)")

    return digest, {
        "subagents": meta_per,
        "subagent_reports": subagent_reports,   # the trace, for debugging "why didn't X show up"
        "curator": curator_meta,
        "registrar": registrar_out,
        "discovered_sources": discovered_sources,
        "n_candidates": len(deduped),
        "n_kept": len(digest.kept),
    }


def _count_proposals(reg: dict) -> int:
    return (len(reg.get("source_proposals", [])) + len(reg.get("author_proposals", []))
            + len(reg.get("profile_proposals", [])))


def _store_proposals(reg: dict, digest_id: str) -> int:
    n = 0
    for p in reg.get("source_proposals", []):
        if p.get("url"):
            store.add_proposal("source", {"name": p.get("name", ""), "url": p["url"],
                                          "why": p.get("why", "")}, p.get("rationale", ""), digest_id)
            n += 1
    for p in reg.get("author_proposals", []):
        if p.get("name"):
            store.add_proposal("author", {"name": p["name"], "affiliation": p.get("affiliation", ""),
                                          "why": p.get("why", "")}, p.get("rationale", ""), digest_id)
            n += 1
    for p in reg.get("profile_proposals", []):
        if p.get("field"):
            store.add_proposal("profile", {"field": p["field"], "proposed": p.get("proposed", "")},
                               p.get("rationale", ""), digest_id)
            n += 1
    return n


# ── helpers ────────────────────────────────────────────────────────────
def _tag_followed_authors(candidates: list[dict], followed: list[dict]) -> int:
    """Tag candidates authored by a followed researcher with `by_followed_author`
    (last-name match). The curator treats these as high-signal — this is the
    'authors as a boost, not a search lane' mechanism."""
    last_names = {}
    for a in followed:
        name = (a.get("name") or "").strip()
        parts = name.split()
        if parts and len(parts[-1]) > 2:
            last_names[parts[-1].lower()] = name
    if not last_names:
        return 0
    n = 0
    for c in candidates:
        for author in c.get("authors", []):
            toks = str(author).split()
            if toks and toks[-1].lower() in last_names:
                c["by_followed_author"] = last_names[toks[-1].lower()]
                n += 1
                break
    return n


def _dedupe_candidates(candidates: list[dict]) -> list[dict]:
    by_key: dict[str, dict] = {}
    for c in candidates:
        key = c.get("url") or c.get("title", "")
        if key in by_key:
            existing = by_key[key]
            # Prefer longer summary; merge discovered_via traces.
            if len(c.get("summary", "")) > len(existing.get("summary", "")):
                existing["summary"] = c["summary"]
            existing["discovered_via"] = (
                f"{existing.get('discovered_via', '')} + {c.get('discovered_via', '')}"
            )
        else:
            by_key[key] = c
    return list(by_key.values())


def _build_digest(profile_d: dict, ws: str, we: str, curator_out: dict) -> Digest:
    kept_items = []
    for k in curator_out.get("kept", []):
        kept_items.append(Item(
            id=k.get("id") or Item.make_id(k.get("url", k.get("title", ""))),
            title=k.get("title", ""),
            url=k.get("url", ""),
            venue=k.get("venue", ""),
            date=k.get("date", ""),
            authors=list(k.get("authors", [])),
            summary=k.get("summary", ""),
            discovered_via=k.get("discovered_via", ""),
            why_kept=k.get("why", ""),
            venue_detail=k.get("venue_detail", ""),
            karma=k.get("karma"),
            comments=k.get("comments"),
            citations=k.get("citations"),
            arxiv_id=k.get("arxiv_id"),
        ))
    did = short_id("d", ws, we, datetime.utcnow().isoformat())
    return Digest(
        id=did,
        generated_at=datetime.utcnow().isoformat(timespec="seconds") + "Z",
        window_start=ws, window_end=we,
        profile_snapshot=profile_d,
        kept=kept_items,
        dropped=list(curator_out.get("dropped", [])),
        coverage=curator_out.get("coverage", {}),
    )


def _already_seen_titles() -> list[str]:
    """Titles surfaced in past digests — subagents use this to suppress dupes."""
    seen: set[str] = set()
    for row in store.list_digests():
        d = store.get_digest(row["id"])
        if not d:
            continue
        for k in d.get("kept", []):
            if k.get("title"):
                seen.add(k["title"])
    return sorted(seen)


def window_since_last(default_days: int = 30) -> tuple[str, str]:
    """Start = day after the most recent digest's window_end, so a feed never
    has gaps regardless of how often you run it (and `already_seen` prevents
    repeats). First-ever run = last `default_days`. End = today."""
    end = date.today()
    rows = store.list_digests()   # most recent first
    if rows:
        try:
            last_end = date.fromisoformat(rows[0]["window_end"])
            start = date.fromordinal(last_end.toordinal() + 1)
        except (ValueError, KeyError, TypeError):
            start = date.fromordinal(end.toordinal() - default_days + 1)
        if start > end:           # last digest already runs through today
            start = date.fromordinal(end.toordinal() - 6)
    else:
        start = date.fromordinal(end.toordinal() - default_days + 1)
    return start.isoformat(), end.isoformat()


_RANGE_DAYS = {"week": 7, "2weeks": 14, "month": 30}


def window_for_range(range_key: str) -> tuple[str, str]:
    """Map a UI range choice to (start, end). 'since_last' is the default."""
    if range_key == "since_last":
        return window_since_last()
    end = date.today()
    days = _RANGE_DAYS.get(range_key, 7)
    return date.fromordinal(end.toordinal() - days + 1).isoformat(), end.isoformat()
