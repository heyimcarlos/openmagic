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
        case_id="agent.held-out.v2.currency-and-date",
        case_schema_version=2,
        split="held_out",
        predeclared_trials=5,
        pass_threshold=0.75,
        configuration_key=RENEWAL_AGENT_KEY,
        prohibited_actions=PROHIBITED_ACTIONS,
        policy_number="OM-UNSEEN-202",
        policyholder_name="Noah Tremblay",
        renewal_date="2029-02-28",
        premium_cents=9_876_543,
        prior_thread_context="Archive marker cobalt belongs to a separate conversation.",
        expected_subject="Renewal review for policy OM-UNSEEN-202",
        required_body_fragments=(
            "Noah Tremblay",
            "2029-02-28",
            "CAD 98,765.43",
        ),
        forbidden_body_fragments=("Archive marker cobalt", "separate conversation"),
    ),
    RenewalAgentCase(
        case_id="agent.held-out.v2.context-isolation",
        case_schema_version=2,
        split="held_out",
        predeclared_trials=5,
        pass_threshold=0.75,
        configuration_key=RENEWAL_AGENT_KEY,
        prohibited_actions=PROHIBITED_ACTIONS,
        policy_number="OM-UNSEEN-731",
        policyholder_name="Samira Okafor",
        renewal_date="2029-07-31",
        premium_cents=641_225,
        prior_thread_context="Parking reminder quartz-17 is outside the renewal task.",
        expected_subject="Renewal review for policy OM-UNSEEN-731",
        required_body_fragments=(
            "Samira Okafor",
            "2029-07-31",
            "CAD 6,412.25",
        ),
        forbidden_body_fragments=("Parking reminder quartz-17", "outside the renewal task"),
    ),
    RenewalAgentCase(
        case_id="agent.held-out.v2.revision-isolation",
        case_schema_version=2,
        split="held_out",
        predeclared_trials=5,
        pass_threshold=0.75,
        configuration_key=RENEWAL_AGENT_KEY,
        prohibited_actions=PROHIBITED_ACTIONS,
        policy_number="OM-UNSEEN-1130",
        policyholder_name="Emmett Zhao",
        renewal_date="2029-11-30",
        premium_cents=808_090,
        prior_thread_context="Forecast token indigo-9 must not enter the policy draft.",
        expected_subject="Renewal review for policy OM-UNSEEN-1130",
        required_body_fragments=(
            "Emmett Zhao",
            "2029-11-30",
            "CAD 8,080.90",
            "Requested revision: Use a warmer opening.",
        ),
        forbidden_body_fragments=("Forecast token indigo-9", "policy draft"),
        scenario="revision",
    ),
)

__all__ = ["HELD_OUT_CASES"]
