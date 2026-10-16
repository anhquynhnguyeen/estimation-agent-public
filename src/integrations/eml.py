"""Read a saved email file (.eml, e.g. downloaded from Front or Gmail) into an EmailMessage.

An email from an @anduintransact.com address is ours ("outbound"); anything else is the client's ("inbound").
"""

import email
import re
from email import policy
from email.utils import parsedate_to_datetime
from html import unescape
from pathlib import Path

from src.integrations.base import Attachment, EmailMessage

OUR_DOMAIN = "anduintransact.com"


def eml_attachments(raw: bytes) -> list[tuple[str, bytes]]:
    """(file name, content) of each attachment in a saved email."""
    msg = email.message_from_bytes(raw, policy=policy.default)
    return [(Path(p.get_filename() or f"attachment-{i}").name, p.get_payload(decode=True) or b"")
            for i, p in enumerate(msg.iter_attachments())]


def parse_eml(raw: bytes, attachments_dir: Path | None = None) -> EmailMessage:
    msg = email.message_from_bytes(raw, policy=policy.default)
    body = msg.get_body(preferencelist=("plain", "html"))
    text = body.get_content() if body else ""
    if body is not None and body.get_content_type() == "text/html":
        text = unescape(re.sub(r"<(br|/p|/div)[^>]*>", "\n", text, flags=re.I))
        text = re.sub(r"<[^>]+>", "", text)
    sender = str(msg.get("from") or "")
    try:
        sent_at = parsedate_to_datetime(msg["date"]).isoformat() if msg["date"] else ""
    except (TypeError, ValueError):
        sent_at = ""

    attachments = []
    if attachments_dir:
        attachments_dir.mkdir(parents=True, exist_ok=True)
        for i, part in enumerate(msg.iter_attachments()):
            name = Path(part.get_filename() or f"attachment-{i}").name
            target = attachments_dir / (f"{i}-{name}" if (attachments_dir / name).exists() else name)
            data = part.get_payload(decode=True) or b""
            target.write_bytes(data)
            attachments.append(Attachment(name, target, len(data), part.get_content_type()))

    return EmailMessage(
        direction="outbound" if OUR_DOMAIN in sender.lower() else "inbound",
        body_text=text.strip(),
        author=sender,
        sent_at=sent_at,
        subject=str(msg.get("subject") or ""),
        attachments=attachments,
    )
