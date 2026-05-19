"""SQLite + profile persistence. Single layer between dataclasses and disk."""

from __future__ import annotations
import json
import sqlite3
from contextlib import contextmanager
from datetime import date, timedelta
from typing import Iterator

from .config import DB_PATH, PROFILE_PATH, DATA
from .models import Item, Profile


SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    url TEXT NOT NULL,
    venue TEXT NOT NULL,
    date TEXT NOT NULL,
    authors_json TEXT NOT NULL,
    description TEXT,
    subfield TEXT,
    af_karma INTEGER,
    af_comments INTEGER,
    af_url TEXT,
    arxiv_id TEXT,
    arxiv_url TEXT,
    affiliations_json TEXT,
    extra_venues_json TEXT,
    publication_venue TEXT,
    curation_rank INTEGER,
    relevance_reason TEXT,
    score REAL DEFAULT 0,
    is_read INTEGER DEFAULT 0,
    is_starred INTEGER DEFAULT 0,
    ingested_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_items_date ON items(date);
CREATE INDEX IF NOT EXISTS idx_items_venue ON items(venue);
CREATE INDEX IF NOT EXISTS idx_items_score ON items(score);

CREATE VIRTUAL TABLE IF NOT EXISTS items_fts USING fts5(
    item_id UNINDEXED, title, description, authors_text,
    tokenize='porter unicode61'
);
"""


@contextmanager
def conn() -> Iterator[sqlite3.Connection]:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    try:
        yield c
        c.commit()
    finally:
        c.close()


def init() -> None:
    with conn() as c:
        c.executescript(SCHEMA)


# ── Profile ──────────────────────────────────────────────────────────
def load_profile() -> Profile | None:
    if not PROFILE_PATH.exists():
        return None
    try:
        return Profile.from_dict(json.loads(PROFILE_PATH.read_text()))
    except (json.JSONDecodeError, OSError):
        return None


def save_profile(p: Profile) -> None:
    DATA.mkdir(parents=True, exist_ok=True)
    PROFILE_PATH.write_text(json.dumps(p.to_dict(), indent=2))


# ── Items ────────────────────────────────────────────────────────────
def _row_to_item(r: sqlite3.Row) -> Item:
    return Item(
        id=r["id"], title=r["title"], url=r["url"], venue=r["venue"], date=r["date"],
        authors=json.loads(r["authors_json"]) if r["authors_json"] else [],
        description=r["description"] or "",
        subfield=r["subfield"] or "other",
        af_karma=r["af_karma"], af_comments=r["af_comments"], af_url=r["af_url"],
        arxiv_id=r["arxiv_id"], arxiv_url=r["arxiv_url"],
        affiliations=json.loads(r["affiliations_json"]) if r["affiliations_json"] else [],
        extra_venues=json.loads(r["extra_venues_json"]) if r["extra_venues_json"] else [],
        publication_venue=r["publication_venue"],
        curation_rank=r["curation_rank"],
        relevance_reason=r["relevance_reason"],
        score=r["score"] or 0,
        is_read=bool(r["is_read"]), is_starred=bool(r["is_starred"]),
    )


def save_item(item: Item, c: sqlite3.Connection) -> None:
    from datetime import datetime
    c.execute("""
        INSERT OR REPLACE INTO items
        (id, title, url, venue, date, authors_json, description, subfield,
         af_karma, af_comments, af_url, arxiv_id, arxiv_url,
         affiliations_json, extra_venues_json, publication_venue,
         curation_rank, relevance_reason,
         score, is_read, is_starred, ingested_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        item.id, item.title, item.url, item.venue, item.date,
        json.dumps(item.authors), item.description, item.subfield,
        item.af_karma, item.af_comments, item.af_url,
        item.arxiv_id, item.arxiv_url,
        json.dumps(item.affiliations) if item.affiliations else None,
        json.dumps(item.extra_venues) if item.extra_venues else None,
        item.publication_venue,
        item.curation_rank, item.relevance_reason,
        item.score, int(item.is_read), int(item.is_starred),
        datetime.utcnow().isoformat(timespec="seconds"),
    ))
    c.execute("DELETE FROM items_fts WHERE item_id = ?", (item.id,))
    c.execute(
        "INSERT INTO items_fts (item_id, title, description, authors_text) VALUES (?, ?, ?, ?)",
        (item.id, item.title, item.description, " ".join(item.authors)),
    )


def wipe_items() -> None:
    with conn() as c:
        c.execute("DELETE FROM items")
        c.execute("DELETE FROM items_fts")


def get_item(item_id: str) -> Item | None:
    with conn() as c:
        r = c.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone()
        return _row_to_item(r) if r else None


def list_items(*, date_start: str | None = None, date_end: str | None = None,
                venue: str | None = None, subfield: str | None = None,
                author: str | None = None, qstr: str | None = None,
                limit: int = 500) -> list[Item]:
    sql = "SELECT items.* FROM items"
    where, params = [], []
    if qstr:
        sql += " JOIN items_fts ON items.id = items_fts.item_id"
        where.append("items_fts MATCH ?")
        params.append(qstr)
    if date_start: where.append("date >= ?"); params.append(date_start)
    if date_end:   where.append("date <= ?"); params.append(date_end)
    if venue:      where.append("venue = ?"); params.append(venue)
    if subfield:   where.append("subfield = ?"); params.append(subfield)
    if author:     where.append("authors_json LIKE ?"); params.append(f"%{author}%")
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY score DESC, date DESC LIMIT ?"
    params.append(limit)
    with conn() as c:
        return [_row_to_item(r) for r in c.execute(sql, params).fetchall()]


def items_in_week(week_end: str, limit: int = 30) -> list[Item]:
    start = (date.fromisoformat(week_end) - timedelta(days=6)).isoformat()
    return list_items(date_start=start, date_end=week_end, limit=limit)


def items_in_month(month_end: str, limit: int = 40) -> list[Item]:
    start = (date.fromisoformat(month_end) - timedelta(days=29)).isoformat()
    return list_items(date_start=start, date_end=month_end, limit=limit)


# ── Per-item user actions ────────────────────────────────────────────
def set_read(item_id: str, read: bool = True) -> None:
    with conn() as c:
        c.execute("UPDATE items SET is_read = ? WHERE id = ?", (int(read), item_id))


def toggle_starred(item_id: str) -> bool:
    with conn() as c:
        r = c.execute("SELECT is_starred FROM items WHERE id = ?", (item_id,)).fetchone()
        if not r:
            return False
        new = 0 if r["is_starred"] else 1
        c.execute("UPDATE items SET is_starred = ? WHERE id = ?", (new, item_id))
        return bool(new)


def stats() -> dict:
    with conn() as c:
        total = c.execute("SELECT COUNT(*) FROM items").fetchone()[0]
        starred = c.execute("SELECT COUNT(*) FROM items WHERE is_starred = 1").fetchone()[0]
        return {"total": total, "starred": starred}
