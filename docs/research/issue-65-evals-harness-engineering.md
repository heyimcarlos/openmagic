# Failure proofs, evals, and playground evidence

Date: 2026-07-14

Issue: [Define failure proofs, evals, and playground evidence](https://github.com/heyimcarlos/openmagic/issues/65)

## Decision frame

- **Target:** an application-owned durable Workflow kernel in one PostgreSQL
  database, with replaceable deterministic and Agent Executors.
- **Application boundary:** Workflow Policy owns Commands, completion, authority,
  External Effects, Domain Events, Deliveries, and exact Thread selection.
- **Demonstration boundary:** a safe interview playground should make the same
  contracts visible without claiming the breadth or operating history of a
  mature workflow engine.
- **Current stack:** Python, FastAPI, Pydantic, SQLAlchemy, PostgreSQL, pytest,
  optional live model and provider calls.
- **Hard constraint:** model quality, a hosted tracing service, and a live
  provider must never determine whether the kernel is correct.

## Conclusion

Use one evidence contract with three lanes:

```text
Deterministic release gates
  real PostgreSQL + fresh processes + fault injection + races + public APIs
  -> prove kernel, Policy, Domain Event, Delivery, and Thread invariants

Agent quality experiments
  versioned cases + repeated trials + outcome verifiers + complete traces
  -> measure Agent Executor quality and diagnose harness failures

Live smoke evidence
  pinned provider/model + synthetic data + no irreversible production effect
  -> show integration availability, never architectural correctness
```

This is the same separation visible in Harbor and Deep Agents. Harbor's
verifier writes the reward, while its ATIF trajectory records what the Agent
did. Deep Agents hard-fails correctness assertions but records efficiency
expectations without failing the test. OpenMagic should preserve that
distinction in its own vocabulary rather than importing another runtime.

The strict verdict is therefore:

> OpenMagic is implementation-ready when every deterministic invariant passes
> against a migrated PostgreSQL database from fresh processes. Agent and live
> results are separately reported evidence, and cannot turn a failed strict
> verdict into a pass.

## Ranked comparables

Scores are 0 to 5 for domain fit (D), target stack fit (S), production maturity
(M), architecture clarity (A), infrastructure and operations relevance (O),
testing quality (T), and documentation signal (Q).

| Rank | Source | D/S/M/A/O/T/Q | Total | Best match | Important mismatch | Use for |
| --- | --- | --- | ---: | --- | --- | --- |
| 1 | [Harbor](https://github.com/harbor-framework/harbor/tree/a19e01b835769fef0002476ac87cd5d633a9ccca) plus [Terminal-Bench 2.0](https://github.com/harbor-framework/terminal-bench-2/tree/2fd12b88aafdd04a52c298e3940bcb189f9766d6) | 5/5/4/5/5/5/5 | **34/35** | Agent-neutral task, sandbox, verifier, trial, and trajectory boundaries | Built for isolated benchmark environments, not transactional application durability | Eval task contract, outcome verification, reproducible trials, ATIF traces |
| 2 | [Deep Agents Python evals](https://github.com/langchain-ai/deepagents/tree/2266f58e4713d23ba0b30eb8646424d01165c11b/libs/evals) | 5/5/4/5/4/5/5 | **33/35** | Python Agent harness, hard correctness versus soft efficiency, repeated trials, LangSmith feedback | Measures an Agent SDK and coding harness, not business authority or durable effects | Agent Executor cases, trace mining, variance reporting, harness hill-climbing |
| 3 | [Inspect AI](https://github.com/UKGovernmentBEIS/inspect_ai/tree/326f9b5c9a666286619e8a9fbc475f44eec7f533) | 4/5/5/5/4/5/5 | **33/35** | Clean Dataset, Solver, Scorer separation, sandboxes, epochs, replayable logs | General model evaluation framework with more surface than this interview system needs | Vocabulary, scorer isolation, repeated samples, run configuration and logs |
| 4 | [OpenAI Evals](https://github.com/openai/evals/tree/8eac7a7de5215c907fbddc30efdaf316913eccdd) | 3/5/5/4/3/4/5 | **29/35** | Provider-facing eval target seam, registries, executable and model graders | Broad model evaluation registry, weaker fit for database fault proofs | Eval-driven development, grader selection, held-out data, continuous evaluation |

Deep Agents and Inspect AI tie numerically. Deep Agents ranks second because its
concrete harness-improvement loop is closer to the question OpenMagic needs to
answer. Popularity did not affect the score.

The user-supplied [Awesome Evals catalog](https://github.com/benchflow-ai/awesome-evals/blob/ad3340049db0a44c886569d2f0b4c7e4f8d95162/README.md)
identifies Inspect AI, OpenAI Evals, promptfoo, DeepEval, Braintrust, and related
tools. For OpenMagic, Inspect AI is the highest-fit additional framework because
it is Python, Agent-capable, sandbox-aware, and explicit about datasets,
solvers, scorers, epochs, and logs. Promptfoo's YAML and red-team surface is
useful later for prompt matrices, but it should not become the kernel proof
harness. DeepEval and hosted platforms would duplicate capabilities before the
local evidence contract is stable.

## What the LangChain case study actually proves

LangChain reports that it held `gpt-5.2-codex` fixed and improved its coding
Agent from 52.8 to 66.5 percent on 89 Terminal-Bench 2.0 tasks by changing the
harness. The changed dimensions were system prompt, tools, and middleware.
Harbor supplied sandboxes and verification, while LangSmith stored Agent
actions plus latency, token, and cost metrics. The repeatable analysis loop was:
fetch traces, analyze failures, synthesize targeted changes, then rerun the
benchmark. [LangChain, “Improving Deep Agents with harness engineering”](https://www.langchain.com/blog/improving-deep-agents-with-harness-engineering)

The most transferable findings are:

1. **Self-verification needs a deterministic trigger.** A prompt alone did not
   reliably make the Agent test its work. `PreCompletionChecklistMiddleware`
   intercepted completion and requested verification against the task, not
   against the Agent's own implementation.
2. **Context delivery is part of the harness.** `LocalContextMiddleware`
   injected working-directory and tool facts so the model spent less effort on
   error-prone discovery.
3. **Traces expose coupled failures.** A wrong outcome can come from reasoning,
   missing context, a bad tool description, time pressure, or the tool itself.
4. **Doom-loop detection is a temporary heuristic.** Repeated edits to one file
   triggered a reconsideration nudge. This is model-specific mitigation, not a
   durable domain contract.
5. **More reasoning is not monotonically better.** All-`xhigh` reasoning scored
   worse than `high` because more tasks timed out. Cost, latency, and task
   completion must be reported together.
6. **A benchmark gain is not a kernel proof.** The study changed an Agent
   harness and measured task outcomes. It did not prove transaction atomicity,
   crash recovery, lease fencing, exactly-once effects, or delivery authority.

The public [LangSmith trace dataset](https://smith.langchain.com/public/29393299-8f31-48bb-a949-5a1f5968a744/d?tab=2)
makes the case study inspectable. LangSmith defines a trace as a complete request
made of nested runs, including inputs, outputs, tool calls, model calls, and
metadata. Datasets are versioned and experiments retain output, scores, and
traces per example. [LangSmith tracing](https://docs.langchain.com/langsmith/observability-quickstart),
[dataset management](https://docs.langchain.com/langsmith/manage-datasets), and
[evaluation concepts](https://docs.langchain.com/langsmith/evaluation-concepts?mode=ui)
support an effective improvement loop, but hosted traces should remain an
optional copy of OpenMagic evidence. The durable application database and local
JSON report remain authoritative.

## Repository architecture extracts

### Harbor and Terminal-Bench

Harbor's core separation is the most useful comparable:

```text
Task
  instruction.md + task.toml + environment + hidden verifier + oracle solution
      |
      v
Agent adapter -> isolated environment -> resulting state
      |                                  |
      +---------- ATIF trace             +-> verifier -> reward
```

Concrete paths at commit
[`a19e01b`](https://github.com/harbor-framework/harbor/tree/a19e01b835769fef0002476ac87cd5d633a9ccca):

- `src/harbor/trial/trial.py` owns the lifecycle of one trial.
- `src/harbor/verifier/verifier.py` uploads hidden tests, runs them in the
  environment, and parses `/logs/verifier/reward.txt` or `reward.json`.
- `src/harbor/models/trajectories/trajectory.py` defines ATIF trajectory
  identity, ordered steps, final metrics, continuations, and subagent traces.
- `src/harbor/models/trajectories/step.py` defines system, user, and Agent
  steps, tool calls, observations, reasoning effort, and per-step metrics.
- `skills/create-task/SKILL.md` guides an author through instruction,
  environment, verifier, oracle solution, metadata, and Oracle validation. It
  calls verifier design the most important decision and recommends pytest for
  deterministic assertions.

The representative local Terminal-Bench task
`.reference/terminal-bench/original-tasks/sqlite-db-truncate/` makes every
piece concrete:

| File | Purpose |
| --- | --- |
| `task.yaml` | Agent instruction, category, timeouts, difficulty, and parser |
| `Dockerfile`, `docker-compose.yaml` | Fixed execution environment and `/app` workspace |
| `trunc.db` | Corrupted input artifact visible to the Agent |
| `solution.sh` | Oracle procedure used to prove the task is solvable, hidden from the Agent |
| `run-tests.sh` | Installs the pinned test runner and invokes the verifier |
| `tests/test_outputs.py` | Hidden outcome verifier that loads `/app/recover.json`, compares recovered rows, and requires more than six correct rows |

The Agent sees the instruction and its sandboxed workspace. It must produce
`/app/recover.json`. It does not need to emit a special verbal answer. Success
is determined from the resulting artifact by the verifier, not by the Agent's
self-report. An ATIF trace can explain which commands and observations led to
that artifact, but the reward is a separate verifier result.

Current Harbor ATIF v1.7 records:

- a run-scoped `session_id` and document-scoped `trajectory_id`;
- sequential steps beginning at 1;
- each step's source, message, model, reasoning effort, structured tool calls,
  observations, and metrics;
- links from observations to exact tool-call IDs;
- prompt, completion, cached tokens, cost, and optional token-level data;
- final metrics, continuation references, and embedded subagent trajectories;
- `llm_call_count = 0` for deterministic dispatch, with model-only fields
  forbidden in that case.

OpenMagic should copy the separation and trace-linking discipline, not ATIF's
entire schema. OpenMagic lifecycle truth already has stronger durable IDs and
authority boundaries than a benchmark trajectory.

### Deep Agents

Deep Agents is both a harness and a useful example of how a harness evaluates
itself. Concrete paths at commit
[`2266f58`](https://github.com/langchain-ai/deepagents/tree/2266f58e4713d23ba0b30eb8646424d01165c11b):

- `libs/evals/README.md` identifies end-to-end behavioral evals plus Harbor and
  LangSmith result sets.
- `libs/evals/CONTRIBUTING.md` defines a two-tier assertion model:
  `TrajectoryScorer.success(...)` hard-fails correctness and
  `TrajectoryScorer.expect(...)` records efficiency without failing.
- `libs/evals/tests/evals/utils.py` holds the Agent trajectory and scorer seam.
- `libs/evals/tests/evals/pytest_reporter.py` produces correctness, step ratio,
  tool-call ratio, solve rate, and duration summaries.
- `libs/evals/deepagents_harbor/langsmith.py` derives stable example IDs,
  synchronizes task examples, creates experiments, nests Agent traces under
  Harbor trials, and attaches verifier rewards as feedback.
- `libs/evals/deepagents_harbor/stats.py` reports Wilson confidence intervals
  and a minimum detectable effect for binary outcomes.
- `.github/scripts/analyze_eval_failures.py` analyzes failed trajectories into
  one proposed root-cause category, while retaining the original failure data.

Deep Agents also runs repeated trials and reports mean, median, sample standard
deviation, minimum, maximum, and the number of contributing trials. Its docs
explicitly warn that small single-run deltas can be noise. This is the right
shape for Agent quality, but not for deterministic kernel gates, where one
observed invariant violation is a failure.

### Inspect AI

Inspect's task recipe is explicitly Dataset + Solver + Scorer, with optional
epochs, sandbox, approval policy, limits, error policy, metadata, and separate
grader models. Its command line can replace a Solver or re-score an existing log
without regenerating the run. [Inspect tasks](https://inspect.aisi.org.uk/tasks.html)
and [scoring](https://inspect.aisi.org.uk/scoring.html).

Concrete paths at commit
[`326f9b5`](https://github.com/UKGovernmentBEIS/inspect_ai/tree/326f9b5c9a666286619e8a9fbc475f44eec7f533):

- `src/inspect_ai/_eval/task/task.py` defines the Task boundary.
- `src/inspect_ai/dataset/_dataset.py` defines Samples and deterministic
  shuffling.
- `src/inspect_ai/solver/` owns elicitation and Agent behavior.
- `src/inspect_ai/scorer/` owns outcome scores and metrics.
- `src/inspect_ai/log/` records and recovers eval logs.
- `src/inspect_ai/util/_sandbox/` isolates executable work.

Use its separation of behavior from scoring, explicit run configuration,
epochs, and re-scoring. Do not add Inspect as a dependency until OpenMagic needs
cross-model experiment interchange or its sandbox ecosystem. Pytest, PostgreSQL,
and the existing `server/evals/` package are enough for the first proof suite.

### OpenAI Evals guidance

OpenAI's official guidance says to evaluate early and often, make tests reflect
the real task distribution, log development activity so failures can become
cases, automate scoring where possible, calibrate automated graders with human
feedback, and continuously grow the set. It recommends locating where
nondeterminism enters the architecture, then testing instruction following,
functional output, tool selection, argument precision, and handoffs there.
[OpenAI evaluation best practices](https://developers.openai.com/api/docs/guides/evaluation-best-practices)

For OpenMagic, that means deterministic tests at every deterministic boundary,
and model evals only where an Agent actually chooses language, tools, or
arguments. Exact match, executable checks, and database-state checks should be
preferred whenever they express the requirement. Model graders are appropriate
for semantic quality, after their agreement with human examples has been
checked.

## Kernel correctness and Agent quality are different questions

| Question | Correct evaluator | Repeat policy | Can block release? |
| --- | --- | --- | --- |
| Did one Command commit its receipt, application changes, kernel transition, and Domain Events atomically? | PostgreSQL state and public API assertions | Every test run, plus injected crash points | Yes |
| Did concurrent claims preserve one current Attempt and fence losers? | Database constraints, transaction results, and final state | Seeded race loop | Yes |
| Did restart reconstruct only from durable state? | Fresh process with no shared objects or caches | Every crash window | Yes |
| Did an uncertain External Effect avoid unsafe automatic retry? | Provider stub, dispatch evidence, and final policy state | Before, during, and after dispatch faults | Yes |
| Did a Delivery append at most one Message to its exact Thread? | Database state, delivery receipt, and exact Thread Message sequence | Duplicate, crash, and competing-worker cases | Yes |
| Did a deterministic Executor return the expected typed result? | Exact or structural result verifier | Once per case is enough | Yes |
| Did an Agent select the correct tool and arguments? | Outcome verifier plus trajectory checks | Multiple trials with fixed configuration | No, report separately until an explicit product threshold exists |
| Was an Agent response useful, clear, and grounded? | Calibrated human rubric or model grader | Multiple trials and held-out cases | No |
| Can a pinned live model/provider complete a synthetic journey? | Smoke outcome verifier | Opt-in, recorded configuration | No |

The Agent Executor returns a candidate typed Attempt result. It never receives
authority to accept its own result, complete the Workflow, dispatch an External
Effect, or append a Message. Therefore an Agent eval can answer “how often does
this Executor produce a useful candidate?” It cannot answer “is the Workflow
kernel safe?”

## Recommended evidence matrix

| Evidence family | Required scenario | Pass condition | Evidence artifact |
| --- | --- | --- | --- |
| Definition readiness | Every installed Definition and Route, including start, success, failure, revision, confirmation, and Signal paths | Unknown references, incompatible schemas, cycles, missing executable support, and conflicting identity fail startup closed | Readiness JSON with Definition identity, digest, Route coverage, build SHA |
| Command atomicity | Crash or exception before commit, during handler work, and after commit response loss | No partial state; exact replay returns the value-identical receipt; conflicting Command ID reuse fails | Transaction state snapshot and Command receipt hash |
| Route atomicity | Fault at each materialization boundary | Whole finite batch plus dependencies and Trace Event commits, or none commits | Before/after occurrence IDs and trace facts |
| Concurrency | Competing Command replay, Route activation, Step claim, Wait Signal, Attempt result, and Delivery claim | One winner where cardinality is one; losing operations return replay or a typed conflict; constraints agree with protocol | Per-contender result and final database rows |
| Lease fencing | Result and renewal before expiry, at expiry, after expiry, after abandonment, and after Instance close | Only the current unexpired Attempt mutates state; stale work is rejected | Attempt Number, deadlines, Worker provenance, rejection |
| Process loss | Kill Worker after claim, after Executor return but before commit, after accepted result, and during recovery | New process reconstructs from PostgreSQL; accepted output is not recomputed; stale process has no authority | Fresh-process IDs and canonical result hash |
| Retry | Safe classified failure at every Attempt number and exhausted budget | Exact finite delay schedule; no inferred retry safety; one terminal Step result | Attempt history and Policy decision evidence |
| External Effect uncertainty | Fail before dispatch record, after dispatch record before I/O, provider success with response loss, provider failure, reconciliation | One logical effect per Step; no automatic retry after possible commit; uncertainty remains explicit until evidence resolves it | Dispatch identity, provider request ID, evidence classification |
| Domain Event atomicity | Success, failure, approval, revision, and cancellation transitions | Required Domain Events commit with their source transition and never appear without it | Event IDs linked to Command, Workflow, and transition |
| Exact-thread Delivery | Duplicate creation, competing claim, crash before append, crash after append before acknowledgement, wrong Thread proposal | One Delivery identity maps to one exact Thread; at most one Message append; recovery reuses durable append identity; wrong Thread cannot receive it | Delivery, Delivery Attempt, Thread, Message, Domain Event IDs |
| Deterministic Executor | Happy, typed failure, malformed result, timeout, and late result | Only contract-valid typed results can be accepted; malformed or late results cannot mutate lifecycle state | Input/output schemas, hashes, Attempt result |
| Agent Executor | Tool choice, argument extraction, refusal, ambiguity, irrelevant context, malformed tool result, timeout, and revision | Outcome scorer passes scenario rubric; safety boundary remains correct even when quality fails | Trial config, complete sanitized trajectory, outcome scores |
| Trace completeness | Every scenario above | Every accepted transition is reconstructable by durable IDs; no secret or private free-form content is required to prove it | Versioned local JSON trace and redaction audit |
| Backpressure | More ready Steps and Deliveries than Workers can claim, slow Executor, slow provider, retry-delayed work | Bounded claims, no duplicate canonical result, no starvation in the observed window, and measured queue depth, claim latency, throughput, lock wait, and recovery time | Machine-readable load report with environment and database config |
| Playground safety | Reset, repeated run, intentional failure, and disconnected provider | Synthetic identities only, provider effects disabled by default, one reset command, deterministic seed, explicit live opt-in, no secrets in artifacts | Manifest, seed, build SHA, redaction result, report links |
| Claim discipline | Interview report and README wording | Claims do not exceed the evidence lane that produced them | Generated “may claim / may not claim” section |

For race tests, use a deterministic barrier to align contenders, record the
seed, and run enough iterations to exercise scheduling variation. The gate is
zero invariant violations, not a success percentage. For backpressure, publish
the observed environment and distributions rather than inventing a production
service-level objective from a laptop run.

## Agent eval dataset and metrics

Start with OpenMagic-specific cases rather than Terminal-Bench tasks. The Agent
should act through the same public tools used by the application, while a hidden
verifier judges the durable outcome.

### Dataset shape

Each case should pin:

- case ID and schema version;
- synthetic Thread Context cutoff, bounded Domain Event context, audience, and
  locale;
- exact installed Agent version, system prompt digest, tool schema digest,
  model ID, provider, reasoning setting, temperature, and seed where supported;
- initial PostgreSQL fixture identity and expected allowed outcomes;
- prohibited operations and safety invariants;
- deterministic outcome verifier plus optional semantic rubric;
- tags for capability, difficulty, failure family, and held-out split.

Keep development and held-out cases separate. A failure mined from a trace may
join the development set immediately, but the promotion decision should be
tested on an adjacent held-out case, not the exact failure the harness was just
tuned against.

### Metrics

Report these per case, category, model, prompt digest, and full experiment:

- deterministic outcome pass rate;
- tool selection and exact argument accuracy;
- prohibited action count, which must remain zero;
- semantic rubric score and grader identity where used;
- Agent steps, model calls, tool calls, retries, and loop detections;
- wall latency, model latency, tokens, cached tokens, and cost;
- timeout and infrastructure-error counts, kept separate from task failures;
- mean, median, sample standard deviation, minimum, maximum, and contributing
  trial count;
- Wilson confidence interval for binary pass rates and minimum detectable
  effect before claiming one harness is better.

Pair outcome and efficiency metrics. A higher pass rate bought with timeouts,
unbounded calls, or materially higher cost is not an unqualified improvement.

## Harness hill-climbing without benchmark overfitting

Use the LangChain loop, with stronger experimental controls:

1. Freeze the dataset version, held-out split, model/provider identifier,
   reasoning setting, sandbox image, tool schemas, dependencies, and build SHA.
2. Record a hypothesis from a named failure cluster, such as “the Agent stops
   before verifying exact Thread selection.”
3. Change one harness dimension when possible: prompt, tool description,
   deterministic context injection, or middleware.
4. Rerun the affected development cases, then the complete held-out and safety
   suites.
5. Compare repeated trials with confidence intervals and cost/latency, not one
   attractive run.
6. Inspect regressions and successes. Human review approves the interpretation
   and the next change.
7. Version the promoted harness and retain both reports and traces.

Avoid these common forms of overfitting:

- putting hidden verifier details or exact expected tool sequences in the
  Agent prompt;
- adding a special case keyed to one benchmark phrase, path, Party, Workflow,
  or case ID;
- repeatedly tuning on the held-out set until it becomes training data;
- accepting self-verification text as proof that verification occurred;
- selecting only the model on which the harness was optimized and calling the
  behavior model-agnostic;
- treating an LLM failure-analysis label as ground truth without checking the
  trace and verifier;
- rerunning failed trials until one passes, then reporting only the pass;
- silently changing timeouts, reasoning effort, dependencies, or provider
  versions between experiments.

Deterministic context injection should be allowlisted and escaped. Deep Agents'
own threat model notes that reading project files into a system prompt creates
a prompt-injection boundary. OpenMagic should inject typed environment facts,
not arbitrary workspace text, into production Agent Runs.

## Recommended local implementation shape

Extend the existing `server/evals/` package. Do not create a second runtime and
do not put eval concepts into the kernel.

```text
server/evals/
  evidence_contracts.py       lane, case, run, observation, verdict schemas
  evidence_manifest.py        build, DB, dependency, Definition and harness pins
  evidence_runner.py          bounded lane execution and report assembly
  traces.py                   sanitized evidence projection and correlation checks
  kernel_scenarios/           atomicity, crash, race, lease, retry, backpressure
  delivery_scenarios/         Domain Event, Delivery, exact Thread, append recovery
  executor_scenarios/         deterministic and Agent Executor outcome cases
  agent_cases/                versioned synthetic inputs, rubrics, held-out split
  reports.py                  canonical JSON plus derived interview Markdown

server/tests/evals/
  test_issue65_evidence_contract.py
  test_issue65_kernel_failure_proofs.py
  test_issue65_delivery_failure_proofs.py
  test_issue65_agent_case_verifiers.py
  test_issue65_playground_safety.py
```

This should deepen current code rather than discard it:

- `server/evals/v0_evidence.py` already separates deterministic gates,
  diagnostics, and live evidence.
- `server/tests/evals/test_v0_evidence_report.py` already proves that a failed
  deterministic lane alone fails the strict verdict, and that diagnostic/live
  lanes are separately classified.
- `server/tests/evals/test_workflow_recovery_evidence.py` already emits a
  machine-readable recovery report from real PostgreSQL scenarios.
- `server/tests/evals/recovery_scenarios.py` already covers duplicate Cause,
  restart while awaiting exact approval, Worker loss before dispatch, and
  Worker loss after dispatch.
- `server/tests/evals/test_paired_coordination_eval.py` already uses scripted
  model output to prove tool and database behavior deterministically.
- `server/tests/evals/test_live_paired_coordination.py` already keeps a real
  model comparison opt-in and blocks external execution.

The first implementation slice should define the versioned evidence schemas and
matrix, then move the existing lanes under that schema without changing their
behavior. Add missing crash windows, concurrency cases, exact-thread Delivery,
and backpressure after the report format is stable.

### Trace projection

Every evidence record should carry enough typed correlation to reconstruct the
boundary without private prompt content:

```text
Evidence Run
  build SHA, suite version, database/migration identity, environment, seed
    -> Case
       case ID, lane, initial fixture digest, verifier version
         -> durable correlation
            Command ID -> Workflow ID -> Instance ID
              -> Step ID -> Attempt ID and Attempt Number -> Worker ID
              -> Domain Event ID -> Delivery ID -> Delivery Attempt ID
              -> exact Thread ID -> Message ID
              -> Agent Run ID, Agent version, model and prompt/tool digests
         -> observations
            transition, timestamp, input/output hash, latency, tokens, cost,
            provider request ID, typed error, redaction status
         -> verifier result
            pass/fail, scores, explanation, invariant violations
```

Kernel Trace Events remain kernel lifecycle facts. Domain Events remain
application facts. Agent trajectories remain diagnostic records. The report
links them by durable IDs but does not collapse them into one event vocabulary.

### Reproducibility contract

A report is admissible only when it records:

- full lowercase Git SHA and clean checkout;
- suite and case schema versions;
- migration head and Definition identities/digests;
- PostgreSQL version and relevant configuration;
- dependency lock digest and sandbox/container digest where used;
- fixed clock and randomness seeds where the case permits them;
- Agent, prompt, tool schema, provider, model, and reasoning configuration;
- exact command, environment allowlist, start/end timestamps, timeout, and
  artifact hashes;
- all expected cases, including failures and infrastructure errors.

Missing trials must make the report incomplete. They must not disappear from
the denominator.

## What to emulate, defer, and avoid

### Emulate now

- Harbor's explicit task, environment, verifier, oracle, trial, and trace
  boundaries.
- Terminal-Bench's hidden executable outcome checks and solvability check with
  an Oracle.
- Deep Agents' hard correctness versus soft efficiency split.
- Deep Agents' repeated-trial statistics and trace-to-verifier feedback link.
- Inspect's Dataset, Solver, Scorer vocabulary and ability to score stored
  outcomes separately from generation.
- LangSmith's nested trace inspection and versioned experiment comparison as an
  optional observability sink.
- OpenAI's eval-driven development, real-distribution cases, continuous case
  growth, and human calibration of automated graders.

### Defer

- Harbor or Inspect as runtime dependencies, until cross-agent benchmark
  interchange is a demonstrated need.
- ATIF export, until OpenMagic needs to compare its Agent Executor in an
  external harness. Keep the local trace schema mappable to ATIF.
- hosted LangSmith as a required service. Add an exporter only after local
  evidence is complete and redacted.
- generalized benchmark registries, model leaderboards, multi-agent evals,
  RL rollouts, and prompt optimizers.
- a public performance SLO. First publish reproducible observed measurements.

### Avoid

- using Agent success to excuse a failed kernel invariant;
- using a mocked in-memory store for durability, crash, or race claims;
- checking private implementation calls when a public outcome can be verified;
- making the verifier depend on the same model being evaluated;
- recording secrets, real insurance data, raw approval content, or unrestricted
  Thread history in evidence artifacts;
- claiming exactly-once External Effects. The valid claim is fenced durable
  dispatch with explicit uncertainty and evidence-backed reconciliation;
- claiming general durable Python, arbitrary graphs, fleet-scale operation, or
  parity with Temporal, DBOS, Cloudflare Workflows, or other mature engines.

## Claim boundaries

After the full deterministic matrix passes, OpenMagic may claim:

- the application-owned single-PostgreSQL kernel preserves the tested
  Definition, transaction, replay, race, lease, recovery, and retry contracts;
- deterministic and Agent Executors use the same Step and Attempt boundary;
- Workflow Policy, not an Agent, owns authority, completion, retry safety, and
  External Effect decisions;
- the tested Domain Event and Delivery path recovers to at-most-one Message in
  one exact Thread under the documented crash windows;
- the playground reproduces the published synthetic cases from a pinned build;
- the named Agent harness achieved the reported distribution on the pinned case
  set and configuration.

It may not claim:

- exactly-once external side effects;
- correctness for untested crash windows, databases, Definition versions,
  providers, models, or deployment shapes;
- model-agnostic Agent quality from one optimized model;
- production throughput, availability, or fleet-scale maturity from an
  interview playground;
- that LangSmith, Harbor, LangGraph, or an Agent checkpoint is the durable
  source of Workflow truth;
- equivalence to a general workflow engine.

## Sources

All web sources were accessed 2026-07-14.

- Vivek Trivedy, LangChain, [“Improving Deep Agents with harness engineering”](https://www.langchain.com/blog/improving-deep-agents-with-harness-engineering).
- LangChain, [public harness-engineering trace dataset](https://smith.langchain.com/public/29393299-8f31-48bb-a949-5a1f5968a744/d?tab=2).
- LangChain, [LangSmith tracing quickstart](https://docs.langchain.com/langsmith/observability-quickstart),
  [evaluation concepts](https://docs.langchain.com/langsmith/evaluation-concepts?mode=ui),
  and [dataset management](https://docs.langchain.com/langsmith/manage-datasets).
- LangChain, [Deep Agents Python repository at `2266f58`](https://github.com/langchain-ai/deepagents/tree/2266f58e4713d23ba0b30eb8646424d01165c11b).
- Harbor Framework, [Harbor at `a19e01b`](https://github.com/harbor-framework/harbor/tree/a19e01b835769fef0002476ac87cd5d633a9ccca)
  and [documentation](https://www.harborframework.com/docs/run-jobs/run-evals).
- Harbor Framework, [Terminal-Bench 2.0 at `2fd12b8`](https://github.com/harbor-framework/terminal-bench-2/tree/2fd12b88aafdd04a52c298e3940bcb189f9766d6).
- UK AI Security Institute, [Inspect AI at `326f9b5`](https://github.com/UKGovernmentBEIS/inspect_ai/tree/326f9b5c9a666286619e8a9fbc475f44eec7f533),
  [Tasks](https://inspect.aisi.org.uk/tasks.html), and
  [Scoring](https://inspect.aisi.org.uk/scoring.html).
- OpenAI, [Evaluation best practices](https://developers.openai.com/api/docs/guides/evaluation-best-practices)
  and [OpenAI Evals at `8eac7a7`](https://github.com/openai/evals/tree/8eac7a7de5215c907fbddc30efdaf316913eccdd).
- BenchFlow, [Awesome Evals at `ad33400`](https://github.com/benchflow-ai/awesome-evals/blob/ad3340049db0a44c886569d2f0b4c7e4f8d95162/README.md).
