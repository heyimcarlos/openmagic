---
status: accepted
---

# Use Thread-correlated Domain Event Delivery above the durable kernel

OpenMagic models each external conversation as a separate Thread with exactly one immutable Channel Reference. Qualified application Policies atomically commit Domain Events and Deliveries above the durable workflow kernel; Template Delivery is deterministic, Agent Delivery is a restricted contextual Agent Run against an exact Thread cutoff, and Message append is acknowledged atomically through a leased Delivery Attempt. This replaces Party-latest routing, context-free Notification agents, and the inherited mixture of business facts with execution history because exact correlation, deterministic recovery, and a clean kernel seam matter more than preserving hobby-project compatibility.

## Consequences

- Cross-channel continuity belongs to Party and Workflow, not Thread.
- Domain Event, Delivery, Thread, Message, Delivery Attempt, and Agent Run keep distinct durable identities.
- The kernel owns only Definitions, Instances, Steps, Attempts, Signals, Waits, leases, Retry Policy, and Trace Events.
- Migration is a deletion-first cutover with no legacy backfill, dual writes, or compatibility aliases.
