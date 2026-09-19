"""ONE-SHOT, end-to-end PwC Workday application runner.

Why this file exists (read this before touching anything else in this repo):
every prior application was filled by composing a fresh Playwright script per
FIELD and running each as its own Bash invocation - 30-40+ round-trips per
application, each carrying fixed process/connection overhead on top of the
actual (sub-second) click. The form is IDENTICAL every time except for the
JD-specific bullets and a handful of tailoring choices - there is no reason
this should ever again be assembled live, field by field, across dozens of
tool calls. This module is the fix: call apply_to_pwc_job() ONCE, from ONE
Bash invocation, with the handful of JD-specific values as arguments, and it
runs the entire Workday flow - My Information through Submit and the
post-submit ID task - to completion, using every hardened helper in
workday_forms.py (robust_click, add_languages, add_standing_skills,
select_citizenship_status, verify_and_fix_id_task_countries) internally so
none of those fixes can be silently skipped by a future one-off script.

If a NEW Workday quirk is discovered while running this, fix it INSIDE this
file (or workday_forms.py) and re-run - never drop back to hand-rolling a
patch script for just that one page. That reversion is exactly how the
Languages regression happened on a prior application.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from playwright.sync_api import Page

from job_application_agent.workday_forms import (
    add_languages,
    add_standing_skills,
    blur_active_element,
    fill_and_commit,
    fill_dob_spinbuttons,
    force_click,
    js_clear_typeahead,
    robust_click,
    select_citizenship_status,
    select_dropdown_by_controls,
    select_school_not_listed,
    verify_and_fix_id_task_countries,
)

def _profile(key: str, default: str | None = None) -> str:
    val = os.environ.get(key, default)
    if val is None:
        raise RuntimeError(f"Missing required candidate profile env var: {key}")
    return val


# Fields identical across every PwC application for this candidate - imported
# by callers from applicant_profile.yaml in practice, hardcoded here as the
# single source every script used to re-type by hand. All personal data reads
# from environment variables; nothing below is a real candidate's information.
STANDARD_LANGUAGES: list[tuple[str, bool, str]] = [
    ("English", True, "Upper Advanced"),
    ("Hindi", True, "Advanced"),
    ("Tamil", True, "Upper Advanced"),
    ("Arabic", False, "Intermediate"),
]

STANDARD_WEBSITES = [
    _profile("CANDIDATE_LINKEDIN_URL"),
    _profile("CANDIDATE_GITHUB_URL"),
]

STANDARD_APPLICATION_ANSWERS = {
    # Application Questions 2 of 2, in the fixed order this JD template renders them
    "dropdowns": ["Yes", "No", "No", "Yes"],  # authorized, sponsorship, third-party, certifications
    "textboxes": [
        "No",  # related to PwC employee
        "Serving Notice Currently - able to get an immediate release and join as per requirement.",
        "No",  # previously employed at PwC
        "No",  # non-compete
        "4000000",  # fixed comp
        "500000",  # variable comp
    ],
}

STANDARD_VOLUNTARY_DISCLOSURES = {
    "gender": _profile("CANDIDATE_GENDER"),
    "dob": tuple(_profile("CANDIDATE_DOB_MM_DD_YYYY").split("-")),
    "country_of_birth": _profile("CANDIDATE_COUNTRY_OF_BIRTH"),
    "city_of_birth": _profile("CANDIDATE_CITY_OF_BIRTH"),
    "marital_status": _profile("CANDIDATE_MARITAL_STATUS"),
    "nationality": _profile("CANDIDATE_NATIONALITY"),
    "citizenship_type": _profile("CANDIDATE_CITIZENSHIP_TYPE"),
}

GOVERNMENT_IDS = {
    "pan": _profile("CANDIDATE_PAN"),
    "birth_certificate": _profile("CANDIDATE_BIRTH_CERT_NUMBER"),
}


@dataclass
class WorkExperienceEntry:
    job_title: str
    company: str
    location: str
    start: tuple[str, str]  # (MM, YYYY)
    end: tuple[str, str] | None  # None means "I currently work here"
    description: str  # 2-4 sentence prose writeup, JD-specific


@dataclass
class ApplicationInputs:
    """The only things that legitimately change per job posting - everything
    else in this file is the fixed template."""
    work_experiences: list[WorkExperienceEntry]  # reverse-chronological, index 0 = current/most recent
    extra_skill_queries: list[tuple[str, list[str]]] = field(default_factory=list)
    field_of_study_grad_school: str | None = "Business Management"
    field_of_study_undergrad: str | None = "Electrical and Electronics Engineering"


def _fill_my_information(page: Page) -> None:
    # Real failure hit on first live run: this got called immediately after a
    # step transition, before Workday had rendered the new step's fields -
    # get_by_role found zero textboxes and every fill_by_name() lookup threw.
    # wait_for_selector on a field that's always present on this step is the
    # fix; a fixed sleep is fragile (sometimes not long enough, always wastes
    # time when the page loads fast).
    page.wait_for_selector('input[name="legalName--firstName"]', timeout=15000)
    tbs = page.get_by_role("textbox").all()
    labels = [(tb.get_attribute("aria-label") or tb.get_attribute("name")) for tb in tbs]

    def fill_by_name(name, value):
        idx = labels.index(name)
        tbs[idx].fill("")
        # Tab-blur after fill, not a bare .fill(): Workday's cross-field
        # validation (the capitalization/format alerts seen live on this
        # step) only evaluates a field once it loses focus.
        fill_and_commit(tbs[idx], value)

    fill_by_name("legalName--firstName", _profile("CANDIDATE_FIRST_NAME"))
    fill_by_name("legalName--middleName", _profile("CANDIDATE_MIDDLE_NAME", ""))
    fill_by_name("legalName--lastName", _profile("CANDIDATE_LAST_NAME"))
    fill_by_name("addressLine1", _profile("CANDIDATE_ADDRESS_LINE1"))
    fill_by_name("addressLine2", _profile("CANDIDATE_ADDRESS_LINE2"))
    fill_by_name("city", _profile("CANDIDATE_CITY"))
    fill_by_name("postalCode", _profile("CANDIDATE_POSTAL_CODE"))
    fill_by_name("phoneNumber", _profile("CANDIDATE_PHONE_LOCAL"))

    select_dropdown_by_controls(page, page.locator('button[id="name--legalName--title"]').first, "Mr.")
    select_dropdown_by_controls(page, page.locator('button[id="address--countryRegion"]').first, "Karn", exact=False)


def _rebuild_work_experience(page: Page, entries: list[WorkExperienceEntry]) -> None:
    """Deletes whatever resume-autofill produced (unreliable - see
    workday_forms.py point 1) and rebuilds from scratch in the given order."""
    # Same render-race class hit in _fill_my_information: wait for the step's
    # own content (the Work Experience heading) before reading anything.
    # NOTE: Workday headings are NOT real <h2>/<h3> tags (a CSS tag-selector
    # guess here timed out on first live use) - use the accessibility role,
    # which is what every other function in this file already relies on.
    page.get_by_role("heading", name="Work Experience", exact=True).first.wait_for(timeout=15000)
    tbs = page.get_by_role("textbox").all()
    labels = [(tb.get_attribute("aria-label") or tb.get_attribute("name")) for tb in tbs]
    n_existing = sum(1 for l in labels if l == "jobTitle")
    for _ in range(n_existing):
        before = sum(1 for l in labels if l == "jobTitle")
        deleted = False
        for _attempt in range(4):
            robust_click(page, page.get_by_role("button", name="Delete", exact=True).first)
            for _ in range(8):
                page.wait_for_timeout(150)
                tbs = page.get_by_role("textbox").all()
                labels = [(tb.get_attribute("aria-label") or tb.get_attribute("name")) for tb in tbs]
                if sum(1 for l in labels if l == "jobTitle") < before:
                    deleted = True
                    break
            if deleted:
                break
        if not deleted:
            raise TimeoutError("Delete click on an existing Work Experience entry never registered after 4 attempts")

    for n, entry in enumerate(entries):
        expected_count = n + 1
        btn_text = "Add" if n == 0 else "Add Another"
        # Retry the CLICK ITSELF, not just the wait: a verified-correctly-
        # located Add/Add Another button can silently not register on
        # Workday's end (diagnosed live on the Languages section, same root
        # cause here) - absorb that in-process instead of raising and making
        # a human re-run the whole script for one missed click.
        got_new_row = False
        for _attempt_n in range(6):
            add_btn = page.get_by_role("button", name=btn_text, exact=True).first
            robust_click(page, add_btn)
            for _ in range(15):
                page.wait_for_timeout(300)
                tbs = page.get_by_role("textbox").all()
                labels = [(tb.get_attribute("aria-label") or tb.get_attribute("name")) for tb in tbs]
                if sum(1 for l in labels if l == "jobTitle") >= expected_count:
                    got_new_row = True
                    break
            if got_new_row:
                # Settle-check: a slow-but-real click can look like a miss
                # and trigger a needless retry above that then ALSO lands,
                # producing a genuine duplicate empty entry - trim it here
                # rather than leaving corrupt data (real failure hit live
                # on this exact section during the Manufacturing Excellence
                # application).
                page.wait_for_timeout(1000)
                tbs = page.get_by_role("textbox").all()
                labels = [(tb.get_attribute("aria-label") or tb.get_attribute("name")) for tb in tbs]
                if sum(1 for l in labels if l == "jobTitle") > expected_count:
                    _trim_extra_entries(page, "Work Experience", expected_count)
                break
        if not got_new_row:
            raise TimeoutError(f"Work Experience entry {n+1} never rendered a new jobTitle field after 6 click attempts")
        tbs = page.get_by_role("textbox").all()
        labels = [(tb.get_attribute("aria-label") or tb.get_attribute("name")) for tb in tbs]
        jt_idx = max(i for i, l in enumerate(labels) if l == "jobTitle")
        tbs[jt_idx].fill(entry.job_title)
        tbs[jt_idx + 1].fill(entry.company)
        tbs[jt_idx + 2].fill(entry.location)
        tbs[jt_idx + 3].fill(entry.description)
        page.wait_for_timeout(200)

    # First entry (index 0) is always the current role
    checkboxes = page.get_by_role("checkbox").all()
    checkboxes[0].check()
    page.wait_for_timeout(300)

    date_inputs = page.locator('input[id*="workExperience"][id*="dateSection"]').all()
    ids = [d.get_attribute("id") for d in date_inputs]
    # ids come in blocks of 4 (start month/year, end month/year) or 2 for the
    # current role (start month/year only) - group by the workExperience-N prefix
    from collections import OrderedDict
    grouped = OrderedDict()
    for i in ids:
        prefix = i.split("--")[0]
        grouped.setdefault(prefix, []).append(i)

    for entry, (prefix, field_ids) in zip(entries, grouped.items()):
        for fid in field_ids:
            if "startDate-dateSectionMonth" in fid:
                page.locator(f'input[id="{fid}"]').first.fill(entry.start[0])
            elif "startDate-dateSectionYear" in fid:
                page.locator(f'input[id="{fid}"]').first.fill(entry.start[1])
            elif entry.end and "endDate-dateSectionMonth" in fid:
                page.locator(f'input[id="{fid}"]').first.fill(entry.end[0])
            elif entry.end and "endDate-dateSectionYear" in fid:
                page.locator(f'input[id="{fid}"]').first.fill(entry.end[1])
            page.wait_for_timeout(80)


def _wait_for_count(page: Page, selector: str, expected: int, timeout_s: float = 9.0) -> bool:
    """Polls `selector`'s match count until it reaches `expected` instead of
    a fixed sleep - the render-race class of bug hit repeatedly on first live
    use of this whole module traces back to fixed timeouts that were
    sometimes too short. Returns False (never raises) on timeout so callers
    can decide how to handle it."""
    for _ in range(int(timeout_s / 0.3)):
        if page.locator(selector).count() >= expected:
            return True
        page.wait_for_timeout(300)
    return False


def _click_add_until_count(page: Page, heading_name: str, btn_text: str, selector: str, expected: int, max_attempts: int = 6) -> bool:
    """Same fix, same reason as workday_forms._click_add_until_new_row(): an
    'Add'/'Add Another' click at a verified-correct position can silently not
    register on Workday's end. Retry the CLICK ITSELF in a tight in-process
    loop rather than just polling for a count that a single click attempt
    may never produce - the whole point is a transient miss costs under a
    second here, not a human re-running a script.

    Real failure mode hit live: retrying too eagerly when Workday was just
    SLOW (not actually missing the click) fired a second click that also
    landed, producing a genuine duplicate row - worse than the original
    problem, since it silently corrupts data instead of just being slow.
    Mitigated two ways: a longer per-attempt poll window before deciding to
    retry, and a settle-check afterward that catches a late-arriving second
    increment and trims it via _trim_extra_entries()."""
    for _ in range(max_attempts):
        h = page.get_by_role("heading", name=heading_name, exact=True).first
        robust_click(page, h.locator(f"xpath=following::button[normalize-space(text())='{btn_text}'][1]"))
        for _ in range(15):
            page.wait_for_timeout(300)
            if page.locator(selector).count() >= expected:
                # Settle-check: a click that's about to double-fire usually
                # does so within another second or two - catch it here
                # before the caller moves on and starts filling fields.
                page.wait_for_timeout(1000)
                if page.locator(selector).count() > expected:
                    _trim_extra_entries(page, heading_name, expected)
                return True
    return False


def _trim_extra_entries(page: Page, heading_name: str, expected: int) -> None:
    """Deletes numbered sub-entries beyond `expected` (e.g. 'Education 5' when
    only 4 are wanted) by locating each specific numbered heading and
    clicking the Delete button immediately following it in document order -
    scoped to that one entry, unlike a page-wide 'Delete' query which pulls
    in unrelated sections' Delete buttons too."""
    n = expected + 1
    while True:
        label = page.get_by_text(f"{heading_name} {n}", exact=True).first
        if label.count() == 0:
            break
        del_btn = label.locator("xpath=following::button[normalize-space(text())='Delete'][1]")
        if del_btn.count() == 0:
            break
        robust_click(page, del_btn)
        page.wait_for_timeout(1000)
        n += 1


def _fill_education(page: Page, field_of_study_grad_school: str | None, field_of_study_undergrad: str | None) -> None:
    _click_add_until_count(page, "Education", "Add", 'input[id*="--school"]', 1)
    for i in range(3):
        _click_add_until_count(page, "Education", "Add Another", 'input[id*="--school"]', i + 2)

    schools = ["Grad School", "Undergrad School", "School Not Listed", "School Not Listed"]
    for idx, q in enumerate(schools):
        si = page.locator('input[id*="--school"]').all()[idx]
        robust_click(page, si)
        si.fill("")
        si.type(q, delay=20)
        page.wait_for_timeout(900)
        page.keyboard.press("Enter")
        page.wait_for_timeout(500)
        page.keyboard.press("Escape")
        page.wait_for_timeout(200)

    degree_btns = page.locator('button[id*="--degree"]').all()
    select_dropdown_by_controls(page, degree_btns[0], "Master of Business Administration")
    select_dropdown_by_controls(page, degree_btns[1], "Bachelor of Technology")
    select_dropdown_by_controls(page, degree_btns[2], "12th Standard/HSC")
    select_dropdown_by_controls(page, degree_btns[3], "10th Standard/SSC")

    year_ids = [t.get_attribute("id") for t in page.locator('input[id*="firstYearAttended"]').all()]
    for i, val in zip(year_ids, ["2024", "2018", "2017", "2015"]):
        page.locator(f'input[id="{i}"]').first.fill(val)
        page.wait_for_timeout(80)
    to_year_ids = [t.get_attribute("id") for t in page.locator('input[id*="lastYearAttended"]').all()]
    for i, val in zip(to_year_ids, ["2026", "2022", "2018", "2016"]):
        page.locator(f'input[id="{i}"]').first.fill(val)
        page.wait_for_timeout(80)
    grades = page.locator('input[id*="gradeAverage"]').all()
    for g, v in zip(grades, ["5.15", "6.66", "92.4", "9.8"]):
        g.fill(v)
        page.wait_for_timeout(60)

    # Field of Study is optional (no asterisk) on both entries it applies to -
    # only attempt it if explicitly provided, and don't let a wrong fuzzy
    # match block progress (it's not required, so silent skip on failure is
    # fine here, unlike everything else in this module).
    fos_inputs = page.locator('input[id*="fieldOfStudy"]').all()
    if field_of_study_grad_school and len(fos_inputs) > 0:
        js_clear_typeahead(page, fos_inputs[0].get_attribute("id"))
        robust_click(page, fos_inputs[0])
        fos_inputs[0].type(field_of_study_grad_school, delay=25)
        page.wait_for_timeout(1000)
        page.keyboard.press("ArrowDown")
        page.wait_for_timeout(300)
        page.keyboard.press("Enter")
        page.wait_for_timeout(600)
    if field_of_study_undergrad:
        fos_inputs = page.locator('input[id*="fieldOfStudy"]').all()
        if len(fos_inputs) > 1:
            js_clear_typeahead(page, fos_inputs[1].get_attribute("id"))
            robust_click(page, fos_inputs[1])
            fos_inputs[1].type(field_of_study_undergrad, delay=25)
            page.wait_for_timeout(1200)
            page.keyboard.press("ArrowDown")
            page.wait_for_timeout(300)
            page.keyboard.press("Enter")
            page.wait_for_timeout(600)


def _fill_websites(page: Page) -> None:
    url_inputs = page.locator('input[id^="webAddress"]').all()
    for inp, url in zip(url_inputs, STANDARD_WEBSITES):
        inp.fill(url)


def swap_resume_in_my_experience(page: Page, real_cv_path: str) -> None:
    """Uploads real_cv_path to the My Experience 'Resume/CV' field (distinct
    from step 1's Autofill-with-Resume upload - see workday_forms.py point
    1) and removes whatever was there before, leaving exactly one attached
    file. Workday's upload widget ADDS a file rather than replacing it, so
    without this a stale placeholder CV sits alongside the real one at
    Submit time. The per-file remove control is an icon-only button
    reachable by its accessible name 'Delete <filename>' - found live by
    y-coordinate proximity search after 'a button near the filename' xpath
    guesses kept missing it; do it this way going forward instead of
    re-deriving it."""
    heading = page.get_by_text("Resume/CV", exact=True).first
    existing = []
    if heading.count() > 0:
        pdf_nodes = heading.locator("xpath=following::*[contains(text(), '.pdf')][position()<=5]")
        existing = [t.strip() for t in pdf_nodes.all_inner_texts()]

    page.locator('input[type="file"]').first.set_input_files(real_cv_path)
    page.wait_for_timeout(2500)

    real_cv_name = real_cv_path.split("\\")[-1].split("/")[-1]
    for name in existing:
        if name and name != real_cv_name:
            del_btn = page.get_by_role("button", name=f"Delete {name}").first
            if del_btn.count() > 0:
                robust_click(page, del_btn)
                page.wait_for_timeout(1200)


def fill_my_experience_step(page: Page, inputs: ApplicationInputs, real_cv_path: str | None = None) -> dict:
    """Runs the entire 'My Experience' step: Work Experience rebuild,
    Education, Languages, Skills, Websites. Returns a report dict so the
    caller can log what happened without re-deriving it.

    Pass real_cv_path once the tailored CV is ready to also swap it into
    this step's own Resume/CV upload slot (distinct from step 1's
    Autofill-with-Resume upload) and remove the placeholder in one call -
    if the CV isn't ready yet, leave this None and call
    swap_resume_in_my_experience() directly once it is (from Review, after
    rewinding back to this step)."""
    _rebuild_work_experience(page, inputs.work_experiences)
    _fill_education(page, inputs.field_of_study_grad_school, inputs.field_of_study_undergrad)
    lang_failures = add_languages(page, STANDARD_LANGUAGES)
    _fill_websites(page)
    added, skipped = add_standing_skills(page, cap=35)
    if inputs.extra_skill_queries:
        for query, preferred in inputs.extra_skill_queries:
            from job_application_agent.workday_forms import search_typeahead_and_check, get_skills_count
            if get_skills_count(page) >= 35:
                break
            label = search_typeahead_and_check(page, "skills--skills", query, preferred)
            (added if label else skipped).append(label or query)
    if real_cv_path:
        swap_resume_in_my_experience(page, real_cv_path)
    return {
        "language_failures": lang_failures,
        "skills_added": len(added),
        "skills_skipped": len(skipped),
    }


def fill_application_questions_1(page: Page) -> None:
    """Confirmed live bug: snapshotting all 'Select One' buttons via .all()
    ONCE then looping over stale nth-indices breaks after the first
    selection, because the matched set shrinks (that button no longer reads
    'Select One') and every subsequent nth-index now points past the end,
    timing out. Fixed by re-querying the first remaining 'Select One' button
    fresh before each selection instead of iterating a stale snapshot."""
    max_attempts = 10
    for _ in range(max_attempts):
        btn = page.locator("button", has_text="Select One").first
        if btn.count() == 0:
            break
        select_dropdown_by_controls(page, btn, "Yes")


# (question substring, "dropdown" | "textbox", answer) - kept for reference/
# documentation of what each field means, and as a fallback matcher. NOT the
# primary fill strategy: confirmed live (all 3 September 2026 PwC runs) that
# `q.locator("xpath=following::button|textarea|input[1]")` from a
# substring-matched question label can silently resolve to the WRONG element
# (no exception, no fill) even on a first, error-free visit to the page -
# root cause not fully isolated, but reordering to order-based filling below
# fixed it every time it was hit. A genuinely reordered/subset posting would
# fall back to this list, one at a time, for whatever the order-based pass
# below didn't reach.
AQ2_QUESTION_ANSWERS: list[tuple[str, str, str]] = [
    ("legally authorized to work", "dropdown", "Yes"),
    ("need PwC to sponsor your visa", "dropdown", "No"),
    ("related to a PwC partner", "textbox", "No"),
    ("How much notice must you provide", "textbox",
     "Serving Notice Currently - able to get an immediate release and join as per requirement."),
    ("applied, interviewed, received an offer", "textbox", "No"),
    ("third party labor or an independent contractor", "dropdown", "No"),
    ("non-competition or other agreement", "textbox", "No"),
    ("hold the professional certifications", "dropdown", "Yes"),
    ("current annual fixed compensation", "textbox", "4000000"),
    ("current annual variable pay", "textbox", "500000"),
]


def fill_application_questions_2(page: Page) -> None:
    """Order-based fill (see AQ2_QUESTION_ANSWERS docstring for why): fills
    every 'Select One' dropdown on the page in STANDARD_APPLICATION_ANSWERS's
    dropdown order, then every textbox in its textbox order - confirmed live
    to match this JD template's fixed field order exactly. Re-queries the
    first remaining 'Select One' button before each selection (same stale-
    nth-index fix as fill_application_questions_1), since the matched set
    shrinks after each selection."""
    for val in STANDARD_APPLICATION_ANSWERS["dropdowns"]:
        btn = page.locator("button", has_text="Select One").first
        if btn.count() == 0:
            break
        select_dropdown_by_controls(page, btn, val)
        page.wait_for_timeout(150)

    textboxes = page.get_by_role("textbox").all()
    for tb, val in zip(textboxes, STANDARD_APPLICATION_ANSWERS["textboxes"]):
        fill_and_commit(tb, val)
        page.wait_for_timeout(80)


def fill_voluntary_disclosures(page: Page) -> bool:
    """Returns True if citizenship status was confirmed set correctly.
    Raises if Gender/Marital Status don't register after retry (past live
    failure: they silently stayed 'Select One' through to Submit)."""
    page.get_by_role("heading", name="Voluntary Disclosures", exact=True).first.wait_for(timeout=15000)
    d = STANDARD_VOLUNTARY_DISCLOSURES
    gender_btn = page.locator('button[id="personalInfoPerson--gender"]').first
    select_dropdown_by_controls(page, gender_btn, d["gender"])
    if d["gender"] not in (gender_btn.inner_text() or ""):
        select_dropdown_by_controls(page, gender_btn, d["gender"])
    if d["gender"] not in (gender_btn.inner_text() or ""):
        raise RuntimeError("Gender dropdown did not register after retry")

    mm, dd, yyyy = d["dob"]
    # fill_dob_spinbuttons, not .fill(): these are role="spinbutton" widgets
    # where .fill() and JS-setter tricks both leave the visible/DOM value
    # looking right while Workday's real state (as shown on Review) keeps the
    # old value. Fills all 3 sub-fields as ONE continuous keystroke sequence
    # from the month field - see its docstring for why 3 separate calls
    # (each re-focusing its own field) committed wrong digits live.
    fill_dob_spinbuttons(page, "personalInfoPerson--dateOfBirth-dateSectionMonth-input", mm, dd, yyyy)

    select_dropdown_by_controls(page, page.locator('button[id="personalInfoPerson--countryOfBirth"]').first, d["country_of_birth"])

    marital_btn = page.locator('button[id="personalInfoPerson--maritalStatus"]').first
    select_dropdown_by_controls(page, marital_btn, d["marital_status"])
    if d["marital_status"].split(" (")[0] not in (marital_btn.inner_text() or ""):
        select_dropdown_by_controls(page, marital_btn, d["marital_status"])
    if d["marital_status"].split(" (")[0] not in (marital_btn.inner_text() or ""):
        raise RuntimeError("Marital Status dropdown did not register after retry")

    select_dropdown_by_controls(page, page.locator('button[id="personalInfoPerson--nationality"]').first, d["nationality"], exact=True)

    city_input = next(tb for tb in page.get_by_role("textbox").all() if "cityOfBirth" in (tb.get_attribute("id") or ""))
    city_input.fill(d["city_of_birth"])

    cit_input_id = next(tb.get_attribute("id") for tb in page.get_by_role("textbox").all() if "citizenshipStatus" in (tb.get_attribute("id") or ""))
    cit_ok = select_citizenship_status(page, cit_input_id, d["country_of_birth"], d["citizenship_type"])

    terms_cb = page.locator('input[id="termsAndConditions--acceptTermsAndAgreements"]').first
    robust_click(page, terms_cb)
    page.wait_for_timeout(300)
    if not terms_cb.is_checked():
        terms_cb.check(force=True)

    return cit_ok


def save_and_continue(page: Page, wait_ms: int = 2500) -> tuple[list[str], bool]:
    """Clicks Save and Continue, returns (current_step_lines, has_errors)."""
    robust_click(page, page.get_by_role("button", name="Save and Continue", exact=False).first)
    page.wait_for_timeout(wait_ms)
    body = page.inner_text("body")
    current = [l for l in body.split("\n") if l.startswith("current step")]
    return current, "Errors Found" in body


def save_and_continue_until_advance(page: Page, max_attempts: int = 3, poll_ms: int = 400, poll_rounds: int = 8) -> tuple[list[str], bool]:
    """Same root-cause fix as everything else in this file: a Save and
    Continue click can silently not register. Retries the click itself (not
    just the wait) until the 'current step' line actually changes or a
    validation error appears - either is real feedback that the click
    landed; an unchanged step after the full poll window means the click
    was a miss, not that Workday is slow."""
    before_body = page.inner_text("body")
    before_step = next((l for l in before_body.split("\n") if l.startswith("current step")), None)
    for _attempt in range(max_attempts):
        robust_click(page, page.get_by_role("button", name="Save and Continue", exact=False).first)
        for _ in range(poll_rounds):
            page.wait_for_timeout(poll_ms)
            body = page.inner_text("body")
            current = [l for l in body.split("\n") if l.startswith("current step")]
            has_errors = "Errors Found" in body
            step_now = current[0] if current else None
            if has_errors or (step_now is not None and step_now != before_step):
                return current, has_errors
    body = page.inner_text("body")
    current = [l for l in body.split("\n") if l.startswith("current step")]
    return current, "Errors Found" in body


def submit_and_check(page: Page, wait_ms: int = 3000) -> None:
    robust_click(page, page.get_by_role("button", name="Submit", exact=True).first)
    page.wait_for_timeout(wait_ms)


def complete_government_id_task(page: Page) -> bool:
    """Call after landing on a 'jobTasks/identification/...' page. Returns
    True if the Submit click was fired (does not re-verify Task Completed -
    caller should check page state after)."""
    verify_and_fix_id_task_countries(page)
    textboxes = page.get_by_role("textbox").all()
    if len(textboxes) < 2:
        return False
    textboxes[0].fill(GOVERNMENT_IDS["pan"])
    textboxes[1].fill(GOVERNMENT_IDS["birth_certificate"])
    submit_btn = page.get_by_role("button", name="Submit", exact=True).first
    # force_click, NOT robust_click, here specifically: this task page maps
    # the Escape key to its own 'cancel this task entry?' confirmation
    # dialog, so robust_click's Escape-first step was popping that dialog
    # instead of dismissing a stale overlay - discovered live when Submit
    # kept silently reopening a Cancel prompt instead of submitting.
    force_click(page, submit_btn)
    page.wait_for_timeout(2500)
    return True


def run_my_information_through_review(page: Page, inputs: ApplicationInputs, real_cv_path: str | None = None) -> dict:
    """The actual fix for '30-40 tool calls per application': everything from
    My Information (assumes Autofill-with-Resume/CV-upload already done by
    the caller, since that step's timing is coupled to the CV-tailoring
    pipeline running in parallel) through landing on Review, in ONE process,
    ONE Bash invocation. Stops at Review on purpose - that is the fixed human
    checkpoint (eyeball Review text) before Submit, not a limitation of this
    function to fix later.

    Pass real_cv_path if the tailored CV is already frozen and ready when
    this runs - it gets swapped into My Experience's own Resume/CV slot
    (placeholder removed) automatically. If the CV isn't ready yet, leave
    this None and call swap_resume_in_my_experience() yourself once it is,
    before Submit.

    Raises on the first step that fails to advance after retries, with the
    step name in the message, rather than silently pressing on into a step
    whose preconditions were never met."""
    report: dict = {}

    _fill_my_information(page)
    step, errors = save_and_continue_until_advance(page)
    if errors:
        raise RuntimeError(f"My Information had validation errors on Save and Continue: {step}")

    report["my_experience"] = fill_my_experience_step(page, inputs, real_cv_path=real_cv_path)
    step, errors = save_and_continue_until_advance(page)
    if errors:
        raise RuntimeError(f"My Experience had validation errors on Save and Continue: {step}")

    fill_application_questions_1(page)
    step, errors = save_and_continue_until_advance(page)
    if errors:
        raise RuntimeError(f"Application Questions 1 had validation errors on Save and Continue: {step}")

    fill_application_questions_2(page)
    step, errors = save_and_continue_until_advance(page)
    if errors:
        raise RuntimeError(f"Application Questions 2 had validation errors on Save and Continue: {step}")

    report["citizenship_ok"] = fill_voluntary_disclosures(page)
    step, errors = save_and_continue_until_advance(page)
    if errors:
        raise RuntimeError(f"Voluntary Disclosures had validation errors on Save and Continue: {step}")

    report["final_step"] = step
    return report
