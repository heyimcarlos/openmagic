# Minimum durable kernel invariants from DBOS and Cloudflare

Date: 2026-07-13

Issue: [#62](https://github.com/heyimcarlos/openmagic/issues/62)

## Decision frame

- **Target:** extract OpenMagic's proven Workflow runtime behavior into a small,
  application-owned durable kernel.
- **Stack:** Python, FastAPI, Pydantic, SQLAlchemy, PostgreSQL, replaceable
  Python and Agent Executors.
- **Domain:** long-running insurance operations with human authority and
  externally irreversible effects.
- **Hard boundary:** no cron, arbitrary durable loops, nested Workflows,
  runtime-generated graphs, multiple database abstraction, or general Python
  replay.
- **Question:** which ideas from DBOS and Cloudflare Workflows should become
  contracts, without adopting their general workflow-authoring models?

## Conclusion

Extract the existing OpenMagic protocols. Do not adopt DBOS, Cloudflare
Workflows, or LangGraph as the kernel.

The useful common core is small:

1. Stable durable identity makes command replay safe.
2. Each bounded work item has immutable input, isolated attempts, and one
   canonical output.
3. Claims are leased, and every attempt mutation is fenced by current execution
   authority.
4. Recovery reads durable state and creates a new attempt. It never trusts
   process memory.
5. A successful result is reused. A failed attempt is retried only after trusted
   business policy classifies it as safe.
6. Business policy can commit its authorization, effect fence, evidence, and
   retry decision atomically with a kernel transition. Those concepts do not
   become kernel vocabulary.

DBOS and Cloudflare both rely on replayable author code around durable steps.
OpenMagic should not. Its authoring surface should remain closed, typed, finite,
and versioned.

```text
Command
      |
      v
Business policy
  validate, authorize, compile finite work, classify retry,
  require exact approval, fence effects, preserve uncertainty,
  judge completion and reconciliation
      |
      v
Small durable kernel
  Definitions, Instances, Steps, Attempts, Signals, Waits,
  leases, retry timing, Trace Events, transactions
      |
      +----------------------+
      v                      v
Python Executor          Agent Executor
trusted adapter          optional LangGraph inside one Attempt
```

## Ranked comparables

Scores are 0 to 5 for domain fit (D), target stack fit (S), production maturity
(M), architecture clarity (A), operations relevance (O), testing quality (T),
and documentation signal (Q).

| Rank | Source | D/S/M/A/O/T/Q | Total | Best match | Important mismatch | Use for |
| --- | --- | --- | ---: | --- | --- | --- |
| 1 | DBOS Python | 3/5/5/4/5/5/5 | 32/35 | Stable workflow identity, recorded operation output, restart recovery | Replays general Python workflows and includes queues, messaging, schedules, child workflows, and database transaction integration | Recovery and idempotency contracts |
| 2 | Cloudflare Workflows | 3/2/5/4/5/3/5 | 27/35 | Durable step boundaries, persisted returns, restart-safe authoring rules | Hosted runtime, broad step API, loops, events, schedules, nested workflows, and DAG authoring | Work-item granularity and process-loss rules |
| 3 | LangGraph | 1/5/4/4/2/5/5 | 26/35 | Python agent checkpoints, pending writes, and interrupts | Graph thread and checkpoint state do not model business authority, leases, exact effects, or effect uncertainty | Optional implementation inside an Agent Executor |

LangGraph scores well as a Python agent runtime, but its domain and operations
fit for this kernel are weak. Popularity is not part of the score.

## Exact invariant map

| Invariant | Comparable evidence | OpenMagic proof today | Decision |
| --- | --- | --- | --- |
| Stable invocation identity | DBOS workflow IDs are globally unique and can act as idempotency keys | Job, Run, command Cause, approval, and dispatch identities are durable; duplicate result and approval commands replay consistently | **Adopt.** Every mutating command needs a stable idempotency or Cause identity. Never infer identity from a process or executor session. |
| Completed work is not repeated | DBOS records operation output by `(workflow_uuid, function_id)` and reuses it during recovery; Cloudflare persists `step.do` returns | A Job publishes one canonical output; a Run result is write-once and conflicting replay fails | **Adopt.** Reuse the canonical output, but do not copy positional function numbering. Use explicit durable IDs and versioned kinds. |
| Attempts are at least once | DBOS steps can be retried until they complete; Cloudflare states that a step may execute more than once | Each claim creates a fresh Run and consumes the persisted attempt budget | **Adopt with stronger wording.** Attempts may repeat. External effects are never claimed to be exactly once. |
| Recovery comes from durable state | DBOS finds pending workflows at startup; Cloudflare says in-memory state may disappear during hibernation or restart | Expired Runs become abandoned, stale Workers are fenced, and eligible Jobs can receive fresh Runs | **Already proven.** Extraction must preserve this behavior without replaying application Python. |
| Durable state crosses boundaries through results | Cloudflare requires state after a step to be built from persisted step returns, not mutated globals or event objects | Executors receive a bounded packet and return one typed Run Result; they do not commit lifecycle state | **Adopt.** Executor memory and checkpoints remain disposable. |
| Work is granular | Cloudflare recommends one self-contained unit per independently retryable external interaction | Each Job is one bounded obligation, and each side-effecting Job represents exactly one External Effect | **Already stronger.** Keep one logical external effect per Job. |
| Side effects need retry discipline | Cloudflare advises idempotent provider calls because a failure may occur after the provider commits | Dispatch commits before the provider call; transport loss becomes `uncertain`; uncertain work does not requeue | **Keep OpenMagic's stronger rule above the kernel.** Provider idempotency is evidence, not a substitute for policy-owned dispatch and uncertainty. The kernel supplies the atomic, fenced transition. |
| Versions remain interpretable | DBOS records application version and function identity; both products depend on stable author code across recovery | Workflow and Job Kinds are versioned contracts selected by the registry | **Adopt.** A referenced version remains available while unfinished work uses it. Do not persist arbitrary executable definitions. |
| Concurrency is enforced by the database | DBOS persists workflow and operation identity; PostgreSQL supports row locks and queue-oriented `SKIP LOCKED` | Row locks serialize aggregate transitions; a partial unique index permits only one running Run per Job | **Adopt.** Correctness lives in transactions and constraints, not a single Worker process. |

Concrete local proof includes:

- `server/workflows/execution_protocol.py`: transactional claims, lease recovery,
  stale-Run fencing, write-once Run results, persisted retry decisions.
- `server/workflows/models.py`: one running Run per Job and valid terminal Run
  shapes enforced by database constraints.
- `server/workflows/approval_protocol.py`: one exact presented effect, one
  durable approving Cause, and idempotent approval replay.
- `server/workflows/external_effect_protocol.py`: approval revalidation,
  fingerprint integrity, one dispatch marker, and dispatch before provider I/O.
- `server/tests/workflows/test_draft_run_notification.py`: concurrent claim,
  expired Run, recovery, stale Worker, and duplicate result cases.
- `server/tests/workflows/test_approved_email_effect.py`: exact approval,
  dispatch races, provider uncertainty, replay, and no unsafe retry.
- `server/tests/evals/test_workflow_recovery_evidence.py`: restart and Worker-loss
  evidence across the complete path.

## Authoring contract

The kernel should accept a finite transition plan, not user-authored control
flow.

1. A **Command** is a closed, typed request with a stable Command Type.
2. Trusted **business policy** validates its input and authority, then compiles
   one finite acyclic batch of known Job Kinds from a versioned Workflow
   Definition.
3. A later Command may append another finite batch, such as a
   revision or reconciliation. Executors may not append work.
4. Every Step Kind declares its input, output, Attempt result, executor key,
   maximum attempts, and retry timing. Business policy declares retry
   classification, effect behavior, approval requirements, and completion.
5. The kernel persists the plan and transitions it. It does not interpret
   Python, prompts, graph nodes, schedules, or objective text.
6. Executors receive one current Attempt packet and return one typed result. They
   cannot grant authority, choose retry safety, publish output, or complete a
   Workflow.

The current code already contains both seams. `ProposeWorkflowWorkCommand` and
`WorkflowKindRegistry.compile_work()` are the desired closed operation path.
The more general `WorkflowProposal.jobs` authoring surface should not become a
public kernel contract. Keep it only where trusted migration or tests require
it, then narrow callers toward Commands.

The extraction should make this vocabulary boundary explicit:

| Current domain/runtime term | Kernel term | Ownership after extraction |
| --- | --- | --- |
| Workflow Kind and compiled graph shape | Versioned Definition | Kernel stores and validates structure; business policy selects it |
| Workflow | Instance | Kernel lifecycle plus business completion policy |
| Workflow Job | Step | Kernel claim and dependency state; business meaning stays above |
| Workflow Job Run | Attempt | Kernel lease, fence, timing, and write-once result |
| Workflow Event | Trace Event or Domain Event | Kernel lifecycle facts are generic; Domain Event vocabulary stays above |
| Delivery prerequisite or due condition | Signal or Wait | Kernel correlation primitive; Delivery meaning is resolved in #59 |
| Approval Grant, External Effect, effect evidence | No kernel equivalent | Business policy records them atomically beside kernel transitions |

## Explicit exclusions

| Exclude from the kernel | Present in comparables | Reason |
| --- | --- | --- |
| General Python replay | DBOS workflow functions | Couples durable truth to call order, decorators, serialization, and deployed code shape. OpenMagic already has explicit Jobs and Runs. |
| Arbitrary durable loops | DBOS ordinary Python control flow; Cloudflare durable agent loops | Makes the kernel a programming language runtime and weakens bounded retry and completion reasoning. |
| Cron and general sleep | DBOS scheduler; Cloudflare schedules and `step.sleep` | Time belongs to the existing Trigger domain and typed due work, not execution authoring. |
| Nested Workflows and child graphs | DBOS child workflows; Cloudflare child instance creation | Obscures business ownership, cancellation, effect accounting, and completion evidence. A new business objective is an explicit linked Workflow. |
| Runtime-generated or caller-provided DAGs | Cloudflare Python DAGs; LangGraph graphs | Commands select application-owned definitions. One Command may compile only a finite validated batch. |
| Generic workflow messaging and waits | DBOS send/recv and events; Cloudflare `waitForEvent`; LangGraph interrupts | Thread-correlated Delivery and human decisions need domain records, correlation, authority, expiry, deduplication, and acknowledgement. Issue #59 defines that contract above the kernel. |
| Multiple database or transaction abstraction | DBOS system and application database integration | OpenMagic's authority, claims, effects, and Delivery Outbox must commit in one PostgreSQL transaction. Cross-database orchestration would weaken the seam. |
| Fork, rewind, and time travel | DBOS workflow fork; LangGraph checkpoint replay | Historical Attempts, currently Workflow Job Runs, are immutable evidence. Corrections and revisions create new linked work rather than alternate history. |
| Kernel-owned Domain Event vocabulary | All general runtimes expose runtime events | The kernel may record lifecycle facts, but `approval.requested`, `renewal.draft.ready`, and Thread-correlated Delivery meaning belong to application policy and issue #59. |

This exclusion list is a stop condition. Adding any item requires a new ADR that
explains why a Command plus finite work cannot express the need.

## LangGraph's limited role

LangGraph may implement an Agent Executor for one Attempt:

```text
Kernel claims Attempt
  -> Agent Executor starts or loads Attempt-scoped LangGraph checkpoint
  -> graph reasons and calls read-only or kernel-mediated tools
  -> Agent Executor returns typed Attempt result
  -> kernel validates current lease and commits or rejects it
```

Required fences:

- The LangGraph `thread_id` is derived from the Attempt ID, not the Instance ID.
- A checkpoint is executor-owned cache. It is not a Workflow, Job, approval,
  durable wait, or source of authority.
- Resume is allowed only while the same Attempt retains execution authority.
- A stale Attempt checkpoint is abandoned and cannot publish output or dispatch an
  effect.
- An irreversible tool call must cross business policy's committed effect fence
  through the Control Plane. The kernel supplies only the atomic current-Attempt
  transition. LangGraph replay alone is not effect safety.
- Interrupts do not implement business approval. They may surface a request,
  but the response must pass through the domain command and exact-approval
  protocol.

For short Attempts, no checkpointer is necessary. Add LangGraph only when an
Agent Executor has a measured need for multi-node reasoning or within-Attempt
recovery.

## Repository architecture extracts

### DBOS Python

- `dbos/_schemas/system_database.py` stores workflow status and operation output
  separately. Operation output has the primary key `(workflow_uuid,
  function_id)`.
- `dbos/_core.py` checks for recorded operation execution before invoking a
  child or step again.
- `dbos/_recovery.py` finds pending workflow identities and restarts execution
  from durable state.
- `dbos/_scheduler.py`, `dbos/_queue.py`, workflow messaging, forking, and child
  workflow support show the breadth OpenMagic should not copy.

Use the durable identity and recorded-output contracts. Avoid the decorator and
general replay architecture.

### Cloudflare Workflows

- `src/content/docs/workflows/build/rules-of-workflows.mdx` states that steps can
  retry, memory outside steps can disappear, incoming events are immutable, and
  side effects outside steps may repeat.
- `src/content/docs/workflows/build/events-and-parameters.mdx` documents buffered
  event waits.
- `src/content/docs/workflows/build/trigger-workflows.mdx` documents schedules
  and child Workflow creation.
- `src/content/docs/workflows/python/dag.mdx` documents DAG authoring from Python
  parameter dependencies.

Use the process-loss and granular-result rules. Avoid the general authoring API.

### LangGraph

- `libs/checkpoint/langgraph/checkpoint/base/__init__.py` defines the checkpoint
  saver boundary.
- `libs/checkpoint-postgres/langgraph/checkpoint/postgres/base.py` keys
  checkpoints and pending writes by thread, namespace, checkpoint, and task.
- `libs/langgraph/langgraph/types.py` defines `Interrupt`, `Command`, and
  `interrupt()`.

Use these only behind the Agent Executor interface. The kernel must remain
correct if every LangGraph checkpoint is deleted.

## Standards guidance

- PostgreSQL row locks serialize decisions over one aggregate. `SKIP LOCKED` is
  appropriate only for queue-like candidate selection, while a later locked
  aggregate read must decide eligibility and authority.
- PostgreSQL partial unique indexes are useful for state-dependent cardinality
  rules, such as one running Run per Job. Constraints should backstop protocol
  code at concurrency boundaries.
- Durable runtimes consistently require idempotency at retry boundaries.
  OpenMagic should treat idempotency as one possible safety mechanism, then
  preserve explicit uncertainty whenever the provider outcome cannot be proven.

## Options

| Option | Points | When to choose | Risks | First slice |
| --- | ---: | --- | --- | --- |
| A. Extract existing protocols behind a small kernel package | 10/10 | Default | Accidental leakage of email, Broker, approval, or effect policy into the kernel | Define the kernel state machine and policy ports from existing tests, then move without behavior changes |
| B. Put DBOS under the business layer | 5/10 | Only if general Python durable execution becomes a product requirement | Two durability models, replay coupling, broader database and operational surface | Prototype one effect-free Job and compare failure semantics |
| C. Use Cloudflare Workflows or LangGraph as the kernel | 3/10 | Only if OpenMagic gives up owning its authority and effect protocol | Hosted or graph runtime becomes business truth; stale-Worker and uncertainty invariants need a second control plane | Not recommended |

## Final recommendation

- **Choose A.** Extract, do not replace.
- Preserve existing integration tests as the executable kernel contract.
- Put authority, approval, completion, Commands, and Domain Event
  vocabulary in policy modules above the kernel. External Effect fencing,
  uncertainty, and evidence stay there too.
- Keep the kernel limited to durable identities, finite work, claims, Attempts,
  write-once results, canonical output, Signals, Waits, Trace Events, and atomic
  transitions.
- Defer Delivery and Thread correlation to issue #59.
- Stop extraction if it requires interpreting general Python or adding any item
  in the exclusion table.

## Sources

All web sources accessed 2026-07-13.

- [DBOS Workflows tutorial](https://docs.dbos.dev/python/tutorials/workflow-tutorial)
- [DBOS workflow recovery](https://docs.dbos.dev/production/workflow-recovery)
- [DBOS workflow communication](https://docs.dbos.dev/python/tutorials/workflow-communication)
- [DBOS Python repository](https://github.com/dbos-inc/dbos-transact-py/tree/e9e351574b9a01aad62ead31bddb629fe1eb3e9c), revision `e9e351574b9a01aad62ead31bddb629fe1eb3e9c`
- [DBOS system database schema](https://github.com/dbos-inc/dbos-transact-py/blob/e9e351574b9a01aad62ead31bddb629fe1eb3e9c/dbos/_schemas/system_database.py)
- [DBOS recovery implementation](https://github.com/dbos-inc/dbos-transact-py/blob/e9e351574b9a01aad62ead31bddb629fe1eb3e9c/dbos/_recovery.py)
- [Cloudflare Rules of Workflows](https://developers.cloudflare.com/workflows/build/rules-of-workflows/)
- [Cloudflare Workflows overview](https://developers.cloudflare.com/workflows/)
- [Cloudflare docs repository](https://github.com/cloudflare/cloudflare-docs/tree/c182b697f3ea3fe18582a5e7be773cf20b153faf/src/content/docs/workflows), revision `c182b697f3ea3fe18582a5e7be773cf20b153faf`
- [LangGraph persistence](https://docs.langchain.com/oss/python/langgraph/persistence)
- [LangGraph interrupts](https://docs.langchain.com/oss/python/langgraph/interrupts)
- [LangGraph repository](https://github.com/langchain-ai/langgraph/tree/55ec2f21939ce7755e6398c11b541de8926245ee), revision `55ec2f21939ce7755e6398c11b541de8926245ee`
- [PostgreSQL `SELECT` locking clause](https://www.postgresql.org/docs/current/sql-select.html#SQL-FOR-UPDATE-SHARE)
- [PostgreSQL partial indexes](https://www.postgresql.org/docs/17/indexes-partial.html)
- [OpenMagic Workflow domain](../../CONTEXT.md)
