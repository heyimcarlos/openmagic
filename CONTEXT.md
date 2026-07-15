# OpenMagic Workflow Domain

OpenMagic coordinates durable work across conversations, people, organizations, and replaceable execution processes. Application Packages supply the business vocabulary and rules. This glossary names the concepts that remain stable when applications, channels, providers, and agent runtimes change.

## Language

**OpenMagic Runtime**:
The reusable product that executes application-owned durable work through stable public interfaces. It includes the generic Workflow kernel and multi-Agent execution capabilities but contains no insurance, commerce, or other application-specific rules.
_Avoid_: Application Package, Reference Application, insurance runtime

**Application Package**:
The user-owned business definition built on the OpenMagic Runtime. It owns its business state, Commands, Workflow Definitions, qualified Policies, Domain Events, Executor configurations, tools, templates, and completion rules without changing the reusable runtime.
_Avoid_: OpenMagic Runtime, tenant, kernel plugin

**Reference Application**:
A named illustrative Application Package, such as Example Insurance or Example Commerce, used to demonstrate and prove reuse of the OpenMagic Runtime. Its business rules are examples rather than runtime contracts.
_Avoid_: Company A, production tenant, OpenMagic Runtime

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
One bounded contextual execution of an Agent against an exact Thread ID and immutable Agent Run Input. Every Agent Run reconstructs Thread Context through an immutable Message Sequence cutoff, while its separate durable identity does not create a new conversational continuity. An Agent Run started for Agent Delivery belongs to one Delivery Attempt and has no independent delivery authority.
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
A trusted, immutable typed request expressing intent to change application state or begin work. It carries an opaque Command ID, stable Command Type, separate positive schema version, typed Actor and Cause, and typed input with exact target identities where applicable; it contains no authorization claim, Policy decision, Workflow Definition structure, Route, Step kind, prompt, or kernel instruction.
_Avoid_: Operation Variant, event, prompt instruction

**Command ID**:
The caller-owned immutable identity of one Command and its application-level idempotency scope. Exact replay returns the original result, while conflicting reuse is rejected.
_Avoid_: Cause ID, Message ID, kernel operation ID

**Command Type**:
The stable lowercase dotted discriminator identifying the action requested by a Command, such as `renewal.request_revision`. Version information is not embedded in the Command Type.
_Avoid_: Operation Variant Type, Event Type, version-suffixed Command name

**Command Schema Version**:
The positive integer that pins one Command Type's complete typed input and Command Result compatibility contract. A breaking input or result change requires a new version, while implementation and Workflow Definition versions remain independent.
_Avoid_: Command Type suffix, deployment version, Workflow Definition Version

**Workflow Command**:
A Command submitted to the Workflow Control Plane. Not every Command targets a Workflow.
_Avoid_: Workflow Event, general Command Handler

**Command Handler**:
The deterministic application-owned behavior for one Command Type and schema version. It applies the relevant qualified Policies and prepares application and kernel changes, while the reusable application-configured Command Dispatcher owns idempotency and atomic commit.
_Avoid_: Agent tool, prompt router

**Command Result**:
The immutable application-owned typed outcome produced by a Command Handler. Its contract is pinned by the Command Type and schema version.
_Avoid_: Command Receipt, Domain Event, kernel receipt

**Command Receipt**:
The immutable acknowledgement that one Command Result committed atomically with its application changes, kernel transitions, and Domain Events. Exact Command replay returns the value-identical receipt without reevaluating the Command.
_Avoid_: Command Result, Domain Event, execution log

**Policy**:
A deterministic rule used to make an application decision and qualified by its owner or purpose, such as Workflow Policy, Delivery Policy, or Approval Policy.
_Avoid_: Global Policy, agent judgment

**Policy Decision**:
The typed output of evaluating a Policy. It becomes durable history only when committed as a Domain Event.
_Avoid_: Domain Event, agent verdict

**Verification Challenge**:
A durable, single-use request for a Party to prove current control of an on-file identifier through a second channel. It binds one Party, protected Thread, protected Workflow, purpose, exact waiting protected Command, and the current identifier's distinct delivery Thread. The code is delivered only to the identifier delivery Thread whose Channel Reference represents the on-file identifier, while successful protected-command resumption returns only to the protected Thread. The Challenge expires after 10 minutes and creates delivery through a typed side-effecting Step in a separate system Workflow. Verification delivery therefore cannot change the protected business Workflow's cancellation or completion semantics.
_Avoid_: Verification tool, global Party verification, Workflow authority grant

**Verification Session**:
The short-lived assurance established when a Verification Challenge succeeds. For 15 minutes, it proves control of the current on-file email for the same Party and Thread, so another protected Command does not require another code. Every protected Command still revalidates its own Workflow authority, lifecycle, and exact Approval Grant requirements. Expiry blocks new protected Commands, but it does not erase private facts already shown in that Thread.
_Avoid_: Login session, reusable approval, role assignment

**Workflow**:
A durable business objective that may span many Messages and kernel transitions. Every Workflow owns exactly one Instance for its lifetime, while the kernel has no dependency back to the Workflow; its active, completed, or cancelled business lifecycle remains independent of Instance execution state, with completed and cancelled terminal. Cancellation succeeds atomically only while unfinished work remains safely cancelable; it closes the Instance and revokes execution authority, while completed work remains permanent history. If any External Effect has crossed its dispatch boundary or remains uncertain, cancellation is too late and changes nothing.
_Avoid_: Agent, conversation, run

**Workflow Definition**:
An immutable, versioned, closed declarative transition system for one class of Workflow. Its identity is a stable Definition Key and Definition Version. Its canonical declarative manifest is durably registered with a content digest: registering the same identity and digest is idempotent, while the same identity with different content is an integrity failure. Registration validates the complete canonical manifest before the Definition becomes selectable, including unique keys, known references, one typed activation contract per Route, finite non-empty output batches, acyclic AND-only dependencies, schema-compatible bindings, compatible Wait and Signal contracts, and positive Step lease and maximum Attempt durations with the lease no longer than the maximum. Unknown fields, references, and constructs fail closed. An Instance is permanently pinned to one exact Definition identity when created and never upgrades in place. The Definition declares stable Step Templates, Wait Templates, and named Routes. Typed Commands, accepted Step outcomes, and Signals may activate runtime occurrences only through those predefined Routes. Business Policy authorizes and selects a Route; the kernel validates it against the Instance's pinned Definition version and atomically materializes the resulting finite occurrences. Callers, Agents, Workers, and Executors cannot invent templates, Step kinds, edges, or Routes. A Workflow's human-readable objective is searchable context, not executable logic.
_Avoid_: Workflow Kind, interpreted objective prompt, caller-provided graph, mutable definition, arbitrary durable Python

**Workflow Definition Identity**:
The immutable pair of a stable lowercase Definition Key and positive integer Definition Version pinned to an Instance for its complete lifetime. Version is a separate field and is never parsed from the Definition Key. Any manifest change creates a new version; deployment changes never alter the rulebook of an existing Instance. The registered manifest remains available while any Instance references it, and exact executable support remains installed while any referencing Instance is open. Missing support fails deployment readiness and runtime transitions closed; the kernel never falls back to a newer version or silently upgrades an Instance.
_Avoid_: Version-suffixed Kind name, latest Definition, deployment version, in-place Workflow upgrade

**Instance**:
One durable execution of an exact Workflow Definition Identity. Its kernel lifecycle is only open or closed: an open Instance may be active, waiting, retry-delayed, blocked, or quiescent, while closing atomically cancels its pending Steps, leased Attempts, and unsatisfied Waits; a closed Instance is terminal and accepts no further transitions, quiescence never closes it, and Business Policy separately owns the meaning of Workflow completion, cancellation, or failure.
_Avoid_: Workflow, business completion status, queue state, Agent Run

**Step Template**:
A stable key within one Workflow Definition that describes an allowed kind of Step occurrence and pins its typed contracts, Executor key, Retry Policy, renewable lease duration, and immutable maximum Attempt duration. A Route may materialize the same Step Template more than once, but each occurrence has its own durable identity.
_Avoid_: Runtime Step ID, caller-created node, loop body

**Step**:
One durable runtime occurrence of a Step Template with its own opaque Step ID and a lifecycle of pending, succeeded, failed, or cancelled. Pending covers every unresolved condition, while claimability is derived from the open Instance, exact AND-success dependencies, retry timing and budget, and absence of a current Attempt; terminal state is write-once and carries either one canonical typed success output or one canonical typed failure, never both, and only that accepted terminal outcome may activate a predefined Route. Route materialization is idempotent within an Instance by the unique source-scoped tuple of Route Key, activation source kind, activation source ID, and Route output slot, so replay returns the same Step while a later activation of the same Template creates a new occurrence.
_Avoid_: Step Template, sequence-derived identity, timestamp-derived identity, Workflow Job

**Attempt**:
One isolated execution of a Step with an opaque Attempt ID for exact authority identity and a strictly increasing, never-reused per-Step Attempt Number for ordering and fencing generation. It also records Worker provenance, lease, timing, and a write-once typed result, with a lifecycle of leased, completed, abandoned, or cancelled: completed means a valid result was accepted regardless of its reported outcome, abandoned is reserved for lease or Worker loss, cancelled records deliberate authority revocation, and every terminal state permanently ends execution authority.
_Avoid_: Agent Run, Delivery Attempt, result outcome, Workflow Job Run

**Lease**:
A bounded grant of execution authority held by one current Attempt until its renewable lease deadline or immutable hard execution deadline, whichever arrives first. Renewal is allowed only before expiry and never extends the hard deadline; expiry ends authority immediately without a grace period, while durable recovery subsequently records the Attempt as abandoned.
_Avoid_: Worker ownership, heartbeat, process liveness, execution result

**Retry Policy**:
The immutable, finite Attempt budget and exact delay schedule declared by a Step Template. It bounds and times retries that Business Policy has classified as safe, while the kernel never infers retry safety from an error, lost Worker, or provisional outcome.
_Avoid_: Business retry classification, unbounded retry, Worker backoff, in-process sleep

**Wait Template**:
A stable key within one Workflow Definition that describes an allowed kind of Wait occurrence.
_Avoid_: Timer callback, blocking Worker, arbitrary subscription

**Wait**:
A durable, non-executable occurrence of a Wait Template with its own opaque Wait ID and a lifecycle of unsatisfied, satisfied, or cancelled. It has no Executor, Attempt, lease, Retry Policy, business output, deadline, timer, or expiry scheduler; an accepted Signal may atomically satisfy it with permanent Signal provenance, while Instance closure cancels an unsatisfied Wait. Its replay-safe materialization identity uses the same Instance, Route, activation source, and output slot tuple as a Step, so replay returns the same Wait ID rather than creating another acceptance surface. A Wait may be satisfied only once by one Signal; that Signal may be consumed by only that Wait. Replay of the same Signal identity returns the recorded result, while another Signal cannot replace it. Every Signal targets one exact Wait ID; Business Policy performs domain correlation and authorization before submission. The kernel verifies the Wait's Instance and declared Signal contract but does not search by business data or buffer a Signal before its target Wait exists. Time-based progression occurs when Business Policy authorizes a typed Signal produced through Trigger; competing Signals serialize against the Wait and only the first valid one satisfies it.
_Avoid_: Step, dependency edge, sleeping Worker, business approval record

**Route**:
A stable, named transition declared by a Workflow Definition. A Route maps one allowed typed activation, such as a Command, accepted Step outcome, or Signal, to one finite batch of predefined Step or Wait occurrences. Every Definition declares exactly one `start` Route that may activate only while atomically creating an Instance: the same transaction pins the Definition identity, validates Instance Input, materializes the complete initial occurrence batch, and records its Trace Event. Replay returns the same Instance and occurrences, so no empty unstarted Instance can survive. Applying any Route is one atomic transition that validates the pinned Definition, activation source, typed input, bindings, and dependencies; satisfies a targeted Wait when applicable; materializes the complete finite occurrence batch; records concrete dependencies and a Trace Event; and commits all or nothing. One activation source may select at most one Route. Replay of the same source, Route, and input returns the same occurrences, while reuse of that source with conflicting Route or input is an integrity failure. Route bindings may copy only Definition literals, immutable Instance Input, typed Route input prepared by Business Policy, accepted Signal payload, or canonical output from an exact prerequisite Step. Computed transformations remain above the kernel. Conditional paths exist only as predefined Routes selected by Business Policy. The kernel does not evaluate callbacks, arbitrary predicates, payload expressions, general path languages, or OR dependency edges.
_Avoid_: Dynamic graph mutation, Executor-selected branch, arbitrary reducer action

**Instance Input**:
The immutable, typed data validated by an Instance's pinned Workflow Definition when that Instance is created. It provides structured objective context without duplicating relational application records and is neither executable logic, a prompt, a completion condition, nor a mutable Workflow Definition.
_Avoid_: Workflow Input, parsed objective text, Workflow prompt, duplicated application data

**Workflow Completion Condition**:
The objective-specific, evidence-backed predicate that the Workflow Control Plane evaluates after relevant Step or evidence transitions. It represents satisfaction of the business objective, not kernel quiescence or a human override. Completion requires all completion-relevant work to be terminal and every relevant External Effect to be certain. A human may provide evidence, authorize replacement work, or cancel the Workflow, but may not bypass the predicate to declare it completed.
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

**Execution Authority**:
The temporary, revocable permission held by one current leased Attempt to report its result or participate in a policy-owned External Effect fence. Attempt completion, abandonment, cancellation, lease expiry, hard-deadline expiry, or Instance closure ends it durably; interrupting the live Worker or Execution Agent afterward is best-effort cleanup and is never the authority boundary.
_Avoid_: Process liveness, agent ownership

**Workflow Control Plane**:
The deterministic application boundary that validates Workflow Commands, applies qualified Business Policies, and atomically coordinates policy-owned Workflow and Domain Event changes with kernel Instance, Step, Attempt, Wait, Signal, and Trace Event transitions. Parties, Agents, Workers, Executors, adapters, reconcilers, and human reviewers submit Commands, typed results, evidence, or decisions through this boundary rather than writing lifecycle state directly.
_Avoid_: Agent orchestration prompt, Worker-owned state

**External Effect**:
One logical, materially irreversible change outside OpenMagic requested by a side-effecting Step. Multiple independent External Effects require separate Step occurrences.
_Avoid_: Tool Call, Attempt

**External Effect Evidence**:
A durable, typed observation used by the Workflow Control Plane to determine whether an External Effect was applied, was not applied, or remains uncertain. It preserves its source and relationship to the relevant Step and Attempt; Business Policy interprets it, while Agents never turn an unsupported judgment into evidence.
_Avoid_: Agent claim, unclassified tool output, retry decision

**Effect-Defining Input**:
The immutable data and artifact references that specify exactly one External Effect for a Step. A material change activates a predefined Route that creates a new linked Step rather than mutating the existing input.
_Avoid_: Mutable Step input, approval summary

**Approval Grant**:
Immutable evidence that one identified and authorized Party explicitly authorized the exact External Effect represented by one immutable Step. It binds to that Step's complete Effect-Defining Input rather than its Workflow, any Attempt, or an integration provider's account permission; it never transfers to a replacement Step. It retains a typed Cause reference to the human Message or UI action that expressed approval without duplicating that content. Its usability is derived: at most one invalidating fact may end it permanently before dispatch, while the durable dispatch-started Domain Event consumes it even when the provider outcome remains uncertain. Invalidation and dispatch serialize, so whichever commits first decides whether the External Effect may start. An input-fingerprint mismatch is an integrity failure that blocks dispatch rather than something a new approval can repair. Step failure alone does not invalidate the Grant, and the Grant remains historical evidence after invalidation or consumption.
_Avoid_: Workflow approval, Attempt approval, Tool permission, Agent consent

**Draft Revision**:
The single canonical, frozen content revision published when a Draft Step succeeds. A Draft Step may have multiple Attempts and provisional results, but it publishes at most one Draft Revision; downstream Steps reference that revision rather than any individual Attempt result.
_Avoid_: Mutable draft, provisional Attempt result

**Revision Step**:
A new immutable Step occurrence created through a predefined revision Route after a material change to Effect-Defining Input. When the earlier Step is safely undispatched, the Revision Step replaces its obligation; otherwise it represents an additional External Effect and cannot erase or reverse the earlier one.
_Avoid_: Mutated Step, overwritten Step, Revision Job

**Correction Workflow**:
A new Workflow linked to a terminal Workflow when a Party chooses to pursue a distinct corrective business objective after an earlier External Effect is confirmed. Its Steps create new External Effects rather than retrying or replacing completed work, and it never reopens or changes the original Workflow or Instance.
_Avoid_: Retry, Revision Step, reopened Workflow

**Reconciliation Step**:
An externally read-only but internally stateful and auditable Step that determines whether another Step's External Effect occurred after an ambiguous Attempt result. Its policy-owned relationship to the original Step is the authoritative representation of unresolved effect certainty.
_Avoid_: Retry, effect replay, Reconciliation Job

**Worker**:
A replaceable process that claims and performs an Attempt. It does not own the Step, Instance, or Workflow.
_Avoid_: Step owner, Agent

**Executor**:
The trusted code a Worker uses to perform one Attempt, either a deterministic adapter or a fresh Execution Agent runtime selected by the pinned Step Template. It may perform Tool Calls and produce a typed Attempt result, but it cannot commit lifecycle state or select its own retry disposition.
_Avoid_: Worker, Step, caller-selected handler

**Execution Agent**:
An optional AI reasoning implementation used within an Attempt. It is neither the Worker nor the durable unit of work.
_Avoid_: Workflow, Step, Worker

**Tool Call**:
One operation an Executor performs during an Attempt, such as invoking Composio Gmail send. It is not the durable Step, Attempt, or final Attempt result; an externally irreversible Tool Call may cross the Step's policy-owned External Effect dispatch boundary.
_Avoid_: Step, Attempt, External Effect

**Actor**:
The Party, System, or authorized execution that performed the action recorded by a Domain Event. An unknown Actor is reserved for incomplete legacy provenance, not normal automation.
_Avoid_: Initiator, cause

**Cause**:
The Command, Message, Trigger, prior Domain Event, Step, or other typed source that directly led to a Domain Event.
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
An immutable, kernel-owned record of one successful state mutation, ordered by a strictly increasing Instance-local sequence and carrying its typed source identity, input digest, schema version, and committed receipt. Trace Events provide operational history and idempotent replay but are not the source of current state and carry no application-domain meaning.
_Avoid_: Domain Event, event-sourced state, timestamp order, Message

**Signal**:
An accepted, durable, typed kernel correlation record that targets and satisfies one exact Wait. It is persisted atomically with Wait satisfaction and predefined Route materialization; exact Signal identity replays the recorded result, conflicting reuse fails, and rejected or early input is never buffered as a Signal.
_Avoid_: Domain Event, Delivery, Message, pending subscription

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
