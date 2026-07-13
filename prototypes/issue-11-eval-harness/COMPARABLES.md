# Issue 11 evaluation and recovery comparables

Accessed: 2026-07-13

## Decision frame

- Target: the smallest credible paired evaluation and recovery harness for the
  OpenMagic V0 renewal-email tracer.
- Stack: Python, pytest, FastAPI, PostgreSQL, SQLAlchemy, OpenRouter, Composio,
  and AgentMail.
- Scale: one local demo tracer, one inherited baseline, one durable V0 path.
- Hard constraints: reuse production boundaries, keep prompt context
  disposable, never force unsafe Gmail failures, and do not build a generalized
  evaluation platform.
- Key question: which observations are deterministic correctness gates, which
  are paired model diagnostics, and which are narrow live-provider evidence?

## Ranked comparables

| Rank | Source | Score | Best match | Important mismatch | Use for |
| ---: | --- | ---: | --- | --- | --- |
| 1 | Prefect | 33/35 | Python orchestration recovery, leases, crashes, event ordering | Much broader platform and API | Deterministic fault fixtures and persisted-state assertions |
| 2 | Deep Agents evals | 29/35 | Repeated real-model trials, trajectories, correctness and efficiency | Agent benchmark, not a durable queue | Statistical model diagnostics and per-trial reports |
| 3 | Executor E2E | 28/35 | User-meaningful journeys, restart tests, per-run artifacts and traces | TypeScript and multi-target product matrix | Scenario readability, artifact isolation, restart proof |

Scores cover domain fit, target-stack fit, production maturity, architecture
clarity, infrastructure relevance, testing quality, and maintainability signal.

## Repository architecture extracts

### Prefect

- Commit: `0e7435055e18952aa8604dab78507b087a18defb`
- `tests/concurrency/test_leases.py` forces renewal failures and blocked event
  loops, then asserts the caller's actual cancellation behavior.
- `tests/server/worker_communication/test_cleanup_queue.py` uses a small
  controllable clock and applies one standard contract suite to a concrete
  backend.
- `tests/events/server/test_db_ordering.py` feeds proper, jumbled, backwards,
  and missing event sequences into the same causal-ordering contract.
- `tests/public/flows/test_flow_crashes.py` verifies crash truth from the API's
  persisted Run state instead of trusting the failed local call.
- Emulate: inject faults at stable boundaries, control time directly, and
  assert the durable record after the process-facing operation fails.
- Avoid: importing Prefect's generalized orchestration test hierarchy or
  backend matrix into a one-tracer demo.

### Deep Agents evals

- Commit: `14f384fc0083c07a7f44f97543b40b74cf93c13f`
- `libs/evals/README.md` separates end-to-end behavioral evaluation from unit
  and integration coverage and captures full trajectories plus correctness and
  efficiency.
- `libs/evals/CONTRIBUTING.md` uses a two-tier scorer: `.success(...)` hard-fails
  correctness violations, while `.expect(...)` records trajectory-shape
  expectations such as steps and tool calls without failing the eval.
- `libs/evals/scripts/run_trials.py` keeps every trial's report, bounds trial
  count, aggregates mean, median, sample deviation, minimum, and maximum, and
  runs provider-bound trials sequentially within one process.
- `libs/evals/deepagents_evals/trial_summary.py` preserves missing metrics as
  missing rather than silently treating them as zero.
- Emulate: retain one result per trial, identify the exact model and build, and
  aggregate variable model behavior without converting it into protocol truth.
- Avoid: LangSmith, Harbor, radar charts, model groups, and multi-benchmark
  infrastructure for V0.

### Executor

- Commit: `0a50c796c2cc334cf3e9bf6d4be33c77dbfac93b`
- `e2e/AGENTS.md` defines one user-meaningful journey per scenario and treats
  readable test source as the review artifact.
- `e2e/src/scenario.ts` gives each run a fresh artifact directory with result,
  duration, source, and surface evidence. Missing target capabilities become
  explicit skips, not false passes.
- `e2e/scenarios/restart-persistence.test.ts` proves a write before restart and
  reads it through a fresh authenticated client after restart.
- `e2e/src/trace-harvest.ts` keeps request timings and trace identifiers in one
  wall-clock-ordered ledger without making the ledger the correctness oracle.
- Emulate: make each OpenMagic scenario readable as a specification, isolate
  artifacts by run, and prove restart continuity through a fresh boundary.
- Avoid: Executor's target registry, viewer, media pipeline, and capability
  framework.

## Language and standards guidance

- Pytest's official parametrization guidance supports named case matrices at
  the function and fixture levels. OpenMagic should use explicit case IDs for
  fault boundaries rather than a custom scenario runner.
- Pytest's `record_property` fixture can attach diagnostics to JUnit output.
  The existing retrieval evaluation already uses this seam, so the paired
  harness should extend it before inventing a report protocol.
- Python documents `time.perf_counter_ns()` as a high-resolution monotonic
  duration clock. Segment timers should use it for local, model, and provider
  intervals. Persisted domain timestamps remain business evidence, not
  benchmark clocks.

## Recommended shape

Use three separate pytest lanes over shared typed fixtures:

1. `paired`: the same synthetic requests and model configuration exercise the
   inherited legacy profile and the Workflow profile. The V0 safety outcome is
   gated. The baseline is a comparator, not a second required implementation.
   Model variability, Packet reads, context burden, tool counts, and latency are
   recorded per trial and summarized as diagnostics. Correct authorization,
   bounded individual responses, unambiguous resolution, and mutation safety
   remain hard assertions.
2. `recovery`: parametrized deterministic tests use real PostgreSQL and public
   Control Plane or Worker seams. Worker loss, duplicate input, approval races,
   dispatch uncertainty, restart, and Notification delivery are strict gates.
3. `live`: the existing opt-in Composio plus AgentMail journey proves one normal
   provider path. It never injects transport loss or retries an uncertain send.

Keep scenario definitions as typed data and assertions in ordinary readable
pytest tests. Emit a small JSON and Markdown evidence summary from test
properties only after the scenarios pass. Do not introduce a framework class,
plugin, dashboard, vector store, or second execution engine.

## Options

| Option | Points | When to choose | Risks | First slice |
| --- | ---: | --- | --- | --- |
| Three separated lanes | 10/10 | V0 needs both comparative and safety evidence | Requires clear report labels | Pair three renewal requests, then add missing recovery fixtures |
| One combined end-to-end score | 4/10 | Only if one ranking number is the product | Hides safety failures behind averages | Not recommended |
| Recovery tests only | 7/10 | If the demo drops the baseline claim | Cannot demonstrate agent-overload improvement | Existing tests plus restart fixtures |

## Final recommendation

Adopt the three-lane prototype. Strict gates must express V0 business and
lifecycle correctness. Baseline outcomes, model-dependent quality, token
burden, and segmented latency remain visible comparisons. The live provider
journey remains a smoke proof. This boundary would change only if the product
later adopts a statistically powered model-release gate with a maintained
dataset and explicit regression thresholds.

The current one-Packet-per-Interaction behavior remains the demo policy because
it is already implemented and safe. It is not treated as the proven optimum.
The paired eval records Packet reads and resolution failures so later evidence
can justify iterative multi-Packet inspection without weakening authorization,
bounded-response, or pre-mutation resolution rules.

## Sources

- [Prefect repository at the inspected commit](https://github.com/PrefectHQ/prefect/tree/0e7435055e18952aa8604dab78507b087a18defb)
- [Deep Agents evals at the inspected commit](https://github.com/langchain-ai/deepagents/tree/14f384fc0083c07a7f44f97543b40b74cf93c13f/libs/evals)
- [Executor E2E at the inspected commit](https://github.com/UsefulSoftwareCo/executor/tree/0a50c796c2cc334cf3e9bf6d4be33c77dbfac93b/e2e)
- [Pytest parametrization](https://docs.pytest.org/en/stable/how-to/parametrize.html)
- [Pytest output and `record_property`](https://docs.pytest.org/en/stable/how-to/output.html)
- [Python performance-counter documentation](https://docs.python.org/3/library/time.html#time.perf_counter_ns)
