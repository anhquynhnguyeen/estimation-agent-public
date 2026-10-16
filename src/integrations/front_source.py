"""Reads emails from Front. READ-ONLY: this file only ever uses GET requests.

Front API docs: https://dev.frontapp.com/reference
NOTE: field names (is_inbound, author, attachments[].url ...) are taken from the docs.
They still need checking against a real response once we have a token.
"""

import html
import re
from datetime import datetime, timezone
from pathlib import Path

import requests

from src.integrations.base import Attachment, ConversationSummary, EmailMessage, EmailSource, EmailThread

API = "https://api2.frontapp.com"


def conversation_id_from_link(text: str) -> str:
    """Accepts a Front link or a bare id and returns the id, e.g. 'cnv_abc123'."""
    match = re.search(r"cnv_[A-Za-z0-9]+", text or "")
    return match.group(0) if match else ""


def _html_to_text(body: str) -> str:
    """Very simple HTML -> plain text, good enough for reading emails."""
    body = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</li>", "\n", body or "")
    body = re.sub(r"<[^>]+>", "", body)
    return re.sub(r"\n{3,}", "\n\n", html.unescape(body)).strip()


def _ts_to_text(ts) -> str:
    """Front sends times as seconds since 1970; turn that into readable text."""
    if not ts:
        return ""
    return datetime.fromtimestamp(float(ts), tz=timezone.utc).isoformat(timespec="seconds")


def _safe_filename(name: str) -> str:
    """Remove characters that could break a file path (like '/' or '..')."""
    name = re.sub(r"[^\w.\- ()\[\],]", "_", name or "file").strip(" .")
    return name or "file"


class FrontEmailSource(EmailSource):
    def __init__(self, token: str, inbox_id: str = ""):
        if not token:
            raise ValueError("FRONT_API_TOKEN is missing in .env")
        self.inbox_id = inbox_id
        self.session = requests.Session()
        self.session.headers["Authorization"] = f"Bearer {token}"

    def _get(self, path_or_url: str, **params):
        url = path_or_url if path_or_url.startswith("http") else API + path_or_url
        resp = self.session.get(url, params=params, timeout=30)
        resp.raise_for_status()  # stop with a clear error if Front says no
        return resp

    def list_recent(self, limit: int = 25) -> list[ConversationSummary]:
        """Latest conversations, newest first (from one inbox if FRONT_INBOX_ID is set)."""
        path = f"/inboxes/{self.inbox_id}/conversations" if self.inbox_id else "/conversations"
        data = self._get(path, limit=limit).json()
        return [
            ConversationSummary(
                conversation_id=c.get("id", ""),
                subject=c.get("subject") or "(no subject)",
                last_message_at=_ts_to_text(c.get("last_message", {}).get("created_at") or c.get("created_at")),
                status=c.get("status", ""),
            )
            for c in data.get("_results", [])
        ]

    def fetch_thread(self, conversation_id: str, save_dir: Path) -> EmailThread:
        """Get every message in the conversation and download all attachments."""
        conv = self._get(f"/conversations/{conversation_id}").json()

        # Collect all pages of messages (Front returns them in pages)
        raw_messages, url = [], f"/conversations/{conversation_id}/messages"
        while url:
            page = self._get(url).json()
            raw_messages += page.get("_results", [])
            url = (page.get("_pagination") or {}).get("next")

        raw_messages.sort(key=lambda m: m.get("created_at") or 0)  # oldest first

        save_dir.mkdir(parents=True, exist_ok=True)
        messages = []
        for m in raw_messages:
            author = m.get("author") or {}
            # Sender: a teammate (author) or, for client emails, the 'from' recipient
            sender = author.get("email") or next(
                (r.get("handle", "") for r in m.get("recipients", []) if r.get("role") == "from"), ""
            )
            messages.append(EmailMessage(
                direction="inbound" if m.get("is_inbound") else "outbound",
                body_text=m.get("text") or _html_to_text(m.get("body", "")),
                author=sender,
                sent_at=_ts_to_text(m.get("created_at")),
                subject=m.get("subject", ""),
                message_id=m.get("id", ""),
                attachments=[self._download(a, save_dir) for a in m.get("attachments", [])],
            ))

        return EmailThread(subject=conv.get("subject", ""), messages=messages, conversation_id=conversation_id)

    def _download(self, att: dict, save_dir: Path) -> Attachment:
        """Save one attachment to disk (skips download if we already have it)."""
        name = _safe_filename(att.get("filename", "file"))
        target = save_dir / name
        if not target.exists():
            target.write_bytes(self._get(att["url"]).content)
        return Attachment(filename=name, path=target, size_bytes=target.stat().st_size,
                          content_type=att.get("content_type", ""))
