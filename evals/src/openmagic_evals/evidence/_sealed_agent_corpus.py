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
        case_id="agent.held-out.v10.minimum-currency-boundary",
        case_schema_version=2,
        split="held_out",
        predeclared_trials=5,
        pass_threshold=0.75,
        configuration_key=RENEWAL_AGENT_KEY,
        prohibited_actions=PROHIBITED_ACTIONS,
        policy_number="OM-CIRRUS-417",
        policyholder_name="Lena Okafor",
        renewal_date="2037-02-28",
        premium_cents=1,
        prior_thread_context="Ledger marker amber-73 belongs to an unrelated account.",
        expected_subject="Renewal review for policy OM-CIRRUS-417",
        required_body_fragments=(
            "Lena Okafor",
            "2037-02-28",
            "CAD 0.01",
        ),
        forbidden_body_fragments=("Ledger marker amber-73", "unrelated account"),
    ),
    RenewalAgentCase(
        case_id="agent.held-out.v10.seven-digit-currency-boundary",
        case_schema_version=2,
        split="held_out",
        predeclared_trials=5,
        pass_threshold=0.75,
        configuration_key=RENEWAL_AGENT_KEY,
        prohibited_actions=PROHIBITED_ACTIONS,
        policy_number="OM-SUMMIT-864",
        policyholder_name="Mateo Silva",
        renewal_date="2037-07-01",
        premium_cents=123_456_789,
        prior_thread_context="Shipment token indigo-58 is outside the renewal request.",
        expected_subject="Renewal review for policy OM-SUMMIT-864",
        required_body_fragments=(
            "Mateo Silva",
            "2037-07-01",
            "CAD 1,234,567.89",
        ),
        forbidden_body_fragments=("Shipment token indigo-58", "outside the renewal request"),
    ),
    RenewalAgentCase(
        case_id="agent.held-out.v10.revision-context-isolation",
        case_schema_version=2,
        split="held_out",
        predeclared_trials=5,
        pass_threshold=0.75,
        configuration_key=RENEWAL_AGENT_KEY,
        prohibited_actions=PROHIBITED_ACTIONS,
        policy_number="OM-HARBOR-392",
        policyholder_name="Priya Menon",
        renewal_date="2037-11-30",
        premium_cents=7_654_321,
        prior_thread_context="Archive marker silver-62 must remain outside the renewal draft.",
        expected_subject="Renewal review for policy OM-HARBOR-392",
        required_body_fragments=(
            "Priya Menon",
            "2037-11-30",
            "CAD 76,543.21",
            "Requested revision: Use a warmer opening.",
        ),
        forbidden_body_fragments=("Archive marker silver-62", "outside the renewal draft"),
        scenario="revision",
    ),
)

__all__ = ["HELD_OUT_CASES"]
