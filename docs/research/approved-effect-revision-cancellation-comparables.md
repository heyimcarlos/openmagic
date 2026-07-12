# Approved effect revision and cancellation comparables

Date: 2026-07-11

## Decision frame

- **Target project:** OpenMagic durable workflow control plane.
- **Current stack:** inherited OpenPoke Python and FastAPI backend, Next.js frontend, direct Composio Gmail integration.
- **Target stack:** Python, FastAPI, Pydantic, PostgreSQL, SQLAlchemy, and Alembic.
- **Domain:** human-approved insurance work containing externally irreversible effects such as sending email.
- **Hard constraints:** approval must cover exact content; Worker claim is distinct from provider dispatch; a dispatched effect cannot be silently replayed, erased, or rewritten; V0 accepts a valid Composio `successful: true` result as Send Job success; Gmail Sent trigger ingestion is deferred.
- **Question:** what one general rule should govern edits and cancellation before and after an irreversible effect begins?

## Ranked comparables

Scores are 0 to 5 for domain fit (D), target stack fit (S), production maturity (M), architecture clarity (A), operations relevance (O), testing quality (T), and documentation signal (Q).

| Rank | Source | D/S/M/A/O/T/Q | Total | Best match | Important mismatch | Use for |
| --- | --- | --- | ---: | --- | --- | --- |
| 1 | Temporal | 4/4/5/5/5/5/5 | 33/35 | Durable attempts, cancellation, retry hazards, and external-effect ambiguity | Activities normally assume idempotency; OpenMagic Gmail sends are not safely replayable | Separate claim, execution, cancellation, and provider effect boundaries |
| 2 | Camunda 8 / Zeebe | 4/3/5/5/5/5/5 | 32/35 | Activated Job leases, cancellation, and rejection of stale completion | Java engine and BPMN process model are much broader than OpenMagic V0 | Fence a claimed Worker without treating claim as dispatch |
| 3 | OpenAI Agents SDK plus GitHub review rules | 4/5/4/5/3/5/5 | 31/35 | Approval scoped to one tool call with preserved arguments; changed reviewed input requires approval again | An agent RunState is not a complete business workflow control plane | Bind approval to one exact pending action and its immutable input |
| 4 | Cloudflare Agents | 5/2/4/4/5/5/5 | 30/35 | Durable agent waits, human approval, email acceptance receipts, reply routing, and restart handling | TypeScript and Workers runtime; examples do not close the non-idempotent send crash gap | Product flow, durable approval wait, exact action logging, and reply correlation |

## Key evidence

### Temporal

Temporal documents Activities as at-least-once work and recommends idempotency because a Worker may complete an external action and crash before reporting completion. Cancellation is cooperative, so requesting cancellation does not prove that already-running external code stopped. Source paths in `temporalio/temporal` include `service/history/api/respondactivitytaskcompleted/api.go`, `service/history/api/respondactivitytaskcanceled/api.go`, and `service/history/api/recordactivitytaskheartbeat/api.go`. The Python SDK documents cancellation delivery through heartbeats and provides explicit Activity cancellation modes.

**Local implication:** a Worker claim or cancellation request cannot stand in for proof about an external email. OpenMagic needs its own dispatch boundary and must not retry after that boundary merely because a Run disappeared.

### Camunda 8 / Zeebe

Zeebe separates activating a Job from completing it. `zeebe/engine/src/main/java/io/camunda/zeebe/engine/processing/job/JobCancelProcessor.java` appends a cancelled Job event when the Job still exists. `JobCompleteProcessor.java` and `JobCommandPreconditionValidator.java` require an eligible current Job and a valid lease, so stale completion is rejected. `ProcessInstanceCancelProcessor.java` propagates process cancellation through engine-owned state.

**Local implication:** OpenMagic may safely cancel a claimed Run before dispatch if cancellation wins the database race and invalidates the Worker's completion and dispatch authority. This does not undo an external action the Worker already performed.

### OpenAI Agents SDK and GitHub

`openai-agents-python/docs/human_in_the_loop.md` pauses on a `ToolApprovalItem` that exposes the exact tool name and arguments. `src/agents/run_context.py` scopes ordinary approvals to the exact tool call ID, and `src/agents/run_state.py` serializes approvals and pending tool input for later resumption. A changed call is a new call and requires a new decision.

GitHub protected-branch rules record the reviewed diff and can dismiss an approval when new commits change it. The approved thing is the reviewed version, not a mutable intent such as "merge whatever this branch contains later."

**Local implication:** approval must reference one exact Workflow Job and its immutable Effect-Defining Input. Editing that input cannot reuse the prior approval.

### Cloudflare Agents

Cloudflare provides several useful adjacent patterns:

- `packages/agents/src/workflows.ts` implements a durable `waitForApproval()` event wait. No Worker or browser request needs to remain alive while a human decides.
- `examples/think-workflows/src/index.ts` creates and stores a draft, waits for a targeted approval event, and only then performs the publish step.
- `packages/codemode/src/runtime.ts` records `executionId`, sequence, connector, method, and exact arguments. Replay with different arguments is rejected as divergence.
- `examples/email-agent/src/server.ts` awaits `sendEmail()`, receives a provider `messageId`, and then records the outbox entry. Its client says "Email accepted by Email Service," which is more precise than claiming recipient delivery.
- `docs/agents/email.md` stores pending reply information for a delayed or human-approved response. Signed agent routing headers and `In-Reply-To` keep later replies connected to the originating agent and email thread.

Cloudflare also exposes two copying risks:

- The email example persists its outbox record after the external send. A crash between provider acceptance and `setState()` can leave an applied email without matching local state.
- `packages/codemode/src/runtime.ts` deliberately re-executes a call left in `executing` after a crash before `recordResult()`. That is acceptable only when the operation is idempotent or duplicate-safe. It is unsafe for an ordinary Gmail send.

**Local implication:** borrow the durable wait, exact argument identity, honest acceptance wording, receipt storage, and reply-routing ideas. Keep OpenMagic's pre-dispatch event, one-dispatch allowance, and ambiguous-outcome handling instead of copying generic replay.

## Recommended general rule

Use one rule for every approved External Effect:

> Approval authorizes one exact immutable Workflow Job. Before that Job crosses its dispatch boundary it may be cancelled and replaced. After it crosses the boundary, its effect cannot be erased or rewritten; changed intent becomes a separately approved Workflow Job.

The lifecycle consequence is:

1. A proposed side-effecting Job contains immutable Effect-Defining Input.
2. The applicable Approval Grant identifies that exact Job and input. The detailed grant contract remains the responsibility of `Define Approval Grants and invalidation`.
3. Approval satisfies that Job's approval prerequisite. The Job becomes queued only when its other non-temporal prerequisites are also satisfied.
4. A Worker claim creates a Run. Claiming is still reversible and does not mean the provider was called.
5. Before `external_effect_dispatch_started`, an edit or cancellation may win one transaction that fences the Run, cancels the old Job, creates a Revision Job, and leaves the Revision Job waiting for fresh approval.
6. Immediately before the provider call, the Worker atomically revalidates its lease, Job status, authority, approval, absence of a committed cancellation, and unused dispatch allowance, then records `external_effect_dispatch_started`.
7. If that dispatch transaction wins first, cancellation is too late. The original Job continues toward succeeded, failed, or reconciliation. The changed email or other changed intent becomes a separate Revision Job waiting for approval.
8. A valid Composio `successful: true` response succeeds both the Run and Send Job in V0.
9. An ambiguous post-dispatch timeout or lost response is never blindly retried.

This rule needs no Job-level `cancelling` state. The user receives one of two definitive outcomes from the database race:

- **Cancelled:** the old Job and active Run were cancelled before dispatch; the replacement is waiting for approval.
- **Too late:** dispatch already began; the original effect remains authoritative and a separately approved follow-up may be prepared.

## What to emulate now

- Preserve exact effect input and approval identity durably.
- Pause approval waits without holding a Worker.
- Fence stale Workers at dispatch and completion.
- Treat claim, dispatch, provider acceptance, and recipient delivery as different observations.
- Preserve provider receipts such as a message ID when the provider actually returns one.
- Carry stable internal identity into safe provider correlation fields when supported.
- Test both possible winners of the cancellation-versus-dispatch transaction race.

## What to defer

- General Composio trigger ingestion and Gmail Sent confirmation.
- Provider-neutral compensation machinery.
- A universal cancellation protocol for external systems.
- Automatic approval reuse across different Jobs or changed effect input.

## What to avoid

- Mutating an approved Job in place.
- Equating Worker claim with provider dispatch.
- Reporting "cancelled" before the cancellation transaction wins.
- Treating a Workflow engine's cancellation as proof that an external email stopped.
- Retrying an `executing` or post-dispatch send after a crash without authoritative non-application evidence.
- Copying Cloudflare Workflow or Temporal retry defaults around a non-idempotent Gmail call.
- Adding email-specific lifecycle states when the immutable approved-effect rule already covers the case.

## Sources

Accessed 2026-07-11.

- [Temporal Python error-handling best practices](https://docs.temporal.io/develop/python/best-practices/error-handling)
- [Temporal server repository](https://github.com/temporalio/temporal)
- [Temporal Python SDK repository](https://github.com/temporalio/sdk-python)
- [Camunda job workers](https://docs.camunda.io/docs/components/concepts/job-workers/)
- [Camunda Zeebe API](https://docs.camunda.io/docs/8.7/apis-tools/zeebe-api/gateway-service/)
- [Camunda repository](https://github.com/camunda/camunda)
- [OpenAI Agents Python human-in-the-loop guide](https://github.com/openai/openai-agents-python/blob/main/docs/human_in_the_loop.md)
- [GitHub protected branches and stale approvals](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-protected-branches/about-protected-branches)
- [Cloudflare Agents repository](https://github.com/cloudflare/agents)
- [Cloudflare Email Agent example](https://github.com/cloudflare/agents/tree/main/examples/email-agent)
- [Cloudflare Agents email guide](https://github.com/cloudflare/agents/blob/main/docs/agents/email.md)
- [Cloudflare Think Workflow approval example](https://github.com/cloudflare/agents/blob/main/examples/think-workflows/src/index.ts)
- [Cloudflare CodeMode durable runtime](https://github.com/cloudflare/agents/blob/main/packages/codemode/src/runtime.ts)
