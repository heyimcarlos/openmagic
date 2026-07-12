# PostgreSQL protocol implementation comparables

Date: 2026-07-12

This note records the implementation convergence used by
[Persist and inspect an atomic renewal Workflow graph](https://github.com/heyimcarlos/openmagic/issues/17).

## Sources

- Prefect source at commit
  [`0e7435055e18952aa8604dab78507b087a18defb`](https://github.com/PrefectHQ/prefect/tree/0e7435055e18952aa8604dab78507b087a18defb).
- [SQLAlchemy 2.0 asynchronous I/O](https://docs.sqlalchemy.org/en/20/orm/extensions/asyncio.html).
- [SQLAlchemy constraint and index definitions](https://docs.sqlalchemy.org/en/20/core/constraints.html).
- [Alembic asyncio cookbook](https://alembic.sqlalchemy.org/en/latest/cookbook.html#using-asyncio-with-alembic).
- [Testcontainers PostgreSQL module](https://testcontainers-python.readthedocs.io/en/latest/modules/postgres/README.html).
- Open SWE `pyproject.toml` at commit
  `30832d29bcfa12c5669c374add585e8b829a8ac2`.
- Deep Agents `libs/deepagents/pyproject.toml` at commit
  `14f384fc0083c07a7f44f97543b40b74cf93c13f`.

## Observed convergence

### Transaction ownership

Prefect creates an async session per operation and lets the application model
function receive that session. SQLAlchemy documents `AsyncSession` as a mutable
transactional object that must not be shared across concurrent tasks. OpenMagic
therefore creates one session for one Control Plane command and keeps every
accepted transition inside one explicit transaction.

The Control Plane owns transaction lifetime. Callers never receive a session,
repository object, or ORM row. This keeps the public interface aligned with the
domain rule that only the Control Plane commits Workflow state.

### Schema authority

Prefect treats Alembic migrations as the deployable schema and tests migration
heads independently of ordinary model behavior. SQLAlchemy recommends stable
constraint naming so later migrations can address constraints predictably.
OpenMagic therefore uses:

- Alembic as the only production schema-creation path.
- Explicit names for every important constraint and index.
- SQLAlchemy metadata that mirrors the migration for typed query construction.
- A migration consistency test rather than `Base.metadata.create_all()` in
  application startup.

### PostgreSQL semantics

The locked OpenMagic protocol depends on PostgreSQL JSONB, partial unique
indexes, composite foreign keys, and row locking. A SQLite substitute would not
exercise those semantics. Local integration tests therefore use an isolated
PostgreSQL container when no explicit test URL is supplied. CI may supply a
dedicated PostgreSQL service through the same URL contract.

### Python project structure

Open SWE and Deep Agents converge on `pyproject.toml`, `uv`, Ruff, pytest, and a
declared development dependency group. OpenMagic adopts that structure while
retaining `server/requirements.txt` temporarily as a compatibility install
surface for the inherited README path.

## Chosen module seams

1. **Workflow contracts:** typed caller input and read projections.
2. **Workflow Kind registry:** recognized contracts, validation, derived retry
   limits, and trusted execution strategies.
3. **Workflow database:** async engine and session factory only.
4. **Workflow Control Plane:** the public command and trace interface that owns
   authorization, transaction lifetime, aggregate locking, and persistence.

Tests call the Control Plane and inspect its returned trace. Separate migration
tests inspect PostgreSQL structural enforcement because the schema itself is
the interface under test.

For the V0 creation tracer, the initial Workflow Event preserves the trusted
actor and organization scope used to authorize development-trace reads. This
is audit provenance, not duplicated Workflow input or a replacement for the
relational Party model planned by the retrieval ticket. The creation command
builds its return trace inside the write transaction, so it cannot commit the
aggregate and then report a false command failure because a second read loses
authority.

## Rejected alternatives

- **Prefect-style multi-dialect database interface:** OpenMagic has one selected
  PostgreSQL protocol. Abstracting SQLite and PostgreSQL would widen the
  interface while weakening the exact constraints being demonstrated.
- **A repository per table:** callers work with one Workflow aggregate. Exposing
  six table repositories would distribute transaction and lifecycle knowledge.
- **Pure event sourcing:** locked decisions require relational current state
  plus append-only Events.
- **ORM `create_all()` at startup:** it bypasses migration review and cannot
  safely evolve deployed databases.
- **SQLite unit tests as protocol evidence:** they cannot prove PostgreSQL
  locking, partial indexes, JSONB, or composite constraint behavior.
- **A generic workflow framework:** V0 implements only the renewal tracer's
  registered Kinds and lifecycle protocol.
