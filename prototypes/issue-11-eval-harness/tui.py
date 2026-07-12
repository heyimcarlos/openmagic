"""Throwaway terminal viewer for the proposed issue 11 evaluation lanes."""

from __future__ import annotations

from textwrap import fill

from model import Lane, scenarios_for

BOLD = "\x1b[1m"
DIM = "\x1b[2m"
RESET = "\x1b[0m"
LANES: tuple[Lane | None, ...] = (None, "paired journey", "protocol recovery", "live provider")


def render(selected_lane: int, selected_scenario: int) -> None:
    lane = LANES[selected_lane]
    scenarios = scenarios_for(lane)
    selected_scenario %= len(scenarios)
    scenario = scenarios[selected_scenario]
    print("\x1b[2J\x1b[H", end="")
    print(f"{BOLD}OpenMagic V0 evaluation prototype{RESET}")
    print(f"{DIM}Lane:{RESET} {lane or 'all'}")
    print(f"{DIM}Scenario:{RESET} {selected_scenario + 1}/{len(scenarios)}\n")
    print(f"{BOLD}{scenario.name}{RESET}")
    print(f"  systems:      {scenario.systems}")
    print(f"  verdict:      {scenario.verdict}")
    print(
        f"  perturbation: {fill(scenario.perturbation, width=72, subsequent_indent='                ')}"
    )
    print("  evidence:")
    for item in scenario.evidence:
        print(f"    - {item}")
    print(f"\n{DIM}Full lane inventory:{RESET}")
    for index, item in enumerate(scenarios):
        marker = ">" if index == selected_scenario else " "
        print(f" {marker} [{item.verdict[0]}] {item.name}")
    print(
        f"\n{BOLD}[n]{RESET} next  {BOLD}[p]{RESET} previous  {BOLD}[l]{RESET} lane  {BOLD}[q]{RESET} quit"
    )


def main() -> None:
    selected_lane = 0
    selected_scenario = 0
    while True:
        render(selected_lane, selected_scenario)
        command = input("> ").strip().lower()
        if command == "q":
            return
        if command == "n":
            selected_scenario += 1
        elif command == "p":
            selected_scenario -= 1
        elif command == "l":
            selected_lane = (selected_lane + 1) % len(LANES)
            selected_scenario = 0


if __name__ == "__main__":
    main()
