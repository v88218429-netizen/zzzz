#!/bin/bash
set -euo pipefail

RAW="https://raw.githubusercontent.com/v88218429-netizen/zzzz/mainggg"
WORK="$HOME/.ozon-radar-bootstrap"
BACKUPS="$HOME/.wb-os-backups"
mkdir -p "$WORK/ozon_radar" "$WORK/wb_os/apps_script" "$WORK/wb_os/tools" "$BACKUPS"

echo "OZON RADAR · LIVE 1 MIN"
echo "======================"

if command -v clasp >/dev/null 2>&1; then
  run_clasp() { clasp "$@"; }
elif command -v npx >/dev/null 2>&1; then
  run_clasp() { npx --yes @google/clasp "$@"; }
else
  echo "❌ Нужен Node/npm (clasp)."
  exit 2
fi

echo "[1/9] Скачиваю live-пакет..."
curl -fsSL "$RAW/ozon_radar/worker.py" -o "$WORK/ozon_radar/worker.py"
curl -fsSL "$RAW/ozon_radar/requirements.txt" -o "$WORK/ozon_radar/requirements.txt"
curl -fsSL "$RAW/ozon_radar/install_launchd.sh" -o "$WORK/ozon_radar/install_launchd.sh"
curl -fsSL "$RAW/ozon_radar/repair_mac_runner.sh" -o "$WORK/ozon_radar/repair_mac_runner.sh"
curl -fsSL "$RAW/wb_os/apps_script/OZON_RADAR_TELEGRAM_RELAY.gs" -o "$WORK/wb_os/apps_script/OZON_RADAR_TELEGRAM_RELAY.gs"
curl -fsSL "$RAW/wb_os/apps_script/K2_EVOLUTION_ENGINE.gs" -o "$WORK/wb_os/apps_script/K2_EVOLUTION_ENGINE.gs"
curl -fsSL "$RAW/wb_os/tools/discover_clasp_project.py" -o "$WORK/wb_os/tools/discover_clasp_project.py"
curl -fsSL "$RAW/wb_os/tools/patch_live_master.py" -o "$WORK/wb_os/tools/patch_live_master.py"
chmod +x "$WORK/ozon_radar/install_launchd.sh" "$WORK/ozon_radar/repair_mac_runner.sh"

echo "[2/9] Восстанавливаю self-hosted Mac runner..."\nbash "$WORK/ozon_radar/repair_mac_runner.sh" || true\n\necho "[3/9] Проверяю Chrome и Google OAuth..."
test -x "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
if [ ! -f "$HOME/.clasprc.json" ]; then
  echo "Нужна одноразовая авторизация Google."
  run_clasp login
fi

echo "[4/9] Проверяю реальную выдачу Ozon..."
PROBE="$WORK/probe-venv"
rm -rf "$PROBE"
python3 -m venv "$PROBE"
"$PROBE/bin/python" -m pip install --upgrade pip >/dev/null
"$PROBE/bin/python" -m pip install -r "$WORK/ozon_radar/requirements.txt"
"$PROBE/bin/python" "$WORK/ozon_radar/worker.py" \
  --probe "лопата садовая" "5094364543" --max-position 30

echo "[5/9] Ставлю минутный worker..."
bash "$WORK/ozon_radar/install_launchd.sh"

echo "[6/9] Нахожу текущий WB OS Apps Script..."
PROJECT_DIR="$(python3 "$WORK/wb_os/tools/discover_clasp_project.py")"
test -f "$PROJECT_DIR/.clasp.json"
cd "$PROJECT_DIR"
run_clasp pull

SOURCE_ROOT="$(PROJECT_DIR="$PROJECT_DIR" python3 - <<'PY'
import json, os, pathlib
p=pathlib.Path(os.environ["PROJECT_DIR"])
cfg=json.loads((p/".clasp.json").read_text(encoding="utf-8"))
root=str(cfg.get("rootDir") or "").strip()
print(str((p/root).resolve() if root else p.resolve()))
PY
)"
test -d "$SOURCE_ROOT"

echo "[7/9] Backup + Telegram relay..."
TS="$(date +%Y%m%d-%H%M%S)"
BACKUP="$BACKUPS/ozon-radar-live-$TS"
mkdir -p "$BACKUP"
rsync -a "$PROJECT_DIR/" "$BACKUP/"

cp "$WORK/wb_os/apps_script/K2_EVOLUTION_ENGINE.gs" "$SOURCE_ROOT/K2_EVOLUTION_ENGINE.gs"
cp "$WORK/wb_os/apps_script/OZON_RADAR_TELEGRAM_RELAY.gs" "$SOURCE_ROOT/OZON_RADAR_TELEGRAM_RELAY.gs"
python3 "$WORK/wb_os/tools/patch_live_master.py" "$SOURCE_ROOT"

echo "[8/9] Проверяю синтаксис и отправляю Apps Script..."
while IFS= read -r -d '' f; do
  TMPDIR_CHECK="$(mktemp -d -t ozon-radar-js.XXXXXX)"
  cp "$f" "$TMPDIR_CHECK/check.js"
  node --check "$TMPDIR_CHECK/check.js"
  rm -rf "$TMPDIR_CHECK"
done < <(find "$SOURCE_ROOT" -type f \( -name '*.gs' -o -name '*.js' \) -print0)

cd "$PROJECT_DIR"
run_clasp push -f

# If Execution API is available, create the 1-minute relay immediately.
# Otherwise WB OS master will call ensureOzonRadarTelegramTrigger_ automatically.
run_clasp run setupOzonRadarTelegramRelay >/dev/null 2>&1 || true

echo "[9/9] Проверяю worker..."
sleep 12
if [ -f "$HOME/.ozon-radar/health.json" ]; then
  cat "$HOME/.ozon-radar/health.json"
else
  echo "Health пока не создан; показываю логи:"
  tail -80 "$HOME/.ozon-radar/logs/stderr.log" 2>/dev/null || true
  tail -80 "$HOME/.ozon-radar/logs/stdout.log" 2>/dev/null || true
fi

echo
echo "✅ OZON RADAR установлен."
echo "Проверка: каждые 60 секунд."
echo "Управление: лист 06_Радар_1мин."
echo "Telegram: через существующий WB OS bot."
echo "Rollback Apps Script: $BACKUP"
