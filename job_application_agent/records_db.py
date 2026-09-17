"""Durable, queryable record of every email/form application's actual content -
distinct from the registry (which tracks status/scores) and the scratch .txt
files under private/applications/ (which are working drafts, not a retrieval
system). SQLite: local, zero-config, no server, stdlib-only.

Each record stores every field/answer that actually went out for an
application (email body, or every Q&A pair from a form) as a JSON blob, so
"what did I tell this company" is always answerable later without digging
through scattered draft files.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

IST = timezone(timedelta(hours=5, minutes=30))
DEFAULT_DB_PATH = Path(__file__).resolve().parent.parent.parent / "private" / "applications.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS application_records (
    company TEXT NOT NULL,
    job_title TEXT NOT NULL,
    channel TEXT NOT NULL,          -- 'email' | 'google_form' | 'portal_form' | 'linkedin_dm' | ...
    content_json TEXT NOT NULL,     -- {"field_name": "value", ...} - every field/answer sent
    submitted_at TEXT,              -- IST timestamp string, NULL if drafted but not yet sent
    created_at TEXT NOT NULL,
    PRIMARY KEY (company, job_title)
);
"""


def _connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute(SCHEMA)
    return conn


def save_record(
    company: str,
    job_title: str,
    channel: str,
    content: dict[str, str],
    submitted_at: str | None = None,
    db_path: Path = DEFAULT_DB_PATH,
) -> None:
    """Upsert - re-saving the same (company, job_title) replaces the prior
    record rather than duplicating it, since there's one real application per
    job, same rule as the CV registry."""
    conn = _connect(db_path)
    try:
        conn.execute(
            """INSERT INTO application_records (company, job_title, channel, content_json, submitted_at, created_at)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(company, job_title) DO UPDATE SET
                   channel=excluded.channel,
                   content_json=excluded.content_json,
                   submitted_at=excluded.submitted_at""",
            (company, job_title, channel, json.dumps(content, ensure_ascii=False),
             submitted_at, datetime.now(IST).strftime("%Y-%m-%d %H:%M IST")),
        )
        conn.commit()
    finally:
        conn.close()


def get_record(company: str, job_title: str, db_path: Path = DEFAULT_DB_PATH) -> dict | None:
    conn = _connect(db_path)
    try:
        row = conn.execute(
            "SELECT company, job_title, channel, content_json, submitted_at, created_at "
            "FROM application_records WHERE company = ? AND job_title = ?",
            (company, job_title),
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        return None
    return {
        "company": row[0],
        "job_title": row[1],
        "channel": row[2],
        "content": json.loads(row[3]),
        "submitted_at": row[4],
        "created_at": row[5],
    }


def list_records(db_path: Path = DEFAULT_DB_PATH) -> list[dict]:
    conn = _connect(db_path)
    try:
        rows = conn.execute(
            "SELECT company, job_title, channel, content_json, submitted_at, created_at "
            "FROM application_records ORDER BY created_at DESC"
        ).fetchall()
    finally:
        conn.close()
    return [
        {
            "company": r[0], "job_title": r[1], "channel": r[2],
            "content": json.loads(r[3]), "submitted_at": r[4], "created_at": r[5],
        }
        for r in rows
    ]
