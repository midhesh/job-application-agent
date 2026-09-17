from __future__ import annotations

import re

from pydantic import BaseModel

# Adapted from cv-tailor-agent's guardrails.py - same style_rules.yaml, applied to
# free-text application content (emails, cover notes, form answers) instead of
# fixed CV bullets. Can't check "no invented numbers vs. one original bullet" the
# same way here since there's no single source bullet being rewritten - instead
# this checks the STYLE rules mechanically and leaves fact-checking (does every
# claim trace to cv_master.yaml / applicant_profile.yaml) to human review, since
# free-text drafting can legitimately reference multiple source facts at once.

EM_DASH = "—"
CONTRASTIVE_RE = re.compile(
    r"\bnot\s+[^,.;]{1,40},?\s+(it'?s|it is|but)\b|\bisn'?t\s+about\s+[^,.;]{1,40},?\s+(it'?s|it is)\b",
    re.IGNORECASE,
)
ASYNDETIC_TRIPLE_RE = re.compile(
    r"\b(\w+),\s+(\w+),\s+(\w+)\b(?!\s*,?\s*and\b)"
)
CRUTCH_WORDS = ["actually", "real", "genuinely", "exact", "specific"]


class ContentGuardrailResult(BaseModel):
    passed: bool
    violations: list[str]


def check_content(text: str) -> ContentGuardrailResult:
    violations: list[str] = []

    if EM_DASH in text:
        violations.append("Contains an em dash.")

    if CONTRASTIVE_RE.search(text):
        violations.append("Contains 'not X, it's Y' / 'isn't about X, it's Y' contrastive framing.")

    if ASYNDETIC_TRIPLE_RE.search(text):
        violations.append("Possible bare three-item list with no conjunction (asyndetic triple) - verify by eye, this regex overfires on ordinary sentences.")

    crutch_counts = {w: len(re.findall(rf"\b{w}\b", text, re.IGNORECASE)) for w in CRUTCH_WORDS}
    stacked = {w: n for w, n in crutch_counts.items() if n > 1}
    if stacked:
        violations.append(f"Crutch word(s) used more than once: {stacked}")

    return ContentGuardrailResult(passed=not violations, violations=violations)
