# OpenMagic Workflow Domain

OpenMagic coordinates durable insurance work across conversations, people, organizations, and replaceable execution processes. This glossary names the business concepts that remain stable when channels, providers, and agent runtimes change.

## Language

**Party**:
A person or organization represented by OpenMagic independently of any particular Workflow. Every human or organizational identity is a Party; an application user is not a separate domain concept.
_Avoid_: Client, user, account

**Party Identifier**:
A typed identifier with a canonical value associated with a Party; at most one association for a given kind and value may be current, though revoked historical associations may reuse it. It carries its own verification and revocation history: an unverified association may preserve channel continuity, while a current verified association may support identity resolution; neither grants authority.
_Avoid_: Name match, user ID, global Party verification

**Organization Membership**:
The relationship through which a person Party is affiliated with an organization Party, such as through employment. It is evaluated only after the person has been independently identified and is distinct from that person's role in a particular Workflow.
_Avoid_: Employee identity, organization user, global role

**Provisional Party**:
A Party with no current Party Identifier verified strongly enough for the requested action. Provisionality is an assurance condition, not a separate person or organization kind.
_Avoid_: Anonymous user, guest Party

**Party Resolution**:
A non-destructive relationship from a Provisional Party to the established Party it was later verified to represent. Historical references remain on the Provisional Party, while future identity resolution uses the established Party.
_Avoid_: Party merge, rewritten Actor

**Workflow**:
A durable business objective that may span many messages, waits, Workflow Jobs, and Workflow Job Runs. Its lifecycle records whether that objective is active, completed, or cancelled independently of any individual Job or Run. Completed and cancelled are terminal. Cancellation succeeds atomically only while every unfinished Job and Run remains safely cancelable; it cancels that work and revokes its execution authority, while completed work remains permanent history. If any External Effect has crossed its dispatch boundary or remains uncertain, cancellation is too late and changes nothing.
_Avoid_: Agent, conversation, run

**Workflow Kind**:
A recognized, versioned contract for one class of Workflow. It defines the allowed Workflow Job Kinds and graph rules and supplies the evidence-backed completion predicate for that durable business objective. A Workflow's human-readable objective is searchable context, not executable logic. A referenced Kind remains supported while active Workflows use it.
_Avoid_: Interpreted objective prompt, mutable workflow definition, arbitrary completion-condition data

**Workflow Input**:
The immutable, typed business data validated by a Workflow's versioned Workflow Kind when that Workflow is created. It provides structured objective context such as a renewal period without duplicating relational Participants or Organizations. It is neither executable workflow logic, a prompt, a completion condition, nor a mutable workflow definition.
_Avoid_: Parsed objective text, workflow prompt, duplicated Participant data, mutable workflow definition

**Workflow Completion Condition**:
The objective-specific, evidence-backed predicate that the Workflow Control Plane evaluates after relevant Workflow Job or evidence transitions. It represents satisfaction of the business objective, not an empty queue or a human override. Completion requires all completion-relevant work to be terminal and every relevant External Effect to be certain. A human may provide evidence, authorize replacement work, or cancel the Workflow, but may not bypass the predicate to declare it completed.
_Avoid_: Empty queue, manual completion override

**Workflow Participant**:
The association through which a Party participates in a particular Workflow. It holds that Party's Workflow Roles for the Workflow.
_Avoid_: Workflow user, owner

**Workflow Role**:
A Workflow Participant's relationship to a Workflow, limited in V0 to Broker, Reporter, Policyholder, and Claimant. A Workflow Participant may hold more than one Workflow Role; assignment and revocation affect current authority without erasing historical participation.
_Avoid_: Global user type

**Broker**:
A Workflow Role for a verified person Party authorized through an active Organization Membership to manage an assigned renewal or claim-intake Workflow on behalf of that organization. It grants workflow-scoped operational authority, not ownership of the Workflow or authority to adjudicate a claim.
_Avoid_: Workflow owner, global employee role

**Reporter**:
A Workflow Role for a Party that supplies initial notice or information about a reported incident. It does not imply that the Party is the Policyholder or Claimant.
_Avoid_: Initiator, unverified Claimant

**Policyholder**:
A Workflow Role for the Party that holds the policy relevant to a Workflow. It does not imply that the Party reported the incident or is the Claimant.
_Avoid_: Customer, Reporter

**Claimant**:
A Workflow Role for the Party whose claimed loss is represented in a claim-intake Workflow. It does not imply that the Party is the Policyholder or Reporter.
_Avoid_: Reporter, Policyholder

**Workflow Job**:
One durable, bounded unit of required work belonging to a Workflow that owns dependencies and retry policy, may have many Workflow Job Runs, and represents exactly one External Effect when it is side-effecting. While unfinished, it is waiting when a non-temporal prerequisite is unresolved, queued when those prerequisites are satisfied but its next eligible time may not have arrived, or running; it terminates as succeeded, failed, or cancelled.
_Avoid_: Agent, workflow

**Workflow Job Output**:
The single canonical result a Workflow Job publishes when its durable obligation succeeds. It normally comes from validated successful Run data, but authoritative reconciliation may publish it independently when that evidence satisfies the obligation. Failed or uncertain attempts publish no Workflow Job Output; a Draft Revision is the Workflow Job Output of a successful Draft Job.
_Avoid_: Provisional Run data, mutable result, output projector

**Workflow Job Kind**:
A recognized, versioned contract for one class of Workflow Job. It defines the persisted input, published output, Run Result data, and trusted execution semantics that the Workflow Control Plane and Worker resolve through the application-owned registry. Its version changes only for a breaking contract change, and a referenced Kind remains supported while unfinished Workflow Jobs use it. A Party may be authorized to propose some recognized Kinds but not others.
_Avoid_: Executor name, deployment version, model version, provider version

**Workflow Job Run**:
One isolated attempt to execute a Workflow Job. It carries the execution-specific worker, lease, timing, and result and may cross its Workflow Job's dispatch boundary at most once; after crossing, the External Effect is treated as possibly applied until an authoritative outcome is durably recorded from the attempt or through reconciliation. It is cancelled when the Workflow Control Plane deliberately revokes its Execution Authority before completion, while abandoned is reserved for Worker or lease loss.
_Avoid_: Execution Attempt, agent run

**Workflow Job Run Result**:
The typed, evidence-bearing final report produced for one Workflow Job Run by its Executor and Worker. Every Kind uses the same outcome, evidence, data, and error envelope while validating its own data contract. The outcome is succeeded when the obligation was satisfied, failed when the attempt ended with a known failure, or uncertain when an External Effect may have occurred but remains unresolved. Uncertain is never treated as probably failed. Deterministic application code validates the conclusion and evidence, applies the Workflow Job Kind's retry classification, and transitions the Run and Job; an Execution Agent may supply observations but never decides retry safety.
_Avoid_: Agent verdict, untyped completion message

**Execution Authority**:
The temporary, revocable permission held by one current Workflow Job Run to mutate its Run or cross its Workflow Job's dispatch boundary. Cancellation, completion, failure, or abandonment revokes it durably; interrupting the live Worker or Execution Agent afterward is best-effort cleanup and is never the authority boundary.
_Avoid_: Process liveness, agent ownership

**Workflow Control Plane**:
The deterministic application boundary that alone validates commands, grants or revokes Execution Authority, applies Workflow Job policy, and commits Workflow, Workflow Job, Workflow Job Run, and Workflow Event transitions. Parties, agents, Workers, adapters, reconcilers, and human reviewers submit commands, typed results, evidence, or decisions through this boundary rather than writing lifecycle state directly.
_Avoid_: Agent orchestration prompt, Worker-owned state

**Workflow Packet**:
A bounded, point-in-time projection of the durable Workflow facts needed by an Interaction Agent, Worker, or Execution Agent for one interaction or Workflow Job Run. It may be reconstructed after process loss and never grants authority or replaces the PostgreSQL record.
_Avoid_: Durable prompt memory, Workflow ownership, authority token

**External Effect**:
One logical, materially irreversible change outside OpenMagic requested by a side-effecting Workflow Job. Multiple independent External Effects require separate Workflow Jobs.
_Avoid_: Tool call, Workflow Job Run

**External Effect Evidence**:
A durable, typed observation used by the Workflow Control Plane to determine whether an External Effect was applied, was not applied, or remains uncertain. It preserves its source and relationship to the relevant Workflow Job Run; application policy interprets it, while agents never turn an unsupported judgment into evidence.
_Avoid_: Agent claim, unclassified tool output, retry decision

**Effect-Defining Input**:
The immutable data and artifact references that specify exactly one External Effect for a Workflow Job. A material change creates a new linked Workflow Job rather than mutating the existing input.
_Avoid_: Mutable job payload, approval summary

**Approval Grant**:
Immutable evidence that one identified and authorized Party explicitly authorized the exact External Effect represented by one immutable Workflow Job. It binds to that Job's complete Effect-Defining Input rather than its Workflow, any Workflow Job Run, or an integration provider's account permission; it never transfers to a replacement Job. It retains a typed Cause reference to the human message or UI action that expressed approval without duplicating that content. Its usability is derived: at most one invalidating fact may end it permanently before dispatch, while the durable dispatch-started event consumes it even when the provider outcome remains uncertain. Invalidation and dispatch serialize, so whichever commits first decides whether the External Effect may start. An input-fingerprint mismatch is an integrity failure that blocks dispatch rather than something a new approval can repair. Job failure alone does not invalidate the Grant, and the Grant remains historical evidence after invalidation or consumption.
_Avoid_: Workflow approval, Run approval, tool permission, agent consent

**Draft Revision**:
The single canonical, frozen content revision published when a Draft Workflow Job succeeds. A Draft Job may have multiple Workflow Job Runs and provisional Run outputs, but it publishes at most one Draft Revision; the producing Draft Job identifies that revision. Downstream Workflow Jobs reference the Draft Revision rather than any individual Run's provisional output.
_Avoid_: Mutable draft, provisional Run output

**Revision Job**:
A new immutable Workflow Job linked to an earlier Workflow Job after a material change to Effect-Defining Input. When the earlier Job is safely undispatched, the Revision Job replaces its obligation and the earlier Job is cancelled; otherwise it represents an additional External Effect and cannot erase or reverse the earlier one.
_Avoid_: Mutated job, overwritten job

**Correction Workflow**:
A new Workflow linked to a terminal Workflow when a Party chooses to pursue a distinct corrective business objective after an earlier External Effect is confirmed. Its Workflow Jobs create new External Effects rather than retrying or replacing completed work, and it never reopens or changes the original Workflow.
_Avoid_: Retry, Revision Job, reopened Workflow

**Reconciliation Job**:
An externally read-only but internally stateful and auditable Workflow Job that determines whether another Workflow Job's External Effect occurred after an ambiguous Run. Its relationship to the original Workflow Job is the authoritative representation of unresolved effect certainty.
_Avoid_: Retry, effect replay

**Worker**:
A replaceable process that claims and performs a Workflow Job Run. It does not own the Workflow Job or Workflow.
_Avoid_: Job owner, agent

**Executor**:
The trusted code a Worker uses to perform one Workflow Job Run, either a deterministic adapter or a fresh Execution Agent runtime selected by the Workflow Job Kind. It may perform Tool Calls and produce a Workflow Job Run Result, but it cannot commit lifecycle state.
_Avoid_: Worker, Workflow Job, caller-selected handler

**Execution Agent**:
An optional AI reasoning implementation used within a Workflow Job Run. It is neither the Worker nor the durable unit of work.
_Avoid_: Workflow, Workflow Job, Worker

**Tool Call**:
One operation an Executor performs during a Workflow Job Run, such as invoking Composio Gmail send. It is not the durable Workflow Job, the attempt, or the final Workflow Job Run Result; an externally irreversible Tool Call may cross the Workflow Job's External Effect dispatch boundary.
_Avoid_: Workflow Job, Workflow Job Run, External Effect

**Actor**:
The Party, System, or Workflow Job Run that performed the action recorded by a Workflow Event. An unknown Actor is reserved for incomplete legacy provenance, not normal automation.
_Avoid_: Initiator, cause

**Cause**:
The message, schedule, prior event, job, or other typed source that directly led to a Workflow Event.
_Avoid_: Actor, initiator

**Workflow Event**:
An immutable fact about a meaningful Workflow transition, decision, or outcome, recorded with separate typed Actor and Cause references.
_Avoid_: Log line, notification

**Notification**:
A durable delivery obligation created when a Workflow Event or due reminder must be communicated to a destination. It has delivery and acknowledgement state independent of the immutable fact that caused it. A Workflow Notification wakes a fresh Interaction Agent with durable identifiers so that agent reads a new Workflow Packet rather than receiving free-form execution output.
_Avoid_: Workflow Event, Trigger, agent result injection

**Trigger**:
A durable schedule that determines when a Notification or typed Workflow work should become due, including recurrence where applicable. It owns time and recurrence, not execution instructions, business work, or delivery state.
_Avoid_: Workflow Event, Notification, raw named-agent instruction
