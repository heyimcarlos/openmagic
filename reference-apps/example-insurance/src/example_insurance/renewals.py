"""Installed Example Insurance renewal Workflow Control Plane."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from typing import Any
from uuid import UUID, uuid4

import psycopg
from openmagic_runtime.agents import AgentRunInput, AgentRuns
from openmagic_runtime.commands import (
    Actor,
    Cause,
    CommandDispatcher,
    CommandReceipt,
    CommandRegistryBuilder,
    StateConflict,
)
from openmagic_runtime.delivery import (
    ClaimDelivery,
    ClaimedDelivery,
    DeliveryAcknowledgement,
    DeliveryControl,
    DeliveryWork,
    claim_delivery_once,
)
from openmagic_runtime.execution import (
    AttemptExecution,
    CancellationToken,
    DeterministicExecutor,
    FreshAgentExecutor,
)
from openmagic_runtime.kernel.control import KernelControl, StartInstance
from openmagic_runtime.kernel.definitions import DefinitionCatalog
from openmagic_runtime.kernel.work import ClaimedAttempt, ClaimWork, KernelWork, claim_once
from openmagic_runtime.threads import ThreadAccess
from psycopg import Connection
from psycopg.types.json import Jsonb

from example_insurance.renewal_definition import RENEWAL_DEFINITION
from example_insurance.renewal_evidence import RenewalEvidenceProjector
from example_insurance.renewal_policies import (
    RenewalCompletionPolicy,
    RenewalDeliveryPolicy,
    RenewalWorkflowPolicy,
)


@dataclass(frozen=True)
class StartRenewalOutreachInput:
    workflow_id: UUID
    thread_id: UUID
    policy_id: UUID
    policy_number: str
    policyholder_name: str
    renewal_date: str
    expiring_premium_cents: int


@dataclass(frozen=True)
class StartRenewalOutreach:
    command_id: UUID
    actor: Actor
    cause: Cause
    input: StartRenewalOutreachInput


@dataclass(frozen=True)
class StartRenewalOutreachResult:
    workflow_id: UUID
    instance_id: UUID
    thread_id: UUID


@dataclass(frozen=True)
class WorkflowAttemptResult:
    attempt_id: UUID
    template_key: str
    executor_key: str
    agent_run_id: UUID | None
    agent_runtime_generation: int | None
    steps: dict[str, UUID]
    waits: dict[str, UUID]


@dataclass(frozen=True)
class RenewalDraftCandidate:
    subject: str
    body: str


def _validate_start_command(command: StartRenewalOutreach) -> None:
    value = command.input
    if not command.actor.identifier.strip() or not command.cause.identifier.strip():
        raise ValueError("Command Actor and Cause identifiers must be non-empty")
    if not value.policy_number.strip() or not value.policyholder_name.strip():
        raise ValueError("Policy number and policyholder name must be non-empty")
    date.fromisoformat(value.renewal_date)
    if value.expiring_premium_cents <= 0:
        raise ValueError("Expiring premium must be positive")


def _draft_agent_factory() -> Callable[[dict[str, Any]], RenewalDraftCandidate]:
    def run(value: dict[str, Any]) -> RenewalDraftCandidate:
        premium = int(value["expiring_premium_cents"]) / 100
        return RenewalDraftCandidate(
            subject=f"Renewal review for policy {value['policy_number']}",
            body=(
                f"Hello {value['policyholder_name']}, your policy renews on "
                f"{value['renewal_date']}. The expiring premium is CAD {premium:,.2f}. "
                "Please review this draft before any renewal email is sent."
            ),
        )

    return run


class ExampleInsurance:
    def __init__(self, *, database_url: str) -> None:
        self._database_url = database_url
        registrations = (
            CommandRegistryBuilder()
            .register(
                command_type="renewal.start_outreach",
                schema_version=1,
                command_class=StartRenewalOutreach,
                result_class=StartRenewalOutreachResult,
                handler=self._handle_start,
                result_decoder=lambda payload: StartRenewalOutreachResult(
                    workflow_id=UUID(payload["workflow_id"]),
                    instance_id=UUID(payload["instance_id"]),
                    thread_id=UUID(payload["thread_id"]),
                ),
                validator=_validate_start_command,
            )
            .build()
        )
        self._dispatcher = CommandDispatcher(
            database_url=database_url,
            registrations=registrations,
        )
        self._workflow_policy = RenewalWorkflowPolicy()
        self._delivery_policy = RenewalDeliveryPolicy()
        self._completion_policy = RenewalCompletionPolicy()
        self._executors = {
            "example_insurance.renewal_facts.v1": DeterministicExecutor(
                lambda value: {
                    "policy_number": value["policy_number"],
                    "policyholder_name": value["policyholder_name"],
                    "renewal_date": value["renewal_date"],
                    "expiring_premium_cents": value["expiring_premium_cents"],
                }
            ),
            "example_insurance.renewal_draft_agent.v1": FreshAgentExecutor(
                _draft_agent_factory,
                result_class=RenewalDraftCandidate,
                encoder=lambda candidate: {
                    "subject": candidate.subject,
                    "body": candidate.body,
                },
                timeout_seconds=5,
            ),
        }

    def prepare(self) -> None:
        DefinitionCatalog(database_url=self._database_url).register(RENEWAL_DEFINITION)

    def start_renewal_outreach(
        self, command: StartRenewalOutreach
    ) -> CommandReceipt[StartRenewalOutreachResult]:
        return self._dispatcher.execute(
            command_type="renewal.start_outreach",
            schema_version=1,
            command=command,
        )

    def run_workflow_worker_once(self, *, worker_id: str) -> WorkflowAttemptResult | None:
        self.recover_expired_workflow_attempt()
        attempt = self.claim_workflow_attempt(worker_id=worker_id, claim_request_id=uuid4())
        if attempt is None:
            return None
        return self.complete_workflow_attempt(attempt=attempt, worker_id=worker_id)

    def recover_expired_workflow_attempt(self) -> bool:
        with psycopg.connect(self._database_url) as connection, connection.transaction():
            required = KernelWork(connection).recover_expired()
            if required is None:
                return False
            AgentRuns(connection).abandon_for_attempt(required.attempt_id)
            decision = self._workflow_policy.expired_attempt(
                template_key=required.template_key,
                attempt_number=required.attempt_number,
            )
            control = KernelControl(connection)
            if decision.action == "retry":
                control.retry(required)
            elif decision.failure is not None:
                control.fail(required, failure=decision.failure)
            else:
                raise RuntimeError("Failure disposition requires structured failure data")
            if not required.consumed:
                raise RuntimeError("Recovery disposition remained unresolved")
            return True

    def claim_workflow_attempt(
        self, *, worker_id: str, claim_request_id: UUID
    ) -> ClaimedAttempt | None:
        return claim_once(
            database_url=self._database_url,
            request=ClaimWork(
                claim_request_id=claim_request_id,
                worker_id=worker_id,
                executor_keys=tuple(self._executors),
            ),
        )

    def complete_workflow_attempt(
        self, *, attempt: ClaimedAttempt, worker_id: str
    ) -> WorkflowAttemptResult:
        agent_run_id: UUID | None = None
        with psycopg.connect(self._database_url) as connection, connection.transaction():
            authority = KernelWork(connection).execution_authority(
                attempt,
                worker_id=worker_id,
            )
            durable_attempt = authority.claim
            if authority.directive == "replay":
                agent_runs = AgentRuns(connection)
                existing_run = agent_runs.find_by_attempt(durable_attempt.attempt_id)
                if existing_run is not None and existing_run.status == "completed":
                    agent_run_id = existing_run.agent_run_id
                if authority.accepted_observation is None:
                    raise RuntimeError("Completed Attempt has no accepted observation")
                accepted_observation = authority.accepted_observation
            elif durable_attempt.executor_key == "example_insurance.renewal_draft_agent.v1":
                agent_runs = AgentRuns(connection)
                if agent_runs.find_by_attempt(durable_attempt.attempt_id) is not None:
                    raise RuntimeError("Agent Attempt already has a durable Agent Run")
                thread_id = UUID(durable_attempt.input["thread_id"])
                cutoff = ThreadAccess(connection).context_cutoff(thread_id)
                agent_run = agent_runs.start(
                    attempt_id=durable_attempt.attempt_id,
                    input=AgentRunInput(
                        agent_key="example_insurance.renewal_draft",
                        agent_version=1,
                        task_type="renewal.draft",
                        task_version=1,
                        thread_id=thread_id,
                        context_through_sequence=cutoff,
                        domain_event_context=(),
                        audience_context={
                            "kind": "workflow_role",
                            "identifier": "broker",
                        },
                        locale="en-CA",
                        task_input=dict(durable_attempt.input),
                    ),
                )
                agent_run_id = agent_run.agent_run_id
        if authority.directive == "replay":
            return self.submit_workflow_observation(
                attempt=durable_attempt,
                worker_id=worker_id,
                observation=accepted_observation,
            )
        executor = self._executors[durable_attempt.executor_key]
        try:
            observation = executor.execute(
                AttemptExecution(
                    instance_id=durable_attempt.instance_id,
                    step_id=durable_attempt.step_id,
                    attempt_id=durable_attempt.attempt_id,
                    attempt_number=durable_attempt.attempt_number,
                    template_key=durable_attempt.template_key,
                    executor_key=durable_attempt.executor_key,
                    input=durable_attempt.input,
                ),
                CancellationToken(),
            )
        except Exception as error:
            if agent_run_id is not None:
                with psycopg.connect(self._database_url) as connection, connection.transaction():
                    AgentRuns(connection).fail_for_attempt(
                        durable_attempt.attempt_id,
                        {"class": type(error).__name__},
                    )
            raise
        return self.submit_workflow_observation(
            attempt=durable_attempt,
            worker_id=worker_id,
            observation=observation.value,
        )

    def submit_workflow_observation(
        self,
        *,
        attempt: ClaimedAttempt,
        worker_id: str,
        observation: dict[str, Any],
    ) -> WorkflowAttemptResult:
        with psycopg.connect(self._database_url) as connection, connection.transaction():
            required = KernelWork(connection).accept_result(
                attempt,
                worker_id=worker_id,
                observation=observation,
            )
            control = KernelControl(connection)
            workflow = connection.execute(
                "SELECT workflow_id, thread_id FROM example_insurance.renewal_workflows "
                "WHERE instance_id = %s FOR UPDATE",
                (required.instance_id,),
            ).fetchone()
            if workflow is None:
                raise RuntimeError("Renewal Workflow is unavailable")
            workflow_id = UUID(str(workflow[0]))
            thread_id = UUID(str(workflow[1]))
            agent_run_id: UUID | None = None
            if required.template_key == "gather_renewal_facts":
                decision = self._workflow_policy.facts_succeeded(
                    workflow_id=workflow_id,
                    thread_id=thread_id,
                    observation=observation,
                )
                steps, waits = control.succeed(
                    required,
                    output=decision.output,
                    outcome_route=decision.outcome_route,
                    route_input=decision.route_input,
                )
            elif required.template_key == "draft_renewal_email":
                if required.replayed:
                    existing_draft = connection.execute(
                        "SELECT draft_id, agent_run_id FROM example_insurance.renewal_drafts "
                        "WHERE step_id = %s",
                        (required.step_id,),
                    ).fetchone()
                    if existing_draft is None:
                        raise RuntimeError("Accepted draft result has no durable draft")
                    draft_id = UUID(str(existing_draft[0]))
                    agent_run_id = UUID(str(existing_draft[1]))
                    decision = self._workflow_policy.draft_succeeded(
                        workflow_id=workflow_id,
                        draft_id=draft_id,
                    )
                    steps, waits = control.succeed(
                        required,
                        output=decision.output,
                        outcome_route=decision.outcome_route,
                        route_input=decision.route_input,
                    )
                else:
                    agent_run = AgentRuns(connection).complete_for_attempt(
                        required.attempt_id,
                        observation,
                    )
                    agent_run_id = agent_run.agent_run_id
                    draft_id = uuid4()
                    connection.execute(
                        "INSERT INTO example_insurance.renewal_drafts "
                        "(draft_id, workflow_id, step_id, agent_run_id, subject, body) "
                        "VALUES (%s, %s, %s, %s, %s, %s)",
                        (
                            draft_id,
                            workflow_id,
                            required.step_id,
                            agent_run_id,
                            observation["subject"],
                            observation["body"],
                        ),
                    )
                    event_id = uuid4()
                    connection.execute(
                        "INSERT INTO example_insurance.domain_events "
                        "(event_id, event_type, schema_version, workflow_id, actor, cause, "
                        "payload) VALUES "
                        "(%s, 'renewal.draft.ready', 1, %s, %s, %s, %s)",
                        (
                            event_id,
                            workflow_id,
                            Jsonb({"kind": "system", "identifier": "workflow-control-plane"}),
                            Jsonb({"kind": "attempt", "identifier": str(required.attempt_id)}),
                            Jsonb(
                                {
                                    "draft_id": str(draft_id),
                                    "step_id": str(required.step_id),
                                }
                            ),
                        ),
                    )
                    DeliveryControl(connection).create(
                        domain_event_id=event_id,
                        thread_id=thread_id,
                        audience=self._delivery_policy.audience,
                        message_author=self._delivery_policy.message_author,
                        content_descriptor=self._delivery_policy.content_descriptor(observation),
                        message_content=self._delivery_policy.render_message(observation),
                        retry_policy=self._delivery_policy.retry_policy,
                    )
                    decision = self._workflow_policy.draft_succeeded(
                        workflow_id=workflow_id,
                        draft_id=draft_id,
                    )
                    steps, waits = control.succeed(
                        required,
                        output=decision.output,
                        outcome_route=decision.outcome_route,
                        route_input=decision.route_input,
                    )
                    if self._completion_policy.is_complete(
                        approval_wait_state="unsatisfied",
                        external_effect_count=0,
                    ):
                        raise RuntimeError("Renewal completed before durable approval")
            else:
                raise RuntimeError(f"unsupported renewal Step: {required.template_key}")
            if not required.consumed:
                raise RuntimeError("Attempt disposition remained unresolved")
        return WorkflowAttemptResult(
            attempt_id=required.attempt_id,
            template_key=required.template_key,
            executor_key=attempt.executor_key,
            agent_run_id=agent_run_id,
            agent_runtime_generation=1 if agent_run_id is not None else None,
            steps=steps,
            waits=waits,
        )

    def run_delivery_worker_once(self, *, worker_id: str) -> DeliveryAcknowledgement | None:
        claim = self.claim_delivery_attempt(worker_id=worker_id, claim_request_id=uuid4())
        if claim is None:
            return None
        return self.complete_delivery_attempt(claim=claim, worker_id=worker_id)

    def claim_delivery_attempt(
        self, *, worker_id: str, claim_request_id: UUID
    ) -> ClaimedDelivery | None:
        return claim_delivery_once(
            database_url=self._database_url,
            request=ClaimDelivery(claim_request_id=claim_request_id, worker_id=worker_id),
        )

    def complete_delivery_attempt(
        self, *, claim: ClaimedDelivery, worker_id: str
    ) -> DeliveryAcknowledgement:
        with psycopg.connect(self._database_url) as connection, connection.transaction():
            return DeliveryWork(connection).acknowledge(
                claim,
                worker_id=worker_id,
                proposed_thread_id=claim.thread_id,
            )

    def replay_delivery_acknowledgement(
        self, *, delivery_attempt_id: UUID
    ) -> DeliveryAcknowledgement:
        with psycopg.connect(self._database_url) as connection, connection.transaction():
            return DeliveryWork(connection).replay_acknowledgement(delivery_attempt_id)

    def renewal_evidence_json(self, workflow_id: UUID) -> str:
        return RenewalEvidenceProjector(database_url=self._database_url).to_json(workflow_id)

    def _handle_start(
        self,
        command: StartRenewalOutreach,
        connection: Connection[tuple[Any, ...]],
    ) -> StartRenewalOutreachResult:
        try:
            ThreadAccess(connection).require(command.input.thread_id)
        except KeyError:
            raise StateConflict("The exact Thread does not exist") from None
        existing = connection.execute(
            "SELECT 1 FROM example_insurance.renewal_workflows WHERE workflow_id = %s",
            (command.input.workflow_id,),
        ).fetchone()
        if existing is not None:
            raise StateConflict("The renewal Workflow already exists")
        start = KernelControl(connection).start(
            StartInstance(
                command_id=command.command_id,
                definition_key=RENEWAL_DEFINITION.identity.key,
                definition_version=RENEWAL_DEFINITION.identity.version,
                instance_input={
                    "workflow_id": str(command.input.workflow_id),
                    "thread_id": str(command.input.thread_id),
                    "policy_id": str(command.input.policy_id),
                },
                route_input={
                    "policy_id": str(command.input.policy_id),
                    "policy_number": command.input.policy_number,
                    "policyholder_name": command.input.policyholder_name,
                    "renewal_date": command.input.renewal_date,
                    "expiring_premium_cents": command.input.expiring_premium_cents,
                },
            )
        )
        connection.execute(
            "INSERT INTO example_insurance.renewal_workflows "
            "(workflow_id, start_command_id, instance_id, thread_id, policy_id, policy_number, "
            "policyholder_name, renewal_date, expiring_premium_cents, lifecycle) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'active')",
            (
                command.input.workflow_id,
                command.command_id,
                start.instance_id,
                command.input.thread_id,
                command.input.policy_id,
                command.input.policy_number,
                command.input.policyholder_name,
                command.input.renewal_date,
                command.input.expiring_premium_cents,
            ),
        )
        return StartRenewalOutreachResult(
            workflow_id=command.input.workflow_id,
            instance_id=start.instance_id,
            thread_id=command.input.thread_id,
        )


__all__ = [
    "ExampleInsurance",
    "StartRenewalOutreach",
    "StartRenewalOutreachInput",
    "StartRenewalOutreachResult",
    "WorkflowAttemptResult",
]
