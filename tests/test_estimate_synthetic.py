"""Check the estimation engine on synthetic case 1.

Run:  .venv/bin/python tests/test_estimate_synthetic.py
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.estimation.engine import estimate, features_from_diff, load_playbook  # noqa: E402
from tests.test_diff_synthetic import REAL_EXPORT, V1, run_diff  # noqa: E402
from src.pdf.annotation_export import read_export  # noqa: E402


def features(logic_pages=frozenset()):
    return features_from_diff(run_diff(), read_export(REAL_EXPORT).total_fields, set(logic_pages))


def test_features():
    f = features()
    assert (f.fields_added, f.fields_reworded, f.embedded_files_swapped) == (1, 1, 1), f
    assert f.swapped_pages == [1] and f.field_pages_changed == [2, 3, 4], f


def test_estimate_without_logic():
    e = estimate(features())
    assert e.placement == "slight"
    assert not e.needs_engineer
    # 30 min baseline (uniform reflows: low end) + (2 fields + 1 file + 8 UI fixes) x 70s, no extra buffer
    assert (e.low_minutes, e.high_minutes) == (43, 43), (e.low_minutes, e.high_minutes)


def test_ui_fix_count():
    # one per field: page 2: 5 fields moved; page 3: IRA moved + family office added; page 4: 1 field reworded
    # (the reworded question wording is not a field)
    assert features().ui_items == 8, features().ui_items


def test_logic_pages_add_checklist_work():
    e = estimate(features({3, 4}))
    assert e.needs_engineer
    assert any(d.label == "Checklist logic changes" for d in e.drivers)


def test_extra_pdfs_are_embedded_files_under_cl_ui():
    from src import review
    f = features_from_diff(run_diff(), read_export(REAL_EXPORT).total_fields, extra_pdfs=["fw9.pdf", "fw8ben.pdf"])
    e = estimate(f)
    added = next(d for d in e.drivers if d.label == "Add embedded file(s)")
    assert added.low_s == 140 and "fw9.pdf, fw8ben.pdf" in added.why
    assert review.part_minutes(e, review.CL_UI_DRIVERS)[0] > review.part_minutes(estimate(features()), review.CL_UI_DRIVERS)[0]


def test_strict_notion_mode():
    pb = load_playbook() | {"logic_page_change_mode": "strict_notion"}
    e = estimate(features(), pb)
    carried = next(d for d in e.drivers if d.label.startswith("Carry over"))
    assert "16 fields x 17s" in carried.why, carried.why  # 17 fields - 1 reworded


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
