"""Dataclasses for everything that has a shape.

These are the only data structures the rest of the code knows about. JSON
files and SQLite rows are converted to/from these at the boundaries
(store.py).
"""

from __future__ import annotations
import hashlib
from dataclasses import dataclass, field, asdict
from typing import Optional


@dataclass
class Source:
    """One place to crawl. Trust ∈ [0, 50]."""
    slug: str
    name: str
    url: str
    trust: int = 25
    rationale: str = ""
    feed_url: Optional[str] = None  # discovered RSS/Atom — preferred over HTML parsing


@dataclass
class Author:
    name: str
    affiliation: Optional[str] = None
    why: Optional[str] = None


@dataclass
class Profile:
    """The user's context map. Single source of truth for personalization."""
    user_summary: str = ""
    research_areas: list[str] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)
    trusted_authors: list[Author] = field(default_factory=list)
    sources: list[Source] = field(default_factory=list)
    subfield_weights: dict[str, int] = field(default_factory=dict)
    dislikes: list[str] = field(default_factory=list)  # negative preferences (phrases)
    hidden_with_reason: list[dict] = field(default_factory=list)
    # ↑ [{"item_title": ..., "reason": ..., "venue": ..., "date": ...}]
    # Feeds into the curation prompt so the agent learns what to avoid.
    notes: str = ""
    user_identity: dict = field(default_factory=dict)
    research_notes: str = ""

    @classmethod
    def from_dict(cls, d: dict) -> "Profile":
        # Tolerate either rich dicts or bare strings for authors
        authors_raw = d.get("trusted_authors", [])
        authors = []
        for a in authors_raw:
            if isinstance(a, dict):
                authors.append(Author(
                    name=a.get("name", ""),
                    affiliation=a.get("affiliation"),
                    why=a.get("why"),
                ))
            elif isinstance(a, str):
                authors.append(Author(name=a))

        sources = []
        for s in d.get("sources", []):
            if not (s.get("slug") and s.get("url")):
                continue
            sources.append(Source(
                slug=s["slug"], name=s.get("name", s["slug"]), url=s["url"],
                trust=int(s.get("trust", 25)),
                rationale=s.get("rationale", ""),
                feed_url=s.get("feed_url"),
            ))

        return cls(
            user_summary=d.get("user_summary", ""),
            research_areas=list(d.get("research_areas", [])),
            keywords=list(d.get("keywords", [])),
            trusted_authors=authors,
            sources=sources,
            subfield_weights=dict(d.get("subfield_weights", {})),
            dislikes=list(d.get("dislikes", [])),
            hidden_with_reason=list(d.get("hidden_with_reason", [])),
            notes=d.get("notes", ""),
            user_identity=dict(d.get("user_identity", {})),
            research_notes=d.get("research_notes", ""),
        )

    def to_dict(self) -> dict:
        return asdict(self)

    # Convenience views used by scoring + UI
    def trusted_author_names(self) -> list[str]:
        return [a.name for a in self.trusted_authors if a.name]

    def venue_trust(self, slug: str) -> int:
        for s in self.sources:
            if s.slug == slug:
                return s.trust
        return 15  # unknown source gets a floor


# ScoreBreakdown is gone — the curation agent is the single source of truth
# for "is this relevant" and ordering. Each Item carries its curation_rank
# and the agent's free-text relevance_reason.


@dataclass
class Item:
    """One piece of work. Cross-posts collapse to a single Item with extra_venues."""
    id: str
    title: str
    url: str            # canonical (primary) URL
    venue: str          # canonical source slug
    date: str           # YYYY-MM-DD
    authors: list[str]
    description: str
    subfield: str = "other"
    af_karma: Optional[int] = None
    af_comments: Optional[int] = None
    af_url: Optional[str] = None
    arxiv_id: Optional[str] = None
    arxiv_url: Optional[str] = None
    affiliations: list[str] = field(default_factory=list)
    extra_venues: list[dict] = field(default_factory=list)  # [{"venue", "url", "date"}]
    publication_venue: Optional[str] = None  # e.g. "NeurIPS 2026" — quality signal from S2
    curation_rank: Optional[int] = None      # 1-based; set by the curation agent
    relevance_reason: Optional[str] = None   # the agent's text rationale
    score: float = 0.0                       # derived from curation_rank (for SQL ordering)
    is_read: bool = False
    is_starred: bool = False

    @staticmethod
    def make_id(url: str) -> str:
        return "i_" + hashlib.sha1(url.encode()).hexdigest()[:12]

    @classmethod
    def from_raw(cls, raw: dict) -> "Item":
        """Build from a gatherer-emitted dict (loose shape)."""
        url = raw.get("url") or raw.get("primary_url") or ""
        return cls(
            id=cls.make_id(url),
            title=raw["title"],
            url=url,
            venue=raw.get("venue") or raw.get("primary_venue") or "other",
            date=raw.get("date") or raw.get("primary_date") or "",
            authors=list(raw.get("authors", [])),
            description=raw.get("description", ""),
            subfield=raw.get("subfield", "other"),
            af_karma=raw.get("af_karma") or raw.get("karma"),
            af_comments=raw.get("af_comments") or raw.get("comments"),
            af_url=raw.get("af_url"),
            arxiv_id=raw.get("arxiv_id"),
            arxiv_url=raw.get("arxiv_url"),
            affiliations=list(raw.get("affiliations", [])),
            extra_venues=list(raw.get("all_other_venues") or raw.get("extra_venues") or []),
            publication_venue=raw.get("publication_venue"),
            curation_rank=raw.get("curation_rank"),
            relevance_reason=raw.get("relevance_reason"),
        )
