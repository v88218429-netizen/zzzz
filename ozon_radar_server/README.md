# Ozon Live Radar · Railway

24/7 service for live Ozon search-position monitoring.

## Runtime flow

1. Google Apps Script pushes enabled rows from `06_Радар_1мин` to `POST /config`.
2. The server checks Ozon storefront JSON sequentially at each row's interval.
3. Every check becomes an event in `GET /events`.
4. Significant drops and TOP-boundary exits are sent to Telegram immediately.
5. Apps Script pulls events once per minute and writes them to Google Sheets history.

## Required Railway variables

- `RADAR_SECRET`
- `TELEGRAM_BOT_TOKEN` or `BOT_TOKEN`
- `TELEGRAM_CHAT_ID` or `OWNER_CHAT_ID`

Optional:

- `OZON_PROXY` — HTTP/SOCKS proxy if the Railway egress IP is blocked by Ozon.
- `PORT` — Railway sets this automatically.

## Endpoints

- `GET /health` — public health/status summary.
- `POST /config` — Bearer-authenticated live task configuration.
- `GET /events?after=<cursor>` — Bearer-authenticated event feed.
- `GET /state` — Bearer-authenticated current state.

The service never treats an Ozon transport/anti-bot error as a lost search position, so a 403/307 cannot generate a false ranking alert.
