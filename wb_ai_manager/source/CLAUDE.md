# WB AI Control Center — Claude Code instructions

This project operates a Wildberries seller account through an MCP server. Treat marketplace writes as production changes.

## Operating model

- Read freely.
- Diagnose before proposing a write.
- Route writes through the project's action-gate whenever possible.
- Never lower a price, raise an ad bid/budget, stop a campaign permanently, cancel an order, deliver a supply, delete stock/media/cards, or answer a return claim without explicit approval.
- Prefer `pause` over irreversible `stop` for emergency advertising actions.
- When reporting an anomaly, connect advertising + funnel + stock + price + search position before attributing a cause.
- Do not call undocumented public WB endpoints unless `ENABLE_PUBLIC_WB_SEARCH=true` and the task is competitor monitoring.
- Never print or commit WB/Telegram/LLM tokens.

## Source of truth

- Thresholds and safety rules: `config/policies.yaml`
- SKU floors/costs: `config/catalog.csv`
- Search/competitor watchlist: `config/watch_queries.yaml`
- Runtime history: `data/control_center.sqlite3`
- Autonomous agents: `src/wb_control_center/agents/`

## Useful commands

```bash
wb-control doctor
wb-control run-agent advertising_monitor
wb-control run-agent inventory
wb-control run-all
wb-control run
```


## v0.7 read-only + live-policy rule
This package is read-only. Never weaken `FORCE_READ_ONLY` or add a UI write control unless the owner explicitly asks for a separate future build. Business strategy may be tuned live through Policy Studio; hard safety limits may not. Advertising recommendations must be numerical (`current → target bid`, spend corridor, evidence, re-check condition) or explicitly blocked for missing data — never end with vague wording such as "don't scale".
