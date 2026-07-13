# Issue 29 recovery comparables

Recorded: 2026-07-13

## Decision frame

OpenMagic must prove that one authenticated Cause cannot create duplicate typed
work, that durable state survives process replacement, and that Worker loss
cannot duplicate an irreversible email send. PostgreSQL remains authoritative.
Prompt checkpoints and process-local replay caches are out of scope.

## Ranked comparables

| Rank | Source | Score | Best match | Mismatch | Use for |
| ---: | --- | ---: | --- | --- | --- |
| 1 | Executor | 31/35 | duplicate replay, restart journeys, isolated evidence | TypeScript, SQLite, larger E2E platform | stable replay and public restart proof |
| 2 | Deep Agents | 30/35 | Python resume tests, identity before persistence, delayed writes | LangGraph prompt checkpoints | fresh-boundary reconstruction and race tests |

Both references were pulled before review. Executor was at
`0a50c796c2cc334cf3e9bf6d4be33c77dbfac93b`; Deep Agents was at
`14f384fc0083c07a7f44f97543b40b74cf93c13f`.

## Convergence

- Executor joins concurrent duplicate deliveries and replays one settled result.
  Its restart scenario discards the first service boundary and continues through
  a fresh client against durable storage.
- Deep Agents assigns durable identity before asynchronous persistence and tests
  resume through a second application boundary. Delayed persistence tests expose
  races that normal in-memory tests miss.
- OpenMagic already has an immutable `workflow_jobs_proposed` Event and a durable
  authenticated Interaction Cause. The Event serves as the narrow command
  receipt by storing a canonical typed proposal digest. A new receipt table is
  unnecessary for either new Workflow creation or an existing Workflow's initial
  Job graph.
- Identical Cause, actor, Organization, and typed proposal replay the original
  stable graph receipt even after lifecycle state progresses. A content or typed
  proposal mismatch fails closed. Locking the Cause and Workflow rows plus a
  partial unique proposal-Cause index serialize concurrent delivery. Historical
  Events without the new digest remain readable but cannot be replayed as if they
  satisfied the new contract.
- Recovery proof uses fresh database, Control Plane, retrieval, toolbox, and
  Worker objects. It does not restore prompt history.
- Pre-dispatch loss permits one later Run within the persisted budget. A committed
  dispatch forces waiting and keeps the deterministic adapter invocation count at
  one.

## What not to copy

- Do not use Executor's bounded in-memory settled-result cache. It does not
  survive restart.
- Do not copy LangGraph checkpointing or resume full prompt state. OpenMagic
  reconstructs a bounded Workflow Packet.
- Do not build Executor's viewer, multi-target recorder, or generalized E2E
  platform.
- Do not treat rerunning as safe after dispatch. An email remains uncertain until
  authoritative evidence resolves it.

## Sources

- Executor `packages/core/execution/src/engine.ts`
- Executor `e2e/scenarios/restart-persistence.test.ts`
- Executor `e2e/src/scenario.ts`
- Deep Agents `libs/code/tests/integration_tests/test_compact_resume.py`
- Deep Agents `libs/deepagents/tests/unit_tests/test_messages_reducer.py`
- Deep Agents `libs/acp/tests/test_agent.py`
