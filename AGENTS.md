# Persistent project rules

## Banned services (explicit owner instruction, 2026-10-14)
**Never use, configure, invoke, or recommend Make or Railway.** They do not work for the owner. This rule applies to all automation, analytics, DUSHA, WB/Ozon, and AI Video Monitor projects in this repository. Do not silently restore either service as a fallback.

## Allowed implementation paths
Use GitHub Actions, Google Apps Script, Google Drive/Sheets connectors, and other actually verified integrations. Background operation must not depend on the owner's Mac being online; local Mac may only assist during one-time setup/debugging.

## Acceptance requirements
Treat actual results—not plans, source code, or a green lint/test—as the success criterion. For AI Video Monitor, demonstrate live GitHub-to-original-Google-Sheet read, authenticated write, and readback; real video transcript extraction; grounded analysis tied to owner goals; and repeated scheduled runs. Keep a visible truthful status when permissions, Google consent, or YouTube transcription are blocked. Never invent video content or claim unverified automation is live.
