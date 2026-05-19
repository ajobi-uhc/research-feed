"""Synthetic test: dedup() correctly clusters cross-posts.

Hits no network. Builds three known cross-post groups + some singletons and
verifies the merge produces the expected number of cards.
"""

from __future__ import annotations
import asyncio

from src import agents
from src.models import Item
from ._common import TestResult, load_profile, check


async def run() -> TestResult:
    r = TestResult("dedup: cross-post merging")
    profile = load_profile("profile_mech_interp")

    items = [
        # Group A: same paper on transformer-circuits + AF
        Item(id="a1", title="Natural Language Autoencoders Produce Unsupervised Explanations of LLM Activations",
             url="https://transformer-circuits.pub/2026/nla/index.html",
             venue="transformer_circuits", date="2026-05-07",
             authors=["Kit Fraser-Taliente", "Subhash Kantamneni", "Sam Marks"],
             description=""),
        Item(id="a2", title="Natural Language Autoencoders Produce Unsupervised Explanations of LLM Activations",
             url="https://www.alignmentforum.org/posts/abc/natural-language-autoencoders",
             venue="alignment_forum", date="2026-05-07",
             authors=["Subhash Kantamneni", "Sam Marks"],
             description="", af_karma=211, af_comments=30),

        # Group B: title similar enough + shared author → should merge
        Item(id="b1", title="Sleeper Agent Backdoor Results Are Messy",
             url="https://example.com/sleeper",
             venue="alignment_forum", date="2026-04-28",
             authors=["Sebastian Prasanna", "Vivek Hebbar"],
             description="", af_karma=81),
        Item(id="b2", title="Sleeper Agent Backdoor Results are Messy.",  # punctuation/case diff
             url="https://arxiv.org/abs/2604.xxxxx",
             venue="arxiv_standalone", date="2026-04-29",
             authors=["Sebastian Prasanna"],
             description="", arxiv_id="2604.xxxxx"),

        # Singletons
        Item(id="c1", title="Introspection Adapters",
             url="https://alignment.anthropic.com/2026/introspection-adapters/",
             venue="anthropic_alignment_science", date="2026-04-28",
             authors=["Jack Lindsey", "Sam Marks"],
             description=""),
        Item(id="d1", title="Risk from fitness-seeking AIs",
             url="https://blog.redwoodresearch.org/p/risk-from-fitness-seeking-ais",
             venue="redwood", date="2026-05-01",
             authors=["Alex Mallen"],
             description="", af_karma=99),
    ]

    print(f"  input: {len(items)} items, expecting 4 clusters")
    merged = agents.dedup(items, profile)
    print(f"  output: {len(merged)} merged items")

    check(r, len(merged) == 4, "dedup produced 4 clusters as expected",
          f"got {len(merged)} clusters (expected 4)")

    # Group A: the transformer_circuits version should be primary (trust 50)
    nla = next((m for m in merged if "Autoencoders" in m.title), None)
    if nla:
        check(r, nla.venue == "transformer_circuits",
              "NLA cluster's primary venue is transformer_circuits",
              f"NLA primary venue is {nla.venue}, expected transformer_circuits")
        check(r, len(nla.extra_venues) == 1,
              "NLA has 1 extra_venue (AF version)",
              f"NLA extra_venues = {nla.extra_venues}")
        check(r, nla.af_karma == 211,
              "AF karma bubbled up from the cross-post",
              f"NLA af_karma = {nla.af_karma}, expected 211")
    else:
        r.fail("NLA cluster missing")

    # Group B: arxiv_id should bubble up
    sleeper = next((m for m in merged if "Sleeper" in m.title), None)
    if sleeper:
        check(r, sleeper.arxiv_id == "2604.xxxxx",
              "arxiv_id bubbled up to cluster primary",
              f"sleeper.arxiv_id = {sleeper.arxiv_id}")
    else:
        r.fail("Sleeper Agent cluster missing")

    # Singletons should have no extra_venues
    intros = next((m for m in merged if m.title == "Introspection Adapters"), None)
    if intros:
        check(r, not intros.extra_venues,
              "singleton has no extra_venues",
              f"singleton has extra_venues: {intros.extra_venues}")

    return r


if __name__ == "__main__":
    res = asyncio.run(run())
    res.print()
