from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import BinaryIO

import pytest
from openmagic_evals.evidence.playground_client import parse_playground_response
from openmagic_evals.harness.local_provider import LocalEmailProvider
from openmagic_playground.deployment import PlaygroundDeployment
from openmagic_playground.process_launching import finish_owned_context
from openmagic_playground.responses import (
    ControlExerciseResponse,
    RenewalDemonstrationResponse,
)
from openmagic_playground.synthetic_provider import SyntheticEmailProvider
from pydantic import ValidationError


class _DescendantProcessLauncher:
    """Real launcher adapter whose owned leader and descendant resist TERM."""

    def __init__(self, working_directory: Path) -> None:
        self.working_directory = working_directory
        self.process_ids: list[int] = []
        self.child_process_ids: list[int] = []
        self.outputs: list[BinaryIO] = []

    def launch(
        self,
        command: Sequence[str],
        *,
        working_directory: Path,
        environment: Mapping[str, str],
        output: BinaryIO,
    ) -> subprocess.Popen[bytes]:
        del command, working_directory, environment
        child_pid_path = self.working_directory / f"child-{time.monotonic_ns()}.pid"
        process = subprocess.Popen(
            [
                sys.executable,
                "-c",
                (
                    "import pathlib,signal,subprocess,sys,time;"
                    "signal.signal(signal.SIGTERM, signal.SIG_IGN);"
                    "child=subprocess.Popen([sys.executable,'-c',"
                    "'import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); "
                    "time.sleep(30)']);"
                    f"pathlib.Path({str(child_pid_path)!r}).write_text(str(child.pid));"
                    "time.sleep(30)"
                ),
            ],
            stdin=subprocess.DEVNULL,
            stdout=output,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        deadline = time.monotonic() + 3
        while not child_pid_path.is_file() and time.monotonic() < deadline:
            time.sleep(0.01)
        if not child_pid_path.is_file():
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()
            raise RuntimeError("descendant process fixture did not become ready")
        self.process_ids.append(process.pid)
        self.child_process_ids.append(int(child_pid_path.read_text(encoding="utf-8")))
        self.outputs.append(output)
        return process


def _process_is_live(pid: int) -> bool:
    try:
        value = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
    except FileNotFoundError:
        return False
    _, _, suffix = value.rpartition(")")
    return suffix.split()[0] not in {"X", "Z"}


def _assert_launcher_reaped(launcher: _DescendantProcessLauncher, expected: int) -> None:
    assert len(launcher.process_ids) == expected
    assert len(launcher.child_process_ids) == expected
    assert all(not _process_is_live(pid) for pid in launcher.process_ids)
    assert all(not _process_is_live(pid) for pid in launcher.child_process_ids)
    assert all(output.closed for output in launcher.outputs)


def test_deployment_start_failure_reaps_every_descendant_and_closes_every_log(
    tmp_path: Path,
) -> None:
    launcher = _DescendantProcessLauncher(tmp_path)
    deployment = PlaygroundDeployment(
        working_directory=tmp_path,
        readiness_timeout=0.1,
        shutdown_timeout=0.1,
        process_launcher=launcher,
    )

    with pytest.raises(TimeoutError, match="did not become ready"):
        deployment.start()

    _assert_launcher_reaped(launcher, expected=3)
    assert deployment.processes == ()
    deployment.stop()


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


def test_local_provider_start_failure_reaps_descendant_group_and_closes_log(
    tmp_path: Path,
) -> None:
    launcher = _DescendantProcessLauncher(tmp_path)
    provider = LocalEmailProvider(
        working_directory=tmp_path,
        readiness_timeout=0.1,
        shutdown_timeout=0.1,
        process_launcher=launcher,
    )
    with pytest.raises(TimeoutError, match="did not become ready"):
        provider.start()

    _assert_launcher_reaped(launcher, expected=1)
    provider.stop()


def test_synthetic_provider_start_failure_reaps_descendant_group_and_closes_log(
    tmp_path: Path,
) -> None:
    launcher = _DescendantProcessLauncher(tmp_path)
    provider = SyntheticEmailProvider(
        working_directory=tmp_path,
        readiness_timeout=0.1,
        shutdown_timeout=0.1,
        process_launcher=launcher,
    )
    with pytest.raises(TimeoutError, match="did not become ready"):
        provider.start()

    _assert_launcher_reaped(launcher, expected=1)
    provider.stop()


def test_owned_context_preserves_execution_and_cleanup_failures() -> None:
    execution_error = ValueError("scenario failed")

    def fail_cleanup() -> None:
        raise RuntimeError("cleanup failed")

    with pytest.raises(BaseExceptionGroup, match="execution and cleanup") as raised:
        finish_owned_context(
            fail_cleanup,
            execution_error=execution_error,
            message="execution and cleanup failed",
        )

    assert raised.value.exceptions[0] is execution_error
    assert str(raised.value.exceptions[1]) == "cleanup failed"


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
