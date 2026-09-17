from __future__ import annotations

from pathlib import Path

import yaml
from jinja2 import Environment, FileSystemLoader
from playwright.sync_api import sync_playwright
from pypdf import PdfReader, PdfWriter

from cv_tailor_agent.schema import CVDocument

TEMPLATES_DIR = Path(__file__).resolve().parent.parent.parent / "templates"


def load_cv_document(yaml_path: Path) -> CVDocument:
    data = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
    return CVDocument.model_validate(data)


def render_html(doc: CVDocument) -> str:
    env = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)))
    template = env.get_template("cv_template.html")
    return template.render(**doc.model_dump())


def html_to_pdf(html: str, out_path: Path, base_dir: Path = TEMPLATES_DIR) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_html = out_path.with_suffix(".tmp.html")
    tmp_html.write_text(html, encoding="utf-8")
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page()
            page.goto(tmp_html.resolve().as_uri())
            page.pdf(path=str(out_path), prefer_css_page_size=True, print_background=True)
            browser.close()
    finally:
        tmp_html.unlink(missing_ok=True)


def set_pdf_metadata(path: Path, author: str, title: str) -> None:
    reader = PdfReader(str(path))
    writer = PdfWriter()
    writer.append(reader)
    writer.add_metadata({"/Author": author, "/Title": title, "/Creator": "cv-tailor-agent"})
    with open(path, "wb") as f:
        writer.write(f)


def render_document(doc: CVDocument, out_path: Path) -> None:
    html = render_html(doc)
    html_to_pdf(html, out_path)
    set_pdf_metadata(out_path, author=doc.header.name, title=f"{doc.header.name} - CV")


def render_cv(yaml_path: Path, out_path: Path) -> None:
    doc = load_cv_document(yaml_path)
    render_document(doc, out_path)
