# Issue 29 Workflow recovery evidence

The deterministic recovery suite proves four boundaries through public
application interfaces:

- identical authenticated Cause replay creates one stable two-Job graph;
- a fresh Interaction toolbox reads the frozen Packet and grants exact approval
  after every prior application object is disposed;
- pre-dispatch Worker loss abandons and fences Run 1, then permits one Run 2;
- post-dispatch Worker loss abandons Run 1, leaves the Send Job waiting, and never
  invokes the deterministic adapter twice.

Run the acceptance suite:

```bash
uv run pytest -q server/tests/evals/test_workflow_recovery_evidence.py
```

Write an exact-build evidence artifact:

```bash
OPENMAGIC_RECOVERY_EVAL_APPLICATION_BUILD="$(git rev-parse HEAD)" \
OPENMAGIC_RECOVERY_EVAL_OUTPUT_DIR=/tmp/openmagic-recovery-eval \
uv run pytest -q server/tests/evals/test_workflow_recovery_evidence.py
```

Each run gets an exclusive directory with `report.json` and `report.md`. The
report contains stable Workflow, Job, Run, and Event identifiers, lifecycle
states, and durable Run build provenance. Approval Grant identity, its dispatch
link, the dispatch Run, duplicate replay, conflicting-command rejection, stale
Run rejection, and deterministic adapter invocation count remain explicit.
Cause references are hashed. Workflow inputs, Job inputs and outputs, provider
payloads, prompts, messages, email addresses, and credentials are omitted.

The deterministic adapter is called only for the post-dispatch crash seam. No
Composio call or external email occurs in this suite.
