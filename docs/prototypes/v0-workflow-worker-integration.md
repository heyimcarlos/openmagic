# V0 Workflow tools and Worker integration prototype

## Question

Can the smallest OpenMagic integration replace direct named-agent delegation
with typed Workflow tools and disposable agent runtimes, while keeping all
waiting ownership in durable Workflow state?

## Run

```bash
python server/services/workflows/prototype_v0.py --auto
```

The command executes one renewal-email happy path and prints the complete
relevant in-memory state after every action.

## What the prototype makes concrete

```text
Fresh Interaction Agent
  -> search_workflows
  -> read_workflow_packet
  -> propose typed Draft and Send Jobs
  -> exits

Worker
  -> claims one eligible Job and creates one Run
  -> resolves the Job Kind contract
  -> creates a fresh Execution Agent only for a Draft Run
  -> reports a typed Run Result
  -> discards Run-scoped context

Workflow Event
  -> creates a Notification in the same Control Plane action

Notification Worker
  -> claims one Notification
  -> starts a fresh Interaction Agent with identifiers
  -> fresh agent reads a new Workflow Packet
  -> emits the user-facing update
  -> exits
```

The Send Job uses a deterministic adapter, not an Execution Agent. The adapter
is called only after the Control Plane records dispatch and rejects a second
call for the same logical Job.

## Reference convergence

- Deep Agents has a central construction seam that assembles model, tools,
  middleware, and runtime separately. OpenMagic should likewise centralize Job
  Kind to Executor resolution rather than let callers choose handlers. See
  `.reference/deepagents/libs/ARCHITECTURE.md` and
  `.reference/deepagents/libs/deepagents/deepagents/graph.py`.
- Open SWE routes different external triggers through one durable run-dispatch
  contract and consumes queued follow-up facts at a runtime seam. OpenMagic
  adopts the single dispatch interface but replaces thread-owned business state
  with Workflow Packets reconstructed from PostgreSQL. See
  `.reference/open-swe/agent/dispatch.py` and
  `.reference/open-swe/agent/middleware/check_message_queue.py`.
- Open SWE binds approval to a fingerprinted change rather than a broad thread.
  This reinforces OpenMagic's exact Job and effect fingerprint rule. See
  `.reference/open-swe/agent/dashboard/workflow_approval.py`.
- Cloudflare Think makes an agent itself a durable actor with local storage.
  That is clean for its product model, but it is the wrong ownership seam for
  OpenMagic because the locked domain says a Workflow, not an agent, owns the
  business objective. See
  `.reference/agents/think-starters/business-workflow/start.md`.

## Prototype verdict

The smallest credible implementation needs three deep modules:

1. **Workflow tools module:** authorization-scoped search and packet reads plus
   typed commands. It never exposes handlers or lifecycle writes.
2. **Workflow Control Plane module:** the only transition authority, hiding
   transactions, registries, graph validation, events, and notifications behind
   commands.
3. **Worker runtime module:** one Job claim, one Run execution, one registry
   lookup, and one typed result submission. Agent construction is private to the
   Run executor.

Notification delivery is a separate queue consumer. It receives identifiers,
not formatted execution output, and creates a new Interaction Agent turn that
loads a fresh Packet.

The printed `live_runtimes` state is empty after every action. This is the
observable proof that neither Interaction Agent nor Execution Agent owns the
Workflow while it waits.

## Deliberate omissions

This throwaway artifact does not model PostgreSQL transactions, leases, retry
or uncertainty branches, cancellation, revision, search ranking, browser UI,
or live Composio. Those behaviors are already specified or belong to the next
evaluation prototype. The artifact exists only to validate the integration
shape and implementation-ticket boundaries.
