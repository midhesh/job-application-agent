from __future__ import annotations

import re
from pathlib import Path

from pydantic import BaseModel

from cv_tailor_agent.contracts import CVRecord
from job_application_agent.patterns import EMAIL, EMAIL_AND_LINKEDIN_DM, classify_application_pattern

EMAIL_CAPABLE_PATTERNS = (EMAIL, EMAIL_AND_LINKEDIN_DM)

# Final gate before a human is asked to approve sending: cross-check the drafted
# call-to-action against what the ORIGINAL job posting actually specified (right
# address, right subject line), confirm a CV is genuinely attached and is the
# exact frozen file on record for this (company, job_title) - not a stale or
# wrong one - and confirm the LinkedIn URL in the signature is the real profile,
# not a typo. This runs in addition to, not instead of, the content guardrails
# (guardrails.py checks writing quality/fabrication; this checks mechanical
# correctness against the source-of-truth JD and registry).


class ApplicationValidationResult(BaseModel):
    passed: bool
    checks: dict[str, bool]
    issues: list[str]


def validate_email_application(
    draft_text: str,
    cv_record: CVRecord,
    expected_linkedin_url: str,
) -> ApplicationValidationResult:
    issues: list[str] = []
    checks: dict[str, bool] = {}

    target = classify_application_pattern(cv_record.jd_text, cv_record.job_post_url)
    checks["pattern_is_email"] = target.pattern in EMAIL_CAPABLE_PATTERNS
    if not checks["pattern_is_email"]:
        issues.append(
            f"Expected an email-capable application per the JD ({EMAIL_CAPABLE_PATTERNS}), classifier says {target.pattern!r}."
        )

    to_match = re.search(r"^To:\s*(.+)$", draft_text, re.MULTILINE)
    to_addr = to_match.group(1).strip() if to_match else None
    checks["to_matches_jd"] = bool(
        to_addr and target.email_address and to_addr.lower() == target.email_address.lower()
    )
    if not checks["to_matches_jd"]:
        issues.append(f"Draft 'To:' is {to_addr!r}, but the JD specifies {target.email_address!r}.")

    subj_match = re.search(r"^Subject:\s*(.+)$", draft_text, re.MULTILINE)
    subj = subj_match.group(1).strip() if subj_match else None
    if target.email_subject:
        # JD gave an exact required subject string - must match exactly.
        checks["subject_matches_jd"] = bool(subj and subj == target.email_subject)
        if not checks["subject_matches_jd"]:
            issues.append(f"Draft subject is {subj!r}, but the JD specifies {target.email_subject!r}.")
    else:
        # JD only said "mention the role" or similar, no exact string required -
        # just confirm a non-empty subject was actually provided, not that it
        # matches text that was never specified.
        checks["subject_matches_jd"] = bool(subj)
        if not checks["subject_matches_jd"]:
            issues.append("No subject line found in draft, and the JD requires one (even if no exact wording was specified).")

    attach_match = re.search(r"^Attachment:\s*(.+)$", draft_text, re.MULTILINE)
    attach_name = attach_match.group(1).strip() if attach_match else None
    checks["attachment_present"] = attach_name is not None
    if not checks["attachment_present"]:
        issues.append("No 'Attachment:' line found in the draft - a CV must be explicitly attached, not implied.")
    else:
        expected_filename = Path(cv_record.location).name
        checks["attachment_matches_frozen_cv"] = attach_name == expected_filename
        if not checks["attachment_matches_frozen_cv"]:
            issues.append(
                f"Draft attaches {attach_name!r}, but the frozen CV on record for this job is {expected_filename!r} - wrong CV would go out."
            )
        checks["attachment_file_exists"] = Path(cv_record.location).exists()
        if not checks["attachment_file_exists"]:
            issues.append(f"The attachment file does not actually exist on disk: {cv_record.location}")

    linkedin_match = re.search(r"linkedin\.com/in/\S+", draft_text, re.IGNORECASE)
    linkedin_in_draft = linkedin_match.group(0).rstrip("/.,") if linkedin_match else None
    checks["linkedin_present"] = linkedin_in_draft is not None
    checks["linkedin_matches_profile"] = bool(
        linkedin_in_draft
        and linkedin_in_draft.rstrip("/").lower() in expected_linkedin_url.rstrip("/").lower()
    )
    if not checks["linkedin_matches_profile"]:
        issues.append(
            f"LinkedIn URL in draft ({linkedin_in_draft!r}) does not match the known profile ({expected_linkedin_url!r})."
        )

    return ApplicationValidationResult(passed=not issues, checks=checks, issues=issues)
