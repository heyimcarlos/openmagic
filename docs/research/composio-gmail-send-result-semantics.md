# Composio Gmail send result semantics

Date: 2026-07-11
Rechecked: 2026-07-12

## Bottom line

`GMAIL_SEND_EMAIL` returning `successful: true` is useful positive evidence, but it is not absolute proof that the email was sent successfully or delivered.

The strongest claim supported by Composio's public contract is:

> OpenMagic received a completed Composio response in which Composio classified the action execution as successful.

Composio describes the flag only as whether the action execution was successful. Its public Gmail tool schema does not expose a provider acknowledgement field, a Gmail message ID, an `applied` state, a delivery state, or a retry-safety classification. The Gmail API contract is stronger when observed directly: `users.messages.send` returns a Gmail `Message` on success, and Gmail automatically applies the `SENT` label to messages sent through that method. Even then, Google's Gmail API error guide explicitly says that a 200 response cannot be assumed to mean that the email was successfully sent because mail-send quota enforcement can lag. Delivery to a recipient can also fail later. Sources: [Composio Gmail toolkit](https://docs.composio.dev/toolkits/gmail.md), [Gmail `users.messages.send`](https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.messages/send), [Gmail label semantics](https://developers.google.com/workspace/gmail/api/guides/labels), and [Gmail error handling, mail sending limits](https://developers.google.com/workspace/gmail/api/guides/handle-errors#resolve_a_429_error).

The result therefore narrows the success branch, but it does not remove the structural uncertainty around a timeout, lost response, process crash, or stale worker. It also does not establish final delivery.

A positively correlated `GMAIL_EMAIL_SENT_TRIGGER` event can strengthen the evidence to "Gmail later exposed this message in the authenticated user's `SENT` mailbox." That still is not delivery proof, and the absence of an event is not proof that the send was not applied.

## Tool identity and published schema

The current official toolkit page, accessed on 2026-07-11, identifies:

- Toolkit: `GMAIL`
- Toolkit version: `20260703_00`
- Tool name: `Send Email`
- Tool slug: `GMAIL_SEND_EMAIL`
- Behavior: immediate and irreversible send through the Gmail API, with no scheduled-send support

The input fields published for this tool are:

| Field | Type | Required | Relevant contract |
| --- | --- | --- | --- |
| `recipient_email` | string | no | Primary recipient, also accepts `to` as an alias |
| `extra_recipients` | array | no | Additional `To` recipients |
| `cc` | array | no | CC recipients |
| `bcc` | array | no | BCC recipients |
| `subject` | string | no | Either subject or body must be present |
| `body` | string | no | Either subject or body must be present |
| `is_html` | boolean | no | Must be true for an HTML body |
| `user_id` | string | no | Gmail user, with `me` denoting the authenticated user |
| `from_email` | string | no | Verified Gmail send-as alias |
| `attachment` | string | no | One file or a list, subject to Gmail size limits |

At least one of `recipient_email` or its `to` alias, `cc`, or `bcc` must be present. The page recommends `GMAIL_REPLY_TO_THREAD` rather than this action for a reply. Source: [current Composio Gmail tool definition](https://docs.composio.dev/toolkits/gmail.md). The cloned SDK's older CLI recording confirms the same name and slug and shows that tool definitions are versioned (`.reference/composio/ts/packages/cli/recordings/ascii/tools/info-gmail-send-email.ascii:197-220`).

The tool-specific page publishes only this output:

| Field | Published type | Required | Published meaning |
| --- | --- | --- | --- |
| `data` | string | yes | Data from the action execution |
| `error` | string | no | Error, if any, during action execution |
| `successful` | boolean | yes | Whether the action execution was successful |

There is no Gmail-specific result field in that public schema. In particular, it does not promise `message_id`, `thread_id`, an upstream HTTP status, `SENT`, or delivery status. Source: [current Composio Gmail tool definition](https://docs.composio.dev/toolkits/gmail.md).

There is a documentation type mismatch worth treating as an unresolved contract detail. The tool page labels `data` as a string, while the v3.1 execute API and TypeScript SDK type it as an object. The v3.1 API says its contents vary by tool (`.reference/composio/docs/public/openapi.json:2494-2542`), and the TypeScript SDK validates it as a record (`.reference/composio/ts/packages/core/src/types/tool.types.ts:347-357`). Neither layer publishes Gmail-specific data fields. Code should not assume a message ID exists without pinning a toolkit version and verifying its live schema.

## What `successful` means at each Composio layer

### Direct execute API

`POST /api/v3.1/tools/execute/{tool_slug}` is a synchronous request-response endpoint. Its 200 response is described as successfully executing an action and receiving a response. The body requires `data`, `error`, and `successful`; `error` is documented as null on success, and `successful` says only that tool execution succeeded (`.reference/composio/docs/public/openapi.json:2494-2542`). There is no asynchronous execution handle or later completion endpoint in this contract.

The API separately documents 400, 401, 403, 404, 408, 410, 413, 422, 429, 500, 501, 502, and 503 responses. Of particular relevance, 408 means tool execution exceeded its timeout, 502 means an error communicating with the provider API, and 503 means the provider API is unavailable (`.reference/composio/docs/public/openapi.json:2547-2678`). These statuses report request or execution failure, not whether an already admitted external request was applied.

An HTTP 200 can also carry an in-band, or soft, failure with `successful: false` and an error string rather than throwing. Composio's CLI regression tests exercise this exact response shape and require it to be displayed as an execution failure (`.reference/composio/ts/packages/cli/test/src/commands/tools/tools.execute.cmd.test.ts:2050-2115`). Code must inspect the envelope rather than equating "no exception" with success.

### Direct TypeScript SDK

The direct TypeScript SDK does not independently verify Gmail. It copies `data`, `error`, and `successful` from the backend response, changes `log_id` to `logId`, and preserves optional session information (`.reference/composio/ts/packages/core/src/models/Tools.ts:199-217`). The call is awaited as one HTTP request (`.reference/composio/ts/packages/core/src/models/Tools.ts:914-961`). Therefore, `successful` is the backend's classification, not a new SDK-level observation.

### Direct Python SDK

The Python SDK's direct response type is `{data, error, successful}` (`.reference/composio/python/composio/core/models/tools.py:83-87`). Its high-level execute path returns the backend result but drops `log_id` and `session_info` while serializing it (`.reference/composio/python/composio/core/models/tools.py:583-602`). It does not add a Gmail verification step.

### Session execution

Session execution has a different surface. The low-level TypeScript session response returns only `data`, `error`, and `logId` (`.reference/composio/ts/packages/core/src/utils/transformers/toolRouterResponseTransform.ts:151-159`). A provider-wrapped session path synthesizes `successful` as `!response.error` rather than receiving the direct execute flag (`.reference/composio/ts/packages/core/src/models/Tools.ts:1145-1156`). Python session execution similarly exposes `data`, `error`, and `log_id` (`.reference/composio/python/composio/core/models/tool_router_session.py:698-741`).

The practical consequence is that the meaning of a visible success boolean depends on the execution path. On direct execution it is copied from the Composio backend. On some session/provider paths it is derived from the absence of an error. Neither is documented as proof of Gmail delivery.

## Gmail's own success contract

Gmail documents `users.messages.send` as sending the specified message to the recipients in its `To`, `Cc`, and `Bcc` headers. If the method succeeds, its response body is a Gmail `Message`. Source: [Gmail `users.messages.send`](https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.messages/send).

A Gmail `Message` contains an immutable `id`, `threadId`, and `labelIds`, among other fields. Source: [Gmail Message resource](https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.messages). Gmail also documents that it automatically applies the `SENT` system label to messages sent using `messages.send` or `drafts.send`. Source: [Gmail label semantics](https://developers.google.com/workspace/gmail/api/guides/labels).

Those facts support this narrower interpretation of a directly observed Gmail success response:

> Gmail accepted and completed the API call sufficiently to return a mailbox message representing the send operation.

They do not support either of these stronger claims:

- Every recipient server accepted the message.
- The message was delivered to every recipient's inbox.

Google adds an even more direct caveat. Its current Gmail API error guide says the mail sending pipeline can lag when enforcing daily limits, so a 200 API response cannot be assumed to mean the email was successfully sent. Source: [Gmail error handling, lines on mail sending limits](https://developers.google.com/workspace/gmail/api/guides/handle-errors#resolve_a_429_error). A later bounce or nondelivery notice is also outside the synchronous send response.

## `GMAIL_EMAIL_SENT_TRIGGER` as reconciliation evidence

Composio also publishes an `Email Sent` trigger with slug `GMAIL_EMAIL_SENT_TRIGGER`. It is a polling trigger that watches the authenticated user's Gmail `SENT` label and emits metadata for messages it finds. Its optional configuration includes a polling interval, a Gmail search query, and `userId`. Its published payload fields are all optional:

| Payload field | Meaning |
| --- | --- |
| `message_id` | Gmail message ID |
| `thread_id` | Gmail thread ID |
| `sender` | Sender address |
| `to`, `cc`, `bcc` | Recipient headers |
| `recipients` | Combined comma-separated recipients |
| `subject` | Subject |
| `message_text` | Message body text |
| `message_timestamp` | ISO timestamp |
| `attachment_list` | Attachments |
| `payload` | Raw Gmail payload |

Sources: [current Composio Gmail toolkit](https://docs.composio.dev/toolkits/gmail.md) and the cloned generated toolkit catalog (`.reference/composio/docs/public/data/toolkits.json:334-344`). Composio's general trigger guide says Gmail triggers are polling-based and can have up to roughly 15 minutes of latency on Composio-managed auth (`.reference/composio/docs/content/docs/triggers.mdx:15-24`). The Gmail FAQ describes roughly one-minute polling by default (`.reference/composio/docs/content/toolkits/faq/gmail.md:27-29`); the trigger-specific interval is configurable.

A positively correlated sent-trigger event is valuable late provider evidence. It is stronger than the Composio action flag alone because it comes from a later read of Gmail's `SENT` mailbox state and can carry Gmail's own message ID. A reconciler can use `message_id` to fetch the Gmail message and confirm the current label state.

It still proves only mailbox-side sent state, not final recipient delivery. Google's quota warning still applies, and recipient servers can later reject or bounce a message.

The trigger also has important attribution limits:

- It fires for a Gmail message sent by the authenticated user, not specifically for a message sent by one OpenMagic Job. A manual Gmail send or another client can create a matching event.
- Its payload has no OpenMagic Workflow ID, Job ID, External Effect ID, Composio execution log ID, or request idempotency key.
- Recipient, subject, body, timestamp, and thread matches can be non-unique. The published schema does not promise that any individual payload field will be present.
- The strongest correlation would be an exact Gmail message ID returned by the original action, but the public action schema does not promise one. The next-best option is a stable, unique OpenMagic correlation marker deliberately placed in a searchable message field and used in the trigger query. A recipient, subject, and time-window match is weaker evidence and may need human review.

Absence of a trigger event is not evidence that the send was not applied. It can mean the next poll has not occurred, the trigger was not active for that account, a query excluded the message, webhook receipt is delayed, a payload field needed for matching was omitted, or no uniquely attributable match was possible. Composio documents webhook delivery and retries, but it does not publish a negative-completeness contract for this Gmail poller (`.reference/composio/docs/content/docs/triggers.mdx:7-24`). Therefore, an event can help conclude `applied`, while a missing event cannot by itself conclude `not_applied`.

## The missing implementation boundary

The cloned repository is explicitly the SDK monorepo. Its repository map contains TypeScript SDKs, Python SDKs, the CLI, provider adapters, and documentation, but not the hosted Composio backend or Gmail action implementation (`.reference/composio/README.md:24-33`, `.reference/composio/README.md:151-160`). Tool schemas are fetched from the hosted API (`.reference/composio/ts/packages/core/src/models/Tools.ts:650-690`; `.reference/composio/python/composio/core/models/tools.py:169-179`), and execution is sent to the hosted execute endpoint.

An end-to-end source trace therefore stops at the Composio backend boundary. The public source does not let us confirm:

- which Gmail endpoint and client implementation the current `GMAIL_SEND_EMAIL` version invokes;
- the exact condition used by the backend to set `successful`;
- whether the backend checks the returned Gmail `Message` or `SENT` label;
- whether any backend layer can return success before the Gmail request completes;
- whether tool-specific data contains a Gmail message ID in practice.

The toolkit description makes it reasonable to infer that the action invokes Gmail's send API. It is not reasonable to upgrade that inference into an exactly defined, source-proven success predicate.

## Timeouts, retries, and the uncertain branch

Composio's current high-level Python and TypeScript SDKs intentionally disable automatic retries for direct tool execution because the calls are non-idempotent. Both implementations state that a read timeout can occur after an email has already been sent, and a silent retry can duplicate it (`.reference/composio/python/composio/core/models/tools.py:583-602`; `.reference/composio/ts/packages/core/src/models/Tools.ts:154-171`, `.reference/composio/ts/packages/core/src/models/Tools.ts:947-951`). The Python changelog records this as an SDK 0.16.0 fix and calls it an interim measure pending backend idempotency keys (`.reference/composio/python/CHANGELOG.md:31-42`).

Composio's still-open idempotency issue states the structural problem explicitly: after a read timeout the request may already be in flight, and the client cannot tell whether the backend performed the action. It also says the backend idempotency header and deduplication semantics still require platform support and confirmation. Source: [Composio issue 3654](https://github.com/ComposioHQ/composio/issues/3654).

This yields three distinct result classes:

| Observation | Defensible conclusion | Retry safety |
| --- | --- | --- |
| Complete response with `successful: true` and no error | Composio classified execution as successful; likely Gmail API acknowledgement if the action faithfully wraps `messages.send` | Do not retry |
| Definitive pre-application rejection, such as invalid arguments or authentication before provider dispatch | Known failure | Retry only after correcting the cause and under a new authorised attempt |
| Timeout, connection loss, worker loss, 5xx, provider communication error, malformed response, or result lost before commit | Outcome is uncertain | Do not blindly retry |

`successful: false` is not by itself a sufficient `not_applied` proof. The caller also needs to know what error produced it and whether that error is evidence that Gmail never accepted a request. The schema has no field that makes this distinction.

## Current OpenMagic integration findings

OpenMagic is currently wired to direct Python execution, but it bypasses Composio's high-level `composio.tools.execute()` method:

1. `Composio.client` returns the raw `HttpClient` (`.reference/composio/python/composio/sdk.py:239-241`).
2. OpenMagic calls `client.client.tools.execute(...)` (`server/services/gmail/client.py:481-488`).
3. The retry-disabled safeguard exists only on the high-level path, which calls `self._client.without_retries.tools.execute(...)` (`.reference/composio/python/composio/core/models/tools.py:583-602`).

Against the current cloned SDK, OpenMagic's raw call therefore bypasses that safeguard and retains the raw client's configured default retry behavior. Composio's open issue documents the raw generated client's default of two retries and the duplicate-email consequence ([Composio issue 3654](https://github.com/ComposioHQ/composio/issues/3654)). OpenMagic's dependency is also only constrained as `composio>=0.5.0`, with no upper bound or lock (`server/requirements.txt:7`), and its execute call does not pin a Gmail toolkit version (`server/services/gmail/client.py:483-487`).

The response handling also does not enforce the published envelope:

- `_normalize_tool_response` converts a model or mapping to a dictionary but does not validate `successful`, `error`, or tool-specific `data` (`server/services/gmail/client.py:438-463`).
- `_execute` records `"succeeded"` whenever no exception was raised, even if a returned envelope could contain `successful: false` and a non-null error (`server/agents/execution_agent/tools/gmail.py:324-343`).
- The actual OpenMagic send path currently calls `GMAIL_SEND_DRAFT`, not `GMAIL_SEND_EMAIL` (`server/agents/execution_agent/tools/gmail.py:375-383`). The official toolkit page gives `GMAIL_SEND_DRAFT` the same generic `{data, error, successful}` output shape, so the semantic limitation still applies.

These are implementation observations, not changes made by this research.

## Lifecycle implication for OpenMagic

The existing `external_effect_dispatch_started` boundary remains necessary.

A complete positive response can be accepted as evidence that the provider adapter acknowledged the operation. If OpenMagic defines its business effect as "submitted to Gmail" rather than "delivered to every recipient," it can use that evidence to settle the success branch. The canonical wording should still avoid claiming delivery or exactly-once execution.

All no-response paths remain uncertain. OpenMagic should preserve the one-dispatch rule and reconcile using any available evidence, such as Composio log ID, Gmail message ID, a stable correlation marker in the message, and a lookup in Gmail's sent mailbox. The current public tool schema does not guarantee a Gmail message ID, so that field must be verified against a pinned live tool version before it becomes a required contract.

The sent-email trigger is an appropriate positive reconciliation input when the event can be tied uniquely to the Job. It must not be used as a negative oracle: a missing event leaves the Job waiting or escalated unless some other evidence proves `not_applied`.

## V0 adapter seam fact check

The current sources support a small, fixed `gmail.send_email.v1` adapter. This is the seam-specific conclusion for the Wayfinder decision.

### Provider call

The adapter should own the translation from OpenMagic's immutable effect input to Composio's `GMAIL_SEND_EMAIL` arguments. The current published tool version is `20260703_00`, and manual execution should pin that version. The provider arguments are `recipient_email`, `extra_recipients`, `cc`, `bcc`, `subject`, `body`, `is_html`, `user_id`, `from_email`, and `attachment`. Composio requires at least one To, Cc, or Bcc recipient and at least one of subject or body. The call sends immediately and is irreversible. Source: [current Composio Gmail tool definition](https://docs.composio.dev/toolkits/gmail.md).

The outer execute request separately selects the Composio user or connected account and toolkit version. Those routing values are trusted adapter configuration, not caller-selected email content. The OpenMagic Job ID also remains internal correlation metadata. The published Gmail tool input has no Job correlation or idempotency field (`.reference/composio/docs/public/openapi.json:2086-2149`, `.reference/composio/docs/public/openapi.json:2464-2489`).

### Result normalization

The public direct-execute response is:

```text
data
error
successful
log_id, optional at the raw API layer
```

The API describes `successful` only as whether tool execution succeeded. It does not publish Gmail-specific receipt fields. The toolkit page labels `data` as a string, while the execute API and SDKs type it as an object, so the adapter may preserve validated raw data but must not require a Gmail message ID or thread ID (`.reference/composio/docs/public/openapi.json:2494-2542`, `.reference/composio/ts/packages/core/src/types/tool.types.ts:347-357`).

For V0, a complete response with `successful: true` and no error is sufficient evidence to return a succeeded Run Result. The normalized evidence should identify Composio, `GMAIL_SEND_EMAIL`, the pinned toolkit version, and the success flag. A raw `log_id` may be retained when available, but it is diagnostic evidence, not an idempotency key or proof of delivery. The canonical Job output may contain the adapter's normalized receipt without promising provider fields that Composio does not publish.

The adapter must inspect the envelope even when the HTTP call returns normally. Composio explicitly supports an in-band failure with `successful: false` and an error (`.reference/composio/ts/packages/cli/test/src/commands/tools/tools.execute.cmd.test.ts:2050-2121`). A deterministic pre-dispatch validation failure can return `failed`. After dispatch, `successful: false` may return `failed` only when the error proves the effect was not applied. Timeouts, connection loss, provider communication errors, malformed responses, or ambiguous errors return `uncertain`. None of those uncertain branches may automatically send again.

### Retry and correlation boundary

Current Python and TypeScript SDK direct-execute paths disable automatic HTTP retries because a read timeout can happen after an email was sent, and retrying can duplicate the side effect (`.reference/composio/python/composio/core/models/tools.py:583-601`, `.reference/composio/ts/packages/core/src/models/Tools.ts:154-172`, `.reference/composio/ts/packages/core/src/models/Tools.ts:947-954`). Composio's open idempotency issue confirms that the execute endpoint does not yet have a documented, backend-honored idempotency contract: [Composio issue 3654](https://github.com/ComposioHQ/composio/issues/3654).

The V0 adapter must therefore use a retry-disabled execution path. OpenMagic's committed `external_effect_dispatch_started` Event and one-dispatch-per-Job constraint provide the durable duplicate-send boundary. A timeout or lost response becomes an uncertain Run Result and a waiting Job, not a second provider call.

### Trigger boundary

`GMAIL_EMAIL_SENT_TRIGGER` exists, but it polls Gmail's `SENT` label and is asynchronous. Its payload can include a Gmail message ID, thread ID, addresses, subject, body text, and timestamp, but all published payload fields are optional. It carries no OpenMagic Job ID or Composio execution log ID. Source: [current Composio Gmail trigger definition](https://docs.composio.dev/toolkits/gmail.md). Gmail triggers are polling-based and can take up to roughly 15 minutes on Composio-managed authentication: [Composio trigger documentation](https://docs.composio.dev/docs/triggers).

The trigger is therefore outside the V0 success path. A later, uniquely correlated trigger can be positive reconciliation evidence. Waiting for it would add latency and ingestion machinery, and its absence cannot prove that the send did not happen.

### Deterministic fake cases

The deterministic Composio fake should exercise the same adapter contract with four branches:

1. `successful: true`, which produces a succeeded Run Result and normalized receipt.
2. A known validation or provider rejection proving no effect, which produces a failed Run Result.
3. A timeout, lost response, provider communication failure, or ambiguous soft failure after dispatch, which produces an uncertain Run Result.
4. A malformed or contradictory response, which fails closed as uncertain after dispatch.

Live Composio only needs to prove the normal successful branch for V0. The fake proves lifecycle behavior without sending duplicate emails.

## V0 product decision

For the first production-shaped demo, OpenMagic treats a complete Composio Gmail response with `successful: true` as sufficient to succeed both the Workflow Job Run and its Send Email Workflow Job. The user-facing flow does not wait for `GMAIL_EMAIL_SENT_TRIGGER`, and V0 does not add a general Composio trigger-ingestion subsystem for sent-email confirmation.

This is an intentional product boundary, not a stronger claim about Gmail delivery. OpenMagic may tell the user that the email was sent after the successful response, but the durable observation remains that Composio classified the execution as successful. A timeout, lost response, or other ambiguous outcome remains unsafe to retry automatically because the email may already have been sent.

Composio triggers can be reconsidered when a concrete tracer requires an inbound event, such as a customer reply, or when operational evidence shows that asynchronous sent-mail reconciliation is worth its additional ingestion, correlation, and lifecycle complexity. Their absence from V0 must not block the renewal send path.

## Source snapshot

The local Composio clone was on `next` at commit `a0f37a7f7728c922e044dfb35c33dad9aae7ae7c`, dated 2026-07-10. It was pulled and confirmed current on 2026-07-12. The official Gmail and Composio web documentation was rechecked on 2026-07-12. Product source was not modified.
