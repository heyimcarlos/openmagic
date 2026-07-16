# OpenMagic playground

The playground is a synthetic demonstration surface. It is not correctness
evidence and is never a production dependency. External effects are disabled
by default, fixtures are pinned to `issue-71.v1`, and provider behavior is local.

Print the safety contract:

```bash
PYTHONPATH=apps/playground/src uv run python -m openmagic_playground manifest
PYTHONPATH=apps/playground/src uv run python -m openmagic_playground controls
```

The private `openmagic-evidence playground` command controls separate API,
Workflow Worker, and Delivery Worker pools. It verifies process stop, drain,
fresh restart, deterministic fixtures, local provider behavior, and reset.
