"""Score = derived from curation_rank.

The curation agent is the single source of truth for ordering. We just
translate its 1-based rank into a numeric score that SQL can sort.

  score = (max_curated - curation_rank + 1) when curation_rank is set
  score = 0 otherwise

No arbitrary venue/author/topic weighting math here. The agent has already
considered all of those when picking the order.
"""

from __future__ import annotations
from .models import Item


def score_from_rank(rank: int | None, total_curated: int) -> float:
    """Convert 1-based curation_rank into a sort-friendly score (higher = better)."""
    if rank is None:
        return 0.0
    return float(max(0, total_curated - rank + 1))


def rescore_all() -> int:
    """Recompute scores from stored curation_ranks.

    Cheap. Called after profile edits even though edits don't change curation_rank —
    keeping the call site so the contract stays simple. To actually re-rank,
    the user clicks Re-run discovery.
    """
    from . import store
    n = 0
    with store.conn() as c:
        # Get the max curation_rank across all items so newer batches dominate
        max_rank = c.execute(
            "SELECT MAX(curation_rank) FROM items WHERE curation_rank IS NOT NULL"
        ).fetchone()[0] or 0
        for r in c.execute("SELECT id, curation_rank FROM items").fetchall():
            new_score = score_from_rank(r["curation_rank"], max_rank)
            c.execute("UPDATE items SET score = ? WHERE id = ?", (new_score, r["id"]))
            n += 1
    return n
