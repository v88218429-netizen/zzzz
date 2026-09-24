#!/bin/bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
mkdir -p data
LOG="$ROOT/data/setup.log"
: > "$LOG"

find_python() {
  for bin in python3.13 python3.12 python3.11 python3; do
    if command -v "$bin" >/dev/null 2>&1; then
      if "$bin" - <<'PY' >/dev/null 2>&1
import sys
raise SystemExit(0 if sys.version_info >= (3,11) else 1)
PY
      then
        echo "$bin"
        return 0
      fi
    fi
  done
  return 1
}

PYTHON_BIN="$(find_python || true)"
if [ -z "$PYTHON_BIN" ]; then
  echo '❌ Нужен Python 3.11 или новее.'
  if command -v brew >/dev/null 2>&1; then
    echo 'Homebrew найден. Устанавливаю Python 3.12…'
    brew install python@3.12 >>"$LOG" 2>&1 || { echo "Не удалось установить Python. Лог: $LOG"; exit 1; }
    PYTHON_BIN="$(find_python || true)"
  fi
fi
if [ -z "$PYTHON_BIN" ]; then
  echo 'Установи Python 3.11+ и снова запусти START_WB_AI_MANAGER.command.'
  echo 'Страница загрузки сейчас откроется в браузере.'
  open 'https://www.python.org/downloads/macos/' >/dev/null 2>&1 || true
  exit 1
fi

echo "[1/3] Python: $($PYTHON_BIN -c 'import sys; print(sys.version.split()[0])')"
# Migrate previous credentials/settings BEFORE creating a blank .env.
if [ ! -f .env ]; then
  "$PYTHON_BIN" scripts/migrate_previous_env.py >>"$LOG" 2>&1 || true
fi
[ -f .env ] || cp .env.example .env
chmod 600 .env
[ -f config/catalog.csv ] || cp config/catalog.example.csv config/catalog.csv
[ -f config/watch_queries.yaml ] || cp config/watch_queries.example.yaml config/watch_queries.yaml

if [ ! -x .venv/bin/python ]; then
  echo '[2/3] Создаю локальное окружение…'
  rm -rf .venv
  "$PYTHON_BIN" -m venv .venv >>"$LOG" 2>&1
fi
source .venv/bin/activate

echo '[3/3] Устанавливаю компоненты WB AI Manager…'
python -m pip install --upgrade pip >>"$LOG" 2>&1 || { echo "Ошибка pip. Лог: $LOG"; exit 1; }
pip install -e . >>"$LOG" 2>&1 || { echo "Не удалось установить компоненты. Лог: $LOG"; exit 1; }

echo '✅ WB AI Manager готов.'
