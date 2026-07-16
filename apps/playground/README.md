# OpenMagic playground

The playground is a synthetic demonstration surface. It is not correctness
evidence and is never a production dependency. External effects are disabled
by default, fixtures are pinned to `issue-71.v1`, and provider behavior is local.

Print the safety contract:

```bash
uv run openmagic-playground manifest
uv run openmagic-playground controls
uv run openmagic-playground demo-renewal
uv run openmagic-playground demo-verification
uv run openmagic-playground exercise --working-directory .artifacts/playground
```

The public `exercise` command owns separate API, Workflow Worker, and Delivery
Worker pools. It exercises start, drain, atomic reset, fresh restart, and stop.
The private eval command invokes this installed surface as an external observer.
