"""Check the PDF diff against the answer key of synthetic case 1.

Run:  .venv/bin/python tests/test_diff_synthetic.py
(The case lives in synthetic/case_01/, which is not in git. Rebuild it with
 .venv/bin/python scripts/make_synthetic_case.py if it's missing.)
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.pdf.annotation_export import read_export  # noqa: E402
from src.pdf.diff import diff_documents  # noqa: E402

CASE = ROOT / "synthetic" / "case_01"
V1 = CASE / "Maple Ridge Growth Fund II - Subscription Booklet (old).pdf"
V2 = CASE / "Maple Ridge Growth Fund II - Subscription Booklet (updated).pdf"
# Field map of the synthetic Original, exported from our real annotation tool (fake data, safe to commit)
REAL_EXPORT = ROOT / "tests" / "fixtures" / "maple_ridge_export"


def run_diff(with_export=True):
    return diff_documents(V1, V2, read_export(REAL_EXPORT) if with_export else None)


def test_categories_match_answer_key():
    expected = {p["page"]: p["category"] for p in json.loads((CASE / "expected.json").read_text())["pages"]}
    for page in run_diff().pages:
        want = expected[page.page_v1]
        if want == 4:
            # Code can only see the wording change; the AI classifier decides if it affects logic
            assert page.category == 3 and page.needs_ai_check, (page.page_v1, page.category, page.reasons)
        else:
            assert page.category == want, (page.page_v1, page.category, want, page.reasons)


def test_page2_is_one_reflow():
    page2 = run_diff().pages[1]
    assert len(page2.reflows) == 1, page2.reflows
    assert page2.count("moved") == 5 and page2.count("reworded") == 0


def test_field_counts():
    expected = json.loads((CASE / "expected.json").read_text())
    pages = run_diff().pages
    assert sum(p.fields_added for p in pages) == expected["fields_added"]
    assert sum(p.count("reworded") for p in pages) == expected["fields_reworded"]
    # The Part C question wording changed too (the logic change on page 4)
    assert pages[3].count("reworded", containers=True) == 1


def test_running_header_reported_once():
    diff = run_diff()
    assert len(diff.repeated_changes) == 1
    assert diff.pages[4].category is None and diff.pages[5].category is None


def test_word_summary_is_short_evidence():
    change = run_diff().pages[0].text_changes[0]
    assert change.summary == '"5" -> "10"', change.summary


def test_works_without_export():
    """No annotation export: fields are guessed from boxes/blank lines. Same categories expected."""
    cats = [p.category for p in run_diff(with_export=False).pages]
    assert cats == [1, 2, 3, 3, None, None], cats


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
