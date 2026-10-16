"""ClickUp item numbers and custom fields (made-up names only).

Run:  .venv/bin/python tests/test_clickup_fields.py
"""

import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src import clickup_fields as cf  # noqa: E402

CFG = cf.load_config()


def test_next_item_handles_the_ways_teams_write_items():
    names = ["Update - Maple Ridge Growth Fund II - Items 49, 50", "Update - Maple Ridge Growth Fund II - Item 48",
             "Update - Maple Ridge Growth Fund II - Item 46 & 47", "Update - Maple Ridge Growth Fund II - Items 42-43",
             "Update - Maple Ridge Growth Fund II- Items 34 & 40", "Update - Maple Ridge Growth Fund II - Item 26-39",
             "Import Update - Maple Ridge Growth Fund II - Custom Import - Item 21",
             "Validation Update - Maple Ridge Growth Fund II", "Maple Ridge Growth Fund II"]
    assert cf.next_item(names) == "Item 51"
    assert cf.next_item(["Update - Fund - Items 2 to 5"]) == "Item 6"
    assert cf.next_item([]) == "Item 1"


def test_parent_values_and_clickup_format():
    received = datetime(2026, 10, 12, tzinfo=timezone.utc)
    values = cf.parent_values(CFG, previous=None, doc_type="Updated", received=received, front_id="cnv_123",
                              fields_changed=True)
    assert values["engagement_type"] == "Update - Customer Request"
    assert values["duplicable"] == "Yes - Partially" and "complexity_level" not in values   # left empty
    assert values["service"] == "Digitization - EO"                      # default when nothing to copy
    api = {f["id"]: f for f in cf.to_clickup(values, CFG)}
    fields = CFG["fields"]
    assert api[fields["engagement_type"]["id"]]["value"] == [fields["engagement_type"]["options"]["Update - Customer Request"]]
    assert api[fields["doc_received_date"]["id"]]["value"] == int(received.timestamp() * 1000)
    assert api[fields["front_id"]["id"]]["value"] == "cnv_123"
    assert api[fields["zero_request"]["id"]]["value"] is True
    picked = cf.parent_values(CFG, previous=None, doc_type="Updated", received=None, front_id=None,
                              fields_changed=True, complexity="Medium")
    assert picked["complexity_level"] == "Medium"


def test_unknown_option_is_skipped_not_guessed():
    assert cf.to_clickup({"doc_type_received": "Something else"}, CFG) == []


def test_copy_from_previous_task():
    f = CFG["fields"]["process_version"]
    task = {"custom_fields": [{"id": f["id"], "value": 4,
                               "type_config": {"options": [{"id": f["options"]["V3 - Blueprint"], "name": "V3 - Blueprint",
                                                            "orderindex": 4}]}}]}
    previous = cf.from_clickup(task, CFG, ["process_version", "service"])
    assert previous == {"process_version": "V3 - Blueprint"}
    values = cf.parent_values(CFG, previous=previous, doc_type="Final", received=None, front_id=None,
                              fields_changed=False)
    assert values["process_version"] == "V3 - Blueprint" and values["duplicable"] == "Yes - Full"


def test_subtask_values():
    api = cf.to_clickup(cf.subtask_values(CFG), CFG)
    role = CFG["fields"]["pic_role"]
    assert {"id": role["id"], "value": role["options"]["DS"]} in api


if __name__ == "__main__":
    failed = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS  {name}")
            except AssertionError as e:
                failed += 1
                print(f"FAIL  {name}: {e}")
    sys.exit(1 if failed else 0)
