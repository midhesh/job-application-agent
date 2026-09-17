from __future__ import annotations

import re

from pydantic import BaseModel

EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
SUBJECT_RE = re.compile(r"subject\s*(?:line)?[^\"\n]*\"([^\"]+)\"", re.IGNORECASE)

EMAIL = "email"
LINKEDIN_EASY_APPLY = "linkedin_easy_apply"
LINKEDIN_DM = "linkedin_dm"
EMAIL_AND_LINKEDIN_DM = "email_and_linkedin_dm"
PORTAL_FORM = "portal_form"
UNKNOWN = "unknown"

# Matches "DM or email" / "email or DM" instructions - a real, distinct third
# pattern from a plain email-only JD. Not every "DM or X" case means BOTH should
# be used - that's a per-case call the human makes (applying is cheap, and doing
# both is worth it especially when already connected to the named contact on
# LinkedIn) - this only detects that the JD explicitly OFFERS the DM channel
# alongside email, it doesn't itself decide to use both.
DM_OFFERED_RE = re.compile(r"\bDM\b", re.IGNORECASE)


class ApplicationTarget(BaseModel):
    pattern: str  # EMAIL | LINKEDIN_EASY_APPLY | LINKEDIN_DM | EMAIL_AND_LINKEDIN_DM | PORTAL_FORM | UNKNOWN
    email_address: str | None = None
    email_subject: str | None = None
    dm_offered: bool = False  # True if the JD text itself offers DM as an alternative/additional channel
    notes: str = ""


def classify_application_pattern(jd_text: str | None, job_post_url: str | None) -> ApplicationTarget:
    """Prefer an explicit email instruction in the JD text over the URL - a JD can
    be sourced from LinkedIn (job_post_url) but still say 'apply by emailing X',
    which is the actual action to take, not LinkedIn's own Apply button.

    Every case needs eyes-on nuance, not just this classifier - e.g. Rovia's JD
    says 'DM or email', which is genuinely a case for doing BOTH (cheap, and the
    candidate is already connected to the named poster on LinkedIn), not just
    picking one. This function only surfaces what the JD text itself offers -
    the human/agent decides whether to use one channel or both."""
    if jd_text:
        email_match = EMAIL_RE.search(jd_text)
        dm_offered = bool(DM_OFFERED_RE.search(jd_text))
        if email_match:
            subject_match = SUBJECT_RE.search(jd_text)
            pattern = EMAIL_AND_LINKEDIN_DM if dm_offered else EMAIL
            notes = "Email address found in JD text"
            notes += ", JD also explicitly offers DM as an alternative/additional channel." if dm_offered else "."
            return ApplicationTarget(
                pattern=pattern,
                email_address=email_match.group(0),
                email_subject=subject_match.group(1) if subject_match else None,
                dm_offered=dm_offered,
                notes=notes,
            )
        if dm_offered:
            return ApplicationTarget(
                pattern=LINKEDIN_DM,
                dm_offered=True,
                notes="JD offers DM with no email address given - LinkedIn DM only.",
            )
    if job_post_url and "linkedin.com/jobs/view" in job_post_url:
        return ApplicationTarget(
            pattern=LINKEDIN_EASY_APPLY,
            notes="LinkedIn Jobs listing URL - native Apply button flow, exact fields shown only once the listing is opened.",
        )
    if job_post_url:
        return ApplicationTarget(
            pattern=PORTAL_FORM,
            notes=f"Non-LinkedIn URL with no email instruction in the JD - likely a company portal/form: {job_post_url}",
        )
    return ApplicationTarget(
        pattern=UNKNOWN,
        notes="Could not classify from JD text or URL - ask the user rather than guessing.",
    )
