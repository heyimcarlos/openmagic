# Issue 40 Workflow telemetry convergence brief

Accessed 2026-07-13.

## Decision frame

- Target: durable, user-facing telemetry for one Interaction turn in OpenMagic.
- Stack: FastAPI, PostgreSQL, Pydantic, Next.js, and the existing Workflow
  Control Plane.
- Scale: a V0 renewal-email tracer, not a general observability platform.
- Hard constraints: authorization before projection, no raw prompts, reasoning,
  tool arguments, provider results, credentials, or secrets. Telemetry failure
  must never fail the underlying agent turn or chat history response.
- Key question: how can the UI explain what happened without making telemetry
  a second source of truth for Workflow state or execution authority?

## Ranked comparables

| Rank | Source | Score | Best match | Important mismatch | Use for |
|---|---|---:|---|---|---|
| 1 | OpenAI Agents Python | 32/35 | Typed trace records, explicit sensitive-data controls, non-disruptive processors | Export-oriented traces are not durable, authorized business projections | Failure isolation and data minimization |
| 2 | LangGraph | 31/35 | Stable UI message identity, correlation metadata, typed projected state | UI messages can live in graph state, while OpenMagic reconstructs from PostgreSQL and chat Cause IDs | Projection and stable identity |
| 3 | Deep Agents | 29/35 | Tool-call correlation, terminal status handling, bounded display, strong failure-path tests | Hooks may include raw arguments and output and the debug buffer is in memory | Correlation, bounded rendering, terminal outcomes |

Scores cover domain fit, target stack fit, production maturity, architecture
clarity, operations relevance, testing quality, and maintainability signal.

## Repository extracts

### OpenAI Agents Python

Revision: `68fadc7abaefa2d33e22bdea71d93ce8d9ef2f10`

Exact inspected paths:

- `/home/ren/repos/openmagic/.reference/openai-agents-python/src/agents/tracing/processor_interface.py`
- `/home/ren/repos/openmagic/.reference/openai-agents-python/src/agents/tracing/spans.py`
- `/home/ren/repos/openmagic/.reference/openai-agents-python/src/agents/mcp/util.py`
- `/home/ren/repos/openmagic/.reference/openai-agents-python/tests/mcp/test_mcp_tracing.py`
- `/home/ren/repos/openmagic/.reference/openai-agents-python/tests/test_run_config.py`

Observed shape:

- A processor receives typed trace and span lifecycle callbacks.
- Processor methods must return quickly, handle errors internally, and avoid
  disrupting agent execution.
- Span guidance explicitly warns against sensitive data.
- MCP output is omitted when sensitive tracing is disabled, and snapshot tests
  prove the redacted shape.

OpenMagic use:

- Treat activity receipt recording and telemetry projection as non-critical
  observers. Log their failure and preserve the agent turn.
- Use a closed persisted allowlist instead of a setting that can re-enable raw
  tool payloads. User-facing telemetry has a stricter boundary than developer
  tracing.

Do not copy:

- Do not expose full span input or output in chat.
- Do not make an exporter or generic tracing SDK part of the Workflow protocol.

### LangGraph

Revision: `55ec2f21939ce7755e6398c11b541de8926245ee`

Exact inspected paths:

- `/home/ren/repos/openmagic/.reference/langgraph/libs/langgraph/langgraph/graph/ui.py`
- `/home/ren/repos/openmagic/.reference/langgraph/libs/langgraph/langgraph/types.py`
- `/home/ren/repos/openmagic/.reference/langgraph/libs/checkpoint/README.md`
- `/home/ren/repos/openmagic/.reference/langgraph/libs/langgraph/tests/test_stream_data_transformers.py`

Observed shape:

- UI messages have stable IDs, typed payloads, and metadata that can include the
  originating message ID.
- Updates and removal are applied by ID, so correctness does not depend on
  arrival order.
- Checkpoints distinguish current durable state from task and stream views.
- Pending, successful, failed, and interrupted task shapes are explicit.

OpenMagic use:

- Correlate the turn, activity receipts, Workflow Events, and the projected UI
  by the trusted Cause ID.
- Keep the transport model typed and stable while deriving its current status
  from the authorized Workflow Packet.
- Polling may replace the same turn projection as durable state advances.

Do not copy:

- Do not persist the telemetry card itself as Workflow state.
- Do not let UI updates mutate Jobs, approvals, External Effects, or authority.

### Deep Agents

Revision: `b2d62ce45640f49707eb057a135de0ebc5a5a6cd`

Exact inspected paths:

- `/home/ren/repos/openmagic/.reference/deepagents/libs/code/deepagents_code/hooks.py`
- `/home/ren/repos/openmagic/.reference/deepagents/libs/code/deepagents_code/_debug_buffer.py`
- `/home/ren/repos/openmagic/.reference/deepagents/libs/code/deepagents_code/tool_display.py`
- `/home/ren/repos/openmagic/.reference/deepagents/libs/code/deepagents_code/tui/widgets/message_store.py`
- `/home/ren/repos/openmagic/.reference/deepagents/libs/code/deepagents_code/tui/textual_adapter.py`
- `/home/ren/repos/openmagic/.reference/deepagents/libs/code/tests/unit_tests/test_non_interactive.py`
- `/home/ren/repos/openmagic/.reference/deepagents/libs/code/tests/unit_tests/tui/widgets/test_messages.py`

Observed shape:

- Tool use and terminal results are correlated by `tool_id`; concurrent delivery
  is explicitly allowed to arrive out of order.
- Terminal result dispatch is protected from rendering failures, so a broken
  widget does not erase the terminal record.
- Display values are sanitized and bounded.
- The debug console uses a bounded structured buffer with monotonic indices.

OpenMagic use:

- Give each activity receipt a stable Cause-local sequence and correlate by
  identifiers, never by observed order alone.
- Record `running`, `succeeded`, or `failed` through a small typed boundary.
- Make projection and rendering failure independent from execution correctness.

Do not copy:

- Deep Agents hook payloads can carry raw arguments and bounded tool output.
  OpenMagic user telemetry must persist neither.
- Its in-memory debug ring is useful for a console, but cannot provide restart
  continuity for chat telemetry.

## Standards guidance

### W3C Trace Context

[Trace Context](https://www.w3.org/TR/trace-context/) defines stable trace and
parent identifiers for correlating distributed work. Its security section also
warns that propagated fields are caller input and can be abused.

Local implication: the same broad principle applies to a Cause ID, but
OpenMagic does not need to implement W3C propagation for V0. The Control Plane
accepts a Cause only through its trusted application boundary. A model-provided
identifier is not authority.

### OpenTelemetry logs data model

The [OpenTelemetry logs data model](https://opentelemetry.io/docs/specs/otel/logs/data-model/)
uses explicit event fields, timestamps, attributes, and optional trace context.
The [logging specification](https://opentelemetry.io/docs/specs/otel/logs/)
emphasizes exact correlation by shared execution context instead of inferring
relationships from time and origin.

Local implication: store typed action, status, Cause, sequence, trusted
Workflow ID, and timestamps. Do not infer a Workflow link from timing. This is
an interaction activity receipt, not a replacement for a Workflow Event or a
full OpenTelemetry LogRecord.

### OWASP Logging Cheat Sheet

The [OWASP Logging Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Logging_Cheat_Sheet.html)
recommends excluding or sanitizing session identifiers, tokens, passwords,
connection strings, keys, and sensitive personal data. It also recommends
sanitizing untrusted event data before recording it.

Local implication: an allowlist is safer than trying to redact arbitrary tool
arguments or results after collection. Persist only action key, lifecycle
status, trusted Workflow identity, Cause-local sequence, and timestamps.

## Convergence

The three repositories and three standards converge on these rules:

1. Correlate by a stable trusted identifier, never by arrival order or timing.
2. Project a small typed lifecycle instead of exposing raw execution payloads.
3. Bound, sanitize, and minimize telemetry at the collection boundary.
4. Keep telemetry processing failure isolated from the operation it observes.
5. Derive user-visible current state from the authoritative durable system.

OpenMagic should implement this shape:

```text
Interaction Cause
      | records
      v
sanitized activity receipts
      |
      +---- Cause-linked Workflow Events
      |                 |
      v                 v
authorization-scoped Workflow Packet
      |
      v
deterministic ChatTurnTelemetry projection
      |
      +---- main chat turn
      +---- cockpit telemetry view
```

The receipt answers what visible Interaction action ran. The Workflow Packet
answers what may happen now. Workflow Events preserve what happened and why.
The projector combines those authorized sources without becoming authority.

Recommended module boundaries:

- `ConversationLog` owns durable message and Cause correlation.
- A narrow activity receipt store owns sanitized action lifecycle persistence.
- A Workflow telemetry projector owns authorization, batching, labels, stages,
  and approval checkpoint derivation.
- The chat route owns graceful fallback to text-only history.
- The existing web telemetry component renders the same transport contract in
  chat and cockpit.

## Options considered

| Option | Score | When it fits | Why it is not the V0 choice |
|---|---:|---|---|
| A. Render generic OpenTelemetry spans | 4/10 | Developer observability across many services | Too broad, payload-rich, and disconnected from Workflow authorization |
| B. Durable sanitized receipts plus deterministic projection | 10/10 | User-facing explanation of one durable agent turn | Chosen, smallest shape that covers activity and current Workflow state |
| C. Render Workflow Events only | 6/10 | An operator audit timeline | Cannot explain pre-Workflow search and packet reads, and risks presenting immutable history as current state |

## Rejected alternatives

- Raw tool arguments and results in telemetry, because redaction after capture is
  brittle and may expose prompts, provider payloads, PII, or secrets.
- A generic tracing framework inside the Control Plane, because it adds a second
  lifecycle vocabulary without improving V0 correctness.
- Sequence or timestamp-only correlation, because concurrency and retries can
  reorder observations.
- Persisting rendered telemetry cards, because they become stale and compete
  with the Workflow Packet as the source of current state.
- Returning a chat-history error when projection fails, because observability
  must not disrupt the user-visible agent result.

## Final recommendation

Implement Option B. Persist a minimal, Cause-correlated activity receipt around
allowlisted Interaction tools, then batch-project authorized Workflow state
onto completed chat turns. Reuse the exact typed transport in the main chat and
cockpit. Defer W3C trace propagation, OpenTelemetry export, live token streams,
and generalized observability until a real operational need appears.

This recommendation changes if OpenMagic later needs cross-service operational
tracing. In that case, emit OpenTelemetry from the same trusted boundaries, but
keep its records separate from the authorization-scoped user projection.

## Sources

- [OpenAI Agents Python](https://github.com/openai/openai-agents-python/tree/68fadc7abaefa2d33e22bdea71d93ce8d9ef2f10), accessed 2026-07-13.
- [LangGraph](https://github.com/langchain-ai/langgraph/tree/55ec2f21939ce7755e6398c11b541de8926245ee), accessed 2026-07-13.
- [Deep Agents](https://github.com/langchain-ai/deepagents/tree/b2d62ce45640f49707eb057a135de0ebc5a5a6cd), accessed 2026-07-13.
- [W3C Trace Context](https://www.w3.org/TR/trace-context/), accessed 2026-07-13.
- [OpenTelemetry logs data model](https://opentelemetry.io/docs/specs/otel/logs/data-model/), accessed 2026-07-13.
- [OpenTelemetry logging](https://opentelemetry.io/docs/specs/otel/logs/), accessed 2026-07-13.
- [OWASP Logging Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Logging_Cheat_Sheet.html), accessed 2026-07-13.
