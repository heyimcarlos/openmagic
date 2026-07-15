---
status: accepted
---

# Rebuild as runtime and reference application packages

OpenMagic will replace the mixed prototype with independently installable Python distributions in one uv workspace: the reusable OpenMagic Runtime, Example Insurance reference application, API composition layer, and private eval harness. Each Application Deployment owns one PostgreSQL database containing independently migrated runtime and application schemas. The replacement starts from empty package roots and fresh migration baselines because preserving the legacy facade, source layout, tables, migration history, or test suite would carry obsolete ownership and vocabulary into the new design.

## Consequences

- Production imports are one-way: API to application or runtime, application to runtime, and runtime never to application.
- The runtime exposes explicit role-scoped Modules rather than a root convenience facade, and persistence internals remain private.
- The prototype has no database upgrade path. Legacy data and migrations are deleted, then synthetic environments are rebuilt from the new baselines.
- Legacy source is consulted only through Git history. Accepted contracts and invariants are requirements, not code to extract mechanically.
- Correctness is established through a clean-slate, integration-first evidence suite using real PostgreSQL transactions, packaged migrations, independently restartable processes, and local protocol servers rather than internal mocks.
- Dual reads, dual writes, SQL table renames, compatibility aliases, translated legacy tests, and rolling compatibility with old processes are explicitly rejected.
