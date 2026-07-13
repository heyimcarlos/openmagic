# Step-up verification and resumption comparables

Accessed 2026-07-12.

## Decision frame

- Target: let an SMS interaction associated with a Party continue freely until
  a workflow-scoped sensitive operation requires proof of control of a seeded
  on-file email address.
- Stack: FastAPI, Pydantic, PostgreSQL, SQLAlchemy, Alembic, Next.js, Composio
  Gmail, and the OpenMagic Workflow Control Plane.
- Scale: a synthetic V0 demonstration, not production identity proofing or a
  general account system.
- Hard constraints: the model does not decide authorization, secrets never
  enter model context, challenge state survives restart, successful proof
  resumes the exact waiting interaction, and outbound email remains a typed
  durable External Effect.
- Key question: whether the model should call a general verification tool, or
  whether deterministic protected operations should suspend and resume around
  a verification flow.

## Ranked repository comparables

| Rank | Source | Score | Best match | Important mismatch | Use for |
| --- | --- | ---: | --- | --- | --- |
| 1 | [Ory Kratos](https://github.com/ory/kratos) | 31/35 | Explicit flows, HMAC codes, expiry, attempt limits, atomic consumption, courier delivery | Go identity platform at much larger scope | Challenge state machine and persistence invariants |
| 2 | [Supabase Auth](https://github.com/supabase/auth) | 30/35 | PostgreSQL-backed one-time tokens, email OTP and magic links, dedicated reauthentication path | General account and JWT server, not workflow-scoped | Token supersession, delivery throttling, and reauthentication separation |
| 3 | [django-allauth](https://github.com/pennersr/django-allauth) | 29/35 | Python flow that suspends a protected request, checks recent authentication, then resumes it | Django session framework and password/MFA focus | Protected-operation interface and continuation pattern |

Scoring criteria are domain fit, target stack fit, production maturity,
architecture clarity, infrastructure relevance, testing quality, and
documentation signal. Each criterion is scored from 0 to 5.

## Repository architecture extracts

### Ory Kratos

Relevant paths:

- `selfservice/flow/verification/handler.go` owns browser and native
  verification flows.
- `selfservice/strategy/code/persistence.go` defines small persistence
  interfaces for creating, consuming, and deleting flow-bound codes.
- `selfservice/strategy/code/code_login.go` stores an HMAC, issue time, expiry,
  use time, flow ID, and identity ID.
- `persistence/sql/persister_code.go` increments the submission count before
  checking a code, compares HMAC values in constant time, and atomically sets
  `used_at` with a `used_at IS NULL` guard.
- `courier/` isolates message rendering and delivery from verification flow
  decisions.
- `selfservice/strategy/code/strategy_login_test.go` covers unknown identities,
  changed identifiers, five-attempt exhaustion, expiry, and replay.

Practices to emulate:

- Model verification as a durable flow, not a flag on a conversation.
- Bind a code to the flow and identity, not only to an email address.
- Count failed submissions independently of successful transactional work.
- Consume once with a conditional database update.
- Keep delivery behind its own adapter.

Practices to avoid:

- Do not copy the full self-service identity platform, schema-driven UI, or
  multiple credential strategies into V0.

### Supabase Auth

Relevant paths:

- `internal/api/reauthenticate.go` keeps reauthentication separate from normal
  login and accepts only previously confirmed email or phone destinations.
- `internal/api/mail.go` generates a code, hashes it with its destination,
  applies send-frequency limits, and records the send time.
- `internal/models/one_time_token.go` gives tokens a typed purpose, clears the
  previous token for the same user and purpose, and stores only the token hash.
- `internal/mailer/templatemailer/templatemailer.go` has distinct templates for
  magic-link and reauthentication messages.
- `internal/api/verify_test.go` exercises token validation and replacement.

Practices to emulate:

- Distinguish email-address verification, login, recovery, and reauthentication
  purposes even if they share token machinery.
- Allow only one active token for one subject and purpose.
- Throttle delivery separately from code attempts.
- Keep email templates purpose-specific.

Practices to avoid:

- Do not issue a general bearer session or create an OpenMagic account after
  success. The result should be a narrow workflow and purpose assertion.
- Do not attach verification state directly to the Party row.

### django-allauth

Relevant paths:

- `allauth/account/decorators.py` uses `reauthentication_required` to protect an
  operation based on recent authentication.
- `allauth/account/internal/flows/reauthentication.py` records the pending
  state and callback, redirects through reauthentication, and resumes the
  suspended request afterward.
- `allauth/account/middleware.py` routes a reauthentication requirement into
  that flow.
- `allauth/headless/spec/doc/openapi.yaml` exposes a typed
  `reauthentication_required` response to headless clients.

Practices to emulate:

- Put the requirement at the protected operation, not in UI or model prompts.
- Return a typed `verification_required` outcome that clients can present.
- Persist a continuation and resume only after a recent proof exists.

Practices to avoid:

- Do not persist an importable callback name or replay an arbitrary serialized
  HTTP request. Persist a recognized continuation kind plus validated typed
  arguments.

## Product and standards guidance

### General Magic

[General Magic](https://generalmagic.inc/) describes Ava as its AI customer
broker and positions the product as omnichannel customer servicing over text,
email, and voice, with human handoff for higher-stakes work. The public site
supports the intended experience but does not publish its verification
implementation. The screenshot is therefore product inspiration, not evidence
for a security design.

Local implication: preserve a single conversational experience while moving
security decisions into deterministic application code.

### OWASP Authentication and Session Management

The [Authentication Cheat
Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Authentication_Cheat_Sheet.html)
recommends reauthentication for sensitive features and context-aware step-up.
The [Forgot Password Cheat
Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Forgot_Password_Cheat_Sheet.html)
recommends uniform responses, cryptographically generated secrets, secure
storage, short expiry, and single use. The [Session Management Cheat
Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Session_Management_Cheat_Sheet.html)
requires server-side session meaning and session identifier rotation after a
privilege change.

Local implications:

- The SMS channel identifies the interaction but does not supply fresh proof.
- Challenge creation and confirmation must be deterministic and rate-limited.
- Success creates narrow server-side state and rotates or replaces any browser
  session identifier used by a web callback.
- User-visible failures must not disclose whether a Party or email exists.

### NIST SP 800-63B-4

[NIST SP 800-63B-4](https://pages.nist.gov/800-63-4/sp800-63b/authenticators/)
requires short out-of-band secrets to expire within ten minutes, be accepted
once, contain at least six decimal digits, and be rate-limited. It also states
that email is not an acceptable out-of-band authenticator. Email confirmation
and recovery codes are treated separately from authentication.

Local implication: call the V0 result proof of control of the seeded on-file
email, not strong authentication, MFA, identity proofing, or a NIST assurance
level. It may unlock synthetic workflow-local data for the demonstration only.

## Options

| Option | Points | When to choose | Risks | First slice |
| --- | ---: | --- | --- | --- |
| A. General model-visible `request_verification` tool | 4/10 | The agent legitimately chooses verification policy | The model can omit, duplicate, or spam challenges and becomes part of the authorization boundary | Tool creates one email code |
| B. Deterministic verification gate around protected tools | 9/10 | Sensitive operations have recognized purposes | Requires a small continuation model and protected-tool integration | Gate `read_workflow_packet` for one policyholder scenario |
| C. Full external identity provider | 5/10 | Production accounts and standards-based authentication are required | Scope, account model, deployment, and integration overwhelm the V0 tracer | Integrate hosted verification |

## Recommended local shape

Choose option B.

The model continues calling ordinary typed workflow tools. A protected tool
crosses one deep verification module with a small interface:

```text
require(context, workflow_id, purpose, continuation)
  -> allowed | verification_required

confirm(interaction_id, code)
  -> accepted | invalid | expired | exhausted
```

When proof is missing, `require` atomically creates or reuses a challenge,
persists the typed continuation, and creates one typed verification-email Job.
The Interaction Agent receives a typed `verification_required` result and can
present the deterministic user message. The model cannot choose the destination
or see the code.

```text
Inbound SMS
  -> resolve phone Party Identifier
  -> Interaction Agent calls protected workflow tool
  -> verification gate
       -> fresh assertion: continue
       -> otherwise: persist challenge + continuation
                      create verification-email Job
  -> Worker sends through Composio
  -> Party replies with code
  -> ingress confirms before invoking the model
  -> record narrow verification assertion
  -> create Notification for the waiting interaction
  -> fresh Interaction Agent reads a fresh Workflow Packet
  -> revalidate current role and authority
  -> answer the original request
```

Minimum durable records:

- Interaction: channel and channel identifier association used to route replies.
- Verification Challenge: interaction, Party, destination Party Identifier,
  Workflow, purpose, original Cause, typed continuation, expiry, attempt count,
  consumed time, and superseded time.
- Verification Assertion: interaction, Party, Workflow, purpose, source
  challenge, verification time, expiry, and revocation time.

Minimum invariants:

- One live challenge per interaction, Workflow, and purpose.
- A new challenge supersedes the old challenge atomically.
- Challenge expiry is ten minutes and failed entry is capped at five attempts.
- Confirmation binds to the originating interaction, Party, Workflow, purpose,
  and pending request.
- Consumption is one conditional update and is replay-safe under concurrency.
- Successful proof never globally verifies the phone identifier and never
  grants a Workflow Role.
- Resume loads current data and revalidates authority. It does not replay stale
  model output or a raw callback closure.
- Challenge delivery is one typed, system-authorized External Effect. It needs
  no human Approval Grant, but its dispatch and result use normal Job evidence.

For V0, prefer a six-digit code entered back into the originating SMS
interaction. It matches Ava's shown experience, Ory's default code strategy,
and the manual secret-transfer pattern. A magic-link adapter can later consume
the same challenge and continuation contract without changing protected tools.

## What to defer

- General accounts, passwords, KYC, MFA, assurance levels, passkeys, recovery,
  email changes, and production identity-proofing claims.
- Risk scoring, device fingerprinting, IP reputation, and generalized policy
  configuration.
- Multiple challenge delivery providers or a general authentication server.

## What would invalidate this recommendation

- Production use with real sensitive insurance data would require an explicit
  digital identity risk assessment and a stronger authenticator than email.
- If the first deployed channel is browser-only rather than SMS, a magic-link
  callback plus a secure rotated browser session may be simpler than code entry.
- If an existing identity provider becomes authoritative for Parties, OpenMagic
  should consume its assertion and keep only workflow-scoped continuation state.

## Sources

- [General Magic](https://generalmagic.inc/), accessed 2026-07-12.
- [Ory Kratos repository](https://github.com/ory/kratos), latest default branch
  cloned 2026-07-12.
- [Supabase Auth repository](https://github.com/supabase/auth), latest default
  branch cloned 2026-07-12.
- [django-allauth repository](https://github.com/pennersr/django-allauth), latest
  default branch cloned 2026-07-12.
- [OWASP Authentication Cheat
  Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Authentication_Cheat_Sheet.html),
  accessed 2026-07-12.
- [OWASP Forgot Password Cheat
  Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Forgot_Password_Cheat_Sheet.html),
  accessed 2026-07-12.
- [OWASP Session Management Cheat
  Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Session_Management_Cheat_Sheet.html),
  accessed 2026-07-12.
- [NIST SP 800-63B-4 authenticator
  requirements](https://pages.nist.gov/800-63-4/sp800-63b/authenticators/),
  accessed 2026-07-12.
