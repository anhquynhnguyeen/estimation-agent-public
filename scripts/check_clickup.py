"""Check the app's own ClickUp connection (read-only: it creates nothing).

Run it yourself in a terminal:  .venv/bin/python scripts/check_clickup.py
It never prints the token.
"""

import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src import settings  # noqa: E402

API = "https://api.clickup.com/api/v2"

print("CLICKUP_API_TOKEN set:   ", bool(settings.CLICKUP_API_TOKEN))
print("CLICKUP_TEST_LIST_ID set:", bool(settings.CLICKUP_TEST_LIST_ID))
if not settings.CLICKUP_API_TOKEN:
    sys.exit("Add CLICKUP_API_TOKEN to .env first.")

headers = {"Authorization": settings.CLICKUP_API_TOKEN}
user = requests.get(f"{API}/user", headers=headers, timeout=30)
if user.status_code != 200:
    sys.exit(f"Token not accepted by ClickUp (HTTP {user.status_code}).")
print("Token works, signed in as:", user.json()["user"].get("username"))

if settings.CLICKUP_TEST_LIST_ID:
    lst = requests.get(f"{API}/list/{settings.CLICKUP_TEST_LIST_ID}", headers=headers, timeout=30)
    if lst.status_code == 200:
        print("Test list found:", lst.json().get("name"), "(the app will only create tasks here)")
    else:
        print(f"Test list not reachable (HTTP {lst.status_code}). Check CLICKUP_TEST_LIST_ID.")
