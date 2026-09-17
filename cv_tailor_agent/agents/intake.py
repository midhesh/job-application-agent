from __future__ import annotations

import anthropic

from cv_tailor_agent.contracts import IntakeResult
from cv_tailor_agent.llm import call_structured, research_with_web_search

RESEARCH_PROMPT = """A candidate is applying to this job. Research the company briefly:
who they are, their stage/funding, and what their core product actually does, so the
research can inform how a resume should be tailored. 2-4 short searches is plenty.

Job description:
{jd_text}

Additional notes provided by the candidate:
{company_notes}
"""

INTAKE_SYSTEM = """You extract structured hiring requirements from a job description and
brief research summary, for a resume-tailoring pipeline. Be precise: separate what is
explicitly required (must_have) from what is merely preferred (nice_to_have). Include
plausible synonym phrasings a candidate's resume might use instead of the JD's exact words,
so a keyword matcher does not falsely penalize equivalent language."""


def run_intake(client: anthropic.Anthropic, jd_text: str, company_notes: str | None) -> IntakeResult:
    research_text = research_with_web_search(
        client,
        RESEARCH_PROMPT.format(jd_text=jd_text, company_notes=company_notes or "(none provided)"),
    )

    user_content = f"""Job description:
{jd_text}

Company notes from candidate:
{company_notes or "(none provided)"}

Research summary:
{research_text}
"""
    return call_structured(
        client,
        system=INTAKE_SYSTEM,
        user_content=user_content,
        tool_name="submit_intake",
        tool_description="Submit the structured requirement analysis for this job description.",
        result_model=IntakeResult,
    )
