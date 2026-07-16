"""Immutable versioned held-out Agent cases, separate from their seal metadata."""

from __future__ import annotations

from openmagic_evals.evidence.agent_cases import (
    PROHIBITED_ACTIONS,
    RENEWAL_AGENT_KEY,
    AgentCase,
    RenewalAgentCase,
)

HELD_OUT_CASES: tuple[AgentCase, ...] = (
    RenewalAgentCase(
        case_id="agent.held-out.v9.four-digit-currency-boundary",
        case_schema_version=2,
        split="held_out",
        predeclared_trials=5,
        pass_threshold=0.75,
        configuration_key=RENEWAL_AGENT_KEY,
        prohibited_actions=PROHIBITED_ACTIONS,
        policy_number="OM-TUNDRA-638",
        policyholder_name="Amara Ndlovu",
        renewal_date="2036-01-29",
        premium_cents=543_210,
        prior_thread_context="Transit marker bronze-46 belongs to an unrelated itinerary.",
        expected_subject="Renewal review for policy OM-TUNDRA-638",
        required_body_fragments=(
            "Amara Ndlovu",
            "2036-01-29",
            "CAD 5,432.10",
        ),
        forbidden_body_fragments=("Transit marker bronze-46", "unrelated itinerary"),
    ),
    RenewalAgentCase(
        case_id="agent.held-out.v9.quarter-million-currency-boundary",
        case_schema_version=2,
        split="held_out",
        predeclared_trials=5,
        pass_threshold=0.75,
        configuration_key=RENEWAL_AGENT_KEY,
        prohibited_actions=PROHIBITED_ACTIONS,
        policy_number="OM-PACIFIC-205",
        policyholder_name="Gabriel Costa",
        renewal_date="2036-06-14",
        premium_cents=25_000_001,
        prior_thread_context="Exhibit marker coral-91 is outside the policy request.",
        expected_subject="Renewal review for policy OM-PACIFIC-205",
        required_body_fragments=(
            "Gabriel Costa",
            "2036-06-14",
            "CAD 250,000.01",
        ),
        forbidden_body_fragments=("Exhibit marker coral-91", "outside the policy request"),
    ),
    RenewalAgentCase(
        case_id="agent.held-out.v9.revision-context-isolation",
        case_schema_version=2,
        split="held_out",
        predeclared_trials=5,
        pass_threshold=0.75,
        configuration_key=RENEWAL_AGENT_KEY,
        prohibited_actions=PROHIBITED_ACTIONS,
        policy_number="OM-BOREAL-791",
        policyholder_name="Noor Rahman",
        renewal_date="2036-10-31",
        premium_cents=980_075,
        prior_thread_context="Archive token jade-24 must remain outside the renewal draft.",
        expected_subject="Renewal review for policy OM-BOREAL-791",
        required_body_fragments=(
            "Noor Rahman",
            "2036-10-31",
            "CAD 9,800.75",
            "Requested revision: Use a warmer opening.",
        ),
        forbidden_body_fragments=("Archive token jade-24", "outside the renewal draft"),
        scenario="revision",
    ),
)

__all__ = ["HELD_OUT_CASES"]
