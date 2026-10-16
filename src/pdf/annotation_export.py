"""Reads the annotation export zip from our PDF tool (e.g. "... - Version 8.zip").

The zip holds the annotated v1 PDF and annotation_document_data.json, which lists every
object with its page number. That tells us which pages have fields and which don't.

What the real export looks like (checked against our tool's output, 2026-10-12):
- `area` is a fraction of the page (0 to 1), not PDF points.
- Group containers (radio groups, checkbox groups...) have no page or area, only `children`.
- A text highlight is a PdfTextLabelDTO (the wording, `content` = [1, "text"], `labelOf` = its field)
  whose `children` are PdfTextBlockDTO boxes that hold the page and area.
"""

import json
import zipfile
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

ANNOTATION_FILE = "annotation_document_data.json"

# Text highlights: not fillable fields (see open question in CLAUDE.md)
HIGHLIGHT_TYPES = {"PdfTextBlockDTO", "PdfTextLabelDTO"}
# Containers that group other fields; they are not placed on the page themselves
CONTAINER_TYPES = {"PdfRadioGroupFieldDTO", "PdfMultipleCheckboxesFieldDTO", "PdfGroupFieldDTO",
                   "PdfParagraphFieldDTO"}


@dataclass
class Highlight:
    """A highlighted piece of v1 text, used later to check if the wording changed in v2."""
    page: int                    # starting at 1
    area: dict                   # {left, top, right, bottom} as fractions of the page
    text: str | None             # None if the box has no label text attached
    field_id: str | None = None  # the field this wording belongs to
    field_type: str | None = None  # e.g. "PdfCheckboxFieldDTO", or a container like "PdfRadioGroupFieldDTO"

    @property
    def of_container(self) -> bool:
        """Wording of a group (usually the question), not of one fillable field."""
        return self.field_type in CONTAINER_TYPES


@dataclass
class AnnotationExport:
    pdf_path: Path | None
    total_pages: int
    total_objects: int
    fields_per_page: dict[int, int]  # page number (starting at 1) -> how many fillable fields
    object_types: dict[str, int]     # e.g. {"PdfTextFieldDTO": 7, "PdfTextLabelDTO": 17}
    highlights: list[Highlight] = field(default_factory=list)

    @property
    def total_fields(self) -> int:
        return sum(self.fields_per_page.values())

    @property
    def pages_without_fields(self) -> list[int]:
        """Instruction-type pages: no fields in v1."""
        return [p for p in range(1, self.total_pages + 1) if self.fields_per_page.get(p, 0) == 0]


def extract_zip(zip_path: Path, out_dir: Path) -> Path:
    """Unzip safely: only keep the file names, never folders like '../' from inside the zip."""
    out_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path) as z:
        for member in z.infolist():
            if member.is_dir():
                continue
            name = Path(member.filename).name  # drop any folder part
            if not name or name.startswith("."):
                continue
            (out_dir / name).write_bytes(z.read(member))
    return out_dir


def _label_text(content) -> str | None:
    """`content` is [1, "text"] in the real export (a plain string in older synthetic files)."""
    if isinstance(content, list):
        content = next((c for c in content if isinstance(c, str)), None)
    return content if isinstance(content, str) else None


def _read_highlights(objects: list[dict]) -> list[Highlight]:
    by_id = {o["id"]: o for o in objects if "id" in o}
    highlights, used_blocks = [], set()

    for label in (o for o in objects if o.get("__typename__") == "PdfTextLabelDTO"):
        text = _label_text(label.get("content"))
        # Real format: the page/area live on the PdfTextBlockDTO children
        boxes = [by_id[c] for c in label.get("children", []) if c in by_id]
        if not boxes and "pageIndex" in label:  # older synthetic format: area on the label itself
            boxes = [label]
        for box in boxes:
            used_blocks.add(box["id"])
            owner = by_id.get(label.get("labelOf"), {})
            highlights.append(Highlight(int(box["pageIndex"]) + 1, box.get("area", {}), text,
                                        label.get("labelOf"), owner.get("__typename__")))

    # Highlight boxes with no label text attached
    for block in (o for o in objects if o.get("__typename__") == "PdfTextBlockDTO"):
        if block["id"] not in used_blocks and "pageIndex" in block:
            highlights.append(Highlight(int(block["pageIndex"]) + 1, block.get("area", {}), None))

    return sorted(highlights, key=lambda h: (h.page, h.area.get("top", 0)))


def read_export(folder: Path) -> AnnotationExport:
    """Read an already-unzipped export folder."""
    data = json.loads((folder / ANNOTATION_FILE).read_text(encoding="utf-8"))
    objects = data.get("pdfObjectDTO", [])

    # Only fillable fields placed on a page count. pageIndex starts at 0 in the file;
    # we show pages starting at 1 like a PDF viewer.
    fillable = [o for o in objects
                if o.get("__typename__") not in HIGHLIGHT_TYPES | CONTAINER_TYPES and "pageIndex" in o]
    per_page = Counter(int(o["pageIndex"]) + 1 for o in fillable)
    types = Counter(o.get("__typename__", "unknown") for o in objects)

    pdfs = sorted(folder.glob("*.pdf"))
    return AnnotationExport(
        pdf_path=pdfs[0] if pdfs else None,
        total_pages=int(data.get("totalPages") or 0),
        total_objects=len(objects),
        fields_per_page=dict(sorted(per_page.items())),
        object_types=dict(types.most_common()),
        highlights=_read_highlights(objects),
    )
