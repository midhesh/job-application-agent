"""Reusable, DOM-shift-safe Google Form filling - built from two real bugs hit
filling the 12 Flags form by hand:

1. Google's Email field renders as `<input type="email">`, not type="text" -
   a selector scoped to type=text silently skipped it and shifted every other
   answer down by one field. Fixed here by matching on BOTH input and
   textarea, any type, scoped inside the specific question's own container.
2. Adding a file answer inserts a new listitem into the DOM (the uploaded
   file's own chip), shifting every field AFTER it by one index. Fixed here
   by locating fields by their QUESTION TEXT, never by a raw positional
   index that can silently drift after any DOM-mutating action.

Requires a Chrome process already launched with --remote-debugging-port
(see the 12 Flags session for the launch command) - this module only
connects to an existing CDP endpoint, it doesn't launch anything itself,
since headed launches must go through Start-Process, not Playwright's own
process spawn (confirmed broken in this execution environment).
"""
from __future__ import annotations

from pathlib import Path

from playwright.sync_api import Page, sync_playwright


def connect(cdp_port: int = 9333):
    """Returns (playwright, browser, page) for an already-launched, already
    navigated Chrome instance. Caller is responsible for closing playwright
    when done (or just letting the process exit - this doesn't close the
    actual browser window, only the automation connection to it)."""
    p = sync_playwright().start()
    browser = p.chromium.connect_over_cdp(f"http://localhost:{cdp_port}")
    page = browser.contexts[0].pages[0]
    return p, browser, page


def find_field_container(page: Page, question_text: str):
    """Locate a question's container by matching its own text, never by
    position - the one fix that matters most, since any file upload or
    validation error inserted into the DOM shifts positional indices."""
    items = page.get_by_role("listitem").filter(has_text=question_text)
    count = items.count()
    if count == 0:
        raise ValueError(f"No question found matching {question_text!r}")
    if count > 1:
        # Prefer the one whose heading STARTS with the text, to disambiguate
        # a question from an unrelated field that merely contains the same
        # substring somewhere in its content.
        for i in range(count):
            heading = items.nth(i).get_by_role("heading").first
            if heading.count() and heading.inner_text().strip().startswith(question_text):
                return items.nth(i)
    return items.first


def fill_field(page: Page, question_text: str, value: str) -> str:
    """Fill a text/email/textarea field by question text and return the
    actual value read back from the DOM - never trust a fill() call blindly,
    always verify what actually landed."""
    container = find_field_container(page, question_text)
    field = container.locator("input, textarea").first
    field.click()
    field.fill("")
    field.fill(value)
    actual = field.input_value()
    if actual != value:
        raise RuntimeError(f"Fill mismatch for {question_text!r}: wrote {value!r}, DOM has {actual!r}")
    return actual


def upload_file(page: Page, question_text: str, file_path: str | Path, add_file_label: str = "Add file") -> None:
    """Handles Google Forms' file-question flow: click 'Add file', which
    opens a Drive-picker iframe (not a plain native file input), find the
    real <input type=file> inside THAT iframe specifically, and set it
    directly - bypassing the native OS file-picker dialog entirely, which
    can't be automated from here."""
    container = find_field_container(page, question_text)
    container.get_by_text(add_file_label).click()

    picker_frame = None
    for frame in page.frames:
        if "docs.google.com/picker" in frame.url:
            picker_frame = frame
            break
    if picker_frame is None:
        raise RuntimeError("Could not find the Google Drive picker iframe after clicking Add file")

    file_input = picker_frame.locator("input[type=file]")
    file_input.set_input_files(str(file_path))
