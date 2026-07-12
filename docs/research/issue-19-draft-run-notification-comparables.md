# Issue 19: Draft Run and Notification comparables

## OpenMagic constraint

OpenMagic needs one PostgreSQL-backed Job claim, one disposable drafting runtime,
one write-once result commit, and one durable Notification delivery. Workflow
state must outlive every agent runtime. The implementation must not restore the
inherited named-agent roster or in-memory result reinjection path.

## Implementations inspected

### Prefect

Prefect selects due work with PostgreSQL row locks and `SKIP LOCKED`, then proves
through concurrent-session tests that two workers do not receive the same work.
Its cleanup queue separates reservation, lease validation, acknowledgement, and
redelivery.

Relevant local references:

- `.reference/prefect/src/prefect/server/database/sql/postgres/get-runs-from-worker-queues.sql.jinja`
- `.reference/prefect/src/prefect/server/database/query_components.py`
- `.reference/prefect/tests/server/database/test_queries.py`
- `.reference/prefect/src/prefect/server/worker_communication/cleanup_queue/memory.py`
- `.reference/prefect/src/prefect/testing/standard_test_suites/worker_cleanup_queue.py`

### Open SWE and Deep Agents

Open SWE creates fresh execution and preparation identifiers for scheduled
runs. Deep Agents centralizes construction of the model, tools, middleware, and
response contract in an application-owned factory. Both keep callers away from
executor selection.

Relevant local references:

- `.reference/open-swe/agent/dispatch.py`
- `.reference/open-swe/agent/middleware/prepare_run.py`
- `.reference/open-swe/tests/agent/test_agent_schedules.py`
- `.reference/deepagents/libs/deepagents/deepagents/graph.py`

### Executor

Executor rebuilds request-scoped resources for each invocation and tests that
concurrent requests never share those resources. Its delivery tests also avoid
treating a scheduled or closed writer as proof of delivery.

Relevant local references:

- `.reference/executor/packages/core/api/src/server/request-scoped.ts`
- `.reference/executor/apps/cloud/src/api.request-scope.node.test.ts`
- `.reference/executor/packages/hosts/cloudflare/src/mcp/agents-sse-max-age.test.ts`

## Convergence applied

- Candidate discovery is separate from the authoritative transition. OpenMagic
  locks the Workflow first, locks and revalidates the Job second, and retains
  the existing partial unique index as the final one-running-Run fence.
- The registry selects `fresh_execution_agent`. A Worker receives a bounded
  packet and cannot select a model, prompt, tool, handler, attempt budget, or
  lifecycle state.
- The drafting runtime receives one typed input and no transcript, history, or
  inherited broad tool registry. Its runtime identifier is fresh Run
  observability only.
- The provider or model call occurs outside PostgreSQL. The Control Plane then
  validates and commits one normalized Run Result.
- Draft output, `draft_ready`, and the `approval_required` Notification commit
  together. Notification delivery is leased separately and acknowledged only
  after a fresh Interaction runtime reloads the Workflow Packet and commits the
  exact presentation to the user-facing message boundary.

## Deliberate differences

OpenMagic does not import a generalized queue framework. Its Workflow row is
the aggregate serialization boundary, its Job and Run have distinct meanings,
and its existing PostgreSQL schema already contains the necessary queue state.
Notification leasing does not lock the Workflow because delivery state is not
authoritative Workflow domain state.
