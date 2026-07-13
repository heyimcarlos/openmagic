# Issue 11 paired evaluation harness prototype

This throwaway prototype answers one question: what is the smallest evaluation
shape that compares the inherited direct-delegation baseline with the durable
Workflow path without confusing model quality, protocol safety, and live
provider availability?

Run it with:

```bash
uv run python prototypes/issue-11-eval-harness/tui.py
```

The proposed shape has three deliberately separate lanes:

1. **Paired journey:** run the same synthetic broker requests through the
   inherited baseline and the V0 Workflow path. Compare final task behavior,
   Workflow selection, duplicate input, context burden, and latency segments.
   V0 correctness is gated. The inherited baseline remains an observation, so
   demonstrating an inherited weakness does not make the harness itself fail.
   Authorization and the one-Workflow-Packet cap are correctness gates. The
   comparative context bytes, approximate tokens, and tool counts are
   diagnostics because smaller context is useful only while it stays sufficient.
2. **Protocol recovery:** exercise PostgreSQL, Workers, leases, approval,
   dispatch, and notification faults only against the durable path. These are
   deterministic correctness gates, not model benchmarks.
3. **Live provider:** preserve one narrow real Composio and AgentMail success
   journey. It proves wiring, not recovery safety.

This structure reuses the existing production boundaries and pytest fixtures.
It does not propose a generic evaluation framework, a second Workflow engine,
or live fault injection against Gmail.
