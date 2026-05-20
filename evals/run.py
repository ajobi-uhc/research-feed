"""Eval harness — score the discovery+curation pipeline against per-persona oracles.

Two modes:
  pinned (default)  load a fixed profile from fixtures/<name>_profile.json and run
                    discovery+curation on it. Fast, cheap, repeatable — the recall
                    number measures the pipeline, not onboarding luck.
  --onboard         run the onboarding agent from fixtures/<name>_input.json first,
                    then discovery. Use to evaluate onboarding end-to-end.

Usage:
  python -m evals.run                  # all personas, pinned
  python -m evals.run priya            # one persona
  python -m evals.run all --onboard    # end-to-end (re-onboards each)
  python -m evals.run mara --start 2026-04-19 --end 2026-05-19

Per-persona artifacts land in evals/runs/full/<name>/ (profile, digest, diff, log,
summary); a consolidated table is (re)written to evals/runs/full/RESULTS.md.
"""
from __future__ import annotations
import argparse
import asyncio
import json
import time
from pathlib import Path

from research_feed.agents.onboarding import create_profile
from research_feed.digest import generate_digest
from research_feed.models import Profile

from .match import diff, format_report

FIXTURES = Path(__file__).parent / "fixtures"
OUT = Path(__file__).parent / "runs" / "full"
ALL = ["mara", "david", "priya", "arya"]
WINDOW = ("2026-04-19", "2026-05-19")   # matches the oracle window


def _fixture(name: str, kind: str) -> dict:
    """kind ∈ {input, profile, oracle}."""
    return json.loads((FIXTURES / f"{name}_{kind}.json").read_text())


async def run_one(name: str, *, onboard: bool, ws: str, we: str) -> dict:
    pdir = OUT / name
    pdir.mkdir(parents=True, exist_ok=True)
    log_path = pdir / "agent.log"
    log_path.write_text(f"EVAL — {name} — {'onboard+' if onboard else 'pinned '}discovery — {ws}..{we}\n")
    t0 = time.time()
    try:
        if onboard:
            print(f"[{name}] onboarding…", flush=True)
            profile, _ = await create_profile(log_path=log_path, **_fixture(name, "input"))
        else:
            profile = Profile.from_dict(_fixture(name, "profile"))
        # Record the profile used (the sample loader reads this).
        (pdir / "onboarded_profile.json").write_text(json.dumps(profile.to_dict(), indent=2))

        print(f"[{name}] discovery {ws}..{we}…", flush=True)
        digest, _ = await generate_digest(profile, ws, we, persist=False,
                                            already_seen=[], run_registrar=False, log_path=log_path)
        (pdir / f"digest_{we}.json").write_text(json.dumps(digest.to_dict(), indent=2))

        oracle = _fixture(name, "oracle")["items"]
        d = diff([{"title": k.title, "url": k.url} for k in digest.kept], oracle)
        (pdir / "diff.txt").write_text(format_report(name, d))

        bi = d["by_importance"]
        summary = {
            "persona": name, "mode": "onboard" if onboard else "pinned",
            "sources": len(profile.sources), "interests": len(profile.interests),
            "authors": len(profile.authors), "kept": len(digest.kept),
            "oracle_total": d["n_oracle"], "recalled": len(d["recalled"]),
            "critical": _frac(bi["critical"]), "important": _frac(bi["important"]),
            "worth": _frac(bi["worth-including"]), "extras": len(d["extras"]),
            "elapsed_min": round((time.time() - t0) / 60, 1), "error": None,
        }
    except Exception as e:
        summary = {"persona": name, "error": repr(e), "elapsed_min": round((time.time() - t0) / 60, 1)}
        print(f"[{name}] ERROR: {e!r}", flush=True)
    (pdir / "summary.json").write_text(json.dumps(summary, indent=2))
    print(f"[{name}] done ({summary['elapsed_min']} min)", flush=True)
    return summary


def _frac(pair: tuple[int, int]) -> str:
    return f"{pair[0]}/{pair[1]}"


def write_results() -> str:
    """(Re)write RESULTS.md from every <persona>/summary.json on disk, so the table
    stays current even after running a single persona."""
    rows = [json.loads((OUT / n / "summary.json").read_text())
            for n in ALL if (OUT / n / "summary.json").exists()]
    out = [
        "# Eval results — discovery + curation vs per-persona oracles", "",
        f"Window {WINDOW[0]} → {WINDOW[1]} (matches the oracle). Recall by importance. "
        "*Extras* (items we surface that aren't in the oracle) are **not** penalized — the oracle "
        "is a specific hand-curated list, not exhaustive, so extras are often valid finds.", "",
        "`mode=pinned` scores discovery+curation on a fixed profile (the default — isolates the "
        "pipeline from onboarding variance); `mode=onboard` re-onboards end-to-end.", "",
        "| persona | mode | recall | critical | important | worth | kept | extras |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        if r.get("error"):
            out.append(f"| {r['persona']} | — | **ERROR** | | | | | |")
        else:
            out.append(f"| {r['persona']} | {r.get('mode', '?')} | {r['recalled']}/{r['oracle_total']} | "
                       f"{r['critical']} | {r['important']} | {r.get('worth', '—')} | {r['kept']} | {r['extras']} |")
    out += ["", "Per-persona artifacts under `evals/runs/full/<name>/`: `onboarded_profile.json`, "
            "`digest_*.json`, `diff.txt` (recalled/missed/extras), `agent.log` (full trace), "
            "`summary.json`. Load any persona live in the UI via “Explore a sample researcher.”", ""]
    text = "\n".join(out)
    (OUT / "RESULTS.md").write_text(text)
    return text


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("persona", nargs="?", default="all", help="persona name or 'all'")
    ap.add_argument("--onboard", action="store_true",
                    help="re-onboard from _input.json (end-to-end) instead of the pinned _profile.json")
    ap.add_argument("--start", default=WINDOW[0])
    ap.add_argument("--end", default=WINDOW[1])
    args = ap.parse_args()
    names = ALL if args.persona == "all" else [args.persona]

    print(f"Eval: {names} | {'onboard+' if args.onboard else 'pinned '}discovery | "
          f"{args.start}..{args.end}\n", flush=True)
    await asyncio.gather(*[run_one(n, onboard=args.onboard, ws=args.start, we=args.end) for n in names],
                         return_exceptions=True)
    print("\n" + "=" * 64 + "\n" + write_results())


if __name__ == "__main__":
    asyncio.run(main())
