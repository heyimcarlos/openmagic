"""PROTOTYPE: pure state model for comparing three Workflow Definitions.

Question: can Agent, deterministic, and longer hybrid Workflows reuse the same
closed Definition, Step, Attempt, Wait, Signal, Domain Event, and Delivery
contracts without application vocabulary leaking into the kernel?
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from uuid import NAMESPACE_URL, uuid5


@dataclass(frozen=True)
class Template:
    key: str
    kind: str
    executor_key: str | None = None
    execution_mode: str | None = None
    external_effect: bool = False
    max_attempts: int = 3
    default_signal: str | None = None


@dataclass(frozen=True)
class Route:
    key: str
    source_key: str
    activation: str
    outputs: tuple[str, ...]


@dataclass(frozen=True)
class EventRule:
    source_key: str
    activation: str
    event_type: str
    content_mode: str | None = None
    content_key: str | None = None


@dataclass(frozen=True)
class Scenario:
    key: str
    title: str
    application: str
    definition_key: str
    definition_version: int
    templates: tuple[Template, ...]
    routes: tuple[Route, ...]
    events: tuple[EventRule, ...]
    revision_signals: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class Attempt:
    attempt_id: str
    attempt_number: int
    state: str
    outcome: str | None = None


@dataclass(frozen=True)
class Occurrence:
    occurrence_id: str
    template_key: str
    kind: str
    state: str
    attempts: tuple[Attempt, ...] = ()
    satisfying_signal_id: str | None = None
    dispatch_state: str | None = None


@dataclass(frozen=True)
class DomainEvent:
    event_id: str
    event_type: str
    cause: str


@dataclass(frozen=True)
class DeliveryAttempt:
    attempt_id: str
    attempt_number: int
    state: str
    agent_run_id: str | None = None


@dataclass(frozen=True)
class Delivery:
    delivery_id: str
    event_id: str
    thread_id: str
    content_mode: str
    content_key: str
    state: str
    attempts: tuple[DeliveryAttempt, ...] = ()
    message_id: str | None = None
    message_sequence: int | None = None


@dataclass(frozen=True)
class TraceEvent:
    sequence: int
    event_type: str
    source_id: str


@dataclass(frozen=True)
class RuntimeState:
    scenario_key: str
    instance_id: str
    thread_id: str
    instance_open: bool
    occurrences: tuple[Occurrence, ...]
    domain_events: tuple[DomainEvent, ...]
    deliveries: tuple[Delivery, ...]
    traces: tuple[TraceEvent, ...]


@dataclass(frozen=True)
class Transition:
    state: RuntimeState
    message: str


def _id(*parts: object) -> str:
    return str(uuid5(NAMESPACE_URL, ":".join(str(part) for part in parts)))


def _template(scenario: Scenario, key: str) -> Template:
    return next(template for template in scenario.templates if template.key == key)


def _route(scenario: Scenario, source_key: str, activation: str) -> Route | None:
    return next(
        (
            route
            for route in scenario.routes
            if route.source_key == source_key and route.activation == activation
        ),
        None,
    )


def _event_rule(scenario: Scenario, source_key: str, activation: str) -> EventRule | None:
    return next(
        (
            rule
            for rule in scenario.events
            if rule.source_key == source_key and rule.activation == activation
        ),
        None,
    )


def _active_occurrence_index(state: RuntimeState) -> int | None:
    for index in range(len(state.occurrences) - 1, -1, -1):
        if state.occurrences[index].state in {"pending", "unsatisfied"}:
            return index
    return None


def _append_trace(state: RuntimeState, event_type: str, source_id: str) -> RuntimeState:
    trace = TraceEvent(len(state.traces) + 1, event_type, source_id)
    return replace(state, traces=(*state.traces, trace))


def _replace_occurrence(
    state: RuntimeState,
    index: int,
    occurrence: Occurrence,
) -> RuntimeState:
    occurrences = list(state.occurrences)
    occurrences[index] = occurrence
    return replace(state, occurrences=tuple(occurrences))


def _materialize(
    state: RuntimeState,
    scenario: Scenario,
    route: Route,
    source_id: str,
) -> RuntimeState:
    occurrences = list(state.occurrences)
    for slot, template_key in enumerate(route.outputs):
        template = _template(scenario, template_key)
        occurrence_id = _id(state.instance_id, route.key, source_id, slot)
        occurrences.append(
            Occurrence(
                occurrence_id=occurrence_id,
                template_key=template.key,
                kind=template.kind,
                state="pending" if template.kind == "step" else "unsatisfied",
            )
        )
    state = replace(state, occurrences=tuple(occurrences))
    return _append_trace(state, "route_applied", source_id)


def _emit_application_fact(
    state: RuntimeState,
    scenario: Scenario,
    source_key: str,
    activation: str,
    cause: str,
) -> RuntimeState:
    rule = _event_rule(scenario, source_key, activation)
    if rule is None:
        return state
    event = DomainEvent(
        event_id=_id(state.instance_id, "domain-event", len(state.domain_events) + 1),
        event_type=rule.event_type,
        cause=cause,
    )
    state = replace(state, domain_events=(*state.domain_events, event))
    if rule.content_mode is None or rule.content_key is None:
        return state
    delivery = Delivery(
        delivery_id=_id(event.event_id, state.thread_id, "delivery"),
        event_id=event.event_id,
        thread_id=state.thread_id,
        content_mode=rule.content_mode,
        content_key=rule.content_key,
        state="queued",
    )
    return replace(state, deliveries=(*state.deliveries, delivery))


def _apply_activation(
    state: RuntimeState,
    scenario: Scenario,
    source_key: str,
    activation: str,
    source_id: str,
) -> RuntimeState:
    state = _emit_application_fact(state, scenario, source_key, activation, source_id)
    route = _route(scenario, source_key, activation)
    if route is not None:
        return _materialize(state, scenario, route, source_id)
    state = _emit_application_fact(
        state,
        scenario,
        "__completion__",
        "completed",
        source_id,
    )
    close_source_id = _id(source_id, "completion-close-command")
    state = replace(state, instance_open=False)
    return _append_trace(state, "instance_closed", close_source_id)


def start(scenario: Scenario) -> RuntimeState:
    instance_id = _id(scenario.definition_key, scenario.definition_version, "instance")
    state = RuntimeState(
        scenario_key=scenario.key,
        instance_id=instance_id,
        thread_id=_id(scenario.key, "thread"),
        instance_open=True,
        occurrences=(),
        domain_events=(),
        deliveries=(),
        traces=(),
    )
    route = _route(scenario, "__start__", "start")
    if route is None:
        raise ValueError(f"Scenario {scenario.key} has no start Route")
    return _materialize(state, scenario, route, _id(instance_id, "start-command"))


def advance(state: RuntimeState, scenario: Scenario) -> Transition:
    index = _active_occurrence_index(state)
    if index is None:
        return Transition(state, "The Instance is closed; no transition is available.")
    occurrence = state.occurrences[index]
    template = _template(scenario, occurrence.template_key)
    if occurrence.kind == "wait":
        if template.default_signal is None:
            return Transition(state, "This Wait has no configured happy-path Signal.")
        return accept_signal(state, scenario, template.default_signal)

    current_attempt = next(
        (attempt for attempt in reversed(occurrence.attempts) if attempt.state == "leased"),
        None,
    )
    if current_attempt is None:
        attempt_number = len(occurrence.attempts) + 1
        if attempt_number > template.max_attempts:
            return Transition(state, "The finite Attempt budget is exhausted.")
        attempt = Attempt(
            attempt_id=_id(occurrence.occurrence_id, "attempt", attempt_number),
            attempt_number=attempt_number,
            state="leased",
        )
        occurrence = replace(occurrence, attempts=(*occurrence.attempts, attempt))
        state = _replace_occurrence(state, index, occurrence)
        state = _append_trace(state, "attempt_leased", attempt.attempt_id)
        return Transition(
            state,
            f"Leased Attempt {attempt_number} for {occurrence.template_key}.",
        )

    attempts = list(occurrence.attempts)
    attempts[-1] = replace(current_attempt, state="completed", outcome="succeeded")
    dispatch_state = "confirmed_applied" if template.external_effect else None
    occurrence = replace(
        occurrence,
        state="succeeded",
        attempts=tuple(attempts),
        dispatch_state=dispatch_state,
    )
    state = _replace_occurrence(state, index, occurrence)
    state = _append_trace(state, "attempt_result_accepted", current_attempt.attempt_id)
    state = _apply_activation(
        state,
        scenario,
        occurrence.template_key,
        "succeeded",
        occurrence.occurrence_id,
    )
    return Transition(state, f"Accepted the canonical result for {occurrence.template_key}.")


def accept_signal(
    state: RuntimeState,
    scenario: Scenario,
    signal_type: str,
) -> Transition:
    index = _active_occurrence_index(state)
    if index is None or state.occurrences[index].kind != "wait":
        return Transition(state, "No exact unsatisfied Wait is active.")
    occurrence = state.occurrences[index]
    route = _route(scenario, occurrence.template_key, signal_type)
    if route is None:
        return Transition(state, f"Signal {signal_type} is not declared for this Wait.")
    signal_id = _id(occurrence.occurrence_id, signal_type, "signal")
    occurrence = replace(
        occurrence,
        state="satisfied",
        satisfying_signal_id=signal_id,
    )
    state = _replace_occurrence(state, index, occurrence)
    state = _append_trace(state, "signal_accepted", signal_id)
    state = _apply_activation(
        state,
        scenario,
        occurrence.template_key,
        signal_type,
        signal_id,
    )
    return Transition(state, f"Accepted {signal_type}; the exact Wait is now satisfied.")


def request_revision(state: RuntimeState, scenario: Scenario) -> Transition:
    index = _active_occurrence_index(state)
    if index is None:
        return Transition(state, "No active Wait can accept a revision Signal.")
    occurrence = state.occurrences[index]
    signal = dict(scenario.revision_signals).get(occurrence.template_key)
    if signal is None:
        return Transition(state, "This Wait has no predefined revision Route.")
    return accept_signal(state, scenario, signal)


def lose_step_lease(state: RuntimeState, scenario: Scenario) -> Transition:
    index = _active_occurrence_index(state)
    if index is None:
        return Transition(state, "No active Step exists.")
    occurrence = state.occurrences[index]
    if occurrence.kind != "step" or not occurrence.attempts:
        return Transition(state, "Claim the Step before simulating lease loss.")
    current = occurrence.attempts[-1]
    if current.state != "leased":
        return Transition(state, "No current leased Attempt exists.")
    attempts = (*occurrence.attempts[:-1], replace(current, state="abandoned"))
    occurrence = replace(occurrence, attempts=attempts)
    state = _replace_occurrence(state, index, occurrence)
    state = _append_trace(state, "attempt_abandoned", current.attempt_id)
    return Transition(
        state,
        "The Attempt was abandoned. Its number remains consumed and the Step stays pending.",
    )


def make_effect_uncertain(state: RuntimeState, scenario: Scenario) -> Transition:
    index = _active_occurrence_index(state)
    if index is None:
        return Transition(state, "No active Step exists.")
    occurrence = state.occurrences[index]
    template = _template(scenario, occurrence.template_key)
    if not template.external_effect or not occurrence.attempts:
        return Transition(state, "The current Step is not a leased External Effect.")
    current = occurrence.attempts[-1]
    if current.state != "leased":
        return Transition(state, "The External Effect has no current leased Attempt.")
    attempts = (*occurrence.attempts[:-1], replace(current, state="completed", outcome="uncertain"))
    occurrence = replace(
        occurrence,
        attempts=attempts,
        dispatch_state="uncertain",
    )
    state = _replace_occurrence(state, index, occurrence)
    state = _append_trace(state, "attempt_result_accepted", current.attempt_id)
    return Transition(
        state,
        "Provider outcome is uncertain. Policy deferred the Step and automatic retry is blocked.",
    )


def confirm_uncertain_effect(state: RuntimeState, scenario: Scenario) -> Transition:
    index = _active_occurrence_index(state)
    if index is None:
        return Transition(state, "No pending uncertain effect exists.")
    occurrence = state.occurrences[index]
    if occurrence.dispatch_state != "uncertain":
        return Transition(state, "The current Step has no uncertain External Effect.")
    evidence_id = _id(occurrence.occurrence_id, "reconciliation", "applied")
    occurrence = replace(
        occurrence,
        state="succeeded",
        dispatch_state="confirmed_applied",
    )
    state = _replace_occurrence(state, index, occurrence)
    state = _append_trace(state, "deferred_step_resolved", evidence_id)
    state = _apply_activation(
        state,
        scenario,
        occurrence.template_key,
        "succeeded",
        evidence_id,
    )
    return Transition(state, "Reconciliation evidence confirmed the effect without replaying it.")


def advance_delivery(state: RuntimeState) -> Transition:
    index = next(
        (
            index
            for index, delivery in enumerate(state.deliveries)
            if delivery.state in {"queued", "delivering"}
        ),
        None,
    )
    if index is None:
        return Transition(state, "No Delivery is currently eligible.")
    delivery = state.deliveries[index]
    deliveries = list(state.deliveries)
    if delivery.state == "queued":
        attempt_number = len(delivery.attempts) + 1
        attempt_id = _id(delivery.delivery_id, "attempt", attempt_number)
        agent_run_id = _id(attempt_id, "agent-run") if delivery.content_mode == "agent" else None
        attempt = DeliveryAttempt(
            attempt_id=attempt_id,
            attempt_number=attempt_number,
            state="running",
            agent_run_id=agent_run_id,
        )
        deliveries[index] = replace(
            delivery,
            state="delivering",
            attempts=(*delivery.attempts, attempt),
        )
        return Transition(replace(state, deliveries=tuple(deliveries)), "Claimed one Delivery.")

    current = delivery.attempts[-1]
    delivered_count = sum(item.message_sequence is not None for item in state.deliveries)
    message_id = _id(delivery.delivery_id, "message")
    attempts = (*delivery.attempts[:-1], replace(current, state="succeeded"))
    deliveries[index] = replace(
        delivery,
        state="delivered",
        attempts=attempts,
        message_id=message_id,
        message_sequence=delivered_count + 1,
    )
    return Transition(
        replace(state, deliveries=tuple(deliveries)),
        "Message append and Delivery Acknowledgement committed atomically.",
    )


def lose_delivery_lease(state: RuntimeState) -> Transition:
    index = next(
        (
            index
            for index, delivery in enumerate(state.deliveries)
            if delivery.state == "delivering"
        ),
        None,
    )
    if index is None:
        return Transition(state, "No running Delivery Attempt exists.")
    delivery = state.deliveries[index]
    current = delivery.attempts[-1]
    attempts = (*delivery.attempts[:-1], replace(current, state="abandoned"))
    deliveries = list(state.deliveries)
    deliveries[index] = replace(delivery, state="queued", attempts=attempts)
    return Transition(
        replace(state, deliveries=tuple(deliveries)),
        "Delivery Attempt abandoned. The next claim receives a new Attempt identity.",
    )


def signal_race(state: RuntimeState, scenario: Scenario) -> Transition:
    index = _active_occurrence_index(state)
    if index is None or state.occurrences[index].kind != "wait":
        return Transition(state, "No active Wait exists for a Signal race.")
    template = _template(scenario, state.occurrences[index].template_key)
    if template.default_signal is None:
        return Transition(state, "This Wait has no happy-path Signal.")
    accepted = accept_signal(state, scenario, template.default_signal)
    return Transition(
        accepted.state,
        f"{template.default_signal} won. A competing Signal was rejected without mutation.",
    )


SCENARIOS = (
    Scenario(
        key="renewal",
        title="Agent-driven insurance renewal outreach",
        application="insurance",
        definition_key="renewal.outreach",
        definition_version=1,
        templates=(
            Template("gather_renewal_facts", "step", "renewal_facts.v1", "deterministic"),
            Template("draft_email", "step", "renewal_drafter.v1", "agent"),
            Template("approve_draft", "wait", default_signal="approved"),
            Template(
                "send_email",
                "step",
                "gmail_send.v1",
                "deterministic",
                external_effect=True,
                max_attempts=2,
            ),
        ),
        routes=(
            Route("start", "__start__", "start", ("gather_renewal_facts",)),
            Route("facts_ready", "gather_renewal_facts", "succeeded", ("draft_email",)),
            Route("draft_ready", "draft_email", "succeeded", ("approve_draft",)),
            Route("draft_approved", "approve_draft", "approved", ("send_email",)),
            Route("draft_revised", "approve_draft", "revision_requested", ("draft_email",)),
        ),
        events=(
            EventRule(
                "draft_email",
                "succeeded",
                "renewal.draft.ready",
                "template",
                "renewal_draft_ready.v1",
            ),
            EventRule("approve_draft", "approved", "renewal.draft.approved"),
            EventRule("approve_draft", "revision_requested", "renewal.revision.requested"),
            EventRule(
                "send_email", "succeeded", "renewal.email.sent", "template", "renewal_email_sent.v1"
            ),
            EventRule("__completion__", "completed", "renewal.completed"),
        ),
        revision_signals=(("approve_draft", "revision_requested"),),
    ),
    Scenario(
        key="refund",
        title="Deterministic high-value commerce refund",
        application="commerce",
        definition_key="commerce.high_value_refund",
        definition_version=1,
        templates=(
            Template("validate_request", "step", "refund_validator.v1", "deterministic"),
            Template("verify_account", "wait", default_signal="account_verified"),
            Template("calculate_refund", "step", "refund_calculator.v1", "deterministic"),
            Template(
                "issue_refund",
                "step",
                "payment_refund.v1",
                "deterministic",
                external_effect=True,
                max_attempts=2,
            ),
            Template("confirm_settlement", "step", "settlement_reader.v1", "deterministic"),
        ),
        routes=(
            Route("start", "__start__", "start", ("validate_request",)),
            Route("request_valid", "validate_request", "succeeded", ("verify_account",)),
            Route("account_verified", "verify_account", "account_verified", ("calculate_refund",)),
            Route("amount_ready", "calculate_refund", "succeeded", ("issue_refund",)),
            Route("refund_dispatched", "issue_refund", "succeeded", ("confirm_settlement",)),
        ),
        events=(
            EventRule(
                "validate_request",
                "succeeded",
                "refund.request.validated",
                "template",
                "refund_verification_required.v1",
            ),
            EventRule("verify_account", "account_verified", "refund.account.verified"),
            EventRule("calculate_refund", "succeeded", "refund.amount.calculated"),
            EventRule("issue_refund", "succeeded", "refund.dispatched"),
            EventRule(
                "confirm_settlement", "succeeded", "refund.settled", "template", "refund_settled.v1"
            ),
            EventRule("__completion__", "completed", "refund.completed"),
        ),
    ),
    Scenario(
        key="incident",
        title="Hybrid security incident investigation",
        application="security_operations",
        definition_key="security.incident_investigation",
        definition_version=1,
        templates=(
            Template("normalize_report", "step", "incident_normalizer.v1", "deterministic"),
            Template("analyze_incident", "step", "incident_analyst.v1", "agent"),
            Template("confirm_scope", "wait", default_signal="scope_confirmed"),
            Template("collect_evidence", "step", "evidence_collector.v1", "deterministic"),
            Template("confirm_findings", "wait", default_signal="findings_confirmed"),
            Template("draft_containment", "step", "containment_planner.v1", "agent"),
            Template("approve_containment", "wait", default_signal="containment_approved"),
            Template(
                "apply_containment",
                "step",
                "containment_adapter.v1",
                "deterministic",
                external_effect=True,
                max_attempts=2,
            ),
            Template("verify_recovery", "step", "recovery_checker.v1", "deterministic"),
            Template("confirm_closure", "wait", default_signal="closure_confirmed"),
            Template("archive_evidence", "step", "evidence_archiver.v1", "deterministic"),
        ),
        routes=(
            Route("start", "__start__", "start", ("normalize_report",)),
            Route("report_normalized", "normalize_report", "succeeded", ("analyze_incident",)),
            Route("analysis_ready", "analyze_incident", "succeeded", ("confirm_scope",)),
            Route("scope_confirmed", "confirm_scope", "scope_confirmed", ("collect_evidence",)),
            Route(
                "scope_revised", "confirm_scope", "scope_revision_requested", ("analyze_incident",)
            ),
            Route("evidence_ready", "collect_evidence", "succeeded", ("confirm_findings",)),
            Route(
                "findings_confirmed",
                "confirm_findings",
                "findings_confirmed",
                ("draft_containment",),
            ),
            Route(
                "more_evidence",
                "confirm_findings",
                "more_evidence_requested",
                ("collect_evidence",),
            ),
            Route("plan_ready", "draft_containment", "succeeded", ("approve_containment",)),
            Route(
                "plan_approved",
                "approve_containment",
                "containment_approved",
                ("apply_containment",),
            ),
            Route(
                "plan_revised",
                "approve_containment",
                "plan_revision_requested",
                ("draft_containment",),
            ),
            Route("containment_applied", "apply_containment", "succeeded", ("verify_recovery",)),
            Route("recovery_verified", "verify_recovery", "succeeded", ("confirm_closure",)),
            Route(
                "closure_confirmed", "confirm_closure", "closure_confirmed", ("archive_evidence",)
            ),
        ),
        events=(
            EventRule(
                "analyze_incident",
                "succeeded",
                "incident.analysis.ready",
                "template",
                "incident_scope_review.v1",
            ),
            EventRule("confirm_scope", "scope_confirmed", "incident.scope.confirmed"),
            EventRule(
                "confirm_scope", "scope_revision_requested", "incident.scope.revision_requested"
            ),
            EventRule(
                "collect_evidence",
                "succeeded",
                "incident.findings.ready",
                "agent",
                "incident_findings_explanation.v1",
            ),
            EventRule("confirm_findings", "findings_confirmed", "incident.findings.confirmed"),
            EventRule(
                "confirm_findings", "more_evidence_requested", "incident.more_evidence.requested"
            ),
            EventRule(
                "draft_containment",
                "succeeded",
                "incident.containment.plan_ready",
                "template",
                "incident_containment_review.v1",
            ),
            EventRule(
                "approve_containment", "containment_approved", "incident.containment.approved"
            ),
            EventRule(
                "approve_containment",
                "plan_revision_requested",
                "incident.containment.revision_requested",
            ),
            EventRule("apply_containment", "succeeded", "incident.containment.applied"),
            EventRule(
                "verify_recovery",
                "succeeded",
                "incident.recovery.verified",
                "template",
                "incident_closure_review.v1",
            ),
            EventRule("confirm_closure", "closure_confirmed", "incident.closure.confirmed"),
            EventRule(
                "__completion__", "completed", "incident.closed", "template", "incident_closed.v1"
            ),
        ),
        revision_signals=(
            ("confirm_scope", "scope_revision_requested"),
            ("confirm_findings", "more_evidence_requested"),
            ("approve_containment", "plan_revision_requested"),
        ),
    ),
)


def scenario_by_key(key: str) -> Scenario:
    return next(scenario for scenario in SCENARIOS if scenario.key == key)
