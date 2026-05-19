"""Dataclasses. Profile, Item, Ranking, Source, Author."""

from __future__ import annotations
import hashlib
import json
from dataclasses import dataclass, field, asdict
from typing import Optional


@dataclass
class Source:
    """A pull-mode source (lab/org page). Static config-driven."""
    slug: str
    name: str
    url: str
    feed: Optional[str] = None


@dataclass
class Author:
    name: str
    affiliation: Optional[str] = None
    why: Optional[str] = None


@dataclass
class Profile:
    """The user's research-discovery profile. Single artifact; drives everything."""
    user_summary: str = ""
    tags: list[str] = field(default_factory=list)
    research_areas: list[str] = field(default_factory=list)
    current_question: str = ""
    filter_outs: list[str] = field(default_factory=list)
    followed_authors: list[Author] = field(default_factory=list)
    # Raw onboarding inputs (preserved so re-onboarding can use them)
    seed_papers: list[str] = field(default_factory=list)
    scholar_url: Optional[str] = None
    notes: str = ""
    version_hash: str = ""

    @classmethod
    def from_dict(cls, d: dict) -> "Profile":
        authors_raw = d.get("followed_authors", [])
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
        return cls(
            user_summary=d.get("user_summary", ""),
            tags=list(d.get("tags", [])),
            research_areas=list(d.get("research_areas", [])),
            current_question=d.get("current_question", ""),
            filter_outs=list(d.get("filter_outs", [])),
            followed_authors=authors,
            seed_papers=list(d.get("seed_papers", [])),
            scholar_url=d.get("scholar_url"),
            notes=d.get("notes", ""),
            version_hash=d.get("version_hash", ""),
        )

    def to_dict(self) -> dict:
        return asdict(self)

    def compute_hash(self) -> str:
        """Hash of the parts that affect query generation + ranking."""
        key = json.dumps({
            "tags": sorted(self.tags),
            "areas": sorted(self.research_areas),
            "authors": sorted(a.name for a in self.followed_authors),
            "filter_outs": sorted(self.filter_outs),
            "question": self.current_question.strip(),
        }, sort_keys=True)
        return hashlib.sha1(key.encode()).hexdigest()[:12]

    def followed_author_names(self) -> list[str]:
        return [a.name for a in self.followed_authors if a.name]


@dataclass
class Item:
    """One piece of work. Cross-posts merge into one Item via dedup."""
    id: str
    title: str
    url: str
    venue: str           # source slug ("arxiv_standalone", "alignment_forum", or a PULL source slug)
    date: str            # YYYY-MM-DD
    authors: list[str]
    description: str     # abstract or summary

    # Activity / quality signals
    citation_count: Optional[int] = None
    af_karma: Optional[int] = None
    af_comments: Optional[int] = None
    recent_comment_count: Optional[int] = None  # AF/LW: comments in last N days

    # IDs and venue metadata
    arxiv_id: Optional[str] = None
    arxiv_url: Optional[str] = None
    publication_venue: Optional[str] = None   # "NeurIPS 2026" etc. from S2
    affiliations: list[str] = field(default_factory=list)

    # Provenance
    discovered_via: Optional[str] = None      # "kw:sparse_autoencoder", "author:Nanda", "feed:goodfire"
    extra_urls: list[dict] = field(default_factory=list)  # cross-post URLs

    # Per-item user state
    is_read: bool = False
    is_starred: bool = False

    @staticmethod
    def make_id(url: str) -> str:
        return "i_" + hashlib.sha1(url.encode()).hexdigest()[:12]


@dataclass
class Ranking:
    """The ranker's per-item opinion. Re-generated whenever profile changes."""
    item_id: str
    profile_version: str
    bucket: str           # core / adjacent / peripheral / off-topic
    reasons: list[str] = field(default_factory=list)
    why: str = ""
    novelty: Optional[str] = None
    ranked_at: str = ""

    def bucket_priority(self) -> int:
        """0 = most important. Used for sort order in the UI."""
        return {"core": 0, "adjacent": 1, "peripheral": 2, "off-topic": 3}.get(self.bucket, 4)
