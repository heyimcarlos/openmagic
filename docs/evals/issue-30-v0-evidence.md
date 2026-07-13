# Issue 30 Notification recovery and V0 evidence

The Notification recovery suite exercises the real PostgreSQL Control Plane,
Notification Worker, Packet retrieval boundary, and fresh Interaction runtime.
It proves concurrent claim exclusion, idempotent acknowledgement, one correlated
reply across a lost acknowledgement, controlled scheduling, persisted retry
budget exhaustion, stale approval rejection, and restart from durable
identifiers.

Run every deterministic V0 gate and write one bounded evidence index:

```bash
uv run python -m server.evals.v0_evidence \
  --build "$(git rev-parse HEAD)" \
  --output /tmp/openmagic-v0-evidence
```

Add the real-model diagnostic and one live Composio Gmail smoke when credentials
are available:

```bash
set -a
source .env
set +a
uv run python -m server.evals.v0_evidence \
  --build "$(git rev-parse HEAD)" \
  --output /tmp/openmagic-v0-evidence \
  --run-model-diagnostics \
  --run-live-composio
```

Every run requires a clean Git worktree and verifies the supplied build against
the checked-out commit. It creates one exclusive directory containing
`report.json`, `report.md`, JUnit evidence for non-provider lanes, one bounded
live-smoke result, and the existing typed recovery or paired reports produced by those suites. The report
distinguishes four deterministic safety lanes, the
real-model diagnostic lane, and the live-provider lane. The strict V0 verdict
is derived only from deterministic gates. Optional external lanes remain
visible as `not_run`, `pass`, or `fail` and cannot rewrite deterministic safety
evidence.

The report records the exact build, shell-safe lane commands with their
non-secret environment switches, typed observation outcomes, evidence paths
and digests, and durations. It does not retain prompts, raw provider
responses, email content, mailbox addresses, or credentials. In the live lane,
Send Job completion, Notification delivery, user-visible acknowledgement, and
recipient observation are separate assertions and separate report
observations.
