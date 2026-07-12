# Draft implementation handoff

This draft is not yet published. It depends on human acceptance of the
prototype integration shape.

## 1. Persist and inspect an atomic renewal Workflow graph

**Blocked by:** None

**What it delivers:** A caller can create one typed `renewal_outreach.v1`
Workflow containing the queued Draft Job and waiting Send Job in PostgreSQL,
then inspect the resulting graph, Events, and derived waiting reasons through a
development trace. The application registry, migrations, and transaction seam
are exercised through the same Control Plane interface later tickets use.

**Acceptance direction:**

- The six V0 protocol tables and critical structural constraints exist.
- The versioned Workflow and Job Kind registries validate the renewal graph.
- Graph creation is atomic and serializes through the Workflow row.
- Rejected unknown, invalid, or unauthorized proposals make no domain change.
- A structured trace proves the two Jobs, dependency, initial statuses, and
  causal Event.

## 2. Resolve one Workflow and propose typed work from the Interaction Agent

**Blocked by:** Persist and inspect an atomic renewal Workflow graph

**What it delivers:** A broker request reaches authorization-scoped
`search_workflows`, resolves one candidate, reads one bounded Workflow Packet,
and proposes the typed Draft and Send graph without calling
`send_message_to_agent`.

**Acceptance direction:**

- Synthetic distractors include historical, same-name, wrong-kind, and
  unauthorized Workflows.
- Search exposes cardinality, facets, truncation, applied filters, and truthful
  match reasons.
- Packet loading occurs only after unambiguous resolution.
- Ambiguity and no match cause no mutation.
- The accepted proposal references the resolved Workflow and passes through the
  Control Plane.
- The inherited direct-delegation path remains available as the controlled
  baseline but is not used by the V0 tracer.

## 3. Draft through one claimed Run and notify a fresh Interaction Agent

**Blocked by:** Resolve one Workflow and propose typed work from the Interaction
Agent

**What it delivers:** A deterministic Worker claims the Draft Job, creates one
Run, starts a fresh Execution Agent using a bounded execution packet, publishes
one frozen Draft Revision, and exits that runtime. The resulting Event creates
a Notification that starts a fresh Interaction Agent, reloads the Workflow
Packet, and presents the exact draft for approval.

**Acceptance direction:**

- Claim atomically increments attempts, creates one running Run, and returns one
  Job-specific packet.
- The Job Kind registry, not the caller, chooses the fresh Execution Agent
  strategy.
- Successful Run data publishes the canonical Draft Job output once.
- Draft readiness and Notification creation commit together.
- Notification delivery uses stable identifiers and a new Workflow Packet.
- Runtime history proves no Interaction Agent or Execution Agent owns the
  waiting Workflow.

## 4. Approve and complete one deterministic email effect

**Blocked by:** Draft through one claimed Run and notify a fresh Interaction
Agent

**What it delivers:** Explicit approval of the presented draft records one exact
Approval Grant, queues the Send Job, commits dispatch before one deterministic
email-adapter call, applies the typed successful result, completes the Workflow,
and notifies the user from another fresh Interaction Agent turn.

**Acceptance direction:**

- Approval binds the implicit Broker, Send Job, Draft Job, Cause, and complete
  effect fingerprint.
- Duplicate identical approval is idempotent and stale approval is rejected.
- Dispatch revalidates authority, approval, fingerprint, lifecycle, and the
  unused dispatch allowance in one transaction.
- The deterministic fake records the exact effect and rejects a second call for
  the Job.
- Successful result reporting publishes the normalized receipt and satisfies
  the registered Workflow completion predicate.
- Completion and notification delivery remain distinct observations.

## 5. Prove the approved email through live Composio and recipient evidence

**Blocked by:** Approve and complete one deterministic email effect

**What it delivers:** The same approved Send Job executes through the pinned,
retry-disabled live Composio Gmail adapter and sends one correlated email to the
authorized disposable inbox. OpenMagic succeeds from the normalized Composio
acknowledgement and the smoke harness independently observes the matching
recipient message.

**Acceptance direction:**

- The exact approved plain-text effect maps to `GMAIL_SEND_EMAIL` without
  mutable provider drafts.
- The adapter uses the pinned SDK and toolkit contracts with no automatic
  retry.
- A complete successful response produces a normalized Run Result and Job
  output.
- One dispatch Event, one provider invocation, one received message, one
  succeeded Send Job, and one completed Workflow are observed.
- Credentials and raw provider payloads do not enter source, logs, packets,
  issues, or artifacts.
- The deterministic adapter remains the required path for unsafe fault
  branches; the live smoke proves the current happy path only.

## Proposed dependency chain

```text
Persist atomic graph
  -> Interaction Agent retrieval and proposal
  -> Draft Run and approval Notification
  -> Exact approval and deterministic send
  -> Live Composio and recipient verification
```

Each slice is independently demonstrable and owns one focused pull request.
