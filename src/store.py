"""SQLite + profile.json + query cache. The persistence layer."""

from __future__ import annotations
import json
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from typing import Iterator

from .config import DB_PATH, PROFILE_PATH, QUERY_CACHE_PATH, DATA
from .models import Profile, Item, Ranking


SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    url TEXT NOT NULL,
    venue TEXT NOT NULL,
    date TEXT NOT NULL,
    authors_json TEXT NOT NULL,
    description TEXT,
    citation_count INTEGER,
    af_karma INTEGER,
    af_comments INTEGER,
    recent_comment_count INTEGER,
    arxiv_id TEXT,
    arxiv_url TEXT,
    publication_venue TEXT,
    affiliations_json TEXT,
    discovered_via TEXT,
    extra_urls_json TEXT,
    is_read INTEGER DEFAULT 0,
    is_starred INTEGER DEFAULT 0,
    fetched_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_items_date ON items(date);
CREATE INDEX IF NOT EXISTS idx_items_venue ON items(venue);

CREATE TABLE IF NOT EXISTS rankings (
    item_id TEXT NOT NULL,
    profile_version TEXT NOT NULL,
    bucket TEXT NOT NULL,
    reasons_json TEXT,
    why TEXT,
    novelty TEXT,
    ranked_at TEXT NOT NULL,
    PRIMARY KEY (item_id, profile_version),
    FOREIGN KEY (item_id) REFERENCES items(id)
);
CREATE INDEX IF NOT EXISTS idx_rankings_profile_bucket ON rankings(profile_version, bucket);
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


# ─── Profile ─────────────────────────────────────────────────────────
def load_profile() -> Profile | None:
    if not PROFILE_PATH.exists():
        return None
    try:
        return Profile.from_dict(json.loads(PROFILE_PATH.read_text()))
    except (json.JSONDecodeError, OSError):
        return None


def save_profile(p: Profile) -> None:
    DATA.mkdir(parents=True, exist_ok=True)
    p.version_hash = p.compute_hash()
    PROFILE_PATH.write_text(json.dumps(p.to_dict(), indent=2))


# ─── Query cache (profile-hash → queries) ────────────────────────────
def load_queries(profile_version: str) -> list[str] | None:
    if not QUERY_CACHE_PATH.exists():
        return None
    try:
        cache = json.loads(QUERY_CACHE_PATH.read_text())
        return cache.get(profile_version)
    except (json.JSONDecodeError, OSError):
        return None


def save_queries(profile_version: str, queries: list[str]) -> None:
    DATA.mkdir(parents=True, exist_ok=True)
    cache = {}
    if QUERY_CACHE_PATH.exists():
        try:
            cache = json.loads(QUERY_CACHE_PATH.read_text())
        except json.JSONDecodeError:
            cache = {}
    cache[profile_version] = queries
    QUERY_CACHE_PATH.write_text(json.dumps(cache, indent=2))


# ─── Items ───────────────────────────────────────────────────────────
def _row_to_item(r: sqlite3.Row) -> Item:
    return Item(
        id=r["id"], title=r["title"], url=r["url"], venue=r["venue"], date=r["date"],
        authors=json.loads(r["authors_json"]) if r["authors_json"] else [],
        description=r["description"] or "",
        citation_count=r["citation_count"],
        af_karma=r["af_karma"], af_comments=r["af_comments"],
        recent_comment_count=r["recent_comment_count"],
        arxiv_id=r["arxiv_id"], arxiv_url=r["arxiv_url"],
        publication_venue=r["publication_venue"],
        affiliations=json.loads(r["affiliations_json"]) if r["affiliations_json"] else [],
        discovered_via=r["discovered_via"],
        extra_urls=json.loads(r["extra_urls_json"]) if r["extra_urls_json"] else [],
        is_read=bool(r["is_read"]), is_starred=bool(r["is_starred"]),
    )


def upsert_item(item: Item, c: sqlite3.Connection) -> None:
    c.execute("""
        INSERT INTO items
        (id, title, url, venue, date, authors_json, description,
         citation_count, af_karma, af_comments, recent_comment_count,
         arxiv_id, arxiv_url, publication_venue, affiliations_json,
         discovered_via, extra_urls_json, fetched_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            title = excluded.title,
            description = COALESCE(excluded.description, items.description),
            citation_count = COALESCE(excluded.citation_count, items.citation_count),
            af_karma = COALESCE(excluded.af_karma, items.af_karma),
            af_comments = COALESCE(excluded.af_comments, items.af_comments),
            recent_comment_count = COALESCE(excluded.recent_comment_count, items.recent_comment_count),
            publication_venue = COALESCE(excluded.publication_venue, items.publication_venue),
            extra_urls_json = COALESCE(excluded.extra_urls_json, items.extra_urls_json)
    """, (
        item.id, item.title, item.url, item.venue, item.date,
        json.dumps(item.authors), item.description,
        item.citation_count, item.af_karma, item.af_comments, item.recent_comment_count,
        item.arxiv_id, item.arxiv_url, item.publication_venue,
        json.dumps(item.affiliations) if item.affiliations else None,
        item.discovered_via,
        json.dumps(item.extra_urls) if item.extra_urls else None,
        datetime.utcnow().isoformat(timespec="seconds"),
    ))


def get_item(item_id: str) -> Item | None:
    with conn() as c:
        r = c.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone()
        return _row_to_item(r) if r else None


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


# ─── Rankings ────────────────────────────────────────────────────────
def upsert_ranking(r: Ranking, c: sqlite3.Connection) -> None:
    c.execute("""
        INSERT INTO rankings (item_id, profile_version, bucket, reasons_json, why, novelty, ranked_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(item_id, profile_version) DO UPDATE SET
            bucket = excluded.bucket,
            reasons_json = excluded.reasons_json,
            why = excluded.why,
            novelty = excluded.novelty,
            ranked_at = excluded.ranked_at
    """, (
        r.item_id, r.profile_version, r.bucket,
        json.dumps(r.reasons), r.why, r.novelty, r.ranked_at,
    ))


# ─── Queries: items × rankings for a profile_version ────────────────
def list_items_ranked(
    profile_version: str,
    *,
    date_start: str | None = None,
    date_end: str | None = None,
    limit: int = 200,
    keep_buckets: tuple[str, ...] = ("core", "adjacent"),
) -> list[tuple[Item, Ranking | None]]:
    """Items in a date window joined with their ranking for this profile version.

    Only returns items in `keep_buckets` (default: core + adjacent — peripheral
    and off-topic are dropped from the feed). Items without rankings are also
    dropped because they haven't been judged.

    Returns (item, ranking) tuples sorted by (bucket_priority, date desc).
    """
    where = []
    params: list = [profile_version]
    if date_start:
        where.append("items.date >= ?"); params.append(date_start)
    if date_end:
        where.append("items.date <= ?"); params.append(date_end)
    if keep_buckets:
        placeholders = ",".join("?" * len(keep_buckets))
        where.append(f"r.bucket IN ({placeholders})")
        params.extend(keep_buckets)

    sql = """
        SELECT items.*, r.bucket, r.reasons_json, r.why, r.novelty, r.ranked_at
        FROM items
        LEFT JOIN rankings r ON r.item_id = items.id AND r.profile_version = ?
    """
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += """
        ORDER BY
            CASE r.bucket WHEN 'core' THEN 0 WHEN 'adjacent' THEN 1
                          WHEN 'peripheral' THEN 2 ELSE 3 END,
            items.date DESC
        LIMIT ?
    """
    params.append(limit)

    out = []
    with conn() as c:
        for row in c.execute(sql, params).fetchall():
            item = _row_to_item(row)
            ranking = None
            if row["bucket"]:
                ranking = Ranking(
                    item_id=item.id, profile_version=profile_version,
                    bucket=row["bucket"],
                    reasons=json.loads(row["reasons_json"]) if row["reasons_json"] else [],
                    why=row["why"] or "",
                    novelty=row["novelty"],
                    ranked_at=row["ranked_at"] or "",
                )
            out.append((item, ranking))
    return out


def items_in_window(date_start: str, date_end: str, limit: int = 1000) -> list[Item]:
    """Used by rerank to find items needing a new ranking."""
    with conn() as c:
        rows = c.execute(
            "SELECT * FROM items WHERE date >= ? AND date <= ? ORDER BY date DESC LIMIT ?",
            (date_start, date_end, limit),
        ).fetchall()
        return [_row_to_item(r) for r in rows]


def list_items_ranked_simple(
    profile_version: str, date_start: str, date_end: str, limit: int = 10,
) -> list[tuple[Item, "Ranking | None"]]:
    """Top items in a single week-style window. Same shape as list_items_ranked
    but with a smaller default limit for week previews."""
    return list_items_ranked(
        profile_version,
        date_start=date_start, date_end=date_end,
        limit=limit, keep_buckets=("core", "adjacent"),
    )


def stats() -> dict:
    with conn() as c:
        total = c.execute("SELECT COUNT(*) FROM items").fetchone()[0]
        ranked = c.execute("SELECT COUNT(DISTINCT item_id) FROM rankings").fetchone()[0]
        return {"total_items": total, "ranked_items": ranked}


def transparency_for(profile_version: str, date_start: str, date_end: str) -> dict:
    """Counts for the transparency block: considered N, by bucket."""
    with conn() as c:
        total_in_window = c.execute(
            "SELECT COUNT(*) FROM items WHERE date >= ? AND date <= ?",
            (date_start, date_end),
        ).fetchone()[0]
        by_bucket = {row["bucket"]: row["c"] for row in c.execute(
            """SELECT r.bucket, COUNT(*) c
               FROM items JOIN rankings r ON r.item_id = items.id
               WHERE r.profile_version = ? AND items.date >= ? AND items.date <= ?
               GROUP BY r.bucket""",
            (profile_version, date_start, date_end),
        )}
    return {"total_in_window": total_in_window, "by_bucket": by_bucket}
