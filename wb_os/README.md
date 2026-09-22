# WB Operating System (WB OS)

This directory is the source-of-truth for the marketplace automation architecture.

## Design goal

Scale by **changes**, not by the number of products.

A stable run with 100 or 10,000 products should do almost no work when the source snapshot did not change.

## Runtime model

- **GitHub** — source code, versions, tests, rollback. No high-frequency polling.
- **Google Apps Script** — near-source execution for Google Sheets/K2 while the system is being migrated.
- **Self-hosted runner / VPS** — future persistent worker for high-volume marketplace connectors.
- **Google Sheets** — operator UI/read model, not the primary state database.
- **Telegram** — event-driven alerts only when state changes.

## K2 v0.1

Files:

- `apps_script/K2_BASELINE_2026-09-18.gs` — exact current K2 script captured before migration.
- `apps_script/K2_EVOLUTION_ENGINE.gs` — efficient wrapper that reuses the baseline functions and:
  - hashes the normalized K2 snapshot;
  - skips heavy sheet rewrites when nothing changed;
  - quarantines suspicious large SKU-count drops;
  - refreshes a compact heartbeat;
  - deduplicates Telegram watchdog alerts;
  - sends recovery alerts;
  - records runtime diagnostics.

### Migration function

In the bound Apps Script project run once:

`setupK2Evolution()`

That replaces the old K2 10-minute trigger with `k2EvolutionTick`.

The old baseline remains available for rollback.

## Secrets

Never commit credentials.

Keep in Apps Script Properties:

- `K2_USERNAME`
- `K2_PASSWORD`
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`

## Next organs

The same contract will be used for:

- Wildberries orders / sales / ads
- Ozon orders / positions
- Sellmonitor
- fulfillment stock / supplier orders
- finance and RNP

Each connector should emit normalized events and health, not directly mutate every downstream sheet.


Validation: GitHub Actions runs only when `wb_os/**` changes or by manual dispatch; there is no cron polling.


## Deployment safety

CI validates the `wb_os/final_repair` deployment package end-to-end before it can be treated as green: recursive shell syntax, concatenation of all bundle parts, strict base64 decode, `tar.gz` integrity, extraction, and JavaScript syntax of the extracted Apps Script files. A green syntax-only check is not sufficient for a deploy package.
