#!/bin/bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
if [ ! -x .venv/bin/python ]; then
  echo "WB AI Manager MCP: сначала один раз запусти START_WB_AI_MANAGER.command, чтобы создать .venv." >&2
  exit 1
fi
export PYTHONPATH="$ROOT/src"
export WB_AI_MCP_TRANSPORT=stdio
exec "$ROOT/.venv/bin/python" -m wb_control_center.plugin_gateway
