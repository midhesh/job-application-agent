from __future__ import annotations

from pydantic import BaseModel, Field


class IntakeResult(BaseModel):
    must_have: list[str] = Field(description="Required skills/experience the JD explicitly demands")
    nice_to_have: list[str] = Field(description="Preferred but not required skills/experience")
    keyword_synonyms: dict[str, list[str]] = Field(
        default_factory=dict,
        description="Map of a JD keyword to phrasings the candidate's CV might use instead",
    )
    title_language: str = Field(description="The JD's own job-title phrasing, for tagline mirroring")
    company_context: str = Field(description="2-4 sentences: what the company does, stage, and anything from research relevant to tailoring")


class BulletEdit(BaseModel):
    original_text: str = Field(description="Must match an existing bullet in the CV master verbatim")
    new_text: str = Field(description="Rewritten bullet. Must not introduce any number/statistic absent from original_text")
    rationale: str = Field(description="One sentence: why this rewrite serves the JD")


class TailorPatch(BaseModel):
    tagline: str
    bullet_edits: list[BulletEdit] = Field(default_factory=list)
    subsection_order: dict[str, list[str]] = Field(
        default_factory=dict,
        description="Key is 'organization|title', value is the desired order of subsection headings for that role",
    )


class CriticReport(BaseModel):
    ats_keyword_coverage_pct: float = Field(
        description="Score HARD gates (explicit named years+domain/tool/credential stated as a qualifying "
        "criterion, e.g. '2-5+ years in B2B SaaS') strictly - a real gap here is a real gap, never loosen this. "
        "For requirements that are illustrative EXAMPLES of an underlying ability rather than gates themselves "
        "(e.g. a named tool mentioned as color under a JD that says tools aren't a primary filter), score "
        "whether the underlying ability is evidenced anywhere in the CV, not whether the named example itself "
        "appears verbatim. Conflating these two caused real under-prediction, validated against 6 real "
        "shortlisting outcomes on the base CV (see private/calibration/analysis.md)."
    )
    caliber_signal_pct: float = Field(
        default=50.0,
        description="A separate, holistic signal independent of JD-specific requirement matching: direct "
        "senior-executive/founder proximity and trust (e.g. de facto Chief of Staff), elite pedigree (Tier-1 "
        "MBA, notable employer), and demonstrated scale of ownership (large monetary figures, headcount, "
        "hire-scale). This captures 'who has already trusted this person at what level', which real-world "
        "shortlisting outcomes showed matters beyond literal ability-matching. Weighted modestly in the "
        "composite score - it supplements coverage, it does not replace it.",
    )
    missing_keywords: list[str] = Field(default_factory=list)
    defensibility_flags: list[str] = Field(
        default_factory=list, description="Bullets that overclaim or drift from what the master CV actually supports"
    )
    style_violations: list[str] = Field(default_factory=list)
    passed: bool
    feedback_for_tailor: str = Field(description="Concrete instructions for the next tailoring pass; empty if passed")


class CVRecord(BaseModel):
    """One row in the application-tracking registry/dashboard.

    This is the shared contract point for the dashboard: it's designed to be a
    single common view, not a CV-tool-specific one, so the future Job Application
    Agent can add its own record types (e.g. cover letters, submission status)
    alongside this one without redesigning the dashboard.
    """

    company: str
    job_title: str
    date_created: str
    location: str
    jd_source: str | None = None
    job_post_url: str | None = None
    jd_text: str | None = None
    company_research: str | None = None
    composite_score: float | None = None
    ats_coverage_pct: float | None = None
    status: str = "frozen"
    application_status: str = "not_applied"  # Job Application Agent updates this once an application actually goes out
    # Score breakdown, for the dashboard's detail view - all optional so other
    # future record types (e.g. from a Job Application Agent) aren't forced to have them.
    quantification_density_pct: float | None = None
    strong_verb_lead_pct: float | None = None
    one_line_compliant_pct: float | None = None
    contact_fields_present: int | None = None
    defensibility_flag_count: int | None = None
    style_violation_count: int | None = None
    pct_bullets_changed: float | None = None
    market_gaps: list[str] = Field(default_factory=list)
    # Fields below are owned by the Job Application Agent, not the CV tool - set via
    # update_record_field(), never by loading/mutating/re-upserting a full CVRecord.
    fit_recommendation: str | None = None  # "apply" | "apply_with_flag" | "flag_for_human"
    fit_reasoning: str | None = None
    application_pattern: str | None = None  # "email" | "linkedin_easy_apply" | "portal_form"
    cover_letter_status: str | None = None  # "not_started" | "drafted" | "guardrails_passed"
    submitted_at: str | None = None  # ISO date/time the application was actually sent - only set going forward, never backfilled for historical rows we don't have an exact time for
    submission_evidence_url: str | None = None  # proof the application actually went out - a Gmail permalink for email sends, or a saved screenshot/confirmation-page path for portal sends
    submission_evidence_note: str | None = None  # short human-readable description of what the evidence link/file actually is
    # Real-world outcome tracking, two independent levels (stage reached, and the
    # terminal result at that stage if known) since an application can stall at any
    # stage indefinitely or resolve later - e.g. "interview" + "rejected" (got an
    # interview, then rejected there), or "assignment" + None (furthest known stage,
    # no resolution yet). This is the ONLY honest path to eventually calibrating the
    # fit model with real negative examples - right now every historical data point
    # is a positive outcome, which is why fit.py stays a simple deterministic rule
    # instead of anything "learned". Never overwrite outcome_result to erase a
    # rejection just because a later stage was reached elsewhere - each (company,
    # job_title) is one record, one real outcome.
    outcome_stage: str | None = None  # "application" | "no_response" | "assignment" | "interview" | "offer" - "application" means rejected/resolved at the initial resume screen, never progressed further
    outcome_result: str | None = None  # "pending" | "rejected" | "hired"


class MarketBenchmarkReport(BaseModel):
    role_pattern_summary: str = Field(
        description="2-4 sentences on what public data (LinkedIn snippets, career-site content) "
        "shows as common background/skills for people in this role category at comparable companies. "
        "This is aggregate pattern research, NOT actual hired candidates' resumes for this specific posting - that data isn't public."
    )
    commonly_seen_skills: list[str] = Field(default_factory=list)
    gaps_vs_this_cv: list[str] = Field(
        default_factory=list, description="Commonly-seen skills/background this CV doesn't demonstrate"
    )
    strengths_vs_pattern: list[str] = Field(
        default_factory=list, description="Ways this CV exceeds or stands out against the common pattern"
    )


class TailorRequest(BaseModel):
    jd_text: str
    company_notes: str | None = None
    cv_master_path: str
    out_pdf_path: str
    max_iterations: int = 3


class TailorResult(BaseModel):
    output_pdf_path: str
    tagline_before: str
    tagline_after: str
    bullet_diff: list[BulletEdit]
    ats_keyword_coverage_pct: float
    critic_report: CriticReport | None
    iterations_used: int
    passed: bool
