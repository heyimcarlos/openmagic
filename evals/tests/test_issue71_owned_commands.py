from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from openmagic_evals.evidence._owned_command import capture_owned_command
from openmagic_evals.evidence.deadline import EvidenceTimeout, bounded_evidence


def _process_is_live(process_id: int) -> bool:
    try:
        value = Path(f"/proc/{process_id}/stat").read_text(encoding="utf-8")
    except FileNotFoundError:
        return False
    _, _, suffix = value.rpartition(")")
    return suffix.split()[0] not in {"X", "Z"}


def _term_resistant_tree_command(pid_file: Path) -> tuple[str, ...]:
    return (
        sys.executable,
        "-c",
        (
            "import os,pathlib,signal,subprocess,sys,time;"
            "signal.signal(signal.SIGTERM, signal.SIG_IGN);"
            "child=subprocess.Popen([sys.executable,'-c',"
            "'import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); "
            "time.sleep(30)']);"
            f"pathlib.Path({str(pid_file)!r}).write_text(f'{{os.getpid()}} {{child.pid}}');"
            "time.sleep(30)"
        ),
    )


def _assert_tree_reaped(pid_file: Path) -> None:
    assert pid_file.is_file()
    process_ids = tuple(int(value) for value in pid_file.read_text(encoding="utf-8").split())
    assert all(not _process_is_live(process_id) for process_id in process_ids)


def test_owned_command_timeout_reaps_term_resistant_tree(tmp_path: Path) -> None:
    pid_file = tmp_path / "capture-tree.pids"
    with pytest.raises(TimeoutError):
        capture_owned_command(
            _term_resistant_tree_command(pid_file),
            working_directory=tmp_path,
            environment={"PATH": os.defpath, "PYTHONNOUSERSITE": "1"},
            timeout_seconds=0.1,
        )

    _assert_tree_reaped(pid_file)


def test_owned_command_outer_deadline_reaps_term_resistant_tree(tmp_path: Path) -> None:
    pid_file = tmp_path / "outer-deadline-tree.pids"

    @bounded_evidence
    def run(*, timeout_seconds: int) -> None:
        capture_owned_command(
            _term_resistant_tree_command(pid_file),
            working_directory=tmp_path,
            environment={"PATH": os.defpath, "PYTHONNOUSERSITE": "1"},
            timeout_seconds=30,
        )

    with pytest.raises(EvidenceTimeout):
        run(timeout_seconds=1)

    _assert_tree_reaped(pid_file)


def test_owned_command_captures_complete_result(tmp_path: Path) -> None:
    result = capture_owned_command(
        (sys.executable, "-c", "import sys; print('out'); print('err', file=sys.stderr)"),
        working_directory=tmp_path,
        environment={"PATH": os.defpath, "PYTHONNOUSERSITE": "1"},
        timeout_seconds=3,
    )

    assert result.returncode == 0
    assert result.stdout == "out\n"
    assert result.stderr == "err\n"
