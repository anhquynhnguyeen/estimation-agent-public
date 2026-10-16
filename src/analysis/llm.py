"""The only way the app talks to AI: the company LiteLLM proxy (never a public AI API directly).

LiteLLM speaks the OpenAI format, so we use the `openai` library pointed at the company URL.
Model and address come from .env (LITELLM_BASE_URL, LITELLM_API_KEY, LLM_MODEL).
Nothing sent or received is printed or logged.
"""

import json

from openai import OpenAI, PermissionDeniedError

from src import settings


class AINotAllowed(RuntimeError):
    pass


class LLMClient:
    def __init__(self):
        if not settings.ai_allowed():
            raise AINotAllowed("AI is off: set LITELLM_BASE_URL, LITELLM_API_KEY and LLM_MODEL in .env "
                               "(company LiteLLM only).")
        self.model = settings.LLM_MODEL
        self._client = OpenAI(base_url=settings.LITELLM_BASE_URL, api_key=settings.LITELLM_API_KEY, timeout=120)

    def json(self, system: str, user: str, temperature: float = 0) -> dict:
        """Ask for a JSON object back and return it as a dict."""
        try:
            resp = self._client.chat.completions.create(
                model=self.model,
                temperature=temperature,
                response_format={"type": "json_object"},
                messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            )
        except PermissionDeniedError as e:
            if "cloudflare" in str(e).lower():
                raise AINotAllowed("Cloudflare blocked the call to LiteLLM before it arrived. Ask the LiteLLM "
                                   "admins how apps should connect (allowlist / service token).") from None
            raise
        text = resp.choices[0].message.content or ""
        try:
            return json.loads(_strip_fences(text))
        except json.JSONDecodeError as e:
            raise ValueError(f"The AI didn't return valid JSON ({e}).") from e


def _strip_fences(text: str) -> str:
    """Some models wrap JSON in ```json ... ``` even when asked not to."""
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else ""
        text = text.rsplit("```", 1)[0]
    return text
