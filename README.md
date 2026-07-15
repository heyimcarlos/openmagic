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
uv run ty check packages/openmagic-runtime/src reference-apps/example-insurance/src apps/api/src evals/src
uv run pytest
```

Migrate an explicitly configured PostgreSQL database:

```bash
uv run example-insurance-migrate --database-url postgresql://user:password@host/database
```

No production process reads `.env`. API and Worker entry points require their
database URL, host, and port as explicit arguments.
