"""ClickUp: create tasks after a human approves. Writes ONLY to the test list (CLICKUP_TEST_LIST_ID)
until the manager signs off. Reading the fund's real list (for the next item number and fund-level
fields) is read-only."""

import requests

from src.integrations.base import Created, TaskTracker

API = "https://api.clickup.com/api/v2"


class ClickUpTracker(TaskTracker):
    def __init__(self, token: str, list_id: str):
        self.list_id = list_id
        self.headers = {"Authorization": token, "Content-Type": "application/json"}

    def create_task(self, title, description, estimate_minutes, parent_id=None, custom_fields=None):
        body = {"name": title, "markdown_description": description}
        if parent_id:
            body["parent"] = parent_id  # subtask
        if estimate_minutes:
            body["time_estimate"] = int(estimate_minutes) * 60 * 1000  # ClickUp's "Time estimate", in milliseconds
        if custom_fields:
            body["custom_fields"] = custom_fields
        r = requests.post(f"{API}/list/{self.list_id}/task", headers=self.headers, json=body, timeout=30)
        r.raise_for_status()
        task = r.json()
        return Created(task.get("url") or task["id"], task["id"])

    # ---------- read-only ----------

    def find_fund_list(self, space_id: str, fund: str) -> str | None:
        """The list named like the fund in the Digitization Squad space (folder = client, list = fund)."""
        r = requests.get(f"{API}/space/{space_id}/folder", headers=self.headers, params={"archived": "false"}, timeout=30)
        r.raise_for_status()
        lists = [lst for folder in r.json().get("folders", []) for lst in folder.get("lists", [])]
        target = fund.strip().lower()
        exact = [lst for lst in lists if lst["name"].strip().lower() == target]
        close = [lst for lst in lists if target in lst["name"].lower() or lst["name"].lower() in target]
        match = exact or close
        return match[0]["id"] if len(match) == 1 or exact else None

    def get_task(self, task_id: str, subtasks: bool = False) -> dict:
        r = requests.get(f"{API}/task/{task_id}", headers=self.headers, timeout=30,
                         params={"include_subtasks": "true" if subtasks else "false"})
        r.raise_for_status()
        return r.json()

    def list_tasks(self, list_id: str) -> list[dict]:
        """Top-level tasks (not subtasks) of a list, newest first, with custom fields."""
        tasks, page = [], 0
        while True:
            r = requests.get(f"{API}/list/{list_id}/task", headers=self.headers, timeout=30,
                             params={"include_closed": "true", "subtasks": "false", "page": page,
                                     "order_by": "created"})
            r.raise_for_status()
            data = r.json()
            tasks += data.get("tasks", [])
            if data.get("last_page", True):
                return tasks
            page += 1
