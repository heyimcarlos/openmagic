# Agent tool verification gates

Accessed 2026-07-12.

## Question

Is a model-visible, typed `request_verification` tool a standardized or common
way to protect sensitive agent operations? How do LangChain and LangGraph,
OpenAI Agents SDK, Vercel AI SDK, Cloudflare Agents, and OAuth step-up
authentication handle the same problem?

## Conclusion

There is no cross-vendor standard for a `request_verification` agent tool, a
tool approval interruption, or agent run resumption. The names, state formats,
and resume mechanisms are framework-specific.

There is, however, a strong common architecture:

1. The model proposes the actual typed operation.
2. Deterministic runtime or application code evaluates that operation before
   execution.
3. If more human input is required, execution produces a typed interruption or
   challenge without running the sensitive operation.
4. Durable state preserves the exact pending operation.
5. After the human response, application code validates the response and
   retries or resumes the pending operation.
6. The protected boundary rechecks policy immediately before execution.

This supports a deterministic verification gate around OpenMagic's protected
Workflow tools. It does not support giving the Interaction Agent a general
`request_verification` tool and relying on the model to call it.

```text
Interaction Agent proposes protected Workflow operation
  -> deterministic verification policy checks trusted context
     -> sufficient proof: execute operation
     -> insufficient proof: create/reuse Challenge + safe typed outcome
        -> proof adapter delivers and validates proof
        -> record narrow Assertion + Notification
        -> fresh Interaction Agent turn retries protected operation
        -> policy and current authority are checked again
```

## Ranked repository comparables

| Rank | Source | Score | Best match | Important mismatch | Use for |
| --- | --- | ---: | --- | --- | --- |
| 1 | LangChain and LangGraph | 31/35 | Policy-driven tool interruption with durable checkpoint and resume | Review decisions are not identity evidence | Middleware and interruption shape |
| 2 | OpenAI Agents SDK | 31/35 | Python, typed approval callbacks, tool guardrails, and serializable run state | SDK run state is not the business source of truth | Pre-execution revalidation and typed pause state |
| 3 | Cloudflare Agents Codemode | 29/35 | Durable pause, explicit execution IDs, replay checks, and stale-response no-ops | TypeScript replay runtime rather than a Workflow Control Plane | Concurrency and delayed-response semantics |
| 4 | Vercel AI SDK | 28/35 | Typed approval requests, centralized policy, and forged-client-response defenses | Chat-centric TypeScript runtime with a different durability model | Exact-call binding and approval UI protocol |

Scoring criteria are domain fit, target stack fit, production maturity,
architecture clarity, infrastructure relevance, testing quality, and
documentation signal. Each criterion is scored from 0 to 5. The equal leaders
have different strengths: LangChain is the clearest middleware comparable,
while the OpenAI Agents SDK is the closest Python runtime comparable.

## Comparative result

| Source | Where the requirement is decided | Pause representation | Resume representation | What it standardizes |
| --- | --- | --- | --- | --- |
| LangChain and LangGraph | Middleware configuration keyed by tool name, optionally with a code predicate over the call | LangGraph `interrupt(...)` backed by a checkpointer | `Command(resume=...)` with the same thread | Framework-specific human review of tool calls |
| OpenAI Agents SDK | Tool `needs_approval` boolean or code callback over parsed arguments and trusted run context | `ToolApprovalItem` in run interruptions | Approve or reject serialized `RunState`, then rerun | Framework-specific approval and guardrail pipeline |
| Vercel AI SDK | `toolApproval` policy in application code, or legacy/tool-local `needsApproval` | Typed `tool-approval-request` content | Typed `tool-approval-response`, then another model call or durable workflow resume | Framework-specific approval content and UI states |
| Cloudflare Agents Codemode | Connector tools marked `requiresApproval` | Durable execution and tool log enter `paused` state | Approve explicit execution ID, then abort-and-replay from the durable log | Framework-specific durable execution approval |
| OAuth RFC 9470 | The protected resource evaluates the actual API request | HTTP 401 with `insufficient_user_authentication`, `acr_values`, and/or `max_age` | Obtain a qualifying access token and retry the original request | Standards-track API step-up challenge, not agent tools or proof mechanics |

The frameworks standardize the pattern within their own runtime. RFC 9470
standardizes the closest protocol-level analogue across implementations.

## LangChain and LangGraph

LangChain's `HumanInTheLoopMiddleware` is configured outside the model with an
`interrupt_on` map keyed by tool name. A tool can always interrupt, always pass,
or use a `when` predicate that receives the proposed tool call and runtime
context. After the model produces tool calls, middleware collects the matching
calls and calls LangGraph `interrupt(...)` before the tool node executes them.
The human can approve, edit, reject, or respond, limited by the configured
decision types.

LangGraph supplies the durable primitive below the middleware. `interrupt(...)`
requires a checkpointer and surfaces a value to the caller. The caller resumes
with `Command(resume=...)` using the same thread. LangGraph restarts the node
from its beginning during resume, so code before an interrupt must be safe to
repeat or must delegate effects to idempotent durable application services.

This is runtime-controlled review of the model's intended operation. It is not
a model-selected `request_approval` or `request_verification` tool.

Relevant official sources:

- [LangChain human-in-the-loop documentation](https://docs.langchain.com/oss/python/langchain/human-in-the-loop)
  documents the `interrupt_on` policy, checkpoint requirement, interruption,
  and resume flow.
- [`HumanInTheLoopMiddleware` source](https://github.com/langchain-ai/langchain/blob/233e02a8223234de731dda8f47152fa6c6a40535/libs/langchain_v1/langchain/agents/middleware/human_in_the_loop.py)
  defines the typed request and decisions, optional `when` predicate, and
  post-model interception before execution.
- [LangGraph functional API documentation](https://docs.langchain.com/oss/python/langgraph/use-functional-api#human-in-the-loop)
  shows `interrupt`, `Command`, checkpointers, and tool-call review.
- [`interrupt` and `Command` source](https://github.com/langchain-ai/langgraph/blob/55ec2f21939ce7755e6398c11b541de8926245ee/libs/langgraph/langgraph/types.py)
  defines the resumable exception and explicitly documents node re-execution.

Implications for OpenMagic:

- A middleware-like gate is a good implementation shape, provided the Workflow
  Control Plane still owns the policy and durable Challenge.
- Do not perform email dispatch inline before a LangGraph interrupt. Create an
  idempotent durable Workflow Job through the control plane.
- Do not persist an arbitrary graph callback as the business continuation.
  Persist a recognized continuation kind with validated typed arguments.

## OpenAI Agents SDK

The Agents SDK has two related but distinct mechanisms.

First, a function tool can declare `needs_approval=True` or a callback. The
callback receives trusted run context, parsed tool parameters, and the tool call
ID. When approval is needed and no decision exists for that call, the runner
emits a `ToolApprovalItem` instead of executing the tool. `RunState` preserves
the pending call and approval state, can be serialized, and can be approved or
rejected before the original top-level run resumes. Per-call approval is scoped
to the call ID unless the application explicitly chooses a sticky decision.

Second, input tool guardrails execute around function tools and can allow,
replace the tool result with a rejection message, or raise a tripwire. By
default, an input tool guardrail for an approval-protected function runs after
approval and immediately before execution. The SDK can also run it before the
approval interruption, but still repeats it after approval. This second check
is the closer match for current authority and verification assertion
validation.

These mechanisms are approvals, not an authentication protocol. The SDK does
not create or validate email codes, define proof strength, or scope an identity
assertion to an OpenMagic Workflow.

Relevant official sources:

- [Agents SDK human-in-the-loop documentation](https://openai.github.io/openai-agents-python/human_in_the_loop/)
  documents `needs_approval`, interruptions, serializable `RunState`, and
  approve/reject/resume.
- [Agents SDK guardrails documentation](https://openai.github.io/openai-agents-python/guardrails/#tool-guardrails)
  documents pre-execution input tool guardrails and their ordering around
  approval.
- [`tool_execution.py` source](https://github.com/openai/openai-agents-python/blob/8221e424db96c0dd3152f36e6848f9b8c6f10646/src/agents/run_internal/tool_execution.py)
  evaluates `needs_approval` from parsed arguments and context, creates a
  `ToolApprovalItem`, and runs tool input guardrails before invocation.
- [`run_state.py` source](https://github.com/openai/openai-agents-python/blob/8221e424db96c0dd3152f36e6848f9b8c6f10646/src/agents/run_state.py)
  defines the serializable pause boundary and per-call approval methods.
- [`tool_guardrails.py` source](https://github.com/openai/openai-agents-python/blob/8221e424db96c0dd3152f36e6848f9b8c6f10646/src/agents/tool_guardrails.py)
  defines deterministic allow, reject, and tripwire results.

Implications for OpenMagic:

- A `needs_approval` callback demonstrates that trusted application code can
  make a decision from typed arguments without asking the model.
- Verification should remain a distinct domain concept from `Approval Grant`.
  An SDK approval primitive may help pause execution, but it must not become
  the source of identity, Workflow authority, or durable proof.
- Recheck a Verification Assertion and current Workflow authority immediately
  before returning a protected Workflow Packet.

## Vercel AI SDK

The current AI SDK source centralizes approval policy in application code with
`toolApproval`. A policy can be per-tool or global and can inspect typed input,
the full tool call, messages, tool context, and trusted runtime context. It
returns one of four statuses: not applicable, automatically approved,
automatically denied, or requires user approval. The older tool-local
`needsApproval` remains as a compatibility fallback for ordinary agent calls
and is retained for durable `WorkflowAgent` tools.

When manual approval is required, execution returns a typed
`tool-approval-request`. The application collects a decision, adds a matching
`tool-approval-response`, and invokes the agent again. The protected tool does
not run until the response is processed.

The official source also identifies a security boundary that is directly
relevant to OpenMagic. In stateless chat, message history is client-controlled.
A syntactically valid approval response can be forged unless the application
persists server-side state or cryptographically binds the request to the exact
tool name, tool call ID, and arguments. Vercel provides an experimental HMAC
secret for that binding. Its durable workflow runtime uses persistence instead.

Relevant official sources:

- [AI SDK tool calling documentation](https://ai-sdk.dev/docs/ai-sdk-core/tools-and-tool-calling#tool-execution-approval)
  documents typed approval request and response content, dynamic rules, and
  the second model call.
- [`Tool Approvals` official repository documentation](https://github.com/vercel/ai/blob/c093ee7458ccd5dada05d8461041e47c24ee55c0/content/docs/03-agents/06-tool-approvals.mdx)
  documents centralized `toolApproval`, trusted runtime context, manual resume,
  durable workflows, and the client-history forgery risk.
- [`resolve-tool-approval.ts` source](https://github.com/vercel/ai/blob/c093ee7458ccd5dada05d8461041e47c24ee55c0/packages/ai/src/generate-text/resolve-tool-approval.ts)
  shows that application policy takes precedence over the tool fallback and is
  evaluated from validated typed input.

Implications for OpenMagic:

- Keep Challenge, continuation, and Assertion state in PostgreSQL. Do not trust
  an SMS transcript, browser chat history, or model message claiming that
  verification succeeded.
- Bind the Challenge to the exact Party, interaction, Workflow, purpose, and
  recognized continuation. If operation arguments can change the disclosed
  data, also bind an argument fingerprint or reconstruct arguments from trusted
  durable identifiers.
- The model may receive a safe `verification_required` outcome, but never an
  assertion secret, code hash, destination address, or client-supplied success
  claim.

## Cloudflare Agents Codemode

Cloudflare Codemode tools can be marked `requiresApproval: true`. When
model-generated code reaches one of these tools, the runtime records the exact
pending call, aborts the pass, and returns a typed paused outcome with an
explicit execution ID and pending actions. Approval targets that execution ID,
then the runtime replays the same code. Prior tool results come from the durable
log, and only the newly approved action executes for real.

The replay runtime verifies that connector, method, call order, and stably
serialized arguments match the original run. A mismatch becomes a terminal
replay-divergence error. Side-effectful or nondeterministic work outside normal
connector calls must use a `codemode.step` boundary so its result is recorded
once and replayed.

Cloudflare also handles a useful concurrency edge. `approve()` is a safe no-op
if an execution is no longer paused. It does not revive completed, rejected, or
rolled-back work. `reject()` similarly reports when another actor already
resolved the pending action. This is the correct shape for delayed email or SMS
proof where duplicate replies and concurrent callbacks are normal.

Relevant official sources:

- [Cloudflare Codemode approvals documentation](https://github.com/cloudflare/agents/blob/762998da1c873701305a44c598e9c029617047b4/docs/codemode/approvals.md)
  documents `requiresApproval`, explicit execution IDs, durable pause, replay,
  and stale approval behavior.
- [Cloudflare Codemode runtime documentation](https://github.com/cloudflare/agents/blob/762998da1c873701305a44c598e9c029617047b4/docs/codemode/runtime.md)
  documents the durable log, abort-and-replay, determinism requirement,
  divergence checks, and one-time step boundary.
- [`runtime.ts` source](https://github.com/cloudflare/agents/blob/762998da1c873701305a44c598e9c029617047b4/packages/codemode/src/runtime.ts)
  implements durable execution status, pending action lookup, stale action
  handling, expiry, and replay decisions.
- [`proxy-tool.ts` source](https://github.com/cloudflare/agents/blob/762998da1c873701305a44c598e9c029617047b4/packages/codemode/src/proxy-tool.ts)
  implements the model-facing paused outcome and resume by replay.

This is still approval, not identity verification. A person choosing "approve"
consents to an action. A Party returning a valid email code supplies evidence
of control of an identifier. OpenMagic can reuse the durable pause, explicit
execution ID, replay-safety, and stale no-op patterns without conflating those
domain meanings.

Implications for OpenMagic:

- Give every Challenge and continuation an explicit durable ID. Never infer an
  implicit current challenge when several interactions may be in flight.
- Confirmation of a consumed, superseded, expired, or otherwise terminal
  Challenge must be a safe no-op that never revives the continuation.
- Revalidate the recognized continuation and current Workflow authority on
  resume. Do not replay already executed External Effects.
- Prefer reconstructing a fresh Interaction Agent turn from durable facts over
  replaying arbitrary model-generated code. Cloudflare's replay machinery is a
  useful comparable, but OpenMagic's Workflow Control Plane already supplies a
  stronger business-level durability boundary.

## OAuth RFC 9470

RFC 9470 is the strongest standardized analogue. It is an IETF Standards Track
protocol for step-up authentication at a protected API boundary.

The resource server evaluates the actual request and decides that the existing
authentication is too weak or too old. It denies access with a typed
`insufficient_user_authentication` challenge, optionally stating required
authentication context through `acr_values` and required freshness through
`max_age`. The client obtains a new qualifying access token and retries the
original resource request. The resource server then evaluates the new token and
either returns the resource or denies it again.

Two scope boundaries matter:

- RFC 9470 deliberately leaves the resource server's policy logic out of scope.
- It also leaves the mechanics of authenticating the person to a separate
  authentication layer. It does not standardize email OTPs, magic links, agent
  tools, or workflow continuations.

Relevant official source:

- [RFC 9470, OAuth 2.0 Step Up Authentication Challenge Protocol](https://www.rfc-editor.org/rfc/rfc9470.html),
  especially Sections 2, 3, 4, 6, and 9.

The direct OpenMagic mapping is:

| RFC 9470 role | OpenMagic role |
| --- | --- |
| Client | Interaction Agent runtime and ingress orchestration |
| Protected resource | Protected Workflow query or command boundary |
| Authorization server and authentication layer | Verification Protocol plus proof adapter |
| `insufficient_user_authentication` | Typed `verification_required` outcome |
| `acr` and `auth_time` | Assertion method or assurance marker and `verified_at` |
| Retry original request | Resume recognized continuation, recheck proof and authority |

OpenMagic V0 is not an OAuth implementation and email proof should not be
described as an OAuth assurance level. The useful precedent is ownership of the
decision and retry shape: the protected operation challenges, proof is obtained
elsewhere, and the protected operation reevaluates the request.

## Model-called verification tool versus deterministic gate

| Design | Who decides proof is required? | Failure mode | Recommendation |
| --- | --- | --- | --- |
| Model calls `request_verification` | Model | Model omits the call, calls it too often, chooses the wrong destination or purpose, or treats success text as proof | Do not use as the security boundary |
| Protected tool returns `verification_required` | Deterministic domain policy | Requires a typed continuation and durable challenge state | Use for OpenMagic |
| Ingress exposes `confirm_verification` to model | Model parses the secret and selects challenge | Secret enters model context, ambiguous code routing, prompt injection surface | Do not use for V0 |
| Ingress intercepts proof and calls protocol directly | Deterministic channel adapter | Requires narrow code detection and interaction binding | Use for SMS code and future HTTPS callback |

The Interaction Agent still has an important role. It recognizes that a Party
is asking about a specific Workflow and calls the appropriate typed Workflow
tool. It can explain a safe `verification_required` result in a friendly way.
It does not select the verification destination, inspect the proof, grant the
Assertion, or decide that the sensitive operation may proceed.

## Recommended OpenMagic contract

Do not add a general model-visible `request_verification` tool. Add a deep
Verification Protocol used by every protected Workflow query or command.

Conceptual interface:

```text
require(
  party_id,
  interaction_id,
  workflow_id,
  purpose,
  continuation,
) -> allowed | verification_required

confirm(
  interaction_id,
  submitted_proof,
) -> accepted | invalid | expired | exhausted
```

The `verification_required` outcome should be safe for model context. It can
contain a Challenge ID, purpose, proof method, masked destination, and expiry.
It must not contain the code, code hash, full email address, or a mutable claim
of success.

The continuation should be application-owned and typed:

```text
kind: read_workflow_packet
workflow_id: ...
purpose: sensitive_read
request_cause_id: ...
```

Do not serialize an arbitrary callback, raw model output, or unrestricted tool
arguments. On resume, issue a fresh Interaction Agent Notification, reconstruct
the Workflow Packet from current durable facts, and revalidate both the narrow
Verification Assertion and current Workflow Role.

## Public conversation remains open

The gate applies to operations, not to messages or conversational tone.

```text
"What does a deductible mean?"
  -> public company or insurance knowledge
  -> no protected Workflow tool
  -> no verification

"How do I get started?"
  -> onboarding conversation
  -> may create only explicitly public or provisional state
  -> no verification unless a protected operation is reached

"How much of my orthodontic benefit remains?"
  -> protected Workflow read
  -> deterministic Verification Protocol
```

The model must not receive private Workflow facts in its initial prompt or
general knowledge context. Otherwise it could answer without crossing the
gate. Only the protected typed tool may supply those facts.

## Replaceable proof delivery

The common frameworks pause on an operation, not on a particular user
interface. OpenMagic should follow the same separation:

```text
Protected operation
  -> stable Verification Challenge
     -> email_code adapter
        -> code returned through originating SMS
     -> future email_magic_link adapter
        -> signed token confirmed through HTTPS
  -> stable Verification Assertion
  -> stable Notification and continuation resume
```

Changing from a six-digit code to a magic link changes challenge issuance and
proof confirmation. It does not change protected Workflow tools, policy
purposes, Assertions, Notifications, or continuation semantics.

## Source revisions inspected

- LangChain: `233e02a8223234de731dda8f47152fa6c6a40535`
- LangGraph: `55ec2f21939ce7755e6398c11b541de8926245ee`
- OpenAI Agents SDK Python: `8221e424db96c0dd3152f36e6848f9b8c6f10646`
- Vercel AI SDK: `c093ee7458ccd5dada05d8461041e47c24ee55c0`
- Cloudflare Agents: `762998da1c873701305a44c598e9c029617047b4`
