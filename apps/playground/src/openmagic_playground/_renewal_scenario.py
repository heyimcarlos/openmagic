"""Approved local-effect renewal demonstration."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from example_insurance.renewals import ExampleInsurance
from openmagic_runtime.threads import ThreadStore

from openmagic_playground._scenario_support import (
    approve_renewal,
    create_renewal_fixture,
    projection_correlations,
    scenario_database,
)
from openmagic_playground.deployment_observation import observe_postgres
from openmagic_playground.renewal_observation import RenewalProjection, decode_renewal_projection
from openmagic_playground.responses import (
    PostgresDeploymentObservation,
    RenewalDemonstrationObservation,
    RenewalDemonstrationResponse,
)
from openmagic_playground.synthetic_provider import SyntheticEmailProvider


@dataclass(frozen=True)
class ApprovedEffectPhase:
    projection: RenewalProjection
    provider_process_id: int
    provider_request_id: str


def _execute_approved_effect(
    database_url: str,
    provider: SyntheticEmailProvider,
) -> ApprovedEffectPhase:
    application = ExampleInsurance(
        database_url=database_url,
        email_provider_url=provider.url,
    )
    application.prepare()
    fixture = create_renewal_fixture(application, ThreadStore(database_url=database_url), "renewal")
    approve_renewal(fixture, "renewal")
    completed = application.run_workflow_worker_once(worker_id="playground-email")
    if completed is None:
        raise AssertionError("approved local effect was not attempted")
    projection = decode_renewal_projection(
        application.renewal_evidence_json(fixture.renewal.input.workflow_id)
    )
    requests = provider.requests()
    outcomes = projection.outcomes
    if (
        outcomes.approval_wait_state != "satisfied"
        or outcomes.external_email_effect_count != 1
        or outcomes.external_effect_certainties != ("applied",)
        or outcomes.instance_state != "closed"
        or outcomes.workflow_lifecycle != "completed"
        or outcomes.completion_event_count != 1
        or len(requests) != 1
    ):
        raise AssertionError("synthetic renewal did not complete its approved local effect")
    return ApprovedEffectPhase(
        projection=projection,
        provider_process_id=provider.pid,
        provider_request_id=requests[0].provider_request_id,
    )


def run_renewal_scenario(*, working_directory: Path) -> RenewalDemonstrationResponse:
    with (
        SyntheticEmailProvider(
            working_directory=working_directory / "provider",
            behavior="success",
        ) as provider,
        scenario_database("renewal") as database_url,
    ):
        phase = _execute_approved_effect(database_url, provider)
        return RenewalDemonstrationResponse(
            correlations=projection_correlations(
                phase.projection,
                worker_ids=("playground-email",),
                process_ids=(phase.provider_process_id,),
                provider_request_ids=(phase.provider_request_id,),
            ),
            observation=RenewalDemonstrationObservation(
                approval_wait_state="satisfied",
                external_email_effect_count=1,
                external_effect_certainties=("applied",),
                instance_state="closed",
                message_count=1,
                workflow_lifecycle="completed",
                completion_event_count=1,
                provider_request_count=1,
                approved_local_execution=True,
            ),
            postgres_deployments=(
                PostgresDeploymentObservation.model_validate(observe_postgres(database_url)),
            ),
        )


__all__: list[str] = []
