from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import typer

from cv_tailor_agent.contracts import CVRecord
from cv_tailor_agent.dashboard import generate_dashboard
from cv_tailor_agent.registry import load_registry, update_record_field
from job_application_agent.checklist import (
    append_note,
    list_checklists,
    read_checklist,
    start_checklist,
    update_checklist,
)
from job_application_agent.guardrails import check_content
from job_application_agent.patterns import classify_application_pattern
from job_application_agent.records_db import save_record
from job_application_agent.validation import validate_email_application

app = typer.Typer()
IST = timezone(timedelta(hours=5, minutes=30))

# These four repeated boilerplate steps (classify -> guardrails -> source
# validation -> mark-sent-with-IST-timestamp) were being hand-retyped as fresh
# inline Python for every single application (Qapita, Rovia, ANSR, GoNanny,
# ShopDeck all repeated this by hand). Consolidating them here doesn't change
# what gets checked or skip any step - it just removes the chance of a typo'd
# path or a forgotten check (e.g. the earlier relative-vs-absolute registry
# path bug that silently no-op'd an update) by making the correct call the
# only call.


def _find_record(company: str, job_title: str, registry_path: Path) -> CVRecord:
    records = load_registry(registry_path)
    for r in records:
        if r.company == company and r.job_title == job_title:
            return r
    raise typer.BadParameter(f"No record found for ({company!r}, {job_title!r}) in {registry_path}")


@app.command()
def classify(
    company: str,
    job_title: str,
    registry: Path = typer.Option(Path("../cv-tailor-agent/private/registry.json")),
):
    """Classify the application pattern for an already-registered job."""
    record = _find_record(company, job_title, registry)
    target = classify_application_pattern(record.jd_text, record.job_post_url)
    typer.echo(target.model_dump_json(indent=2))


@app.command()
def check_draft(
    company: str,
    job_title: str,
    draft: Path,
    linkedin_url: str = typer.Option(..., help="Candidate's LinkedIn profile URL, used for source validation"),
    registry: Path = typer.Option(Path("../cv-tailor-agent/private/registry.json")),
):
    """Run content guardrails + source-JD validation on a draft in one call."""
    record = _find_record(company, job_title, registry)
    text = draft.read_text(encoding="utf-8")

    guardrail_result = check_content(text)
    validation_result = validate_email_application(text, record, linkedin_url)

    all_passed = guardrail_result.passed and validation_result.passed
    typer.echo(json.dumps({
        "guardrails": guardrail_result.model_dump(),
        "source_validation": validation_result.model_dump(),
        "all_passed": all_passed,
    }, indent=2))
    if not all_passed:
        raise typer.Exit(code=1)


@app.command()
def mark_sent(
    company: str,
    job_title: str,
    thread_id: str = typer.Option(..., help="Gmail thread ID from the Sent search confirmation"),
    pattern: str = typer.Option("email", help="email | linkedin_dm | linkedin_easy_apply | portal_form"),
    registry: Path = typer.Option(Path("../cv-tailor-agent/private/registry.json")),
    dashboard: Path = typer.Option(Path("../cv-tailor-agent/private/dashboard.html")),
):
    """Mark a job as applied with an IST timestamp and Gmail evidence link, then regenerate the dashboard - the single call replacing the hand-written registry-update snippet repeated for every prior application."""
    submitted_at = datetime.now(IST).strftime("%Y-%m-%d %H:%M IST")
    gmail_link = f"https://mail.google.com/mail/u/0/#sent/{thread_id}"

    ok = update_record_field(company, job_title, {
        "application_status": "applied",
        "application_pattern": pattern,
        "cover_letter_status": "guardrails_passed",
        "submitted_at": submitted_at,
        "submission_evidence_url": gmail_link,
        "submission_evidence_note": "Sent email (Gmail)",
    }, path=registry)

    if not ok:
        typer.echo(f"ERROR: no record found for ({company!r}, {job_title!r}) - nothing updated.")
        raise typer.Exit(code=1)

    generate_dashboard(load_registry(registry), out_path=dashboard)
    typer.echo(f"Marked applied: {company} | {job_title} @ {submitted_at}")
    typer.echo(f"Evidence: {gmail_link}")


@app.command()
def record_content(
    company: str,
    job_title: str,
    channel: str,
    content_file: Path = typer.Option(..., help="JSON file: {\"field name\": \"value\", ...}"),
    submitted_at: str = typer.Option(None, help="IST timestamp string; omit if not yet sent"),
):
    """Save the full set of fields/answers that went out for an application
    into the durable, queryable record store - separate from the registry
    (status/scores) and the scratch .txt drafts (working files only)."""
    content = json.loads(content_file.read_text(encoding="utf-8"))
    save_record(company, job_title, channel, content, submitted_at=submitted_at)
    typer.echo(f"Recorded content for {company} | {job_title} ({channel})")


@app.command()
def checklist_start(
    company: str,
    job_title: str,
    channel: str = typer.Option("unknown", help="workday | email | google_form | linkedin_dm | ..."),
):
    """Create the on-disk per-application checklist (Rule 0b). No-op if it
    already exists - never overwrites in-flight state."""
    path = start_checklist(company, job_title, channel=channel)
    typer.echo(f"Checklist: {path}")


@app.command()
def checklist_update(
    company: str,
    job_title: str,
    cv_status: str = typer.Option(None),
    placeholder_cv_uploaded: str = typer.Option(None, help="Y | N"),
    real_cv_swapped_location_1: str = typer.Option(None, help="Y | N"),
    real_cv_swapped_location_2: str = typer.Option(None, help="Y | N"),
    current_form_step: str = typer.Option(None),
    submission_status: str = typer.Option(None),
):
    """Update one or more checklist fields. Only pass the ones that changed."""
    updates = {
        "cv_status": cv_status,
        "placeholder_cv_uploaded": placeholder_cv_uploaded,
        "real_cv_swapped_location_1": real_cv_swapped_location_1,
        "real_cv_swapped_location_2": real_cv_swapped_location_2,
        "current_form_step": current_form_step,
        "submission_status": submission_status,
    }
    updates = {k: v for k, v in updates.items() if v is not None}
    if not updates:
        typer.echo("Nothing to update - pass at least one field.")
        raise typer.Exit(code=1)
    path = update_checklist(company, job_title, **updates)
    typer.echo(f"Updated: {path}")


@app.command()
def checklist_note(company: str, job_title: str, note: str):
    """Append a timestamped freeform note to the checklist."""
    path = append_note(company, job_title, note)
    typer.echo(f"Noted: {path}")


@app.command()
def checklist_show(company: str, job_title: str):
    """Print the current checklist - read this FIRST after any compaction or
    session resume, instead of re-deriving state from a lossy summary."""
    typer.echo(read_checklist(company, job_title))


@app.command()
def checklist_list():
    """List all per-application checklists, most recently updated first."""
    paths = list_checklists()
    if not paths:
        typer.echo("No checklists yet.")
        return
    for p in paths:
        typer.echo(p.name)


if __name__ == "__main__":
    app()
