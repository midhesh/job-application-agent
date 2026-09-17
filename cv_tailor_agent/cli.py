from __future__ import annotations

import json
import os
import re
import sys
from datetime import date
from pathlib import Path

import typer
from rich import print as rprint
from rich.panel import Panel

from cv_tailor_agent.contracts import CriticReport, CVRecord, MarketBenchmarkReport, TailorPatch
from cv_tailor_agent.dashboard import generate_dashboard
from cv_tailor_agent.guardrails import apply_patch, validate_patch
from cv_tailor_agent.llm import load_style_rules
from cv_tailor_agent.pipeline import run_pipeline
from cv_tailor_agent.prompts import build_critic_prompt, build_market_benchmark_prompt, build_tailor_prompt
from cv_tailor_agent.registry import load_registry, upsert_record
from cv_tailor_agent.render import load_cv_document, render_document
from cv_tailor_agent.scoring import compute_delta, compute_scorecard
from cv_tailor_agent.similarity import find_similar_cases


def _sanitize(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", s).strip("_")

app = typer.Typer()


@app.command("similar-cases")
def similar_cases_cmd(
    jd: Path = typer.Option(..., help="Path to the new JD text file"),
    exclude_company: str | None = typer.Option(None, help="Skip this company (e.g. if re-running the same one)"),
    top: int = typer.Option(3, help="How many past cases to show"),
) -> None:
    """Run this BEFORE tailoring a new JD - surfaces the most similar past cases
    (by JD/company-research text overlap) so the same reasoning isn't re-derived
    from scratch each time."""
    jd_text = jd.read_text(encoding="utf-8")
    records = load_registry()
    matches = find_similar_cases(jd_text, records, exclude_company=exclude_company, top_n=top)
    if not matches:
        rprint("[yellow]No past cases with stored JD text to compare against yet.[/yellow]")
        return
    for record, score in matches:
        rprint(Panel.fit(
            f"Similarity: {score:.0%}\n"
            f"Composite score achieved: {record.composite_score}\n"
            f"Location: {record.location}\n"
            f"Market gaps noted: {record.market_gaps or 'none'}",
            title=f"{record.company} - {record.job_title}",
        ))


@app.command("prep-tailor-prompt")
def prep_tailor_prompt(
    cv_master: Path = typer.Option(..., help="Path to cv_master.yaml"),
    jd: Path = typer.Option(..., help="Path to a text file containing the job description"),
    notes: Path | None = typer.Option(None, help="Optional path to a text file of company notes"),
    out: Path = typer.Option(Path("private/prompt_tailor.txt"), help="Where to write the ready-to-paste prompt"),
) -> None:
    """No AI call here. Generates a prompt to paste into ANY chat AI (Claude.ai, ChatGPT, Gemini, etc.)."""
    doc = load_cv_document(cv_master)
    jd_text = jd.read_text(encoding="utf-8")
    notes_text = notes.read_text(encoding="utf-8") if notes else None
    prompt = build_tailor_prompt(doc, jd_text, notes_text, load_style_rules())
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(prompt, encoding="utf-8")
    rprint(f"[green]Prompt written to {out}[/green] — paste its full contents into any AI chat, "
           f"save the JSON it returns, then run validate-patch.")


@app.command("prep-critic-prompt")
def prep_critic_prompt(
    cv_master: Path = typer.Option(..., help="Path to cv_master.yaml (the original, untailored version)"),
    jd: Path = typer.Option(..., help="Path to the job description text file"),
    patch: Path = typer.Option(..., help="Path to the TailorPatch JSON produced from the tailor prompt"),
    out: Path = typer.Option(Path("private/prompt_critic.txt"), help="Where to write the ready-to-paste prompt"),
) -> None:
    """No AI call here. Generates a fresh-eyes review prompt - paste into a NEW chat (any AI, any provider)."""
    doc = load_cv_document(cv_master)
    jd_text = jd.read_text(encoding="utf-8")
    patch_obj = TailorPatch.model_validate_json(patch.read_text(encoding="utf-8"))
    tailored = apply_patch(doc, patch_obj)
    prompt = build_critic_prompt(jd_text, doc, tailored, load_style_rules())
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(prompt, encoding="utf-8")
    rprint(f"[green]Prompt written to {out}[/green] — paste into a *new* chat session (fresh eyes, no shared history) "
           f"and save its JSON response.")


@app.command("validate-patch")
def validate_patch_cmd(
    cv_master: Path = typer.Option(..., help="Path to cv_master.yaml"),
    patch: Path = typer.Option(..., help="Path to a TailorPatch JSON file"),
) -> None:
    """Deterministic, no-AI check: does this patch only touch real bullets, with no invented numbers/em-dashes?"""
    doc = load_cv_document(cv_master)
    patch_obj = TailorPatch.model_validate_json(patch.read_text(encoding="utf-8"))
    errors = validate_patch(doc, patch_obj)
    if errors:
        rprint("[red]REJECTED[/red] — fix these and resubmit:")
        for e in errors:
            rprint(f"  - {e}")
        sys.exit(1)
    rprint("[green]OK[/green] — patch passes all deterministic guardrails.")


@app.command("apply-render")
def apply_render_cmd(
    cv_master: Path = typer.Option(..., help="Path to cv_master.yaml"),
    patch: Path = typer.Option(..., help="Path to a TailorPatch JSON file (must already pass validate-patch)"),
    out: Path = typer.Option(..., help="Output PDF path"),
) -> None:
    """No-AI step: apply an already-validated patch and render the final PDF."""
    doc = load_cv_document(cv_master)
    patch_obj = TailorPatch.model_validate_json(patch.read_text(encoding="utf-8"))
    errors = validate_patch(doc, patch_obj)
    if errors:
        rprint("[red]REFUSING TO RENDER[/red] — patch fails guardrails:")
        for e in errors:
            rprint(f"  - {e}")
        sys.exit(1)
    tailored = apply_patch(doc, patch_obj)
    render_document(tailored, out)
    rprint(f"[green]Rendered:[/green] {out}")


@app.command("score-cv")
def score_cv_cmd(
    cv_master: Path = typer.Option(..., help="Path to a cv_master.yaml (base or, via --patch, tailored)"),
    patch: Path | None = typer.Option(None, help="Optional TailorPatch JSON to score the tailored version instead of base"),
    critic: Path | None = typer.Option(None, help="Optional CriticReport JSON to fold keyword coverage/flags into the score"),
) -> None:
    doc = load_cv_document(cv_master)
    if patch:
        patch_obj = TailorPatch.model_validate_json(patch.read_text(encoding="utf-8"))
        doc = apply_patch(doc, patch_obj)
    critic_obj = CriticReport.model_validate_json(critic.read_text(encoding="utf-8")) if critic else None
    card = compute_scorecard(doc, critic_obj)
    rprint(Panel.fit(
        f"Composite score: {card.composite_score}/100\n\n"
        f"Structural: {card.structural.score}/100\n"
        f"  Quantification density: {card.structural.quantification_density_pct}%\n"
        f"  Strong-verb-lead bullets: {card.structural.strong_verb_lead_pct}%\n"
        f"  One-line compliant: {card.structural.one_line_compliant_pct}%\n"
        f"  Contact fields present: {card.structural.contact_fields_present}/4\n\n"
        f"ATS keyword coverage: {card.ats_keyword_coverage_pct if card.ats_keyword_coverage_pct is not None else 'n/a (pass --critic)'}\n"
        f"Defensibility flags: {card.defensibility_flag_count}\n"
        f"Style violations: {card.style_violation_count}\n\n"
        f"[dim]{card.caveat}[/dim]",
        title="CV Scorecard",
    ))


@app.command("diff-cv")
def diff_cv_cmd(
    cv_master: Path = typer.Option(..., help="Path to the base cv_master.yaml"),
    patch: Path = typer.Option(..., help="Path to the TailorPatch JSON"),
) -> None:
    base = load_cv_document(cv_master)
    patch_obj = TailorPatch.model_validate_json(patch.read_text(encoding="utf-8"))
    tailored = apply_patch(base, patch_obj)
    delta = compute_delta(base, tailored)
    rprint(Panel.fit(
        f"Bullets changed: {delta.bullets_changed}/{delta.bullets_total} ({delta.pct_bullets_changed}%)\n"
        f"Avg similarity of changed bullets to their original: {delta.avg_similarity_ratio} (1.0 = identical)\n"
        f"Tagline changed: {delta.tagline_changed}\n"
        f"Subsections reordered: {delta.subsections_reordered}",
        title="Delta from base CV",
    ))


@app.command("prep-market-benchmark-prompt")
def prep_market_benchmark_prompt(
    cv_master: Path = typer.Option(..., help="Path to cv_master.yaml"),
    jd: Path = typer.Option(..., help="Path to the job description text file"),
    patch: Path | None = typer.Option(None, help="Optional patch to benchmark the tailored version"),
    out: Path = typer.Option(Path("private/prompt_benchmark.txt")),
) -> None:
    """No AI call here. Generates a prompt for market-pattern research - paste into any chat AI with web search/browsing."""
    doc = load_cv_document(cv_master)
    if patch:
        patch_obj = TailorPatch.model_validate_json(patch.read_text(encoding="utf-8"))
        doc = apply_patch(doc, patch_obj)
    jd_text = jd.read_text(encoding="utf-8")
    prompt = build_market_benchmark_prompt(jd_text, doc)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(prompt, encoding="utf-8")
    rprint(f"[green]Prompt written to {out}[/green] — use an AI with web search/browsing enabled for a real result.")


@app.command("freeze")
def freeze_cmd(
    cv_master: Path = typer.Option(...),
    patch: Path = typer.Option(...),
    critic: Path = typer.Option(...),
    company: str = typer.Option(..., help="Used for the dashboard registry and default output filename"),
    job_title: str = typer.Option(..., help="Used for the dashboard registry and default output filename"),
    jd_source: str | None = typer.Option(None, help="Short description of where the JD came from"),
    job_post_url: str | None = typer.Option(None, help="URL of the original job posting, shown in the dashboard detail view"),
    jd: Path | None = typer.Option(None, help="Path to the JD text file - stored for future similar-cases comparisons"),
    out: Path | None = typer.Option(
        None,
        help="Output PDF path. If omitted, derived as {CV_OUTPUT_DIR or private/output}/{Name}_{Company}_{JobTitle}_CV.pdf",
    ),
    min_score: float = typer.Option(95.0, help="Minimum composite scorecard score required to freeze"),
    benchmark: Path | None = typer.Option(None, help="Optional MarketBenchmarkReport JSON, shown but not gating"),
    accept_below_threshold: bool = typer.Option(
        False,
        "--accept-below-threshold",
        help="Explicit human override to freeze below min_score anyway (e.g. applying despite a known, "
        "disclosed profile-fit gap). The score is never altered - this only changes whether freeze proceeds. "
        "The resulting record is marked 'frozen_below_threshold' in the dashboard, never disguised as a clean pass.",
    ),
) -> None:
    """The finalization gate: only renders the frozen final PDF if every check actually passes.
    Also enforces one CV per (company, job_title) via the registry, and regenerates the dashboard."""
    doc = load_cv_document(cv_master)
    patch_obj = TailorPatch.model_validate_json(patch.read_text(encoding="utf-8"))

    blockers = validate_patch(doc, patch_obj)
    if blockers:
        rprint("[red]NOT FROZEN[/red] — guardrails failed:")
        for b in blockers:
            rprint(f"  - {b}")
        sys.exit(1)

    critic_obj = CriticReport.model_validate_json(critic.read_text(encoding="utf-8"))
    if not critic_obj.passed:
        rprint(f"[red]NOT FROZEN[/red] — critic did not pass: {critic_obj.feedback_for_tailor}")
        sys.exit(1)

    tailored = apply_patch(doc, patch_obj)
    card = compute_scorecard(tailored, critic_obj)
    below_threshold = card.composite_score < min_score
    if below_threshold and not accept_below_threshold:
        rprint(f"[red]NOT FROZEN[/red] — composite score {card.composite_score} is below the {min_score} threshold.")
        rprint("[dim]Pass --accept-below-threshold to freeze anyway as an explicit, disclosed human override.[/dim]")
        sys.exit(1)

    if out is None:
        output_dir = Path(os.environ.get("CV_OUTPUT_DIR", "private/output"))
        filename = f"{_sanitize(tailored.header.name)}_{_sanitize(company)}_{_sanitize(job_title)}_CV.pdf"
        out = output_dir / filename

    render_document(tailored, out)
    if below_threshold:
        rprint(
            f"[yellow]FROZEN BELOW THRESHOLD[/yellow] — score {card.composite_score}/100 is under the "
            f"{min_score} bar, frozen anyway via explicit override. Final CV: {out}"
        )
    else:
        rprint(f"[green]FROZEN[/green] — all checks passed (score {card.composite_score}/100). Final CV: {out}")

    delta = compute_delta(doc, tailored)
    market_gaps: list[str] = []
    company_research: str | None = None
    if benchmark:
        bm = MarketBenchmarkReport.model_validate_json(benchmark.read_text(encoding="utf-8"))
        market_gaps = bm.gaps_vs_this_cv
        company_research = bm.role_pattern_summary
    jd_text = jd.read_text(encoding="utf-8") if jd else None

    record = CVRecord(
        company=company,
        job_title=job_title,
        date_created=date.today().isoformat(),
        location=str(out.resolve()),
        jd_source=jd_source,
        job_post_url=job_post_url,
        jd_text=jd_text,
        company_research=company_research,
        composite_score=card.composite_score,
        ats_coverage_pct=card.ats_keyword_coverage_pct,
        status="frozen_below_threshold" if below_threshold else "frozen",
        quantification_density_pct=card.structural.quantification_density_pct,
        strong_verb_lead_pct=card.structural.strong_verb_lead_pct,
        one_line_compliant_pct=card.structural.one_line_compliant_pct,
        contact_fields_present=card.structural.contact_fields_present,
        defensibility_flag_count=card.defensibility_flag_count,
        style_violation_count=card.style_violation_count,
        pct_bullets_changed=delta.pct_bullets_changed,
        market_gaps=market_gaps,
    )
    upsert_record(record)
    generate_dashboard(load_registry())
    rprint("[green]Dashboard updated:[/green] private/dashboard.html")

    if market_gaps:
        rprint(f"[yellow]For your judgment (not blocking):[/yellow] market pattern gaps: {market_gaps}")


@app.command("render")
def render_cmd(
    cv_master: Path = typer.Option(..., help="Path to cv_master.yaml (no tailoring, just format-fidelity render)"),
    out: Path = typer.Option(..., help="Output PDF path"),
) -> None:
    doc = load_cv_document(cv_master)
    render_document(doc, out)
    rprint(f"[green]Rendered:[/green] {out}")


@app.command("run-automated")
def run_automated(
    cv_master: Path = typer.Option(..., help="Path to cv_master.yaml"),
    jd: Path = typer.Option(..., help="Path to a text file containing the job description"),
    notes: Path | None = typer.Option(None, help="Optional path to a text file of company notes"),
    out: Path = typer.Option(Path("output/tailored_cv.pdf"), help="Output PDF path"),
    max_iterations: int = typer.Option(3, help="Max tailor<->critic loop iterations"),
) -> None:
    jd_text = jd.read_text(encoding="utf-8")
    notes_text = notes.read_text(encoding="utf-8") if notes else None

    result = run_pipeline(
        cv_master_path=cv_master,
        jd_text=jd_text,
        company_notes=notes_text,
        out_pdf_path=out,
        max_iterations=max_iterations,
    )

    status = "[green]PASSED[/green]" if result.passed else "[yellow]DID NOT FULLY PASS[/yellow]"
    rprint(Panel.fit(
        f"{status} after {result.iterations_used} iteration(s)\n\n"
        f"Tagline before: {result.tagline_before}\n"
        f"Tagline after:  {result.tagline_after}\n\n"
        f"ATS keyword coverage: {result.ats_keyword_coverage_pct:.0f}%\n"
        f"Bullets rewritten: {len(result.bullet_diff)}\n"
        f"Output: {result.output_pdf_path}",
        title="cv-tailor-agent",
    ))
    if result.critic_report:
        if result.critic_report.missing_keywords:
            rprint(f"[yellow]Missing keywords:[/yellow] {result.critic_report.missing_keywords}")
        if result.critic_report.defensibility_flags:
            rprint(f"[red]Defensibility flags:[/red] {result.critic_report.defensibility_flags}")
        if result.critic_report.style_violations:
            rprint(f"[red]Style violations:[/red] {result.critic_report.style_violations}")


if __name__ == "__main__":
    app()
