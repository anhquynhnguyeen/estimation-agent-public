"""Doccom (https://doccom.example.com), Anduin's internal PDF comparison tool.

Two ways in, same result (a list of DoccomChange per page):
- Automatic: DoccomClient uploads Original + Revised, waits, reads Doccom's change list and
  downloads the side-by-side PDF. Endpoints taken from Doccom's own web page (no public docs):
    POST /api/compare (form fields: left, right)  -> {"jobId": ...}
    GET  /api/jobs/<id>                           -> {"status": "done", "result": {"changes": ...}}
    GET  /api/jobs/<id>/download/side-by-side.pdf
    review page: /review?job=<id>
- Manual (when the app can't reach Doccom): a person runs the comparison in the browser, pastes the
  review link and uploads the downloaded side-by-side PDF; read_comparison_pdf() reads its marks.

Real client subdocs may be sent to Doccom (Anduin's own tool, confirmed by Anh 2026-10-13).
"""

import re
import time
from dataclasses import dataclass
from pathlib import Path

import pymupdf
import requests

BASE_URL = "https://doccom.example.com"
JOB_RE = re.compile(r"[?&]job=([0-9a-f]{16,64})")

# Mark colours in the side-by-side PDF
RED = (0.82, 0.14, 0.18)   # deleted (on Original pages)
BLUE = (0.04, 0.41, 0.85)  # inserted (on Revised pages)


@dataclass
class DoccomChange:
    kind: str            # "insert", "delete", "replace" (from the API) or "deleted"/"inserted" (from the PDF)
    page: int            # Original page number when known, else Revised page number (starting at 1)
    old: str = ""
    new: str = ""


def review_url(job_id: str) -> str:
    return f"{BASE_URL}/review?job={job_id}"


def job_id_from_link(link: str) -> str | None:
    """Accepts a full review link or a bare job id."""
    link = (link or "").strip()
    m = JOB_RE.search(link)
    if m:
        return m.group(1)
    return link if re.fullmatch(r"[0-9a-f]{16,64}", link) else None


class DoccomClient:
    def __init__(self, base_url: str = BASE_URL, timeout: int = 60):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.session = requests.Session()

    def compare(self, original: Path, revised: Path) -> str:
        """Upload both PDFs and return the job id. Not private, so teammates can open the review link."""
        with open(original, "rb") as left, open(revised, "rb") as right:
            r = self.session.post(
                f"{self.base_url}/api/compare", params={"private": "false", "directLink": "false"},
                files={"left": (original.name, left, "application/pdf"),
                       "right": (revised.name, right, "application/pdf")},
                timeout=self.timeout)
        self._check(r)
        return r.json()["jobId"]

    def wait(self, job_id: str, max_seconds: int = 120) -> dict:
        """Poll until the comparison is finished; returns the job (with result.changes)."""
        deadline = time.time() + max_seconds
        while True:
            r = self.session.get(f"{self.base_url}/api/jobs/{job_id}", timeout=self.timeout)
            self._check(r)
            job = r.json()
            if job.get("status") == "done":
                return job
            if job.get("status") == "failed":
                raise RuntimeError(f"Doccom comparison failed: {job.get('detail') or job.get('error') or 'no reason given'}")
            if time.time() > deadline:
                raise TimeoutError("Doccom is taking too long; open the review link later.")
            time.sleep(2)

    def download_pdf(self, job_id: str, target: Path, variant: str = "side-by-side") -> Path:
        r = self.session.get(f"{self.base_url}/api/jobs/{job_id}/download/{variant}.pdf", timeout=self.timeout)
        self._check(r)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(r.content)
        return target

    @staticmethod
    def _check(r: requests.Response):
        # Behind Cloudflare Access a missing login shows up as a redirect to a login page
        if "cloudflareaccess" in r.url or "text/html" in r.headers.get("content-type", "") and r.status_code == 200:
            raise PermissionError("Doccom asked for a login (Cloudflare Access). Use the manual option.")
        r.raise_for_status()


def changes_from_job(job: dict) -> list[DoccomChange]:
    """Doccom's own change list (Draftable format), without the unchanged ('match') parts."""
    out = []
    for c in job["result"]["changes"]["changes"]:
        if c["kind"] == "match":
            continue
        left, right = c.get("leftRegion") or {}, c.get("rightRegion") or {}
        page = left.get("pageIndex") if left.get("pageIndex") is not None else right.get("pageIndex")
        out.append(DoccomChange(c["kind"], (page or 0) + 1, c.get("leftText", ""), c.get("rightText", "")))
    return out


def read_comparison_pdf(path: Path) -> list[DoccomChange]:
    """Read the marks from a downloaded side-by-side PDF (manual option).

    Layout: page 1 is a cover; then Original page 1, Revised page 1, Original page 2, ...
    Red boxes on Original pages = deleted text, blue boxes on Revised pages = inserted text.
    """
    out = []
    with pymupdf.open(path) as doc:
        for i in range(1, len(doc)):
            page, subdoc_page = doc[i], (i + 1) // 2
            for d in page.get_drawings():
                fill = d.get("fill")
                if not fill:
                    continue
                colour = tuple(round(x, 2) for x in fill)
                if colour not in (RED, BLUE):
                    continue
                for item in d["items"]:
                    if item[0] != "re":
                        continue
                    text = " ".join(page.get_textbox(item[1]).split())
                    if colour == RED:
                        out.append(DoccomChange("deleted", subdoc_page, old=text))
                    else:
                        out.append(DoccomChange("inserted", subdoc_page, new=text))
    return out


def repeated_on_most_pages(changes: list[DoccomChange]) -> set[tuple[str, str]]:
    """(old, new) pairs found on most pages, e.g. a version number in the header."""
    pages = {c.page for c in changes}
    seen: dict[tuple[str, str], set[int]] = {}
    for c in changes:
        seen.setdefault((c.old, c.new), set()).add(c.page)
    return {k for k, p in seen.items() if len(p) >= 2 and len(p) >= 0.5 * max(len(pages), 1)}
