# Mandatory guardrails

- Tenant isolation: a token/account belongs to exactly one tenant context.
- Provenance: writable entity IDs must come from live reads for that tenant.
- Hard caps: absolute amount and percentage-change caps live in backend configuration, not prompts.
- Units: semantic values are stored with currency/unit; only adapters translate to WB API fields.
- Staging: every write first becomes a staged immutable proposal.
- Preview: show BEFORE → AFTER and expected impact before approval.
- Approval: approval is separate from recommendation; the model cannot approve its own proposal.
- Revalidation: re-check live state and all guardrails immediately before apply.
- Idempotency: apply a staged change at most once.
- Cooldown: prevent repeated oscillating changes.
- Audit: log actor, source facts, policy ids, before/after, result and verification.
- Rollback: where technically possible, retain a previous value and rollback plan.
- Kill switch: tenant and global switches can disable all writes immediately.
- Degraded mode: when WB API health is degraded, read-only monitoring continues but writes stop.
