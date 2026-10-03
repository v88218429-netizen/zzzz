# Sellmonitor GitHub Central Worker

Production execution path:

```
GitHub (mainggg)
  -> GitHub Actions (ubuntu-latest)
  -> authorized Google Apps Script adapter
  -> Sellmonitor CONTROL CENTER
  -> client spreadsheet 97_Управление / 97_Код
  -> WB + Sellmonitor loaders
  -> QC
  -> READY
```

Make is not part of the runtime. Self-hosted/Mac runners are not part of the runtime.

## Files

- `core.py` — fail-closed client state machine and QC contract.
- `SELLMONITOR_GITHUB_WORKER.gs` — central queue executor. It opens each registered client by `spreadsheet_id`, executes only active `RUN_REMOTE` code from `97_Код`, namespaces Script Properties per client, records DONE/ERROR, and mirrors health into CONTROL CENTER.
- `sellmonitor-github-ci.yml` — syntax + state machine tests.
- `sellmonitor-github-deploy.yml` — GitHub-hosted deployment into the existing authorized Apps Script adapter.
- `sellmonitor-github-tick.yml` — GitHub-hosted 15-minute scheduler.

## Security

Client secrets are never committed. Runtime code rewrites `PropertiesService.getScriptProperties()` to a per-spreadsheet namespace in the central adapter, preventing cross-client key collisions.

The only GitHub repository secret required by the Google adapter is `CLASPRC_JSON`. The repository already has a known Apps Script project ID in the existing hosted deploy workflow; the worker reuses that authorized adapter instead of creating another service.

## READY semantics

A client is never marked READY from HTTP success alone. READY is accepted only after the client workbook reports every onboarding stage complete and QC PASS/READY. Errors stay fail-closed.
