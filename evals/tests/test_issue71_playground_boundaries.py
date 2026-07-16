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
from pydantic import ValidationError


class _FailingLog(io.BytesIO):
    def close(self) -> None:
        super().close()
        raise RuntimeError("synthetic log-close failure")


class _SurvivingProcess:
    pid = 710071

    @staticmethod
    def poll() -> None:
        return None

    @staticmethod
    def terminate() -> None:
        raise OSError("synthetic terminate failure")

    @staticmethod
    def kill() -> None:
        raise OSError("synthetic kill failure")

    @staticmethod
    def wait(timeout: float) -> None:
        raise subprocess.TimeoutExpired("synthetic-survivor", timeout)


class _RetryableContainer:
    def __init__(self) -> None:
        self.stop_calls = 0

    def stop(self) -> None:
        self.stop_calls += 1
        if self.stop_calls == 1:
            raise RuntimeError("synthetic container-stop failure")


def _term_resistant_process() -> subprocess.Popen[bytes]:
    process = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import signal,time; signal.signal(signal.SIGTERM, lambda *_: None); time.sleep(30)",
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    time.sleep(0.1)
    return process


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
        log_handle=log,
    )


def test_stop_reaps_every_child_and_closes_every_log_before_reporting_failures(
    tmp_path: Path,
) -> None:
    first = _term_resistant_process()
    second = _term_resistant_process()
    failing_log = _FailingLog()
    surviving_log = io.BytesIO()
    deployment = PlaygroundDeployment(
        working_directory=tmp_path,
        shutdown_timeout=0.1,
    )
    deployment._running.extend((_running(first, failing_log), _running(second, surviving_log)))

    with pytest.raises(ExceptionGroup, match="playground cleanup failed") as raised:
        deployment.stop()

    process_group = raised.value.exceptions[0]
    assert isinstance(process_group, ExceptionGroup)
    assert str(process_group.exceptions[0]) == "synthetic log-close failure"
    assert first.poll() is not None
    assert second.poll() is not None
    assert failing_log.closed
    assert surviving_log.closed
    assert deployment.processes == ()


def test_stop_retains_ownership_when_child_death_cannot_be_verified(tmp_path: Path) -> None:
    process = cast(subprocess.Popen[bytes], _SurvivingProcess())
    log = io.BytesIO()
    deployment = PlaygroundDeployment(working_directory=tmp_path, shutdown_timeout=0.1)
    running = _running(process, log)
    deployment._running.append(running)

    with pytest.raises(ExceptionGroup, match="playground cleanup failed"):
        deployment.stop()

    assert deployment._running == [running]
    assert deployment.processes == (running.public,)
    assert log.closed


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


def test_provider_retains_child_but_closes_log_when_death_cannot_be_verified(
    tmp_path: Path,
) -> None:
    provider = LocalEmailProvider(working_directory=tmp_path, shutdown_timeout=0.1)
    process = cast(subprocess.Popen[bytes], _SurvivingProcess())
    log = (tmp_path / "survivor.log").open("ab")
    provider._process = process
    provider._log_handle = log

    with pytest.raises(ExceptionGroup, match="local email provider cleanup failed"):
        provider.stop()

    assert provider._process is process
    assert provider._log_handle is None
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
