# Ozon Live Radar · 24/7 Server

Server-only live Ozon search-position monitor. Local Mac is not part of the production architecture.

Target deployment for the existing setup:
- Railway project: `believable-nourishment`
- Existing Telegram service: `wb-bot`
- Recommended deployment: a separate lightweight service in the same Railway project, reusing the bot token/chat ID through Railway environment variables.

## Runtime flow

1. Google Apps Script pushes enabled rows from `06_Радар_1мин` to `POST /config`.
2. The server checks Ozon storefront search sequentially at each row's interval.
3. Every successful check becomes an event in `GET /events`.
4. Significant drops and TOP-boundary exits are sent to Telegram immediately.
5. Apps Script pulls events once per minute and writes them to `06_Радар_История`.

## Why this is not the Wildberries price parser

Wildberries exposes a public card JSON endpoint that accepts ordinary server requests.

Ozon storefront search is protected by Variti/anti-bot. Hosted-server diagnostics on 2026-09-19 produced:
- desktop composer/entrypoint JSON: HTTP 403;
- mobile `api.ozon.ru` mweb endpoints: HTTP 403;
- hosted Chrome: Ozon page returned «Похоже, нет соединения»;
- no residential-proxy secrets are currently configured in GitHub.

Therefore deployment must always run `POST /probe` from the final Railway service before enabling alerts. A source error is never treated as a ranking drop.

## Required Railway variables

- `RADAR_SECRET`
- `TELEGRAM_BOT_TOKEN` or `BOT_TOKEN`
- `TELEGRAM_CHAT_ID` or `OWNER_CHAT_ID`

Optional:
- `OZON_PROXY` — proxy URL only if the final Railway egress is blocked by Ozon.
- `RADAR_TASKS_JSON` — boot-time task list; otherwise Apps Script supplies tasks with `POST /config`.
- `PORT` — Railway sets this automatically.

## Endpoints

- `GET /health` — public service status.
- `POST /probe` — Bearer-authenticated one-off Ozon egress test; does not change radar state.
- `POST /config` — Bearer-authenticated live task configuration.
- `GET /events?after=<cursor>` — Bearer-authenticated event feed.
- `GET /state` — Bearer-authenticated current state.

## Safety rule

HTTP 403/307/429, captcha, anti-bot pages, transport errors and empty search payloads are source failures. They never become `NOT_FOUND`, never overwrite the last valid position, and never trigger a false Telegram ranking alert.
