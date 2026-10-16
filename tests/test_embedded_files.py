"""Embedded files: consecutive pages without fields, split when the section changes.

Builds small fake PDFs on the fly (no client data). Run:  .venv/bin/python tests/test_embedded_files.py
"""

import sys
import tempfile
from pathlib import Path

import pymupdf

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.estimation.engine import features_from_diff  # noqa: E402
from src.pdf.diff import diff_documents  # noqa: E402

# (heading or None, body text, has a field)
PAGES = [
    ("Instructions", "Wire funds within {days} business days.", False),
    (None, "Send the booklet to the administrator {where}.", False),          # Instructions continued
    ("Subscription Agreement", "The Subscriber agrees to the {terms}.", False),
    (None, "Governing law: Delaware.", False),                                   # Agreement continued
    ("Part A - Investor Information", "Full legal name", True),
]
ORIGINAL = {"days": "5", "where": "by email", "terms": "terms"}
REVISED = {"days": "10", "where": "by courier", "terms": "updated terms"}


def make_pdf(path: Path, values: dict, headings_as_bookmarks: bool):
    doc = pymupdf.open()
    toc = []
    for n, (heading, body, has_field) in enumerate(PAGES, start=1):
        page = doc.new_page()
        page.insert_text((72, 40), "Test Fund - Subscription Booklet", fontsize=8)  # running header
        if heading:
            # With bookmarks, headings look like body text so only the bookmarks can tell sections apart
            page.insert_text((72, 90), heading, fontsize=10 if headings_as_bookmarks else 14, fontname="helv")
            toc.append([1, heading, n])
        page.insert_text((72, 130), body.format(**values), fontsize=10)
        if has_field:
            page.draw_line((300, 132), (540, 132), width=0.5)
        page.insert_text((300, 760), str(n), fontsize=8)
    if headings_as_bookmarks:
        doc.set_toc(toc)
    doc.save(path)


def embedded_files(headings_as_bookmarks: bool):
    with tempfile.TemporaryDirectory() as tmp:
        orig, rev = Path(tmp) / "original.pdf", Path(tmp) / "revised.pdf"
        make_pdf(orig, ORIGINAL, headings_as_bookmarks)
        make_pdf(rev, REVISED, headings_as_bookmarks)
        diff = diff_documents(orig, rev)
        sections = [p.section for p in diff.pages]
        return features_from_diff(diff, total_fields=1), sections


def test_split_by_heading():
    f, sections = embedded_files(headings_as_bookmarks=False)
    assert sections[:4] == ["Instructions", "Instructions", "Subscription Agreement", "Subscription Agreement"], sections
    assert f.swapped_pages == [1, 2, 3], f.swapped_pages
    assert f.embedded_files_swapped == 2, f.embedded_files_swapped   # not 1: two sections


def test_split_by_bookmarks():
    f, sections = embedded_files(headings_as_bookmarks=True)
    assert sections[2] == "Subscription Agreement", sections
    assert f.embedded_files_swapped == 2, f.embedded_files_swapped


if __name__ == "__main__":
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
