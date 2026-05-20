"""Data shapes. Nothing fancy — dataclasses + JSON in/out."""
from __future__ import annotations
import hashlib
from dataclasses import dataclass, field, asdict


def hash12(s: str) -> str:
    """First 12 hex chars of sha1(s) — the shared short-id primitive."""
    return hashlib.sha1(s.encode()).hexdigest()[:12]


def short_id(prefix: str, *parts: str) -> str:
    """Stable short id, '<prefix>_<hash>', from the joined parts."""
    return f"{prefix}_{hash12(':'.join(parts))}"


@dataclass
class Source:
    name: str          # human label
    url: str           # page to fetch
    why: str = ""      # 1-line: why this source is in the profile


@dataclass
class Author:
    name: str
    affiliation: str = ""
    why: str = ""


@dataclass
class Profile:
    """The user's editable context. Drives every agent."""
    user_summary: str = ""                                 # narrative paragraph
    current_question: str = ""                             # what they're tracking now
    interests: list[str] = field(default_factory=list)     # topics/methods we look for
    authors: list[Author] = field(default_factory=list)
    sources: list[Source] = field(default_factory=list)    # lab/org URLs
    filter_outs: list[str] = field(default_factory=list)
    seed_papers: list[str] = field(default_factory=list)   # urls/titles, kept so we can suppress
    notes: str = ""

    @classmethod
    def from_dict(cls, d: dict) -> "Profile":
        return cls(
            user_summary=d.get("user_summary", ""),
            current_question=d.get("current_question", ""),
            interests=list(d.get("interests", [])),
            authors=[Author(**a) if isinstance(a, dict) else Author(name=str(a))
                     for a in d.get("authors", [])],
            sources=[Source(**s) for s in d.get("sources", []) if s.get("url")],
            filter_outs=list(d.get("filter_outs", [])),
            seed_papers=list(d.get("seed_papers", [])),
            notes=d.get("notes", ""),
        )

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Item:
    """One candidate. Subagents emit these; the curator decides what makes the digest."""
    id: str
    title: str
    url: str
    venue: str                # source slug or "arxiv" or "alignment_forum" etc.
    date: str                 # YYYY-MM-DD
    authors: list[str] = field(default_factory=list)
    summary: str = ""         # 1-3 sentence abstract or post summary
    discovered_via: str = ""  # which subagent / search produced this
    why_kept: str = ""        # subagent's reasoning when kept
    venue_detail: str = ""    # published venue (e.g. "NeurIPS"), if accepted somewhere

    # Optional signals (None if not applicable)
    karma: int | None = None
    comments: int | None = None
    citations: int | None = None
    arxiv_id: str | None = None

    @staticmethod
    def make_id(url: str) -> str:
        return short_id("i", url)


@dataclass
class Digest:
    """What the curator produces. Persisted as one row + JSON."""
    id: str
    generated_at: str        # ISO datetime
    window_start: str
    window_end: str
    profile_snapshot: dict   # copy of profile at generation time
    kept: list[Item]         # ranked items shown in the digest (best first)
    dropped: list[dict]      # [{title, url, source, reason}] — rejection ledger
    coverage: dict           # {items_considered, items_kept, items_dropped}

    def to_dict(self) -> dict:
        d = asdict(self)
        return d
