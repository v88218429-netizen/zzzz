# Persistent project rules

## Banned services (explicit owner instruction, 2026-10-14)
**Never use, configure, invoke, or recommend Make or Railway.** They do not work for the owner. This rule applies to all automation, analytics, DUSHA, WB/Ozon, and AI Video Monitor projects in this repository. Do not silently restore either service as a fallback.

## Allowed implementation paths
Use GitHub Actions, Google Apps Script, Google Drive/Sheets connectors, and other actually verified integrations. Background operation must not depend on the owner's Mac being online; local Mac may only assist during one-time setup/debugging.

## Acceptance requirements
Treat actual results—not plans, source code, or a green lint/test—as the success criterion. For AI Video Monitor, demonstrate live GitHub-to-original-Google-Sheet read, authenticated write, and readback; real video transcript extraction; grounded analysis tied to owner goals; and repeated scheduled runs. Keep a visible truthful status when permissions, Google consent, or YouTube transcription are blocked. Never invent video content or claim unverified automation is live.

## Codex project workflows (load on demand)
- For WB audits, product-image QA and safe release work, use the relevant `.agents/skills/*/SKILL.md` rather than loading all source files and long instruction blocks into every task.
- Before changing a marketplace runtime, Google Sheet bridge or GitHub Actions job, follow `.agents/skills/safe-wb-release/SKILL.md`. Preserve live schedules, business thresholds, credentials, and approval/evidence gates.
- When working on OpenAI API, Codex, MCP, SDK or plugin integration, consult the read-only OpenAI Docs MCP server if available. Do not assume it is installed on every device.
- `python3 scripts/codex_ci_audit.py --json` is read-only: review candidates are not proof of waste or permission to disable workflows.
