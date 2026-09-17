"""Per-application, on-disk checklist state - the actual mechanism behind
Rule 0b in APPLICATION_PLAYBOOK.md. Conversational memory (and even a whole
turn's worth of stated intentions) does not survive a context-compaction
event; a file does. The registry (cv-tailor-agent/private/registry.json) only
captures *finished* CVs/applications - this tracks the in-flight sub-steps
that were getting lost: CV status, placeholder-CV swap, current form step,
submission status.

One plain markdown file per application under private/checklists/, key:value
lines so both a human (Read tool) and this module (regex) can read/update it
without re-deriving state from a lossy conversation summary or from
re-reading live browser state (which can itself be stale).
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

IST = timezone(timedelta(hours=5, minutes=30))
CHECKLIST_DIR = Path(__file__).resolve().parent.parent.parent / "private" / "checklists"

FIELDS = [
    "CV status",
    "Placeholder CV uploaded",
    "Real CV swapped (location 1)",
    "Real CV swapped (location 2)",
    "Current form step",
    "Submission status",
]


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")


def checklist_path(company: str, job_title: str) -> Path:
    return CHECKLIST_DIR / f"{_slug(company)}_{_slug(job_title)}.md"


def _now() -> str:
    return datetime.now(IST).strftime("%Y-%m-%d %H:%M IST")


def start_checklist(company: str, job_title: str, channel: str = "unknown") -> Path:
    """Create the checklist file. If it already exists, this is a no-op that
    just returns the existing path - never overwrite in-flight state."""
    CHECKLIST_DIR.mkdir(parents=True, exist_ok=True)
    path = checklist_path(company, job_title)
    if path.exists():
        return path

    now = _now()
    lines = [
        f"# Application Checklist: {company} - {job_title}",
        "",
        f"- Started: {now}",
        f"- Channel: {channel}",
        "- CV status: not_started",
        "- Placeholder CV uploaded: N",
        "- Real CV swapped (location 1): N",
        "- Real CV swapped (location 2): N",
        "- Current form step: not_started",
        "- Submission status: not_submitted",
        f"- Last updated: {now}",
        "",
        "## Notes",
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def update_checklist(company: str, job_title: str, **updates: str) -> Path:
    """Update one or more fields by keyword, e.g.
    update_checklist("PwC", "SAP FSCM - Senior Associate", cv_status="frozen_at_87.5",
                      placeholder_cv_uploaded="Y")
    Keyword names map to field labels by replacing "_" with " " and matching
    case-insensitively against FIELDS (and Channel). Raises if the checklist
    doesn't exist yet - call start_checklist() first.
    """
    path = checklist_path(company, job_title)
    if not path.exists():
        raise FileNotFoundError(f"No checklist for ({company!r}, {job_title!r}) - call start_checklist first: {path}")

    text = path.read_text(encoding="utf-8")
    known_labels = FIELDS + ["Channel"]
    label_by_key = {_slug(label): label for label in known_labels}

    for key, value in updates.items():
        label = label_by_key.get(_slug(key))
        if label is None:
            raise ValueError(f"Unknown checklist field {key!r}. Known fields: {list(updates)}")
        pattern = re.compile(rf"^- {re.escape(label)}: .*$", re.MULTILINE)
        replacement = f"- {label}: {value}"
        if pattern.search(text):
            text = pattern.sub(replacement, text)
        else:
            text = text.rstrip("\n") + f"\n{replacement}"

    text = re.sub(r"^- Last updated: .*$", f"- Last updated: {_now()}", text, flags=re.MULTILINE)
    path.write_text(text, encoding="utf-8")
    return path


def append_note(company: str, job_title: str, note: str) -> Path:
    """Append a timestamped, freeform line under ## Notes - for anything that
    doesn't fit the fixed fields (a blocker hit, a live user takeover, a bug
    worked around)."""
    path = checklist_path(company, job_title)
    if not path.exists():
        raise FileNotFoundError(f"No checklist for ({company!r}, {job_title!r}) - call start_checklist first: {path}")

    text = path.read_text(encoding="utf-8")
    entry = f"- {_now()}: {note}"
    if "## Notes" in text:
        text = text.rstrip("\n") + f"\n{entry}\n"
    else:
        text = text.rstrip("\n") + f"\n\n## Notes\n\n{entry}\n"

    text = re.sub(r"^- Last updated: .*$", f"- Last updated: {_now()}", text, flags=re.MULTILINE)
    path.write_text(text, encoding="utf-8")
    return path


def read_checklist(company: str, job_title: str) -> str:
    path = checklist_path(company, job_title)
    if not path.exists():
        raise FileNotFoundError(f"No checklist for ({company!r}, {job_title!r}): {path}")
    return path.read_text(encoding="utf-8")


def list_checklists() -> list[Path]:
    if not CHECKLIST_DIR.exists():
        return []
    return sorted(CHECKLIST_DIR.glob("*.md"), key=lambda p: p.stat().st_mtime, reverse=True)
