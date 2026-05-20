# Evals

Compares our pipeline's output against a held-out **oracle feed** (the "ideal"
weekly briefing for a given profile, produced by an external deep-research agent).

## Fixtures

For each test profile (Mara, David, Priya, Arya):

- `fixtures/<name>_profile.json` — the Profile we feed into the pipeline
- `fixtures/<name>_oracle.json`  — the ground-truth items (with importance labels)

## How to run

```bash
# One profile (smoke test — costs $5–15, ~15 min)
uv run python -m evals.run mara

# All four (full sweep — $20–60, ~1 hour)
uv run python -m evals.run all

# Custom window
uv run python -m evals.run arya --start 2026-04-19 --end 2026-05-19
```

Output:
- `evals/runs/<name>_<end>.json` — the full digest our pipeline produced
- stdout — diff report (recall by importance, missed items, extras, recalled)

## Metric

We report **recall by importance level**:
- `critical`: items the oracle flagged as "user would consider it a real miss"
- `important`: clearly worth their time
- `worth-including`: relevant but lower priority

The most important signal is **critical recall**. A pipeline that misses a
"critical" item is more broken than one that misses three marginal ones.

Items match by:
1. Normalized URL equality, OR
2. Normalized title equality, OR
3. Fuzzy title similarity ≥ 82 (rapidfuzz)
