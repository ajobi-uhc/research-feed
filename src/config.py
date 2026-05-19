"""Single source of truth for tunables. Paths, models, sources, knobs."""

from pathlib import Path
import os

# ─────── Paths ───────────────────────────────────────────────────────
ROOT = Path(__file__).parent.parent
DATA = ROOT / "data"
DB_PATH = DATA / "feed.db"
PROFILE_PATH = DATA / "profile.json"
QUERY_CACHE_PATH = DATA / "query_cache.json"

# Treat this date as "today" — prototype operates on a fixed reference.
TODAY = "2026-05-19"

# Fetch + ranker windows
WINDOW_DAYS = 60                  # fetch this many days back
DISPLAY_WINDOW_DAYS = 30          # show this many days in default views

# ─────── Models per LLM call ─────────────────────────────────────────
# Each LLM function looks up its model by role. Override via env.
MODELS = {
    "profile_drafter": os.getenv("MODEL_PROFILE_DRAFTER", "claude-sonnet-4-6"),
    "query_gen":       os.getenv("MODEL_QUERY_GEN",       "claude-haiku-4-5"),
    "cheap_filter":    os.getenv("MODEL_CHEAP_FILTER",    "claude-haiku-4-5"),
    "html_extract":    os.getenv("MODEL_HTML_EXTRACT",    "claude-haiku-4-5"),
    "ranker":          os.getenv("MODEL_RANKER",          "claude-sonnet-4-6"),
}

# ─────── Static source registry — hand-maintained ───────────────────
# Lab/org pages. AF and LW have their own dedicated fetcher.
# Each entry: slug, name, url (landing/research index), feed (RSS/Atom or None).
# `feed` set per source: a real RSS/Atom URL if confirmed, else None.
# When `feed` is None, the pipeline falls back to HTML+Haiku extraction.
# The feed values here are the ones the sources_health eval verified.
PULL_SOURCES = [
    # Anthropic family
    {"slug": "anthropic_alignment_science",
     "name": "Anthropic Alignment Science",
     "url":  "https://alignment.anthropic.com/",
     "feed": None},  # Distill site, no RSS — HTML fallback
    {"slug": "transformer_circuits",
     "name": "Transformer Circuits Thread",
     "url":  "https://transformer-circuits.pub/",
     "feed": None},
    {"slug": "anthropic_research",
     "name": "Anthropic Research",
     "url":  "https://www.anthropic.com/research",
     "feed": None},
    # Other frontier labs
    {"slug": "openai_safety",
     "name": "OpenAI Safety / Alignment",
     "url":  "https://openai.com/research/",
     "feed": None},
    {"slug": "deepmind_safety",
     "name": "Google DeepMind Safety",
     "url":  "https://deepmind.google/discover/blog/",
     "feed": None},
    # Eval / safety orgs
    {"slug": "metr",
     "name": "METR",
     "url":  "https://metr.org/blog",
     "feed": "https://metr.org/feed.xml"},
    {"slug": "apollo",
     "name": "Apollo Research",
     "url":  "https://www.apolloresearch.ai/research",
     "feed": None},
    {"slug": "uk_aisi",
     "name": "UK AI Security Institute",
     "url":  "https://www.aisi.gov.uk/work",
     "feed": None},
    {"slug": "us_aisi",
     "name": "US AISI / NIST CAISI",
     "url":  "https://www.nist.gov/aisi",
     "feed": None},
    {"slug": "redwood",
     "name": "Redwood Research",
     "url":  "https://blog.redwoodresearch.org/",
     "feed": "https://blog.redwoodresearch.org/feed"},
    # Interp orgs
    {"slug": "goodfire",
     "name": "Goodfire Research",
     "url":  "https://www.goodfire.ai/research",
     "feed": "https://www.goodfire.ai/research/rss.xml"},
    {"slug": "far_ai",
     "name": "FAR AI",
     "url":  "https://far.ai/publications",
     "feed": None},
    {"slug": "mats",
     "name": "MATS Program",
     "url":  "https://www.matsprogram.org/research",
     "feed": None},
    {"slug": "palisade",
     "name": "Palisade Research",
     "url":  "https://palisaderesearch.org/",
     "feed": None},
    {"slug": "truthful_ai",
     "name": "Truthful AI / Owain Evans",
     "url":  "https://truthfulai.com/",
     "feed": None},
    {"slug": "timaeus",
     "name": "Timaeus",
     "url":  "https://timaeus.co/research",
     "feed": None},
    {"slug": "gray_swan",
     "name": "Gray Swan AI",
     "url":  "https://www.grayswan.ai/research",
     "feed": None},
]

# Subfield taxonomy for item tagging (used by the ranker)
SUBFIELDS = [
    "mech_interp", "evals", "control", "alignment_training",
    "scalable_oversight", "deceptive_alignment", "agent_safety",
    "model_organisms", "governance", "strategy", "other",
]

# ─────── Knobs ───────────────────────────────────────────────────────
S2_API_KEY = os.getenv("SEMANTIC_SCHOLAR_API_KEY")

CHEAP_FILTER_KEEP_TARGET = 200
RANKER_BATCH_SIZE = 15
AF_MIN_KARMA = 25

# Buckets in priority order (used for sort + UI colors)
BUCKETS = ["core", "adjacent", "peripheral", "off-topic"]


# ─────── .env loader ─────────────────────────────────────────────────
def load_dotenv() -> None:
    env = ROOT / ".env"
    if not env.exists():
        return
    for line in env.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
