"""THROWAWAY PROTOTYPE for the V0 Workflow integration seam.

This file answers one design question: can OpenMagic replace direct named-agent
delegation with typed Workflow tools, a one-Job Worker claim, Run-scoped
execution, and Notification-driven fresh Interaction Agent turns?

Run from the repository root:

    python server/services/workflows/prototype_v0.py --auto

The prototype is intentionally in-memory, deterministic, and incomplete. It is
an executable design artifact, not production code.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import asdict, dataclass, field
from typing import Any, Callable


DRAFT_KIND = "renewal_email.draft.v1"
SEND_KIND = "gmail.send_email.v1"


class PrototypeError(RuntimeError):
    """Report a violated prototype invariant."""


class IdFactory:
    """Create stable, readable identifiers for the transcript."""

    def __init__(self) -> None:
        self._counts: dict[str, int] = {}

    def new(self, prefix: str) -> str:
        value = self._counts.get(prefix, 0) + 1
        self._counts[prefix] = value
        return f"{prefix}_{value:02d}"


@dataclass
class Workflow:
    id: str
    kind: str
    objective: str
    status: str
    input: dict[str, Any]
    organization: str
    participants: list[str]
    authorized_party_ids: set[str]


@dataclass
class Job:
    id: str
    workflow_id: str
    kind: str
    status: str
    input: dict[str, Any]
    dependencies: list[str] = field(default_factory=list)
    output: dict[str, Any] | None = None
    attempts: int = 0
    max_attempts: int = 1


@dataclass
class Run:
    id: str
    workflow_id: str
    job_id: str
    status: str
    worker_id: str
    runtime_instance_id: str | None = None
    result: dict[str, Any] | None = None


@dataclass(frozen=True)
class RunResult:
    outcome: str
    data: dict[str, Any] | None
    evidence: list[dict[str, Any]]
    error: dict[str, Any] | None = None


@dataclass
class WorkflowEvent:
    id: str
    workflow_id: str
    event_type: str
    actor: str
    cause: str
    job_id: str | None = None
    run_id: str | None = None
    data: dict[str, Any] = field(default_factory=dict)


@dataclass
class Notification:
    id: str
    workflow_id: str
    workflow_event_id: str
    kind: str
    status: str = "queued"


@dataclass(frozen=True)
class JobKindContract:
    executor: str
    execute: Callable[[dict[str, Any]], RunResult]


class PrototypeStore:
    """Hold the facts that PostgreSQL will own in the implementation."""

    def __init__(self) -> None:
        self.ids = IdFactory()
        self.workflows: dict[str, Workflow] = {}
        self.jobs: dict[str, Job] = {}
        self.runs: dict[str, Run] = {}
        self.events: list[WorkflowEvent] = []
        self.notifications: dict[str, Notification] = {}
        self.search_audit: list[dict[str, Any]] = []
        self.packet_reads: list[dict[str, str]] = []
        self.user_messages: list[str] = []
        self.live_interaction_runtimes: set[str] = set()
        self.live_execution_runtimes: set[str] = set()
        self.interaction_runtime_history: list[str] = []
        self.execution_runtime_history: list[str] = []
        self.provider_calls: list[dict[str, Any]] = []

    def target_snapshot(self, workflow_id: str) -> dict[str, Any]:
        workflow = self.workflows[workflow_id]
        jobs = [job for job in self.jobs.values() if job.workflow_id == workflow_id]
        job_ids = {job.id for job in jobs}
        runs = [run for run in self.runs.values() if run.job_id in job_ids]
        events = [event for event in self.events if event.workflow_id == workflow_id]
        notifications = [
            notification
            for notification in self.notifications.values()
            if notification.workflow_id == workflow_id
        ]
        return {
            "workflow": _jsonable(asdict(workflow)),
            "jobs": [_jsonable(asdict(job)) for job in jobs],
            "runs": [_jsonable(asdict(run)) for run in runs],
            "events": [_jsonable(asdict(event)) for event in events],
            "notifications": [_jsonable(asdict(notification)) for notification in notifications],
            "live_runtimes": {
                "interaction_agents": sorted(self.live_interaction_runtimes),
                "execution_agents": sorted(self.live_execution_runtimes),
            },
            "runtime_history": {
                "interaction_agents": list(self.interaction_runtime_history),
                "execution_agents": list(self.execution_runtime_history),
            },
            "provider_call_count": len(self.provider_calls),
            "user_messages": list(self.user_messages),
            "search_audit": list(self.search_audit),
            "packet_reads": list(self.packet_reads),
        }


class ControlPlane:
    """Own every accepted Workflow, Job, Run, and Event transition."""

    def __init__(self, store: PrototypeStore) -> None:
        self.store = store

    def search_workflows(self, *, party_id: str, query: str) -> list[dict[str, Any]]:
        terms = {term.casefold() for term in query.split()}
        matches: list[Workflow] = []
        for workflow in self.store.workflows.values():
            if party_id not in workflow.authorized_party_ids:
                continue
            searchable = " ".join(
                [
                    workflow.objective,
                    workflow.organization,
                    *workflow.participants,
                    str(workflow.input.get("renewal_period", "")),
                ]
            ).casefold()
            if all(term in searchable for term in terms):
                matches.append(workflow)
        matches.sort(key=lambda workflow: (workflow.status != "active", workflow.id))
        result = [
            {
                "workflow_id": workflow.id,
                "objective": workflow.objective,
                "status": workflow.status,
                "organization": workflow.organization,
                "renewal_period": workflow.input.get("renewal_period"),
            }
            for workflow in matches
        ]
        self.store.search_audit.append(
            {"party_id": party_id, "query": query, "result_ids": [item["workflow_id"] for item in result]}
        )
        return result

    def read_workflow_packet(self, *, party_id: str, workflow_id: str) -> dict[str, Any]:
        workflow = self.store.workflows.get(workflow_id)
        if workflow is None or party_id not in workflow.authorized_party_ids:
            raise PrototypeError("not_found")
        jobs = [job for job in self.store.jobs.values() if job.workflow_id == workflow_id]
        packet = {
            "packet_version": "v1",
            "workflow": _jsonable(asdict(workflow)),
            "jobs": [self._job_packet(job) for job in jobs],
            "recent_events": [
                _jsonable(asdict(event))
                for event in self.store.events
                if event.workflow_id == workflow_id
            ][-20:],
        }
        self.store.packet_reads.append({"party_id": party_id, "workflow_id": workflow_id})
        return packet

    def propose_renewal_email_jobs(self, *, party_id: str, workflow_id: str) -> tuple[str, str]:
        workflow = self._authorized_active_workflow(party_id, workflow_id)
        if any(job.workflow_id == workflow_id for job in self.store.jobs.values()):
            raise PrototypeError("The prototype accepts the initial graph only once")

        draft_id = self.store.ids.new("job_draft")
        send_id = self.store.ids.new("job_send")
        self.store.jobs[draft_id] = Job(
            id=draft_id,
            workflow_id=workflow.id,
            kind=DRAFT_KIND,
            status="queued",
            input={
                "recipient_name": "John Smith",
                "renewal_period": workflow.input["renewal_period"],
            },
            max_attempts=2,
        )
        self.store.jobs[send_id] = Job(
            id=send_id,
            workflow_id=workflow.id,
            kind=SEND_KIND,
            status="waiting",
            input={
                "sender": "broker@acme.example",
                "to": ["john@example.test"],
                "subject": {"job_output": draft_id, "field": "subject"},
                "body": {"job_output": draft_id, "field": "body"},
            },
            dependencies=[draft_id],
            max_attempts=1,
        )
        self._append_event(
            workflow_id=workflow.id,
            event_type="workflow_jobs_proposed",
            actor=f"party:{party_id}",
            cause="message:request_renewal_email",
            data={"job_ids": [draft_id, send_id]},
        )
        return draft_id, send_id

    def approve_job(
        self,
        *,
        party_id: str,
        job_id: str,
        expected_draft_revision_id: str,
        cause: str,
    ) -> str:
        job = self.store.jobs[job_id]
        self._authorized_active_workflow(party_id, job.workflow_id)
        if job.kind != SEND_KIND or job.status != "waiting":
            raise PrototypeError("Only the waiting Send Job can be approved")
        if job.dependencies != [expected_draft_revision_id]:
            raise PrototypeError("The presented Draft Revision is stale")
        effect = self._resolve_send_input(job)
        fingerprint = hashlib.sha256(_canonical_json(effect).encode()).hexdigest()[:16]
        event = self._append_event(
            workflow_id=job.workflow_id,
            event_type="approval_granted",
            actor=f"party:{party_id}",
            cause=cause,
            job_id=job.id,
            data={
                "draft_revision_id": expected_draft_revision_id,
                "effect_fingerprint": fingerprint,
            },
        )
        job.status = "queued"
        return event.id

    def claim_one(self, *, worker_id: str) -> dict[str, Any] | None:
        for job in self.store.jobs.values():
            if job.status != "queued":
                continue
            workflow = self.store.workflows[job.workflow_id]
            if workflow.status != "active" or job.attempts >= job.max_attempts:
                continue
            if any(self.store.jobs[dependency].status != "succeeded" for dependency in job.dependencies):
                continue
            if job.kind == SEND_KIND and self._approval_event(job.id) is None:
                continue
            job.attempts += 1
            job.status = "running"
            run = Run(
                id=self.store.ids.new("run"),
                workflow_id=job.workflow_id,
                job_id=job.id,
                status="running",
                worker_id=worker_id,
            )
            self.store.runs[run.id] = run
            self._append_event(
                workflow_id=job.workflow_id,
                event_type="run_started",
                actor=f"worker:{worker_id}",
                cause=f"job:{job.id}",
                job_id=job.id,
                run_id=run.id,
            )
            return {
                "run_id": run.id,
                "job_id": job.id,
                "job_kind": job.kind,
                "input": self._execution_input(job),
            }
        return None

    def record_runtime_instance(self, *, run_id: str, runtime_instance_id: str) -> None:
        run = self.store.runs[run_id]
        if run.status != "running":
            raise PrototypeError("Runtime provenance belongs to a running Run")
        run.runtime_instance_id = runtime_instance_id

    def start_dispatch(self, *, run_id: str) -> None:
        run, job = self._current_run_and_job(run_id)
        if job.kind != SEND_KIND:
            raise PrototypeError("Only the Send Job crosses the dispatch boundary")
        approval = self._approval_event(job.id)
        if approval is None:
            raise PrototypeError("Dispatch requires exact approval")
        if any(
            event.event_type == "external_effect_dispatch_started" and event.job_id == job.id
            for event in self.store.events
        ):
            raise PrototypeError("The logical External Effect was already dispatched")
        self._append_event(
            workflow_id=job.workflow_id,
            event_type="external_effect_dispatch_started",
            actor=f"run:{run.id}",
            cause=f"approval:{approval.id}",
            job_id=job.id,
            run_id=run.id,
            data={"approval_grant_id": approval.id},
        )

    def report_run_result(self, *, run_id: str, result: RunResult) -> None:
        run, job = self._current_run_and_job(run_id)
        if result.outcome != "succeeded" or result.data is None:
            raise PrototypeError("The happy-path prototype accepts successful results only")
        run.result = _jsonable(asdict(result))
        run.status = "succeeded"
        job.output = result.data
        job.status = "succeeded"

        event_type = "draft_ready" if job.kind == DRAFT_KIND else "email_send_succeeded"
        event = self._append_event(
            workflow_id=job.workflow_id,
            event_type=event_type,
            actor=f"run:{run.id}",
            cause=f"job:{job.id}",
            job_id=job.id,
            run_id=run.id,
            data={"outcome": result.outcome},
        )
        notification_kind = "approval_required" if job.kind == DRAFT_KIND else "send_confirmed"
        self._append_notification(event, notification_kind)

        if job.kind == SEND_KIND:
            workflow = self.store.workflows[job.workflow_id]
            workflow.status = "completed"
            self._append_event(
                workflow_id=workflow.id,
                event_type="workflow_completed",
                actor="system:control_plane",
                cause=f"event:{event.id}",
            )

    def claim_notification(self) -> Notification | None:
        for notification in self.store.notifications.values():
            if notification.status == "queued":
                notification.status = "delivering"
                return notification
        return None

    def mark_notification_delivered(self, notification_id: str) -> None:
        notification = self.store.notifications[notification_id]
        if notification.status != "delivering":
            raise PrototypeError("Only a claimed Notification can be delivered")
        notification.status = "delivered"

    def _job_packet(self, job: Job) -> dict[str, Any]:
        packet = _jsonable(asdict(job))
        if job.kind == SEND_KIND and all(
            self.store.jobs[dependency].status == "succeeded" for dependency in job.dependencies
        ):
            packet["resolved_input"] = self._resolve_send_input(job)
        packet["waiting_reasons"] = self._waiting_reasons(job)
        return packet

    def _execution_input(self, job: Job) -> dict[str, Any]:
        return self._resolve_send_input(job) if job.kind == SEND_KIND else dict(job.input)

    def _resolve_send_input(self, job: Job) -> dict[str, Any]:
        resolved = dict(job.input)
        for field_name in ("subject", "body"):
            reference = job.input[field_name]
            source = self.store.jobs[reference["job_output"]]
            if source.output is None:
                raise PrototypeError("The Draft Revision has not been published")
            resolved[field_name] = source.output[reference["field"]]
        return resolved

    def _waiting_reasons(self, job: Job) -> list[str]:
        reasons = [
            f"dependency:{dependency}"
            for dependency in job.dependencies
            if self.store.jobs[dependency].status != "succeeded"
        ]
        if job.kind == SEND_KIND and not reasons and self._approval_event(job.id) is None:
            reasons.append("exact_approval")
        return reasons

    def _authorized_active_workflow(self, party_id: str, workflow_id: str) -> Workflow:
        workflow = self.store.workflows.get(workflow_id)
        if workflow is None or party_id not in workflow.authorized_party_ids:
            raise PrototypeError("not_found")
        if workflow.status != "active":
            raise PrototypeError("Workflow is terminal")
        return workflow

    def _current_run_and_job(self, run_id: str) -> tuple[Run, Job]:
        run = self.store.runs[run_id]
        job = self.store.jobs[run.job_id]
        if run.status != "running" or job.status != "running":
            raise PrototypeError("The Run no longer has Execution Authority")
        return run, job

    def _approval_event(self, job_id: str) -> WorkflowEvent | None:
        return next(
            (
                event
                for event in self.store.events
                if event.event_type == "approval_granted" and event.job_id == job_id
            ),
            None,
        )

    def _append_event(
        self,
        *,
        workflow_id: str,
        event_type: str,
        actor: str,
        cause: str,
        job_id: str | None = None,
        run_id: str | None = None,
        data: dict[str, Any] | None = None,
    ) -> WorkflowEvent:
        event = WorkflowEvent(
            id=self.store.ids.new("event"),
            workflow_id=workflow_id,
            event_type=event_type,
            actor=actor,
            cause=cause,
            job_id=job_id,
            run_id=run_id,
            data=data or {},
        )
        self.store.events.append(event)
        return event

    def _append_notification(self, event: WorkflowEvent, kind: str) -> None:
        notification = Notification(
            id=self.store.ids.new("notification"),
            workflow_id=event.workflow_id,
            workflow_event_id=event.id,
            kind=kind,
        )
        self.store.notifications[notification.id] = notification


class WorkflowTools:
    """Expose the small typed surface visible to an Interaction Agent."""

    def __init__(self, control_plane: ControlPlane, party_id: str) -> None:
        self.control_plane = control_plane
        self.party_id = party_id

    def search_workflows(self, query: str) -> list[dict[str, Any]]:
        return self.control_plane.search_workflows(party_id=self.party_id, query=query)

    def read_workflow_packet(self, workflow_id: str) -> dict[str, Any]:
        return self.control_plane.read_workflow_packet(
            party_id=self.party_id,
            workflow_id=workflow_id,
        )

    def propose_renewal_email_jobs(self, workflow_id: str) -> tuple[str, str]:
        return self.control_plane.propose_renewal_email_jobs(
            party_id=self.party_id,
            workflow_id=workflow_id,
        )

    def approve_job(self, job_id: str, draft_revision_id: str, cause: str) -> str:
        return self.control_plane.approve_job(
            party_id=self.party_id,
            job_id=job_id,
            expected_draft_revision_id=draft_revision_id,
            cause=cause,
        )


class FreshInteractionAgent:
    """Represent one disposable Interaction Agent turn."""

    def __init__(self, store: PrototypeStore, tools: WorkflowTools) -> None:
        self.store = store
        self.tools = tools
        self.runtime_id = store.ids.new("interaction_runtime")

    def __enter__(self) -> FreshInteractionAgent:
        self.store.live_interaction_runtimes.add(self.runtime_id)
        self.store.interaction_runtime_history.append(self.runtime_id)
        return self

    def __exit__(self, *_: object) -> None:
        self.store.live_interaction_runtimes.remove(self.runtime_id)

    def plan_renewal_email(self) -> tuple[str, str, str]:
        matches = self.tools.search_workflows("John Smith Acme 2026")
        if len(matches) != 1:
            raise PrototypeError("The Interaction Agent must resolve one Workflow")
        workflow_id = matches[0]["workflow_id"]
        self.tools.read_workflow_packet(workflow_id)
        draft_id, send_id = self.tools.propose_renewal_email_jobs(workflow_id)
        return workflow_id, draft_id, send_id

    def approve_exact_send(self, send_job_id: str, draft_job_id: str) -> str:
        self.tools.read_workflow_packet(self.store.jobs[send_job_id].workflow_id)
        return self.tools.approve_job(
            send_job_id,
            draft_job_id,
            cause="message:yes_send_it",
        )

    def handle_notification(self, notification: Notification) -> None:
        packet = self.tools.read_workflow_packet(notification.workflow_id)
        if notification.kind == "approval_required":
            send_job = next(job for job in packet["jobs"] if job["kind"] == SEND_KIND)
            effect = send_job["resolved_input"]
            message = f"Draft ready for approval: {effect['subject']}"
        else:
            message = "The approved renewal email was sent."
        self.store.user_messages.append(message)


class DraftExecutionAgent:
    """Create a fresh reasoning runtime for exactly one Draft Run."""

    def __init__(self, store: PrototypeStore) -> None:
        self.store = store
        self.runtime_id = store.ids.new("execution_runtime")

    def __enter__(self) -> DraftExecutionAgent:
        self.store.live_execution_runtimes.add(self.runtime_id)
        self.store.execution_runtime_history.append(self.runtime_id)
        return self

    def __exit__(self, *_: object) -> None:
        self.store.live_execution_runtimes.remove(self.runtime_id)

    def execute(self, execution_input: dict[str, Any]) -> RunResult:
        name = execution_input["recipient_name"]
        period = execution_input["renewal_period"]
        return RunResult(
            outcome="succeeded",
            data={
                "subject": f"Your {period} policy renewal",
                "body": f"Hello {name},\n\nLet's review your {period} renewal options.",
            },
            evidence=[{"type": "agent_output_validated"}],
        )


class DeterministicComposioFake:
    """Record the exact provider request and reject duplicate dispatch."""

    def __init__(self, store: PrototypeStore) -> None:
        self.store = store

    def execute(self, execution_input: dict[str, Any]) -> RunResult:
        job_id = execution_input["job_id"]
        if any(call["job_id"] == job_id for call in self.store.provider_calls):
            raise PrototypeError("A Job may dispatch its External Effect only once")
        effect = execution_input["effect"]
        self.store.provider_calls.append({"job_id": job_id, "effect": effect})
        return RunResult(
            outcome="succeeded",
            data={"provider": "composio", "accepted": True},
            evidence=[{"type": "provider_acknowledged", "successful": True}],
        )


class Worker:
    """Claim one Job, execute one Run, then discard Run-scoped context."""

    def __init__(self, store: PrototypeStore, control_plane: ControlPlane) -> None:
        self.store = store
        self.control_plane = control_plane
        self.worker_id = "worker_01"
        adapter = DeterministicComposioFake(store)
        self.registry = {
            DRAFT_KIND: JobKindContract(executor="fresh_execution_agent", execute=self._draft),
            SEND_KIND: JobKindContract(executor="deterministic_composio", execute=adapter.execute),
        }

    def tick(self) -> str | None:
        packet = self.control_plane.claim_one(worker_id=self.worker_id)
        if packet is None:
            return None
        run_id = packet["run_id"]
        contract = self.registry[packet["job_kind"]]
        if contract.executor == "fresh_execution_agent":
            result = contract.execute({"run_id": run_id, "effect": packet["input"]})
        else:
            self.control_plane.start_dispatch(run_id=run_id)
            result = contract.execute({"job_id": packet["job_id"], "effect": packet["input"]})
        self.control_plane.report_run_result(run_id=run_id, result=result)
        return packet["job_id"]

    def _draft(self, context: dict[str, Any]) -> RunResult:
        with DraftExecutionAgent(self.store) as agent:
            self.control_plane.record_runtime_instance(
                run_id=context["run_id"],
                runtime_instance_id=agent.runtime_id,
            )
            return agent.execute(context["effect"])


class NotificationWorker:
    """Deliver one Notification through a fresh Interaction Agent turn."""

    def __init__(
        self,
        store: PrototypeStore,
        control_plane: ControlPlane,
        tools: WorkflowTools,
    ) -> None:
        self.store = store
        self.control_plane = control_plane
        self.tools = tools

    def tick(self) -> str | None:
        notification = self.control_plane.claim_notification()
        if notification is None:
            return None
        with FreshInteractionAgent(self.store, self.tools) as agent:
            agent.handle_notification(notification)
        self.control_plane.mark_notification_delivered(notification.id)
        return notification.id


def seed_store() -> tuple[PrototypeStore, str]:
    store = PrototypeStore()
    broker_id = "party_broker"
    fixtures = [
        Workflow(
            id="wf_target_2026",
            kind="renewal_outreach.v1",
            objective="2026 renewal outreach for John Smith",
            status="active",
            input={"renewal_period": "2026"},
            organization="Acme Brokerage",
            participants=["John Smith"],
            authorized_party_ids={broker_id},
        ),
        Workflow(
            id="wf_history_2025",
            kind="renewal_outreach.v1",
            objective="2025 renewal outreach for John Smith",
            status="completed",
            input={"renewal_period": "2025"},
            organization="Acme Brokerage",
            participants=["John Smith"],
            authorized_party_ids={broker_id},
        ),
        Workflow(
            id="wf_hidden_2026",
            kind="renewal_outreach.v1",
            objective="2026 renewal outreach for John Smith",
            status="active",
            input={"renewal_period": "2026"},
            organization="Northwind Brokerage",
            participants=["John Smith"],
            authorized_party_ids={"party_other_broker"},
        ),
    ]
    store.workflows.update({workflow.id: workflow for workflow in fixtures})
    return store, broker_id


def run_demo(*, interactive: bool) -> None:
    store, broker_id = seed_store()
    control_plane = ControlPlane(store)
    tools = WorkflowTools(control_plane, broker_id)
    worker = Worker(store, control_plane)
    notification_worker = NotificationWorker(store, control_plane, tools)

    workflow_id = "wf_target_2026"
    draft_id = ""
    send_id = ""

    def step(title: str, action: Callable[[], None]) -> None:
        print(f"\n=== {title} ===")
        action()
        print(json.dumps(store.target_snapshot(workflow_id), indent=2, sort_keys=True))
        if interactive:
            input("\nPress Enter for the next action...")

    def propose() -> None:
        nonlocal workflow_id, draft_id, send_id
        with FreshInteractionAgent(store, tools) as agent:
            workflow_id, draft_id, send_id = agent.plan_renewal_email()

    def approve() -> None:
        with FreshInteractionAgent(store, tools) as agent:
            agent.approve_exact_send(send_id, draft_id)

    step("Fresh Interaction Agent searches, reads one Packet, and proposes both Jobs", propose)
    step("Worker claims only the Draft Job and destroys fresh Execution Agent context", worker.tick)
    step("Notification wakes a fresh Interaction Agent, which reloads the Packet", notification_worker.tick)
    step("A new Interaction Agent turn submits exact Job approval", approve)
    step("Worker claims only the Send Job, commits dispatch, and calls the adapter once", worker.tick)
    step("Completion Notification wakes another fresh Interaction Agent", notification_worker.tick)

    if store.live_interaction_runtimes or store.live_execution_runtimes:
        raise PrototypeError("No agent runtime may outlive its turn or Run")
    print("\nVERDICT: Workflow state survived every disposable agent runtime.")


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _jsonable(value: Any) -> Any:
    if isinstance(value, set):
        return sorted(value)
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    return value


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--auto",
        action="store_true",
        help="Run without pausing between state transitions.",
    )
    args = parser.parse_args()
    run_demo(interactive=not args.auto)


if __name__ == "__main__":
    main()
