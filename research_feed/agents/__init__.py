"""Agents package.

Each module is one agent (or agent-shaped helper). The orchestrator
(`research_feed/digest.py`) imports the `run_*_agent` functions and composes them.

  onboarding.create_profile   — builds the Profile (WebFetch on seeds)
  arxiv.run_arxiv_agent       — papers: OpenAlex fetch → Haiku filter → Sonnet propose
  forum.run_forum_agent       — AF/LW: LW GraphQL fetch → Sonnet propose
  sources.run_sources_agent   — lab/org blogs: WebFetch/WebSearch agent (no common API)
  curator.run_curator_agent   — Opus, final synthesis over deduped candidates
  registrar.run_registrar_agent — proposes durable profile/registry updates

  runner.run_agent            — shared claude-agent-sdk invocation helper
"""
