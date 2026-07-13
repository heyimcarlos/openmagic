"""JSON and Markdown reporting for paired coordination evidence."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from .coordination_contracts import CoordinationReport, CoordinationTrial


def build_coordination_report(
    *,
    run_id: str,
    generated_at: datetime,
    trials: tuple[CoordinationTrial, ...],
) -> CoordinationReport:
    """Pair profiles and apply only Workflow correctness to the report gate."""

    pairs: dict[str, list[CoordinationTrial]] = {}
    for trial in trials:
        pairs.setdefault(trial.scenario_id, []).append(trial)
    if any(
        len(pair) != 2 or {trial.profile for trial in pair} != {"legacy", "workflow"}
        for pair in pairs.values()
    ):
        raise ValueError("Every scenario requires one legacy and one Workflow trial")
    if len({trial.model for trial in trials}) != 1:
        raise ValueError("Paired trials must use one model configuration")
    if len({trial.application_build for trial in trials}) != 1:
        raise ValueError("Paired trials must use one application build")
    workflow = tuple(trial for trial in trials if trial.profile == "workflow")
    baseline = tuple(trial for trial in trials if trial.profile == "legacy")
    return CoordinationReport(
        run_id=run_id,
        generated_at=generated_at,
        v0_passed=all(trial.correctness is True for trial in workflow),
        workflow_trials=len(workflow),
        baseline_trials=len(baseline),
        trials=trials,
    )


def write_coordination_report(
    report: CoordinationReport,
    output_directory: Path,
) -> tuple[Path, Path]:
    """Write one isolated JSON record and Markdown summary for a completed run."""

    output_directory.mkdir(parents=True, exist_ok=True)
    run_directory = output_directory / report.run_id
    run_directory.mkdir(exist_ok=False)
    json_path = run_directory / "report.json"
    markdown_path = run_directory / "report.md"
    trial_directory = run_directory / "trials"
    trial_directory.mkdir()
    for trial in report.trials:
        trial_path = trial_directory / f"{trial.scenario_id}-{trial.profile}.json"
        trial_path.write_text(trial.model_dump_json(indent=2) + "\n", encoding="utf-8")
    json_path.write_text(report.model_dump_json(indent=2) + "\n", encoding="utf-8")
    markdown_path.write_text(_render_markdown(report), encoding="utf-8")
    return json_path, markdown_path


def _render_markdown(report: CoordinationReport) -> str:
    verdict = "PASS" if report.v0_passed else "FAIL"
    lines = [
        f"# Paired coordination evaluation: {report.run_id}",
        "",
        f"Strict V0 verdict: {verdict}",
        "",
        "Baseline outcomes and trajectory measurements are diagnostic. They do not decide the V0 verdict.",
        "",
        "| Scenario | Profile | Outcome | Verdict |",
        "| --- | --- | --- | --- |",
    ]
    for trial in report.trials:
        trial_verdict = (
            "diagnostic" if trial.correctness is None else "pass" if trial.correctness else "fail"
        )
        lines.append(
            f"| {trial.scenario_id} | {trial.profile} | {trial.outcome} | {trial_verdict} |"
        )
    lines.extend(
        [
            "",
            "## Diagnostics",
            "",
            "| Scenario | Profile | Model calls | Tool calls | Searches | Packet reads | Approx. context tokens | Model ms | Local tool ms |",
            "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for trial in report.trials:
        diagnostics = trial.diagnostics
        lines.append(
            "| "
            + " | ".join(
                (
                    trial.scenario_id,
                    trial.profile,
                    str(diagnostics.model_calls),
                    str(len(diagnostics.tool_calls)),
                    str(diagnostics.search_calls),
                    str(diagnostics.packet_reads),
                    str(diagnostics.approximate_context_tokens),
                    f"{diagnostics.model_duration_ms:.3f}",
                    f"{diagnostics.local_tool_duration_ms:.3f}",
                )
            )
            + " |"
        )
    return "\n".join(lines) + "\n"


__all__ = ["build_coordination_report", "write_coordination_report"]
