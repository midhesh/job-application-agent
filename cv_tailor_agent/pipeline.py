from __future__ import annotations

from pathlib import Path

from cv_tailor_agent.agents.critic import run_critic
from cv_tailor_agent.agents.intake import run_intake
from cv_tailor_agent.agents.tailor import run_tailor
from cv_tailor_agent.contracts import TailorResult
from cv_tailor_agent.guardrails import apply_patch, validate_patch
from cv_tailor_agent.llm import get_client, load_style_rules
from cv_tailor_agent.render import load_cv_document, render_document


def run_pipeline(
    cv_master_path: Path,
    jd_text: str,
    company_notes: str | None,
    out_pdf_path: Path,
    max_iterations: int = 3,
) -> TailorResult:
    client = get_client()
    style_rules = load_style_rules()
    doc = load_cv_document(cv_master_path)
    original_tagline = doc.header.tagline

    intake = run_intake(client, jd_text, company_notes)

    feedback: str | None = None
    critic_report = None
    tailored_doc = doc
    accepted_patch = None
    iterations_used = 0

    for i in range(max_iterations):
        iterations_used = i + 1
        patch = run_tailor(client, doc, intake, style_rules, critic_feedback=feedback)

        errors = validate_patch(doc, patch)
        if errors:
            feedback = "Your previous submission was rejected by automated checks:\n" + "\n".join(
                f"- {e}" for e in errors
            )
            continue

        tailored_doc = apply_patch(doc, patch)
        accepted_patch = patch
        critic_report = run_critic(client, jd_text, doc, tailored_doc, intake, style_rules)

        if critic_report.passed:
            break
        feedback = critic_report.feedback_for_tailor

    render_document(tailored_doc, out_pdf_path)

    return TailorResult(
        output_pdf_path=str(out_pdf_path),
        tagline_before=original_tagline,
        tagline_after=tailored_doc.header.tagline,
        bullet_diff=accepted_patch.bullet_edits if accepted_patch else [],
        ats_keyword_coverage_pct=critic_report.ats_keyword_coverage_pct if critic_report else 0.0,
        critic_report=critic_report,
        iterations_used=iterations_used,
        passed=bool(critic_report and critic_report.passed),
    )
