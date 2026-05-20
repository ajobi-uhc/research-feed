"""Env vars and paths. Tiny on purpose."""
import os
from pathlib import Path

ROOT = Path(__file__).parent.parent
DATA = ROOT / "data"
DATA.mkdir(exist_ok=True)

DB_PATH = DATA / "feed.db"
PROFILE_PATH = DATA / "profile.json"   # mirrored to disk so it's easy to inspect

# Models
MODEL_HAIKU = "claude-haiku-4-5"
MODEL_SONNET = "claude-sonnet-4-6"
MODEL_OPUS = "claude-opus-4-7"


def load_dotenv() -> None:
    """Minimal .env loader. Called once at import time."""
    env = ROOT / ".env"
    if not env.exists():
        return
    for line in env.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


load_dotenv()

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
# OpenAlex needs no key — a mailto just puts us in the faster "polite pool".
OPENALEX_MAILTO = os.environ.get("OPENALEX_MAILTO", "aryajakkli2002@gmail.com")
