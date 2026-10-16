"""Real past cases vs the team's own estimate. The files are in data/samples/ (never in git), so this
test only runs on a machine that has them; elsewhere it is skipped.

Run:  .venv/bin/python tests/test_real_cases.py
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src import review  # noqa: E402
from src.estimation.engine import estimate, features_from_diff  # noqa: E402
from src.pdf.annotation_export import read_export  # noqa: E402
from src.pdf.diff import diff_documents  # noqa: E402

SAMPLES = ROOT / "data" / "samples"
# folder: (Original, Revised, annotation export folder, team estimate {PDF, CL & UI} in minutes)
CASES = {
    "example-capital-fund": ("Example Capital Fund - Subscription Booklet (old).pdf",
                             "Example Capital Fund - Subscription Booklet (updated).pdf",
                             "Example Capital Fund - Subscription Booklet - Version 8",
                             {"annotation": 30, "cl": 45},   # team estimate in the CS Slack thread
                             ["fw8ben.pdf", "fw9.pdf"]),     # IRS forms the client attached (embedded files)
}


def test_matches_team_estimate():
    for folder, (orig, rev, export, team, extra) in CASES.items():
        base = SAMPLES / folder
        if not (base / orig).exists():
            print(f"SKIP  {folder}: files not on this machine")
            continue
        exp = read_export(base / export)
        f = features_from_diff(diff_documents(base / orig, base / rev, exp), exp.total_fields, extra_pdfs=extra)
        assert f.added_files == extra
        t = review.rounded_times(estimate(f), f)
        assert (t["annotation"], t["cl"]) == (team["annotation"], team["cl"]), (folder, t, team)


if __name__ == "__main__":
    try:
        test_matches_team_estimate()
        print("PASS  test_matches_team_estimate")
    except AssertionError as e:
        print(f"FAIL  test_matches_team_estimate: {e}")
        sys.exit(1)
