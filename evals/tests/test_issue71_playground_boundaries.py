from __future__ import annotations

import json
import sys
from pathlib import Path
from uuid import UUID

import pytest
from openmagic_evals.evidence.playground_client import parse_playground_response
from openmagic_evals.harness.local_provider import LocalEmailProvider
from openmagic_playground.deployment import PlaygroundDeployment
from openmagic_playground.process_launching import ProcessCommand
from openmagic_playground.responses import (
    ControlExerciseResponse,
    PlaygroundInstanceDefinitionCorrelation,
    PlaygroundRuntimeCorrelations,
    RenewalDemonstrationResponse,
)
from openmagic_playground.synthetic_provider import SyntheticEmailProvider
from openmagic_runtime.processes import finish_owned_cleanup, owned_cleanup_scope
from pydantic import ValidationError


def _descendant_command(pid_path: Path, *, leader_raises: bool) -> ProcessCommand:
    return ProcessCommand(
        (
            sys.executable,
            "-c",
            (
                "import os,pathlib,signal,subprocess,sys,time;"
                "signal.signal(signal.SIGTERM, signal.SIG_IGN);"
                "child=subprocess.Popen([sys.executable,'-c',"
                "'import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); "
                "time.sleep(30)']);"
                f"pathlib.Path({str(pid_path)!r}).write_text("
                "f'{os.getpid()} {child.pid}');"
                + (
                    "raise RuntimeError('fixture failure after spawn')"
                    if leader_raises
                    else "time.sleep(30)"
                )
            ),
        )
    )


def _process_is_live(pid: int) -> bool:
    try:
        value = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
    except FileNotFoundError:
        return False
    _, _, suffix = value.rpartition(")")
    return suffix.split()[0] not in {"X", "Z"}


def _assert_command_trees_reaped(pid_files: tuple[Path, ...]) -> None:
    process_ids = tuple(
        int(value)
        for pid_file in pid_files
        for value in pid_file.read_text(encoding="utf-8").split()
    )
    assert all(not _process_is_live(pid) for pid in process_ids)


@pytest.mark.parametrize("leader_raises", [False, True])
def test_deployment_start_failure_reaps_every_descendant(
    tmp_path: Path,
    leader_raises: bool,
) -> None:
    pid_files = tuple(tmp_path / f"{role}.pids" for role in ("api", "workflow", "delivery"))
    deployment = PlaygroundDeployment(
        working_directory=tmp_path,
        readiness_timeout=0.1,
        shutdown_timeout=0.1,
        process_command_overrides={
            "api": _descendant_command(pid_files[0], leader_raises=leader_raises),
            "workflow-worker": _descendant_command(pid_files[1], leader_raises=leader_raises),
            "delivery-worker": _descendant_command(pid_files[2], leader_raises=leader_raises),
        },
    )

    with pytest.raises(
        (RuntimeError, TimeoutError),
        match=r"exited before readiness|did not become ready",
    ):
        deployment.start()

    _assert_command_trees_reaped(pid_files)
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


def test_playground_correlations_reject_duplicate_or_conflicting_definition_mappings() -> None:
    instance_id = UUID("00000000-0000-0000-0000-000000000071")
    workflow_id = UUID("00000000-0000-0000-0000-000000000072")
    mapping = PlaygroundInstanceDefinitionCorrelation(
        instance_id=instance_id,
        definition_key="example_insurance.renewal_outreach",
        definition_version=2,
    )
    with pytest.raises(ValidationError, match="Instance identities must be unique"):
        PlaygroundRuntimeCorrelations(
            workflow_ids=(workflow_id,),
            instance_ids=(instance_id, instance_id),
            instance_definitions=(mapping,),
        )
    with pytest.raises(ValidationError, match="only one Definition identity"):
        PlaygroundRuntimeCorrelations(
            workflow_ids=(workflow_id,),
            instance_ids=(instance_id,),
            instance_definitions=(
                mapping,
                mapping.model_copy(update={"definition_version": 3}),
            ),
        )


@pytest.mark.parametrize("leader_raises", [False, True])
def test_local_provider_start_failure_reaps_descendant_group_and_closes_log(
    tmp_path: Path,
    leader_raises: bool,
) -> None:
    pid_file = tmp_path / "local-provider.pids"
    provider = LocalEmailProvider(
        working_directory=tmp_path,
        readiness_timeout=0.1,
        shutdown_timeout=0.1,
        process_command_override=_descendant_command(pid_file, leader_raises=leader_raises),
    )
    with pytest.raises(
        (RuntimeError, TimeoutError),
        match=r"exited before readiness|did not become ready",
    ):
        provider.start()

    _assert_command_trees_reaped((pid_file,))
    provider.stop()


def test_synthetic_provider_start_timeout_reaps_descendant_group(tmp_path: Path) -> None:
    pid_file = tmp_path / "synthetic-provider.pids"
    provider = SyntheticEmailProvider(
        working_directory=tmp_path,
        readiness_timeout=0.1,
        shutdown_timeout=0.1,
        process_command_override=_descendant_command(pid_file, leader_raises=False),
    )
    with pytest.raises(
        (RuntimeError, TimeoutError),
        match=r"exited before readiness|did not become ready",
    ):
        provider.start()

    _assert_command_trees_reaped((pid_file,))
    provider.stop()


def test_immutable_command_spawn_then_raise_reaps_term_resistant_tree(
    tmp_path: Path,
) -> None:
    pid_file = tmp_path / "spawn-then-raise.pids"
    provider = SyntheticEmailProvider(
        working_directory=tmp_path,
        readiness_timeout=1,
        shutdown_timeout=0.1,
        process_command_override=_descendant_command(pid_file, leader_raises=True),
    )

    with pytest.raises(RuntimeError, match="exited before readiness"):
        provider.start()

    _assert_command_trees_reaped((pid_file,))
    provider.stop()


def test_owned_context_preserves_execution_and_cleanup_failures() -> None:
    execution_error = ValueError("scenario failed")

    def fail_cleanup() -> None:
        raise RuntimeError("cleanup failed")

    with pytest.raises(BaseExceptionGroup, match="execution and cleanup") as raised:
        finish_owned_cleanup(
            fail_cleanup,
            execution_error=execution_error,
            message="execution and cleanup failed",
        )

    assert raised.value.exceptions[0] is execution_error
    assert str(raised.value.exceptions[1]) == "cleanup failed"


def test_owned_cleanup_scope_preserves_body_and_cleanup_failures() -> None:
    execution_error = ValueError("control scenario failed")

    def fail_cleanup() -> None:
        raise RuntimeError("deployment stop failed")

    with (
        pytest.raises(BaseExceptionGroup, match="control execution and cleanup") as raised,
        owned_cleanup_scope(
            fail_cleanup,
            message="control execution and cleanup failed",
        ),
    ):
        raise execution_error

    assert raised.value.exceptions[0] is execution_error
    assert str(raised.value.exceptions[1]) == "deployment stop failed"


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
