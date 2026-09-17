"""Renders a stored application record (from records_db) as a standalone,
self-contained HTML page - what the dashboard's 'View Submitted Content' link
opens in a new tab. Static file, no server, same pattern the dashboard
already uses for 'Open PDF' (a plain file:// link), so this required no new
serving infrastructure - just another generated static file next to the CV.
"""
from __future__ import annotations

import html
from pathlib import Path

PAGE_TEMPLATE = """<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>{title}</title>
<style>
  body {{ font-family: -apple-system, "Segoe UI", Arial, sans-serif; max-width: 800px;
         margin: 40px auto; padding: 0 20px; color: #1a1a1a; background: #f7f7f8; }}
  h1 {{ font-size: 18px; margin-bottom: 4px; }}
  .meta {{ color: #6b7280; font-size: 13px; margin-bottom: 24px; }}
  .field {{ background: #fff; border: 1px solid #e5e7eb; border-radius: 8px;
            padding: 14px 18px; margin-bottom: 14px; }}
  .field-label {{ color: #6b7280; font-size: 11px; text-transform: uppercase;
                  letter-spacing: 0.04em; margin-bottom: 6px; }}
  .field-value {{ white-space: pre-wrap; font-size: 14px; line-height: 1.5; }}
</style></head>
<body>
  <h1>{company} &mdash; {job_title}</h1>
  <div class="meta">Channel: {channel} &middot; Submitted: {submitted_at}</div>
  {fields_html}
</body></html>
"""

FIELD_TEMPLATE = """<div class="field">
  <div class="field-label">{label}</div>
  <div class="field-value">{value}</div>
</div>"""


def generate_content_page(record: dict, out_path: Path) -> None:
    fields_html = "\n".join(
        FIELD_TEMPLATE.format(label=html.escape(k), value=html.escape(str(v)))
        for k, v in record["content"].items()
    )
    page = PAGE_TEMPLATE.format(
        title=f"{record['company']} - {record['job_title']}",
        company=html.escape(record["company"]),
        job_title=html.escape(record["job_title"]),
        channel=html.escape(record["channel"]),
        submitted_at=html.escape(record["submitted_at"] or "not yet sent"),
        fields_html=fields_html,
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(page, encoding="utf-8")
