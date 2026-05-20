# Eval results — discovery + curation vs per-persona oracles

Window 2026-04-19 → 2026-05-19 (matches the oracle). Recall by importance. *Extras* (items we surface that aren't in the oracle) are **not** penalized — the oracle is a specific hand-curated list, not exhaustive, so extras are often valid finds.

`mode=pinned` scores discovery+curation on a fixed profile (the default — isolates the pipeline from onboarding variance); `mode=onboard` re-onboards end-to-end.

| persona | mode | recall | critical | important | worth | kept | extras |
|---|---|---|---|---|---|---|---|
| mara | onboard | 5/12 | 2/3 | 2/5 | — | 15 | 10 |
| david | onboard | 8/20 | 5/7 | 2/10 | — | 20 | 12 |
| priya | onboard | 1/17 | 0/4 | 1/8 | — | 17 | 16 |
| arya | onboard | 3/6 | 3/4 | 0/1 | — | 17 | 14 |

Per-persona artifacts under `evals/runs/full/<name>/`: `onboarded_profile.json`, `digest_*.json`, `diff.txt` (recalled/missed/extras), `agent.log` (full trace), `summary.json`. Load any persona live in the UI via “Explore a sample researcher.”
