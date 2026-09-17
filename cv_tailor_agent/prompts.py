from __future__ import annotations

import yaml

from cv_tailor_agent.llm import style_rules_block
from cv_tailor_agent.schema import CVDocument

TAILOR_PROMPT_TEMPLATE = """You are tailoring a candidate's CV to a specific job. Work within hard constraints:

1. You may ONLY rephrase bullets that already exist in the CV master data below. Every
   "original_text" you output must match an existing bullet verbatim, character for character.
2. You may NEVER introduce a number, percentage, or statistic that is not already present
   in the bullet you are rewriting. If you don't have web search/browsing available, skip
   live research and rely on the company notes given below.
3. If you do have web search or browsing available, spend it briefly (2-4 searches max) on
   the company/founder/product, then use that context to decide emphasis, not to invent facts.
4. Decide an emphasis strategy: given the job description, which of a role's existing
   sub-headings should lead, for a recruiter doing a 6-8 second scan of page 1? Only reorder
   subsections within a role if it meaningfully changes what leads.
5. Don't default to the smallest possible edit count. If a bolder rewrite, or reordering
   subsections, genuinely serves the JD better, do it - the only real limits are the
   guardrails (no fabricated bullets, no invented numbers, one-line length), not a
   preference for minimalism. Leave a bullet untouched only when rewriting it wouldn't
   actually improve fit, not out of caution about how many edits you're making.
6. Mirror the job description's own title language in the tagline where truthful.

Style rules (violating any of these is a failure):
{style_rules}

CV master data (source of truth - only real bullets from here may be selected):
{cv_yaml}

Job description:
{jd_text}

Company notes from the candidate:
{company_notes}

Current tagline: {tagline}

Respond with ONLY a single JSON object (no prose, no markdown fences) matching exactly this shape:
{{
  "tagline": "string",
  "bullet_edits": [
    {{"original_text": "exact existing bullet text", "new_text": "rewritten bullet", "rationale": "one sentence"}}
  ],
  "subsection_order": {{"Organization|Job Title": ["Heading A", "Heading B"]}}
}}
Leave "bullet_edits" and "subsection_order" empty ([] / {{}}) if no changes are warranted there.
"""

CRITIC_PROMPT_TEMPLATE = """You are an independent reviewer. You did NOT write the CV below and have no
stake in defending it — evaluate it with fresh eyes. You are given the job description,
the ORIGINAL CV, and the TAILORED CV that resulted from someone else's edits.

Your job:
0. Read the JD's actual REQUIREMENTS/"what you'll need" section as the real screen -
   don't score coverage against the RESPONSIBILITIES/"what you'll do" section as if
   it were a requirements checklist. Responsibilities describe the job, not what a
   candidate must already have; a tactical detail appearing only in responsibilities
   (e.g. "manage the CEO's calendar") is often something learned on the job, not a
   screening filter, even though it reads like a specific skill if you don't separate
   the two sections. Validated against real shortlisting outcomes where scoring
   coverage against responsibilities badly under-predicted the real result.
1. Estimate ATS keyword coverage - but first classify each JD requirement as either:
   - a HARD GATE: an explicit named years+domain/tool/credential stated as a qualifying
     criterion (e.g. "2-5+ years in B2B SaaS", "MBA from Tier 1 College"). Score these
     strictly - a real gap here is a real gap. Do not loosen this for any reason.
   - an ILLUSTRATIVE EXAMPLE of an underlying ability, not a gate itself (e.g. a named
     tool mentioned as color under a JD that says tools/background "are not primary
     filters", or a specific tactic mentioned under a JD fundamentally asking for a
     trait like "context management" or "ownership"). For these, score whether the
     UNDERLYING ABILITY is evidenced anywhere in the CV by any means, not whether the
     named example itself literally appears. Conflating these two caused real,
     validated under-prediction on real shortlisting outcomes - don't repeat it.
2. Separately assess caliber_signal_pct: a holistic, JD-independent signal - does the
   candidate show direct senior-executive/founder proximity and trust (e.g. de facto
   Chief of Staff), elite pedigree, and large-scale demonstrated ownership? This is
   real signal validated against real shortlisting outcomes, but it SUPPLEMENTS
   coverage, it does not replace or inflate it - keep it as an honest, separate number.
3. Flag any tailored bullet that overclaims relative to what the original bullet actually
   supports, or that the candidate likely couldn't defend under a direct follow-up question.
4. Flag any violation of the style rules below.
5. Confirm structure is intact: same sections, same bullet count per role, nothing longer
   than roughly one line.
6. Decide pass/fail. Pass only if coverage is strong AND there are no defensibility or
   structural problems. caliber_signal_pct does not by itself decide pass/fail.

Style rules:
{style_rules}

Job description:
{jd_text}

Original CV:
{original_yaml}

Tailored CV:
{tailored_yaml}

Respond with ONLY a single JSON object (no prose, no markdown fences) matching exactly this shape:
{{
  "ats_keyword_coverage_pct": 0,
  "caliber_signal_pct": 0,
  "missing_keywords": ["string"],
  "defensibility_flags": ["string"],
  "style_violations": ["string"],
  "passed": true,
  "feedback_for_tailor": "concrete instructions if failing, empty string if passing"
}}
"""


MARKET_BENCHMARK_PROMPT_TEMPLATE = """You are benchmarking a candidate's CV against the real market, for this job description:

{jd_text}

IMPORTANT: There is no public database of "the resume that got this exact job" - real
hired candidates' resumes aren't published anywhere searchable. Don't pretend otherwise
or fabricate a specific comparison. If you have web search/browsing available, research
in this order:

1. FIRST, check if a specific named person posted or is named in this job description
   (a recruiter, hiring manager, or founder). If so, look at THEIR public profile/activity
   - their own hiring history (have they posted near-identical roles before, with stated
   criteria?) and their own career background are a much stronger, more specific signal
   than generic content, and are often just as easy to find. Don't skip straight to
   generic searches when a named individual is right there in the job posting.
2. THEN, check if any other current employees at the SAME company are publicly visible
   (search "[company name] [adjacent role/team] LinkedIn") - even people in a different
   function than the one hiring can reveal a real pattern in what that company values
   (e.g. a consulting background showing up in a completely different team is still a
   genuine signal about that company, not a coincidence to ignore).
3. THEN, broaden to what's publicly visible about people currently in comparable roles at
   comparable companies: common skills, typical background (e.g. consulting vs. in-house
   ops vs. startup), and what career-advice content commonly lists as expectations for
   this role category. Be honest if this broader layer doesn't surface verifiable named
   profiles - don't pad the result with unverified guesses.

Treat all of this as aggregate/individual PATTERN research, not a literal comparison to
"the person who got the job" - that specific data point doesn't exist publicly.

Candidate's tailored CV:
{cv_yaml}

Respond with ONLY a single JSON object (no prose, no markdown fences) matching exactly this shape:
{{
  "role_pattern_summary": "2-4 sentences",
  "commonly_seen_skills": ["string"],
  "gaps_vs_this_cv": ["string"],
  "strengths_vs_pattern": ["string"]
}}
"""


def build_market_benchmark_prompt(jd_text: str, doc: CVDocument) -> str:
    return MARKET_BENCHMARK_PROMPT_TEMPLATE.format(
        jd_text=jd_text,
        cv_yaml=yaml.dump(doc.model_dump(), sort_keys=False, allow_unicode=True),
    )


def build_tailor_prompt(doc: CVDocument, jd_text: str, company_notes: str | None, style_rules: list[dict]) -> str:
    return TAILOR_PROMPT_TEMPLATE.format(
        style_rules=style_rules_block(style_rules),
        cv_yaml=yaml.dump(doc.model_dump(), sort_keys=False, allow_unicode=True),
        jd_text=jd_text,
        company_notes=company_notes or "(none provided)",
        tagline=doc.header.tagline,
    )


def build_critic_prompt(jd_text: str, original_doc: CVDocument, tailored_doc: CVDocument, style_rules: list[dict]) -> str:
    return CRITIC_PROMPT_TEMPLATE.format(
        style_rules=style_rules_block(style_rules),
        jd_text=jd_text,
        original_yaml=yaml.dump(original_doc.model_dump(), sort_keys=False, allow_unicode=True),
        tailored_yaml=yaml.dump(tailored_doc.model_dump(), sort_keys=False, allow_unicode=True),
    )
