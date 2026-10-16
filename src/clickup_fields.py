"""ClickUp custom fields and item numbers for "Update - <fund> - <item>" tasks.

Rules live in config/clickup_fields.yaml (learned from the team's existing tasks).
"""

import re
from datetime import datetime, timezone
from pathlib import Path

import yaml

CONFIG = Path(__file__).resolve().parents[1] / "config" / "clickup_fields.yaml"
ITEM_NUMBERS = re.compile(r"\bItems?\s+([\d\s,&to-]+)", re.IGNORECASE)


def load_config(path: Path = CONFIG) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


# ---------- Next item number ----------

def next_item(task_names: list[str]) -> str:
    """Names of the tasks in the fund's list -> the next item, e.g. 'Item 51'.
    Item numbers run across the whole list, written many ways: 'Item 48', 'Items 49, 50', 'Item 46 & 47',
    'Items 42-43', 'Items 2 to 5', 'Item 17, 23, 24, 25'. Names without an item number are ignored."""
    highest = 0
    for name in task_names:
        for m in ITEM_NUMBERS.finditer(name):
            highest = max([highest, *map(int, re.findall(r"\d+", m.group(1)))])
    return f"Item {highest + 1}"


# ---------- Field values (readable names first, ClickUp format at the end) ----------

def complexity_level(total_hours: float, cfg: dict) -> str:
    for level, limit in cfg["complexity_hours"].items():
        if total_hours <= limit:
            return level
    return "Extreme Heavy"


def parent_values(cfg: dict, *, previous: dict | None, doc_type: str, received: datetime | None,
                  front_id: str | None, fields_changed: bool, complexity: str | None = None) -> dict:
    """Readable values for the parent task: {field key: value}. Complexity Level stays empty unless a
    reviewer picks one (it depends on the whole parent estimate, which other teams set)."""
    values = dict(cfg["parent"]["fixed"])
    for key, default in cfg["parent"]["copy_from_previous"].items():
        values[key] = (previous or {}).get(key) or default
    values["doc_type_received"] = doc_type
    values["duplicable"] = "Yes - Partially" if fields_changed else "Yes - Full"
    if complexity:
        values["complexity_level"] = complexity
    if received:
        values["doc_received_date"] = received
    if front_id:
        values["front_id"] = front_id
    return values


def subtask_values(cfg: dict) -> dict:
    return dict(cfg["subtask"]["fixed"])


def to_clickup(values: dict, cfg: dict) -> list[dict]:
    """Readable values -> the list ClickUp's API expects. Unknown options are skipped, never guessed."""
    out = []
    for key, value in values.items():
        field = cfg["fields"].get(key)
        if not field or value in (None, ""):
            continue
        kind, entry = field["type"], {"id": field["id"]}
        if kind == "drop_down":
            option = field["options"].get(value)
            if not option:
                continue
            entry["value"] = option
        elif kind == "labels":
            option = field["options"].get(value)
            if not option:
                continue
            entry["value"] = [option]
        elif kind == "date":
            entry["value"] = int(value.replace(tzinfo=value.tzinfo or timezone.utc).timestamp() * 1000)
            entry["value_options"] = {"time": False}
        elif kind == "checkbox":
            entry["value"] = bool(value)
        else:
            entry["value"] = str(value)
        out.append(entry)
    return out


def from_clickup(task: dict, cfg: dict, keys) -> dict:
    """Read readable values (for `keys`) from a ClickUp task, e.g. to copy from the fund's previous update."""
    by_id = {cf["id"]: cf for cf in task.get("custom_fields", [])}
    out = {}
    for key in keys:
        field = cfg["fields"].get(key)
        cf = by_id.get(field["id"]) if field else None
        if not cf or cf.get("value") in (None, "", []):
            continue
        names = {o.get("id"): o.get("name") or o.get("label") for o in cf.get("type_config", {}).get("options", [])}
        names |= {str(o.get("orderindex")): o.get("name") for o in cf.get("type_config", {}).get("options", [])}
        value = cf["value"]
        name = names.get(str(value[0] if isinstance(value, list) else value))
        if name in field.get("options", {}):
            out[key] = name
    return out


def parse_date(text: str | None) -> datetime | None:
    """Email dates: ISO text or epoch seconds (Front)."""
    if not text:
        return None
    try:
        return datetime.fromtimestamp(float(text), tz=timezone.utc)
    except ValueError:
        pass
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
