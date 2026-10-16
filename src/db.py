"""The SQLite database: one file (data/app.db) that stores every case and its results."""

import json
import sqlite3
from datetime import datetime, timezone

from src.settings import DB_PATH

# Each table, in plain words:
#   cases            one row per client reply we are processing
#   messages         emails in the conversation (ours and the client's)
#   attachments      files: v1 PDF, v2 PDF, annotation export, extra documents
#   analyses         AI results (email analysis, change classification), with confidence
#   changes          each change found between v1 and v2, with category and evidence
#   estimates        computed time range and what drove it
#   review_decisions what the reviewer approved / edited / rejected
#   actual_time      real time spent, to compare with estimates later
SCHEMA = """
CREATE TABLE IF NOT EXISTS cases (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    source TEXT NOT NULL,                 -- 'front' or 'manual'
    front_conversation_id TEXT,
    fund_name TEXT,
    subject TEXT,
    data_mode TEXT NOT NULL,              -- 'synthetic' or 'real'
    status TEXT NOT NULL DEFAULT 'new'    -- new / analyzed / in_review / approved / rejected
);

CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    case_id INTEGER NOT NULL REFERENCES cases(id),
    front_message_id TEXT,
    direction TEXT NOT NULL,              -- 'inbound' (client) or 'outbound' (us)
    author TEXT,
    sent_at TEXT,
    subject TEXT,
    body_text TEXT
);

CREATE TABLE IF NOT EXISTS attachments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    case_id INTEGER NOT NULL REFERENCES cases(id),
    message_id INTEGER REFERENCES messages(id),
    filename TEXT NOT NULL,
    role TEXT NOT NULL,                   -- 'v1', 'v2', 'annotation_export', 'other'
    path TEXT NOT NULL,
    size_bytes INTEGER
);

CREATE TABLE IF NOT EXISTS analyses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    case_id INTEGER NOT NULL REFERENCES cases(id),
    step TEXT NOT NULL,                   -- e.g. 'email_analysis', 'change_classification'
    output_json TEXT NOT NULL,
    confidence REAL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS changes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    case_id INTEGER NOT NULL REFERENCES cases(id),
    page_v1 INTEGER,
    page_v2 INTEGER,
    category TEXT,                        -- text_only / reflow / fields_changed / logic
    fields_moved INTEGER DEFAULT 0,
    fields_added INTEGER DEFAULT 0,
    fields_removed INTEGER DEFAULT 0,
    fields_reworded INTEGER DEFAULT 0,
    evidence TEXT,                        -- quoted PDF text
    reasoning TEXT,
    confidence REAL
);

CREATE TABLE IF NOT EXISTS estimates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    case_id INTEGER NOT NULL REFERENCES cases(id),
    low_seconds INTEGER NOT NULL,
    high_seconds INTEGER NOT NULL,
    drivers_json TEXT NOT NULL,           -- list of "what added time" lines
    playbook_json TEXT NOT NULL,          -- copy of the numbers used, for later checking
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS review_decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    case_id INTEGER NOT NULL REFERENCES cases(id),
    item_type TEXT NOT NULL,              -- 'issue', 'change', 'estimate', 'task'
    item_id INTEGER,
    decision TEXT NOT NULL,               -- 'approved', 'edited', 'rejected'
    edited_json TEXT,
    reviewer TEXT,
    created_at TEXT NOT NULL
);

-- Estimates vs actual (Day 5): the team's own estimate and the real time, per work type, in minutes
CREATE TABLE IF NOT EXISTS reference_times (
    case_id INTEGER NOT NULL REFERENCES cases(id),
    kind TEXT NOT NULL,                   -- 'team' (team's estimate) or 'actual' (real time spent)
    work_type TEXT NOT NULL,              -- 'pdf_annotation' or 'cl_ui'
    minutes REAL,
    source TEXT,                          -- 'manual' or 'clickup'
    updated_at TEXT NOT NULL,
    PRIMARY KEY (case_id, kind, work_type)
);

-- The ClickUp parent task of a case ("Update - <fund> - <item>"), to read actual hours from
CREATE TABLE IF NOT EXISTS case_clickup (
    case_id INTEGER PRIMARY KEY REFERENCES cases(id),
    task_url TEXT NOT NULL
);

-- One Slack thread per fund inside the client's channel (e.g. #cs-maple-ridge)
CREATE TABLE IF NOT EXISTS slack_threads (
    channel TEXT NOT NULL,
    fund_name TEXT NOT NULL,
    thread_ts TEXT NOT NULL,              -- Slack's id of the thread's first message
    PRIMARY KEY (channel, fund_name)
);

CREATE TABLE IF NOT EXISTS actual_time (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    case_id INTEGER NOT NULL REFERENCES cases(id),
    work_type TEXT NOT NULL,              -- 'pdf_annotation', 'ui', 'checklist'
    seconds INTEGER NOT NULL,
    logged_by TEXT,
    created_at TEXT NOT NULL
);
"""


def now() -> str:
    """Current time as text, e.g. '2026-10-12T09:30:00+00:00'."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect() -> sqlite3.Connection:
    """Open the database (creates the file and tables the first time)."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row  # lets us read columns by name: row["subject"]
    conn.executescript(SCHEMA)
    return conn


def create_case(conn, source, data_mode, subject=None, fund_name=None, front_conversation_id=None) -> int:
    """Add a new case and return its id number."""
    cur = conn.execute(
        "INSERT INTO cases (created_at, source, front_conversation_id, fund_name, subject, data_mode)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (now(), source, front_conversation_id, fund_name, subject, data_mode),
    )
    conn.commit()
    return cur.lastrowid


def add_message(conn, case_id, direction, body_text, author=None, sent_at=None, subject=None, front_message_id=None) -> int:
    cur = conn.execute(
        "INSERT INTO messages (case_id, front_message_id, direction, author, sent_at, subject, body_text)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        (case_id, front_message_id, direction, author, sent_at, subject, body_text),
    )
    conn.commit()
    return cur.lastrowid


def add_attachment(conn, case_id, filename, role, path, size_bytes=None, message_id=None) -> int:
    cur = conn.execute(
        "INSERT INTO attachments (case_id, message_id, filename, role, path, size_bytes)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (case_id, message_id, filename, role, str(path), size_bytes),
    )
    conn.commit()
    return cur.lastrowid


def add_review_decision(conn, case_id, item_type, item_id, decision, edited=None, reviewer=None) -> int:
    cur = conn.execute(
        "INSERT INTO review_decisions (case_id, item_type, item_id, decision, edited_json, reviewer, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        (case_id, item_type, item_id, decision, to_json(edited) if edited is not None else None, reviewer, now()),
    )
    conn.commit()
    return cur.lastrowid


def latest_review_decisions(conn, case_id, item_type):
    """The newest decision per item (older ones stay in the table as history)."""
    return conn.execute(
        "SELECT * FROM review_decisions WHERE id IN (SELECT MAX(id) FROM review_decisions"
        " WHERE case_id = ? AND item_type = ? GROUP BY item_id)", (case_id, item_type)).fetchall()


def add_analysis(conn, case_id, step, output, confidence=None) -> int:
    cur = conn.execute(
        "INSERT INTO analyses (case_id, step, output_json, confidence, created_at) VALUES (?, ?, ?, ?, ?)",
        (case_id, step, to_json(output), confidence, now()),
    )
    conn.commit()
    return cur.lastrowid


def latest_analysis(conn, case_id, step) -> dict | None:
    row = conn.execute("SELECT output_json FROM analyses WHERE case_id = ? AND step = ? ORDER BY id DESC LIMIT 1",
                       (case_id, step)).fetchone()
    return json.loads(row["output_json"]) if row else None


def add_estimate(conn, case_id, low_seconds, high_seconds, drivers, playbook) -> int:
    cur = conn.execute(
        "INSERT INTO estimates (case_id, low_seconds, high_seconds, drivers_json, playbook_json, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (case_id, round(low_seconds), round(high_seconds), to_json(drivers), to_json(playbook), now()),
    )
    conn.commit()
    return cur.lastrowid


def get_slack_thread(conn, channel, fund_name) -> str | None:
    row = conn.execute("SELECT thread_ts FROM slack_threads WHERE channel = ? AND fund_name = ?",
                       (channel, fund_name)).fetchone()
    return row["thread_ts"] if row else None


def save_slack_thread(conn, channel, fund_name, thread_ts):
    conn.execute("INSERT OR REPLACE INTO slack_threads (channel, fund_name, thread_ts) VALUES (?, ?, ?)",
                 (channel, fund_name, thread_ts))
    conn.commit()


def set_reference_time(conn, case_id, kind, work_type, minutes, source="manual"):
    conn.execute("INSERT OR REPLACE INTO reference_times (case_id, kind, work_type, minutes, source, updated_at)"
                 " VALUES (?, ?, ?, ?, ?, ?)", (case_id, kind, work_type, minutes, source, now()))
    conn.commit()


def reference_times(conn, case_id) -> dict:
    """{(kind, work_type): minutes}"""
    rows = conn.execute("SELECT kind, work_type, minutes FROM reference_times WHERE case_id = ?", (case_id,))
    return {(r["kind"], r["work_type"]): r["minutes"] for r in rows}


def set_clickup_task(conn, case_id, task_url):
    conn.execute("INSERT OR REPLACE INTO case_clickup (case_id, task_url) VALUES (?, ?)", (case_id, task_url))
    conn.commit()


def get_clickup_task(conn, case_id) -> str | None:
    row = conn.execute("SELECT task_url FROM case_clickup WHERE case_id = ?", (case_id,)).fetchone()
    return row["task_url"] if row else None


def list_cases(conn):
    return conn.execute("SELECT * FROM cases ORDER BY id DESC").fetchall()


def get_case_details(conn, case_id):
    """Everything about one case: the case row, its emails and its files."""
    case = conn.execute("SELECT * FROM cases WHERE id = ?", (case_id,)).fetchone()
    messages = conn.execute("SELECT * FROM messages WHERE case_id = ? ORDER BY sent_at, id", (case_id,)).fetchall()
    files = conn.execute("SELECT * FROM attachments WHERE case_id = ? ORDER BY id", (case_id,)).fetchall()
    return case, messages, files


def to_json(value) -> str:
    return json.dumps(value, ensure_ascii=False)


if __name__ == "__main__":
    # Run "python -m src.db" to create the database and see the tables.
    c = connect()
    tables = [r["name"] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
    print(f"Database ready at {DB_PATH}")
    print("Tables:", ", ".join(tables))
