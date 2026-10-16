"""Pretend ClickUp and Slack: they just remember what would have been sent.
Used until the real ones are connected (Day 4), and in demos/tests."""

from src.integrations.base import Created, Notifier, Posted, TaskTracker


class MockTracker(TaskTracker):
    def __init__(self):
        self.tasks = []

    def create_task(self, title, description, estimate_minutes, parent_id=None, custom_fields=None):
        self.tasks.append({"title": title, "description": description, "estimate_minutes": estimate_minutes,
                           "parent_id": parent_id, "custom_fields": custom_fields or []})
        task_id = f"mock-task-{len(self.tasks)}"
        return Created(f"(pretend) {title}", task_id)


class MockNotifier(Notifier):
    def __init__(self):
        self.posts = []

    def post(self, text, channel, thread_ts=None):
        self.posts.append({"text": text, "channel": channel, "thread_ts": thread_ts})
        ts = f"mock-{len(self.posts)}"
        return Posted(f"(pretend) #{channel}" + (" in thread" if thread_ts else " new thread"), thread_ts or ts)
