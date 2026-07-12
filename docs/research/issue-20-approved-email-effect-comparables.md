# Issue 20 Approved Email Effect Comparables

## Decision Frame

- Target project: OpenMagic V0 renewal outreach tracer.
- Current and target stack: Python 3.13, FastAPI, PostgreSQL, SQLAlchemy,
  Pydantic, and Composio 0.17.1.
- Domain and scale: one interview-demo Workflow, one approved irreversible
  email effect, replaceable Workers, and durable PostgreSQL evidence.
- Hard constraints: exact immutable approval, one dispatch per Job, no provider
  retry, uncertain outcomes never resend automatically, and disposable agent
  context.
- Key questions: where approval ends and execution begins, how dispatch wins a
  race, how Composio is called exactly once, and how result replay remains safe.

## Ranked Comparables

| Rank | Source | Score | Best Match | Mismatch | Use For |
|---|---|---:|---|---|---|
| 1 | Composio Python | 33/35 | Exact Python provider SDK and irreversible tool execution | No OpenMagic business approval or Workflow lifecycle | Public execute path, pinned toolkit version, response envelope, no-retry transport |
| 2 | Executor | 29/35 | Approval then resume, identity rejection, duplicate delivery | TypeScript and a different execution domain | Approval separation, stale identity rejection, browser acceptance |
| 3 | Prefect | 28/35 | Mature Python transaction, lease, and first-writer tests | Much broader orchestration platform | Idempotent committed records, real concurrency fixtures, lease-loss behavior |

Scores cover domain fit, Python fit, maturity, architecture clarity,
operations relevance, test quality, and maintainability signal. Repository
snapshots were updated and inspected on 2026-07-12:

- Composio `a0f37a7f7728c922e044dfb35c33dad9aae7ae7c`
- Executor `0a50c796c2cc334cf3e9bf6d4be33c77dbfac93b`
- Prefect `0e7435055e18952aa8604dab78507b087a18defb`

## Repository Architecture Extracts

### Composio Python

- Retry-disabled client:
  `.reference/composio/python/composio/client/__init__.py:222`.
- High-level direct execute:
  `.reference/composio/python/composio/core/models/tools.py:562`.
- Generic response type:
  `.reference/composio/python/composio/core/models/tools.py:83`.
- Transport-level no-retry proof:
  `.reference/composio/python/tests/test_no_retry_writes.py:121`.
- Toolkit version behavior:
  `.reference/composio/python/tests/test_tool_execution.py:84` and
  `.reference/composio/python/composio/utils/toolkit_version.py`.
- Gmail irreversible-send metadata:
  `.reference/composio/docs/public/data/toolkits.json:259`.

The public `Composio.tools.execute` path routes non-idempotent writes through a
cached client whose maximum retries are zero. Its test forces a transient HTTP
failure and asserts one transport request, while reads retain normal retries.
An explicit toolkit version is required. The stable result surface is
`data`, `error`, and `successful`; the public path deliberately removes `log_id`
and session details.

OpenMagic therefore calls only the public path, pins SDK 0.17.1 and Gmail
toolkit `20260703_00`, treats only `successful=true` with an empty error as a
positive acknowledgement, and maps every ambiguous post-dispatch observation
to `uncertain`.

### Executor

- Browser approval and missing-identity rejection:
  `.reference/executor/e2e/local/mcp-browser-approve.test.ts`.
- Duplicate approval resume:
  `.reference/executor/e2e/cloud/mcp-client-sessions.test.ts:358`.
- Lower-level completed-result replay:
  `.reference/executor/packages/core/execution/src/tool-invoker.test.ts:1611`.

Executor records approval before resuming execution, tests the real browser
boundary, rejects absent identity, and recognizes repeated delivery of a
completed command. OpenMagic adopts that separation through `approve_job`,
`begin_external_effect_dispatch`, the adapter call, and write-once Run result
reporting. It does not copy Executor's broader session model.

### Prefect

- Serializable transaction behavior:
  `.reference/prefect/src/prefect/transactions.py:303`.
- Existing committed record recognition:
  `.reference/prefect/tests/test_transactions.py:716`.
- Competing-write first-winner test:
  `.reference/prefect/tests/test_transactions.py:781`.
- Lease renewal and loss:
  `.reference/prefect/tests/concurrency/test_leases.py`.

Prefect tests competing writes with real concurrency and treats a stable key as
the identity of committed work. OpenMagic keeps its much smaller Workflow-row
lock and PostgreSQL uniqueness constraints, but copies the first-writer and
lease-loss test shapes.

## Language and Standards Guidance

- [Python typing protocols](https://docs.python.org/3/library/typing.html#typing.Protocol),
  accessed 2026-07-12: the adapter is a narrow structural protocol so the live
  and deterministic implementations share one callable contract.
- [PostgreSQL row-level locks](https://www.postgresql.org/docs/current/explicit-locking.html#LOCKING-ROWS),
  accessed 2026-07-12: approval, invalidation, cancellation, dispatch, and
  completion serialize through the owning Workflow row.
- [Pydantic model immutability](https://docs.pydantic.dev/latest/concepts/models/#faux-immutability),
  accessed 2026-07-12: commands, effects, adapter contexts, and results reject
  extra fields and are frozen value objects. PostgreSQL remains the durable
  write-once authority.

## Recommended Shape

```text
server/workflows/
  approval_protocol.py
  external_effect_protocol.py
  email_effects.py
  email_adapter.py
  lifecycle_protocol.py
```

- `approval_protocol.py` owns exact presentation, Party authority, Cause,
  fingerprint, stale-command, and idempotency validation.
- `external_effect_protocol.py` commits dispatch after revalidating Run lease,
  grant usability, current authority, fingerprint, and dispatch uniqueness.
- `email_effects.py` is the single canonical resolution and fingerprint seam.
- `email_adapter.py` contains the shared protocol, stateful deterministic fake,
  and pinned Composio implementation.
- `lifecycle_protocol.py` owns cancellation and its commit race with dispatch.
- The existing Worker and result protocol remain the lifecycle backbone.

The Job keeps the previously persisted V1 sender address. Trusted effect
resolution maps it to the stable verified Party Identifier before presentation,
approval, and dispatch. This preserves executable unfinished V1 Jobs without
pretending an address alone is the durable mailbox identity.

## Options

| Option | Points | When To Choose | Risks | First Slice |
|---|---:|---|---|---|
| Narrow protocols plus existing Worker | 10/10 | V0 and current PostgreSQL aggregate | Requires careful transaction boundaries | Exact approval through deterministic send |
| Generic executor abstraction | 6/10 | Many unrelated provider kinds already exist | Premature weak abstraction | Registry-driven executor base class |
| Reuse inherited Gmail tools | 2/10 | Only for non-durable legacy behavior | Raw retries, mutable drafts, weak result interpretation | None |

## Final Recommendation

Use the narrow protocol split. Keep provider details outside Job input and the
Control Plane, resolve the complete effect once through one canonical module,
commit dispatch before the provider call, and let both adapters return the same
Run Result envelope. Defer generalized provider registries, Gmail triggers,
attachments, HTML, aliases, drafts, and reconciliation machinery.

This recommendation would change when multiple unrelated side-effecting Job
Kinds demonstrate a genuinely shared execution lifecycle beyond the current
email-specific adapter seam.
