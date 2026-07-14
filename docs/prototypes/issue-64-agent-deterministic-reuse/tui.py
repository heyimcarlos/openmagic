"""PROTOTYPE: interactive shell for the Workflow reuse state model."""

from __future__ import annotations

import json
import os
from dataclasses import asdict

from model import (
    SCENARIOS,
    RuntimeState,
    Transition,
    advance,
    advance_delivery,
    confirm_uncertain_effect,
    lose_delivery_lease,
    lose_step_lease,
    make_effect_uncertain,
    request_revision,
    signal_race,
    start,
)

BOLD = "\x1b[1m"
DIM = "\x1b[2m"
RESET = "\x1b[0m"


def render(state: RuntimeState, scenario_index: int, message: str) -> None:
    os.system("clear")
    scenario = SCENARIOS[scenario_index]
    print(f"{BOLD}Issue #64 reuse prototype{RESET}")
    print(f"{scenario.title}")
    print(
        f"{DIM}{scenario.definition_key} v{scenario.definition_version} | "
        f"Instance {'open' if state.instance_open else 'closed'}{RESET}\n"
    )
    print(f"{BOLD}Last action{RESET}: {message}\n")
    print(f"{BOLD}Durable state{RESET}")
    print(json.dumps(asdict(state), indent=2))
    print(f"\n{BOLD}Actions{RESET}")
    print("[n] claim/complete current Step or accept happy-path Signal")
    print("[v] request a predefined revision at the current Wait")
    print("[x] race two Signals against the current Wait")
    print("[l] abandon the current kernel Attempt lease")
    print("[u] make the current External Effect outcome uncertain")
    print("[e] reconcile an uncertain External Effect as applied")
    print("[d] claim/acknowledge the next Delivery")
    print("[k] abandon the current Delivery Attempt lease")
    print("[r] replay the last accepted identity without mutation")
    print("[1] renewal  [2] refund  [3] incident  [0] reset  [q] quit")


def main() -> None:
    scenario_index = 2
    state = start(SCENARIOS[scenario_index])
    message = "Started the incident scenario."
    while True:
        render(state, scenario_index, message)
        action = input("\n> ").strip().lower()
        if action == "q":
            return
        if action in {"1", "2", "3"}:
            scenario_index = int(action) - 1
            state = start(SCENARIOS[scenario_index])
            message = f"Selected {SCENARIOS[scenario_index].title}."
            continue
        if action == "0":
            state = start(SCENARIOS[scenario_index])
            message = "Reset the selected scenario."
            continue
        scenario = SCENARIOS[scenario_index]
        if action == "n":
            result = advance(state, scenario)
        elif action == "v":
            result = request_revision(state, scenario)
        elif action == "x":
            result = signal_race(state, scenario)
        elif action == "l":
            result = lose_step_lease(state, scenario)
        elif action == "u":
            result = make_effect_uncertain(state, scenario)
        elif action == "e":
            result = confirm_uncertain_effect(state, scenario)
        elif action == "d":
            result = advance_delivery(state)
        elif action == "k":
            result = lose_delivery_lease(state)
        elif action == "r":
            result = Transition(state, "Exact replay returned the same durable state.")
        else:
            message = "Unknown action."
            continue
        state = result.state
        message = result.message


if __name__ == "__main__":
    main()
