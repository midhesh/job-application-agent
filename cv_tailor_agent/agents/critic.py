from __future__ import annotations

import anthropic
import yaml

from cv_tailor_agent.contracts import CriticReport, IntakeResult
from cv_tailor_agent.llm import call_structured, style_rules_block
from cv_tailor_agent.schema import CVDocument

CRITIC_SYSTEM = """You are an independent reviewer with no knowledge of how this tailored CV
was produced. You did not write it, so evaluate it with fresh eyes rather than assuming it's
correct. You are given the job description, the requirements, the ORIGINAL CV, and the
TAILORED CV. Your job:

0. Read the JD's actual REQUIREMENTS section as the real screen - don't score
   coverage against the RESPONSIBILITIES section as if it were a checklist.
   Responsibilities describe the job, not what a candidate must already have.
1. Estimate ATS keyword coverage - first classify each requirement as a HARD GATE
   (explicit named years+domain/tool/credential as a qualifying criterion - score
   strictly, never loosen a real gap here) or an ILLUSTRATIVE EXAMPLE of an underlying
   ability (a named tool/tactic offered as color, not a gate - score whether the
   ability is evidenced anywhere, not whether the named example literally appears).
   Conflating these caused validated real-world under-prediction; see
   private/calibration/analysis.md.
2. Separately assess caliber_signal_pct: a holistic, JD-independent signal capturing
   direct senior-executive/founder proximity and trust, elite pedigree, and
   demonstrated scale of ownership. This supplements coverage, it does not replace it.
3. Flag any tailored bullet that overclaims relative to what the original bullet actually
   supports, or that the candidate likely couldn't defend under a direct follow-up question.
4. Flag any violation of the style rules below.
5. Confirm structure is intact: same sections, same number of bullets per role, no bullet
   longer than roughly one line.
6. Decide pass/fail. Pass only if coverage is strong AND there are no defensibility or
   structural problems. caliber_signal_pct does not by itself decide pass/fail. If
   failing, give concrete, actionable feedback for the next revision.

Style rules:
{style_rules}
"""


def run_critic(
    client: anthropic.Anthropic,
    jd_text: str,
    original_doc: CVDocument,
    tailored_doc: CVDocument,
    intake: IntakeResult,
    style_rules: list[dict],
) -> CriticReport:
    user_content = f"""Job description:
{jd_text}

Requirements:
must_have: {intake.must_have}
nice_to_have: {intake.nice_to_have}
keyword_synonyms: {intake.keyword_synonyms}

Original CV:
{yaml.dump(original_doc.model_dump(), sort_keys=False, allow_unicode=True)}

Tailored CV:
{yaml.dump(tailored_doc.model_dump(), sort_keys=False, allow_unicode=True)}
"""
    return call_structured(
        client,
        system=CRITIC_SYSTEM.format(style_rules=style_rules_block(style_rules)),
        user_content=user_content,
        tool_name="submit_critic_report",
        tool_description="Submit the independent review verdict for this tailored CV.",
        result_model=CriticReport,
    )
