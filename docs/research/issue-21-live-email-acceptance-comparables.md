# Live email acceptance comparables

Accessed 2026-07-12. Repository paths are pinned to the local reference commits
listed under Sources.

## Decision Frame

- Target project: OpenMagic V0
- Current and target stack: Python, pytest, PostgreSQL, Composio, and AgentMail
- Domain and scale: one controlled irreversible email effect in a local demo
- Hard constraints: one provider invocation, no automatic retry, independent
  recipient evidence, deterministic failure coverage, opt-in credentials, and
  no sensitive evidence in source or logs
- Key question: how should a narrow live-provider proof coexist with the normal
  deterministic suite?

## Ranked Comparables

| Rank | Source | Score | Best Match | Mismatch | Use For |
|---|---|---:|---|---|---|
| 1 | Executor E2E | 33/35 | One readable user journey, real surfaces, isolated identities, eventual evidence | TypeScript and a much larger multi-target harness | Scenario shape and evidence discipline |
| 2 | Composio SDK E2E | 29/35 | Credential-gated integration tests separated from unit tests | Primarily SDK compatibility, not durable workflow semantics | Opt-in provider configuration and narrow runtime proof |
| 3 | LangChain partner integration tests | 25/35 | Provider tests live beside deterministic coverage and gate on environment credentials | Model APIs are usually reversible and do not require dispatch fencing | pytest placement and credential gating |

Scores cover domain fit, Python fit, production maturity, architecture clarity,
operations relevance, testing quality, and maintainability signal.

## Repository Architecture Extracts

### Executor

- Relevant paths: `e2e/AGENTS.md`, `e2e/src/scenario.ts`,
  `e2e/src/trace-harvest.ts`, and `e2e/cloud/toolkit-opencode-real.test.ts`
- Observed shape: one user-meaningful journey is the review artifact. It uses
  unique scoped identities, public product surfaces, named outcome assertions,
  bounded eventual polling, and independently harvested evidence.
- OpenMagic choice: keep one readable live test that performs the whole approved
  send and reduces recipient observation to a boolean. Do not copy Executor's
  multi-target runner or recording infrastructure for this V0 ticket.

### Composio

- Relevant paths: `python/composio/integration_test/README.md`,
  `python/composio/integration_test/conftest.py`, and
  `ts/e2e-tests/_utils/src/runner.ts`
- Observed shape: provider integration tests are explicit commands requiring
  environment credentials. The larger E2E runner isolates temporary runtime
  resources, while ordinary package tests remain independent of live services.
- OpenMagic choice: make the Gmail proof opt-in and keep the default test suite
  deterministic. Validate that the configured Composio user has exactly one
  active Gmail connection before admitting dispatch.

### LangChain

- Relevant paths:
  `libs/partners/perplexity/tests/integration_tests/test_chat_models.py` and
  `libs/langchain_v1/tests/unit_tests/agents/test_responses_spec.py`
- Observed shape: external-provider tests use normal pytest tests with explicit
  credential-based skip conditions. Deterministic contract tests remain fast and
  runnable without provider access.
- OpenMagic choice: use a module-level opt-in skip so collection is safe in CI,
  while a missing required variable during an explicitly enabled run fails with
  only the missing variable names.

## Language and Standards Guidance

- Pytest's skip guidance explicitly supports conditional skips for unavailable
  external resources. OpenMagic uses this only for the live provider journey,
  never for deterministic acceptance coverage.
- HTTPX timeouts bound every AgentMail request. A separate monotonic deadline
  bounds eventual recipient polling without relying on global mailbox counts.
- Async resource ownership remains explicit: the AgentMail client and Workflow
  database are closed in `finally`, so failed assertions do not leak sessions.

## Recommended Shape

- Place the proof at `server/tests/live/test_composio_email_smoke.py` so its
  source reads as the acceptance specification.
- Use existing Control Plane, Worker, adapter, Approval Grant, dispatch Event,
  and Notification contracts. Add no parallel test-only workflow engine.
- Read secrets only from environment variables and wrap secret values in
  `SecretStr`.
- Generate the correlation subject and durable identities per run.
- Snapshot recipient message IDs before dispatch, then poll for one new message
  matching both the in-memory subject and configured sender.
- Assert exact event counts, one adapter invocation, successful Run and Job,
  completed Workflow, delivered confirmation Notification, normalized receipt,
  and independent recipient observation.
- Keep malformed response, transport loss, uncertainty, and no-retry branches in
  the deterministic adapter suite.

## Options

| Option | Points | When To Choose | Risks | First Slice |
|---|---:|---|---|---|
| Opt-in pytest journey | 10/10 | V0 with local credentials and an existing pytest topology | Live provider remains eventually consistent | One exact approved send |
| Standalone shell script | 6/10 | Manual demos without a test suite | Assertions and cleanup drift from CI conventions | Invoke current adapter |
| Run live Gmail in every CI build | 2/10 | Dedicated isolated provider tenant with strong quotas | Flakiness, duplicate effects, and secret exposure | Not appropriate for V0 |

## Final Recommendation

Use the opt-in pytest journey. It follows the repository's existing test
topology, reads as a product guarantee, and proves the provider happy path
without weakening or replacing deterministic failure acceptance. Defer recorded
browser artifacts and generalized live-provider orchestration until multiple
provider Kinds require the same operational machinery.

## Sources

- Executor, commit `0a50c796c2cc334cf3e9bf6d4be33c77dbfac93b`
- Composio, commit `a0f37a7f7728c922e044dfb35c33dad9aae7ae7c`
- LangChain, commit `a8fd0da2b7c3409db9a16d0c7bcd55463967351b`
- [Pytest conditional skipping](https://docs.pytest.org/en/stable/how-to/skipping.html), accessed 2026-07-12
- [HTTPX timeout guidance](https://www.python-httpx.org/advanced/timeouts/), accessed 2026-07-12
- [Python event loop time](https://docs.python.org/3/library/asyncio-eventloop.html#asyncio.loop.time), accessed 2026-07-12
