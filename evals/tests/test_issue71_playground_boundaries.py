from __future__ import annotations

import io
import json
import os
import signal
import subprocess
import sys
import time
from multiprocessing import get_context
from pathlib import Path
from threading import Thread
from typing import Any, cast

import pytest
from openmagic_evals.evidence.playground_client import parse_playground_response
from openmagic_evals.evidence.race_processes import _reap_processes
from openmagic_evals.harness.local_provider import LocalEmailProvider
from openmagic_playground.deployment import (
    ManagedProcess,
    PlaygroundDeployment,
    _RunningProcess,
)
from openmagic_playground.responses import (
    ControlExerciseResponse,
    RenewalDemonstrationResponse,
)
from openmagic_playground.synthetic_provider import SyntheticEmailProvider
from openmagic_runtime.processes import OwnedProcess
from pydantic import ValidationError


class _FailingLog(io.BytesIO):
    def close(self) -> None:
        super().close()
        raise RuntimeError("synthetic log-close failure")


class _RetryableContainer:
    def __init__(self) -> None:
        self.stop_calls = 0

    def stop(self) -> None:
        self.stop_calls += 1
        if self.stop_calls == 1:
            raise RuntimeError("synthetic container-stop failure")


def _term_resistant_process_group(tmp_path: Path) -> tuple[subprocess.Popen[bytes], int]:
    child_pid_path = tmp_path / f"child-{time.monotonic_ns()}.pid"
    process = subprocess.Popen(
        [
            sys.executable,
            "-c",
            (
                "import pathlib,signal,subprocess,sys,time;"
                "signal.signal(signal.SIGTERM, signal.SIG_IGN);"
                "child=subprocess.Popen([sys.executable,'-c',"
                "'import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(30)']);"
                f"pathlib.Path({str(child_pid_path)!r}).write_text(str(child.pid));"
                "time.sleep(30)"
            ),
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    deadline = time.monotonic() + 3
    while not child_pid_path.is_file() and time.monotonic() < deadline:
        time.sleep(0.01)
    if not child_pid_path.is_file():
        process.kill()
        process.wait()
        raise RuntimeError("descendant process fixture did not become ready")
    return process, int(child_pid_path.read_text(encoding="utf-8"))


def _process_is_live(pid: int) -> bool:
    try:
        value = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
    except FileNotFoundError:
        return False
    _, _, suffix = value.rpartition(")")
    return suffix.split()[0] not in {"X", "Z"}


def _ignore_terminate() -> None:
    signal.signal(signal.SIGTERM, lambda *_: None)
    time.sleep(30)


def _running(process: subprocess.Popen[bytes], log: object) -> _RunningProcess:
    return _RunningProcess(
        public=ManagedProcess(
            role="api",
            pid=process.pid,
            health_url="http://127.0.0.1:1/health",
            worker_id=None,
        ),
        process=process,
        owner=OwnedProcess.subprocess(process, resources=(cast(Any, log),)),
    )


def test_stop_reaps_every_child_and_closes_every_log_before_reporting_failures(
    tmp_path: Path,
) -> None:
    first, first_child = _term_resistant_process_group(tmp_path)
    second, second_child = _term_resistant_process_group(tmp_path)
    failing_log = _FailingLog()
    surviving_log = io.BytesIO()
    deployment = PlaygroundDeployment(
        working_directory=tmp_path,
        shutdown_timeout=0.1,
    )
    deployment._running.extend((_running(first, failing_log), _running(second, surviving_log)))

    with pytest.raises(ExceptionGroup, match="playground cleanup failed") as raised:
        deployment.stop()

    assert str(raised.value.exceptions[0]) == "synthetic log-close failure"
    assert first.poll() is not None
    assert second.poll() is not None
    assert not _process_is_live(first_child)
    assert not _process_is_live(second_child)
    assert failing_log.closed
    assert surviving_log.closed
    assert deployment.processes == ()


def test_stop_retains_postgres_ownership_when_container_stop_fails(tmp_path: Path) -> None:
    deployment = PlaygroundDeployment(working_directory=tmp_path, shutdown_timeout=0.1)
    container = _RetryableContainer()
    deployment._container = cast(Any, container)

    with pytest.raises(ExceptionGroup, match="playground cleanup failed"):
        deployment.stop()

    assert deployment._container is container
    deployment.stop()
    assert deployment._container is None
    assert container.stop_calls == 2


@pytest.mark.parametrize(
    "provider_url",
    [
        "http://example.test:8080",
        "https://192.0.2.71/provider",
        "http://[2001:db8::71]:8080",
    ],
)
def test_playground_rejects_nonlocal_email_provider_url(
    tmp_path: Path,
    provider_url: str,
) -> None:
    with pytest.raises(ValueError, match="local loopback"):
        PlaygroundDeployment(
            working_directory=tmp_path,
            email_provider_url=provider_url,
        )


@pytest.mark.parametrize(
    "provider_url",
    [
        "http://localhost:8071",
        "http://127.0.0.1:8071",
        "http://[::1]:8071",
    ],
)
def test_playground_accepts_local_email_provider_url(
    tmp_path: Path,
    provider_url: str,
) -> None:
    deployment = PlaygroundDeployment(
        working_directory=tmp_path,
        email_provider_url=provider_url,
    )

    assert deployment.email_provider_url == provider_url


def test_control_response_rejects_malformed_nested_payload() -> None:
    malformed = """{
        "response_schema_version": 1,
        "response_type": "control-exercise",
        "controls": {
            "start": 3,
            "drain": 3,
            "reset": true,
            "restart": 3,
            "stop": true
        },
        "correlations": {"workflow_ids": ["00000000-0000-0000-0000-000000000071"]},
        "fixture": {"approval_wait_state": 7},
        "original_process_ids": [10, 11, 12],
        "restarted_process_ids": [20, 21, 22],
        "postgres_deployments": []
    }"""

    with pytest.raises(ValidationError) as raised:
        parse_playground_response(malformed, response_type=ControlExerciseResponse)
    assert ("fixture", "approval_wait_state") in {
        tuple(error["loc"]) for error in raised.value.errors()
    }


def test_race_reaper_kills_and_closes_every_term_resistant_contender() -> None:
    context = get_context("spawn")
    processes = tuple(context.Process(target=_ignore_terminate) for _ in range(2))
    for process in processes:
        process.start()
    time.sleep(0.2)

    _reap_processes(processes, timeout_seconds=0.1)

    for process in processes:
        with pytest.raises(ValueError, match="process object is closed"):
            process.is_alive()


def test_provider_readiness_failure_reaps_child_and_closes_log(tmp_path: Path) -> None:
    provider = LocalEmailProvider(
        working_directory=tmp_path,
        readiness_timeout=0.2,
        shutdown_timeout=0.1,
    )
    provider.url = "http://127.0.0.1:1"
    failures: list[BaseException] = []

    def start() -> None:
        try:
            provider.start()
        except BaseException as error:
            failures.append(error)

    starter = Thread(target=start)
    starter.start()
    deadline = time.monotonic() + 2
    while provider._process is None and time.monotonic() < deadline:
        time.sleep(0.01)
    pid = provider.pid
    starter.join(timeout=3)

    assert not starter.is_alive()
    assert len(failures) == 1
    assert isinstance(failures[0], TimeoutError)
    assert provider._process is None
    assert provider._log_handle is None
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


def test_local_provider_reaps_descendant_process_group_and_closes_log(tmp_path: Path) -> None:
    provider = LocalEmailProvider(working_directory=tmp_path, shutdown_timeout=0.1)
    process, child_pid = _term_resistant_process_group(tmp_path)
    log = (tmp_path / "survivor.log").open("ab")
    provider._process = process
    provider._owner = OwnedProcess.subprocess(process, resources=(log,))
    provider._log_handle = log

    provider.stop()

    assert provider._process is None
    assert not _process_is_live(child_pid)
    assert provider._log_handle is None
    assert log.closed


def test_synthetic_provider_reaps_descendant_process_group_and_closes_log(
    tmp_path: Path,
) -> None:
    provider = SyntheticEmailProvider(working_directory=tmp_path, shutdown_timeout=0.1)
    process, child_pid = _term_resistant_process_group(tmp_path)
    log = (tmp_path / "synthetic-survivor.log").open("ab")
    provider._process = process
    provider._owner = OwnedProcess.subprocess(process, resources=(log,))
    provider._log = log

    provider.stop()

    assert provider._process is None
    assert not _process_is_live(child_pid)
    assert provider._log is None
    assert log.closed


def test_control_response_rejects_unknown_nested_fields() -> None:
    malformed = """{
        "response_schema_version": 1,
        "response_type": "control-exercise",
        "controls": {
            "start": 3,
            "drain": 3,
            "reset": true,
            "restart": 3,
            "stop": true,
            "unowned": true
        },
        "correlations": {"workflow_ids": ["00000000-0000-0000-0000-000000000071"]},
        "fixture": {
            "approval_wait_state": "unsatisfied",
            "external_email_effect_count": 0,
            "instance_state": "waiting",
            "message_count": 1,
            "workflow_lifecycle": "awaiting_approval"
        },
        "original_process_ids": [10, 11, 12],
        "restarted_process_ids": [20, 21, 22],
        "postgres_deployments": []
    }"""

    with pytest.raises(ValidationError) as raised:
        parse_playground_response(malformed, response_type=ControlExerciseResponse)
    assert ("controls", "unowned") in {tuple(error["loc"]) for error in raised.value.errors()}


def test_demonstration_response_rejects_invalid_safe_boundary() -> None:
    malformed = json.dumps(
        {
            "response_schema_version": 1,
            "response_type": "demonstration",
            "demonstration": "renewal",
            "correlations": {"workflow_ids": ["00000000-0000-0000-0000-000000000071"]},
            "observation": {
                "approval_wait_state": "unsatisfied",
                "external_email_effect_count": 0,
                "instance_state": "closed",
                "message_count": 2,
                "workflow_lifecycle": "complete",
            },
            "postgres_deployments": [
                {
                    "deployment_id": "sha256:deployment",
                    "postgres_version": "17.10",
                    "postgres_image": "postgres@sha256:image",
                    "postgres_configuration": {"timezone": "UTC"},
                    "postgres_configuration_digest": "sha256:configuration",
                    "migration_heads": {
                        "example_insurance": "0004_deterministic_verification",
                        "openmagic_runtime": "0003_fenced_effect_kernel",
                    },
                }
            ],
        }
    )

    with pytest.raises(ValidationError) as raised:
        parse_playground_response(malformed, response_type=RenewalDemonstrationResponse)
    locations = {tuple(error["loc"]) for error in raised.value.errors()}
    assert ("observation", "approval_wait_state") in locations
    assert ("observation", "external_email_effect_count") in locations
