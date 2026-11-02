"""All settings in one place. Override with environment variables."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCE_DSN = os.getenv("MM_SOURCE_DSN", "dbname=energy_grants")
MODEL_DB = os.getenv("MM_MODEL_DB", str(ROOT / "model.db"))
PROVIDER = os.getenv("MM_PROVIDER", "mock")          # mock | bedrock | anthropic

# Confidence badge thresholds (also adjustable in the UI sidebar)
HIGH = float(os.getenv("MM_HIGH", "0.85"))
MEDIUM = float(os.getenv("MM_MEDIUM", "0.6"))

MIN_OVERLAP = float(os.getenv("MM_MIN_OVERLAP", "0.95"))   # relationship inference
MAX_ATTEMPTS = int(os.getenv("MM_MAX_ATTEMPTS", "3"))      # LLM tries per table

# Placeholder prices (USD per million tokens). Set to your model's real prices.
PRICE_IN = float(os.getenv("MM_PRICE_IN_PER_M", "3.0"))
PRICE_OUT = float(os.getenv("MM_PRICE_OUT_PER_M", "15.0"))


def band(conf: float, high: float | None = None, medium: float | None = None) -> str:
    high = HIGH if high is None else high
    medium = MEDIUM if medium is None else medium
    return "High" if conf >= high else "Medium" if conf >= medium else "Low"
