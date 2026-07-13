# Contingent issue 11 implementation tickets

These drafts are not published tracker tickets until the issue 11 verdict
boundary is approved. They follow the same vertical-ticket shape used by the
completed issue 10 handoff.

## Pair the legacy and Workflow renewal coordination journeys

### What to build

Run one typed synthetic request corpus through the inherited legacy Interaction
profile and the V0 Workflow profile, using the same model configuration and
isolated conversation state. Stop the legacy path at its named-agent dispatch
boundary and let the Workflow path use real PostgreSQL search, Packet retrieval,
and proposal commands. Preserve one bounded result per profile, scenario, and
trial, then render a concise paired evidence report.

### Acceptance criteria

- Unique, ambiguous, missing, authorization-distractor, irrelevant-context,
  and duplicate-Cause renewal requests have stable scenario identifiers.
- The Workflow profile must select the right Workflow or clarify safely, load
  at most one full Packet, never leak unauthorized data, and make no mutation
  on ambiguity or no match.
- The legacy profile remains an observed baseline. Its outcome never weakens a
  V0 correctness assertion or causes unsafe execution.
- A credentialed opt-in runner repeats both profiles with the same real model
  configuration and records model/build identity, tool trajectory, Packet
  count, context bytes, approximate tokens, and separate local/model durations.
- Deterministic PR coverage validates the runner and report contracts without
  claiming that scripted model output measures model quality.
- Every trial has its own secret-safe JSON evidence record. A Markdown summary
  distinguishes strict V0 verdicts from baseline and performance diagnostics.
- No Execution Agent, Composio call, or email External Effect occurs in this
  coordination comparison.

### Decision provenance

- Prototype V0 paired evaluation and recovery harness.
- Define the V0 Workflow search and packet-retrieval contract.
- Prototype V0 workflow tools, packets, and Worker integration.
- Issue 11 ranked comparables and coverage audit.

### Blocking

This is the first implementation frontier after issue 11 resolves.

## Prove restart and duplicate-Cause recovery

### What to build

Exercise the durable Workflow through explicit application and database
boundary reconstruction while waiting for approval and after Worker loss.
Make re-delivery of one authenticated interaction Cause replay-safe so it
cannot create a second Workflow graph or a different mutation.

### Acceptance criteria

- The same authenticated Cause and content delivered twice produces one
  accepted Workflow graph and one stable replay result.
- Reusing one Cause with conflicting content or a conflicting typed mutation
  fails closed and changes no Workflow state.
- Concurrent duplicate delivery cannot create two Workflows or two initial Job
  graphs.
- Restart while awaiting approval disposes all application objects, constructs
  fresh boundaries, reads the frozen Draft and waiting Send Job from
  PostgreSQL, and continues through exact approval without prompt history.
- Worker loss before dispatch abandons the old Run, revokes late authority, and
  allows the deterministic policy to create one later Run when budget remains.
- Worker loss after dispatch leaves the Send Job waiting and never invokes the
  deterministic adapter twice.
- The evidence report includes the resulting Workflow, Job, Run, Event,
  Approval, and dispatch trace for each recovery boundary.

### Decision provenance

- Complete the minimum V0 Workflow and Job lifecycle authority.
- Specify the minimum V0 PostgreSQL Job protocol.
- Define V0 renewal-email acceptance scenarios.
- Prototype V0 paired evaluation and recovery harness.

### Blocking

Blocked by Pair the legacy and Workflow renewal coordination journeys.

## Complete the Notification fault matrix and V0 evaluation report

### What to build

Drive Notification delivery through lost, duplicate, delayed, stale-lease,
out-of-order, and restart cases using real PostgreSQL and fresh Interaction
runtimes. Consolidate the paired, recovery, deterministic-Composio, and live
smoke evidence into the bounded issue 11 report consumed by the walkthrough.

### Acceptance criteria

- Duplicate claim or acknowledgement produces no duplicate correlated reply.
- Delivery failure and lease loss retry only under the persisted Notification
  attempt budget, and stale claims cannot acknowledge later work.
- A delayed Notification remains unclaimable before `available_at` and becomes
  claimable afterward under a controlled clock.
- Out-of-order delivery never presents a stale approval request. Under V0's
  existing statuses, a no-longer-actionable approval Notification exhausts or
  terminates as failed rather than misleading the Party.
- Restart reconstructs the Notification Worker and fresh Interaction runtime
  from identifiers, then delivers without prior prompt or in-memory batch state.
- Job completion, Notification delivery, and user-visible acknowledgement are
  reported as separate observations.
- The final report labels deterministic gates, baseline/model diagnostics, and
  the live Composio smoke distinctly. It includes exact commands and current
  build identity without credentials or raw mailbox content.
- The report is reproducible through one documented command and becomes the
  evidence input for the five-minute walkthrough ticket.

### Decision provenance

- Specify the minimum V0 PostgreSQL Job protocol.
- Compare OpenMagic with Effective AI's multi-agent runtime.
- Prototype V0 workflow tools, packets, and Worker integration.
- Prototype V0 paired evaluation and recovery harness.

### Blocking

Blocked by Prove restart and duplicate-Cause recovery.
