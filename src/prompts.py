"""All LLM prompts. Edit prose here, not in code.

Each prompt uses named placeholders; callers in llm.py do .format().
"""

# ─────────────────────────────────────────────────────────────────────
# PROFILE_DRAFTER
# Builds the user's Profile from concrete artifacts they paste at onboarding.
# Runs through the Claude Agent SDK because it needs WebFetch on seed papers.
# Writes JSON to the path given.
# ─────────────────────────────────────────────────────────────────────
PROFILE_DRAFTER = """You are drafting a research-discovery profile for an AI-safety researcher. The user has given you concrete inputs; your job is to read those inputs (including fetching the seed papers) and produce a profile grounded in artifacts, not guesses.

## User inputs

### Seed papers (URLs/titles they recently found valuable)
{seed_papers}

### Google Scholar URL (optional, may be empty)
{scholar_url}

### Authors they read whenever published (names, may be empty)
{followed_authors}

### A specific question or area they're tracking right now
{current_question}

### Things to filter out (may be empty)
{filter_outs}

## Required process

1. For each seed paper URL above, use WebFetch to load the page. Extract the title and abstract (or first paragraphs if it's a blog post).
2. If a Scholar URL is given, WebFetch it and skim recent publications + most-frequent coauthors.
3. Identify common themes across the seed papers — be specific. "Empirical SAE work focused on feature splitting" is better than "interpretability".
4. Identify the epistemic style implied: Anthropic-empirical interp vs theoretical-alignment, control vs eval, etc.
5. For each named followed author, note their affiliation if you can recall it.
6. Synthesize: write a one-paragraph user_summary capturing what this person cares about and the angle they prefer; produce a tag list (concrete phrases, useful for arxiv keyword search); preserve current_question and filter_outs as given.

## Output — write JSON to: {output_path}

Schema:
```json
{{
  "user_summary": "<one paragraph, specific about topics + style>",
  "tags": ["6-15 specific arxiv-searchable phrases"],
  "research_areas": ["3-6 short tags, broader than `tags`"],
  "current_question": "<echo the user's input, refined>",
  "filter_outs": [...],
  "followed_authors": [
    {{"name": "...", "affiliation": "<lab or null>", "why": "<1 line>"}}
  ],
  "notes": "<2-3 sentences explaining your choices — what themes you found, what you decided about the user's style>"
}}
```

After writing, final message: "Profile written." No other content.
"""


# ─────────────────────────────────────────────────────────────────────
# QUERY_GEN
# Produces ~5-10 Semantic Scholar search queries from the profile.
# Cached on profile.version_hash; regen only on profile change.
# Plain completion call, no tools.
# ─────────────────────────────────────────────────────────────────────
QUERY_GEN = """Given the user's research profile below, produce 5-10 Semantic Scholar search queries that would surface papers they care about.

## Profile
```json
{profile_json}
```

## Query guidelines

- Each query should be a specific phrase that appears in real abstracts. Avoid single words like "alignment" or "safety" (returns thousands of unrelated hits).
- Combine related concepts: "sparse autoencoder feature splitting" beats just "sparse autoencoder".
- Cover the profile's research_areas and tags with diverse angles — not just rephrasings of one idea.
- Don't include author names; those go through a separate lookup.
- 5-10 queries total.

## Output — strict JSON, no preamble

```json
{{
  "queries": ["...", "...", "..."]
}}
```
"""


# ─────────────────────────────────────────────────────────────────────
# CHEAP_FILTER
# Per discovery run: drop clearly off-topic items before the expensive ranker.
# Single Haiku call over the deduped candidate pool.
# Followed-author items skip this entirely (handled by the caller).
# ─────────────────────────────────────────────────────────────────────
CHEAP_FILTER = """You are a relevance triage filter for an AI-safety researcher's feed. For each item below, decide if it's plausibly relevant to AI safety / alignment / interpretability / model evaluations.

Be moderately STRICT. The user has stated interests (below); drop anything clearly outside the AI/ML/safety space. The downstream ranker does the precise judgment, but it shouldn't have to look at obvious junk.

## DROP without hesitation:
- Engineering / physics / biology / medicine / chemistry papers (even if "decentralized" or "federated" appears)
- Pure applications of ML to non-ML domains (wind turbine prognostics, medical imaging, signal processing)
- Generic distributed-systems / federated-learning papers with no AI-safety angle
- General security / cryptography papers unrelated to AI
- Hardware / systems / networking papers
- Math / theory papers with no ML connection
- Anything where the publication venue is clearly a non-AI domain (Energy Science, Power Systems, Bioinformatics, etc.) UNLESS the abstract clearly ties to AI safety

## KEEP (let the ranker decide):
- AI safety / alignment / interpretability papers, even if marginal
- LLM / agent / RLHF papers, even if they're capabilities-leaning
- Eval / red-teaming / control papers
- Papers from known safety labs (Anthropic, DeepMind safety, OpenAI, Redwood, METR, Apollo, AISI, Goodfire, FAR, MATS, Truthful AI, Timaeus, Gray Swan)
- Papers by a trusted author

## User profile (summary)
{profile_summary}

## Filter-outs (reject items matching these phrases)
{filter_outs}

## Items ({n_items})

```json
{items_json}
```

## Output — strict JSON, no preamble

```json
{{
  "keep": ["<item_id>", "<item_id>", ...],
  "drop": ["<item_id>", "<item_id>", ...],
  "notes": "<1 sentence about this batch — anything notable>"
}}
```

Aim to keep roughly {keep_target} items if there are at least that many candidates. Don't pad to hit the target — drop genuinely off-topic stuff.
"""


# ─────────────────────────────────────────────────────────────────────
# RANKER
# The expensive structured ranker. Batched, Sonnet-class.
# Receives profile + recent session feedback (relevant/not-relevant + whys)
# + a batch of items. Emits a relevance bucket + reasoning per item.
# ─────────────────────────────────────────────────────────────────────
# ─────────────────────────────────────────────────────────────────────
# HTML_EXTRACT
# Fallback for lab/org sources without RSS. Given the HTML of an index
# page, extract in-window items. Cheap Haiku call per feedless source.
# ─────────────────────────────────────────────────────────────────────
HTML_EXTRACT = """Extract recent research/blog items from this HTML page.

## Window: {window_start} to {window_end} (inclusive)

Only include items dated in this window. Skip product launches, hiring posts, generic announcements.

## Site

{site_name} — base URL: {base_url}

## HTML (truncated to {max_chars} chars)

```html
{html}
```

## Output

Strict JSON, no preamble, no fences:

```json
{{
  "items": [
    {{
      "title": "...",
      "url": "<absolute URL — if href is relative in the HTML, prepend the base URL>",
      "date": "YYYY-MM-DD",
      "authors": ["..."],
      "description": "<1 sentence, can be empty>"
    }}
  ]
}}
```

If you can't find any items in the window, return: `{{"items": []}}`. Don't invent.
"""


RANKER = """You are the relevance ranker for an AI-safety researcher's daily-ish feed. Classify each item in the batch into one of {{core, adjacent, peripheral, off-topic}} and explain why.

## User profile
```json
{profile_json}
```

## Items in this batch ({n_items})

```json
{items_json}
```

## How to judge

1. **Author match**: if any author is in `followed_authors`, that's a strong signal — usually `core` or `adjacent`, unless the topic is clearly outside their stated interests.
2. **Affiliation match**: papers from recognized safety labs (Anthropic, DeepMind safety, OpenAI safety, METR, Apollo, AISI, Redwood, Goodfire, FAR, MATS, etc.) get a quality boost.
3. **Topic match**: how well does the abstract overlap with profile.tags / research_areas / current_question?
4. **Publication venue**: top-tier ML venues (NeurIPS, ICML, ICLR, JMLR, TMLR, AAAI, COLT) raise confidence. Workshop or unrelated venues lower it.
5. **Activity signals**: `citation_count`, `af_karma`, `recent_comment_count` are date-resilience signals. An older paper with high citations or an older AF post with new comment activity is more relevant than its date suggests.
6. **Filter-outs**: if an item matches profile.filter_outs, mark it `off-topic`.
7. **Don't pad**: there's no quota for `core`. If only one item is genuinely core, only one is core.

## Be aggressive about `off-topic`

Mark an item `off-topic` (NOT `peripheral`) if any of these are true:
- The abstract is about a non-AI domain (engineering, physics, biology, medicine, materials, signal processing) even if it uses ML methods
- The paper is published in a non-AI venue (e.g. "Energy Science", "IEEE Power Systems", "Bioinformatics") AND the abstract has no clear safety/alignment/interp angle
- It's generic distributed-systems / federated-learning / privacy work with no AI-specific component
- The user's filter_outs list matches the topic

`peripheral` should be reserved for items that ARE in the AI/ML/safety space but only loosely touch the user's interests. Don't use `peripheral` as a "I'm not sure" bucket — if you're unsure, prefer `off-topic`. The feed only shows `core` + `adjacent` to the user; `peripheral` is essentially "discarded but logged for transparency".

## Output — strict JSON, no preamble

```json
{{
  "items": [
    {{
      "id": "<item_id>",
      "relevance": "core" | "adjacent" | "peripheral" | "off-topic",
      "reasons": ["<short signal>", "<short signal>"],
      "why": "<1-2 lines, specific to THIS user — name the signal>",
      "novelty": "new direction" | "incremental" | "survey"
    }}
  ],
  "notes": "<1 sentence about this batch>"
}}
```

`reasons` are short tags ("author Nanda matched", "abstract overlaps SAE focus"); `why` is a human-readable line that will be shown on the item card.
"""
