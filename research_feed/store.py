"""SQLite store. Profile + sources + authors + proposals + digests + runs.

Everything lives in feed.db. The Profile dataclass is assembled from the
profile row + active sources + active authors, so agents see the same shape
as before — the DB is just the backend, and it lets us add/deactivate a single
source or author without rewriting the whole profile.
"""
import json
import sqlite3
from datetime import datetime

from .config import DB_PATH
from .models import Profile, Source, Author, Digest, short_id


SCHEMA = """
CREATE TABLE IF NOT EXISTS profile (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    user_summary TEXT,
    current_question TEXT,
    interests_json TEXT,
    filter_outs_json TEXT,
    seed_papers_json TEXT,
    notes TEXT,
    origin TEXT,                 -- how this profile got here: 'onboarding' | 'user' | 'sample'
    updated_at TEXT
);

CREATE TABLE IF NOT EXISTS sources (
    id TEXT PRIMARY KEY,
    name TEXT,
    url TEXT UNIQUE,
    why TEXT,
    origin TEXT,                 -- 'onboarding' | 'user' | 'registrar'
    active INTEGER DEFAULT 1,
    created_at TEXT,
    last_hit_at TEXT             -- last time it produced a kept item
);

CREATE TABLE IF NOT EXISTS authors (
    id TEXT PRIMARY KEY,
    name TEXT,
    affiliation TEXT,
    why TEXT,
    origin TEXT,
    active INTEGER DEFAULT 1,
    created_at TEXT
);

CREATE TABLE IF NOT EXISTS proposals (
    id TEXT PRIMARY KEY,
    kind TEXT,                   -- 'source' | 'author' | 'profile'
    payload_json TEXT,           -- the proposed change
    rationale TEXT,              -- why the registrar proposed it
    status TEXT DEFAULT 'pending',  -- 'pending' | 'accepted' | 'rejected'
    created_at TEXT,
    digest_id TEXT
);

CREATE TABLE IF NOT EXISTS digests (
    id TEXT PRIMARY KEY,
    generated_at TEXT NOT NULL,
    window_start TEXT NOT NULL,
    window_end TEXT NOT NULL,
    payload TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY,
    kind TEXT,                   -- 'onboarding' | 'discovery'
    status TEXT,                 -- 'running' | 'done' | 'error'
    started_at TEXT,
    finished_at TEXT,
    window_start TEXT,
    window_end TEXT,
    digest_id TEXT,              -- discovery only
    log TEXT,                    -- full-fidelity agent log (prompts, tool calls + results, CoT)
    meta_json TEXT,              -- cost, subagent_reports, registrar output, etc.
    error TEXT
);
"""


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    return c


def init() -> None:
    with _conn() as c:
        c.executescript(SCHEMA)


def _now() -> str:
    return datetime.utcnow().isoformat(timespec="seconds")


def _sid(url: str) -> str:
    return short_id("s", url.strip().lower())


def _aid(name: str) -> str:
    return short_id("a", name.strip().lower())


# ── Profile (assembled from row + active sources + active authors) ──────
def load_profile() -> Profile | None:
    with _conn() as c:
        row = c.execute("SELECT * FROM profile WHERE id = 1").fetchone()
        if not row:
            return None
        srcs = c.execute(
            "SELECT name, url, why FROM sources WHERE active = 1 ORDER BY created_at"
        ).fetchall()
        auths = c.execute(
            "SELECT name, affiliation, why FROM authors WHERE active = 1 ORDER BY created_at"
        ).fetchall()
    return Profile(
        user_summary=row["user_summary"] or "",
        current_question=row["current_question"] or "",
        interests=json.loads(row["interests_json"] or "[]"),
        filter_outs=json.loads(row["filter_outs_json"] or "[]"),
        seed_papers=json.loads(row["seed_papers_json"] or "[]"),
        notes=row["notes"] or "",
        sources=[Source(name=s["name"] or "", url=s["url"], why=s["why"] or "") for s in srcs],
        authors=[Author(name=a["name"], affiliation=a["affiliation"] or "",
                        why=a["why"] or "") for a in auths],
    )


def save_profile(p: Profile, origin: str = "user") -> None:
    """Upsert the profile row and reconcile sources/authors tables.

    Used by onboarding (origin='onboarding'), manual edits (origin='user'), and
    the sample loader (origin='sample'). Reconcile = the incoming lists become
    the active set; existing rows keep their original origin/created_at; rows no
    longer present go inactive.
    """
    with _conn() as c:
        c.execute("""
            INSERT INTO profile (id, user_summary, current_question, interests_json,
                                 filter_outs_json, seed_papers_json, notes, origin, updated_at)
            VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                user_summary=excluded.user_summary,
                current_question=excluded.current_question,
                interests_json=excluded.interests_json,
                filter_outs_json=excluded.filter_outs_json,
                seed_papers_json=excluded.seed_papers_json,
                notes=excluded.notes,
                origin=excluded.origin,
                updated_at=excluded.updated_at
        """, (p.user_summary, p.current_question, json.dumps(p.interests[:15]),
              json.dumps(p.filter_outs), json.dumps(p.seed_papers), p.notes, origin, _now()))

        _reconcile_sources(c, p.sources, origin)
        _reconcile_authors(c, p.authors, origin)


def profile_origin() -> str | None:
    """How the current profile got here: 'onboarding' | 'user' | 'sample' | None."""
    with _conn() as c:
        row = c.execute("SELECT origin FROM profile WHERE id = 1").fetchone()
    return row["origin"] if row else None


def _deactivate_missing(c: sqlite3.Connection, table: str, keep: list[str]) -> None:
    """Mark rows not in `keep` inactive — the incoming set becomes the active set."""
    if keep:
        ph = ",".join("?" * len(keep))
        c.execute(f"UPDATE {table} SET active=0 WHERE active=1 AND id NOT IN ({ph})", tuple(keep))
    else:
        c.execute(f"UPDATE {table} SET active=0 WHERE active=1")


def _reconcile_sources(c: sqlite3.Connection, sources: list[Source], origin: str) -> None:
    incoming = {_sid(s.url): s for s in sources if s.url}
    for sid, s in incoming.items():
        if c.execute("SELECT 1 FROM sources WHERE id=?", (sid,)).fetchone():
            c.execute("UPDATE sources SET name=?, why=?, active=1 WHERE id=?", (s.name, s.why, sid))
        else:
            c.execute("INSERT INTO sources (id, name, url, why, origin, active, created_at) "
                      "VALUES (?, ?, ?, ?, ?, 1, ?)", (sid, s.name, s.url, s.why, origin, _now()))
    _deactivate_missing(c, "sources", list(incoming))


def _reconcile_authors(c: sqlite3.Connection, authors: list[Author], origin: str) -> None:
    incoming = {_aid(a.name): a for a in authors if a.name}
    for aid, a in incoming.items():
        if c.execute("SELECT 1 FROM authors WHERE id=?", (aid,)).fetchone():
            c.execute("UPDATE authors SET affiliation=?, why=?, active=1 WHERE id=?",
                      (a.affiliation, a.why, aid))
        else:
            c.execute("INSERT INTO authors (id, name, affiliation, why, origin, active, created_at) "
                      "VALUES (?, ?, ?, ?, ?, 1, ?)", (aid, a.name, a.affiliation, a.why, origin, _now()))
    _deactivate_missing(c, "authors", list(incoming))


# ── Single-item ops (used when accepting proposals / toggling) ──────────
def add_source(name: str, url: str, why: str, origin: str = "registrar") -> None:
    with _conn() as c:
        c.execute("INSERT OR IGNORE INTO sources (id, name, url, why, origin, active, created_at) "
                  "VALUES (?, ?, ?, ?, ?, 1, ?)",
                  (_sid(url), name, url, why, origin, _now()))


def add_author(name: str, affiliation: str, why: str, origin: str = "registrar") -> None:
    with _conn() as c:
        c.execute("INSERT OR IGNORE INTO authors (id, name, affiliation, why, origin, active, created_at) "
                  "VALUES (?, ?, ?, ?, ?, 1, ?)",
                  (_aid(name), name, affiliation, why, origin, _now()))


# ── Proposals ───────────────────────────────────────────────────────────
def add_proposal(kind: str, payload: dict, rationale: str, digest_id: str = "") -> None:
    assert kind in ("source", "author", "profile")
    pid = short_id("p", kind, json.dumps(payload, sort_keys=True))
    with _conn() as c:
        # Don't re-create a proposal that's already pending/decided for the same change.
        if c.execute("SELECT 1 FROM proposals WHERE id=?", (pid,)).fetchone():
            return
        c.execute("INSERT INTO proposals (id, kind, payload_json, rationale, status, created_at, digest_id) "
                  "VALUES (?, ?, ?, ?, 'pending', ?, ?)",
                  (pid, kind, json.dumps(payload), rationale, _now(), digest_id))


def list_proposals(status: str = "pending") -> list[dict]:
    with _conn() as c:
        rows = c.execute("SELECT * FROM proposals WHERE status=? ORDER BY created_at DESC",
                         (status,)).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["payload"] = json.loads(d.pop("payload_json"))
            out.append(d)
        return out


def accept_proposal(proposal_id: str) -> None:
    with _conn() as c:
        r = c.execute("SELECT kind, payload_json FROM proposals WHERE id=? AND status='pending'",
                      (proposal_id,)).fetchone()
        if not r:
            return
        kind, payload = r["kind"], json.loads(r["payload_json"])
        if kind == "source":
            add_source(payload.get("name", ""), payload["url"], payload.get("why", ""),
                       origin="registrar")
        elif kind == "author":
            add_author(payload["name"], payload.get("affiliation", ""),
                       payload.get("why", ""), origin="registrar")
        elif kind == "profile":
            # payload: {field: value} for profile-row fields
            _apply_profile_change(c, payload)
        c.execute("UPDATE proposals SET status='accepted' WHERE id=?", (proposal_id,))


def reject_proposal(proposal_id: str) -> None:
    with _conn() as c:
        c.execute("UPDATE proposals SET status='rejected' WHERE id=? AND status='pending'",
                  (proposal_id,))


_PROFILE_LIST_COLS = {"interests": "interests_json", "filter_outs": "filter_outs_json"}
_PROFILE_SCALAR_COLS = {"user_summary": "user_summary",
                        "current_question": "current_question", "notes": "notes"}


def _apply_profile_change(c: sqlite3.Connection, payload: dict) -> None:
    """payload = {"field": <field>, "proposed": <value>}.

    For list fields (interests/filter_outs), `proposed` is the item(s) to ADD
    (appended, deduped). For scalar fields, `proposed` replaces the value.
    """
    field = payload.get("field")
    proposed = payload.get("proposed")
    if field in _PROFILE_LIST_COLS:
        col = _PROFILE_LIST_COLS[field]
        row = c.execute(f"SELECT {col} FROM profile WHERE id=1").fetchone()
        current = json.loads((row[col] if row else None) or "[]")
        additions = proposed if isinstance(proposed, list) else [proposed]
        for a in additions:
            if a and a not in current:
                current.append(a)
        c.execute(f"UPDATE profile SET {col}=?, updated_at=? WHERE id=1", (json.dumps(current), _now()))
    elif field in _PROFILE_SCALAR_COLS:
        col = _PROFILE_SCALAR_COLS[field]
        c.execute(f"UPDATE profile SET {col}=?, updated_at=? WHERE id=1", (proposed, _now()))


# ── Digests ───────────────────────────────────────────────────────────
def save_digest(d: Digest | dict) -> None:
    """Persist a digest. Accepts a Digest or its dict form (the latter loads
    saved eval digests)."""
    row = d.to_dict() if isinstance(d, Digest) else d
    with _conn() as c:
        c.execute(
            "INSERT OR REPLACE INTO digests (id, generated_at, window_start, window_end, payload) "
            "VALUES (?, ?, ?, ?, ?)",
            (row["id"], row["generated_at"], row["window_start"], row["window_end"], json.dumps(row))
        )


def reset_feed() -> None:
    """Clear digests + pending proposals — used when loading a sample profile."""
    with _conn() as c:
        c.execute("DELETE FROM digests")
        c.execute("DELETE FROM proposals")


def get_digest(digest_id: str) -> dict | None:
    with _conn() as c:
        r = c.execute("SELECT payload FROM digests WHERE id = ?", (digest_id,)).fetchone()
        return json.loads(r["payload"]) if r else None


def list_digests() -> list[dict]:
    with _conn() as c:
        rows = c.execute(
            "SELECT id, generated_at, window_start, window_end FROM digests "
            "ORDER BY generated_at DESC"
        ).fetchall()
        return [dict(r) for r in rows]




# ── Runs (saved agent runs: full log + meta, for debugging / repro) ────
def start_run(run_id: str, kind: str, window_start: str = "", window_end: str = "") -> None:
    with _conn() as c:
        c.execute("INSERT OR REPLACE INTO runs (id, kind, status, started_at, window_start, window_end) "
                  "VALUES (?, ?, 'running', ?, ?, ?)",
                  (run_id, kind, _now(), window_start, window_end))


def finish_run(run_id: str, *, status: str, log: str = "", meta: dict | None = None,
               digest_id: str = "", error: str = "") -> None:
    with _conn() as c:
        c.execute("UPDATE runs SET status=?, finished_at=?, log=?, meta_json=?, digest_id=?, error=? "
                  "WHERE id=?",
                  (status, _now(), log, json.dumps(meta or {}), digest_id, error, run_id))


def get_run(run_id: str) -> dict | None:
    with _conn() as c:
        r = c.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
        if not r:
            return None
        d = dict(r)
        d["meta"] = json.loads(d.pop("meta_json") or "{}")
        return d


def list_runs(limit: int = 50) -> list[dict]:
    with _conn() as c:
        rows = c.execute("SELECT id, kind, status, started_at, finished_at, window_start, "
                         "window_end, digest_id FROM runs ORDER BY started_at DESC LIMIT ?",
                         (limit,)).fetchall()
        return [dict(r) for r in rows]
