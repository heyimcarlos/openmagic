# Clean-slate integration testing and evidence comparables

Date: 2026-07-14

Issue: [Choose the deletion-first migration](https://github.com/heyimcarlos/openmagic/issues/58)

## Decision frame

- **Target:** a new test and evidence system for separately packaged OpenMagic
  Runtime, Example Insurance, API, playground, and eval distributions.
- **Stack:** Python 3.11+, pytest, FastAPI, SQLAlchemy, Alembic, PostgreSQL,
  separate API, Workflow Worker, and Delivery Worker processes.
- **Domain:** durable workflow execution, exact transaction outcomes, process
  recovery, concurrent claims, policy-controlled effects, Domain Event
  delivery, and nondeterministic Agent execution.
- **Scale:** local and CI proof of correctness, plus laptop-scale backpressure
  evidence. It does not claim production fleet scale.
- **Hard constraints:** real PostgreSQL, real migrations, explicit transaction
  commits, public interfaces, fresh processes for recovery claims, no reliance
  on process memory, and no model or provider result deciding kernel safety.
- **Testing preference:** integration-first, end-to-end where the claim crosses
  processes, minimal mocking, and a clean slate rather than migration of the
  legacy test suite.

## Ranked comparables

Scores are 0 to 5 for domain fit, target stack fit, production maturity,
architecture clarity, infrastructure and operations relevance, testing quality,
and documentation signal.

| Rank | Source | D/S/M/A/O/T/Q | Total | Best match | Important mismatch | Use for |
| --- | --- | --- | ---: | --- | --- | --- |
| 1 | [DBOS Transact Python](https://github.com/dbos-inc/dbos-transact-py/tree/e9e351574b9a01aad62ead31bddb629fe1eb3e9c) | 5/5/4/4/5/5/4 | **32/35** | Python, PostgreSQL, durable workflows, commit fault injection, concurrency, recovery, and database chaos | Replays Python workflow code and includes more engine features than OpenMagic accepts | Named commit failure points, real database chaos, high-volume recovery, and single-execution tests |
| 2 | [Zero to Production](https://github.com/LukeMathWalker/zero-to-production/tree/970987c5f793af6fc8e557731c9bbb23b620451e) | 3/2/4/5/4/5/5 | **28/35** | Black-box application tests, random ports, a new migrated PostgreSQL database per test, and protocol-level email server | Rust, single web process, and no durable worker recovery | `TestApp` harness shape, test isolation, migrations, HTTP-first acts, and database outcome verification |
| 3 | [Prefect](https://github.com/PrefectHQ/prefect/tree/6c39a5eb0c0e0d8e40c535827e4e25d067bd86c3) | 4/5/5/4/5/4/5 | **32/35** | Python orchestration with integration tests that launch actual API and worker subprocesses | Much larger distributed product and broader deployment matrix | Process-level API and worker tests, bounded polling, isolated CLI execution, and lifecycle cleanup |
| 4 | [Harbor](https://github.com/harbor-framework/harbor/tree/a19e01b835769fef0002476ac87cd5d633a9ccca) plus [Deep Agents evals](https://github.com/langchain-ai/deepagents/tree/2266f58e4713d23ba0b30eb8646424d01165c11b/libs/evals) | 4/5/4/5/4/5/5 | **32/35** | Isolated trials, independent outcome verification, complete trajectories, hard correctness versus soft expectations, and repeated Agent trials | Agent benchmark infrastructure does not prove transactional application durability | Eval case shape, scorer independence, reports, held-out cases, Wilson intervals, and harness validation |

DBOS, Prefect, and Harbor plus Deep Agents tie numerically. DBOS ranks first
because its PostgreSQL durability and commit-failure problem is closest to the
OpenMagic kernel. Zero to Production ranks second despite its lower numeric
score because the user explicitly prefers its integration-test style and its
`TestApp` pattern is the clearest foundation for the new suite.

## Repository architecture extracts

### Zero to Production

The repository's `tests/api/helpers.rs` provides one `TestApp` harness. For each
test it:

- selects a unique PostgreSQL database name;
- selects a random OS port;
- creates and migrates the database using production migrations;
- launches the real application;
- exposes an HTTP client and a database pool for outcome verification;
- launches a real HTTP server at the email-provider seam rather than replacing
  application functions;
- generates unique users and data so parallel tests do not collide.

The application is exercised through HTTP in files such as
`tests/api/subscriptions.rs`. Tests inspect PostgreSQL after the request when
the durable state is part of the outcome. One test deliberately damages the
database schema and asserts that the public request fails, which demonstrates
that integration tests may alter infrastructure to create a failure without
mocking application behavior.

Practices to emulate:

- one high-leverage `TestApplication` harness;
- a new migrated database per test;
- random ports and unique identities;
- public request interfaces for the act phase;
- database reads for outcome verification;
- provider substitution at the network protocol seam only.

Practices not to copy directly:

- spawning the server as a task inside the same process is insufficient for
  OpenMagic crash and restart claims;
- a provider test server is appropriate for deterministic integration tests,
  but live provider availability remains a separate smoke lane.

### DBOS Transact Python

Concrete paths:

- `chaos-tests/conftest.py` starts PostgreSQL and repeatedly restarts it at
  randomized intervals.
- `chaos-tests/test_workflows.py` executes thousands of workflows, events, and
  receives while database chaos is active.
- `tests/test_singleexec.py` checks one logical execution under concurrent
  callers and injects connection loss at named step-commit and workflow-commit
  points.
- `tests/test_failures.py` exercises retry, recovery, connection loss after a
  commit, and unique-constraint conflicts caused by ambiguous commit results.
- `tests/test_schema_migration.py` verifies schema evolution separately.

Practices to emulate:

- named failure points around transaction commit;
- database restart and connection-loss tests, not only exception-return tests;
- enough repeated work to make recovery and concurrency behavior observable;
- explicit separation between ordinary integration tests and heavier chaos
  suites;
- verification of committed state after an ambiguous client result.

Practices not to copy:

- OpenMagic does not replay arbitrary Python workflow code;
- private database mutation is not an application interface and should be used
  only by the external evidence verifier or infrastructure controller;
- randomized chaos complements, but does not replace, deterministic named
  crash windows.

### Prefect

Concrete paths:

- `integration-tests/test_task_worker.py` starts an actual API subprocess and
  runs a task worker against it.
- `integration-tests/test_worker.py` invokes isolated CLI subprocesses, waits
  for observable worker events with bounded deadlines, and verifies lifecycle
  order.
- `tests/server/database/test_migrations.py` owns migration-specific checks.
- `tests/workers/test_process_worker.py` keeps process-worker behavior distinct
  from server model tests.

Practices to emulate:

- separate integration tests for API, worker, migration, and process behavior;
- real subprocesses when process identity is part of the claim;
- bounded readiness polling instead of fixed sleeps;
- deterministic cleanup of every launched process;
- event correlation across process boundaries.

Practices not to copy:

- OpenMagic does not need Prefect's deployment and infrastructure matrix;
- in-process server fixtures cannot support OpenMagic's fresh-process proof
  lane even if they are useful for fast package tests.

### Harbor and Deep Agents

Concrete paths:

- Harbor `tests/integration/test_hello_user_e2e.py` runs Oracle and no-op Agents
  in isolated Docker environments and lets an independent verifier determine
  reward.
- Harbor `src/harbor/trial/trial.py` owns a complete trial lifecycle rather
  than allowing the Agent to decide success.
- Deep Agents `libs/evals/CONTRIBUTING.md` separates hard correctness assertions
  from soft efficiency expectations.
- Deep Agents `libs/evals/deepagents_harbor/stats.py` implements Wilson
  confidence intervals and a minimum detectable effect for binary outcomes.
- Deep Agents uses separate repeated-trial jobs rather than mixing model
  comparison and variance measurement into one report shape.

Practices to emulate:

- independently verify outcomes from durable state;
- keep correctness, quality, efficiency, and live availability as different
  verdicts;
- store complete sanitized trial configuration and traces;
- validate the harness itself with Oracle and known-failing controls;
- repeat nondeterministic Agent cases and report uncertainty.

Practices not to copy:

- a hosted trace store cannot be OpenMagic's authoritative evidence;
- Agent benchmark pass rates cannot override one deterministic invariant
  violation.

## Book, standards, and official guidance

### Zero to Production in Rust

The official sample and companion article make `spawn_app` the only helper that
depends on application internals. Test acts and assertions remain based on the
deployed interface and durable outcome. Later chapters evolve this into a
`TestApp` containing a public address and database pool. The test database must
be isolated because a repeated run against shared durable state exposes unique
constraint conflicts.

Local implication: OpenMagic should use one `TestDeployment` helper, but it
must launch multiple OS processes and expose role-specific clients rather than
returning application objects.

Sources:

- [Official Zero to Production repository](https://github.com/LukeMathWalker/zero-to-production)
- [Official sample, integration testing chapter](https://www.zero2prod.com/assets/sample_zero2prod.pdf)
- [Author's database and integration testing article](https://www.lpalmieri.com/posts/2020-08-31-zero-to-production-3-5-html-forms-databases-integration-tests/)

### PostgreSQL transaction and locking documentation

PostgreSQL defines visibility and conflict behavior at transaction and lock
level. `READ COMMITTED` is statement-scoped, `REPEATABLE READ` holds a stable
snapshot after the first statement, and `SERIALIZABLE` may reject a transaction
whose result cannot be explained by a serial order. Row locking through
`SELECT FOR UPDATE` conflicts with concurrent mutations and waits until the
current transaction ends.

Local implication: tests must use real independent database sessions and real
commits. Wrapping the whole test in a rollback transaction would hide the
visibility, response-loss, recovery, and process-restart behavior being proved.

Sources:

- [PostgreSQL transaction modes](https://www.postgresql.org/docs/current/sql-set-transaction.html)
- [PostgreSQL explicit locking](https://www.postgresql.org/docs/current/explicit-locking.html)

### pytest

pytest fixtures provide explicit, modular environments. Official guidance
prefers explicit fixture requests over distant implicit setup. pytest also
notes that explicit dependency injection is safer for controlled code than
global monkeypatching.

Local implication: infrastructure fixtures may launch PostgreSQL and processes,
but tests should not monkeypatch OpenMagic modules, repositories, clocks, or
provider clients. Strict marker registration should make unit, integration,
failure-proof, Agent eval, live, and playground suites explicit.

Sources:

- [pytest fixtures](https://docs.pytest.org/en/latest/explanation/fixtures.html)
- [pytest integration practices and documentation index](https://docs.pytest.org/en/stable/contents.html)
- [pytest monkeypatch guidance](https://docs.pytest.org/en/stable/how-to/monkeypatch.html)

### Testcontainers and SQLx test databases

Testcontainers recommends real containerized dependencies in place of
in-memory substitutes. SQLx supports a fresh database per test, automatic
production migrations, and composable test-only fixture data.

Local implication: use one pinned PostgreSQL container per test worker for
startup efficiency, then create one uniquely named database per test. Apply the
runtime and Application Package migrations to every database. Do not truncate a
shared database and do not let `.env` configuration override the harness.

Sources:

- [Testcontainers for Python with PostgreSQL](https://testcontainers.com/guides/getting-started-with-testcontainers-for-python/)
- [SQLx automatic test database management](https://docs.rs/sqlx/latest/sqlx/attr.test.html)

## Recommended local shape

```text
packages/openmagic-runtime/tests/
  contracts/                 pure validation and canonicalization
  integration/               real PostgreSQL through public Python interfaces

reference-apps/example-insurance/tests/
  integration/               real business commands and committed outcomes

apps/api/tests/
  contract/                  HTTP schema and error contract

evals/
  src/openmagic_evals/
    harness/
      deployment.py          TestDeployment process and database lifecycle
      postgres.py            unique database and migration orchestration
      processes.py           API, Workflow Worker, Delivery Worker launch/kill
      providers.py           local protocol services and opt-in live config
      faults.py              named barriers, crash points, and network faults
      verifier.py            read-only durable-state outcome verification
    failure_proofs/
    agent_quality/
    live_smokes/
    playground_checks/
  cases/
  tests/                     tests of the harness and known controls
```

### `TestDeployment` contract

For every system-level deterministic case:

1. Create a unique PostgreSQL database in a pinned container.
2. Apply the packaged OpenMagic Runtime migrations.
3. Apply the packaged Application Package migrations.
4. Launch the installed API, Workflow Worker, and Delivery Worker as separate
   OS processes with random ports and an explicit environment allowlist.
5. Wait for bounded readiness from public health interfaces.
6. Submit Commands through the real public application interface.
7. Inject failure by killing a process, restarting PostgreSQL, closing a real
   connection, using a named barrier, or controlling a protocol-level local
   provider.
8. Launch replacement processes from a clean interpreter.
9. Verify public receipts and read-only durable state.
10. Emit a versioned sanitized JSON trace and deterministic verdict.
11. Stop every process and drop the database on success. Retain a failed
    database only when explicitly requested for diagnosis.

### Mocking policy

Allowed:

- pure tests of deterministic functions with plain values;
- real local HTTP, SMTP, or provider-protocol processes with scripted
  responses;
- read-only verifier SQL after actions occur through public interfaces;
- harness-owned OS process control, database control, barriers, and network
  fault proxies;
- deterministic Executor adapters that satisfy the same installed public seam
  as Agent Executors.

Rejected:

- monkeypatching OpenMagic functions, methods, modules, repositories, or clocks;
- `MagicMock`, `AsyncMock`, or behavior-only fake databases in integration and
  proof suites;
- SQLite or in-memory persistence as a substitute for PostgreSQL;
- calling protocol implementation classes directly when the claim is about an
  application or worker boundary;
- wrapping an end-to-end test in a rollback transaction;
- treating `asyncio.gather` within one process as a process race proof;
- manually rewriting durable rows to simulate a public transition;
- allowing `.env`, developer services, or ambient credentials to affect a
  deterministic test;
- allowing a live provider, model, or hosted trace service to determine the
  strict architectural verdict.

### Suite layers

| Layer | Act through | Infrastructure | Verdict |
| --- | --- | --- | --- |
| Pure contract | Public deterministic function or value type | None | Package correctness |
| Package integration | Public package interface | Fresh migrated PostgreSQL database | Module contract |
| Application integration | HTTP or installed application command interface | API plus PostgreSQL | Business transaction outcome |
| Failure proof | HTTP and worker interfaces from separate processes | PostgreSQL, API, both Worker pools, local providers, fault controller | Strict release gate |
| Agent quality | Same installed Executor and application seams | Versioned cases and repeated trials | Quality distribution only |
| Live smoke | Same public integration seam | Explicit pinned live provider or model | Availability evidence only |
| Playground check | Playground public interface | Synthetic deployment with effects disabled | Demonstration safety and reproducibility |

## Options

| Option | Points | When to choose | Risks | First slice |
| --- | ---: | --- | --- | --- |
| A. Clean-slate integration-first suite | **10/10** | Correctness and architecture evidence matter more than preserving old tests | More infrastructure code, slower than isolated unit tests | Build `TestDeployment`, one fresh database, and one Command atomicity case |
| B. Port selected legacy tests | 4/10 | The implementation and public interfaces remain mostly unchanged | Preserves old vocabulary, mocks, facade coupling, and overclaimed restart evidence | Copy PostgreSQL tests into new packages |
| C. Mostly unit tests plus a few E2E smokes | 3/10 | Fast feedback is the dominant goal and durability claims are weak | Cannot prove transaction, race, crash, or restart behavior | Mock repositories and run one happy-path deployment |

## Final recommendation

Choose Option A. Delete the legacy test and eval suite as part of the rewrite
and derive the new catalog only from accepted contracts, invariant matrices,
Application Package behavior, and claim boundaries.

The first test slice should establish the harness itself before kernel breadth:

1. build and install the runtime, Example Insurance, and API distributions;
2. create and migrate one unique PostgreSQL database;
3. start one API process and one Workflow Worker process;
4. submit one typed start Command;
5. inject failure before commit and after commit response loss;
6. restart from a fresh process;
7. verify one value-identical receipt and no partial state;
8. run a known-bad control to prove that the verifier detects the invariant
   violation.

Defer browser automation, provider breadth, cross-database support, hosted
trace integration, and production load claims. The recommendation would change
only if OpenMagic abandons PostgreSQL atomicity, collapses workers back into the
API process, or stops claiming fresh-process recovery.

## Sources

Web sources accessed 2026-07-14:

- [Zero to Production repository](https://github.com/LukeMathWalker/zero-to-production)
- [Zero to Production sample](https://www.zero2prod.com/assets/sample_zero2prod.pdf)
- [Luca Palmieri, database and integration tests](https://www.lpalmieri.com/posts/2020-08-31-zero-to-production-3-5-html-forms-databases-integration-tests/)
- [DBOS Transact Python](https://github.com/dbos-inc/dbos-transact-py)
- [PostgreSQL transaction modes](https://www.postgresql.org/docs/current/sql-set-transaction.html)
- [PostgreSQL explicit locking](https://www.postgresql.org/docs/current/explicit-locking.html)
- [pytest documentation](https://docs.pytest.org/en/stable/)
- [pytest fixtures](https://docs.pytest.org/en/latest/explanation/fixtures.html)
- [pytest monkeypatch guidance](https://docs.pytest.org/en/stable/how-to/monkeypatch.html)
- [Testcontainers for Python and PostgreSQL](https://testcontainers.com/guides/getting-started-with-testcontainers-for-python/)
- [SQLx automatic test database management](https://docs.rs/sqlx/latest/sqlx/attr.test.html)

Pinned local repository sources:

- `.reference/zero-to-production` at `970987c5f793af6fc8e557731c9bbb23b620451e`
- `.reference/dbos-transact-py` at `e9e351574b9a01aad62ead31bddb629fe1eb3e9c`
- `.reference/prefect` at `6c39a5eb0c0e0d8e40c535827e4e25d067bd86c3`
- `.reference/harbor` at `a19e01b835769fef0002476ac87cd5d633a9ccca`
- `.reference/deepagents` at `2266f58e4713d23ba0b30eb8646424d01165c11b`
