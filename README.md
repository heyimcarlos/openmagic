# OpenMagic

OpenMagic is being rebuilt as a reusable durable runtime plus an independently
owned Example Insurance reference application. Issue 67 establishes the clean
runtime foundation. Later tracer bullets add Workflow behavior.

## Workspace

```text
packages/openmagic-runtime/          reusable runtime distribution
reference-apps/example-insurance/   reference Application Package
apps/api/                            deployment composition distribution
apps/playground/                     demonstration application
evals/                               private evidence distribution
```

Production dependencies flow in one direction:

```text
API -> Example Insurance -> OpenMagic Runtime
API ---------------------> OpenMagic Runtime
```

The eval distribution is never a production dependency.

## Development

Requires Python 3.11+, uv, and Docker.

```bash
uv sync --all-packages --locked --group dev
uv run ruff format --check .
uv run ruff check .
uv run ty check packages/openmagic-runtime/src reference-apps/example-insurance/src apps/api/src evals/src apps/playground/src
uv run ty check packages/openmagic-runtime/tests reference-apps/example-insurance/tests evals/tests
uv run pytest
```

Migrate an explicitly configured PostgreSQL database:

```bash
uv run example-insurance-migrate --database-url postgresql://user:password@host/database
```

No production process reads `.env`. API and Worker entry points require their
database URL, host, and port as explicit arguments.

## Enterprise evidence

The private eval distribution owns four separate evidence products. Only the
deterministic product can pass or fail the release gate.

```text
deterministic correctness -> strict release verdict
Agent quality             -> measured development and held-out outcomes
provider availability     -> opt-in live smoke
playground behavior       -> synthetic demonstration only
```

Generate the versioned JSON schema and the complete deterministic package from
a clean checkout:

```bash
mkdir -p .artifacts/issue71
uv run openmagic-evidence schema --output .artifacts/issue71/schema.json
uv run openmagic-evidence audit-surface --repository-root .
uv run pytest evals/tests/test_foundation_wheels.py
uv run openmagic-evidence deterministic \
  --repository-root . \
  --output .artifacts/issue71/deterministic-release.json
uv run openmagic-evidence races \
  --repository-root . \
  --output .artifacts/issue71/races.json
uv run openmagic-evidence processes \
  --repository-root . \
  --working-directory .artifacts/issue71/processes \
  --output .artifacts/issue71/processes.json
```

Generate the other evidence products and final claim report:

```bash
uv run openmagic-evidence agent-quality \
  --repository-root . \
  --output .artifacts/issue71/agent-quality.json \
  --timeout-seconds 300
uv run openmagic-evidence playground \
  --repository-root . \
  --working-directory .artifacts/issue71/playground \
  --output .artifacts/issue71/playground.json \
  --timeout-seconds 120
uv run openmagic-evidence live-smoke \
  --repository-root . \
  --provider openai-responses \
  --model unavailable \
  --endpoint https://api.openai.com/v1/responses \
  --synthetic-case-id live.synthetic.unavailable \
  --output .artifacts/issue71/live-smoke.json \
  --timeout-seconds 10
```

Live smoke remains unavailable unless `--allow-live` and a mode `0600`
credential file are supplied with an explicitly pinned provider endpoint,
model, derived configuration digest, and reversible synthetic case. Credential values
are never included in commands, logs, or artifacts.

The public synthetic demonstrations use a fresh database and local provider:

```bash
uv run openmagic-evidence demo-renewal \
  --repository-root . \
  --working-directory .artifacts/issue71/renewal-demo \
  --output .artifacts/issue71/renewal-demo.json
uv run openmagic-evidence demo-verification \
  --repository-root . \
  --output .artifacts/issue71/verification-demo.json
uv run openmagic-evidence claim-report \
  --deterministic .artifacts/issue71/deterministic-release.json \
  --agent-quality .artifacts/issue71/agent-quality.json \
  --live-smoke .artifacts/issue71/live-smoke.json \
  --playground .artifacts/issue71/playground.json \
  --processes .artifacts/issue71/processes.json \
  --races .artifacts/issue71/races.json \
  --renewal-demo .artifacts/issue71/renewal-demo.json \
  --verification-demo .artifacts/issue71/verification-demo.json \
  --output .artifacts/issue71/claim-report.md
```

All artifacts are canonical, schema validated, redaction audited, and pinned to
the exact clean Git build, lock digest, migration heads, Definition digests,
case corpus, command, and PostgreSQL deployment shape.
