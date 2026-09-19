from __future__ import annotations

from datetime import datetime
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

import sys

from cv_tailor_agent.contracts import CVRecord

# job_application_agent lives in a sibling repo with its own venv - this import
# only succeeds when dashboard.py is invoked from an environment that has it
# installed (job-application-agent's own venv). Historical CV-tool-only runs
# (no application content yet) still work fine without it.
try:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent.parent / "job-application-agent" / "src"))
    from job_application_agent.content_page import generate_content_page
    from job_application_agent.records_db import get_record
    _CONTENT_AVAILABLE = True
except ImportError:
    _CONTENT_AVAILABLE = False

TEMPLATES_DIR = Path(__file__).resolve().parent.parent.parent / "templates"
DEFAULT_DASHBOARD_PATH = Path("private/dashboard.html")


def score_class(score: float | None) -> str:
    if score is None:
        return ""
    if score >= 95:
        return "score-excellent"
    if score >= 90:
        return "score-good"
    if score >= 80:
        return "score-warn"
    return "score-bad"


def cv_status_display(status: str) -> tuple[str, str]:
    """Verbose label, used in the filter dropdown and detail view."""
    labels = {
        "frozen": "Frozen (clean pass)",
        "frozen_below_threshold": "Frozen (below threshold)",
        "historical_reference": "Historical reference",
    }
    return labels.get(status, status), status


def cv_status_cell_display(status: str) -> tuple[str, str]:
    """Short label for the main table cell - the filter dropdown already carries
    the verbose distinction, so the cell itself just needs Created (green/yellow
    by threshold) or Base CV (grey, never processed by the CV tool at all)."""
    if status == "frozen":
        return "Created", "cvcell-created"
    if status == "frozen_below_threshold":
        return "Created", "cvcell-created-warn"
    if status == "historical_reference":
        return "Base CV", "cvcell-base"
    return status, "cvcell-base"


def application_status_display(status: str) -> tuple[str, str]:
    if status == "not_applied":
        return "Not Applied", "status-not-applied"
    if status == "shortlisted":
        return "Applied - Shortlisted", "status-shortlisted"
    if status == "applied":
        return "Applied", "status-applied"
    if status == "closed":
        return "Closed (Not Accepting)", "status-closed"
    if status == "on_hold":
        return "On Hold", "status-on-hold"
    return status, "status-other"


def fit_recommendation_display(recommendation: str | None) -> tuple[str, str]:
    labels = {
        "apply": ("Apply", "fit-apply"),
        "apply_with_flag": ("Apply (flagged gap)", "fit-flag"),
        "flag_for_human": ("Human Review", "fit-human"),
    }
    if recommendation is None:
        return "Not assessed", "fit-none"
    label, css = labels.get(recommendation, (recommendation, "fit-none"))
    return label, css


_OUTCOME_STAGE_LABELS = {
    "application": "Application",
    "no_response": "No Response",
    "assignment": "Assignment",
    "interview": "Interview",
    "offer": "Offer",
}
_OUTCOME_RESULT_LABELS = {
    "pending": "Pending",
    "rejected": "Rejected",
    "hired": "Hired",
}


def outcome_display(stage: str | None, result: str | None) -> tuple[str, str]:
    """A stage alone (e.g. 'assignment') with no result yet means still in
    progress/unknown, not failed - only an explicit 'rejected'/'hired' result is
    treated as terminal. Two independent axes because a stage can stall
    indefinitely, or resolve later, and a rejection can happen at any stage."""
    if stage is None:
        return "—", "outcome-none"
    stage_label = _OUTCOME_STAGE_LABELS.get(stage, stage)
    if result in ("rejected", "hired"):
        result_label = _OUTCOME_RESULT_LABELS.get(result, result)
        css = "outcome-hired" if result == "hired" else "outcome-rejected"
        return f"{stage_label} ({result_label})", css
    return stage_label, "outcome-inprogress"


def generate_dashboard(records: list[CVRecord], out_path: Path = DEFAULT_DASHBOARD_PATH) -> None:
    env = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)))
    template = env.get_template("dashboard_template.html")

    # Strict chronological order, most recent first - no status-based grouping.
    ordered = sorted(records, key=lambda r: r.date_created, reverse=True)

    rows = []
    for r in ordered:
        d = r.model_dump()
        d["pdf_uri"] = Path(r.location).resolve().as_uri()
        d["location_filename"] = Path(r.location).name
        d["score_class"] = score_class(r.composite_score)
        d["app_status_label"], d["app_status_class"] = application_status_display(r.application_status)
        d["cv_status_label"], d["cv_status_value"] = cv_status_display(r.status)
        d["cv_cell_label"], d["cv_cell_class"] = cv_status_cell_display(r.status)
        d["fit_label"], d["fit_class"] = fit_recommendation_display(r.fit_recommendation)
        d["outcome_label"], d["outcome_class"] = outcome_display(r.outcome_stage, r.outcome_result)

        d["content_page_uri"] = None
        if _CONTENT_AVAILABLE:
            db_record = get_record(r.company, r.job_title)
            if db_record is not None:
                content_out = out_path.parent / "applications" / f"{r.company}_{r.job_title}".replace(" ", "_").replace("/", "_") / "content.html"
                generate_content_page(db_record, content_out)
                d["content_page_uri"] = content_out.resolve().as_uri()

        rows.append(d)

    html = template.render(records=rows, generated_at=datetime.now().strftime("%Y-%m-%d %H:%M"))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html, encoding="utf-8")
