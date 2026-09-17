from __future__ import annotations

import anthropic
import yaml

from cv_tailor_agent.contracts import IntakeResult, TailorPatch
from cv_tailor_agent.llm import call_structured, style_rules_block
from cv_tailor_agent.schema import CVDocument

TAILOR_SYSTEM = """You tailor a candidate's CV to a specific job, working within hard constraints:

1. You may ONLY rephrase bullets that already exist in the CV master data below. Every
   `original_text` you submit must match an existing bullet verbatim.
2. You may NEVER introduce a number, percentage, or statistic that is not already present
   in the bullet you are rewriting. If a bullet doesn't already support a claim, don't
   manufacture one; rewrite the phrasing instead, or leave the bullet untouched.
3. Decide an emphasis strategy first: given the JD, which of a role's existing sub-headings
   (e.g. "Strategic Leadership" vs "Enterprise Systems") should lead, for a recruiter doing
   a 6-8 second scan of page 1? Only reorder subsections within a role if it meaningfully
   changes what leads; do not reorder for its own sake.
4. Don't default to the smallest possible edit count. If a bolder rewrite, or reordering
   subsections, genuinely serves the JD better, do it - the only real limits are the
   guardrails above, not a preference for minimalism.
5. Mirror the JD's own title language in the tagline where truthful.

Style rules (violating any of these is a failure):
{style_rules}
"""


def run_tailor(
    client: anthropic.Anthropic,
    doc: CVDocument,
    intake: IntakeResult,
    style_rules: list[dict],
    critic_feedback: str | None = None,
) -> TailorPatch:
    cv_yaml = yaml.dump(doc.model_dump(), sort_keys=False, allow_unicode=True)

    user_content = f"""CV master data (source of truth - only real bullets from here may be selected):
{cv_yaml}

Job requirements:
must_have: {intake.must_have}
nice_to_have: {intake.nice_to_have}
keyword_synonyms: {intake.keyword_synonyms}
title_language: {intake.title_language}
company_context: {intake.company_context}

Current tagline: {doc.header.tagline}
"""
    if critic_feedback:
        user_content += f"\nFeedback from the previous review round to address:\n{critic_feedback}\n"

    return call_structured(
        client,
        system=TAILOR_SYSTEM.format(style_rules=style_rules_block(style_rules)),
        user_content=user_content,
        tool_name="submit_tailor_patch",
        tool_description="Submit the tagline, bullet rewrites, and subsection reordering for this application.",
        result_model=TailorPatch,
    )
