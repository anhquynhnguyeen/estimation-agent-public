"""Estimates vs actual (Day 5): read the real time from ClickUp and compare it with the agent's estimate.

Actual time = "[PIC] Actual Working Hours" on the parent's PDF Annotation and CL & UI Fix subtasks
(read-only; the app never writes this field).
"""

import re

from src.clickup_fields import load_config

TASK_ID = re.compile(r"/t/(?:\d+/)?([0-9a-z]+)")

# Subtask name endings -> our work types
WORK_TYPES = {
    "pdf_annotation": ("pdf annotation",),
    "cl_ui": ("cl & ui fix", "cl&ui fix", "cl and ui fix", "ui fix", "cl fix", "ui + checklist"),
}


def task_id_from_url(url: str) -> str | None:
    """'https://app.clickup.com/t/86d4ccpzr' or '.../t/1234567/86d4ccpzr' or a bare id -> '86d4ccpzr'."""
    url = (url or "").strip()
    m = TASK_ID.search(url)
    if m:
        return m.group(1)
    return url if re.fullmatch(r"[0-9a-z]{6,}", url) else None


def work_type_of(name: str) -> str | None:
    tail = name.lower().rsplit(" - ", 1)[-1].strip()
    for work_type, endings in WORK_TYPES.items():
        if any(tail.endswith(e) for e in endings):
            return work_type
    return None


def actual_hours_field(task: dict, field_id: str) -> float | None:
    for cf in task.get("custom_fields", []):
        if cf.get("id") == field_id and cf.get("value") not in (None, ""):
            try:
                return float(cf["value"])
            except (TypeError, ValueError):
                return None
    return None


def actual_minutes_from_clickup(tracker, task_url: str) -> dict:
    """{'pdf_annotation': minutes, 'cl_ui': minutes} from the parent's subtasks (only those with hours filled)."""
    task_id = task_id_from_url(task_url)
    if not task_id:
        raise ValueError("That doesn't look like a ClickUp task link.")
    field_id = load_config()["fields"]["actual_working_hours"]["id"]
    parent = tracker.get_task(task_id, subtasks=True)
    totals: dict[str, float] = {}
    for sub in parent.get("subtasks", []):
        work_type = work_type_of(sub.get("name", ""))
        if not work_type:
            continue
        hours = actual_hours_field(sub, field_id)
        if hours is None:  # subtask list may come without custom fields: read the subtask itself
            hours = actual_hours_field(tracker.get_task(sub["id"]), field_id)
        if hours is not None:
            totals[work_type] = totals.get(work_type, 0) + hours * 60
    return totals
