# Workflow retrieval implementation comparables

Date: 2026-07-12

This note records the implementation convergence for
[Resolve one Workflow and propose typed renewal work](https://github.com/heyimcarlos/openmagic/issues/18).

## Reference revisions

- LangChain at `a8fd0da2b7c3409db9a16d0c7bcd55463967351b`.
- Deep Agents at `14f384fc0083c07a7f44f97543b40b74cf93c13f`.
- Open SWE at `30832d29bcfa12c5669c374add585e8b829a8ac2`.
- Prefect at `0e7435055e18952aa8604dab78507b087a18defb`.

## Convergence

### Hidden authority context

LangChain keeps runtime-injected arguments in full validation while removing
them from the model-visible tool schema. OpenMagic follows that boundary:
search and packet requests contain business filters or one Workflow ID, while
the authenticated Party context is supplied by application composition.

Relevant LangChain sources:

- `libs/core/langchain_core/tools/base.py:670`
- `libs/core/langchain_core/tools/base.py:1489`
- `libs/core/tests/unit_tests/test_tools.py:1933`

### Authorization-first candidate relation

Deep Agents removes inaccessible paths before formatting search results and
tests that denied paths never appear. OpenMagic starts every search query from
an authorization-scoped PostgreSQL relation. Filters, ranking, exact counts,
facets, match explanations, and pagination consume only that relation.

Relevant Deep Agents sources:

- `libs/deepagents/deepagents/middleware/filesystem.py:396`
- `libs/deepagents/deepagents/middleware/filesystem.py:446`
- `libs/deepagents/tests/unit_tests/test_permissions.py:1193`

### Bounded peripheral vision

Deep Agents bounds search output while making truncation explicit. Prefect
binds page tokens to the original typed filter. OpenMagic returns exact
authorized cardinality, bounded facets with their own truncation flags, and an
opaque keyset cursor bound to the normalized request.

Relevant sources:

- Deep Agents `libs/deepagents/deepagents/middleware/filesystem.py:460`
- Deep Agents `libs/deepagents/deepagents/middleware/filesystem.py:527`
- Prefect `src/prefect/server/events/storage/database.py:47`
- Prefect `src/prefect/client/schemas/events.py:12`

The OpenMagic cursor carries a contract version, normalized-request digest,
and deterministic ordering tuple. It is authenticated rather than merely
base64-encoded.

### Explicit packet projection

Deep Agents preserves a bounded operational window rather than forwarding all
state. OpenMagic builds `WorkflowPacketV1` explicitly. It never serializes ORM
rows wholesale. The packet contains the complete small V0 Job graph and
Participants, one bounded latest-Run summary per Job, derived approval and
dispatch summaries, and at most 20 recent interaction-relevant Events.

### Fresh authorization

Open SWE verifies that access is resolved again after revocation. Search
results and Workflow Packets are point-in-time context, not authority tokens.
Packet reads and later proposals independently revalidate current authority.

Relevant Open SWE source:

- `tests/dashboard/test_dashboard_reviews.py:91`

## Relational V0 identity surface

Prior Wayfinder decisions require Party, Organization Membership, Workflow
Participant, and Workflow Role data to remain relational. Issue 18 therefore
adds only:

```text
parties
party_identifiers
organization_memberships
workflow_participants
workflow_participant_roles
workflows.organization_party_id
```

An organization is a Party, not a separate entity. Current Broker inspect
authority requires a current verified Party Identifier, an active Broker role
on the Workflow, and an active membership in that Workflow's organization.
This predicate defines the candidate relation before any search operation.

The creation Event retains its organization reference as historical evidence,
but it stops being authoritative current state. Provisional Party resolution,
verification challenges, role-management commands, FNOL identity behavior,
KYC, and generalized IAM remain out of scope.

## Interaction runtime seam

The inherited interaction runtime has synchronous global tools and injects the
named-agent roster into every prompt. PostgreSQL Workflow operations are async.
Issue 18 introduces an async toolbox seam with two explicit profiles:

- A legacy toolbox and prompt preserve the controlled named-agent baseline.
- A workflow toolbox exposes search, packet read, typed renewal proposal,
  user response, and wait. It omits the roster and direct delegation tool.

Tool logging records names, identifiers, counts, and outcomes, not complete
Workflow Packets or email content.

## Rejected patterns

- Accessibility filtering after broad retrieval, which can leak through
  counts, facets, explanations, or pagination.
- Client-side facets computed from the displayed page.
- Unsigned cursors or offset pagination.
- ORM serialization, raw provider responses, credentials, full Run payloads,
  full Event history, or complete message content in Workflow Packets.
- Model-provided Party identity, authorization scope, sort expressions,
  ranking weights, lifecycle state, prompts, handlers, or executor selection.
- Loading several Workflow Packets before resolving one intended Workflow.
