"""Constants. Tiny on purpose."""
from pathlib import Path

ROOT = Path(__file__).parent.parent
DATA = ROOT / "data"
DB_PATH = DATA / "feed.db"
PROFILE_PATH = DATA / "active_profile.json"

# Window for "today" in the prototype. In a real deployment, use date.today().
TODAY = "2026-05-18"

# Models — Sonnet for nuanced curation, Haiku for high-volume triage.
MODEL = "claude-sonnet-4-6"
TRIAGE_MODEL = "claude-haiku-4-5"

# Semantic Scholar — read from env if you want higher rate limit, else None.
# Get a free key at https://www.semanticscholar.org/product/api
import os
S2_API_KEY = os.environ.get("SEMANTIC_SCHOLAR_API_KEY")

# Fixed taxonomy for item categorization. Per-user weights live in the profile.
SUBFIELDS = [
    "mech_interp", "evals", "control", "alignment_training",
    "scalable_oversight", "deceptive_alignment", "agent_safety",
    "model_organisms", "governance", "strategy", "other",
]

# Cheap regex pre-filter for arXiv. Keeps any paper whose title OR abstract
# contains at least one of these. Intentionally permissive — the LLM triage
# stage does the smart cut. Edit freely to broaden coverage.
SAFETY_VOCAB = [
    "alignment", "alignment faking", "deceptive alignment",
    "interpret", "mechanistic interpret", "sparse autoencoder", "SAE",
    "feature steering", "activation patch",
    "AI safety", "safety evaluation", "safety training",
    "AI control", "untrusted monitor", "scalable oversight",
    "scheming", "sandbagging", "model organism", "deceptive AI",
    "RLHF", "constitutional", "reward hacking", "reward hacker",
    "red-team", "red team", "jailbreak", "adversarial robust",
    "dangerous capability", "capability elicitation", "evaluation awareness",
    "chain-of-thought monitor", "CoT monitor", "agentic misalignment",
    "responsible scaling", "frontier safety",
]


def load_dotenv() -> None:
    """Minimal .env loader so ANTHROPIC_API_KEY is available to the SDK."""
    import os
    env = ROOT / ".env"
    if not env.exists():
        return
    for line in env.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
