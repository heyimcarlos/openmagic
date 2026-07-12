"""Pure scenario catalog for the throwaway issue 11 harness prototype."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Lane = Literal["paired journey", "protocol recovery", "live provider"]
Verdict = Literal["gate", "diagnostic", "smoke"]


@dataclass(frozen=True)
class Scenario:
    name: str
    lane: Lane
    systems: str
    verdict: Verdict
    perturbation: str
    evidence: tuple[str, ...]


SCENARIOS = (
    Scenario(
        name="Resolve the intended renewal",
        lane="paired journey",
        systems="baseline + V0",
        verdict="gate",
        perturbation="near-duplicate, historical, wrong-Kind, and unauthorized Workflows",
        evidence=(
            "correct Workflow or clarification",
            "no mutation on ambiguity",
            "no authorization leakage",
        ),
    ),
    Scenario(
        name="Bound the agent context",
        lane="paired journey",
        systems="baseline + V0",
        verdict="diagnostic",
        perturbation="irrelevant roster and Workflow context",
        evidence=("packets loaded", "response bytes", "approximate tokens"),
    ),
    Scenario(
        name="Separate latency sources",
        lane="paired journey",
        systems="baseline + V0",
        verdict="diagnostic",
        perturbation="repeat the same synthetic journeys",
        evidence=("local control-plane time", "model time", "provider time"),
    ),
    Scenario(
        name="Fence concurrent Job claims",
        lane="protocol recovery",
        systems="V0 only",
        verdict="gate",
        perturbation="two Workers claim one eligible Job",
        evidence=("one Run", "one attempt increment", "one current authority"),
    ),
    Scenario(
        name="Recover before dispatch",
        lane="protocol recovery",
        systems="V0 only",
        verdict="gate",
        perturbation="Worker loss and lease expiry before dispatch",
        evidence=("abandoned Run", "requeued Job", "late command rejected"),
    ),
    Scenario(
        name="Stop after uncertain dispatch",
        lane="protocol recovery",
        systems="V0 only",
        verdict="gate",
        perturbation="Worker loss or uncertain result after dispatch",
        evidence=("waiting Job", "no automatic retry", "one provider invocation"),
    ),
    Scenario(
        name="Preserve exact approval across revision races",
        lane="protocol recovery",
        systems="V0 only",
        verdict="gate",
        perturbation="duplicate approval, replacement, cancellation, and dispatch races",
        evidence=("one immutable grant", "exact fingerprint", "serialized invalidation"),
    ),
    Scenario(
        name="Deliver notifications at least once safely",
        lane="protocol recovery",
        systems="V0 only",
        verdict="gate",
        perturbation="lost, duplicate, delayed, out-of-order, and stale delivery",
        evidence=("durable attempts", "stale claim rejected", "one correlated reply"),
    ),
    Scenario(
        name="Resume after process restart",
        lane="protocol recovery",
        systems="V0 only",
        verdict="gate",
        perturbation="restart while awaiting exact approval",
        evidence=("frozen draft retained", "fresh packet", "no prompt-history dependency"),
    ),
    Scenario(
        name="Prove the real Gmail success path",
        lane="live provider",
        systems="V0 only",
        verdict="smoke",
        perturbation="one uniquely correlated approved email",
        evidence=("Composio success", "durable trace", "exactly one AgentMail receipt"),
    ),
)


def scenarios_for(lane: Lane | None) -> tuple[Scenario, ...]:
    return tuple(item for item in SCENARIOS if lane is None or item.lane == lane)
