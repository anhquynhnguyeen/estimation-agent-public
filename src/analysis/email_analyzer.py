"""AI step 1: read our issue email and the client's reply.

For each issue we raised: resolved / partial / open, with the client's own words as evidence.
Also: questions the client asked instead of acting, attachments vs what we asked for, changes the
client made that we didn't ask for, and logic changes the client asks for in the email.
"""

import json

from src.analysis.evidence import check_quotes, clamp_confidence
from src.analysis.llm import LLMClient

SYSTEM = """You help the Digitization team at Anduin. We found issues in a client's subscription document
and emailed them a numbered list. The client replied. Compare the reply with our list.

Rules:
- Use ONLY what the emails say. Never guess what is in attachments you cannot read.
- Every finding needs a short quote copied EXACTLY (word for word) from the client's reply, your reasoning,
  and a confidence between 0 and 1.
- status: "resolved" = the client says it is done; "partial" = done only in part, or a draft/placeholder sent;
  "open" = not done, refused, or the client asked a question instead.
- A "logic change" is anything that changes who may invest or which questions/forms apply
  (e.g. a new investor type, eligibility thresholds, new tax-form conditions). Wording-only fixes are not.

Reply with one JSON object:
{
  "issues": [{"n": <our issue number>, "issue": "<short>", "status": "resolved|partial|open",
              "quote": "<exact words from the reply>", "reasoning": "<why>", "confidence": <0-1>}],
  "client_questions": [{"quote": "<exact>", "about_issue": <n or null>, "confidence": <0-1>}],
  "attachments": [{"filename": "<name>", "matches_issue": <n or null>, "reasoning": "<why>"}],
  "missing_requested_items": [{"issue": <n>, "item": "<what we asked for that did not arrive>"}],
  "unrequested_changes": [{"quote": "<exact>", "reasoning": "<why it matters>", "confidence": <0-1>}],
  "logic_change_requests": [{"quote": "<exact>", "reasoning": "<which logic>", "confidence": <0-1>}],
  "summary": "<two sentences for the CS channel>",
  "confidence": <0-1, overall>
}"""


def analyze_email(our_email: str, client_reply: str, attachment_names: list[str],
                  llm: LLMClient | None = None) -> dict:
    llm = llm or LLMClient()
    user = (f"OUR EMAIL (the issue list):\n{our_email}\n\n"
            f"CLIENT'S REPLY:\n{client_reply}\n\n"
            f"ATTACHMENTS ON THE REPLY: {json.dumps(attachment_names)}")
    result = llm.json(SYSTEM, user)
    return check_email_result(result, client_reply)


def check_email_result(result: dict, client_reply: str) -> dict:
    """Code checks on the AI's answer (also used by tests with a hand-written answer)."""
    for key in ("issues", "client_questions", "unrequested_changes", "logic_change_requests"):
        items = result.get(key) or []
        for item in items:
            item["confidence"] = clamp_confidence(item.get("confidence"))
        result[key] = check_quotes(items, client_reply)
    for key in ("attachments", "missing_requested_items"):
        result[key] = result.get(key) or []
    for issue in result["issues"]:
        if issue.get("status") not in ("resolved", "partial", "open"):
            issue["status"] = "open"
            issue["confidence"] = min(issue["confidence"], 0.3)
    result["confidence"] = min([clamp_confidence(result.get("confidence"))]
                               + [i["confidence"] for i in result["issues"]])
    result["client_asked_question"] = bool(result["client_questions"])
    return result
