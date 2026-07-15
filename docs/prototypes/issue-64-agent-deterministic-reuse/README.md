# Agent and deterministic Workflow reuse prototype

This is a throwaway logic prototype for Wayfinder ticket
[Prototype agent and non-agent reuse](https://github.com/heyimcarlos/openmagic/issues/64).
It asks whether three materially different applications can use the same kernel,
Command Runtime, Executor, Domain Event, and Thread Delivery contracts without
special-case kernel concepts.

## Run

Visual lab:

```bash
npm run dev --prefix web
```

Open `http://127.0.0.1:3000/system/prototype`. Use the route visibility setting
to switch between live routes and all predefined routes on the same canvas.

Each reset now begins before the inbound Message is appended. Playback crosses
the complete boundary sequence: immutable Channel Reference, Thread Message,
bounded Thread Context, typed Command, qualified Policy, generic kernel,
Domain Event, durable Delivery, and acknowledged reply on the exact same
Thread. The lab also supports manual progression, predefined revisions, Signal
races, kernel lease loss, External Effect uncertainty, and reconciliation. It
is an in-memory prototype and does not call the existing demo API.

The canvas folds the complete interaction into one editable view:

```text
Thread <-> Conversation Agent
                | typed Command
                v
       Workflow Control Plane -> Workflow Kernel <-> Workflow Worker
                |                                      |
                |                                      v
                |                               Executor Interface
                |                                /      |       \
                |                    deterministic   Agent   external effect
                |
                +-> Domain Event Record + Delivery Record
                                            |
                                            v
                                      Delivery Worker

Template Delivery: Delivery Worker renders frozen Template -> Thread
Agent Delivery:    Delivery Worker -> Conversation Agent -> Thread
```

Every Agent Run reconstructs immutable Thread Context. The Conversation Agent
and Thread provide conversational continuity while each bounded Agent Run keeps
its own durable execution identity. Agent Delivery returns candidate content to
the current Delivery Attempt, and Delivery retains authority to append the final
Message. The return paths do not introduce runtime graph cycles, arbitrary
loops, or mutable Workflow structure.

The visual grammar distinguishes system nature. Stacked square records are
durable, rounded cards are disposable runtimes, double borders mark an
Interface, dashed cards mark optional runtimes, and dotted cards sit outside
OpenMagic. Blue routes are calls, pink routes are returns, orange routes are
atomic commits, and teal routes are claims or leases.

The presentation intentionally folds the pure Template Renderer into the
Delivery Worker card. The renderer remains a separate contract, but it is too
low-level to earn a canvas primitive. Numbered active-route pills provide a
short talk-through order. A toggleable white trace panel shows every committed
trace entry in sequence and explains the current state in plain language.

Cards may be dragged, the canvas may be panned or zoomed, and connected arrows
reroute immediately. Layout can be locked or organized back to the reviewed
lane arrangement. The all-routes setting keeps inactive routes visible for
topology review. The live-routes setting mounts only edges carrying current
work. None of those presentation actions changes a Command, Definition,
Instance, Step, Wait, Delivery, or Thread identity. Earlier layouts were
retired after the editable canvas was selected.

The visualization makes these authority boundaries explicit:

```text
external conversation
  -> inbound Message appended at an atomic Thread Sequence
  -> exact Thread Context reconstructed through that cutoff
  -> typed Command submitted without authority claims
  -> application Policy authorizes the business transition
  -> generic kernel progresses the pinned Definition
  -> application records a Domain Event and durable Delivery
  -> Delivery Worker renders a frozen Template or invokes a same-Thread Conversation Agent Run
  -> Agent candidate content returns to the current Delivery Attempt
  -> Delivery appends idempotently at the next Thread Sequence
  -> acknowledgement targets the same immutable Channel Reference
```

Terminal state inspector:

```bash
uv run python docs/prototypes/issue-64-agent-deterministic-reuse/tui.py
```

The terminal starts with the incident scenario. Every action redraws the complete
in-memory durable state. No database, network, provider, or LLM is used.

## Result being tested

All three scenarios use this dependency direction:

```text
typed application Command
          |
          v
application Handler + qualified Policies
          |
          +---- Domain Event + Delivery Outbox
          |
          v
reusable Command Runtime transaction
          |
          v
generic kernel Control Interface
          |
          v
Definition -> Instance -> Step/Wait -> Attempt/Signal -> Trace Event
```

The kernel sees only Definition, Instance, Step, Attempt, Wait, Signal, Route,
lease, Retry Policy, and Trace Event records. Application Handlers translate
insurance, commerce, and security intent into that vocabulary.

## Matched comparison

| Concern | Renewal outreach | Commerce refund | Incident investigation |
|---|---|---|---|
| Application | Insurance | Commerce | Security operations |
| Execution | Deterministic plus Agent | Deterministic only | Deterministic plus Agent |
| Executable Steps | 3, with repeatable drafting | 4 | 7, with repeatable analysis |
| Exact Waits | Draft approval | Account verification | Scope, findings, containment, closure |
| External Effect | Send email | Issue payment refund | Apply containment |
| Contextual Agent Delivery | No | No | Findings explanation only |
| Completion evidence | Confirmed email effect | Confirmed settlement | Recovery verified, closure confirmed, evidence archived |

## Shared public seams

The three applications configure the same reusable Modules:

```text
CommandDispatcher.execute(Command<C>) -> CommandReceipt<Result<C>>

KernelControl
  create_instance(...)
  apply_command_route(...)
  accept_signal(...)
  close_instance(...)

KernelWork
  claim_step(...)
  renew_attempt(...)
  report_attempt_result(...)

Executor<I, O>.execute(ExecutionContext<I>, CancellationToken) -> O

DeliveryWork
  claim_delivery(...)
  acknowledge_delivery(...)
  report_delivery_failure(...)
```

The names are conceptual, not final Python interfaces. Static application types
remain outside each seam. Registry implementations may erase those types only
inside the reusable Command Runtime or Executor registry.

## Typed Command mappings

Callers submit business intent and exact target identities. They never submit a
Definition, Route key, Step key, graph, Executor key, or Policy decision.

| Command Type | Essential typed input | Application Handler and Policy result | Kernel mutation |
|---|---|---|---|
| `renewal.start_outreach` | renewal case, source Thread | Select `renewal.outreach` v1 and map immutable renewal facts | Create Instance through `start` |
| `renewal.request_revision` | Workflow, exact approval Wait, presented draft, revision facts | Revalidate authority and presentation | Accept `revision_requested` Signal |
| `renewal.approve_draft` | Workflow, exact Wait, draft fingerprint, presentation cutoff | Create exact Approval Grant | Accept `approved` Signal and bind returned send Step |
| `renewal.begin_email_dispatch` | Workflow, send Step, Attempt ID and number, effect fingerprint | Validate Grant, authority, fence, and uncertainty | Obtain current Attempt guard; no Route |
| `refund.request` | order, payment, amount request, source Thread | Validate requester and select refund Definition | Create Instance through `start` |
| `refund.verify_account` | Workflow, exact verification Wait, challenge and evidence | Validate challenge, account control, and expiry | Accept `account_verified` Signal |
| `refund.begin_dispatch` | refund effect, exact Attempt identity, fingerprint | Fence provider refund and return typed permit | Obtain current Attempt guard; no Route |
| `incident.open` | incident report, affected system, source Thread | Select incident Definition and map report facts | Create Instance through `start` |
| `incident.confirm_scope` | Workflow, exact Wait, analysis artifact, presentation cutoff | Validate responder authority and artifact | Accept `scope_confirmed` or revision Signal |
| `incident.confirm_findings` | Workflow, exact Wait, evidence bundle, presentation cutoff | Validate investigator authority and evidence identity | Accept findings or more-evidence Signal |
| `incident.approve_containment` | Workflow, exact Wait, plan fingerprint, presentation cutoff | Create containment Approval Grant | Accept approval or revision Signal |
| `incident.confirm_closure` | Workflow, exact Wait, recovery evidence | Validate closure authority and recovery certainty | Accept `closure_confirmed` Signal |
| `incident.begin_containment` | exact effect, Grant, Step, Attempt ID and number, fingerprint | Fence containment effect and return typed permit | Obtain current Attempt guard; no Route |

Provider results and reconciliation evidence return through separate trusted,
typed application Commands. Every Handler maps kernel errors into the shared
application-facing error taxonomy. The kernel never sees the business Command
Type.

## Concrete Definition summaries

### Renewal Definition `renewal.outreach`, version 1

| Template | Kind | Executor | Retry Policy |
|---|---|---|---|
| `gather_renewal_facts` | Step | deterministic `renewal_facts.v1` | 3 bounded Attempts |
| `draft_email` | Step | Agent `renewal_drafter.v1` | 3 bounded Attempts |
| `approve_draft` | Wait | none | none |
| `send_email` | Step, External Effect | deterministic `gmail_send.v1` | 2 bounded Attempts, Policy-gated |

| Route | Accepted activation | Finite output batch |
|---|---|---|
| `start` | Instance creation Command | `gather_renewal_facts` |
| `facts_ready` | canonical facts Step success | `draft_email` |
| `draft_ready` | canonical draft Step success | `approve_draft` |
| `draft_approved` | exact `approved` Signal | `send_email` |
| `draft_revised` | exact `revision_requested` Signal | another `draft_email` occurrence |

### Refund Definition `commerce.high_value_refund`, version 1

| Template | Kind | Executor | Retry Policy |
|---|---|---|---|
| `validate_request` | Step | deterministic `refund_validator.v1` | 3 bounded Attempts |
| `verify_account` | Wait | none | none |
| `calculate_refund` | Step | deterministic `refund_calculator.v1` | 3 bounded Attempts |
| `issue_refund` | Step, External Effect | deterministic `payment_refund.v1` | 2 bounded Attempts, Policy-gated |
| `confirm_settlement` | Step, read-only evidence | deterministic `settlement_reader.v1` | 3 bounded Attempts |

| Route | Accepted activation | Finite output batch |
|---|---|---|
| `start` | Instance creation Command | `validate_request` |
| `request_valid` | canonical validation Step success | `verify_account` |
| `account_verified` | exact `account_verified` Signal | `calculate_refund` |
| `amount_ready` | canonical calculation Step success | `issue_refund` |
| `refund_dispatched` | confirmed effect Step success | `confirm_settlement` |

### Incident Definition `security.incident_investigation`, version 1

| Template | Kind | Executor | Retry Policy |
|---|---|---|---|
| `normalize_report` | Step | deterministic `incident_normalizer.v1` | 3 bounded Attempts |
| `analyze_incident` | Step | Agent `incident_analyst.v1` | 3 bounded Attempts |
| `confirm_scope` | Wait | none | none |
| `collect_evidence` | Step | deterministic `evidence_collector.v1` | 3 bounded Attempts |
| `confirm_findings` | Wait | none | none |
| `draft_containment` | Step | Agent `containment_planner.v1` | 3 bounded Attempts |
| `approve_containment` | Wait | none | none |
| `apply_containment` | Step, External Effect | deterministic `containment_adapter.v1` | 2 bounded Attempts, Policy-gated |
| `verify_recovery` | Step, read-only evidence | deterministic `recovery_checker.v1` | 3 bounded Attempts |
| `confirm_closure` | Wait | none | none |
| `archive_evidence` | Step | deterministic `evidence_archiver.v1` | 3 bounded Attempts |

| Route | Accepted activation | Finite output batch |
|---|---|---|
| `start` | Instance creation Command | `normalize_report` |
| `report_normalized` | normalized report Step success | `analyze_incident` |
| `analysis_ready` | canonical analysis Step success | `confirm_scope` |
| `scope_confirmed` | exact scope Signal | `collect_evidence` |
| `scope_revised` | exact revision Signal | another `analyze_incident` occurrence |
| `evidence_ready` | canonical evidence Step success | `confirm_findings` |
| `findings_confirmed` | exact findings Signal | `draft_containment` |
| `more_evidence` | exact more-evidence Signal | another `collect_evidence` occurrence |
| `plan_ready` | canonical plan Step success | `approve_containment` |
| `plan_approved` | exact approval Signal | `apply_containment` |
| `plan_revised` | exact revision Signal | another `draft_containment` occurrence |
| `containment_applied` | confirmed effect Step success | `verify_recovery` |
| `recovery_verified` | canonical recovery Step success | `confirm_closure` |
| `closure_confirmed` | exact closure Signal | `archive_evidence` |

The final Step of each Definition has no downstream Route. Its accepted result
causes application Completion Policy to record the business completion Event and
close the exact Instance atomically. Kernel quiescence alone never completes it.

## Scenario 1: Agent-driven renewal outreach

```text
renewal.start_outreach Command
  -> gather_renewal_facts Step, deterministic
  -> draft_email Step, Agent Executor
  -> approve_draft Wait
       | approved
       +-> send_email Step, fenced External Effect
       |
       | revision_requested
       +-> another draft_email occurrence
  -> confirmed send evidence
  -> Completion Policy closes Workflow and Instance
```

The Agent receives bounded typed drafting input. It cannot choose a Route,
approve the draft, send the email, or commit state. `renewal.draft.ready` creates
a deterministic Template Delivery to the exact originating Thread. Revisions
materialize new `draft_email` occurrences from the predefined revision Route.

Representative application facts:

```text
renewal.draft.ready
renewal.draft.approved
renewal.revision.requested
renewal.email.sent
renewal.completed
```

## Scenario 2: Deterministic high-value commerce refund

```text
refund.request Command
  -> validate_request Step
  -> verify_account Wait
  -> calculate_refund Step
  -> issue_refund Step, fenced External Effect
  -> confirm_settlement Step, read-only provider evidence
  -> Completion Policy closes Workflow and Instance
```

No Agent is registered or invoked. Account verification is an application
Policy and exact Signal, not a kernel verification state. Refund calculation,
provider input, retry safety, evidence interpretation, and completion remain
typed commerce contracts above the kernel.

Representative application facts:

```text
refund.request.validated
refund.account.verified
refund.amount.calculated
refund.dispatched
refund.settled
refund.completed
```

Every user-facing update is a deterministic Template Delivery to the exact
support Thread.

## Scenario 3: Hybrid security incident investigation

```text
incident.open Command
  -> normalize_report Step, deterministic
  -> analyze_incident Step, Agent Executor
  -> confirm_scope Wait
       | revision_requested -> another analyze_incident occurrence
       v
  -> collect_evidence Step, deterministic
  -> confirm_findings Wait
       | more_evidence_requested -> another collect_evidence occurrence
       v
  -> draft_containment Step, Agent Executor
  -> approve_containment Wait
       | revision_requested -> another draft_containment occurrence
       v
  -> apply_containment Step, fenced External Effect
  -> verify_recovery Step, deterministic and read-only
  -> confirm_closure Wait
  -> archive_evidence Step, deterministic
  -> Completion Policy closes Workflow and Instance
```

This scenario supplies the main pressure test: seven executable Step
occurrences, four separate confirmation Waits, two Agent-backed templates,
deterministic work, a materially irreversible effect, and repeatable predefined
revision Routes. An unknown number of requested revisions creates new durable
occurrences without changing the Definition or introducing a kernel loop.

`incident.findings.ready` is the only Agent Delivery. Delivery Policy selects it
because the explanation must respond to meaning already present in the exact
incident Thread. The Delivery freezes the Thread ID and Message Sequence cutoff.
A restricted Conversation Agent rehydrates only that Thread and may produce one
candidate Message. It cannot submit Commands, select Routes, approve containment,
or perform External Effects. Every other incident Delivery uses a Template.

Representative application facts:

```text
incident.analysis.ready
incident.scope.confirmed
incident.findings.ready
incident.findings.confirmed
incident.containment.plan_ready
incident.containment.approved
incident.containment.applied
incident.recovery.verified
incident.closure.confirmed
incident.closed
```

## Concrete runtime timelines

The identifiers below are illustrative opaque identities. Ordering comes from
Instance Trace sequence and Thread Message Sequence, not timestamps.

### Renewal happy path

```text
Command cmd-renewal-1 commits Workflow wf-renewal-1
  Trace 1: start Route -> Step step-facts-1
  Trace 2: Attempt attempt-facts-1, number 1, leased
  Trace 3: facts result accepted -> Step step-draft-1
  Trace 4: Attempt attempt-draft-1, number 1, leased
  Trace 5: draft result accepted -> Wait wait-approval-1
  Domain Event event-draft-1 -> Delivery delivery-draft-1
  Delivery Attempt delivery-attempt-1 -> Message message-41, sequence 41
  Command cmd-approve-1 -> Signal signal-approved-1
  Trace 6: Wait satisfied -> Step step-send-1
  Command cmd-dispatch-1 commits fence for attempt-send-1
  Provider evidence confirms effect
  Trace 7: send result accepted
  Domain Event event-sent-1 -> Delivery delivery-sent-1
  Completion Policy records renewal.completed and closes Instance
```

### Refund happy path

```text
Command cmd-refund-1 -> Step step-validate-1
  validation success -> Wait wait-account-1
  Command cmd-verify-1 -> Signal signal-account-1
  Wait satisfaction -> Step step-calculate-1
  calculation success -> Step step-refund-1
  Command cmd-refund-dispatch-1 commits provider fence
  provider evidence confirms refund -> Step step-settlement-1
  settlement evidence succeeds
  Domain Event refund.settled -> Template Delivery to thread-support-1
  Completion Policy records refund.completed and closes Instance
```

### Incident happy path

```text
cmd-incident-1
  -> step-normalize-1 / attempt 1
  -> step-analysis-1 / Agent Attempt 1
  -> wait-scope-1 / signal-scope-1
  -> step-evidence-1 / attempt 1
  -> wait-findings-1 / signal-findings-1
  -> step-plan-1 / Agent Attempt 1
  -> wait-containment-1 / signal-containment-1
  -> step-apply-1 / fenced deterministic Attempt 1
  -> step-recovery-1 / deterministic Attempt 1
  -> wait-closure-1 / signal-closure-1
  -> step-archive-1 / deterministic Attempt 1
  -> incident.closed + Instance closure
```

Every arrow is either one accepted Route activation or application completion.
There is no mutable program counter, caller-authored edge, or orchestration-code
replay.

## Delivery plans

| Domain Event | Destination | Content Mode | Frozen presentation contract |
|---|---|---|---|
| `renewal.draft.ready` | exact renewal Thread ID | Template | key `renewal_draft_ready`, version 1, locale, typed draft input |
| `renewal.email.sent` | same exact renewal Thread ID | Template | key `renewal_email_sent`, version 1, typed effect evidence |
| `refund.request.validated` | exact support Thread ID | Template | key `refund_verification_required`, version 1, typed challenge reference |
| `refund.settled` | same exact support Thread ID | Template | key `refund_settled`, version 1, typed settlement facts |
| `incident.analysis.ready` | exact incident Thread ID | Template | key `incident_scope_review`, version 1, typed artifact reference |
| `incident.findings.ready` | exact incident Thread ID | Agent | versioned Agent and task, exact Thread cutoff, bounded Event and audience context |
| `incident.containment.plan_ready` | same exact incident Thread ID | Template | key `incident_containment_review`, version 1, typed plan reference |
| `incident.recovery.verified` | same exact incident Thread ID | Template | key `incident_closure_review`, version 1, typed recovery evidence |
| `incident.closed` | same exact incident Thread ID | Template | key `incident_closed`, version 1, typed closure facts |

Each Domain Event and Delivery Outbox record commits in the transaction that
created the communication obligation. Rendering occurs later. Template rendering
is pure and cannot read Thread history. Agent rendering may rehydrate only the
Delivery's exact Thread through its frozen Message Sequence cutoff. Every
Delivery independently revalidates current Audience authorization before content
generation.

## Identity and transaction rules

Each scenario uses the same identities and fences:

| Mutation | Durable idempotency identity | Atomic result |
|---|---|---|
| Command | Command ID | Receipt plus application, kernel, Event, and Outbox changes |
| Route | source kind and source ID | Complete finite Step or Wait batch plus Trace Event |
| Step claim | claim request ID | One opaque Attempt ID and monotonic Attempt Number |
| Attempt result | Attempt ID | Write-once result and policy disposition |
| Wait satisfaction | Signal ID plus exact Wait ID | Signal, satisfied Wait, Route batch, Trace Event |
| External Effect dispatch | Command ID and exact Attempt guard | Policy fence, Domain Event, Dispatch Permit |
| Delivery claim | Delivery Attempt ID | One current leased Delivery Attempt |
| Delivery acknowledgement | successful Delivery Attempt ID | Message append, sequence, Attempt success, Delivery success |

All application and kernel changes share one PostgreSQL transaction through the
application transaction Adapter. Executor, Agent, provider, Template rendering,
and Agent Delivery work occur outside that transaction.

## Failure cases exposed by the prototype

### Kernel lease loss

An abandoned Attempt permanently consumes its Attempt Number. The Step remains
pending and a policy-authorized retry allocates a new opaque Attempt ID and the
next number. A late result from the abandoned Attempt is stale.

### Competing confirmations

Two Signals targeting one Wait serialize through the Instance lock. The first
valid Signal records permanent satisfaction provenance and activates one Route.
The other Signal is rejected without creating kernel state.

### External Effect uncertainty

The dispatch fence commits before the provider call. If the provider response is
lost, the Attempt may report uncertainty, but the Step remains pending and
unscheduled. Automatic retry is blocked. Reconciliation evidence may later prove
the effect occurred and allow Policy to succeed the Step without replaying it.

### Delivery lease loss

A lost Delivery Worker abandons its Delivery Attempt. A new Attempt receives a
new identity and attempt number. A stale Agent Run or renderer cannot append the
Message. Acknowledgement atomically appends exactly one Message and assigns its
Thread-local sequence.

### Exact replay

Committed Command, Route, Signal, result, and acknowledgement identities return
their original receipts. Conflicting content under the same identity fails. A
rejected request consumes no identity and creates no durable record.

## Fit verdict

The three scenarios fit without adding Agent, insurance, commerce, security,
approval, verification, Thread, Delivery, or External Effect fields to the
kernel. The accepted Modules remain deep:

- The kernel hides serialization, occurrence identity, fencing, recovery,
  retries, Signals, and Trace receipts behind role-scoped Interfaces.
- The Command Runtime hides typed dispatch, controlled erasure, canonical
  validation, idempotency, receipts, and transaction orchestration.
- Each application owns vocabulary, Policies, Handlers, Definition mappings,
  Domain Events, Deliveries, completion, authority, evidence, and effects.
- Executors share one typed cancellable Interface whether their implementation
  is deterministic or Agent-backed.

No new durable domain term is required by these examples. The incident scenario
does reveal one implementation acceptance requirement: startup readiness must
validate every Route referenced by repeated confirmation and revision paths,
not only the happy path. That requirement already follows from the accepted
Definition contract and does not require a new kernel Interface.
