"""Check the code around the AI steps, using a stand-in that returns hand-written answers.
This tests quote checking and wiring, NOT the AI's judgement (that needs LiteLLM).

Run:  .venv/bin/python tests/test_analysis_checks.py
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.analysis.change_classifier import classify_changes  # noqa: E402
from src.analysis.email_analyzer import analyze_email  # noqa: E402
from src.pdf.extract import extract  # noqa: E402
from tests.test_diff_synthetic import CASE, V1, V2, run_diff  # noqa: E402


class StandInLLM:
    """Returns a fixed answer instead of calling the AI."""
    def __init__(self, answer):
        self.answer, self.prompts = answer, []

    def json(self, system, user):
        self.prompts.append(user)
        return self.answer


def test_email_quotes_are_checked():
    reply = (CASE / "client_reply.txt").read_text()
    answer = {
        "issues": [
            {"n": 3, "status": "resolved", "quote": "The wire deadline is now 10 business days", "confidence": 0.95},
            {"n": 4, "status": "open", "quote": "We refuse to add a Title field", "confidence": 0.9},  # made up
            {"n": 5, "status": "maybe", "quote": "it is still a draft", "confidence": 0.8},  # bad status
        ],
        "client_questions": [{"quote": "is it really needed if the signatory is an individual?", "confidence": 0.9}],
        "confidence": 0.9,
    }
    r = analyze_email("our list", reply, ["Side Letter Template - DRAFT.pdf"], StandInLLM(answer))
    i3, i4, i5 = r["issues"]
    assert i3["quote_verified"] and i3["confidence"] == 0.95
    assert not i4["quote_verified"] and i4["confidence"] <= 0.3, i4      # quote not in the reply
    assert i5["status"] == "open" and i5["confidence"] <= 0.3, i5        # unknown status
    assert r["client_asked_question"] and r["confidence"] <= 0.3


def test_classifier_only_sends_flagged_pages():
    diff = run_diff()
    answer = {"pages": [
        {"page": 3, "logic_change": True, "confidence": 0.9,
         "quote": "A family office with at least $5,000,000 in assets under management"},
        {"page": 4, "logic_change": True, "confidence": 0.85, "quote": "or a U.S. tax resident"},
        {"page": 9, "logic_change": True, "confidence": 0.9, "quote": "x"},  # page that wasn't asked about
    ]}
    llm = StandInLLM(answer)
    r = classify_changes(diff, extract(V2), llm)
    sent = llm.prompts[0]
    assert '"page": 2' in sent and '"page": 3' in sent and '"page": 4' in sent and '"page": 1' not in sent
    assert r["logic_pages"] == [3, 4], r
    assert all(p["quote_verified"] for p in r["pages"])


if __name__ == "__main__":
    if not V1.exists():
        sys.exit("synthetic/case_01 not found: run scripts/make_synthetic_case.py first")
    failed = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS  {name}")
            except AssertionError as e:
                failed += 1
                print(f"FAIL  {name}: {e}")
    sys.exit(1 if failed else 0)
