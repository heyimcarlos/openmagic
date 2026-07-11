# Domain Docs

How the engineering skills consume this repository's domain documentation when exploring the codebase.

## Before exploring, read these

- `CONTEXT.md` at the repository root.
- `docs/adr/` for decisions that affect the area being explored.

If either location does not exist, proceed silently. Do not suggest creating it preemptively. The `/domain-modeling` skill creates these files lazily when terminology or a durable architectural decision is actually resolved.

## File structure

OpenMagic uses a single-context layout:

```text
/
├── CONTEXT.md
├── docs/
│   └── adr/
│       ├── 0001-event-sourced-orders.md
│       └── 0002-postgres-for-write-model.md
├── server/
└── web/
```

`CONTEXT.md` is a domain glossary, not a technical specification. It should define canonical business concepts and explicitly distinguish terms that are easy to conflate.

`docs/adr/` contains architectural decisions that are hard to reverse, surprising without context, and the result of a real tradeoff.

## Use the glossary's vocabulary

When an issue title, implementation plan, hypothesis, or test names a domain concept, use the term defined in `CONTEXT.md`. Do not drift to synonyms the glossary explicitly avoids.

If a needed concept is absent, reconsider whether the term belongs to this domain. If it does, note the gap for `/domain-modeling`.

## Flag ADR conflicts

If proposed work contradicts an existing ADR, surface the conflict explicitly instead of silently overriding it:

> Contradicts ADR-0007 (event-sourced orders), but it may be worth reopening because...
