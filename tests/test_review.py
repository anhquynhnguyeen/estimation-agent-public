"""Review & approve on synthetic case 1, with the pretend ClickUp/Slack and a throwaway database.

Run:  .venv/bin/python tests/test_review.py
"""

import os
import sys
import tempfile
from pathlib import Path

os.environ["DATA_DIR"] = tempfile.mkdtemp()  # before importing settings: never touch the real data/ folder
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src import db, review  # noqa: E402
from src.integrations.mocks import MockNotifier, MockTracker  # noqa: E402
from tests.test_diff_synthetic import V1, run_diff  # noqa: E402
from tests.test_estimate_synthetic import estimate, features  # noqa: E402

FUND = "Maple Ridge Growth Fund II"
EMAIL = {"issues": [{"n": 1, "status": "resolved", "confidence": 0.9, "quote_verified": True},
                    {"n": 4, "status": "open", "confidence": 0.9, "quote_verified": True}],
         "client_asked_question": True, "missing_requested_items": [], "summary": "Most issues fixed.",
         "confidence": 0.9}


def new_case(conn):
    return db.create_case(conn, "manual", "synthetic", subject="test", fund_name=FUND)


def test_attention_reasons():
    f = features({3, 4})
    reasons = review.attention_reasons(estimate(f), f, EMAIL, None, has_field_map=True)
    assert reasons == ["Logic change (p. 3, 4)", "Client asked a question"], reasons
    f0 = features()
    calm = dict(EMAIL, client_asked_question=False)
    assert review.attention_reasons(estimate(f0), f0, calm, None, has_field_map=True) == []


def test_rounded_times_and_slack_message():
    f = features({3, 4})
    t = review.rounded_times(estimate(f), f)
    assert (t["annotation"], t["cl"], t["ui_missing"]) == (30, 15, False), t
    details = review.change_details(run_diff(), {3, 4})
    msg = review.slack_message("https://doccom.example.com/review?job=abc", details, t)
    assert "<https://doccom.example.com/review?job=abc|Original and updated>" in msg
    assert 'Instructions for Subscribers: changed "5" to "10"' in msg
    assert "Part B - Investor Type" in msg and "(logic change)" in msg
    assert "PDF annotation: 30 mins" in msg and "CL and UI fix: 15 mins" in msg and "+ UI" not in msg
    assert "engineer" not in msg.lower()


def test_proposed_tasks():
    f = features({3, 4})
    e = estimate(f)
    tasks = review.propose_tasks(FUND, 1, e, f, review.rounded_times(e, f), "https://doccom.example.com/review?job=abc")
    assert [(t["task"], t["minutes"]) for t in tasks] == [("PDF Annotation", 30), ("CL & UI Fix", 15)]
    assert review.parent_title(FUND, " Items 2 to 5 ") == f"Update - {FUND} - Items 2 to 5"


def test_task_descriptions_have_link_then_details():
    f = features({3, 4})
    e = estimate(f)
    url = "https://doccom.example.com/review?job=abc"
    details = review.change_details(run_diff(), {3, 4})
    tasks = review.propose_tasks(FUND, 1, e, f, review.rounded_times(e, f), url, details)
    for t in tasks:
        d = t["description"]
        assert d.index(url) < d.index("The document has been updated with:") < d.index("Estimate (")
        assert '- Instructions for Subscribers: changed "5" to "10"' in d and "•" not in d
    edited = review.slack_message(url, details, review.rounded_times(e, f)).replace('"5" to "10"', '"5" to "15"')
    assert '"5" to "15"' in review.details_from_message(edited)            # reviewer's edit reaches ClickUp
    assert review.details_from_message("no details here") is None
    parent = review.task_description(url, details)
    assert parent.startswith("Comparison: [Original and updated](") and "Estimate (" not in parent


def test_copy_text_for_pasting():
    msg = "- <https://doccom.example.com/review?job=abc|Original and updated>"
    assert review.copy_text(msg) == "- Original and updated: https://doccom.example.com/review?job=abc"


def test_default_channel():
    assert review.default_channel("Maple Ridge Growth Fund II") == "cs-maple-ridge"


def test_approve_once_and_one_thread_per_fund():
    conn = db.connect()
    f = features({3, 4})
    e = estimate(f)
    tracker, notifier = MockTracker(), MockNotifier()
    first, second, other = new_case(conn), new_case(conn), new_case(conn)
    tasks = review.propose_tasks(FUND, first, e, f, review.rounded_times(e, f), None)
    tasks[1]["create"] = False  # reviewer unticks the CL and UI task
    try:
        review.approve(conn, first, FUND, " ", tasks, "hello", "cs-maple-ridge", e, "tester", tracker, notifier)
        assert False, "approve without an item should be refused"
    except ValueError:
        pass
    review.approve(conn, first, FUND, "Item 3", tasks, "hello", "cs-maple-ridge", e, "tester", tracker, notifier,
                   parent_fields=[{"id": "p", "value": 1}], subtask_fields=[{"id": "s", "value": 2}])
    parent, sub = tracker.tasks
    assert parent["title"] == f"Update - {FUND} - Item 3" and parent["parent_id"] is None
    assert parent["custom_fields"] == [{"id": "p", "value": 1}] and sub["custom_fields"] == [{"id": "s", "value": 2}]
    assert sub["title"] == f"Update - {FUND} - Item 3 - PDF Annotation" and sub["parent_id"] == "mock-task-1"
    assert notifier.posts[0]["thread_ts"] is None                                # first post starts the thread
    assert conn.execute("SELECT status FROM cases WHERE id = ?", (first,)).fetchone()["status"] == "approved"
    try:
        review.approve(conn, first, FUND, "Item 3", tasks, "hello", "cs-maple-ridge", e, "tester", tracker, notifier)
        assert False, "second approve should be refused"
    except RuntimeError:
        pass
    review.approve(conn, second, FUND, "Item 4", tasks, "again", "cs-maple-ridge", e, "tester", tracker, notifier)
    assert notifier.posts[1]["thread_ts"] == "mock-1"                            # same fund: same thread
    review.approve(conn, other, "Maple Ridge Arrow Fund I", "Item 1", tasks, "x", "cs-maple-ridge", e, "tester",
                   tracker, notifier)
    assert notifier.posts[2]["thread_ts"] is None                                # other fund: its own thread


def test_reject():
    conn = db.connect()
    case_id = new_case(conn)
    review.reject(conn, case_id, "wrong Revised file", "tester")
    assert conn.execute("SELECT status FROM cases WHERE id = ?", (case_id,)).fetchone()["status"] == "rejected"


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
