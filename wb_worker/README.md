# WB worker

Persistent Wildberries Seller API worker for Maxim's EA / WB AI Manager.

## Runtime

- package: `wb-mcp-server==2.6.1`
- HTTP/SSE port: `8001`
- health: `/api/health`
- MCP: `/sse`
- persistent state: `/data`

## Secrets

Never commit tokens.

Required at runtime:
- `WB_API_TOKEN` — seller read-only token
- `MCP_AUTH_TOKEN` — protects MCP SSE/messages when exposed outside a private network

Optional:
- `HEALTH_CHECK_INTERVAL_MIN=30`
- `DATA_DIR=/data`

The WB token is intentionally not stored in this repository.
