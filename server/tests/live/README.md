# Live Gmail acceptance

This opt-in smoke test proves the current provider happy path against one
authorized disposable inbox. It uses the same Workflow Control Plane, Worker,
Composio adapter, Approval Grant, dispatch Event, and Notification delivery
contracts as the deterministic suite.

The test is intentionally excluded from ordinary CI unless explicitly enabled:

```bash
set -a
source .env
set +a
OPENMAGIC_RUN_LIVE_EMAIL_SMOKE=1 \
  uv run pytest -q server/tests/live/test_composio_email_smoke.py
```

Required environment variables are documented in `.env.example`. The test
prints no credentials, connection identifiers, provider responses, message
content, or mailbox contents. Its unique subject is generated in memory, and
recipient observation is reduced to a boolean assertion.

The deterministic adapter suite remains authoritative for unsafe failure,
transport-loss, malformed-response, and no-retry branches. This live test proves
only that the currently configured Gmail provider path succeeds end to end.
