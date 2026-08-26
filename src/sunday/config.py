import os
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[2]
ENV_PATH = REPO_ROOT / ".env"

load_dotenv(ENV_PATH)

DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
# DeepSeek's Anthropic-compatible endpoint (Messages API format, not OpenAI's).
DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com/anthropic")
DEEPSEEK_MODEL_PRO = os.getenv("DEEPSEEK_MODEL_PRO", "deepseek-v4-pro")
DEEPSEEK_MODEL_FLASH = os.getenv("DEEPSEEK_MODEL_FLASH", "deepseek-v4-flash")


def require_deepseek_key() -> None:
    if not DEEPSEEK_API_KEY:
        raise RuntimeError(
            f"DEEPSEEK_API_KEY not set. Fill it in at {ENV_PATH} "
            "(copy .env.example there first)."
        )
