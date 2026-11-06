---
name: wb-seller-audit
description: "Read-only Wildberries seller audits, SKU advertising economics, inventory and FBS investigations using verified live data."
---

# WB seller audit (Codex)

Use only for WB seller analysis, campaign diagnostics, financial reconciliation or incidents. Do not load all source files by default.

1. Identify the exact account, SKU/nmID, campaign, requested period and business question.
2. Load only the relevant existing project skill:
   - Overall audit: \`wb_ai_manager/source/skills/wb-supervisor/SKILL.md\`
   - Advertising: \`wb_ai_manager/source/skills/wb-advertising/SKILL.md\`
   - Stock: \`wb_ai_manager/source/skills/wb-inventory/SKILL.md\`
   - Unit economics: \`wb_ai_manager/source/skills/wb-unit-economics/SKILL.md\`
   - Incidents: \`wb_ai_manager/source/skills/wb-incident-investigation/SKILL.md\`
   - Safety: \`wb_ai_manager/source/skills/wb-safety/SKILL.md\`
3. Prefer the freshest authenticated source. For every metric, retain source, as-of time, account and unit. A passed unit test is not proof that a production feed is healthy.
4. For advertising, never infer nmID-level performance from multi-item campaign totals. Preserve the seller's manually assigned target margin.
5. Confirm FBS product mappings and distinguish cachepot (кашпо) from flower pot/вазон. Never silently change human-verified mappings.
6. Start with read-only diagnostics. Respect \`FORCE_READ_ONLY=true\` and existing approval/evidence gates. Never issue a marketplace write or change monetary rules without explicit authorized workflow.
7. Output: verified facts, missing/stale sources, hypothesis, recommended action, risk, acceptance check, and rollback. Do not claim a live fix without a successful read-back.
8. Reduce context: summarize large results with counts and exceptions; retrieve exact source rows on demand.

Do not configure or suggest Make or Railway. GitHub Actions / Google Apps Script / existing verified services are the preferred paths.
