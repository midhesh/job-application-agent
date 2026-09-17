"""Reusable Workday "My Experience"/"My Information" page automation, built
from the real bugs hit filling PwC's Business Strategy & Operations Associate
2 application - the reference case for every subsequent PwC-referred
application, all on the same Workday tenant (pwc.wd3.myworkdayjobs.com).

Key discoveries this module encodes so they never have to be re-derived:

1. Workday's resume-autofill on "My Experience" is unreliable: it can drop the
   most recent/current role entirely and scramble other roles' description
   text into the wrong entry. There is no reorder control (no up/down, no
   drag handle exposed as a button) - only Delete per entry. The reliable fix
   is to delete ALL entries and re-add them in the desired order via connect()
   -> rebuild_work_experience().
2. The Region dropdown for Indian states renders with diacritics (e.g.
   "Karn?taka" for Karnataka) due to a font/encoding quirk - match by list
   position/substring via select_dropdown_by_controls(), not exact ASCII
   string comparison.
3. Phone Number wants ONLY the local number (no country code, that's a
   separate field) - passing the full "+91 <number>" string fails validation,
   the bare local number passes.
4. Field of Study / School / Skills are all the same "multiselect typeahead"
   widget. Its search box does NOT reliably clear via locator.fill("") -
   leftover text concatenates across calls, which silently corrupts every
   subsequent search. Always force-clear via JS property setter
   (js_clear_typeahead) before typing into one of these fields.
5. The typeahead's real "Search Results" checkbox panel renders ABOVE the
   input (not below, where a normal dropdown would appear) after typing text
   and pressing Enter - looking below the input finds nothing. Match a
   checkbox by its nearest ancestor <li>/<label> text via
   search_typeahead_and_check().
6. A School not in Workday's database: type the literal phrase
   "School Not Listed" and press Enter - Workday accepts it directly as the
   selected value, no separate free-text name field appears.
7. Degree dropdown: prefer the closest formal-degree option over a generic
   "Postgraduate (Diploma)" entry when the candidate's actual diploma is
   functionally equivalent (clears more ATS filters) - a standing choice,
   not one to re-ask about.
8. Field of Study for a niche engineering major: match on the closest
   available option in Workday's fixed list rather than the exact wording
   from the transcript - a standing choice, not one to re-ask about.
9. Some past employers share a city that Workday's resume parser leaves
   blank on import - fill in Location manually every time for those roles.
10. Internship roles should have "(MBA Summer Internship)" or "(Internship)"
    appended to the Job Title so they aren't misread as full-time roles.
11. Role Description fields should be a short 2-4 sentence prose writeup
    (crisp, JD-aligned paraphrase of the real CV bullets), never a raw bullet
    dump - see build_role_description_prompts() callers for the pattern.
12. Citizenship Status (Voluntary Disclosures step) is NOT a flat typeahead -
    it's a two-level, ReactVirtualized country picker (pick a country, then a
    citizenship type for that country) rendered in a portal, ~7000px tall
    virtual list of which only ~4 rows are ever in the DOM at once. Typing
    into its search box does NOT filter the list (confirmed dead end, cost
    real time chasing it). aria-controls is absent. Generic text-matching
    (get_by_text("India")) is unsafe - the same country name also appears in
    the separate, already-set Country of Birth / Primary Nationality
    dropdowns elsewhere on the same page, and a wrong match silently
    overwrites one of those instead of erroring. The ONLY reliable method is
    select_citizenship_status() below: locate the specific
    ReactVirtualized__Grid element, move the mouse over it and fire a real
    (non-JS-forced) wheel event to scroll it (setting .scrollTop directly via
    JS does not trigger React's re-render), then click the real, now-visible
    row by exact text scoped to that grid element specifically.
13. Government ID task country/territory dropdowns can silently default to
    the WRONG country (observed: "Bangladesh" for both the National ID and
    Government ID sections, on two separate applications) - Workday does not
    default to the candidate's actual nationality. Always call
    verify_and_fix_id_task_countries() before filling ID numbers or
    submitting that task; never assume the prefilled country is correct.
14. After ANY action that can trigger a DOM re-render on Workday (adding a
    row, submitting a dropdown selection, an Escape-driven overlay dismissal),
    element ids for OTHER fields on the same page can silently change (seen:
    cityOfBirth -> personalInfoPerson--cityOfBirth after a nearby dropdown
    interaction). Never cache a locator across such an action and reuse it -
    re-query by role/label immediately before the next use, and prefer
    re-locating by a stable substring (e.g. id*="cityOfBirth") over an exact
    id match when the exact id has already been observed to drift once in a
    session.
15. A whole application (Integrated Strategy & Operations - Associate 2)
    reached Submit with only 1 of 4 intended Languages entries present - the
    'Add Another' flow silently overwrote the existing entry instead of
    creating a new one, and every individual dropdown-select call still
    returned success, so nothing in the per-step logic ever signaled a
    problem. The defect was only caught after the fact, by the user, on an
    already-submitted application. Root fix: NEVER hand-roll the
    click-select-verify sequence for languages, skills, or any other
    'Add Another'-driven repeating section inline in a script again - use
    add_languages() (and add_standing_skills() for skills, which already had
    this class of self-check) below, both of which re-read the actual DOM
    state after every add and retry or report failure rather than trusting a
    click's return value. If a new repeating-section bug appears, fix it
    inside the shared helper, not in the one-off script that found it -
    otherwise the next application hits the exact same bug again.
16. Every click in this module goes through robust_click() (Escape, then
    force=True) - not as a stylistic default, but because the class of bug
    in points 5 and 15 traces back to the same root cause (a stale overlay
    physically blocking the real click target) recurring in code that used a
    plain .click() instead of the already-known fix. Do not write a bare
    `.click()` in a new Workday script; call robust_click(page, locator).
"""
from __future__ import annotations

from playwright.sync_api import Page, sync_playwright

# Illustrative shape only - real values are loaded from a candidate profile
# (env vars / local config) at call time, never hardcoded here.
WORKDAY_STANDING_CHOICES = {
    "grad_school_degree": "Master of Business Administration",  # not "Postgraduate (Diploma)"
    "undergrad_field_of_study": "Electrical and Electronics Engineering",  # not "Electronics"
    "past_employer_1_location": "<from candidate profile>",
    "past_employer_2_location": "<from candidate profile>",
    "phone_number_local_only": "<from candidate profile>",  # no "+91 " prefix - that's a separate field
}


def connect(cdp_port: int = 9333):
    """Same CDP-connect pattern as form_filling.connect(), but returns the
    specific tab whose URL contains 'myworkday' rather than pages[0] - a
    PwC Workday session commonly has multiple tabs open across a batch of
    referred applications, all sharing the one logged-in profile."""
    p = sync_playwright().start()
    browser = p.chromium.connect_over_cdp(f"http://localhost:{cdp_port}")
    for ctx in browser.contexts:
        for page in ctx.pages:
            if "myworkday" in page.url:
                page.bring_to_front()
                return p, browser, page
    raise RuntimeError("No open Workday tab found - navigate to the job posting first.")


def js_clear_typeahead(page: Page, input_id: str) -> None:
    """The multiselect typeahead's search box (Field of Study, School, Skills)
    does not reliably clear via locator.fill('') - leftover text concatenates
    across calls. This sets .value via the native property setter and fires
    the input event React listens for, which is the only combination that
    reliably empties it."""
    page.evaluate(
        """
        (id) => {
            const el = document.getElementById(id);
            const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
            setter.call(el, '');
            el.dispatchEvent(new Event('input', { bubbles: true }));
        }
        """,
        input_id,
    )


def robust_click(page: Page, locator, settle_ms: int = 200) -> None:
    """The ONE click path every interaction in this module should go through -
    written after tracing a whole class of recurring failures back to a
    single cause: a stale overlay (a prior typeahead's 'Search Results' panel,
    a just-closed dropdown's listbox) is still in the DOM and physically
    intercepts pointer events on the real target, so a plain .click() burns
    the tool's full ~30s retry budget before failing outright. Escape first to
    ask Workday to dismiss whatever's open, then force=True so a residual
    overlay node that didn't actually get removed can't block the click
    anyway. Cheaper to always pay the ~200ms Escape cost than to debug this
    failure mode again per-script, which is what happened repeatedly before
    this existed."""
    page.keyboard.press("Escape")
    try:
        locator.evaluate("el => el.scrollIntoView({block: 'center'})")
    except Exception:
        pass
    page.wait_for_timeout(settle_ms)
    locator.click(force=True)


def blur_active_element(page: Page) -> None:
    """Fires a real blur on whatever currently has focus. Several React-driven
    widgets (Workday's dropdowns included) only commit a just-made selection
    or a just-typed value to their internal state on blur, not on the click/
    keystroke itself - per the Playwright reliability write-up this session
    reviewed, this is a known source of 'looked filled but didn't register'
    failures. Cheap (one JS call) so it's safe to call defensively after any
    selection-committing action."""
    try:
        page.evaluate("() => { if (document.activeElement) document.activeElement.blur(); }")
    except Exception:
        pass


def fill_and_commit(locator, value: str) -> None:
    """Fill a text field and press Tab to blur it, so React-driven validators
    that only run on blur (not on every keystroke) actually see the final
    value before the caller moves on. Prefer this over a bare .fill() for any
    field whose value feeds into cross-field validation (dates, comp figures,
    IDs) - a bare .fill() left several such fields technically populated but
    not yet 'seen' by Workday's own validation this session."""
    locator.fill(value)
    locator.press("Tab")


def force_click(page: Page, locator) -> None:
    """Companion to robust_click(), for the ONE situation robust_click is
    wrong for: clicking an option/item inside a menu that a PRIOR call just
    opened. robust_click's Escape-first step exists to dismiss a STALE
    overlay before opening something new - but pressed here, it dismisses
    the very menu you're trying to click into, before the click happens,
    causing a timeout waiting for a now-hidden option (hit live on the first
    real run of this module's own select_dropdown_by_controls). Use
    robust_click() to open a dropdown/typeahead; use force_click() for every
    click on the resulting option/item while that menu is still open.

    Blurs afterward: the click commits the option visually, but the
    underlying React state (and any cross-field validation keyed off it)
    can lag until a real blur fires - see blur_active_element()."""
    try:
        locator.evaluate("el => el.scrollIntoView({block: 'center'})")
    except Exception:
        pass
    locator.click(force=True)
    blur_active_element(page)


def select_dropdown_by_controls(page: Page, button_locator, option_text: str, exact: bool = True) -> bool:
    """For simple 'Select One' dropdowns (Prefix, Region, Phone Device Type,
    Degree) that DO expose aria-controls pointing at their listbox. Matches
    by index/substring rather than exact string since some options (Indian
    state names) render with diacritics.

    Retries the OPENING click itself (not just the wait) up to 4 times: a
    click at a verified-correct position can silently not register on
    Workday's end (same root cause diagnosed for Add/Add Another buttons -
    this is what caused Gender/Marital Status to not register on a live
    Voluntary Disclosures run despite the call site looking correct)."""
    controls = None
    for _attempt in range(4):
        robust_click(page, button_locator)
        for _ in range(6):
            page.wait_for_timeout(150)
            controls = button_locator.get_attribute("aria-controls") or button_locator.get_attribute("aria-owns")
            if controls:
                break
        if controls:
            break
    if not controls:
        return False
    listbox = page.locator(f'[id="{controls}"]')
    for opt in listbox.locator('[role="option"]').all():
        text = opt.inner_text().strip()
        matched = (text == option_text) if exact else (option_text.lower() in text.lower())
        if matched:
            force_click(page, opt)
            page.wait_for_timeout(400)
            return True
    return False


def fill_dob_spinbuttons(page: Page, month_input_id: str, mm: str, dd: str, yyyy: str) -> None:
    """Fills Workday's Date of Birth month/day/year fields, which render as
    role="spinbutton" widgets - a DIFFERENT component from the plain-text-
    styled date inputs used elsewhere (e.g. Work Experience start/end dates,
    which a bare .fill() handles fine). Their visible bounding box is
    near-zero-size (a hidden ARIA proxy behind a separately-rendered visual
    layer), and critically: neither .fill() nor a native-value-setter-plus-
    dispatchEvent("input"/"change") trick (which works for ordinary text
    inputs) actually commits the value to Workday's underlying React state for
    THIS widget - both leave input_value()/aria-valuenow looking correct while
    the Review page still shows the OLD value. Only the Review step's rendered
    text after Save and Continue is a reliable verification signal here -
    callers should still spot-check it rather than trusting this blindly.

    Root causes confirmed live across two separate PwC Workday runs:

    1. `field.click(force=True)` throws `Element is outside of the viewport`
       even with force=True, because the real bounding box is near-zero-size
       (not just visually hidden - Playwright's own viewport-intersection
       check rejects it). Fix: focus it directly via JS (`el.focus()`).
    2. Filling month/day/year as three SEPARATE calls (each re-focusing its
       own field) produces WRONG committed values ("06"->"02", "28"->"00").
       Root cause: this widget auto-advances focus to the next sub-field
       after its 2nd digit is typed (like a native date-of-birth UX control),
       and a separate call's `el.focus()` on the next field can race with
       that auto-advance and bounce focus back, so digits land in the wrong
       field. Fix: focus the MONTH field ONCE, then type the full 8-digit
       string (MMDDYYYY) continuously and let native auto-advance move focus
       between sub-fields - never re-focus mid-sequence.

    Verified fixed live: Review page rendered "06/28/2000" correctly for
    Date of Birth using this exact method (continuous 8-digit typing from the
    month field, ~120ms per keystroke)."""
    field = page.locator(f'input[id="{month_input_id}"]').first
    field.evaluate("el => el.scrollIntoView({block: 'center'})")
    field.evaluate("el => el.focus()")
    page.wait_for_timeout(150)
    # Select-all-then-delete via keyboard (not .fill, which was proven
    # unreliable here) before typing, so a shorter new value doesn't leave
    # trailing old digits.
    page.keyboard.press("Control+A")
    page.keyboard.press("Delete")
    for ch in f"{mm}{dd}{yyyy}":
        page.keyboard.press(ch)
        page.wait_for_timeout(120)
    page.wait_for_timeout(200)
    page.keyboard.press("Tab")
    page.wait_for_timeout(200)


def get_typeahead_search_results(page: Page) -> list[dict]:
    """Returns [{id, text}] for every currently-visible checkbox in the
    typeahead's 'Search Results' panel, which renders ABOVE the input after
    typing + Enter - not below it, where a normal dropdown would be. Filters
    out unrelated checkboxes (currently-work-here, language-fluent) that also
    live on the page."""
    return page.evaluate(
        """
        () => {
          const cbs = Array.from(document.querySelectorAll('input[type="checkbox"]'));
          return cbs.filter(cb => {
            const r = cb.getBoundingClientRect();
            return r.width > 0 && r.height > 0
                && !cb.id.includes('workExperience') && !cb.id.includes('language');
          }).map(cb => {
            const label = cb.closest('li, div[role="option"], label');
            return {id: cb.id, text: label ? label.innerText : ''};
          });
        }
        """
    )


def search_typeahead_and_check(page: Page, input_id: str, query: str, preferred_labels: list[str]) -> str | None:
    """Types `query` into a multiselect typeahead (School, Field of Study,
    Skills), presses Enter to trigger real search results, and checks the
    first result matching any of `preferred_labels` (exact match, in
    priority order; falls back to shortest-label substring match). Returns
    the label actually selected, or None if nothing matched.

    Always call js_clear_typeahead() immediately before this - do not rely
    on locator.fill('') to clear leftover text from a prior call."""
    input_ = page.locator(f'input[id="{input_id}"]').first
    # robust_click (Escape + force=True) is required, not optional: after any
    # prior search (this call or another field's), a "Suggested skills"
    # overlay can be left rendered and physically intercepts pointer events
    # on the real input - see module docstring points 5, 15, 16.
    robust_click(page, input_)
    js_clear_typeahead(page, input_id)
    page.wait_for_timeout(200)
    input_.press_sequentially(query, delay=20)
    page.wait_for_timeout(400)
    page.keyboard.press("Enter")

    # POLL for real search results instead of a fixed sleep: a fixed wait
    # here was the exact bug reported live - the network request for a given
    # query hadn't resolved yet, so `options` came back empty, the query got
    # wrongly recorded as "skipped", and the caller moved straight to the
    # next query while this one's results were still arriving (looked like a
    # rapid repetitive loop from the browser). Retry the Enter itself too, in
    # case a transient click/keypress miss (not just slow network) is why no
    # results ever appeared - same root-cause pattern as every other
    # click-retry helper in this module.
    options: list[dict] = []
    for _attempt in range(3):
        for _ in range(20):
            page.wait_for_timeout(250)
            options = get_typeahead_search_results(page)
            if options:
                break
        if options:
            break
        page.keyboard.press("Enter")

    chosen = None
    for pref in preferred_labels:
        for opt in options:
            if opt["text"].strip().lower() == pref.lower():
                chosen = opt
                break
        if chosen:
            break
    if not chosen:
        q_lower = query.lower()
        candidates = [o for o in options if q_lower in o["text"].lower()]
        if candidates:
            chosen = min(candidates, key=lambda o: len(o["text"]))

    if chosen:
        # force_click, not robust_click: this checkbox is INSIDE the
        # "Search Results" panel that Enter just opened - an Escape here
        # would dismiss that panel before the click lands (see force_click's
        # docstring for the general rule).
        force_click(page, page.locator(f'input[id="{chosen["id"]}"]'))
        # Verify the checkbox actually registered as checked before treating
        # this as done and clearing the search term - the exact "select
        # checkbox, THEN ONLY erase and search next" ordering the user
        # specified. A quick retry if the first click didn't take.
        checked = False
        for _ in range(6):
            page.wait_for_timeout(150)
            if page.locator(f'input[id="{chosen["id"]}"]').first.is_checked():
                checked = True
                break
        if not checked:
            force_click(page, page.locator(f'input[id="{chosen["id"]}"]'))
            page.wait_for_timeout(400)
        return chosen["text"]
    return None


def get_language_entries(page: Page) -> list[dict]:
    """Returns [{prefix, language, fluent, overall}] for every Language entry
    currently on the page, read fresh from the DOM (never trust a value you
    set earlier without re-reading it - see add_languages() docstring)."""
    lang_btns = page.locator('button[id$="--language"]').all()
    lang_btns = [b for b in lang_btns if "language-" in (b.get_attribute("id") or "")]
    entries = []
    for btn in lang_btns:
        prefix = btn.get_attribute("id").replace("--language", "")
        fluent_cb = page.locator(f'input[id="{prefix}--native"]').first
        overall_btns = page.locator(f'button[id^="{prefix}--"]').all()
        overall_btns = [b for b in overall_btns if b.get_attribute("id") != f"{prefix}--language"]
        entries.append({
            "prefix": prefix,
            "language": btn.inner_text().strip(),
            "fluent": fluent_cb.is_checked() if fluent_cb.count() else False,
            "overall": overall_btns[0].inner_text().strip() if overall_btns else "",
        })
    return entries


def add_languages(page: Page, languages: list[tuple[str, bool, str]]) -> list[str]:
    """Fills the Languages section for the given [(name, fluent, overall_level), ...]
    list, e.g. [("English", True, "Upper Advanced"), ("Arabic", False, "Intermediate")].

    Written after a real defect shipped on a submitted application: the
    'Add Another' flow silently OVERWROTE the previous language entry instead
    of creating a new one (root cause never fully isolated - suspected a
    stale-overlay click landing on the wrong, already-open dropdown, the same
    family of bug robust_click() exists to prevent). The bug was invisible in
    the moment because each individual dropdown-select call still returned
    True; only a distinct-entry COUNT check after the fact would have caught
    it, and no such check was being run. This function is the fix: after
    every single language is added, it re-reads ALL entries via
    get_language_entries() and asserts the count grew by exactly 1 and the
    new entry's language/fluent/overall all match what was requested - if
    not, it retries that one language once via robust_click() before giving
    up and returning it in the failure list. Never hand-roll this loop again;
    if a new failure mode shows up, fix it here so every future application
    inherits the fix.

    Returns the list of (name) that could not be verified as correctly added
    after retrying - empty list means everything succeeded and was confirmed
    correct, not just attempted."""
    failed: list[str] = []

    def _select_from_button(btn, text: str) -> bool:
        robust_click(page, btn)
        page.wait_for_timeout(500)
        controls = btn.get_attribute("aria-controls") or btn.get_attribute("aria-owns")
        if not controls:
            return False
        listbox = page.locator(f'[id="{controls}"]')
        for opt in listbox.locator('[role="option"]').all():
            if opt.inner_text().strip() == text:
                force_click(page, opt)  # option inside the menu btn just opened - see force_click docstring
                page.wait_for_timeout(300)
                return True
        return False

    def _click_add_until_new_row(before_count: int, max_attempts: int = 6) -> bool:
        """The actual defect diagnosed live: this exact button, at the
        verified-correct screen position, sometimes does not register a
        click on Workday's end - not a wrong-selector problem (confirmed by
        bounding-box inspection during debugging) but a real, intermittent
        UI miss on Workday's side. The previous version only tried once and
        surfaced the failure to a human, who then had to manually re-invoke
        a whole new script per retry - each retry costing a full tool
        round-trip (10-60s) instead of milliseconds. The fix is this loop:
        absorb the flake IN-PROCESS with a short poll between attempts, so a
        transient miss costs under a second instead of a human noticing,
        writing a new script, and running it again."""
        for _ in range(max_attempts):
            heading = page.get_by_role("heading", name="Languages", exact=True).first
            btn_text = "Add" if before_count == 0 else "Add Another"
            add_btn = heading.locator(f"xpath=following::button[normalize-space(text())='{btn_text}'][1]")
            robust_click(page, add_btn)
            for _ in range(15):
                page.wait_for_timeout(300)
                if len(get_language_entries(page)) > before_count:
                    # Settle-check: Workday can be slow rather than actually
                    # having missed the click - a real double-fire (this
                    # loop's own retry landing on top of a delayed first
                    # click) showed up live as a genuine duplicate empty
                    # 'Select One' row. Give it a moment, then trim any
                    # overshoot instead of leaving a corrupt extra entry.
                    page.wait_for_timeout(1000)
                    entries_now = get_language_entries(page)
                    while len(entries_now) > before_count + 1:
                        extra_prefix = entries_now[before_count]["prefix"]
                        extra_row = page.locator(f'button[id="{extra_prefix}--language"]').first
                        del_btn = extra_row.locator("xpath=following::button[normalize-space(text())='Delete'][1]")
                        if del_btn.count() == 0:
                            break
                        robust_click(page, del_btn)
                        page.wait_for_timeout(800)
                        entries_now = get_language_entries(page)
                    return True
        return False

    for name, fluent, level in languages:
        starting_count = len(get_language_entries(page))

        def _attempt():
            before_count = len(get_language_entries(page))
            if not _click_add_until_new_row(before_count):
                return False  # the Add itself never registered after retrying
            entries_now = get_language_entries(page)
            new_prefix = entries_now[-1]["prefix"]
            new_lang_btn = page.locator(f'button[id="{new_prefix}--language"]').first
            if not _select_from_button(new_lang_btn, name):
                return False
            if fluent:
                page.locator(f'input[id="{new_prefix}--native"]').first.check()
                page.wait_for_timeout(150)
            overall_btns = page.locator(f'button[id^="{new_prefix}--"]').all()
            overall_btns = [b for b in overall_btns if b.get_attribute("id") != f"{new_prefix}--language"]
            if not overall_btns or not _select_from_button(overall_btns[0], level):
                return False
            return True

        def _verify() -> bool:
            entries_after = get_language_entries(page)
            return (
                len(entries_after) == starting_count + 1
                and entries_after[-1]["language"] == name
                and entries_after[-1]["fluent"] == fluent
                and entries_after[-1]["overall"] == level
            )

        ok = False
        # 3 full attempts in-process (not 1) - each is now internally
        # resilient too (_click_add_until_new_row already retries the click
        # itself), so this outer loop only needs to cover the rarer case of
        # the click succeeding but the language/overall select failing.
        for _ in range(3):
            _attempt()
            ok = _verify()
            if ok:
                break
        if not ok:
            failed.append(name)

    return failed


def select_school_not_listed(page: Page, school_input_id: str) -> None:
    """Type the literal phrase 'School Not Listed' and press Enter - Workday
    accepts it directly, no separate free-text school-name field appears."""
    input_ = page.locator(f'input[id="{school_input_id}"]').first
    robust_click(page, input_)
    input_.fill("")
    input_.type("School Not Listed", delay=25)
    page.wait_for_timeout(1000)
    page.keyboard.press("Enter")
    page.wait_for_timeout(600)


def select_citizenship_status(
    page: Page,
    citizenship_input_id: str,
    country: str = "India",
    citizenship_type: str = "Citizen (India)",
) -> bool:
    """Sets the Voluntary Disclosures step's Citizenship Status field, a
    two-level ReactVirtualized country/type picker - NOT a flat typeahead.
    See module docstring point 12 for why every other approach (typing to
    filter, aria-controls, generic text matching, JS scrollTop) fails or is
    actively unsafe on this specific widget.

    Real, hard-won method: open the picker, find its ReactVirtualized__Grid,
    hover the mouse over it and fire a REAL wheel event (not JS-set
    scrollTop, which the virtualized list ignores) to bring `country` into
    the ~4-row rendered window, click it by exact text scoped to that grid
    element only, then click `citizenship_type` from the resulting drill-down
    list the same way. Returns True if the final chip was confirmed selected.

    Confirmed live twice on separate PwC runs: right after the opening click,
    the grid can genuinely not be attached yet - `grid.count() == 0` and
    `bounding_box()` comes back None - for up to a few hundred ms after the
    700ms settle wait already here (a moment later, a standalone check found
    the exact same selector with count 1). Fixed with a poll loop instead of
    a single check. Separately confirmed live: even with that poll loop, a
    single call can still return False as a one-off transient (suspected
    cause: leftover error-banner state on screen from an immediately-prior
    failed Save-and-Continue attempt) - an identical, unmodified re-call
    moments later succeeded cleanly. This function now retries itself once
    automatically before giving up, rather than surfacing a single transient
    failure to the caller as a hard False.
    """
    ok = _select_citizenship_status_once(page, citizenship_input_id, country, citizenship_type)
    if ok:
        return True
    page.wait_for_timeout(800)
    return _select_citizenship_status_once(page, citizenship_input_id, country, citizenship_type)


def _select_citizenship_status_once(
    page: Page,
    citizenship_input_id: str,
    country: str,
    citizenship_type: str,
) -> bool:
    input_ = page.locator(f'input[id="{citizenship_input_id}"]').first
    robust_click(page, input_)
    page.wait_for_timeout(700)

    grid = page.locator(".ReactVirtualized__Grid.ReactVirtualized__List").first
    gb = None
    for _ in range(10):
        if grid.count() > 0:
            gb = grid.bounding_box()
            if gb:
                break
        page.wait_for_timeout(300)
    if not gb:
        return False
    page.mouse.move(gb["x"] + gb["width"] / 2, gb["y"] + gb["height"] / 2)
    # One large real wheel tick reliably reaches anywhere in the ~7000px
    # list for this candidate's alphabet-adjacent countries (confirmed for
    # "India" from a cold-opened list twice); if a different country is ever
    # needed and this doesn't bring it into view, call again with more delta
    # rather than looping small ticks (that scrolls the outer PAGE instead,
    # not the virtualized list, if the mouse drifts off the grid).
    page.mouse.wheel(0, 3000)
    page.wait_for_timeout(500)

    country_row = grid.get_by_text(country, exact=True).first
    if country_row.count() == 0:
        return False
    force_click(page, country_row)
    page.wait_for_timeout(1000)

    type_row = page.get_by_text(citizenship_type, exact=True).first
    if type_row.count() == 0:
        return False
    force_click(page, type_row)
    page.wait_for_timeout(700)
    page.keyboard.press("Escape")
    page.wait_for_timeout(400)

    result = page.evaluate(
        """
        (id) => {
            const el = document.getElementById(id);
            const wrapper = el.closest('[data-automation-id="multiSelectContainer"]');
            const list = wrapper.querySelector('[data-automation-id="selectedItemList"]');
            return list ? Array.from(list.querySelectorAll('li')).map(li => li.innerText.trim()) : [];
        }
        """,
        citizenship_input_id,
    )
    return citizenship_type in result


def verify_and_fix_id_task_countries(page: Page, expected_country: str = "India") -> list[str]:
    """Government ID / National ID task pages have been observed defaulting
    their Country/Territory dropdown to the WRONG country ("Bangladesh", on
    two separate applications) with no visible warning - Workday does not
    default this to the candidate's actual nationality. Call this BEFORE
    filling ID numbers or clicking Submit on any 'jobTasks/identification'
    page. Fixes every Country/Territory button not already showing
    `expected_country` and returns the list of buttons that were corrected
    (empty list = everything was already right)."""
    fixed = []
    country_btns = page.locator('button[id*="--country"]').all()
    for btn in country_btns:
        current = btn.inner_text().strip()
        if current == expected_country:
            continue
        robust_click(page, btn)
        page.wait_for_timeout(600)
        controls = btn.get_attribute("aria-controls") or btn.get_attribute("aria-owns")
        if not controls:
            page.keyboard.press("Escape")
            continue
        listbox = page.locator(f'[id="{controls}"]')
        for opt in listbox.locator('[role="option"]').all():
            if opt.inner_text().strip() == expected_country:
                force_click(page, opt)
                page.wait_for_timeout(400)
                fixed.append(f"{btn.get_attribute('id')}: {current} -> {expected_country}")
                break
    return fixed


def get_skills_count(page: Page, skills_input_id: str = "skills--skills") -> int:
    return page.evaluate(
        """
        (id) => {
            const el = document.getElementById(id);
            const wrapper = el.closest('[data-automation-id="multiSelectContainer"]');
            const list = wrapper.querySelector('[data-automation-id="selectedItemList"]');
            return list ? list.querySelectorAll('li[data-automation-id="menuItem"]').length : -1;
        }
        """,
        skills_input_id,
    )


# JD-agnostic skills always worth adding for this candidate on any Business
# Strategy & Operations / consulting-adjacent role - covers stakeholder work,
# hands-on Agentic AI/GenAI build experience, SAP/ERP delivery, and general
# consulting toolkit. Each is (search query, [acceptable exact-label matches,
# most-preferred first]). Re-run per application; the search-results
# taxonomy is shared across the one Workday tenant.
STANDING_SKILL_QUERIES: list[tuple[str, list[str]]] = [
    ("Structured Problem Solving", ["Structured Problem Solving"]),
    ("Stakeholder Facilitation", ["Stakeholder Facilitation"]),
    ("Change Management", ["Change Management"]),
    ("Digital Transformation", ["Digital Transformation"]),
    ("Management Consulting", ["Management Consulting"]),
    ("Workshop Facilitation", ["Workshop Facilitation"]),
    ("Process Standardization", ["Process Standardization"]),
    ("Business Strategy", ["Business Strategy"]),
    ("Strategic Planning", ["Strategic Planning"]),
    ("Business Case Development", ["Business Case Development"]),
    ("Program Management", ["Program Management"]),
    ("Operations Management", ["Operations Management"]),
    ("Cross-functional Collaboration", ["Cross-Functional Collaboration"]),
    ("Client Relationship", ["Client Relations", "Client Relationship Management"]),
    ("Root Cause Analysis", ["Root Cause Analysis (RCA)", "Root Cause Analysis"]),
    ("Data Analysis", ["Data Analysis"]),
    ("Data Reconciliation", ["Data Reconciliation"]),
    ("Financial Analysis", ["Financial Analysis"]),
    ("Cost Reduction", ["Cost Reduction"]),
    ("Revenue Growth", ["Revenue Growth"]),
    ("Vendor Management", ["Vendor Management"]),
    ("Inventory Management", ["Inventory Management"]),
    ("Supply Chain Management", ["Supply Chain Management"]),
    ("SAP Management", ["SAP Management"]),
    ("SAP FICO Configuration", ["SAP FICO Configuration"]),
    ("ERP Implementation", ["ERP Implementation"]),
    ("Generative AI", ["Generative AI"]),
    ("Machine Learning", ["Machine Learning"]),
    ("Competitive Analysis", ["Competitive Analysis"]),
    ("Process Improvement", ["Process Improvement"]),
    ("Executive Communication", ["Executive Communications", "Executive Communication"]),
    ("Team Leadership", ["Team Leadership"]),
    ("Negotiation", ["Negotiation"]),
    ("Presentation Skills", ["Presentation skills", "Presentation Skills"]),
    ("Key Performance Indicators", ["Key Performance Indicators (KPI)"]),
    ("Prioritization", ["Prioritization"]),
    ("Customer Journey Mapping", ["Customer Journey Mapping"]),
    ("Digital Marketing", ["Digital Marketing"]),
    ("Paid Advertising", ["Paid Advertising"]),
    ("A/B Testing", ["A/B Testing"]),
    ("Data Visualization", ["Data Visualization"]),
    ("Microsoft Excel", ["Microsoft Excel"]),
    ("Debugging", ["Debugging"]),
]


def get_selected_skill_labels(page: Page, skills_input_id: str = "skills--skills") -> list[str]:
    """Returns the exact text of every currently-selected skill chip.
    CORRECTION to an earlier, wrong assumption in this module: Workday's
    widget does NOT reliably prevent re-adding an already-selected skill - a
    real duplicate ("SAP FICO" x2, caught only by a page-level "You cannot
    enter duplicate skills" submit-time error) occurred in practice. Always
    check membership here before adding, don't rely on the widget to guard
    it."""
    return page.evaluate(
        """
        (id) => {
            const el = document.getElementById(id);
            const wrapper = el.closest('[data-automation-id="multiSelectContainer"]');
            const list = wrapper.querySelector('[data-automation-id="selectedItemList"]');
            return list ? Array.from(list.querySelectorAll('li[data-automation-id="menuItem"]'))
                              .map(li => li.innerText.trim())
                       : [];
        }
        """,
        skills_input_id,
    )


def dedupe_skills(page: Page, skills_input_id: str = "skills--skills") -> list[str]:
    """Safety net for the duplicate-skill bug: removes every extra copy of
    any skill chip that appears more than once (keeping the first), so a
    stray duplicate never reaches Submit and triggers Workday's page-level
    'You cannot enter duplicate skills' error. Returns the labels removed.
    Call this once, right before Save and Continue on the My Experience
    step, regardless of how the skills were added."""
    removed = page.evaluate(
        """
        (id) => {
            const el = document.getElementById(id);
            const wrapper = el.closest('[data-automation-id="multiSelectContainer"]');
            const list = wrapper.querySelector('[data-automation-id="selectedItemList"]');
            if (!list) return [];
            const items = Array.from(list.querySelectorAll('li[data-automation-id="menuItem"]'));
            const seen = new Set();
            const removedLabels = [];
            for (const li of items) {
                const text = li.innerText.trim();
                if (seen.has(text)) {
                    const btn = li.querySelector('[data-automation-id="DELETE_charm"]');
                    if (btn) { btn.click(); removedLabels.push(text); }
                } else {
                    seen.add(text);
                }
            }
            return removedLabels;
        }
        """,
        skills_input_id,
    )
    if removed:
        page.wait_for_timeout(400)
    return removed


def add_standing_skills(page: Page, cap: int = 35, skills_input_id: str = "skills--skills") -> tuple[list[str], list[str]]:
    """Runs STANDING_SKILL_QUERIES against the Skills typeahead until either
    the list is exhausted or `cap` selected skills is reached. Returns
    (added_labels, skipped_queries). Safe to re-run on a page that already
    has some skills auto-suggested from resume parsing: checks
    get_selected_skill_labels() before every add and skips a query outright
    if its target label is already present, then runs dedupe_skills() once
    at the end as a safety net (a duplicate has been observed getting
    through even with the membership check, likely a race between the click
    and the list re-rendering)."""
    added, skipped = [], []
    for query, preferred_labels in STANDING_SKILL_QUERIES:
        if get_skills_count(page, skills_input_id) >= cap:
            break
        already = get_selected_skill_labels(page, skills_input_id)
        if any(pref in already for pref in preferred_labels):
            skipped.append(f"{query} (already selected)")
            continue
        label = search_typeahead_and_check(page, skills_input_id, query, preferred_labels)
        (added if label else skipped).append(label or query)
    page.keyboard.press("Escape")
    page.wait_for_timeout(300)
    js_clear_typeahead(page, skills_input_id)
    removed_dupes = dedupe_skills(page, skills_input_id)
    if removed_dupes:
        skipped.append(f"deduped: {removed_dupes}")
    return added, skipped
