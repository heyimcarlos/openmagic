# Workflow telemetry presentation prototype

> PROTOTYPE: throwaway presentation code for deciding how Workflow progress and
> Agent activity should appear inside the existing OpenMagic chat.

## Question

What is the quietest useful disclosure structure for one chat turn that may
advance zero or more durable Workflows?

## Run

```bash
npm run prototype:workflow-telemetry --prefix web -- -p 3010
```

Open `http://127.0.0.1:3010/?variant=A`. Use the floating switcher or the left
and right arrow keys to compare:

- `A`: Quiet stack
- `B`: Request ledger
- `C`: Codex rail with hover-only arrows
- `D`: Codex rail with persistent arrows

Both Codex rail variants remove the horizontal separators between disclosure
rows. They differ only in whether the chevron appears on hover or remains
visible.

The development-only prototype URLs bypass browser Basic Auth. Production and
normal development routes retain the existing authentication behavior.

## Decision

Variant C, the hover-arrow Codex rail, is the selected direction. It keeps Agent
activity and each Workflow collapsed into one quiet line, supports independently
expanded Workflows, and offers a nested disclosure for deeper Workflow activity.
Its chevron sits immediately after the summary, appears only on hover or
keyboard focus, points right while closed and down while open, and adds no hover
background. Expanded activity is a flat list of single-line status rows, with no
vertical rail, indentation, descriptions, durations, or nested disclosures. All
telemetry uses muted gray styling, and the down chevron remains visible while a
disclosure is open. Parent and child telemetry icons share the same left edge as
the assistant message text.

Variant C uses the official shadcn Accordion abstraction backed by Base UI.
Approval remains a checkpoint between Jobs, not a fake Workflow Job.

The presentation prototype uses in-memory fixtures. Durable Cause correlation,
the user-facing Workflow projection, and sanitized activity receipts belong to
the later backend integration phase.

## Screenshots

![Collapsed Codex rail](./codex-rail-collapsed.png)

![Expanded Codex rail](./codex-rail-expanded.png)

![Mobile Codex rail](./codex-rail-mobile.png)
