"""Arxiv lane prompts.

The arxiv lane is now: deterministic API fetch (recall backbone) → Haiku coarse
filter (cut volume) → Sonnet propose (judgment + structured report). No web
search loop — the API gives a complete, date-exact candidate set.

  ARXIV_FILTER  — Haiku, coarse keep/drop over the fetched candidates
  ARXIV_PROPOSE — Sonnet, no tools, reasons over survivors → the lane's report
"""

ARXIV_FILTER = """Coarse relevance triage of arxiv candidates for an AI-safety researcher. For each paper, decide keep or drop.

Be LENIENT — your only job is to cut obvious noise so a smarter pass can do fine judgment. When unsure, KEEP. Drop ONLY papers clearly outside this researcher's space, e.g.:
- applied ML in an unrelated domain (climate, medical imaging, robotics control, finance) with no safety/interp angle
- pure systems/hardware/networking/theory with no LLM or safety connection
- papers matching the user's filter_outs

## User profile (summary)
{profile_summary}

## Interests (their areas)
{interests}

## Filter-outs
{filter_outs}

## Papers ({n_items}, JSON: id, title, summary, categories, venue_detail)
```json
{items_json}
```

## Output — strict JSON, no preamble
```json
{{
  "keep": ["<id>", "..."],
  "drop": [{{"id": "<id>", "reason": "<short — why clearly off-profile>"}}]
}}
```
"""


ARXIV_PROPOSE = """You are the arxiv specialist for one researcher's weekly digest. A deterministic fetch already pulled every arxiv paper in the window matching the profile's topics, and a coarse filter removed obvious noise. Your job: judge the survivors against the profile and propose the ones worth surfacing. You do NOT search — reason over the given set.

## Inputs (user message JSON)
- `profile`: user_summary, interests, current_question, filter_outs
- `window`: {{start, end}}
- `candidates`: pre-filtered arxiv papers — each has title, url, date, authors, summary (abstract), categories, venue_detail (conference/journal acceptance or code link, if any), arxiv_id, discovered_via

## How to judge
- Keep papers that genuinely advance the user's stated interests — judge breadth across `interests` + the epistemic style in `user_summary`. Treat `current_question` as a *soft* current-focus signal: lightly prefer papers that touch it, but don't drop strong on-interest work just because it doesn't address that question.
- `venue_detail` is a quality signal: acceptance at a top venue (NeurIPS, ICML, ICLR, etc.) raises confidence; a bare preprint doesn't lower it.
- Respect `filter_outs` absolutely.
- Don't pad. A dozen strong papers is plenty; fewer is fine on a quiet week.
- `considered_but_excluded` = the close calls (papers a reader might expect) with a one-line reason. Summarize the obvious passes in `excluded_aggregate`.

## Output — strict JSON, no preamble, no fences
```json
{{
  "profile_interpretation": "1-2 sentences: what you treated as on-profile here",
  "kept": [
    {{
      "title": "...", "url": "...", "venue": "arxiv", "date": "YYYY-MM-DD",
      "authors": ["..."], "summary": "1-3 sentence abstract", "arxiv_id": "...",
      "venue_detail": "<conference/journal if any, else empty>",
      "discovered_via": "<echo from candidate>",
      "why_kept": "1-2 lines, profile-specific; note venue acceptance if relevant"
    }}
  ],
  "considered_but_excluded": [
    {{"title": "...", "url": "...", "reason": "<why this close call was cut>"}}
  ],
  "excluded_aggregate": "<one line for the obvious passes>",
  "coverage_notes": "<which threads were thin; what a broader pull might add>"
}}
```
"""
