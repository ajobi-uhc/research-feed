# Research Feed

## What this is

Research Feed is a personalized research-discovery tool for AI-safety researchers who are too busy to track every channel work shows up on — arXiv, the Alignment Forum / LessWrong, and the dozens of lab and org blogs. You invest some time up front building a profile; after that you open it each morning (or every few days) to read a fresh **digest** of the most important new work in *your* subfield, and trust that you're not missing things.

It optimizes for four things, **in order of importance**:

1. **Don't miss important work** in your subfield.
2. **No slop** — surface relevant, substantive work and leave the noise out.
3. **Transparent** — visible reasoning, traceable decisions, and clear source coverage.
4. **Adapts to you** — a steerable profile that learns from your feedback.

**Who it's for:** a researcher who keeps up by word of mouth, occasional Twitter, and skimming LessWrong, but is overwhelmed by how spread out research is. The usage pattern is reading a daily/weekly digest; in production, digests regenerate on a schedule so you never wait on them.

## How to run it

**Prerequisites:** [`uv`](https://docs.astral.sh/uv/) and Node 18+ (with npm). Create your `.env` from the template and add your Anthropic API key:

```sh
cp .env.example .env   # then edit it and set ANTHROPIC_API_KEY
```

Start the backend (FastAPI, port 8000) and frontend (React + Vite) in two terminals:

```sh
uv run uvicorn research_feed.app:app --reload      # backend
cd web && npm install && npm run dev               # frontend
```

Open **http://localhost:5173**. Then:

- **See a sample feed instantly** — on the home screen, click one of the preset researcher profiles under *"Explore a sample researcher."* These load a ready-made profile and feed drawn from the eval personas — the fastest way to see what the tool produces without onboarding.
- **Build your own** — the first visit sends you to onboarding automatically (or go to the **Profile** tab → **Re-run onboarding**). It builds your profile and generates your first feed.

**Evals** (recall vs. hand-curated oracles, per persona):

```sh
uv run python -m evals.run               # all personas
uv run python -m evals.run all --onboard # re-onboard end-to-end
```

## How it works

Two phases: a one-time **onboarding** that builds your profile, and repeated **discovery runs** that generate feeds (these run on a schedule in production, so you never wait on them).

```
   YOUR INPUTS                ┌──────────────────────────────────────────────┐
   seed papers,         ───▶  │  ONBOARDING AGENT   (Sonnet + web search)     │
   followed authors,         │  reads your seeds, finds the labs & authors   │
   current question,         │  you care about, drafts a profile             │
   filter-outs               └───────────────────────┬──────────────────────┘
                                                      ▼
                                          ┌───────────────────────┐
                          you can edit ──▶│       PROFILE          │
                          feedback   ────▶│  interests · authors · │
                          updates it      │  sources · filter-outs │
                                          └───────────┬───────────┘
                                                      │   a discovery run (async / cron)
              ┌───────────────────────────────────────┼───────────────────────────────────────┐
              ▼                                        ▼                                        ▼
     ┌──────────────────┐                    ┌──────────────────┐                    ┌──────────────────┐
     │   PAPERS lane    │                    │   FORUM lane     │                    │   SOURCES lane   │
     │  OpenAlex fetch  │                    │  LW / AF GraphQL │                    │  lab & org blogs │
     │  → Haiku filter  │                    │  → Sonnet judge  │                    │  agent + web     │
     │  → Sonnet judge  │                    │                  │                    │  search          │
     └────────┬─────────┘                    └────────┬─────────┘                    └────────┬─────────┘
              └───────────────────────────────────────┼───────────────────────────────────────┘
                                                       ▼
                                       ┌───────────────────────────────┐
                                       │  dedupe across lanes + tag      │
                                       │  followed authors (a boost)     │
                                       └───────────────┬────────────────┘
                                                       ▼
                                       ┌───────────────────────────────┐         ┌──────────────────┐
                                       │     CURATOR   (Opus 4.7)        │         │    REGISTRAR     │
                                       │  rank best-first, write a       │────────▶│  proposes new    │
                                       │  one-line "why" per item,       │         │  sources/authors │
                                       │  drop slop, log every rejection │         │  (you approve)   │
                                       └───────────────┬────────────────┘         └──────────────────┘
                                                       ▼
                                       ┌───────────────────────────────┐
                                       │   DIGEST  =  your feed          │
                                       │  ranked items + why + rejection │
                                       │  ledger + source coverage       │
                                       └───────────────────────────────┘
```

- **Onboarding** — from your seed papers, followed authors, current question, and filter-outs, a Sonnet agent (with web search) drafts your profile.
- **Discovery** — three lanes run in parallel: **papers** (OpenAlex → Haiku filter → Sonnet), **forum** (LessWrong/AF GraphQL → Sonnet), and **sources** (a web-search agent over lab/org blogs). Deterministic fetch handles recall; the models handle relevance. Results are deduped, work by followed authors is boosted, and the **curator** (Opus 4.7) ranks everything best-first, writes a one-line "why" per item, and drops the rest into a rejection ledger — that's your feed.
- **Adapts & traces** — a **registrar** proposes profile/source updates after each run for you to accept or reject, and every run's full agent trace (prompts, tool calls, reasoning) is saved to a local SQLite DB so any "why wasn't X surfaced?" is answerable after the fact.
