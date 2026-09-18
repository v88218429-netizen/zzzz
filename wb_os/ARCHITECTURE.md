# WB OS architecture

## Biological model

| System concept | WB OS role |
|---|---|
| Genome | GitHub repository |
| Brain | Orchestrator / scheduler |
| Nervous system | Event bus |
| Memory | State store / history |
| Organs | K2, WB, Ozon, Sellmonitor, FF connectors |
| Immune system | Watchdogs, circuit breakers, quarantine |
| Blood | Canonical normalized data contracts |
| Evolution | Shadow tests, measured promotion, rollback |

## Core laws

1. **Delta first.** Do work only for changed data.
2. **Fail closed.** Bad or incomplete source data must not silently overwrite trusted state.
3. **One source of truth.** Operational state must have a canonical representation.
4. **Idempotency.** Re-running the same input must not duplicate actions or alerts.
5. **Backpressure.** When a source is failing, retry slower instead of hammering it.
6. **Observable by default.** Every module exposes last success, age, item count, error state, and version.
7. **No scheduled GitHub polling.** GitHub Actions is for validation/deploy, not the runtime clock.
8. **Batch I/O.** Google Sheets writes happen in blocks, not per cell/product.
9. **State-change alerts.** Telegram only on transitions, material deterioration, or recovery.
10. **Rollbackable changes.** Every production mutation is versioned.

## Runtime phases

### Phase A — current low-cost mode

K2 API -> Apps Script -> trusted K2 sheet -> formulas -> Telegram

Optimization:
- one K2 fetch per tick;
- snapshot hash;
- if unchanged: heartbeat only;
- if changed: one batch stock write + downstream checks.

### Phase B — scalable worker

Marketplace APIs -> persistent worker -> PostgreSQL/SQLite state -> event rules -> Google Sheets read model / Telegram

Google Sheets becomes a view, not the database.

## Event contract

Every future connector should emit a small canonical event:

```json
{
  "event_id": "source:key:version",
  "type": "stock.changed",
  "source": "k2",
  "entity_key": "sku:06338",
  "observed_at": "2026-09-18T14:30:00+03:00",
  "payload": {
    "available": 704,
    "reserved": 0,
    "min_stock": 0
  }
}
```

The rules layer decides what to do with the event.

## Health contract

Every organ must expose:

- version
- last_run_at
- last_success_at
- age_minutes
- fetched_count
- changed_count
- current_status
- last_error
- consecutive_failures
- circuit_state

## Evolution contract

No self-modifying production code.

New logic follows:

observe -> hypothesis -> new version -> shadow run -> compare -> promote -> monitor -> rollback if worse

This gives controlled evolution rather than uncontrolled mutation.
