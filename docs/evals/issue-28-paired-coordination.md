# Issue 28 paired coordination evaluation

The evaluator runs the same synthetic renewal requests through the inherited
legacy Interaction profile and the V0 Workflow profile.

Strict V0 correctness includes authorized Workflow resolution, safe
clarification or no-match behavior, correct proposal, and no unintended Job
mutation. The legacy result, Packet reads, context burden, tool counts, and
segmented duration are diagnostics.

The legacy profile uses the inherited prompt and tool schemas, but its
named-agent dispatch boundary is observation-only. The evaluator never starts
an Execution Agent, calls Composio, or sends an email.

Run deterministic acceptance coverage:

```bash
uv run pytest -q \
  server/tests/evals/test_paired_coordination_report.py \
  server/tests/evals/test_paired_coordination_eval.py
```

Run one credentialed real-model trial:

```bash
set -a
source .env
set +a
OPENMAGIC_RUN_PAIRED_COORDINATION_EVAL=1 \
OPENMAGIC_EVAL_APPLICATION_BUILD="$(git rev-parse HEAD)" \
uv run pytest -q server/tests/evals/test_live_paired_coordination.py
```

Optional configuration:

```text
OPENMAGIC_PAIRED_EVAL_MODEL
OPENMAGIC_PAIRED_EVAL_OUTPUT_DIR
```

The default evidence directory is `/tmp/openmagic-paired-eval`. Each run gets an
isolated directory containing one JSON record per trial, one aggregate JSON
record, and one Markdown summary. Reports contain synthetic scenario
identifiers, build and model identity, typed outcomes, digests, stable synthetic
Workflow IDs, counts, and timings. They do not contain prompts, conversation
history, credentials, raw model payloads, or provider data.
