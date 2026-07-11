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
A durable business objective that may span many messages, waits, Workflow Jobs, and Workflow Job Runs.
_Avoid_: Agent, conversation, run

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
One durable, bounded unit of required work belonging to a Workflow. It owns dependencies and retry policy, may have many Workflow Job Runs, and represents exactly one External Effect when it is side-effecting.
_Avoid_: Agent, workflow

**Workflow Job Run**:
One isolated attempt to execute a Workflow Job. It carries the execution-specific worker, lease, timing, and result, and may dispatch its Workflow Job's External Effect at most once.
_Avoid_: Execution Attempt, agent run

**External Effect**:
One logical, materially irreversible change outside OpenMagic requested by a side-effecting Workflow Job. Multiple independent External Effects require separate Workflow Jobs.
_Avoid_: Tool call, Workflow Job Run

**Effect-Defining Input**:
The immutable data and artifact references that specify exactly one External Effect for a Workflow Job. A material change creates a new linked Workflow Job rather than mutating the existing input.
_Avoid_: Mutable job payload, approval summary

**Revision Job**:
A new immutable Workflow Job linked to an earlier Workflow Job after a material change to Effect-Defining Input. It preserves the earlier Workflow Job as history and never implies that an earlier External Effect was erased or reversed.
_Avoid_: Mutated job, overwritten job

**Reconciliation Job**:
An externally read-only but internally stateful and auditable Workflow Job that determines whether another Workflow Job's External Effect occurred after an ambiguous Run. Its relationship to the original Workflow Job is the authoritative representation of unresolved effect certainty.
_Avoid_: Retry, effect replay

**Worker**:
A replaceable process that claims and performs a Workflow Job Run. It does not own the Workflow Job or Workflow.
_Avoid_: Job owner, agent

**Execution Agent**:
An optional AI reasoning implementation used within a Workflow Job Run. It is neither the Worker nor the durable unit of work.
_Avoid_: Workflow, Workflow Job, Worker

**Actor**:
The Party, System, or Workflow Job Run that performed the action recorded by a Workflow Event. An unknown Actor is reserved for incomplete legacy provenance, not normal automation.
_Avoid_: Initiator, cause

**Cause**:
The message, schedule, prior event, job, or other typed source that directly led to a Workflow Event.
_Avoid_: Actor, initiator

**Workflow Event**:
An immutable fact about a meaningful Workflow transition, decision, or outcome, recorded with separate typed Actor and Cause references.
_Avoid_: Log line, notification
