from __future__ import annotations

import re

from cv_tailor_agent.contracts import CVRecord

WORD_RE = re.compile(r"[a-zA-Z]{3,}")
STOPWORDS = {
    "the", "and", "for", "with", "you", "your", "our", "are", "this", "that",
    "will", "have", "has", "from", "into", "who", "what", "role", "job", "team",
    "work", "working", "company", "candidate", "experience", "years", "location",
}


def _tokenize(text: str) -> set[str]:
    words = {w.lower() for w in WORD_RE.findall(text)}
    return words - STOPWORDS


def jaccard_similarity(text_a: str, text_b: str) -> float:
    a, b = _tokenize(text_a), _tokenize(text_b)
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def find_similar_cases(
    jd_text: str,
    records: list[CVRecord],
    exclude_company: str | None = None,
    top_n: int = 3,
) -> list[tuple[CVRecord, float]]:
    scored = []
    for r in records:
        if exclude_company and r.company == exclude_company:
            continue
        compare_text = (r.jd_text or "") + " " + (r.company_research or "")
        if not compare_text.strip():
            continue
        score = jaccard_similarity(jd_text, compare_text)
        scored.append((r, score))
    scored.sort(key=lambda x: x[1], reverse=True)
    return scored[:top_n]
