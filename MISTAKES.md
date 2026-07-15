# Mistakes

- The backpressure visualizer wrote hundreds of disposable Workflows into the same database as
  the chat demo. Its Notifications then appeared as real chat work. Simulation and load tooling
  must use isolated storage, or perform an explicit durable reset before interactive QA.
- The updated frontend was tested against a backend process launched from an older worktree. The
  edited-approval payload then failed against a stale protocol. End-to-end QA must verify that all
  running services come from the exact revision under test.
- A Wayfinder map update escaped replacement newlines twice, leaving literal `\\n` text around a
  decision pointer. Tracker-body mutations must be read back after every update and checked for
  rendered structure, not only command success.
