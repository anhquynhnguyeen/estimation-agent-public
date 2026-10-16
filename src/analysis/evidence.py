"""Code checks on AI answers: every quote must really be in the source text."""

import re

UNVERIFIED_CONFIDENCE = 0.3  # an answer whose quote can't be found is not trusted


def _norm(text: str) -> str:
    text = text.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    return re.sub(r"\s+", " ", text).strip().lower()


def quote_found(quote: str | None, source: str) -> bool:
    if not quote or not quote.strip():
        return False
    q = _norm(quote).strip(" .\"'")
    return bool(q) and q in _norm(source)


def check_quotes(items: list[dict], source: str, quote_key: str = "quote") -> list[dict]:
    """Mark each item with `quote_verified`; lower the confidence of items whose quote isn't in the source."""
    for item in items:
        ok = quote_found(item.get(quote_key), source)
        item["quote_verified"] = ok
        if not ok:
            item["confidence"] = min(float(item.get("confidence") or 0), UNVERIFIED_CONFIDENCE)
    return items


def clamp_confidence(value) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return 0.0
