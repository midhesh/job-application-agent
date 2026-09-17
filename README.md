# Job Application Agent

A two-part system that automates the tedious parts of applying to jobs: tailoring a CV to a specific job description with deterministic anti-fabrication guardrails, and driving the actual application form (Workday and similar ATS platforms) via a real browser protocol connection rather than screenshot/coordinate clicking.

This is a **code skeleton**, not a runnable end-to-end product. All personal data (name, contact details, employer names, government IDs, compensation figures) has been stripped and replaced with environment-variable/config placeholders. It's here to show the actual architecture and the real bugs that got fixed while building it, not to be cloned and run as-is.

Built by directing an AI coding agent end-to-end rather than hand-writing every line. The architecture decisions, the guardrail rules, and the bug diagnoses below were mine; the agent wrote and iterated on the implementation under that direction.

## `cv_tailor_agent/` — CV tailoring pipeline

Takes a job description and a candidate's master resume data (`cv_master.yaml`, not included), and produces a tailored, scored CV.

- **`pipeline.py`** — orchestrates the stages: intake → tailor → guardrails → critic → score → freeze.
- **`guardrails.py`** — deterministic checks that run before anything is considered acceptable: no invented facts or numbers not already in the master data, no fabricated claims, style rules (no em dashes, no "not X, it's Y" framing, no bare three-item lists).
- **`scoring.py`** — a composite score (structural quality + ATS keyword coverage + a critic's honesty pass). The one hard rule: **the critic can never inflate a score to clear a threshold**. If there's a genuine gap between the candidate's background and the job description, it shows up as a disclosed gap in the output, not a smoothed-over claim. A CV that scores below 80 with a real, non-fabricable domain gap is frozen anyway (with an explicit override flag) rather than argued up.
- **`registry.py`** / **`dashboard.py`** — a shared, file-based source of truth for every CV built and application sent, rendered as a static dashboard. Locked writes so a second concurrent process (e.g. another agent instance working a different job in parallel) can't silently corrupt it.

## `job_application_agent/` — application-form automation

Drives the actual Workday application flow using a real Chrome DevTools Protocol connection into an actual browser, not screenshot-based clicking.

- **`workday_forms.py`** — the hardened form-interaction helpers. The module docstring is effectively a bug log: every function encodes a real failure mode hit live and how it was fixed (see below).
- **`pwc_apply.py`** — the end-to-end orchestration for one specific Workday tenant's application flow, built after the field-by-field approach (30-40 separate script invocations per application) proved too slow and fragile to keep using.
- **`checklist.py`** — an on-disk, per-application checklist. Conversational state doesn't survive a context-compaction event in an LLM agent session; a file does. This exists specifically because in-flight application state (which form step, has the real CV been swapped in yet, has it been submitted) got lost across a session boundary once, and that was worth fixing structurally rather than just being more careful next time.

### Real bugs this code fixes (not hypothetical)

- **Duplicate rows from click-retries**: a click that might have silently missed was retried, but a naive retry-without-checking sometimes fired twice, creating a genuine duplicate entry. Fixed with a settle-check after every retried click that trims the duplicate if one appears.
- **A stale bottom action bar eating clicks**: a fixed Save/Submit bar at the bottom of the viewport physically overlapped elements scrolled near the bottom, silently swallowing clicks that looked like they should have landed. Fixed by always scrolling an element to the center of the viewport before clicking it, not just checking it's "in view."
- **A race condition in a typeahead search**: a fixed timeout after pressing Enter moved on to the next search before results had actually rendered, causing a repeating loop of the same failed search. Fixed by polling for the actual results panel to appear instead of trusting a fixed delay.
- **A virtualized picker that ignores programmatic scrolling**: a two-level country/type picker rendered only ~4 DOM rows of a much longer list at a time, and setting `scrollTop` directly via JavaScript did nothing because the framework never saw a real scroll event. Fixed by firing an actual mouse-wheel event over the element instead.
- **A hidden ARIA-proxy date widget**: a date-of-birth field's real bounding box was a fraction of a pixel wide, causing every viewport-aware click to fail or silently miss. Fixed by focusing the underlying input directly via JavaScript rather than clicking it, then sending real per-character keystrokes instead of a single `.fill()` call (which looked correct in the DOM but never actually committed to the framework's internal state).

## What's deliberately not here

- `cv_master.yaml`, `applicant_profile.yaml`, the registry, any frozen output CVs, and anything else under a `private/` directory in the original project — all real candidate data.
- Any hardcoded name, address, phone number, email, government ID, or compensation figure. Every place one of those was needed, the code now reads from an environment variable via a small `_profile()` / `CANDIDATE` lookup instead.
