"""Pinned synthetic renewal and verification demonstrations."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import psycopg
from example_insurance.migrations import apply_migrations
from example_insurance.renewals import (
    ExampleInsurance,
    SubmitVerificationCode,
    SubmitVerificationCodeInput,
)
from openmagic_runtime.commands import Cause
from openmagic_runtime.threads import ThreadStore

from openmagic_evals.evidence.contracts import (
    ArtifactCase,
    CaseVerdict,
    Correlations,
    PlaygroundArtifact,
    PlaygroundSummary,
    canonical_artifact_json,
    parse_artifact,
)
from openmagic_evals.evidence.redaction import audit_redaction
from openmagic_evals.evidence.release import reproducibility_pin
from openmagic_evals.harness import (
    LocalEmailProvider,
    approve_renewal,
    issue_verification_challenge,
    prepare_renewal_approval,
    renewal_context,
)
from openmagic_evals.harness._postgres import postgres_container


def _digest(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _ids(value: object) -> tuple[UUID, ...]:
    return tuple(UUID(str(item)) for item in value) if isinstance(value, list) else ()


def _write(path: Path, artifact: PlaygroundArtifact) -> PlaygroundArtifact:
    document = canonical_artifact_json(artifact)
    parse_artifact(document)
    audit_redaction(json.loads(document))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(document, encoding="utf-8")
    return artifact


def _demo_artifact(
    *,
    repository_root: Path,
    output: Path,
    command: tuple[str, ...],
    case_id: str,
    started_at: datetime,
    correlations: Correlations,
    observation: dict[str, object],
    process_controls: bool,
) -> PlaygroundArtifact:
    finished_at = datetime.now(UTC)
    return _write(
        output,
        PlaygroundArtifact(
            reproducibility=reproducibility_pin(
                repository_root.resolve(),
                command=command,
                started_at=started_at,
                finished_at=finished_at,
                timeout_seconds=120,
                case_corpus_digest=_digest(case_id),
            ),
            cases=(
                ArtifactCase(
                    case_id=case_id,
                    case_schema_version=1,
                    expected_trials=1,
                    observed_trials=1,
                    seeds=(0,),
                    correlations=correlations,
                    observation_digests=(_digest(observation),),
                    verdict=CaseVerdict(status="passed", invariant_violations=()),
                ),
            ),
            summary=PlaygroundSummary(
                synthetic_data_only=True,
                effects_enabled_by_default=False,
                local_provider=True,
                reset_verified=False,
                process_controls_verified=process_controls,
                contributes_to_correctness=False,
            ),
            limitations=(
                "This is a synthetic demonstration and not correctness evidence.",
                "The result applies only to the pinned local provider and build.",
            ),
        ),
    )


def run_renewal_demo(
    *, repository_root: Path, working_directory: Path, output: Path
) -> PlaygroundArtifact:
    started_at = datetime.now(UTC)
    command_line = (
        "openmagic-evidence",
        "demo-renewal",
        "--working-directory",
        str(working_directory.resolve()),
    )
    with (
        LocalEmailProvider(working_directory=working_directory / "provider") as provider,
        postgres_container(database_name=f"openmagic_test_{uuid4().hex}") as postgres,
    ):
        provider.configure(behaviors=("success",))
        database_url = postgres.get_connection_url(driver=None)
        apply_migrations(database_url)
        application = ExampleInsurance(database_url=database_url, email_provider_url=provider.url)
        application.prepare()
        command, actor = prepare_renewal_approval(
            application,
            ThreadStore(database_url=database_url),
        )
        approve_renewal(application, command, actor)
        result = application.run_workflow_worker_once(worker_id="synthetic-email")
        if result is None:
            raise AssertionError("renewal demonstration did not execute its local effect")
        evidence = json.loads(application.renewal_evidence_json(command.input.workflow_id))
        values = evidence["correlations"]
        outcomes = evidence["outcomes"]
        with psycopg.connect(database_url) as connection:
            trace_rows = connection.execute(
                "SELECT trace_event_id FROM openmagic_runtime.trace_events WHERE instance_id = %s",
                (UUID(values["instance_id"]),),
            ).fetchall()
            delivery_attempt_rows = connection.execute(
                "SELECT delivery_attempt_id FROM openmagic_runtime.delivery_attempts"
            ).fetchall()
        correlations = Correlations(
            command_ids=(UUID(values["command_id"]),),
            workflow_ids=(UUID(values["workflow_id"]),),
            instance_ids=(UUID(values["instance_id"]),),
            step_ids=_ids(values["step_ids"]),
            attempt_ids=_ids(values["attempt_ids"]),
            wait_ids=_ids(outcomes["approval_wait_ids"]),
            signal_ids=_ids(values["signal_ids"]),
            trace_event_ids=tuple(UUID(str(row[0])) for row in trace_rows),
            thread_ids=(UUID(values["thread_id"]),),
            message_ids=_ids(values["message_ids"]),
            agent_run_ids=_ids(values["agent_run_ids"]),
            domain_event_ids=_ids(values["domain_event_ids"]),
            delivery_ids=_ids(values["delivery_ids"]),
            delivery_attempt_ids=tuple(UUID(str(row[0])) for row in delivery_attempt_rows),
            external_effect_ids=_ids(values["logical_effect_ids"]),
            approval_grant_ids=_ids(values["approval_grant_ids"]),
            worker_ids=("synthetic-email",),
            process_ids=(provider.pid,),
            provider_request_ids=tuple(
                str(item["provider_request_id"])
                for item in outcomes["effect_evidence"]
                if item["provider_request_id"] is not None
            ),
        )
        observation = {
            "workflow_lifecycle": outcomes["workflow_lifecycle"],
            "instance_state": outcomes["instance_state"],
            "completion_event_count": outcomes["completion_event_count"],
            "provider_request_count": len(provider.requests()),
        }
    if observation != {
        "workflow_lifecycle": "completed",
        "instance_state": "closed",
        "completion_event_count": 1,
        "provider_request_count": 1,
    }:
        raise AssertionError("renewal demonstration did not reach its accepted terminal outcome")
    return _demo_artifact(
        repository_root=repository_root,
        output=output,
        command=command_line,
        case_id="demo.renewal-complete",
        started_at=started_at,
        correlations=correlations,
        observation=observation,
        process_controls=False,
    )


def run_verification_demo(*, repository_root: Path, output: Path) -> PlaygroundArtifact:
    started_at = datetime.now(UTC)
    command_line = ("openmagic-evidence", "demo-verification")
    with renewal_context(verification_code_secret=b"synthetic-demo-verification") as (
        database_url,
        application,
        threads,
    ):
        scenario = issue_verification_challenge(application, threads)
        challenge_id = scenario.challenge_receipt.result.challenge_id
        if challenge_id is None or scenario.code is None:
            raise AssertionError("verification demonstration did not issue a Challenge")
        receipt = application.submit_verification_code(
            SubmitVerificationCode(
                command_id=uuid4(),
                actor=scenario.actor,
                cause=Cause("message", str(uuid4())),
                input=SubmitVerificationCodeInput(
                    challenge_id=challenge_id,
                    protected_command_id=scenario.protected_command.command_id,
                    workflow_id=scenario.renewal.input.workflow_id,
                    thread_id=scenario.renewal.input.thread_id,
                    purpose="renewal.read_approved_details",
                    code=scenario.code,
                ),
            )
        )
        application.run_delivery_worker_once(worker_id="synthetic-protected-delivery")
        with psycopg.connect(database_url) as connection:
            rows = connection.execute(
                "SELECT c.delivery_instance_id, c.delivery_workflow_id, s.session_id "
                "FROM example_insurance.verification_challenges AS c "
                "JOIN example_insurance.verification_sessions AS s USING (challenge_id) "
                "WHERE c.challenge_id = %s",
                (challenge_id,),
            ).fetchone()
            step_rows = (
                connection.execute(
                    "SELECT step_id, attempt_id FROM openmagic_runtime.attempts WHERE instance_id = %s",
                    (rows[0],),
                ).fetchall()
                if rows is not None
                else ()
            )
        if rows is None or receipt.result.verification_outcome != "verified":
            raise AssertionError("verification demonstration did not verify")
        correlations = Correlations(
            command_ids=(scenario.protected_command.command_id, receipt.command_id),
            workflow_ids=(scenario.renewal.input.workflow_id, UUID(str(rows[1]))),
            instance_ids=(UUID(str(rows[0])),),
            step_ids=tuple(UUID(str(row[0])) for row in step_rows),
            attempt_ids=tuple(UUID(str(row[1])) for row in step_rows),
            thread_ids=(scenario.renewal.input.thread_id, scenario.identifier_thread_id),
            verification_challenge_ids=(challenge_id,),
            verification_session_ids=(UUID(str(rows[2])),),
            worker_ids=("synthetic-protected-delivery",),
        )
        observation = {
            "verification_outcome": receipt.result.verification_outcome,
            "protected_outcome": receipt.result.protected_outcome,
            "session_count": 1,
        }
    return _demo_artifact(
        repository_root=repository_root,
        output=output,
        command=command_line,
        case_id="demo.deterministic-verification",
        started_at=started_at,
        correlations=correlations,
        observation=observation,
        process_controls=False,
    )


__all__ = ["run_renewal_demo", "run_verification_demo"]
