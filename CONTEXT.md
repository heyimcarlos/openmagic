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

**Thread**:
A durable, ordered conversation continuity whose Messages may come from Parties, Systems, Templates, or Agent Runs. It has exactly one immutable Channel Reference and grants no identity proof, Workflow authority, approval, or execution authority.
_Avoid_: Interaction, account session, global login, authorization role

**OpenMagic Thread**:
The product-facing name for a Thread. Internal domain contracts use Thread.
_Avoid_: Chat session, Interaction

**Thread ID**:
The immutable identity that correlates Messages, Deliveries, Agent Runs, Verification Challenges, and one Channel Reference to a Thread.
_Avoid_: Interaction ID, latest conversation, Party ID

**Channel Reference**:
The immutable typed reference to the one external conversation represented by a Thread, such as an SMS conversation or Slack thread. Different external conversations create different Threads even when they resolve to the same Party or concern the same Workflow.
_Avoid_: Channel Binding, Thread identity, Party Identifier

**Message**:
An immutable user-visible item appended to a Thread with an independent Message ID, typed Message Author, Message Sequence, content, Message Source, and descriptive creation timestamp.
_Avoid_: Event, Notification, mutable chat entry

**Message ID**:
The immutable identity of one Message, independent of its Thread, Delivery, Agent Run, or external channel identity.
_Avoid_: Delivery ID, Agent Run ID, sequence number

**Message Sequence**:
The Thread-local number assigned atomically when a Message is appended and used as the authoritative order within that Thread. A Message timestamp is descriptive and does not determine order.
_Avoid_: Timestamp order, global message order

**Message Author**:
The typed origin of a Message, such as a Party, Agent, or System.
_Avoid_: Actor, sender string

**Message Source**:
The typed stable identity that caused one Message append and provides its idempotency scope. Source kinds are `channel`, `delivery`, `agent_run`, and `system`; a Message produced for an Agent Delivery uses the Delivery as its source while the Agent Run remains execution provenance.
_Avoid_: Message Author, timestamp, correlation ID

**Thread Context**:
Reconstructed Thread history supplied to an Agent Run from the exact Thread through an immutable Message Sequence cutoff. It excludes later Messages, other Threads, process memory, and mutable summaries, and it grants no authority.
_Avoid_: Prompt memory, Workflow Packet, authority token

**Agent**:
A configured reasoning component distinct from its executions, runtime process, and Threads.
_Avoid_: Agent Run, Agent Runtime, Thread

**Conversation Agent**:
The user-facing Agent role that reasons within a Thread.
_Avoid_: Interaction Agent, Workflow, Thread owner

**Agent Runtime**:
The disposable in-process implementation that executes an Agent. It has no durable identity or authority of its own.
_Avoid_: Agent, Agent Run, Worker

**Agent Run**:
One bounded contextual execution of an Agent against an exact Thread ID and immutable Agent Run Input. It has durable identity and belongs to one Delivery Attempt, but it has no independent delivery authority.
_Avoid_: Run, Workflow Job Run, kernel Attempt

**Agent Run ID**:
The durable identity of one Agent Run, distinct from Delivery ID and Delivery Attempt identity.
_Avoid_: Run ID, Delivery Attempt ID, runtime instance ID

**Agent Run Input**:
Immutable typed input containing a versioned Agent and typed task, exact Thread ID and Thread Context cutoff, bounded Domain Event context, audience context, and locale. Delivery Policy creates it without free-form prompts or unrestricted database access.
_Avoid_: Prompt, Event Payload, mutable context

**Agent Run Result**:
The typed result of one Agent Run. For Agent Delivery it is candidate Message content that only the current Delivery Attempt may append.
_Avoid_: Message, Workflow Job Run Result, free-form agent output

**Command**:
A typed request expressing intent to change application state or begin work.
_Avoid_: Operation Variant, event, prompt instruction

**Command Type**:
The stable discriminator identifying the action requested by a Command.
_Avoid_: Operation Variant Type, Event Type

**Workflow Command**:
A Command submitted to the Workflow Control Plane. Not every Command targets a Workflow.
_Avoid_: Workflow Event, general Command Handler

**Command Handler**:
The deterministic application boundary that validates a Command, applies the relevant Policies, and commits the result.
_Avoid_: Agent tool, prompt router

**Policy**:
A deterministic rule used to make an application decision and qualified by its owner or purpose, such as Workflow Policy, Delivery Policy, or Approval Policy.
_Avoid_: Global Policy, agent judgment

**Policy Decision**:
The typed output of evaluating a Policy. It becomes durable history only when committed as a Domain Event.
_Avoid_: Domain Event, agent verdict

**Verification Challenge**:
A durable, single-use request for a Party to prove current control of an on-file identifier through a second channel. It binds one Party, Thread, protected Workflow, purpose, and exact waiting protected Command, expires after 10 minutes, and creates delivery through a typed External Effect Job in a separate system Workflow. Verification delivery therefore cannot change the protected business Workflow's cancellation or completion semantics.
_Avoid_: Verification tool, global Party verification, Workflow authority grant

**Verification Session**:
The short-lived assurance established when a Verification Challenge succeeds. For 15 minutes, it proves control of the current on-file email for the same Party and Thread, so another protected Command does not require another code. Every protected Command still revalidates its own Workflow authority, lifecycle, and exact Approval Grant requirements. Expiry blocks new protected Commands, but it does not erase private facts already shown in that Thread.
_Avoid_: Login session, reusable approval, role assignment

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
The deterministic application boundary that alone validates Workflow Commands, grants or revokes Execution Authority, applies Workflow Policies, and commits Workflow, Workflow Job, Workflow Job Run, and Domain Event transitions. Parties, Agents, Workers, adapters, reconcilers, and human reviewers submit Commands, typed results, evidence, or decisions through this boundary rather than writing lifecycle state directly.
_Avoid_: Agent orchestration prompt, Worker-owned state

**Workflow Packet**:
A bounded, point-in-time projection of the durable Workflow facts needed by a Conversation Agent, Worker, or Execution Agent for one Agent Run or Workflow Job Run. It may be reconstructed after process loss and never grants authority or replaces the PostgreSQL record.
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
The Party, System, or authorized execution that performed the action recorded by a Domain Event. An unknown Actor is reserved for incomplete legacy provenance, not normal automation.
_Avoid_: Initiator, cause

**Cause**:
The Command, Message, schedule, prior Domain Event, job, or other typed source that directly led to a Domain Event.
_Avoid_: Actor, initiator

**Domain Event**:
An immutable, policy-owned fact about something meaningful that happened in the application domain, recorded with a typed Event Envelope and Event Payload.
_Avoid_: Business Event, Workflow Event, Trace Event, log line, Delivery

**Event Type**:
A stable lowercase dotted identifier describing a completed fact, such as `renewal.draft.ready`. Payload versioning is not part of the Event Type.
_Avoid_: Command Type, version-suffixed event name

**Event Envelope**:
The common immutable metadata for a Domain Event, including event ID, Event Type, Event Schema Version, timestamp, optional Workflow ID, Actor, Cause, and Event Payload.
_Avoid_: Event Payload, rendered Message

**Event Payload**:
The immutable, typed facts specific to one Event Type. It contains neither prose prompts nor rendered Messages.
_Avoid_: Agent Run Input, Template Input, untyped data bag

**Event Schema Version**:
The integer identifying the compatibility contract of an Event Payload independently of its Event Type. Breaking payload changes increment it, unknown versions are rejected, and referenced historical versions remain readable.
_Avoid_: Event Type suffix, deployment version

**Trace Event**:
Kernel-owned operational history about execution mechanics. It carries no application-domain meaning.
_Avoid_: Domain Event, audit log, Message

**Signal**:
A kernel wake-up or correlation primitive.
_Avoid_: Domain Event, Delivery, Message

**Delivery**:
A durable obligation to communicate one Domain Event to an exact Delivery Destination by appending at most one Message. Delivery ID and Message ID remain distinct; multiple Messages require separate Deliveries.
_Avoid_: Notification, Domain Event, Trigger

**Suppressed Delivery**:
A terminal Delivery whose Message was intentionally withheld because Delivery Policy confirmed that its Audience was no longer authorized. It is neither delivered nor an operational failure, cannot retry or revive, and creates no Delivery Acknowledgement.
_Avoid_: Failed Delivery, cancelled Message, temporary authorization error

**Delivery Destination**:
The typed destination for Delivery content. A conversation Delivery contains one exact immutable Thread ID.
_Avoid_: Audience, latest conversation, Party ID

**Audience**:
The intended Party or recipient role for a Delivery, distinct from its Delivery Destination.
_Avoid_: Delivery Destination, Thread ID

**Content Mode**:
The Delivery Policy-selected method for producing content, either `template` or `agent`.
_Avoid_: Presentation Mode, deterministic mode, contextual mode

**Template Delivery**:
A Delivery that freezes a Template key, separate version, locale, and immutable typed Template Input. Its Message content is produced without contextual reasoning or an LLM.
_Avoid_: Deterministic Message, Agent Delivery

**Agent Delivery**:
A Delivery permitted only when producing its Message requires prior Thread meaning that no suitable Template can express. Its restricted Conversation Agent Run may produce one candidate Message but cannot select destination or Audience, make domain decisions, submit Commands, or perform External Effects.
_Avoid_: Template Delivery, context-free agent result

**Template**:
An immutable, version-addressed specification for deterministic Message generation whose referenced version remains available while a Delivery can execute.
_Avoid_: Prompt, mutable formatter

**Template Input**:
The typed immutable facts supplied to a Template Renderer for one Template Delivery.
_Avoid_: Event Payload, Agent Run Input, mutable Workflow projection

**Template Renderer**:
The pure deterministic function that produces Message content from a Template and Template Input.
_Avoid_: LLM, Conversation Agent

**Contextual Reasoning**:
Delivery Policy-authorized use of an Agent Run after rehydrating the exact destination Thread.
_Avoid_: Default rendering, context-free agent invocation

**Delivery Attempt**:
A durably identified, leased attempt to complete a Delivery and the fence for its Worker's authority. At most one Attempt is running for a Delivery; lease loss abandons it, stale results are rejected, and retries create new Delivery Attempts rather than new Deliveries.
_Avoid_: Delivery, Workflow Job Run, kernel Attempt

**Delivery Retry Policy**:
The immutable versioned policy that gives a Delivery its finite attempt budget, backoff schedule, and deterministic classification of typed failures as retryable or terminal. Every claimed Attempt consumes the budget, while Workers and Agents never choose retry safety.
_Avoid_: Agent retry judgment, mutable retry settings, infinite retry

**Delivery Worker**:
A disposable Worker that claims and performs Delivery Attempts without owning the Delivery or Thread.
_Avoid_: Notification Worker, Conversation Agent

**Delivery Acknowledgement**:
The atomic transition that appends the intended Message, succeeds the current Delivery Attempt, and marks the Delivery delivered with the resulting Message identity. It is idempotent for the successful Attempt and does not prove that a human read the Message.
_Avoid_: Recipient Acknowledgement, delivery claim

**Recipient Acknowledgement**:
Optional later evidence that a recipient read, accepted, or responded to a Message, distinct from Delivery Acknowledgement. It does not control Delivery retries, acknowledgement, or completion.
_Avoid_: Delivery Acknowledgement

**Trigger**:
A durable schedule that determines when a Delivery or typed Workflow work should become due, including recurrence where applicable. It owns time and recurrence, not execution instructions, business work, or delivery state.
_Avoid_: Domain Event, Delivery, raw named-agent instruction
