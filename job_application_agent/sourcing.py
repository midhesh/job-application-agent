from __future__ import annotations

from pydantic import BaseModel, Field

# This is the SCORING logic for a future autonomous "Sourcing & Screening" stage -
# a distinct pipeline stage from fit.py, not a rename of it. The split matters:
#
#   Sourcing (this file): runs across MANY postings found by scanning portals, at
#   low information (title/location/comp hint, rarely full JD detail yet). Its job
#   is triage - "worth a closer look at all?" - so it must stay cheap, high-recall,
#   and genuinely a SOFT filter, per the user's explicit instruction: comp/skill
#   fit are indicators to weigh, not hard gates, because applying is cheap and a
#   false negative here (skipping a job that was actually fine) is the costly
#   error, not a false positive.
#
#   Fit (fit.py): runs ONCE PER POSTING that already passed sourcing and has a
#   real JD + a tailored-CV composite score. Its job is a deeper, JD-specific
#   apply-worthiness call, advisory only (see FitAssessment) - never blocking.
#
# NOTE: actually scanning/scraping job portals (LinkedIn, Naukri, Foundit,
# iimjobs, Wellfound) is separate, not-yet-built infrastructure with its own real
# constraints (login walls, ToS, bot-detection) - explicitly out of scope here.
# This module only scores a posting once its basic facts are already known,
# however they were obtained (manual link today, autonomous scan later).

ROLE_CATEGORY_KEYWORDS = {
    "primary": [
        "founder's office", "founders office", "ceo's office", "cto's office",
        "chief of staff", "program management", "product management",
        "founder's associate", "founders associate",
    ],
    "secondary": ["analyst", "consultant", "consulting"],
}


class SourcingScreenResult(BaseModel):
    should_shortlist: bool = Field(
        description="True unless a genuine hard disqualifier fired. Defaults to True - "
        "this is deliberately a permissive filter, not a precision-optimized one."
    )
    role_category: str = Field(description="'primary' | 'secondary' | 'unclassified'")
    comp_flag: str | None = Field(
        default=None,
        description="Set only when the posting states an explicit comp figure/ceiling "
        "well outside the expected 30-40 LPA CTC band - informational, not blocking.",
    )
    reasoning: str


# A stated ceiling this far under the floor of the expected band is treated as a
# genuine hard disqualifier (not just a flag) - distinguishing "clearly a different
# level of role" from "a reasonable range that happens to undershoot a soft target".
HARD_COMP_FLOOR_LPA = 15.0
EXPECTED_COMP_FLOOR_LPA = 30.0


def screen_posting(
    title: str,
    location_ok: bool = True,
    stated_comp_ceiling_lpa: float | None = None,
) -> SourcingScreenResult:
    """`location_ok`: False only for a genuine hard mismatch (e.g. onsite-only
    outside India with no visa sponsorship and no remote option) - never set False
    for a merely inconvenient location, per the same "applying is cheap, don't
    over-filter" principle."""
    title_lower = title.lower()
    role_category = "unclassified"
    if any(kw in title_lower for kw in ROLE_CATEGORY_KEYWORDS["primary"]):
        role_category = "primary"
    elif any(kw in title_lower for kw in ROLE_CATEGORY_KEYWORDS["secondary"]):
        role_category = "secondary"

    comp_flag = None
    if stated_comp_ceiling_lpa is not None and stated_comp_ceiling_lpa < EXPECTED_COMP_FLOOR_LPA:
        comp_flag = (
            f"Stated ceiling ~{stated_comp_ceiling_lpa} LPA is below the expected "
            f"{EXPECTED_COMP_FLOOR_LPA}-40 LPA band - informational, doesn't block on its own."
        )

    if not location_ok:
        return SourcingScreenResult(
            should_shortlist=False,
            role_category=role_category,
            comp_flag=comp_flag,
            reasoning="Hard location mismatch (onsite-only outside India, no visa sponsorship, no remote option).",
        )

    if stated_comp_ceiling_lpa is not None and stated_comp_ceiling_lpa < HARD_COMP_FLOOR_LPA:
        return SourcingScreenResult(
            should_shortlist=False,
            role_category=role_category,
            comp_flag=comp_flag,
            reasoning=(
                f"Stated comp ceiling ~{stated_comp_ceiling_lpa} LPA is far enough below the "
                f"expected band ({EXPECTED_COMP_FLOOR_LPA}-40 LPA) to signal a different level "
                "of role entirely, not just a soft mismatch."
            ),
        )

    reasoning = f"No hard disqualifier. Role category: {role_category}."
    if comp_flag:
        reasoning += f" {comp_flag}"
    if role_category == "unclassified":
        reasoning += " Title doesn't match known primary/secondary targeting, but that's a soft signal, not a reason to skip."

    return SourcingScreenResult(
        should_shortlist=True,
        role_category=role_category,
        comp_flag=comp_flag,
        reasoning=reasoning,
    )
