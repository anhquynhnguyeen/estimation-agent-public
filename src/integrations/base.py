"""The common "shapes" every outside system must follow.

Because Front and manual paste both return the same EmailThread, the rest of the app
doesn't care where an email came from. Same idea for ClickUp/Slack vs. their mocks.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Attachment:
    filename: str
    path: Path                 # where the file is saved on disk
    size_bytes: int = 0
    content_type: str = ""


@dataclass
class EmailMessage:
    direction: str             # "inbound" = from client, "outbound" = from us
    body_text: str
    author: str = ""
    sent_at: str = ""          # time as text
    subject: str = ""
    message_id: str = ""       # Front's id, empty for manual
    attachments: list[Attachment] = field(default_factory=list)


@dataclass
class EmailThread:
    """A whole conversation: our earlier email(s) listing issues + the client's reply."""
    subject: str
    messages: list[EmailMessage]
    conversation_id: str = ""

    def latest_client_reply(self):
        inbound = [m for m in self.messages if m.direction == "inbound"]
        return inbound[-1] if inbound else None

    def our_earlier_emails(self):
        """Our emails sent BEFORE the latest client reply: this is where the issue list is."""
        reply = self.latest_client_reply()
        if reply is None:
            return [m for m in self.messages if m.direction == "outbound"]
        cutoff = self.messages.index(reply)
        return [m for m in self.messages[:cutoff] if m.direction == "outbound"]


@dataclass
class ConversationSummary:
    """One row in the "pick a conversation" list."""
    conversation_id: str
    subject: str
    last_message_at: str = ""
    status: str = ""


class EmailSource(ABC):
    @abstractmethod
    def list_recent(self, limit: int = 25) -> list[ConversationSummary]: ...

    @abstractmethod
    def fetch_thread(self, conversation_id: str, save_dir: Path) -> EmailThread:
        """Get all messages and download attachments into save_dir."""


@dataclass
class Created:
    link: str
    task_id: str


class TaskTracker(ABC):
    @abstractmethod
    def create_task(self, title: str, description: str, estimate_minutes: int,
                    parent_id: str | None = None, custom_fields: list[dict] | None = None) -> Created:
        """Create a task (a subtask when parent_id is given), with ClickUp-format custom fields."""


@dataclass
class Posted:
    link: str
    thread_ts: str             # the thread the message is in (its own id if it started the thread)


class Notifier(ABC):
    @abstractmethod
    def post(self, text: str, channel: str, thread_ts: str | None = None) -> Posted:
        """Post a message in a channel, inside a thread if thread_ts is given."""
