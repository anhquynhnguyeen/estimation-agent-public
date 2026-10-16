"""Human review: routine or needs attention, the Slack update and ClickUp tasks, and Approve / Reject.
Nothing is sent before a person clicks Approve."""

import re
from pathlib import Path

import yaml

from src import db, settings
from src.clickup_fields import from_clickup, next_item
from src.estimation.engine import Estimate, Features
from src.estimation.rounding import duration, round_up
from src.integrations.base import Notifier, TaskTracker
from src.integrations.clickup import ClickUpTracker
from src.integrations.mocks import MockNotifier, MockTracker
from src.integrations.slack import SlackNotifier
from src.pdf.diff import DocDiff

RULES = Path(__file__).resolve().parents[1] / "config" / "review_rules.yaml"

ANNOTATION_DRIVERS = ("Move annotations", "Carry over", "New or reworded")
CL_UI_DRIVERS = ("Checklist", "UI fix", "Swap embedded", "Add embedded")  # embedded files: done by the UI person


def load_rules(path: Path = RULES) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


# ---------- Routine or needs attention ----------

def attention_reasons(est: Estimate, f: Features, email: dict | None, logic: dict | None,
                      has_field_map: bool, rules: dict | None = None) -> list[str]:
    """Short reasons this case needs a closer look. Empty list = routine."""
    r = rules or load_rules()
    reasons = []
    low = r.get("low_confidence_below", 0.7)
    for result in (email, logic):
        if result and any(i.get("confidence", 1) < low or i.get("quote_verified") is False
                          for i in result.get("issues", []) + result.get("pages", [])):
            reasons.append("AI unsure")
            break
    if r.get("flag_any_logic_change", True) and f.logic_changes:
        reasons.append("Logic change" + (f" (p. {', '.join(map(str, f.logic_pages))})" if f.logic_pages else ""))
    limit = r.get("estimate_above_minutes")
    if limit and est.high_minutes > limit:
        reasons.append(f"Over {duration(round_up(limit))}")
    if email and r.get("flag_client_question", True) and email.get("client_asked_question"):
        reasons.append("Client asked a question")
    if email and r.get("flag_missing_attachments", True) and email.get("missing_requested_items"):
        reasons.append("Documents missing")
    if r.get("flag_uncertain_page_type", True) and not has_field_map:
        reasons.append("No annotation export")
    return reasons


# ---------- Times (rounded the way the team writes them) ----------

def part_minutes(est: Estimate, prefixes) -> tuple[float, float]:
    ds = [d for d in est.drivers if d.label.startswith(prefixes)]
    return sum(d.low_s for d in ds) / 60, sum(d.high_s for d in ds) / 60


def rounded_times(est: Estimate, f: Features) -> dict:
    """PDF annotation and CL + UI, each the middle of its range rounded UP (e.g. 41 min -> 45 mins)."""
    lo, hi = part_minutes(est, ANNOTATION_DRIVERS)
    cl_lo, cl_hi = part_minutes(est, CL_UI_DRIVERS)
    return {
        "annotation": round_up((lo + hi) / 2),
        "cl": round_up((cl_lo + cl_hi) / 2),
        "ui_missing": bool(f.ui_items) and est.playbook.get("ui_change") is None,
    }


def cl_ui_text(t: dict) -> str | None:
    if not t["cl"] and not t["ui_missing"]:
        return None
    if t["ui_missing"]:
        return (duration(t["cl"]) + " + UI") if t["cl"] else "UI (to estimate)"
    return duration(t["cl"])


# ---------- What the document change was ----------

def change_details(diff: DocDiff, logic_pages: set[int], added_files: list[str] = ()) -> str:
    """'Part B: added "A family office ..." (logic change); Instructions: "5" -> "10"' (code, from the comparison)."""
    parts = []
    for p in diff.changed_pages:
        bits = []
        for t in p.text_changes:
            if t.kind == "inserted":
                bits.append(f'added "{_short(t.new)}"')
            elif t.kind == "deleted":
                bits.append(f'removed "{_short(t.old)}"')
            else:
                bits += [_plain(piece) for piece in (t.summary or f'"{t.old}" -> "{t.new}"').split("; ")]
        if not bits and p.reflows:
            bits.append("fields shifted")
        if bits:
            where = p.section or f"page {p.page_v1}"
            parts.append(f"{where}: {', '.join(bits)}" + (" (logic change)" if p.page_v1 in logic_pages else ""))
    if added_files:
        parts.append("New embedded files: " + ", ".join(added_files))
    return "\n".join(f"• {part}" for part in parts)


def _plain(piece: str) -> str:
    """'"5" -> "10"' -> 'changed "5" to "10"';  '+"x"' -> 'added "x"';  '-"x"' -> 'removed "x"'."""
    if piece.startswith('+"'):
        return f'added {_short(piece[1:])}'
    if piece.startswith('-"'):
        return f'removed {_short(piece[1:])}'
    return "changed " + _short(piece.replace('" -> "', '" to "'), 90)


def details_from_message(message: str) -> str | None:
    """The bullet lines under 'The document has been updated with:' in a (possibly edited) Slack message."""
    lines = message.splitlines()
    start = next((i for i, line in enumerate(lines) if line.strip().lower().startswith("the document has been updated")), None)
    if start is None:
        return None
    block = []
    for line in lines[start + 1:]:
        if not line.strip():
            break
        block.append(line)
    return "\n".join(block) or None


def copy_text(message: str) -> str:
    """The Slack message for pasting by hand: Slack's <url|text> links become 'text: url'."""
    return re.sub(r"<(https?://[^|>]+)\|([^>]+)>", r"\2: \1", message)


def channel_url(channel: str | None = None) -> str | None:
    """Link to open the (test) channel in Slack: needs a channel ID like C0123ABC."""
    target = channel or settings.SLACK_TEST_CHANNEL
    return f"{settings.SLACK_WORKSPACE_URL}/archives/{target}" if re.fullmatch(r"[CG][A-Z0-9]{6,}", target or "") else None


def _short(text: str | None, limit: int = 70) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[:limit - 1].rstrip() + "…"


def slack_message(doccom_url: str | None, details: str, t: dict) -> str:
    """The team's Slack update format."""
    docs = f"<{doccom_url}|Original and updated>" if doccom_url else "Original and updated"
    lines = ["Please find the comparison for:", f"- {docs}", "",
             "The document has been updated with:" if details else "The document has no content changes.",
             *([details] if details else []), "",
             f"PDF annotation: {duration(t['annotation'])}"]
    cl_ui = cl_ui_text(t)
    if cl_ui:
        lines.append(f"CL and UI fix: {cl_ui}")
    return "\n".join(lines)


# ---------- ClickUp tasks ----------

def parent_title(fund: str, item: str) -> str:
    """The team's format: 'Update - Maple Ridge Growth Fund II - Item 3' (item is free text: 'Items 2 to 5'...)."""
    return f"Update - {fund} - {item.strip()}"


def task_description(doccom_url: str | None, details: str, estimate_lines: list[str] | None = None,
                     minutes: int | None = None) -> str:
    """ClickUp description: comparison link, then the same change details as the Slack update,
    then (subtasks) what the estimate is based on."""
    parts = []
    if doccom_url:
        parts.append(f"Comparison: [Original and updated]({doccom_url})")
    if details:
        parts.append("The document has been updated with:\n" + details.replace("• ", "- "))
    if estimate_lines:
        parts.append(f"Estimate ({duration(minutes or 0)}):\n" + "\n".join(f"- {line}" for line in estimate_lines))
    return "\n\n".join(parts)


def propose_tasks(fund: str, case_id: int, est: Estimate, f: Features, t: dict, doccom_url: str | None,
                  details: str = "") -> list[dict]:
    """Subtasks of the parent 'Update - <fund> - <item>' task; 'task' is the part after the item."""
    def why(prefixes):
        return [f"{d.label}: {d.why}" for d in est.drivers if d.label.startswith(prefixes)]

    tasks = [{"create": True, "task": "PDF Annotation", "minutes": t["annotation"],
              "description": task_description(doccom_url, details, why(ANNOTATION_DRIVERS), t["annotation"])}]
    if cl_ui_text(t):
        tasks.append({"create": True, "task": "CL & UI Fix", "minutes": t["cl"],
                      "description": task_description(doccom_url, details, why(CL_UI_DRIVERS), t["cl"])})
    return tasks


# ---------- Slack destination: one channel per client, one thread per fund ----------

def default_channel(fund: str) -> str:
    """'Maple Ridge Growth Fund II' -> 'cs-maple-ridge' (first two words; the reviewer can change it)."""
    words = re.findall(r"[a-z0-9]+", (fund or "").lower())
    return "cs-" + "-".join(words[:2]) if words else ""


def clickup_context(fund: str, cfg: dict) -> dict:
    """Read-only look at the fund's ClickUp list: the next item number and fund-level fields to copy
    from its latest update. Empty when ClickUp isn't connected or the fund's list isn't found."""
    tracker, real = get_tracker()
    if not real:
        return {}
    list_id = tracker.find_fund_list(cfg["space_id"], fund)
    if not list_id:
        return {"list_found": False}
    tasks = tracker.list_tasks(list_id)
    latest_update = next((t for t in tasks if t["name"].lower().startswith("update")), None)
    return {"list_found": True, "next_item": next_item([t["name"] for t in tasks]),
            "previous": from_clickup(latest_update, cfg, cfg["parent"]["copy_from_previous"]) if latest_update else {}}


def get_tracker() -> tuple[TaskTracker, bool]:
    """(tracker, is_real). Real ClickUp only with a token AND a test list."""
    if settings.CLICKUP_API_TOKEN and settings.CLICKUP_TEST_LIST_ID:
        return ClickUpTracker(settings.CLICKUP_API_TOKEN, settings.CLICKUP_TEST_LIST_ID), True
    return MockTracker(), False


def get_notifier() -> tuple[Notifier, bool]:
    if settings.SLACK_BOT_TOKEN and settings.SLACK_TEST_CHANNEL:
        return SlackNotifier(settings.SLACK_BOT_TOKEN), True
    return MockNotifier(), False


# ---------- Approve / reject ----------

def already_sent(conn, case_id: int) -> list:
    return conn.execute("SELECT * FROM review_decisions WHERE case_id = ? AND item_type IN ('task', 'slack')"
                        " AND decision = 'sent' ORDER BY id", (case_id,)).fetchall()


def approve(conn, case_id: int, fund: str, item: str, tasks: list[dict], message: str, channel: str, est: Estimate,
            reviewer: str | None, tracker: TaskTracker | None = None, notifier: Notifier | None = None,
            doccom_url: str | None = None, parent_fields: list[dict] | None = None,
            subtask_fields: list[dict] | None = None, details: str = "") -> list[dict]:
    """Create 'Update - <fund> - <item>' with the ticked subtasks, and post the update in the fund's thread."""
    if already_sent(conn, case_id):
        raise RuntimeError("Already sent.")
    if not item.strip():
        raise ValueError("Add the item number first (e.g. 'Item 3').")
    if tracker and notifier:
        real_t = real_n = True
    else:
        (tracker, real_t), (notifier, real_n) = get_tracker(), get_notifier()

    # Until the manager signs off, real posts go to the test channel (thread per fund kept there too)
    target = settings.SLACK_TEST_CHANNEL if real_n and settings.SLACK_TEST_CHANNEL else channel
    if target != channel:
        message = f"_(test: would go to #{channel})_\n" + message

    db.add_estimate(conn, case_id, est.low_s, est.high_s, [vars(d) for d in est.drivers], est.playbook)
    sent = []
    chosen = [t for t in tasks if t.get("create")]
    if chosen:
        title = parent_title(fund, item)
        parent = tracker.create_task(title, task_description(doccom_url, details), 0,
                                     custom_fields=parent_fields)
        if real_t and parent.link.startswith("http"):
            db.set_clickup_task(conn, case_id, parent.link)  # to read actual hours later
        sent.append(_record(conn, case_id, "task", f"ClickUp: {title}", parent.link, not real_t, reviewer))
        for t in chosen:
            sub = f"{title} - {t['task']}"
            made = tracker.create_task(sub, t.get("description", ""), int(t.get("minutes") or 0), parent.task_id,
                                       custom_fields=subtask_fields)
            sent.append(_record(conn, case_id, "task", f"ClickUp: {sub}", made.link, not real_t, reviewer))
    if message.strip():
        thread = db.get_slack_thread(conn, target, fund)
        posted = notifier.post(message, target, thread)
        if not thread:
            db.save_slack_thread(conn, target, fund, posted.thread_ts)
        sent.append(_record(conn, case_id, "slack", f"Slack: #{target}", posted.link, not real_n, reviewer))
    db.add_review_decision(conn, case_id, "case", case_id, "approved", None, reviewer)
    set_status(conn, case_id, "approved")
    return sent


def _record(conn, case_id, item_type, what, link, pretend, reviewer) -> dict:
    record = {"what": what, "link": link, "pretend": pretend}
    db.add_review_decision(conn, case_id, item_type, None, "sent", record, reviewer)
    return record


def reject(conn, case_id: int, reason: str, reviewer: str | None):
    db.add_review_decision(conn, case_id, "case", case_id, "rejected", {"reason": reason}, reviewer)
    set_status(conn, case_id, "rejected")


def set_status(conn, case_id: int, status: str):
    conn.execute("UPDATE cases SET status = ? WHERE id = ?", (status, case_id))
    conn.commit()
