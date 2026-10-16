"""Public demo: when DEMO_SEED is on and there are no cases yet, create the synthetic Maple Ridge case
(fake data only), so a visitor sees a full example straight away. Off by default."""

import importlib.util
import io
import zipfile

from src import db, settings
from src.cases import save_case
from src.integrations.manual_source import build_manual_thread

ROOT = settings.ROOT
FIXTURE = ROOT / "tests" / "fixtures" / "maple_ridge_export" / "annotation_document_data.json"


def seed_if_empty() -> None:
    if not settings.DEMO_SEED or settings.DATA_MODE != "synthetic":
        return
    conn = db.connect()
    empty = not db.list_cases(conn)
    conn.close()
    if not empty:
        return

    spec = importlib.util.spec_from_file_location("make_synthetic_case", ROOT / "scripts" / "make_synthetic_case.py")
    gen = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gen)
    gen.OUT = settings.DATA_DIR / "demo" / "case_01"  # never the project's synthetic/ folder
    gen.main()
    case = gen.OUT
    original = case / "Maple Ridge Growth Fund II - Subscription Booklet (old).pdf"

    # Annotation export in the real tool's format: the Original PDF + the field map
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.write(original, original.name)
        z.write(FIXTURE, FIXTURE.name)

    thread = build_manual_thread(f"RE: {gen.FUND} - Subscription Booklet issues",
                                 (case / "our_email.txt").read_text(), (case / "client_reply.txt").read_text())
    save_case(thread, "manual", "Maple Ridge Growth Fund II", [
        ("annotation_export", "Maple Ridge Growth Fund II - annotation export.zip", buf.getvalue()),
        ("v2", "Maple Ridge Growth Fund II - Subscription Booklet (updated).pdf",
         (case / "Maple Ridge Growth Fund II - Subscription Booklet (updated).pdf").read_bytes()),
    ])
