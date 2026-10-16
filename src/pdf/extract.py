"""Pull text lines (with positions) and field-like marks out of a PDF, page by page.

Plain code, no AI. Positions are in PDF points (1/72 inch), origin at the top-left.
"""

import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import pymupdf

# Glyphs some subdocs use as printed checkboxes
BOX_GLYPHS = "☐☑☒□■▢"
BLANK_RE = re.compile(r"_{4,}")  # "Name: ________" style blanks
# Page stamps that look like headings but are not section titles
STAMPS = {"confidential", "draft", "privileged and confidential", "strictly confidential", "execution version",
          "execution copy", "final", "for discussion purposes only"}


@dataclass
class Line:
    text: str
    x0: float
    y0: float
    x1: float
    y1: float
    font: str
    size: float

    @property
    def key(self) -> str:
        """Text used for matching lines between versions (spacing/case ignored)."""
        return " ".join(self.text.split()).lower()


@dataclass
class Mark:
    """Something on the page that looks like a place to fill in: a box, circle, blank line or widget."""
    kind: str  # "box", "circle", "underline", "glyph", "widget"
    x0: float
    y0: float
    x1: float
    y1: float


@dataclass
class Page:
    number: int  # starting at 1
    width: float
    height: float
    lines: list[Line] = field(default_factory=list)
    marks: list[Mark] = field(default_factory=list)
    heading: str | None = None   # a section title that starts on this page, if any
    section: str | None = None   # the section this page belongs to (from bookmarks or headings)

    @property
    def text(self) -> str:
        return "\n".join(l.text for l in self.lines)


def _lines(page) -> list[Line]:
    out = []
    for block in page.get_text("dict")["blocks"]:
        for ln in block.get("lines", []):
            spans = [s for s in ln["spans"] if s["text"].strip()]
            if not spans:
                continue
            text = " ".join(s["text"].strip() for s in spans)
            x0, y0, x1, y1 = ln["bbox"]
            out.append(Line(text, x0, y0, x1, y1, spans[0]["font"], round(spans[0]["size"], 1)))
    return sorted(out, key=lambda l: (round(l.y0), l.x0))


def _marks(page, lines: list[Line]) -> list[Mark]:
    marks = []
    for d in page.get_drawings():
        r = d["rect"]
        w, h = r.width, r.height
        kinds = {it[0] for it in d["items"]}
        if 6 <= w <= 16 and 6 <= h <= 16 and abs(w - h) < 2:  # small square: checkbox or radio
            marks.append(Mark("circle" if "c" in kinds else "box", r.x0, r.y0, r.x1, r.y1))
        elif h < 2 and w > 40 and kinds <= {"l"}:  # long thin line: text field underline
            marks.append(Mark("underline", r.x0, r.y0, r.x1, r.y1))
    for wdg in page.widgets() or []:  # real fillable PDF fields, if any
        r = wdg.rect
        marks.append(Mark("widget", r.x0, r.y0, r.x1, r.y1))
    for l in lines:
        if any(g in l.text for g in BOX_GLYPHS):
            marks.append(Mark("glyph", l.x0, l.y0, l.x0 + l.size, l.y1))
        if BLANK_RE.search(l.text):
            marks.append(Mark("underline", l.x0, l.y0, l.x1, l.y1))
    return sorted(marks, key=lambda m: (round(m.y0), m.x0))


def _assign_sections(pages: list[Page], toc: list) -> None:
    """Which section each page is in. Bookmarks win; otherwise a big heading near the top of a page
    starts a new section and pages without one continue the previous section."""
    # Running headers/footers (same text on most pages) are never section titles
    counts = Counter(l.key for p in pages for l in p.lines)
    running = {k for k, n in counts.items() if len(pages) > 2 and n >= 0.5 * len(pages)}
    sizes = Counter(l.size for p in pages for l in p.lines if l.key not in running)
    body = sizes.most_common(1)[0][0] if sizes else 10

    for p in pages:
        top = [l for l in p.lines if l.y0 < 0.3 * p.height and l.key not in running and not l.key.isdigit()
               and l.key.strip(" .:-") not in STAMPS]
        big = [l for l in top if l.size >= body + 2 or ("bold" in l.font.lower() and l.size > body)]
        if big:
            p.heading = max(big, key=lambda l: (l.size, -l.y0)).text

    starts = {}
    if toc:
        top_level = min(level for level, _, _ in toc)
        starts = {page: title for level, title, page in toc if level == top_level and page >= 1}
    current = None
    for p in pages:
        current = starts.get(p.number) or (None if starts else p.heading) or current
        p.section = current


def extract(pdf_path: Path) -> list[Page]:
    pages = []
    with pymupdf.open(pdf_path) as doc:
        for i, p in enumerate(doc):
            lines = _lines(p)
            pages.append(Page(i + 1, p.rect.width, p.rect.height, lines, _marks(p, lines)))
        _assign_sections(pages, doc.get_toc())
    return pages
