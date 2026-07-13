# Issue 28 paired coordination evaluation comparables

Recorded: 2026-07-13

## Decision frame

OpenMagic needs one small Python evaluation surface that compares the inherited
named-agent coordination profile with the PostgreSQL Workflow profile. V0
correctness must remain a hard verdict. Baseline behavior, Packet reads,
context burden, tool counts, and timing remain diagnostics. The evaluator must
never launch an Execution Agent or external email effect.

## Ranked comparables

| Rank | Source | Score | Best match | Mismatch | Use for |
| ---: | --- | ---: | --- | --- | --- |
| 1 | Deep Agents evals | 31/35 | Python agent trajectories, hard success versus soft expectations, repeated trials | Broader SDK and LangSmith dependency | Trial model, scoring split, report vocabulary |
| 2 | Executor E2E | 28/35 | One readable journey, isolated run artifacts, black-box public surfaces | TypeScript and multi-target deployment matrix | Per-run evidence and source-as-spec discipline |
| 3 | Existing OpenMagic retrieval eval | 27/35 | Exact V0 fixtures, PostgreSQL authorization, bounded responses | Retrieval only, no paired agent trajectory | Reuse of fixtures and pytest properties |

Scores cover domain fit, Python fit, maturity, architecture clarity,
operations relevance, testing quality, and maintainability signal.

## Deep Agents

- Commit: `14f384fc0083c07a7f44f97543b40b74cf93c13f`
- `libs/evals/CONTRIBUTING.md` defines `.success(...)` assertions that hard-fail
  correctness and `.expect(...)` assertions that record efficiency without
  failing the test.
- `libs/evals/scripts/run_trials.py` keeps one report per trial, includes model
  and SDK identity, bounds trial count, and aggregates variable model outcomes.
- `libs/evals/deepagents_evals/trial_summary.py` preserves missing metrics as
  missing instead of coercing them to zero.
- `libs/evals/EVAL_CATALOG.md` groups cases by capability and keeps a generated
  catalog, but OpenMagic has only one narrow category and needs no catalog
  generator or radar chart.

OpenMagic should copy the verdict split and per-trial record. It should not copy
LangSmith, Harbor, model groups, radar charts, or generalized scorer classes.

## Executor

- Commit: `0a50c796c2cc334cf3e9bf6d4be33c77dbfac93b`
- `e2e/AGENTS.md` makes one user-meaningful journey the unit of review and keeps
  assertions in readable test source.
- `e2e/src/scenario.ts` creates one isolated result directory per run with
  explicit duration and artifact metadata.
- `e2e/scenarios/restart-persistence.test.ts` asserts through public surfaces
  before and after restart rather than trusting internal state.

OpenMagic should keep each coordination case readable and each report bounded.
It should not add Executor's target registry, viewer, recording pipeline, or
capability framework.

## Existing OpenMagic shape

- `server/tests/evals/test_workflow_retrieval_eval.py` already records rank,
  response bytes, and approximate tokens through pytest properties.
- `server/tests/workflows/retrieval_fixtures.py` already provides the target,
  historical, same-name, wrong-Kind, other-policyholder, and unauthorized
  landscape required by the paired corpus.
- `server/agents/interaction_agent/factory.py` exposes explicit `legacy` and
  `workflow` profiles over the same `InteractionAgentRuntime`.

The missing seam is safe observation: the legacy profile must stop at its
named-agent dispatch boundary, while the Workflow profile uses the real search,
Packet, and proposal tools. Both profiles need isolated conversation state and
the same completion dependency.

## Recommended shape

- Add one narrow `server.evals.coordination` module with immutable scenario,
  trial, diagnostic, and report contracts.
- Inject conversation state and model completion into
  `InteractionAgentRuntime` so the evaluator does not monkeypatch private
  methods or write to the user's conversation log.
- Wrap the two tool profiles with an observation boundary. Legacy dispatch is
  acknowledged but never executed. Workflow retrieval and mutation delegate to
  the real toolbox.
- Use `time.perf_counter_ns()` around model and local tool boundaries. Record
  durations without setting performance thresholds.
- Store only synthetic scenario identifiers, tool names, counts, digests, and
  typed outcomes. Do not persist raw provider payloads, credentials, prompts,
  or conversation history.
- Keep deterministic scripted coverage as the PR gate. Add a credentialed,
  opt-in real-model pytest journey that writes JSON and Markdown evidence to a
  caller-selected output directory.

## Sources

- [Deep Agents evals](https://github.com/langchain-ai/deepagents/tree/14f384fc0083c07a7f44f97543b40b74cf93c13f/libs/evals)
- [Executor E2E](https://github.com/UsefulSoftwareCo/executor/tree/0a50c796c2cc334cf3e9bf6d4be33c77dbfac93b/e2e)
- [Pytest parametrization](https://docs.pytest.org/en/stable/how-to/parametrize.html)
- [Python performance counter](https://docs.python.org/3/library/time.html#time.perf_counter_ns)
