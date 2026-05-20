# Contributing

## Ground rules

- Start from the README and architecture docs.
- Keep runtime shells thin and push reusable logic into shared packages.
- Do not bypass SQL guardrails, auditability, or read-only assumptions.
- Document structural changes in `docs/adr/` before broad rewrites.
- Prefer changes that keep local Docker development and future Helm deployment aligned.
