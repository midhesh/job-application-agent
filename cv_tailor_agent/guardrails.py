from __future__ import annotations

import re

from cv_tailor_agent.contracts import TailorPatch
from cv_tailor_agent.schema import CVDocument

NUMBER_RE = re.compile(r"\d[\d,.]*")
EM_DASH = "—"


def all_bullet_texts(doc: CVDocument) -> set[str]:
    texts: set[str] = set()
    for job in doc.experience:
        for sub in job.subsections:
            texts.update(b.text for b in sub.bullets)
    for group_list in (doc.academic_projects, doc.positions_of_responsibility, doc.additional_achievements):
        for grp in group_list:
            texts.update(b.text for b in grp.bullets)
    return texts


def extract_numbers(text: str) -> set[str]:
    return {n.replace(",", "") for n in NUMBER_RE.findall(text)}


TAGLINE_MAX_CHARS = 100  # 105 chars is confirmed to fit on one line at the template's
# font/width; 109 is confirmed to wrap ("Management" orphaned alone on line 2, a real
# defect caught by actually rendering ANSR's first draft). 100 keeps safe margin.


def validate_patch(doc: CVDocument, patch: TailorPatch) -> list[str]:
    errors: list[str] = []
    existing = all_bullet_texts(doc)

    if len(patch.tagline) > TAGLINE_MAX_CHARS:
        errors.append(
            f"tagline is {len(patch.tagline)} chars, over the {TAGLINE_MAX_CHARS}-char "
            f"safe limit - it will wrap to a second line: {patch.tagline!r}"
        )

    for edit in patch.bullet_edits:
        if edit.original_text not in existing:
            errors.append(f"original_text not found verbatim in cv_master: {edit.original_text!r}")
            continue
        orig_numbers = extract_numbers(edit.original_text)
        new_numbers = extract_numbers(edit.new_text)
        invented = new_numbers - orig_numbers
        if invented:
            errors.append(f"new_text introduces numbers not in original ({invented}): {edit.new_text!r}")
        if EM_DASH in edit.new_text or EM_DASH in patch.tagline:
            errors.append("em dash character found in rewritten text or tagline")
        if len(edit.new_text) > len(edit.original_text) * 1.4:
            errors.append(f"new_text is too long relative to original (must stay one line): {edit.new_text!r}")

    valid_keys = {f"{job.organization}|{job.title}" for job in doc.experience}
    for key, order in patch.subsection_order.items():
        if key not in valid_keys:
            errors.append(f"subsection_order references unknown experience entry: {key!r}")
            continue
        job = next(j for j in doc.experience if f"{j.organization}|{j.title}" == key)
        real_headings = {s.heading for s in job.subsections if s.heading}
        if set(order) != real_headings:
            errors.append(f"subsection_order for {key!r} does not match its real subsection headings")

    return errors


def apply_patch(doc: CVDocument, patch: TailorPatch) -> CVDocument:
    new_doc = doc.model_copy(deep=True)
    new_doc.header.tagline = patch.tagline

    edit_map = {e.original_text: e.new_text for e in patch.bullet_edits}

    for job in new_doc.experience:
        for sub in job.subsections:
            for bullet in sub.bullets:
                if bullet.text in edit_map:
                    bullet.text = edit_map[bullet.text]
        key = f"{job.organization}|{job.title}"
        if key in patch.subsection_order:
            order = patch.subsection_order[key]
            job.subsections.sort(
                key=lambda s: order.index(s.heading) if s.heading in order else len(order)
            )

    for group_list in (new_doc.academic_projects, new_doc.positions_of_responsibility, new_doc.additional_achievements):
        for grp in group_list:
            for bullet in grp.bullets:
                if bullet.text in edit_map:
                    bullet.text = edit_map[bullet.text]

    return new_doc
