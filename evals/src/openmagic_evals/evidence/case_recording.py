"""Process-safe recording of observations produced by deterministic proof cases."""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID, uuid4

from example_insurance.renewals import ExampleInsurance

from openmagic_evals.evidence.contracts import (
    AgentCorrelations,
    ApplicationCorrelations,
    Correlations,
    ProcessCorrelations,
    ProviderCorrelations,
    RuntimeCorrelations,
    merge_correlations,
)
from openmagic_evals.evidence.inspection import EvidenceInspection

_DIRECTORY_ENVIRONMENT = "OPENMAGIC_EVIDENCE_OBSERVATION_DIRECTORY"


@dataclass(frozen=True)
class RecordedCaseObservation:
    case_id: str
    scenario_id: str
    correlations: Correlations
    document: dict[str, object]


def _ids(value: object) -> tuple[UUID, ...]:
    return tuple(UUID(str(item)) for item in value) if isinstance(value, list) else ()


def record_renewal_case(
    *,
    case_id: str,
    scenario_id: str,
    application: ExampleInsurance,
    database_url: str,
    workflow_id: UUID,
    document: Mapping[str, object],
    worker_ids: tuple[str, ...] = (),
    process_ids: tuple[int, ...] = (),
    provider_request_ids: tuple[str, ...] = (),
    additional_command_ids: tuple[UUID, ...] = (),
    domain_event_ids: tuple[UUID, ...] | None = None,
) -> None:
    """Project exact durable identities from the renewal scenario that proved a case."""

    raw = json.loads(application.renewal_evidence_json(workflow_id))
    values = raw["correlations"]
    outcomes = raw["outcomes"]
    instance_id = UUID(str(values["instance_id"]))
    trace_event_ids, delivery_attempt_ids = EvidenceInspection(database_url).renewal_demo_ids(
        instance_id
    )
    record_case_observation(
        case_id=case_id,
        scenario_id=scenario_id,
        correlations=Correlations(
            runtime=RuntimeCorrelations(
                command_ids=tuple(
                    dict.fromkeys((UUID(str(values["command_id"])), *additional_command_ids))
                ),
                workflow_ids=(UUID(str(values["workflow_id"])),),
                instance_ids=(instance_id,),
                step_ids=_ids(values["step_ids"]),
                attempt_ids=_ids(values["attempt_ids"]),
                wait_ids=_ids(outcomes["approval_wait_ids"]),
                signal_ids=_ids(values["signal_ids"]),
                trace_event_ids=trace_event_ids,
            ),
            application=ApplicationCorrelations(
                thread_ids=(UUID(str(values["thread_id"])),),
                message_ids=_ids(values["message_ids"]),
                domain_event_ids=(
                    _ids(values["domain_event_ids"])
                    if domain_event_ids is None
                    else domain_event_ids
                ),
                delivery_ids=_ids(values["delivery_ids"]),
                delivery_attempt_ids=delivery_attempt_ids,
                external_effect_ids=_ids(values["logical_effect_ids"]),
                approval_grant_ids=_ids(values["approval_grant_ids"]),
            ),
            agent=AgentCorrelations(agent_run_ids=_ids(values["agent_run_ids"])),
            process=ProcessCorrelations(worker_ids=worker_ids, process_ids=process_ids),
            provider=ProviderCorrelations(provider_request_ids=provider_request_ids),
        ),
        document=document,
    )


def record_complete_durable_chain(
    *,
    application: ExampleInsurance,
    database_url: str,
    renewal_workflow_id: UUID,
    challenge_id: UUID,
) -> None:
    """Record one relationally verified chain from one PostgreSQL snapshot."""

    chain = EvidenceInspection(database_url).durable_chain(
        renewal_workflow_id=renewal_workflow_id,
        challenge_id=challenge_id,
    )
    correlations = Correlations(
        runtime=RuntimeCorrelations(
            command_ids=chain.command_ids,
            workflow_ids=chain.workflow_ids,
            instance_ids=chain.instance_ids,
            step_ids=chain.step_ids,
            attempt_ids=chain.attempt_ids,
            wait_ids=chain.wait_ids,
            signal_ids=chain.signal_ids,
            trace_event_ids=chain.trace_event_ids,
        ),
        application=ApplicationCorrelations(
            thread_ids=chain.thread_ids,
            message_ids=chain.message_ids,
            domain_event_ids=chain.domain_event_ids,
            delivery_ids=chain.delivery_ids,
            delivery_attempt_ids=chain.delivery_attempt_ids,
            approval_grant_ids=chain.approval_grant_ids,
            verification_challenge_ids=chain.verification_challenge_ids,
            verification_session_ids=chain.verification_session_ids,
        ),
        agent=AgentCorrelations(agent_run_ids=chain.agent_run_ids),
    )
    evidence = json.loads(application.renewal_evidence_json(renewal_workflow_id))
    record_case_observation(
        case_id="trace.complete-durable-chain",
        scenario_id="one-relational-chain",
        correlations=correlations,
        document={
            "connected": True,
            "relationship_checks": chain.relationship_checks,
            "renewal_evidence_schema": evidence["schema_version"],
            "renewal_evidence_redacted": evidence["redacted"],
        },
    )


def record_case_observation(
    *,
    case_id: str,
    scenario_id: str,
    correlations: Correlations,
    document: Mapping[str, object],
) -> None:
    """Record one exact proof observation when the release runner requests it."""

    configured = os.environ.get(_DIRECTORY_ENVIRONMENT)
    if configured is None:
        return
    directory = Path(configured)
    directory.mkdir(parents=True, exist_ok=True)
    identity = hashlib.sha256(f"{case_id}\0{scenario_id}".encode()).hexdigest()
    emission_id = uuid4().hex
    target = directory / f"{identity}.{os.getpid()}.{emission_id}.json"
    temporary = directory / f".{identity}.{os.getpid()}.{emission_id}.tmp"
    payload = {
        "case_id": case_id,
        "scenario_id": scenario_id,
        "correlations": correlations.model_dump(mode="json"),
        "document": dict(document),
    }
    temporary.write_text(
        json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, target)


def load_case_observations(
    directory: Path,
) -> dict[str, tuple[RecordedCaseObservation, ...]]:
    """Load exact observations and reject duplicate scenario identities."""

    by_case: dict[str, list[RecordedCaseObservation]] = {}
    identities: set[tuple[str, str]] = set()
    for path in sorted(directory.glob("*.json")):
        raw = json.loads(path.read_text(encoding="utf-8"))
        observation = RecordedCaseObservation(
            case_id=str(raw["case_id"]),
            scenario_id=str(raw["scenario_id"]),
            correlations=Correlations.model_validate(raw["correlations"]),
            document=dict(raw["document"]),
        )
        identity = (observation.case_id, observation.scenario_id)
        if identity in identities:
            raise ValueError(f"duplicate deterministic observation: {identity!r}")
        identities.add(identity)
        by_case.setdefault(observation.case_id, []).append(observation)
    return {
        case_id: tuple(sorted(items, key=lambda item: item.scenario_id))
        for case_id, items in by_case.items()
    }


def merge_case_observations(
    observations: tuple[RecordedCaseObservation, ...],
) -> tuple[Correlations, dict[str, object]]:
    """Merge one case's exact scenarios into its canonical projection."""

    if not observations:
        raise ValueError("a deterministic case requires at least one exact observation")
    return (
        merge_correlations(item.correlations for item in observations),
        {
            "scenarios": [
                {
                    "scenario_id": item.scenario_id,
                    "observation": item.document,
                }
                for item in observations
            ]
        },
    )


__all__ = [
    "RecordedCaseObservation",
    "load_case_observations",
    "merge_case_observations",
    "record_case_observation",
    "record_complete_durable_chain",
    "record_renewal_case",
]
