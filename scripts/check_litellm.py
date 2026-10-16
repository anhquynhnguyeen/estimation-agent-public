"""Check the connection to the company LiteLLM without sending any client data.

Run it yourself in a terminal:  .venv/bin/python scripts/check_litellm.py
It sends one tiny test message ("reply OK") and prints only whether it worked.
It never prints the URL, key or model name.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src import settings  # noqa: E402
from src.analysis.llm import AINotAllowed, LLMClient  # noqa: E402

print("LITELLM_BASE_URL set:", bool(settings.LITELLM_BASE_URL))
print("LITELLM_API_KEY set: ", bool(settings.LITELLM_API_KEY))
print("LLM_MODEL set:       ", bool(settings.LLM_MODEL))

try:
    reply = LLMClient().json("Reply only with a JSON object.", 'Reply with {"ok": true}')
    print("Connection works." if reply.get("ok") is True else "Connected, but the reply was unexpected.")
except AINotAllowed as e:
    print("Not connected:", e)
except Exception as e:  # show only the kind of error, not response bodies
    print("Not connected:", type(e).__name__, getattr(e, "status_code", ""))
