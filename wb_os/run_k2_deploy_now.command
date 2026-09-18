#!/bin/bash
set -euo pipefail

REPO_RAW="https://raw.githubusercontent.com/v88218429-netizen/zzzz/mainggg"
WORK="$HOME/.wb-os-k2-deploy"
BACKUPS="$HOME/.wb-os-backups"
TARGET_HINT="Остатки"

mkdir -p "$WORK" "$BACKUPS"

echo "WB OS · K2 live deploy"
echo "======================"

if command -v clasp >/dev/null 2>&1; then
  run_clasp() { clasp "$@"; }
elif command -v npx >/dev/null 2>&1; then
  run_clasp() { npx --yes @google/clasp "$@"; }
else
  echo "Node/npm не найден. Установи Node.js и запусти файл ещё раз."
  exit 2
fi

curl -fsSL "$REPO_RAW/wb_os/apps_script/K2_EVOLUTION_ENGINE.gs" -o "$WORK/K2_EVOLUTION_ENGINE.gs"
curl -fsSL "$REPO_RAW/wb_os/tools/patch_live_master.py" -o "$WORK/patch_live_master.py"

PROJECT_DIR=""
while IFS= read -r -d '' f; do
  d="$(dirname "$f")"
  if grep -Rqs "function runFinalAutomationCycle_" "$d" 2>/dev/null \
     && grep -Rqs "function getK2WarehouseItems_" "$d" 2>/dev/null; then
    if [ -n "$PROJECT_DIR" ] && [ "$PROJECT_DIR" != "$d" ]; then
      echo "Найдено несколько подходящих clasp-проектов."
      exit 3
    fi
    PROJECT_DIR="$d"
  fi
done < <(find "$HOME/Desktop" "$HOME/Documents" "$HOME/Projects" "$HOME/Code" "$HOME/Developer" "$HOME" \
  -maxdepth 7 -name .clasp.json -print0 2>/dev/null || true)

if [ -z "$PROJECT_DIR" ]; then
  SCRIPT_ID=""

  if command -v osascript >/dev/null 2>&1; then
    URLS="$(
      osascript <<'APPLESCRIPT' 2>/dev/null || true
set outText to ""
tell application "Google Chrome"
  repeat with w in windows
    repeat with t in tabs of w
      set u to URL of t
      set ttl to title of t
      if u contains "script.google.com" and u contains "/projects/" then
        set outText to outText & u & tab & ttl & linefeed
      end if
    end repeat
  end repeat
end tell
return outText
APPLESCRIPT
    )"

    CANDIDATE="$(printf '%s\n' "$URLS" | grep -i "$TARGET_HINT" | head -n 1 || true)"
    if [ -z "$CANDIDATE" ]; then
      COUNT="$(printf '%s\n' "$URLS" | grep -c 'script.google.com' || true)"
      if [ "$COUNT" = "1" ]; then
        CANDIDATE="$(printf '%s\n' "$URLS" | grep 'script.google.com' | head -n 1)"
      fi
    fi

    if [ -n "$CANDIDATE" ]; then
      SCRIPT_URL="$(printf '%s' "$CANDIDATE" | cut -f1)"
      SCRIPT_ID="$(printf '%s' "$SCRIPT_URL" | sed -E 's#.*?/projects/([^/]+).*#\1#')"
    fi
  fi

  if [ -z "$SCRIPT_ID" ]; then
    echo ""
    echo "Не удалось автоматически определить Script ID."
    echo "Открой в Chrome нужную таблицу → Расширения → Apps Script."
    echo "Оставь вкладку Apps Script открытой и запусти этот файл ещё раз."
    exit 4
  fi

  PROJECT_DIR="$WORK/live-project"
  rm -rf "$PROJECT_DIR"
  mkdir -p "$PROJECT_DIR"

  cat > "$PROJECT_DIR/.clasp.json" <<EOF
{"scriptId":"$SCRIPT_ID","rootDir":"."}
EOF
fi

if [ ! -f "$HOME/.clasprc.json" ]; then
  echo ""
  echo "Нужна одноразовая авторизация Google для clasp."
  echo "Сейчас откроется браузер. Выбери тот же Google-аккаунт и нажми Разрешить."
  run_clasp login
fi

cd "$PROJECT_DIR"
run_clasp pull

SOURCE_ROOT="$(
python3 - <<'PY'
import json, pathlib
p=pathlib.Path(".").resolve()
cfg=json.loads((p/".clasp.json").read_text(encoding="utf-8"))
root=str(cfg.get("rootDir") or "").strip()
print(str((p/root).resolve() if root else p))
PY
)"

MATCHES="$(
  grep -RIl "function runFinalAutomationCycle_" "$SOURCE_ROOT" --include='*.gs' --include='*.js' 2>/dev/null | wc -l | tr -d ' '
)"
if [ "$MATCHES" != "1" ]; then
  echo "Проверка остановлена: master-файлов найдено $MATCHES, ожидался ровно 1."
  exit 5
fi

grep -Rq "function getK2WarehouseItems_" "$SOURCE_ROOT" --include='*.gs' --include='*.js' || {
  echo "Проверка остановлена: это не тот K2 Apps Script проект."
  exit 6
}

TS="$(date +%Y%m%d-%H%M%S)"
BACKUP="$BACKUPS/k2-live-$TS"
mkdir -p "$BACKUP"
rsync -a "$PROJECT_DIR/" "$BACKUP/"
echo "Rollback backup: $BACKUP"

cp "$WORK/K2_EVOLUTION_ENGINE.gs" "$SOURCE_ROOT/K2_EVOLUTION_ENGINE.gs"
python3 "$WORK/patch_live_master.py" "$SOURCE_ROOT"

COUNT=0
while IFS= read -r -d '' f; do
  TMP="$(mktemp -t wb-os-js.XXXXXX)"
  cp "$f" "$TMP"
  node --check "$TMP"
  rm -f "$TMP"
  COUNT=$((COUNT+1))
done < <(find "$SOURCE_ROOT" -type f \( -name '*.gs' -o -name '*.js' \) -print0)

echo "Syntax OK: $COUNT files"

cd "$PROJECT_DIR"
run_clasp push -f

echo ""
echo "✅ K2 Evolution code pushed to live Apps Script."
echo "Следующий master-цикл сам покажет LIVE / MASTER на листе «Автоматизация»."
echo ""
read -r -p "Нажми Enter, чтобы закрыть окно..."
