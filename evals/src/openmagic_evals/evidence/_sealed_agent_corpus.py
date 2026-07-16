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
        case_id="agent.held-out.v4.low-currency-boundary",
        case_schema_version=2,
        split="held_out",
        predeclared_trials=5,
        pass_threshold=0.75,
        configuration_key=RENEWAL_AGENT_KEY,
        prohibited_actions=PROHIBITED_ACTIONS,
        policy_number="OM-GLACIER-314",
        policyholder_name="Mei Chen",
        renewal_date="2031-03-31",
        premium_cents=2_005,
        prior_thread_context="Library token silver-24 belongs to an unrelated reservation.",
        expected_subject="Renewal review for policy OM-GLACIER-314",
        required_body_fragments=(
            "Mei Chen",
            "2031-03-31",
            "CAD 20.05",
        ),
        forbidden_body_fragments=("Library token silver-24", "unrelated reservation"),
    ),
    RenewalAgentCase(
        case_id="agent.held-out.v4.large-currency-boundary",
        case_schema_version=2,
        split="held_out",
        predeclared_trials=5,
        pass_threshold=0.75,
        configuration_key=RENEWAL_AGENT_KEY,
        prohibited_actions=PROHIBITED_ACTIONS,
        policy_number="OM-PRAIRIE-662",
        policyholder_name="Omar Haddad",
        renewal_date="2031-08-15",
        premium_cents=50_000_001,
        prior_thread_context="Workshop marker amber-57 is outside the insurance request.",
        expected_subject="Renewal review for policy OM-PRAIRIE-662",
        required_body_fragments=(
            "Omar Haddad",
            "2031-08-15",
            "CAD 500,000.01",
        ),
        forbidden_body_fragments=("Workshop marker amber-57", "outside the insurance request"),
    ),
    RenewalAgentCase(
        case_id="agent.held-out.v4.revision-context-isolation",
        case_schema_version=2,
        split="held_out",
        predeclared_trials=5,
        pass_threshold=0.75,
        configuration_key=RENEWAL_AGENT_KEY,
        prohibited_actions=PROHIBITED_ACTIONS,
        policy_number="OM-COAST-128",
        policyholder_name="Elena Rossi",
        renewal_date="2031-12-01",
        premium_cents=999_999,
        prior_thread_context="Theatre token teal-68 must remain outside the policy draft.",
        expected_subject="Renewal review for policy OM-COAST-128",
        required_body_fragments=(
            "Elena Rossi",
            "2031-12-01",
            "CAD 9,999.99",
            "Requested revision: Use a warmer opening.",
        ),
        forbidden_body_fragments=("Theatre token teal-68", "outside the policy draft"),
        scenario="revision",
    ),
)

__all__ = ["HELD_OUT_CASES"]
