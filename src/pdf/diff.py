"""Compare v1 and v2 of a subdoc, page by page. Plain code, no AI.

For each page it reports:
- text inserted / deleted / replaced (quoted, as evidence)
- reflows: a run of lines that all moved by the same vertical offset counts as ONE reflow
- each v1 text highlight (from the annotation export): unchanged / moved / reworded / removed.
  Highlights are the main signal for field changes; fields are what we count for effort.
- fields added / removed (field-like marks: boxes, circles, blank lines)
- a suggested category (1-3). Category 4 (logic) is a judgment call: pages that may need it are
  flagged `needs_ai_check` for the change classifier.
"""

from collections import Counter
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from pathlib import Path

from src.pdf.annotation_export import AnnotationExport, Highlight
from src.pdf.extract import Line, Page, extract

MOVE_TOLERANCE = 1.5   # points; smaller vertical moves count as "not moved"
ROW_TOLERANCE = 8      # points; a mark this close to a line's middle is on the same row


@dataclass
class TextChange:
    kind: str                 # "inserted", "deleted", "replaced"
    old: str | None
    new: str | None
    y: float                  # vertical position (v2 for inserted/replaced, v1 for deleted)
    new_field: bool = False   # an inserted line with a new checkbox/blank on its row
    on_field_row: bool = False  # a replaced line with a checkbox/blank on its row (used without an export)
    summary: str = ""         # word-level difference, e.g. '"5" -> "10"'


@dataclass
class Reflow:
    from_y: float   # v1 position of the first line that moved
    offset: float   # how far everything moved (points, + = down)
    lines: int      # how many lines moved together


@dataclass
class HighlightResult:
    text: str
    field_id: str | None
    of_container: bool        # wording of a group/question rather than of one field
    status: str               # "unchanged", "moved", "reworded", "removed", "not_found"
    offset: float = 0.0
    new_text: str | None = None


@dataclass
class PageDiff:
    page_v1: int | None
    page_v2: int | None
    v1_fields: int
    section: str | None = None    # section of the Original page (embedded files never cross sections)
    text_changes: list[TextChange] = field(default_factory=list)
    reflows: list[Reflow] = field(default_factory=list)
    highlights: list[HighlightResult] = field(default_factory=list)
    fields_added: int = 0
    fields_removed: int = 0
    size_changed: bool = False
    font_changed: bool = False
    category: int | None = None
    needs_ai_check: bool = False
    reasons: list[str] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return self.category is not None

    def count(self, status: str, containers: bool = False) -> int:
        """Highlights of fillable fields with this status (or of group/question wordings)."""
        return sum(h.status == status and h.of_container == containers for h in self.highlights)


@dataclass
class DocDiff:
    pages: list[PageDiff]
    repeated_changes: list[tuple[str, str]]  # e.g. a footer "Version 7" -> "Version 8" on every page

    @property
    def changed_pages(self) -> list[PageDiff]:
        return [p for p in self.pages if p.changed]


# ---------- Matching pages and lines ----------

def _pair_pages(v1: list[Page], v2: list[Page]) -> list[tuple[Page | None, Page | None]]:
    """Same page count: pair 1-1, 2-2, ... Otherwise line pages up by their text."""
    if len(v1) == len(v2):
        return list(zip(v1, v2))
    sm = SequenceMatcher(None, [_fingerprint(p) for p in v1], [_fingerprint(p) for p in v2], autojunk=False)
    pairs = []
    for op, a0, a1, b0, b1 in sm.get_opcodes():
        if op == "equal" or (op == "replace" and a1 - a0 == b1 - b0):
            pairs += list(zip(v1[a0:a1], v2[b0:b1]))
        else:
            pairs += [(p, None) for p in v1[a0:a1]] + [(None, p) for p in v2[b0:b1]]
    return pairs


def _fingerprint(page: Page) -> str:
    """The first real lines of a page (skipping a running header), used to find the same page."""
    keys = [l.key for l in page.lines]
    return " | ".join(keys[1:4]) if len(keys) > 3 else " | ".join(keys)


def _align(a: list[Line], b: list[Line]):
    return SequenceMatcher(None, [l.key for l in a], [l.key for l in b], autojunk=False).get_opcodes()


# ---------- Per-page analysis ----------

def _reflows(moves: list[tuple[Line, float]]) -> list[Reflow]:
    """Group unchanged lines (in page order) into runs that moved by the same offset."""
    runs: list[Reflow] = []
    for line, dy in moves:
        if runs and abs(runs[-1].offset - dy) <= MOVE_TOLERANCE:
            runs[-1].lines += 1
        else:
            runs.append(Reflow(line.y0, dy, 1))
    return [r for r in runs if abs(r.offset) > MOVE_TOLERANCE]


def _highlight_box(h: Highlight, page: Page) -> tuple[float, float, float, float]:
    """Export areas are fractions of the page; older synthetic exports used points."""
    a = h.area
    vals = [a.get(k, 0) for k in ("left", "top", "right", "bottom")]
    if max(vals) <= 1.5:
        return vals[0] * page.width, vals[1] * page.height, vals[2] * page.width, vals[3] * page.height
    return tuple(vals)


def _lines_in_box(h: Highlight, page: Page) -> list[int]:
    x0, y0, x1, y1 = _highlight_box(h, page)
    hits = []
    for i, l in enumerate(page.lines):
        overlap_y = min(y1, l.y1) - max(y0, l.y0)
        overlap_x = min(x1, l.x1) - max(x0, l.x0)
        if overlap_y > 0.5 * (l.y1 - l.y0) and overlap_x > 0:
            hits.append(i)
    if not hits and h.text:  # box drawn loosely: fall back to finding the wording
        key = " ".join(h.text.split()).lower()
        hits = [i for i, l in enumerate(page.lines) if key[:40] in l.key or l.key in key]
    return hits


def _check_highlights(highlights: list[Highlight], p1: Page, p2: Page,
                      fate: dict[int, tuple[str, int | None]],
                      changes: list[TextChange]) -> list[HighlightResult]:
    """`fate` maps each v1 line to ("equal", v2 line), ("replaced", change index) or ("deleted", None)."""
    results = []
    for h in highlights:
        idx = _lines_in_box(h, p1)
        text = h.text or " ".join(p1.lines[i].text for i in idx)
        if not idx:
            results.append(HighlightResult(text, h.field_id, h.of_container, "not_found"))
            continue
        kinds = [fate[i][0] for i in idx]
        if "replaced" in kinds:
            changed = sorted({fate[i][1] for i in idx if fate[i][0] == "replaced"})
            new = " ".join(changes[c].new for c in changed)
            results.append(HighlightResult(text, h.field_id, h.of_container, "reworded", new_text=new))
        elif all(k == "deleted" for k in kinds):
            results.append(HighlightResult(text, h.field_id, h.of_container, "removed"))
        else:
            dys = [p2.lines[fate[i][1]].y0 - p1.lines[i].y0 for i in idx if fate[i][0] == "equal"]
            dy = max(dys, key=abs) if dys else 0.0
            status = "moved" if abs(dy) > MOVE_TOLERANCE else "unchanged"
            results.append(HighlightResult(text, h.field_id, h.of_container, status, offset=round(dy, 1)))
    return results


def _word_summary(old: str, new: str) -> str:
    """Only the words that differ, e.g. '"5" -> "10"' or '+"or a U.S. tax resident"'."""
    a, b = old.split(), new.split()
    parts = []
    for op, a0, a1, b0, b1 in SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if op == "replace":
            parts.append(f'"{" ".join(a[a0:a1])}" -> "{" ".join(b[b0:b1])}"')
        elif op == "insert":
            parts.append(f'+"{" ".join(b[b0:b1])}"')
        elif op == "delete":
            parts.append(f'-"{" ".join(a[a0:a1])}"')
    return "; ".join(parts)


def _has_mark_on_row(page: Page, line: Line) -> bool:
    mid = (line.y0 + line.y1) / 2
    return any(abs((m.y0 + m.y1) / 2 - mid) < ROW_TOLERANCE for m in page.marks)


def _dominant_font(page: Page) -> tuple[str, float] | None:
    c = Counter((l.font, l.size) for l in page.lines)
    return c.most_common(1)[0][0] if c else None


def _diff_page(p1: Page, p2: Page, v1_fields: int, highlights: list[Highlight],
               ignore: set[tuple[str, str]]) -> PageDiff:
    pd = PageDiff(p1.number, p2.number, v1_fields, section=p1.section)
    fate: dict[int, tuple[str, int | None]] = {}  # v1 line index -> (what happened, v2 line index)
    moves: list[tuple[Line, float]] = []

    for op, a0, a1, b0, b1 in _align(p1.lines, p2.lines):
        if op == "equal":
            for i, j in zip(range(a0, a1), range(b0, b1)):
                fate[i] = ("equal", j)
                moves.append((p1.lines[i], p2.lines[j].y0 - p1.lines[i].y0))
            continue
        old_idx, new_idx = list(range(a0, a1)), list(range(b0, b1))
        if op == "replace":
            # Running header/footer change (reported once for the document, not per page)
            for i, j in zip(range(a0, a1), range(b0, b1)):
                if (p1.lines[i].key, p2.lines[j].key) in ignore:
                    fate[i] = ("equal", j)
                    old_idx.remove(i)
                    new_idx.remove(j)
        if old_idx and new_idx:
            # One edit often re-wraps the next line too: report the block as ONE change
            old = " ".join(p1.lines[i].text for i in old_idx)
            new = " ".join(p2.lines[j].text for j in new_idx)
            on_row = any(_has_mark_on_row(p2, p2.lines[j]) for j in new_idx)
            pd.text_changes.append(TextChange("replaced", old, new, p2.lines[new_idx[0]].y0,
                                              on_field_row=on_row, summary=_word_summary(old, new)))
            for i in old_idx:
                fate[i] = ("replaced", len(pd.text_changes) - 1)
        elif old_idx:
            for i in old_idx:
                fate[i] = ("deleted", None)
                pd.text_changes.append(TextChange("deleted", p1.lines[i].text, None, p1.lines[i].y0))
        else:
            for j in new_idx:
                line = p2.lines[j]
                pd.text_changes.append(TextChange("inserted", None, line.text, line.y0,
                                                  new_field=_has_mark_on_row(p2, line)))

    pd.reflows = _reflows(moves)
    pd.highlights = _check_highlights(highlights, p1, p2, fate, pd.text_changes)

    marks1, marks2 = Counter(m.kind for m in p1.marks), Counter(m.kind for m in p2.marks)
    pd.fields_added = sum((marks2 - marks1).values())
    pd.fields_removed = sum((marks1 - marks2).values())
    pd.size_changed = (round(p1.width), round(p1.height)) != (round(p2.width), round(p2.height))
    pd.font_changed = _dominant_font(p1) != _dominant_font(p2)

    _categorise(pd, p1)
    return pd


def _categorise(pd: PageDiff, p1: Page):
    """Suggest a category from the taxonomy in CLAUDE.md. Code only decides 1-3."""
    has_fields = pd.v1_fields > 0 or bool(p1.marks)
    reworded, removed, moved = pd.count("reworded"), pd.count("removed"), pd.count("moved")
    question_reworded = pd.count("reworded", containers=True) + pd.count("removed", containers=True)
    if not pd.highlights:  # no annotation export: a changed line next to a box/blank = a reworded field
        reworded = sum(t.kind == "replaced" and t.on_field_row for t in pd.text_changes)
    r = pd.reasons

    if not has_fields:
        if pd.text_changes:
            pd.category = 1
            r.append(f"Page has no fields in v1; {len(pd.text_changes)} text change(s): swap the page only")
        return

    if pd.fields_added or pd.fields_removed or reworded or removed or question_reworded:
        pd.category = 3
        if pd.fields_added:
            r.append(f"{pd.fields_added} field(s) added")
        if pd.fields_removed:
            r.append(f"{pd.fields_removed} field(s) removed")
        if reworded:
            r.append(f"{reworded} highlighted field wording(s) changed")
        if removed:
            r.append(f"{removed} highlighted field wording(s) removed")
        if question_reworded:
            r.append(f"{question_reworded} question/group wording(s) changed (possible logic change)")
    elif pd.text_changes or pd.reflows or moved or pd.size_changed or pd.font_changed:
        pd.category = 2
        if pd.text_changes:
            r.append(f"{len(pd.text_changes)} text change(s) outside field wording")
    else:
        return

    if moved:
        r.append(f"{moved} highlighted field(s) moved")
    if pd.reflows:
        r.append("Reflow: " + "; ".join(f"{x.lines} lines from y={x.from_y:.0f} moved {x.offset:+.0f}pt"
                                        for x in pd.reflows))
    if pd.size_changed:
        r.append("Page size changed")
    if pd.font_changed:
        r.append("Main font or font size changed")
    # Any wording change on a page with fields might change logic: let the AI classifier decide
    pd.needs_ai_check = bool(pd.text_changes)


def _repeated_changes(pairs) -> set[tuple[str, str]]:
    """Line replacements that happen on most pages (headers/footers like a version number or date)."""
    seen = Counter()
    for p1, p2 in pairs:
        if not (p1 and p2):
            continue
        page_pairs = set()
        for op, a0, a1, b0, b1 in _align(p1.lines, p2.lines):
            if op == "replace":
                page_pairs |= {(p1.lines[i].key, p2.lines[j].key) for i, j in zip(range(a0, a1), range(b0, b1))}
        seen.update(page_pairs)
    full = sum(1 for p1, p2 in pairs if p1 and p2)
    return {k for k, n in seen.items() if n >= 2 and n >= 0.5 * full}


# ---------- Entry point ----------

def diff_documents(v1_pdf: Path, v2_pdf: Path, export: AnnotationExport | None = None) -> DocDiff:
    """`export` = the v1 annotation export (field map + highlights). Without it, fields are guessed
    from boxes and blank lines on the page."""
    pairs = _pair_pages(extract(v1_pdf), extract(v2_pdf))
    ignore = _repeated_changes(pairs)

    results = []
    for p1, p2 in pairs:
        if p1 is None:
            results.append(PageDiff(None, p2.number, 0, category=3, needs_ai_check=True,
                                    reasons=["New page in v2"]))
            continue
        if p2 is None:
            results.append(PageDiff(p1.number, None, 0, category=3, needs_ai_check=True,
                                    reasons=["Page removed in v2"]))
            continue
        n_fields = export.fields_per_page.get(p1.number, 0) if export else len(p1.marks)
        hls = [h for h in export.highlights if h.page == p1.number] if export else []
        results.append(_diff_page(p1, p2, n_fields, hls, ignore))

    # Show the original text (the keys used for matching are lowercased)
    original = {l.key: l.text for p1, p2 in pairs for p in (p1, p2) if p for l in p.lines}
    return DocDiff(results, sorted((original[a], original[b]) for a, b in ignore))
