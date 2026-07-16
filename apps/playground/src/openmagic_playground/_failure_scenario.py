"""Incomplete external-effect scenarios for playground control evidence."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from example_insurance.renewals import ExampleInsurance
from openmagic_runtime.threads import ThreadStore

from openmagic_playground._scenario_support import (
    approve_renewal,
    create_renewal_fixture,
    projection_correlations,
)
from openmagic_playground.renewal_observation import decode_renewal_projection
from openmagic_playground.responses import FailureScenarioObservation, PlaygroundCorrelations

FailureScenarioKind = Literal["intentional-failure", "disconnected-provider"]


@dataclass(frozen=True)
class FailureScenarioPhase:
    observation: FailureScenarioObservation
    correlations: PlaygroundCorrelations


def run_failure_scenario(
    *,
    database_url: str,
    scenario: FailureScenarioKind,
    provider_url: str,
    provider_connected: bool,
    provider_process_id: int | None = None,
) -> FailureScenarioPhase:
    application = ExampleInsurance(database_url=database_url, email_provider_url=provider_url)
    application.prepare()
    fixture = create_renewal_fixture(
        application,
        ThreadStore(database_url=database_url),
        scenario,
    )
    approve_renewal(fixture, scenario)
    worker_id = f"{scenario}-email"
    result = application.run_workflow_worker_once(worker_id=worker_id)
    if result is None:
        raise AssertionError(f"{scenario} did not exercise its provider boundary")
    projection = decode_renewal_projection(
        application.renewal_evidence_json(fixture.renewal.input.workflow_id)
    )
    outcomes = projection.outcomes
    expected: Literal["not_applied", "uncertain"] = (
        "not_applied" if provider_connected else "uncertain"
    )
    if (
        outcomes.external_effect_certainties != (expected,)
        or outcomes.instance_state != "open"
        or outcomes.workflow_lifecycle != "active"
    ):
        raise AssertionError(f"{scenario} did not retain its explicit incomplete state")
    provider_request_ids = tuple(
        item.provider_request_id
        for item in outcomes.effect_evidence
        if item.provider_request_id is not None
    )
    return FailureScenarioPhase(
        observation=FailureScenarioObservation(
            scenario=scenario,
            external_effect_certainty=expected,
            instance_state="open",
            workflow_lifecycle="active",
            provider_connected=provider_connected,
        ),
        correlations=projection_correlations(
            projection,
            worker_ids=(worker_id,),
            process_ids=(provider_process_id,) if provider_process_id is not None else (),
            provider_request_ids=provider_request_ids,
        ),
    )


__all__: list[str] = []
