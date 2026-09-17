from __future__ import annotations

from pydantic import BaseModel, Field

# The 90+/80-89/<80 score buckets and the "a central gap overrides the score"
# rule are both stated as prose in JOB_APPLICATION_AGENT_HANDOFF.md and were
# applied by hand for BIK/Peak XV. Nothing in cv-tailor-agent encodes this -
# confirmed by direct code read (contracts.py, scoring.py, cli.py) before writing
# this module. This is the first place that framework becomes real, reusable logic.

APPLY = "apply"
APPLY_WITH_FLAG = "apply_with_flag"
FLAG_FOR_HUMAN = "flag_for_human"

# IMPORTANT: FLAG_FOR_HUMAN is advisory, not a pipeline gate. Applying is cheap
# (explicit in the handoff and reconfirmed by the user) - the pipeline still
# proceeds through CV tailoring and content drafting regardless of recommendation.
# The only place this recommendation actually changes behavior is the human
# confirmation gate before sending: it must be surfaced prominently there so the
# human sees the flagged gap before saying yes, not used to silently skip a job.


class GapAssessment(BaseModel):
    """One gap from CVRecord.market_gaps (or ats_keyword_coverage missing_keywords),
    plus the one judgment call the underlying data doesn't carry: is this gap
    central to what the role actually does day-to-day, or peripheral to it?
    That judgment isn't mechanically derivable from the gap text alone - it's made
    the same way CV-tailoring judgment already happens in this workflow (Claude
    reasoning about it during Intake/Research, same as the BIK/Peak XV writeups)."""

    text: str
    is_central: bool = Field(
        description="True if this gap sits at the literal core of what the role does "
        "(e.g. BIK's missing B2B SaaS GTM tenure for a GTM-titled role), not a "
        "peripheral or 'nice to have' mismatch."
    )


class FitAssessment(BaseModel):
    recommendation: str  # APPLY | APPLY_WITH_FLAG | FLAG_FOR_HUMAN
    flagged_gaps: list[str] = Field(default_factory=list)
    reasoning: str


def _score_bucket(composite_score: float) -> str:
    if composite_score >= 90:
        return APPLY
    if composite_score >= 80:
        return APPLY_WITH_FLAG
    return FLAG_FOR_HUMAN


def compute_apply_recommendation(
    composite_score: float, gaps: list[GapAssessment]
) -> FitAssessment:
    """Deterministic once gap centrality is known. Score buckets first, then a
    central gap always overrides toward flag_for_human regardless of score - the
    handoff is explicit that this must not be loosened (BIK: 91.4, still flagged,
    because the gap is central to a GTM-titled role, not because the score was low)."""
    bucket = _score_bucket(composite_score)
    central_gaps = [g.text for g in gaps if g.is_central]

    if central_gaps:
        joined = "; ".join(central_gaps)
        return FitAssessment(
            recommendation=FLAG_FOR_HUMAN,
            flagged_gaps=central_gaps,
            reasoning=(
                f"Composite score {composite_score} alone would suggest '{bucket}', but "
                f"{len(central_gaps)} gap(s) judged central to the role's core function "
                f"override that regardless of score: {joined}."
            ),
        )

    if bucket == APPLY:
        return FitAssessment(
            recommendation=APPLY,
            flagged_gaps=[],
            reasoning=f"Composite score {composite_score} is 90+ and no gap is central to the role. Apply with confidence.",
        )

    non_central = [g.text for g in gaps]
    if bucket == APPLY_WITH_FLAG:
        return FitAssessment(
            recommendation=APPLY_WITH_FLAG,
            flagged_gaps=non_central,
            reasoning=(
                f"Composite score {composite_score} is in the 80-89 range with no central "
                "gap. Apply, but address the specific gap(s) head-on in the cover note/email "
                f"rather than leaving it silent: {'; '.join(non_central) if non_central else 'none recorded'}."
            ),
        )

    return FitAssessment(
        recommendation=FLAG_FOR_HUMAN,
        flagged_gaps=non_central,
        reasoning=(
            f"Composite score {composite_score} is below 80. Never auto-skip - surface this "
            "as a profile-fit judgment call for the human rather than deciding it silently."
        ),
    )
