from __future__ import annotations

import json
import os
import time
from contextlib import contextmanager
from pathlib import Path

from cv_tailor_agent.contracts import CVRecord

DEFAULT_REGISTRY_PATH = Path("private/registry.json")


class RegistryLockTimeout(RuntimeError):
    pass


@contextmanager
def _locked(path: Path, timeout_s: float = 5.0):
    """Best-effort advisory lock so a second agent/process (e.g. the Job Application
    Agent running in a separate Claude Code window) can't interleave a write with
    this one. Not a distributed lock - just enough to stop two local writes landing
    within the same instant from corrupting or dropping each other's changes."""
    lock_path = path.with_suffix(path.suffix + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + timeout_s
    fd = None
    while fd is None:
        try:
            fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            if time.monotonic() > deadline:
                raise RegistryLockTimeout(
                    f"Could not acquire lock on {lock_path} within {timeout_s}s - "
                    "another process may be mid-write. Retry, don't force past this."
                )
            time.sleep(0.05)
    try:
        yield
    finally:
        os.close(fd)
        lock_path.unlink(missing_ok=True)


def load_registry(path: Path = DEFAULT_REGISTRY_PATH) -> list[CVRecord]:
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    return [CVRecord.model_validate(r) for r in data]


def save_registry(records: list[CVRecord], path: Path = DEFAULT_REGISTRY_PATH) -> None:
    """Atomic write: temp file + os.replace, so a concurrent reader always sees
    either the complete old file or the complete new one, never a partial write."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(
        json.dumps([r.model_dump() for r in records], indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    os.replace(tmp_path, path)


def upsert_record(new_record: CVRecord, path: Path = DEFAULT_REGISTRY_PATH) -> list[CVRecord]:
    """Enforces one CV per (company, job_title). If a prior record for the same
    job pointed at a different file, that old file is deleted - not left behind
    as a stray duplicate. Locked + re-reads fresh at write time, so a concurrent
    write from another process (e.g. the Job Application Agent) isn't silently
    dropped or overwritten."""
    with _locked(path):
        records = load_registry(path)  # re-read fresh, inside the lock, not a stale caller copy
        replaced = False
        result: list[CVRecord] = []
        for r in records:
            if r.company == new_record.company and r.job_title == new_record.job_title:
                if r.location != new_record.location:
                    old_path = Path(r.location)
                    if old_path.exists():
                        old_path.unlink()
                result.append(new_record)
                replaced = True
            else:
                result.append(r)
        if not replaced:
            result.append(new_record)
        save_registry(result, path)
        return result


def update_record_field(
    company: str, job_title: str, updates: dict, path: Path = DEFAULT_REGISTRY_PATH
) -> bool:
    """The safe way for the Job Application Agent (or anything else) to change a
    FEW fields on an existing record - e.g. application_status - without
    reconstructing and re-saving a whole CVRecord from a possibly-stale in-memory
    copy, which would silently revert any other field (like composite_score) that
    the CV tool updated in the meantime. Re-reads fresh under the lock. Returns
    False (no exception) if no matching record exists, so the caller can decide
    what that means rather than the registry guessing."""
    with _locked(path):
        records = load_registry(path)
        found = False
        result: list[CVRecord] = []
        for r in records:
            if r.company == company and r.job_title == job_title:
                data = r.model_dump()
                data.update(updates)
                result.append(CVRecord.model_validate(data))
                found = True
            else:
                result.append(r)
        if found:
            save_registry(result, path)
        return found
