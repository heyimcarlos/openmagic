# Issue 18 Workflow retrieval evaluation

Recorded: 2026-07-12

The deterministic V0 landscape contains the target renewal plus historical,
same-name, wrong-organization, wrong-Kind, other-policyholder, and unauthorized
distractors. The executable evaluation uses three sufficient retrieval
expressions and records the resulting properties in the pytest report.

| Diagnostic | Result |
| --- | ---: |
| Hit@1 | 1.0 |
| Hit@3 | 1.0 |
| Mean reciprocal rank | 1.0 |
| Maximum response size | 1,885 bytes |
| Maximum approximate token burden | 471 tokens |

Run it with:

```bash
uv run pytest server/tests/evals/test_workflow_retrieval_eval.py
```

These are small-fixture V0 diagnostics, not a claim about production retrieval
quality. The acceptance suite separately checks ambiguity, iterative
refinement, authorization leakage, cursor integrity, and no-mutation behavior.
