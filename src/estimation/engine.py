"""Turns counted changes into a time estimate. Plain code: the AI never outputs hours.

Steps:
1. features_from_diff(): count what changed (from the Original/Revised comparison + annotation export,
   plus any pages a reviewer or the AI classifier marked as logic changes).
2. estimate(): apply the numbers in config/estimation_playbook.yaml and list what drove the total.
"""

from dataclasses import dataclass, field
from pathlib import Path

import yaml

from src.pdf.diff import DocDiff

PLAYBOOK = Path(__file__).resolve().parents[2] / "config" / "estimation_playbook.yaml"


@dataclass
class Features:
    total_fields: int                 # fillable fields in the Original
    fields_added: int = 0
    fields_removed: int = 0
    fields_reworded: int = 0
    fields_moved: int = 0
    ui_items: int = 0                 # fields/highlights moved, reworded, added or removed: each needs a UI fix
    reflows: int = 0
    embedded_files_swapped: int = 0   # runs of pages without fields that changed (each run = one embedded file)
    logic_changes: int = 0            # pages/items that change Checklist logic
    layout_changed: bool = False      # page size or main font changed on a page with fields
    field_pages_changed: list[int] = field(default_factory=list)
    swapped_pages: list[int] = field(default_factory=list)
    swapped_files: list[str] = field(default_factory=list)  # e.g. "Instructions (pages 1, 2)"
    added_files: list[str] = field(default_factory=list)    # extra PDFs from the client, added as embedded files
    logic_pages: list[int] = field(default_factory=list)


@dataclass
class Driver:
    label: str
    low_s: float
    high_s: float
    why: str


@dataclass
class Estimate:
    low_s: float
    high_s: float
    placement: str                    # none / slight / great
    drivers: list[Driver]
    needs_engineer: bool
    not_estimated: list[str]          # parts we know about but have no benchmark for yet
    playbook: dict                    # the numbers used, saved with the estimate for later checking

    @property
    def low_minutes(self) -> int:
        return round(self.low_s / 60)

    @property
    def high_minutes(self) -> int:
        return round(self.high_s / 60)


def load_playbook(path: Path = PLAYBOOK) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def features_from_diff(diff: DocDiff, total_fields: int, logic_pages: set[int] = frozenset(),
                       extra_logic_changes: int = 0, extra_pdfs: list[str] = ()) -> Features:
    """`logic_pages`: pages judged to change logic (AI classifier or reviewer).
    `extra_logic_changes`: logic changes asked for in the email that aren't visible in the PDF."""
    f = Features(total_fields=total_fields)
    # One embedded file = consecutive pages without fields IN THE SAME SECTION.
    # Count a file once if any of its pages changed.
    file_id, prev_section, in_file = 0, object(), False
    changed_files: dict[int, tuple[str | None, list[int]]] = {}
    for p in diff.pages:
        has_fields = p.v1_fields > 0
        if not has_fields:
            if not in_file or p.section != prev_section:
                file_id += 1
            in_file, prev_section = True, p.section
            if p.changed and p.page_v1:
                f.swapped_pages.append(p.page_v1)
                changed_files.setdefault(file_id, (p.section, []))[1].append(p.page_v1)
            continue
        in_file = False
        f.fields_added += p.fields_added
        f.fields_removed += p.fields_removed
        f.fields_reworded += p.count("reworded")
        f.fields_moved += p.count("moved")
        # One UI fix per FIELD that moved or changed, however many highlights it has; question/group
        # wordings are not fields (Anh, 2026-10-16: matched the team's 45 min on Example Capital)
        changed_highlights = len({h.field_id for h in p.highlights
                                  if h.field_id and h.status in ("moved", "reworded", "removed") and not h.of_container})
        if not p.highlights:  # no annotation export: changed lines next to a field + moved lines (max = fields)
            moved_lines = min(sum(r.lines for r in p.reflows), p.v1_fields)
            changed_highlights = moved_lines + sum(t.on_field_row for t in p.text_changes)
        f.ui_items += changed_highlights + p.fields_added + p.fields_removed
        f.reflows += len(p.reflows)
        f.layout_changed |= p.size_changed or p.font_changed
        if p.changed:
            f.field_pages_changed.append(p.page_v1 or p.page_v2)
    f.embedded_files_swapped = len(changed_files)
    f.swapped_files = [f"{section or 'untitled section'} (pages {_pages(pages)})"
                       for section, pages in changed_files.values()]
    f.added_files = list(extra_pdfs)
    f.logic_pages = sorted(logic_pages)
    f.logic_changes = len(logic_pages) + extra_logic_changes
    return f


def placement_level(f: Features, rules: dict) -> tuple[str, str]:
    """Pick none / slight / great from the comparison, with the reason."""
    moved_share = f.fields_moved / f.total_fields if f.total_fields else 0
    if f.layout_changed:
        return "great", "page size or main font changed on a page with fields"
    if moved_share > rules.get("great_moved_share", 0.5) and f.reflows > 1:
        return "great", f"{moved_share:.0%} of fields moved, in {f.reflows} separate shifts"
    if f.fields_moved or f.fields_added or f.fields_removed:
        how = f"{f.fields_moved} of {f.total_fields} fields moved"
        if f.reflows:
            how += f" ({f.reflows} uniform reflow{'s' if f.reflows > 1 else ''}, cheaper than it looks)"
        return "slight", how
    return "none", "no field moved"


def estimate(f: Features, playbook: dict | None = None) -> Estimate:
    pb = playbook or load_playbook()
    spread = (pb.get("range_spread_pct") or 0) / 100
    drivers: list[Driver] = []

    def per_item(label, count, seconds, why):
        if count and seconds:
            total = count * seconds
            drivers.append(Driver(label, total * (1 - spread), total * (1 + spread), why))

    # 1. Annotation: every new version means carrying the annotations over
    level, level_why = placement_level(f, pb.get("placement_rules", {}))
    new_fields = f.fields_added + f.fields_reworded
    if pb.get("logic_page_change_mode") == "strict_notion" and f.field_pages_changed:
        carried = max(f.total_fields - f.fields_removed - f.fields_reworded, 0)
        per_item("Carry over existing fields", carried, pb["transferred_field"],
                 f"{carried} fields x {pb['transferred_field']}s (strict Notion mode)")
    else:
        lo, hi = pb["placement_minutes"][level]
        rules = pb.get("placement_rules", {})
        if level == "slight" and f.reflows and rules.get("uniform_reflow_low_end", True):
            hi = lo  # fields only shifted in uniform reflows: low end of the range (Anh, 2026-10-16)
            level_why += "; uniform reflows only, so the low end"
        drivers.append(Driver(f"Move annotations to the Revised ({level} placement difference)",
                              lo * 60, hi * 60, (f"{lo}" if lo == hi else f"{lo}-{hi}") + f" min baseline: {level_why}"))
    per_item("New or reworded fields", new_fields, pb["new_field"],
             f"{f.fields_added} added + {f.fields_reworded} reworded = {new_fields} x {pb['new_field']}s")

    # 2. Pages shown as embedded PDF (no fields): swap the file
    per_item("Swap embedded file(s)", f.embedded_files_swapped, pb["embedded_file_swap"],
             f"{f.embedded_files_swapped} file(s) x {pb['embedded_file_swap']}s: " + "; ".join(f.swapped_files))

    # 2b. Extra PDFs the client attached (e.g. IRS forms): each is added as an embedded file (Anh, 2026-10-16)
    per_item("Add embedded file(s)", len(f.added_files), pb["embedded_file_swap"],
             f"{len(f.added_files)} new file(s) x {pb['embedded_file_swap']}s: " + ", ".join(f.added_files))

    # 3. Checklist logic
    per_item("Checklist logic changes", f.logic_changes, pb["checklist_rule_change"],
             f"{f.logic_changes} change(s) x {pb['checklist_rule_change']}s"
             + (f" (pages {_pages(f.logic_pages)})" if f.logic_pages else ""))

    # 4. UI fix: every annotated field or highlight that moved or changed
    per_item("UI fix", f.ui_items, pb.get("ui_change"),
             f"{f.ui_items} field(s)/highlight(s) moved or changed x {pb.get('ui_change')}s")

    not_estimated = []
    if f.ui_items and pb.get("ui_change") is None:
        not_estimated.append("UI fix: no benchmark set in the playbook")

    return Estimate(
        low_s=sum(d.low_s for d in drivers), high_s=sum(d.high_s for d in drivers),
        placement=level, drivers=drivers, needs_engineer=f.logic_changes > 0,
        not_estimated=not_estimated, playbook=pb,
    )


def _pages(pages: list[int]) -> str:
    return ", ".join(map(str, pages)) or "-"
