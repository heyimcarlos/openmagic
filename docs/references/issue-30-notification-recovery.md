# Issue 30 reference brief

## Concern

OpenMagic needs durable, restart-safe Notification delivery without turning
Workflow Events into a mutable queue or rebuilding a general workflow engine.

## Comparables inspected

- Cloudflare Agents persists stable Notification identity, retries delivery,
  reconstructs after startup, and tests duplicate correlated delivery.
- Prefect exercises scheduling and lease behavior under controlled clocks.
- Deep Agents separates evaluation groups and aggregates their outcomes behind
  one runnable command.
- Executor keeps each evaluation run in an isolated result directory and tests
  restart against the real service boundary.

The inspected reference revisions were current on 2026-07-13. Restate had
unrelated local changes, so it was preserved and excluded from this ticket's
reference conclusions.

## Convergence and OpenMagic choice

Stable Notification identity plus an idempotent correlated reply boundary
closes the visible-reply and lost-acknowledgement crash window. Persisted
attempts, `available_at`, and leases remain owned by the Notification record.
The clock is injected only into the Notification protocol so the production
boundary stays small and deterministic tests control time without sleeping.

Restart tests reconstruct the Control Plane, Worker, retrieval service, and
fresh Interaction runtime from PostgreSQL and identifiers. They do not retain
prompt history or inherited batch state. The V0 report is a bounded index over
focused lanes, not a generalized evaluation platform. Deterministic gates own
the V0 verdict, while real-model diagnostics and live Composio evidence remain
separately labeled.

## Rejected alternatives

- A generic queue or scheduler abstraction would duplicate existing protocol
  ownership without improving this tracer.
- In-memory reply deduplication would fail across restart.
- Sleeping in tests would make availability and lease behavior slow and flaky.
- Treating model or live-provider results as deterministic safety proof would
  blur distinct evidence classes.
