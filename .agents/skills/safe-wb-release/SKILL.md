---
name: safe-wb-release
description: "Nonbreaking changes to marketplace agents, FBS, GitHub Actions and Google Sheet bridges with regression tests, read-back and rollback."
---

# Safe release for WB / DUSHA / automation

Invoke for a code change, workflow change, schedule, bridge or deployment affecting WB seller operations or reporting.

1. Read existing AGENTS.md, identify production owners and the exact workflow/entrypoint. Inspect prior implementation and active pending PRs before patching.
2. Capture baseline: current commit, last confirmed live outcome, CI status and relevant production freshness. Never conflate a green test with a successful live read/write.
3. Prefer a small additive change behind an opt-in flag with unchanged default. Do not rewrite user-managed .env, stored tokens, business thresholds, SKU mappings, or data.
4. Create a dedicated branch and draft PR. Do not merge until the relevant checks pass and applicable data read-backs prove no regression.
5. Run the smallest focused test first, then broader integration tests; include missing-source and malformed-data cases. Do not run a heavy video/model-download workflow as a test unless required by the change.
6. For GitHub Actions: distinguish CI jobs (cancellable) from money/order/state-processing jobs (normally non-cancellable). Check branch-protection requirements before adding path filters to required checks.
7. For Sheets / FBS: snapshot row counts, IDs and formulas; write only to an explicitly approved target; compare read-back; make backup + rollback possible.
8. For WB AI Manager: preserve read-only behavior and numerical safety gates; never attribute campaign totals to a single SKU or overwrite seller-defined margin.
9. Final report: changed files, test commands/results, live verification (or explicit NOT VERIFIED), effect on scheduling, risk and rollback link.
10. Never use Make or Railway, never expose credentials in logs, and never claim evidence-gated completion on another controller's behalf.

Stay within repository permissions; seek user authorization for financial writes, publication or destructive operations.
