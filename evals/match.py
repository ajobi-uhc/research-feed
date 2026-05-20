"""Match items between our feed and the oracle feed.

Two items match if:
  1. Normalized URLs are equal, OR
  2. Normalized titles are equal, OR
  3. rapidfuzz title similarity >= TITLE_THRESHOLD
"""
from __future__ import annotations
import re
from urllib.parse import urlparse

from rapidfuzz import fuzz


TITLE_THRESHOLD = 82.0   # fuzz.ratio in [0,100]


def _canonical_id(url: str) -> str:
    """A host-independent identity for a URL where one exists.

    - arxiv:   the paper id (so /abs/2605.06610 and /pdf/2605.06610v2 match)
    - AF / LW: the shared post id (alignmentforum.org and lesswrong.com mirror
      the same post under the same id — e.g. /posts/eAQZaiC3PcBhS4HjM/...)
    Returns "" when no stable id is recoverable.
    """
    if not url:
        return ""
    u = url.strip().lower()
    m = re.search(r"arxiv\.org/(?:abs|pdf)/(\d{4}\.\d{4,5})", u)
    if m:
        return f"arxiv:{m.group(1)}"
    m = re.search(r"(?:alignmentforum\.org|lesswrong\.com|greaterwrong\.com)/posts/([a-z0-9]+)", u)
    if m:
        return f"forum:{m.group(1)}"
    return ""


def _norm_url(url: str) -> str:
    if not url:
        return ""
    p = urlparse(url.strip())
    host = p.netloc.lower().replace("www.", "")
    path = p.path.rstrip("/").lower()
    return f"{host}{path}"


def _norm_title(t: str) -> str:
    if not t:
        return ""
    t = re.sub(r"[^\w\s]", " ", t.lower())
    return re.sub(r"\s+", " ", t).strip()


def items_match(a: dict, b: dict) -> bool:
    # 1. Canonical id (arxiv id / shared AF-LW post id) — host-independent.
    ca, cb = _canonical_id(a.get("url", "")), _canonical_id(b.get("url", ""))
    if ca and cb:
        return ca == cb           # both have ids → decide solely on id
    # 2. Exact normalized URL.
    ua, ub = _norm_url(a.get("url", "")), _norm_url(b.get("url", ""))
    if ua and ub and ua == ub:
        return True
    # 3. Title equality / fuzzy match.
    ta, tb = _norm_title(a.get("title", "")), _norm_title(b.get("title", ""))
    if not ta or not tb:
        return False
    if ta == tb:
        return True
    # Substring containment catches "[Linkpost] X" vs "X (long descriptive)".
    if len(ta) >= 20 and len(tb) >= 20 and (ta in tb or tb in ta):
        return True
    return fuzz.ratio(ta, tb) >= TITLE_THRESHOLD


def diff(our_items: list[dict], oracle_items: list[dict]) -> dict:
    """For each oracle item: was it surfaced by us? For each of ours: is it new?"""
    recalled, matched_ours = [], set()
    for o in oracle_items:
        for i, our in enumerate(our_items):
            if i in matched_ours:
                continue
            if items_match(our, o):
                recalled.append({"oracle": o, "ours": our})
                matched_ours.add(i)
                break

    recalled_titles = {r["oracle"]["title"] for r in recalled}
    missed = [o for o in oracle_items if o["title"] not in recalled_titles]
    extras = [our_items[i] for i in range(len(our_items)) if i not in matched_ours]

    by_importance = {}
    for level in ("critical", "important", "worth-including"):
        total = sum(1 for o in oracle_items if o.get("importance") == level)
        rec = sum(1 for r in recalled if r["oracle"].get("importance") == level)
        by_importance[level] = (rec, total)

    return {
        "recalled": recalled,
        "missed": missed,
        "extras": extras,
        "by_importance": by_importance,
        "n_ours": len(our_items),
        "n_oracle": len(oracle_items),
    }


def format_report(name: str, d: dict) -> str:
    lines = [f"\n=== Eval: {name} ===",
             f"Our feed: {d['n_ours']} items   Oracle: {d['n_oracle']} items"]
    bi = d["by_importance"]
    lines.append("")
    lines.append(f"  Critical:        {bi['critical'][0]}/{bi['critical'][1]} recalled")
    lines.append(f"  Important:       {bi['important'][0]}/{bi['important'][1]} recalled")
    lines.append(f"  Worth-including: {bi['worth-including'][0]}/{bi['worth-including'][1]} recalled")
    total_r = len(d["recalled"])
    total_o = sum(t for _, t in bi.values())
    lines.append(f"  Total recall:    {total_r}/{total_o} = "
                 f"{(100*total_r/total_o if total_o else 0):.0f}%")
    lines.append(f"  Extras in ours:  {len(d['extras'])}  (not in oracle — may be valid finds)")

    if d["missed"]:
        lines.append("\nMISSED (in oracle, not in our feed):")
        for m in d["missed"]:
            lines.append(f"  [{m.get('importance','?'):16}] {m['title']}")
            lines.append(f"  {'':19}{m.get('url','')}")

    if d["extras"]:
        lines.append("\nEXTRAS (in our feed, not in oracle):")
        for e in d["extras"][:15]:
            lines.append(f"  {e.get('title','?')}")
            lines.append(f"      {e.get('url','')}")
        if len(d["extras"]) > 15:
            lines.append(f"  ... and {len(d['extras']) - 15} more")

    if d["recalled"]:
        lines.append("\nRECALLED:")
        for r in d["recalled"]:
            o = r["oracle"]
            lines.append(f"  [{o.get('importance','?'):16}] {o['title']}")

    return "\n".join(lines)
