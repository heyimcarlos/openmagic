# Executor integration testing practices

Date: 2026-07-12

Source snapshot: Executor commit `0a50c796c2cc334cf3e9bf6d4be33c77dbfac93b`

## Bottom line

Executor's main integration-testing pattern is: run the real product stack and
real protocol clients, but replace credentialed upstream SaaS systems with
stateful, wire-level emulators. Those emulators let the product's real SDKs,
OAuth flows, crypto, sandbox, MCP transport, and browser UI run unchanged. They
also expose ledgers and fault controls, which make upstream effects observable
and failure cases deterministic. This is materially stronger than mocking
product modules, but it is not the same as proving every workflow against a
real third-party account.

Executor reserves live services for narrow checks that need no private user
credential, such as invoking the public npm downloads API or verifying that a
PostHog OAuth flow reaches the expected authorization page. It separately tests
real third-party client behavior with the actual OpenCode binary. Browser
recordings, traces, screenshots, copied test source, result manifests, emulator
ledgers, and exported telemetry make test runs inspectable, while Vitest
assertions remain the pass or fail oracle.

## Evidence

### Real application, emulated provider boundary

The E2E contract is explicitly black-box. A scenario drives only the typed API,
web UI, MCP, or CLI, and may not import application internals, inspect the
database, or alter product code or stubs. The same user journey is run across
the deployment targets that provide its required capabilities
([`e2e/AGENTS.md:3-13`](../../.reference/executor/e2e/AGENTS.md#L3),
[`e2e/vitest.config.ts:3-17`](../../.reference/executor/e2e/vitest.config.ts#L3)).

For provider dependencies, the repository's declared rule is to use
`@executor-js/emulate`, not an ad hoc stub. These emulators are stateful,
wire-level services. They mint realistic credentials, serve protocol metadata
and OpenAPI descriptions, allow real SDKs to run unmodified, and record every
request in a ledger
([`AGENTS.md:27-37`](../../.reference/executor/AGENTS.md#L27)). The cloud setup
boots the actual development stack and points the real WorkOS and Autumn SDKs at
local emulators. Real auth and billing code, sealed-session crypto, JWKS, the
sandbox, and application routes remain in the path
([`e2e/setup/cloud.boot.ts:1-5`](../../.reference/executor/e2e/setup/cloud.boot.ts#L1),
[`e2e/setup/cloud.boot.ts:51-108`](../../.reference/executor/e2e/setup/cloud.boot.ts#L51)).

This pattern reaches beyond local processes. Hosted emulator scenarios create a
new isolated provider instance for each run, then use its returned provider URL
and ledger
([`e2e/src/emulator-instance.ts:3-19`](../../.reference/executor/e2e/src/emulator-instance.ts#L3)).
For example, the Microsoft OAuth handoff scenario receives a newly minted
client credential, enters the secret only through the real browser form, checks
that the handoff URL omitted it, completes the real product flow, and verifies
the exact token exchange in the emulator ledger
([`e2e/scenarios/oauth-client-handoff.test.ts:338-347`](../../.reference/executor/e2e/scenarios/oauth-client-handoff.test.ts#L338),
[`e2e/scenarios/oauth-client-handoff.test.ts:372-433`](../../.reference/executor/e2e/scenarios/oauth-client-handoff.test.ts#L372),
[`e2e/scenarios/oauth-client-handoff.test.ts:457-468`](../../.reference/executor/e2e/scenarios/oauth-client-handoff.test.ts#L457)).

Mocks still exist, but are narrow. The update-card browser helper intercepts one
version endpoint to make a presentation state deterministic, while comments
point to separate reachability scenarios for the real route
([`e2e/src/update-card-render.ts:1-7`](../../.reference/executor/e2e/src/update-card-render.ts#L1),
[`e2e/src/update-card-render.ts:25-54`](../../.reference/executor/e2e/src/update-card-render.ts#L25)).
This keeps a UI-state test focused without pretending that its intercepted
request proves the integration endpoint.

### Selective live integrations and credentials

Executor does include live-network checks when credentials are unnecessary:

- A cross-target scenario registers the public npm downloads API through MCP,
  creates a no-auth connection, calls the operation over the public internet,
  and asserts on the returned download count
  ([`e2e/scenarios/no-auth-connection.test.ts:1-20`](../../.reference/executor/e2e/scenarios/no-auth-connection.test.ts#L1),
  [`e2e/scenarios/no-auth-connection.test.ts:143-183`](../../.reference/executor/e2e/scenarios/no-auth-connection.test.ts#L143)).
- A self-host browser scenario discovers PostHog's live MCP OAuth metadata,
  performs dynamic client registration, and verifies the authorization URL. It
  stops before user login, so no PostHog account credential is needed
  ([`e2e/selfhost/posthog-mcp-oauth.test.ts:1-5`](../../.reference/executor/e2e/selfhost/posthog-mcp-oauth.test.ts#L1),
  [`e2e/selfhost/posthog-mcp-oauth.test.ts:34-78`](../../.reference/executor/e2e/selfhost/posthog-mcp-oauth.test.ts#L34)).
- The deployed emulate MCP scenario fetches live remote metadata over the
  network, but uses documented test credentials owned by the emulator, not a
  real provider account
  ([`e2e/selfhost/mcp-oauth-scope-discovery-emulate.test.ts:1-11`](../../.reference/executor/e2e/selfhost/mcp-oauth-scope-discovery-emulate.test.ts#L1),
  [`e2e/selfhost/mcp-oauth-scope-discovery-emulate.test.ts:75-124`](../../.reference/executor/e2e/selfhost/mcp-oauth-scope-discovery-emulate.test.ts#L75)).

The checked CI E2E job supplies timeout configuration, but no WorkOS, Autumn,
Google, Microsoft, PostHog, or LLM account credential. Cloud and self-host
scenarios therefore run on pull requests without product integration secrets
([`.github/workflows/ci.yml:183-252`](../../.reference/executor/.github/workflows/ci.yml#L183)).
Some non-default infrastructure targets do require external machine access. For
example, Windows CLI testing needs EC2 credentials, and desktop VM targets need
guest access, but these targets are explicitly outside the default E2E chain
([`e2e/vitest.config.ts:80-128`](../../.reference/executor/e2e/vitest.config.ts#L80)).

The suite also distinguishes a real client from real inference. Client protocol
behavior can use the actual OpenCode binary with a scripted OpenAI-wire server,
while real-inference evaluation is treated as a separate statistical axis, not
a deterministic E2E assertion
([`e2e/AGENTS.md:253-271`](../../.reference/executor/e2e/AGENTS.md#L253)).

### Browser testing and run evidence

The browser surface launches Chromium against the target's served UI, injects a
real authenticated session cookie, and starts Playwright tracing with DOM
snapshots, screenshots, and sources. Every user-labelled step creates a trace
group and screenshot. A failure captures the final screen. Cleanup uses
acquire/use/release semantics so interruption still closes Chromium and flushes
the trace and video
([`e2e/src/surfaces/browser.ts:39-80`](../../.reference/executor/e2e/src/surfaces/browser.ts#L39),
[`e2e/src/surfaces/browser.ts:145-205`](../../.reference/executor/e2e/src/surfaces/browser.ts#L145)).

The browser also harvests W3C trace IDs and request status and duration from the
wire, linking a user action to server and database traces
([`e2e/src/surfaces/browser.ts:99-135`](../../.reference/executor/e2e/src/surfaces/browser.ts#L99)).
Artifacts are evidence and debugging aids, not the test oracle. The scenario
runner records the Vitest outcome in `result.json`, copies the relevant test
source into the run, lists artifacts, and preserves `skipped.json` when a target
lacks a required capability
([`e2e/src/scenario.ts:138-191`](../../.reference/executor/e2e/src/scenario.ts#L138)).

### Isolation and deterministic faults

Isolation is layered:

- Every cloud identity is created through the real login flow as a fresh user
  and organization, and the cloud database is wiped on boot
  ([`e2e/targets/cloud.ts:1-6`](../../.reference/executor/e2e/targets/cloud.ts#L1),
  [`e2e/targets/cloud.ts:74-102`](../../.reference/executor/e2e/targets/cloud.ts#L74),
  [`e2e/setup/cloud.boot.ts:51-57`](../../.reference/executor/e2e/setup/cloud.boot.ts#L51)).
- Self-host currently shares one bootstrap administrator. Its files run
  serially, resources are prefixed, global-count assertions are avoided, and
  scenario-created resources use finalizers
  ([`e2e/vitest.config.ts:22-28`](../../.reference/executor/e2e/vitest.config.ts#L22),
  [`e2e/AGENTS.md:233-251`](../../.reference/executor/e2e/AGENTS.md#L233)).
- The local target gives each scenario its own process, throwaway data
  directory, and OS-assigned port
  ([`e2e/vitest.config.ts:100-114`](../../.reference/executor/e2e/vitest.config.ts#L100)).
- Per-checkout port blocks are atomically locked, bind-probed, and retried so a
  suite cannot silently attach to another checkout's server
  ([`e2e/src/ports.ts:1-14`](../../.reference/executor/e2e/src/ports.ts#L1),
  [`e2e/src/ports.ts:146-217`](../../.reference/executor/e2e/src/ports.ts#L146)).
- Each scenario deletes its prior run directory before execution, and emulator
  resources or product records are released through `Effect` finalizers
  ([`e2e/src/scenario.ts:138-145`](../../.reference/executor/e2e/src/scenario.ts#L138),
  [`e2e/AGENTS.md:65-67`](../../.reference/executor/e2e/AGENTS.md#L65)).

Faults are injected at the upstream protocol boundary. Autumn faults match an
operation, method, or path; return a chosen status and body; run a bounded number
of times; and can delay a response beyond the application's timeout. Tests clear
faults in finalizers and read the ledger afterward
([`e2e/src/surfaces/autumn.ts:29-53`](../../.reference/executor/e2e/src/surfaces/autumn.ts#L29),
[`e2e/src/surfaces/autumn.ts:158-209`](../../.reference/executor/e2e/src/surfaces/autumn.ts#L158)).
The billing suite uses this to inject both a one-shot `500` and a three-second
delay, then asserts the user-visible result and proves that the faulted request
was actually attempted
([`e2e/cloud/mcp-execution-limits.test.ts:88-155`](../../.reference/executor/e2e/cloud/mcp-execution-limits.test.ts#L88)).
WorkOS tests similarly distinguish a transient `503`, which must preserve a
session, from a definitive `401`, which must condemn it
([`e2e/cloud/mcp-workos-blip-session-survival.test.ts:108-134`](../../.reference/executor/e2e/cloud/mcp-workos-blip-session-survival.test.ts#L108),
[`e2e/cloud/mcp-workos-blip-session-survival.test.ts:149-250`](../../.reference/executor/e2e/cloud/mcp-workos-blip-session-survival.test.ts#L149)).

### CI separation and validation of outputs

Unit and package tests are excluded from the root E2E command and run as their
own CI job. E2E has a separate matrix: four cloud shards, each booting a fresh
stack, plus self-host. Playwright is pinned and installed explicitly. Failed
runs upload traces, recordings, screenshots, result files, and server logs
([`package.json:38-44`](../../.reference/executor/package.json#L38),
[`ci.yml:139-181`](../../.reference/executor/.github/workflows/ci.yml#L139),
[`ci.yml:183-262`](../../.reference/executor/.github/workflows/ci.yml#L183)).
The local stack is separated further: on main it runs only a focused stdio MCP
regression because the broader browser suite is acknowledged as flaky
([`.github/workflows/ci.yml:264-320`](../../.reference/executor/.github/workflows/ci.yml#L264)).
Production artifacts get distinct gates. The self-host publish workflow runs
the scenarios against the published image digest, and the desktop smoke job
runs the compiled sidecar outside its build workspace before checking that a
provider call reaches credential validation
([`.github/workflows/publish-selfhost-docker.yml:244-274`](../../.reference/executor/.github/workflows/publish-selfhost-docker.yml#L244),
[`ci.yml:361-393`](../../.reference/executor/.github/workflows/ci.yml#L361)).

Executor validates outputs at two levels:

1. Scenarios assert on user-meaningful API, terminal, or browser results with
   intent-bearing assertion messages. For example, the NDJSON scenario compares
   live parsed rows with both advertised type surfaces, catching a schema that
   lies about the runtime value
   ([`e2e/scenarios/openapi-ndjson-output.test.ts:193-239`](../../.reference/executor/e2e/scenarios/openapi-ndjson-output.test.ts#L193)).
2. Tests independently validate downstream evidence. Emulator ledgers prove
   which upstream operation ran, usage ledgers prove asynchronous metering, and
   a suite-owned OTLP store proves spans actually left the server. Telemetry
   assertions poll the exported store, not an in-memory span object
   ([`e2e/src/surfaces/telemetry.ts:1-7`](../../.reference/executor/e2e/src/surfaces/telemetry.ts#L1),
   [`e2e/cloud/telemetry-contract.test.ts:134-178`](../../.reference/executor/e2e/cloud/telemetry-contract.test.ts#L134)).

## Implications for OpenMagic V0

1. **Adopt the boundary, not necessarily the framework.** Run OpenMagic's real
   FastAPI service, worker/runtime, PostgreSQL path, and Next.js UI. Substitute
   only external providers with stateful protocol emulators or controllable test
   servers. Avoid mocking internal service calls in the core E2E suite.
2. **Require dual evidence for external effects.** Assert the OpenMagic-visible
   Job or Run result, then assert independently that the provider boundary saw
   the expected request, idempotency key, correlation ID, or dispatch count.
   A UI success state alone should not prove an email or other external effect.
3. **Make fault injection a first-class provider-test contract.** Support
   operation-matched status errors, delays, connection loss, bounded hit counts,
   and an inspectable request ledger. Clear faults in unconditional finalizers.
   This directly supports V0's uncertain-outcome and one-dispatch guarantees.
4. **Separate deterministic PR gates from credentialed smoke tests.** PR CI
   should use hermetic emulators. Any real Gmail or other account test should be
   a small, separately scheduled credentialed smoke suite with isolated test
   tenants and explicit secret scoping. Executor does not provide evidence that
   full credentialed provider happy paths belong in every PR.
5. **Isolate by durable business identity.** Give each test its own tenant,
   Workflow, and provider-emulator instance. Where a shared tenant is
   unavoidable, use unique prefixes, serial execution, no global-count
   assertions, and cleanup finalizers.
6. **Treat artifacts as review evidence, not correctness.** Record Playwright
   traces, video, screenshots, server logs, distributed trace IDs, the exact
   scenario source, and a machine-readable result manifest. Keep the actual
   acceptance predicate in readable assertions over user and provider outcomes.
7. **Gate packaged topology separately.** Add a focused production-container
   smoke or E2E gate in addition to dev-server scenarios. OpenMagic V0 should
   prove that its built server and web artifacts boot and execute one complete
   workflow outside the source workspace.

## Limitation to preserve

The suite provides strong evidence for product wiring and protocol behavior,
but most provider happy paths terminate at an emulator. Its live PostHog test
does not complete account authorization, and the real npm test is no-auth.
OpenMagic should describe emulator-backed coverage honestly as end-to-end
through its own system and protocol boundary, not as proof of a real provider's
current production behavior.
