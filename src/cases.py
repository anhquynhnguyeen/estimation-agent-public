"""Saves a new case: the email thread goes into the database, files go into data/cases/<id>/."""

import json
import shutil
from pathlib import Path

from src import db
from src.integrations.base import EmailThread
from src.integrations.doccom import (DoccomChange, DoccomClient, changes_from_job, read_comparison_pdf,
                                     review_url)
from src.pdf.annotation_export import extract_zip
from src.settings import CASES_DIR, DATA_MODE


def case_dir(case_id: int) -> Path:
    return CASES_DIR / str(case_id)


def save_case(thread: EmailThread, source: str, fund_name: str, files: list[tuple[str, str, bytes | Path]]) -> int:
    """Create a case.

    files: list of (role, filename, content) where content is the file bytes (uploaded)
           or a Path (already downloaded from Front). Roles: v1, v2, annotation_export, other.
    """
    conn = db.connect()
    case_id = db.create_case(conn, source=source, data_mode=DATA_MODE, subject=thread.subject,
                             fund_name=fund_name, front_conversation_id=thread.conversation_id or None)
    folder = case_dir(case_id)
    folder.mkdir(parents=True, exist_ok=True)

    # 1. Save every email in the thread
    for m in thread.messages:
        db.add_message(conn, case_id, m.direction, m.body_text, author=m.author, sent_at=m.sent_at,
                       subject=m.subject, front_message_id=m.message_id or None)

    # 2. Save every file into the case folder, tagged with its role
    for role, filename, content in files:
        target = folder / role / Path(filename).name
        target.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, Path):
            shutil.copyfile(content, target)
        else:
            target.write_bytes(content)
        db.add_attachment(conn, case_id, target.name, role, target, target.stat().st_size)

        # The annotation export is a zip: unpack it next to itself so we can read the fields
        if role == "annotation_export" and target.suffix.lower() == ".zip":
            extract_zip(target, folder / "annotation_export" / "unzipped")

    conn.close()
    return case_id


def case_pdfs(case_id: int) -> tuple[Path | None, Path | None]:
    """(Original, Revised) PDF paths. An Original PDF uploaded on its own wins; otherwise the PDF inside the
    annotation export is used (the export may be of a later version, e.g. Example Capital's Version 8 = Revised)."""
    conn = db.connect()
    _, _, files = db.get_case_details(conn, case_id)
    conn.close()
    original = next((Path(f["path"]) for f in files if f["role"] == "v1"), None)
    unzipped = case_dir(case_id) / "annotation_export" / "unzipped"
    if not original and unzipped.exists():
        original = next(iter(sorted(unzipped.glob("*.pdf"))), None)
    revised = next((Path(f["path"]) for f in files if f["role"] == "v2"), None)
    return original, revised


# ---------- Doccom: the comparison link and PDF for each case ----------

def doccom_dir(case_id: int) -> Path:
    return case_dir(case_id) / "doccom"


def doccom_info(case_id: int) -> dict | None:
    """{"job_id", "review_url", "source": "app" | "manual"} once a Doccom comparison is linked."""
    f = doccom_dir(case_id) / "job.json"
    return json.loads(f.read_text()) if f.exists() else None


def doccom_pdf(case_id: int) -> Path | None:
    f = doccom_dir(case_id) / "side-by-side.pdf"
    return f if f.exists() else None


def save_doccom_link(case_id: int, job_id: str, source: str, pdf_bytes: bytes | None = None):
    folder = doccom_dir(case_id)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "job.json").write_text(json.dumps({"job_id": job_id, "review_url": review_url(job_id), "source": source}))
    if pdf_bytes:
        (folder / "side-by-side.pdf").write_bytes(pdf_bytes)


def send_to_doccom(case_id: int) -> dict:
    """Upload Original + Revised to Doccom, keep the review link and the side-by-side PDF.
    Raises if Doccom can't be reached; the app then offers the manual option."""
    original, revised = case_pdfs(case_id)
    if not (original and revised):
        raise ValueError("The case needs both an Original and a Revised PDF.")
    client = DoccomClient()
    job_id = client.compare(original, revised)
    job = client.wait(job_id)
    save_doccom_link(case_id, job_id, "app")
    client.download_pdf(job_id, doccom_dir(case_id) / "side-by-side.pdf")
    (doccom_dir(case_id) / "changes.json").write_text(json.dumps(job["result"]["changes"]))
    return doccom_info(case_id)


def doccom_changes(case_id: int) -> list[DoccomChange] | None:
    """Doccom's changes for this case: from its change list if the app made the comparison,
    otherwise read from the side-by-side PDF someone uploaded."""
    data = doccom_dir(case_id) / "changes.json"
    if data.exists():
        return changes_from_job({"result": {"changes": json.loads(data.read_text())}})
    pdf = doccom_pdf(case_id)
    return read_comparison_pdf(pdf) if pdf else None
