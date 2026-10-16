"""Slack: post the case update after a human approves.

Channels are per client (e.g. #cs-maple-ridge), with one thread per fund. Until the manager signs off,
review.py sends everything to the test channel instead (SLACK_TEST_CHANNEL)."""

import re

import requests

from src.integrations.base import Notifier, Posted

API = "https://slack.com/api"
PERMALINK_TS = re.compile(r"/p(\d{10})(\d{6})")


def thread_ts_from_link(link: str) -> str | None:
    """A Slack message link (.../archives/C123/p1700000000123456) -> its id '1700000000.123456'."""
    m = PERMALINK_TS.search(link or "")
    return f"{m.group(1)}.{m.group(2)}" if m else None


class SlackNotifier(Notifier):
    def __init__(self, bot_token: str):
        self.headers = {"Authorization": f"Bearer {bot_token}", "Content-Type": "application/json; charset=utf-8"}

    def post(self, text, channel, thread_ts=None):
        body = {"channel": channel, "text": text, "unfurl_links": False}
        if thread_ts:
            body["thread_ts"] = thread_ts
        data = self._call("chat.postMessage", body)
        link = requests.get(f"{API}/chat.getPermalink", headers=self.headers,
                            params={"channel": data["channel"], "message_ts": data["ts"]}, timeout=30).json()
        return Posted(link.get("permalink") or data["ts"], thread_ts or data["ts"])

    def _call(self, method, body):
        r = requests.post(f"{API}/{method}", headers=self.headers, json=body, timeout=30)
        r.raise_for_status()
        data = r.json()
        if not data.get("ok"):
            raise RuntimeError(f"Slack said: {data.get('error', 'unknown error')}")
        return data
