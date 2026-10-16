"""AI step 2: for pages the comparison flagged, decide if the wording change affects logic (category 4).

Code already decided categories 1-3 and found the exact changes; the AI only judges meaning.
Each answer quotes the changed text (checked by code against the page) with reasoning and confidence.
"""

import json

from src.analysis.evidence import check_quotes, clamp_confidence
from src.analysis.llm import LLMClient
from src.pdf.diff import DocDiff
from src.pdf.extract import Page

SYSTEM = """You help the Digitization team at Anduin judge changes between two versions of a fund
subscription document (Original and Revised). Code has already found the exact text changes per page.

For each page decide if the change affects LOGIC, meaning the rules of the online form: who may invest,
investor types, eligibility criteria or thresholds, which tax forms apply and when, which questions are shown
(gating), or signature requirements. Pure wording, typos, formatting, or instructions that change nothing
the investor must answer are NOT logic changes.

Reply with one JSON object:
{"pages": [{"page": <number>, "logic_change": true|false,
            "quote": "<the changed wording, copied EXACTLY from the Revised page text>",
            "reasoning": "<which rule changes, or why none>", "confidence": <0-1>}]}"""


def classify_changes(diff: DocDiff, revised_pages: list[Page], llm: LLMClient | None = None) -> dict:
    flagged = [p for p in diff.pages if p.needs_ai_check and p.page_v2]
    if not flagged:
        return {"pages": []}
    text_by_page = {p.number: p.text for p in revised_pages}
    payload = []
    for p in flagged:
        payload.append({
            "page": p.page_v1 or p.page_v2,
            "changes": [{"kind": t.kind, "original": t.old, "revised": t.new} for t in p.text_changes],
            "field_or_question_wording_changed": [
                {"is": "question/group" if h.of_container else "field", "original": h.text, "revised": h.new_text}
                for h in p.highlights if h.status in ("reworded", "removed")],
            "fields_added": p.fields_added,
            "revised_page_text": text_by_page.get(p.page_v2, ""),
        })
    llm = llm or LLMClient()
    result = llm.json(SYSTEM, "PAGES TO JUDGE:\n" + json.dumps(payload, ensure_ascii=False, indent=1))
    return check_classification(result, {p.page_v1 or p.page_v2: text_by_page.get(p.page_v2, "") for p in flagged})


def check_classification(result: dict, page_texts: dict[int, str]) -> dict:
    """Code checks: only known pages, quotes must be on that Revised page."""
    pages = []
    for item in result.get("pages") or []:
        try:
            page = int(item.get("page"))
        except (TypeError, ValueError):
            continue
        if page not in page_texts:
            continue
        item["page"] = page
        item["logic_change"] = bool(item.get("logic_change"))
        item["confidence"] = clamp_confidence(item.get("confidence"))
        pages.append(check_quotes([item], page_texts[page])[0])
    return {"pages": pages, "logic_pages": sorted(p["page"] for p in pages if p["logic_change"])}
