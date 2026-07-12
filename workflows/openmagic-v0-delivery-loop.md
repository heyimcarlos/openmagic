# OpenMagic V0 Delivery Loop

**Status:** Active

## Purpose

Turn the remaining Wayfinder prototype decisions into a working, tested OpenMagic
V0 through small implementation tickets and one reviewable pull request per
ticket. Preserve the Wayfinder map as the decision record and implementation
handoff rather than expanding it into a delivery tracker.

## Trigger

The loop advances when either:

- The next unclaimed Wayfinder prototype ticket reaches the frontier.
- An approved implementation ticket reaches the delivery frontier because all
  of its blockers are complete.
- Review or active testing returns a pull request for correction.

## Execution mechanism

This checked-in Workflow is the durable, inspectable operating specification.
A persistent Codex goal is the autonomous runner that executes it.

The runner reloads this file, the current ticket, and its linked decision
evidence at the start of every cycle. It does not treat prior prompt context as
authoritative. It preserves the Wayfinder rule of resolving at most one
Wayfinder ticket per session, then uses fresh implementation contexts to work
the ordinary delivery-ticket frontier one pull request at a time.

The initial frontier is the Wayfinder prototype ticket **Prototype V0 workflow
tools, packets, and Worker integration**. Later cycles proceed through the
paired evaluation and recovery harness and the five-minute walkthrough only
when their blockers are complete.

## Phase 1: Resolve a Wayfinder prototype ticket

1. Load the map at low resolution and claim exactly one frontier ticket.
2. Load the locked decisions relevant to that ticket.
3. Produce the smallest concrete artifact needed for informed human reaction.
4. Resolve remaining decisions with the user, one question at a time.
5. Convert the approved result into ordinary implementation tickets.
6. Make every implementation ticket a narrow, complete, demonstrable vertical
   slice with explicit acceptance criteria and native blocking edges.
7. Create each implementation ticket as a child of the Wayfinder prototype
   ticket that produced it. Use native blocking edges across prototype groups
   when delivery order crosses those groups.
8. Add a decision-provenance section linking directly to the governing
   Wayfinder resolution, grilling evidence, prototype assets, and any earlier
   decision tickets whose invariants the implementation must preserve.
9. Record and close the Wayfinder ticket, then update the map with a short
   resolution pointer.

The Wayfinder session decides and decomposes the work. It does not silently turn
into an unbounded implementation session.

The inherited OpenPoke implementation is the controlled baseline, not a
compatibility contract. An implementation ticket may replace, reorganize, or
delete inherited code when its investigation, trusted comparables, acceptance
evidence, and linked Wayfinder decisions support the change. Preserve baseline
behavior only where the ticket or evaluation comparison requires it.

## Phase 2: Deliver one implementation ticket

1. Claim one unblocked implementation ticket.
2. Create a focused branch and pull request for that ticket only.
3. Recover the ticket's governing Wayfinder decisions and acceptance criteria.
4. Inspect the existing OpenMagic code before choosing a new structure.
5. Produce a focused reference brief that maps every analogous concern in the
   ticket to relevant implementations in the trusted reference repositories.
6. Pull and inspect the selected implementations, including their tests,
   naming, typing, contracts, errors, module boundaries, and documentation.
7. Record the observed convergence, meaningful differences, and why the chosen
   shape fits OpenMagic.
8. Add executable acceptance coverage before or alongside the implementation.
9. Implement the smallest end-to-end behavior that satisfies the ticket.
10. Run focused tests, broader regression tests, and static quality checks.
11. Start OpenMagic and actively test the behavior through the browser or
    conversational surface.
12. Inspect the durable PostgreSQL trace where lifecycle behavior is involved.
13. Open the pull request and request the available GitHub Copilot and Codex
    reviewers supported by the repository.
14. Run independent standards, specification, and adversarial reviews.
15. Babysit checks and review delivery until every required signal is terminal.
16. Correct actionable findings, resolve review threads, repeat affected tests
    and active QA, then request or perform re-review.
17. Merge autonomously only after the ticket is green, all blocking findings
    are resolved, and the evidence demonstrates its
    acceptance criteria.
18. Smoke-test the merged result on the integration branch.
19. Close the implementation ticket and advance to the next unblocked ticket.

No implementation ticket owns more than one pull request unless review exposes
a necessary correction to that same ticket.

## Pull request review protocol

Every pull request receives separate review signals:

1. **Standards review:** compare the diff with repository rules, established
   OpenMagic conventions, and maintainability smells.
2. **Specification review:** compare the diff with the implementation ticket,
   its acceptance criteria, and linked Wayfinder decisions.
3. **Adversarial review:** try to break the change through lifecycle races,
   stale authority, restart, duplication, ordering, security, malformed input,
   and missing negative tests relevant to the ticket.
4. **Hosted review:** request the GitHub Copilot and Codex reviewers that the
   repository makes available.

The three internal axes use independent reviewer contexts so one framing does
not hide another. Use the strongest separately configurable reviewer model
available. If the runtime does not expose model selection, record the actual
review mechanism rather than claiming a named model was used.

Review is iterative. New commits invalidate earlier confidence in the affected
surface, so the loop reruns relevant tests and reviews after corrections. A pull
request is mergeable only when required checks pass, blocking review threads
are resolved, no critical internal finding remains, and the branch is current
with its required base.

## Comparable-code discipline

Reference work is a required learning step for every implementation ticket. The
agent must inspect how the trusted projects express each comparable concern,
including ordinary Python structure and naming as well as module boundaries,
public interfaces, persistence protocols, concurrency, agent and tool
contracts, test harnesses, and error modeling.

For each comparable concern:

1. State the OpenMagic constraint being solved.
2. Inspect one to three relevant trusted implementations.
3. Identify their convergence and the constraints behind their differences.
4. Choose the cleanest design that preserves OpenMagic's locked invariants and
   matches this repository's actual constraints.
5. Record the lesson, choice, and rejected alternative in the reference brief
   and pull request when it is not obvious from the code.

Prefer domain-specific boundaries and typed contracts over generic helpers that
merely rename weak runtime checks. Extract shared behavior when the behavior is
actually shared or when a locked domain invariant requires one authority.

Potential references currently include Composio, Deep Agents, LangChain,
Open SWE, Cloudflare Agents, Restate, Temporal, Camunda, and Agent Protocol.
Additional trusted repositories may be cloned into `.reference` when a ticket
has a concrete unanswered comparison question.

## Required evidence per implementation ticket

- Automated acceptance and regression results.
- Browser or conversational QA for user-visible behavior.
- Durable-state evidence for Workflow, Job, Run, Event, Approval, dispatch, or
  Notification behavior touched by the ticket.
- Pull request review against both repository standards and ticket intent.
- A concise reference brief linking the comparable implementations studied and
  explaining how they influenced the resulting code.

## Integration testing policy

Follow Executor's separation between the real product topology, deterministic
provider-boundary control, and narrow live-provider smoke tests.

The primary E2E path runs OpenMagic's real FastAPI service, PostgreSQL control
plane, Worker, agent and adapter contracts, browser surface, and public tool
interfaces. It must not mock internal Control Plane or Worker calls.

PR gates use the deterministic Composio fake through the same adapter contract
as the live integration. The fake is stateful and inspectable: it records the
requested effect, bounds each injected fault, and can deterministically produce
success, known failure, delay, connection loss, crash boundaries, and uncertain
outcomes. It exists to exercise safety branches that cannot be forced reliably
against Gmail without risking duplicates. It is not evidence that live Gmail
works.

Every pull request that changes the email path also runs one credentialed live
smoke journey:

1. Create a uniquely correlated renewal Workflow and approved Send Job.
2. Send through the real Composio Gmail adapter with automatic retry disabled.
3. Deliver to the authorized disposable AgentMail inbox.
4. Assert the successful adapter response and resulting OpenMagic durable
   state.
5. Independently observe the matching message in the recipient inbox.
6. Exercise the browser or conversational acknowledgement surface.
7. Preserve the Workflow trace and relevant browser evidence for review.

The live journey proves the current provider happy path. The deterministic path
proves failure handling and dispatch safety. Neither substitutes for the other.
Browser inspection of Gmail Sent may supplement the evidence, but recipient
observation is the stronger delivery signal.

Use unique correlation values and scoped test identities so repeated runs do
not depend on global mailbox counts. Credentials remain in ignored local
environment files or repository secret storage and must never appear in source,
test artifacts, logs, issue comments, or pull requests.

## Checkpoints

The loop should perform all discoverable and reversible work before asking the
user. It pauses only for a decision that is not already settled by the map,
missing access, an external side effect outside an approved test boundary, or
a material prototype choice for which the evidence supports more than one
reasonable route. It does not pause for ceremonial approval when the locked
decisions and prototype evidence already determine the implementation handoff.
