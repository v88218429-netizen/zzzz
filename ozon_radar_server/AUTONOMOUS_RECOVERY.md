# Ozon Radar autonomous recovery

## Layers

1. **Proxy Scout**
   - Sources: several public, frequently refreshed proxy feeds.
   - Tests candidates in parallel.
   - Tests only public Ozon pages.
   - Never routes credentials, cookies, seller API tokens or private data through public proxies.

2. **Watchdog**
   - Reads radar /health.
   - Counts consecutive failures.
   - After the configured threshold, triggers Proxy Scout.
   - Does not silently claim recovery.

3. **AI Incident Operator**
   - Built with OpenAI Agents SDK.
   - Persistent SQLite session keeps technical incident context.
   - Reads radar health and Proxy Scout output.
   - Can trigger a bounded proxy scan.
   - Produces a root-cause diagnosis and next action.
   - Future tools can be added for GitHub branch/PR creation, CI inspection, Railway config switching and rollback.

## Safe autonomy policy

### Allowed without human approval
- Read logs/health/state.
- Run public-proxy connectivity tests.
- Switch among pre-approved test egress candidates in a canary environment.
- Restart a failed worker.
- Roll back to the last known-good release.
- Create a GitHub branch/PR and run CI.

### Requires a validated gate before automatic production change
- New code release: Windows/Linux CI success + canary Ozon probe success.
- New proxy: public-page probe success; never use with account sessions.

### Never automatic
- Expose or transmit secrets.
- Route authenticated seller traffic through public proxies.
- Delete user data.
- Change billing/payment settings.

## Intended escalation path

WATCHDOG -> PROXY SCOUT -> AI OPERATOR -> CANARY -> AUTO-RECOVERY

Only if all safe recovery paths fail:
AI OPERATOR -> Telegram/user escalation with diagnostics.
