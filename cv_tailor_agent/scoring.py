from __future__ import annotations

import difflib
import re

from pydantic import BaseModel, Field

from cv_tailor_agent.contracts import CriticReport
from cv_tailor_agent.schema import CVDocument

HAS_DIGIT = re.compile(r"\d")

# Denylist of known-weak bullet openers, not an allowlist of "strong" verbs.
# An allowlist requires enumerating every valid strong verb in advance and keeps
# missing real ones used in actual rewrites (caught 3 times: Fixed, Earned/
# Maintained, Removed) - each miss silently under-scored an already-fine bullet.
# A denylist of the handful of genuinely weak patterns is far more stable: anything
# not matching one of these is presumed fine, which is the correct default.
WEAK_OPENER_RE = re.compile(
    r"^(Responsible for|Worked on|Worked with|Helped|Assisted|Involved in|"
    r"In charge of|Tasked with|Duties included|Was |Were |Did |Handled|Dealt with)",
    re.IGNORECASE,
)


def has_weak_opener(bullet: str) -> bool:
    # Deliberately just the denylist above - a suffix heuristic to detect "is the
    # first word a verb at all" was tried and dropped: it misfires on common
    # irregular past-tense verbs (e.g. "Won" doesn't end in ed/ied/t/d/ing despite
    # being a perfectly strong verb). Bare noun-phrase openers (the actual failure
    # mode this was meant to catch) get fixed at the source in cv_master.yaml when
    # found, same as the Grade 8/Sangeet Bhushan cases already were.
    return bool(WEAK_OPENER_RE.match(bullet.strip()))


def all_bullets(doc: CVDocument) -> list[str]:
    out: list[str] = []
    for job in doc.experience:
        for sub in job.subsections:
            out.extend(b.text for b in sub.bullets)
    for group_list in (doc.academic_projects, doc.positions_of_responsibility, doc.additional_achievements):
        for grp in group_list:
            out.extend(b.text for b in grp.bullets)
    return out


class StructuralScore(BaseModel):
    quantification_density_pct: float = Field(description="% of bullets containing at least one number")
    strong_verb_lead_pct: float = Field(description="% of bullets starting with a strong action verb")
    avg_bullet_length: float
    max_bullet_length: int
    one_line_compliant_pct: float = Field(description="% of bullets under a safe one-line character threshold")
    contact_fields_present: int = Field(description="out of 4: location, phone, email, linkedin")
    score: float = Field(description="0-100 composite of the above")


ONE_LINE_MAX_CHARS = 145


def compute_structural_score(doc: CVDocument) -> StructuralScore:
    bullets = all_bullets(doc)
    n = len(bullets) or 1

    quant = sum(1 for b in bullets if HAS_DIGIT.search(b)) / n * 100
    strong_verb = sum(1 for b in bullets if not has_weak_opener(b)) / n * 100
    lengths = [len(b) for b in bullets]
    avg_len = sum(lengths) / n
    max_len = max(lengths) if lengths else 0
    one_line = sum(1 for l in lengths if l <= ONE_LINE_MAX_CHARS) / n * 100

    contact_fields = sum([
        bool(doc.header.location),
        bool(doc.header.phone),
        bool(doc.header.email),
        bool(doc.header.linkedin_url),
    ])

    composite = (quant * 0.3) + (strong_verb * 0.3) + (one_line * 0.3) + (contact_fields / 4 * 100 * 0.1)

    return StructuralScore(
        quantification_density_pct=round(quant, 1),
        strong_verb_lead_pct=round(strong_verb, 1),
        avg_bullet_length=round(avg_len, 1),
        max_bullet_length=max_len,
        one_line_compliant_pct=round(one_line, 1),
        contact_fields_present=contact_fields,
        score=round(composite, 1),
    )


class CVScoreCard(BaseModel):
    structural: StructuralScore
    ats_keyword_coverage_pct: float | None = Field(
        default=None,
        description="This IS the profile-to-job fit score (does the candidate's real, truthful experience "
        "cover what this specific JD asks for), from the Critic report. Not a document-quality metric - a "
        "separate 'profile <> job' score would be redundant with this, since this already measures exactly that.",
    )
    caliber_signal_pct: float | None = Field(
        default=None,
        description="Holistic, JD-independent signal (senior-executive proximity/trust, elite pedigree, scale "
        "of demonstrated ownership) from the Critic report. Added after calibrating against 6 real base-CV "
        "shortlisting outcomes (private/calibration/analysis.md) - supplements coverage at a modest weight, "
        "does not replace or inflate it.",
    )
    defensibility_flag_count: int = 0
    style_violation_count: int = 0
    composite_score: float = Field(
        description="0-100 overall, blending THREE things: structural (35%, job-independent document "
        "mechanics/ATS-parseability), ats_keyword_coverage_pct (50%, job-specific profile fit), and "
        "caliber_signal_pct (15%, holistic trust/pedigree signal). It is not a pure 'how good a candidate fit "
        "are you' number - read the sub-scores separately for that."
    )
    caveat: str = (
        "This score estimates ATS-parseability and internal consistency. It cannot and does not "
        "predict whether a specific recruiter will shortlist this CV - that depends on factors "
        "outside the document itself."
    )


def compute_scorecard(doc: CVDocument, critic_report: CriticReport | None = None) -> CVScoreCard:
    structural = compute_structural_score(doc)
    keyword_pct = critic_report.ats_keyword_coverage_pct if critic_report else None
    caliber_pct = critic_report.caliber_signal_pct if critic_report else None
    defensibility_n = len(critic_report.defensibility_flags) if critic_report else 0
    style_n = len(critic_report.style_violations) if critic_report else 0

    if keyword_pct is None:
        # No JD/critic to compare against - this is a structural-quality-only score,
        # not a penalized one. A base CV with no target job isn't "half a score."
        composite = structural.score
    else:
        # Weights MUST sum to 1.0 - a flawless CV (100 structural, 100% coverage, zero
        # flags) has to be able to reach exactly 100, not be capped below it by
        # construction. (Previously 0.4 + 0.5 = 0.9, silently capping every CV at 90
        # regardless of quality - a real bug, not a reflection of real limitations.)
        # caliber_signal_pct added at a modest, fixed 15% weight after calibrating
        # against 6 real base-CV shortlisting outcomes (private/calibration/analysis.md) -
        # it supplements coverage, it does not replace or dominate it.
        composite = (
            (structural.score * 0.35)
            + (keyword_pct * 0.5)
            + ((caliber_pct if caliber_pct is not None else 50.0) * 0.15)
            - (defensibility_n * 5)
            - (style_n * 3)
        )
    composite = max(0.0, min(100.0, composite))

    return CVScoreCard(
        structural=structural,
        ats_keyword_coverage_pct=keyword_pct,
        caliber_signal_pct=caliber_pct,
        defensibility_flag_count=defensibility_n,
        style_violation_count=style_n,
        composite_score=round(composite, 1),
    )


class DeltaReport(BaseModel):
    bullets_changed: int
    bullets_total: int
    pct_bullets_changed: float
    avg_similarity_ratio: float = Field(description="1.0 = identical text, 0.0 = completely different, across changed bullets")
    tagline_changed: bool
    subsections_reordered: int


def compute_delta(base_doc: CVDocument, tailored_doc: CVDocument) -> DeltaReport:
    base_bullets = all_bullets(base_doc)
    tailored_bullets = all_bullets(tailored_doc)

    base_set = set(base_bullets)
    changed = [b for b in tailored_bullets if b not in base_set]

    ratios = []
    for new_text in changed:
        best = max(
            (difflib.SequenceMatcher(None, new_text, orig).ratio() for orig in base_bullets),
            default=0.0,
        )
        ratios.append(best)

    reorders = 0
    base_order = {f"{j.organization}|{j.title}": [s.heading for s in j.subsections] for j in base_doc.experience}
    for job in tailored_doc.experience:
        key = f"{job.organization}|{job.title}"
        new_order = [s.heading for s in job.subsections]
        if key in base_order and new_order != base_order[key]:
            reorders += 1

    n = len(base_bullets) or 1
    return DeltaReport(
        bullets_changed=len(changed),
        bullets_total=n,
        pct_bullets_changed=round(len(changed) / n * 100, 1),
        avg_similarity_ratio=round(sum(ratios) / len(ratios), 3) if ratios else 1.0,
        tagline_changed=base_doc.header.tagline != tailored_doc.header.tagline,
        subsections_reordered=reorders,
    )
