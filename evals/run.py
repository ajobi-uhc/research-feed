"""Eval runner. Run all evals or just one.

Usage:
    uv run python -m evals.run                 # all
    uv run python -m evals.run arxiv           # one
    uv run python -m evals.run arxiv dedup     # subset
"""

from __future__ import annotations
import asyncio
import sys

from . import arxiv, forum, sources, dedup
from ._common import TestResult

ALL = {
    "arxiv":   arxiv.run,
    "forum":   forum.run,
    "sources": sources.run,
    "dedup":   dedup.run,
}


async def main(names: list[str]):
    targets = names or list(ALL.keys())
    unknown = [n for n in targets if n not in ALL]
    if unknown:
        print(f"unknown test(s): {unknown}. available: {list(ALL.keys())}")
        sys.exit(2)

    print(f"running {len(targets)} eval(s): {targets}\n")
    results: list[TestResult] = []
    for name in targets:
        try:
            res = await ALL[name]()
        except Exception as e:
            res = TestResult(name)
            res.fail(f"runner crashed: {e!r}")
        res.print()
        results.append(res)

    failed = sum(1 for r in results if r.failures)
    warned = sum(1 for r in results if r.warnings and not r.failures)
    passed = len(results) - failed - warned
    print(f"\n{'='*60}")
    print(f"  {passed} passed   {warned} with warnings   {failed} failed")
    sys.exit(failed)


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1:]))
