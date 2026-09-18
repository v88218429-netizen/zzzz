#!/bin/bash
set -euo pipefail

REPO_RAW="https://raw.githubusercontent.com/v88218429-netizen/zzzz/mainggg"
WORK="$HOME/.wb-os-k2-deploy"
BACKUPS="$HOME/.wb-os-backups"

mkdir -p "$WORK" "$BACKUPS"

echo "WB OS · K2 FAST live deploy"
echo "==========================="
echo "[1/8] Проверяю Node/clasp..."

if command -v clasp >/dev/null 2>&1; then
  run_clasp() { clasp "$@"; }
elif command -v npx >/dev/null 2>&1; then
  run_clasp() { npx --yes @google/clasp "$@"; }
else
  echo "❌ Не найден Node/npm. Установи Node.js и повтори."
  exit 2
fi

echo "[2/8] Скачиваю проверенный WB OS payload..."
curl --connect-timeout 10 --max-time 30 -fsSL \
  "$REPO_RAW/wb_os/apps_script/K2_EVOLUTION_ENGINE.gs" \
  -o "$WORK/K2_EVOLUTION_ENGINE.gs"
curl --connect-timeout 10 --max-time 30 -fsSL \
  "$REPO_RAW/wb_os/tools/patch_live_master.py" \
  -o "$WORK/patch_live_master.py"

echo "[3/8] Нужен URL открытого Apps Script проекта."
echo "В Chrome открой: таблица «Остатки v.1 27июля» → Расширения → Apps Script."
echo
read -r -p "Вставь URL из адресной строки Apps Script и нажми Enter: " SCRIPT_URL

SCRIPT_ID="$(printf '%s' "$SCRIPT_URL" | sed -nE 's#.*script\.google\.com/[^ ]*/projects/([^/ ?#]+).*#\1#p')"
if [ -z "$SCRIPT_ID" ]; then
  # Also accept raw Script ID.
  SCRIPT_ID="$(printf '%s' "$SCRIPT_URL" | tr -d '[:space:]')"
fi

if [ -z "$SCRIPT_ID" ]; then
  echo "❌ Не смог определить Script ID."
  exit 3
fi

PROJECT_DIR="$WORK/live-project"
rm -rf "$PROJECT_DIR"
mkdir -p "$PROJECT_DIR"

cat > "$PROJECT_DIR/.clasp.json" <<EOF
{"scriptId":"$SCRIPT_ID","rootDir":"."}
EOF

echo "[4/8] Проверяю Google авторизацию clasp..."
if [ ! -f "$HOME/.clasprc.json" ]; then
  echo "Откроется Google. Это одноразовая авторизация."
  run_clasp login
fi

echo "[5/8] Скачиваю ТОЧНЫЙ live Apps Script..."
cd "$PROJECT_DIR"
run_clasp pull

MATCHES="$(
  grep -RIl "function runFinalAutomationCycle_" "$PROJECT_DIR" \
    --include='*.gs' --include='*.js' 2>/dev/null | wc -l | tr -d ' '
)"
if [ "$MATCHES" != "1" ]; then
  echo "❌ Защита остановила deploy: master-файлов найдено $MATCHES вместо 1."
  echo "Ничего в Google не изменено."
  exit 4
fi

if ! grep -Rq "function getK2WarehouseItems_" "$PROJECT_DIR" \
  --include='*.gs' --include='*.js'; then
  echo "❌ Защита остановила deploy: это не K2 Apps Script проект."
  echo "Ничего в Google не изменено."
  exit 5
fi

echo "[6/8] Делаю rollback-бэкап и ставлю K2 Evolution..."
TS="$(date +%Y%m%d-%H%M%S)"
BACKUP="$BACKUPS/k2-live-$TS"
mkdir -p "$BACKUP"
rsync -a "$PROJECT_DIR/" "$BACKUP/"

cp "$WORK/K2_EVOLUTION_ENGINE.gs" "$PROJECT_DIR/K2_EVOLUTION_ENGINE.gs"
python3 "$WORK/patch_live_master.py" "$PROJECT_DIR"

echo "[7/8] Проверяю синтаксис..."
COUNT=0
while IFS= read -r -d '' f; do
  TMP="$(mktemp -t wb-os-js.XXXXXX)"
  cp "$f" "$TMP"
  node --check "$TMP"
  rm -f "$TMP"
  COUNT=$((COUNT+1))
done < <(find "$PROJECT_DIR" -type f \( -name '*.gs' -o -name '*.js' \) -print0)

echo "Syntax OK: $COUNT файлов"

echo "[8/8] Отправляю в LIVE Apps Script..."
cd "$PROJECT_DIR"
run_clasp push -f

echo
echo "✅ ГОТОВО: K2 Evolution отправлен в LIVE Apps Script."
echo "Rollback backup: $BACKUP"
echo "Теперь жди следующий master-цикл; лист «Автоматизация» должен показать LIVE / MASTER."
echo
read -r -p "Нажми Enter, чтобы закрыть..."
