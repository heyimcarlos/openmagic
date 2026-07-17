"""Isolated Signal acceptance race scenario."""

from __future__ import annotations

from uuid import uuid4

from openmagic_runtime.kernel.control import (
    AcceptSignal,
    SignalReceipt,
    StartInstance,
    start_instance,
)
from openmagic_runtime.kernel.definitions import DefinitionCatalog

from openmagic_evals.evidence._race_operations import AcceptSignalRace
from openmagic_evals.evidence._race_transport import RaceFailureKind, RaceFailureReason
from openmagic_evals.evidence.contracts import (
    Correlations,
    InstanceDefinitionCorrelation,
    ProcessCorrelations,
    RuntimeCorrelations,
)
from openmagic_evals.evidence.inspection import EvidenceInspection
from openmagic_evals.evidence.race_definitions import SIGNAL_RACE_DEFINITION
from openmagic_evals.evidence.race_models import (
    RaceCorpus,
    RaceSeedResult,
    jitter_pair,
    race_observation,
)
from openmagic_evals.evidence.race_processes import run_process_contenders


def _signal_trial(
    database_url: str,
    inspection: EvidenceInspection,
    seed: int,
) -> RaceSeedResult:
    started = start_instance(
        database_url=database_url,
        request=StartInstance(
            command_id=uuid4(),
            definition_key=SIGNAL_RACE_DEFINITION.identity.key,
            definition_version=SIGNAL_RACE_DEFINITION.identity.version,
            instance_input={"value": f"signal-{seed}"},
            route_input={"value": f"signal-{seed}"},
        ),
    )
    wait_id = started.waits["decision"]
    requests = tuple(
        AcceptSignal(
            uuid4(),
            started.instance_id,
            wait_id,
            "eval.issue71.decision",
            1,
            {"value": f"signal-{seed}"},
            route,
        )
        for route in ("approve", "revise")
    )
    jitters = jitter_pair(seed, 400_000)
    contenders = run_process_contenders(
        database_url,
        case_id="race.wait-signal",
        seed=seed,
        jitter_microseconds=jitters,
        requests=(AcceptSignalRace(requests[0]), AcceptSignalRace(requests[1])),
    )
    outcomes = contenders.results
    public = tuple(
        "accepted"
        if item.failure is None
        else "conflict"
        if item.failure.kind is RaceFailureKind.SIGNAL_CONFLICT
        and item.failure.reason is RaceFailureReason.WAIT_ALREADY_SATISFIED
        else "unexpected_error"
        for item in outcomes
    )
    unexpected = tuple(
        (item.failure.kind.value, item.failure.reason.value, item.failure.message)
        for item in outcomes
        if item.failure is not None
        and not (
            item.failure.kind is RaceFailureKind.SIGNAL_CONFLICT
            and item.failure.reason is RaceFailureReason.WAIT_ALREADY_SATISFIED
        )
    )
    if unexpected:
        raise RuntimeError(f"Signal race contender failed unexpectedly: {unexpected}")
    winner = next(item.require_value() for item in outcomes if item.failure is None)
    if not isinstance(winner, SignalReceipt):
        raise AssertionError("Signal race did not return its typed winner receipt")
    constraint_rows = inspection.accepted_signals(wait_id)
    if public.count("accepted") != 1 or constraint_rows != 1:
        raise AssertionError(f"Signal constraint disagreed for seed {seed}")
    return RaceSeedResult(
        seed=seed,
        jitter_microseconds=jitters,
        public_outcomes=public,
        constraint_rows=constraint_rows,
        correlations=Correlations(
            runtime=RuntimeCorrelations(
                instance_ids=(started.instance_id,),
                instance_definitions=(
                    InstanceDefinitionCorrelation.from_identity(
                        started.instance_id,
                        SIGNAL_RACE_DEFINITION.identity,
                    ),
                ),
                step_ids=tuple(winner.steps.values()),
                wait_ids=(wait_id,),
                signal_ids=(winner.signal_id,),
                trace_event_ids=(started.trace_event_id, winner.trace_event_id),
            ),
            process=ProcessCorrelations(process_ids=contenders.process_ids),
        ),
        observation=race_observation(
            {
                "seed": seed,
                "jitter_microseconds": jitters,
                "public_outcomes": public,
                "constraint_rows": constraint_rows,
                "signal_id": str(winner.signal_id),
            }
        ),
        contender_process_ids=contenders.process_ids,
        overlap_barrier_observed=contenders.overlap_barrier_observed,
    )


def run_signal_races(
    database_url: str,
    *,
    seeds: tuple[int, ...] = tuple(range(100)),
) -> RaceCorpus:
    DefinitionCatalog(database_url=database_url).register(SIGNAL_RACE_DEFINITION)
    inspection = EvidenceInspection(database_url)
    return RaceCorpus(
        case_id="race.wait-signal",
        uses_overlap_barrier=True,
        varied_jitter=True,
        database_constraint="openmagic_runtime.signals(wait_id)",
        expected_public_outcomes=("accepted", "conflict"),
        results=tuple(_signal_trial(database_url, inspection, seed) for seed in seeds),
    )


__all__ = ["run_signal_races"]
