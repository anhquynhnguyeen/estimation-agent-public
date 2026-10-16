"""Reads settings from the .env file in one place, so other files don't have to."""

import os
from pathlib import Path

from dotenv import load_dotenv

# Project folder (the one that contains app.py)
ROOT = Path(__file__).resolve().parent.parent

# Load secrets from .env into the environment
load_dotenv(ROOT / ".env")

# Where client files and the database live. Never uploaded to GitHub.
# On Dokploy this folder is a permanent volume, so data survives redeploys.
DATA_DIR = Path(os.getenv("DATA_DIR", ROOT / "data"))
CASES_DIR = DATA_DIR / "cases"
DB_PATH = DATA_DIR / "app.db"

# "synthetic" (fake data, safe anywhere) or "real" (only on Dokploy + LiteLLM)
DATA_MODE = os.getenv("DATA_MODE", "synthetic").strip().lower()

FRONT_API_TOKEN = os.getenv("FRONT_API_TOKEN", "").strip()
FRONT_INBOX_ID = os.getenv("FRONT_INBOX_ID", "").strip()

LITELLM_BASE_URL = os.getenv("LITELLM_BASE_URL", "").strip()
LITELLM_API_KEY = os.getenv("LITELLM_API_KEY", "").strip()
LLM_MODEL = os.getenv("LLM_MODEL", "").strip()


# ClickUp and Slack: only TEST destinations until the manager signs off
CLICKUP_API_TOKEN = os.getenv("CLICKUP_API_TOKEN", "").strip()
CLICKUP_TEST_LIST_ID = os.getenv("CLICKUP_TEST_LIST_ID", "").strip()
SLACK_BOT_TOKEN = os.getenv("SLACK_BOT_TOKEN", "").strip()
SLACK_TEST_CHANNEL = os.getenv("SLACK_TEST_CHANNEL", "").strip()   # channel ID (C...) of the test channel
SLACK_WORKSPACE_URL = os.getenv("SLACK_WORKSPACE_URL", "https://your-workspace.slack.com").strip().rstrip("/")


def ai_allowed() -> bool:
    """Manager's rule: real data may only go to AI through LiteLLM.
    We require LiteLLM in both modes, so AI never goes anywhere else by accident."""
    return bool(LITELLM_BASE_URL and LITELLM_API_KEY and LLM_MODEL)
