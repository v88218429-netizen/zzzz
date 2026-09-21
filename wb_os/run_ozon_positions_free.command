#!/bin/bash
set -euo pipefail

REPO_RAW='https://raw.githubusercontent.com/v88218429-netizen/zzzz/mainggg'
DEFAULT_SCRIPT_URL='https://script.google.com/u/0/home/projects/15tH3jwOa5hKJFahIyPX5AIXO7KRf1O4jusfDfPIdX6KeH_GqMUC71hNT/edit'
SCRIPT_URL="${1:-$DEFAULT_SCRIPT_URL}"

printf '\nOZON · FREE POSITIONS DEPLOY\n============================\n\n'

if command -v clasp >/dev/null 2>&1; then
  run_clasp() { clasp "$@"; }
elif command -v npx >/dev/null 2>&1; then
  run_clasp() { npx --yes @google/clasp "$@"; }
else
  echo '❌ Не найден Node/npm/clasp.'
  exit 2
fi

SCRIPT_ID="$(printf '%s' "$SCRIPT_URL" | sed -nE 's#.*projects/([^/]+)/edit.*#\1#p')"
[ -n "$SCRIPT_ID" ] || SCRIPT_ID="$(printf '%s' "$SCRIPT_URL" | tr -d '[:space:]')"

WORK="$(mktemp -d "${TMPDIR:-/tmp}/ozon-free-pos.XXXXXX")"
PROJECT="$WORK/project"
BACKUPS="$HOME/.wb-os-backups"
mkdir -p "$PROJECT" "$BACKUPS"
trap 'rm -rf "$WORK"' EXIT

printf '[1/6] Скачиваю Ozon Seller API модуль...\n'
curl --connect-timeout 10 --max-time 30 -fsSL   "$REPO_RAW/wb_os/apps_script/OZON_SELLER_POSITIONS_FREE.gs"   -o "$WORK/OZON_SELLER_POSITIONS_FREE.gs"
cp "$WORK/OZON_SELLER_POSITIONS_FREE.gs" "$WORK/check.js"
node --check "$WORK/check.js" >/dev/null

printf '[2/6] Скачиваю точный LIVE Apps Script...\n'
cat > "$PROJECT/.clasp.json" <<JSON
{"scriptId":"$SCRIPT_ID","rootDir":"."}
JSON
(cd "$PROJECT" && run_clasp pull)

STAMP="$(date +%Y%m%d-%H%M%S)"
BACKUP="$BACKUPS/ozon-free-pos-$STAMP"
mkdir -p "$BACKUP"
cp -R "$PROJECT"/. "$BACKUP"/
printf '      rollback: %s\n' "$BACKUP"

printf '[3/6] Подключаю бесплатный контур к существующим облачным часам...\n'
cp "$WORK/OZON_SELLER_POSITIONS_FREE.gs" "$PROJECT/OZON_SELLER_POSITIONS_FREE.gs"

python3 - "$PROJECT" <<'PY'
from pathlib import Path
import sys

root = Path(sys.argv[1])
files = [p for p in root.rglob('*') if p.is_file() and p.suffix in {'.gs','.js'}]

bridges = [p for p in files if 'function ozonRadarServerTick()' in p.read_text(encoding='utf-8', errors='ignore')]
if len(bridges) == 1:
    p = bridges[0]
    s = p.read_text(encoding='utf-8')
    old = """    if (!serverUrl || !secret) {
      return;
    }"""
    new = """    if (!serverUrl || !secret) {
      try {
        if (typeof ozonSellerPositionsFreeTick === 'function') {
          ozonSellerPositionsFreeTick();
        }
      } catch (freeOzonError) {
        Logger.log('Ozon free positions fallback: ' + freeOzonError.message);
      }
      return;
    }"""
    if old in s and 'Ozon free positions fallback:' not in s:
        p.write_text(s.replace(old,new,1),encoding='utf-8')
        print('      patched radar heartbeat:', p.name)

masters = [p for p in files if 'function runFinalAutomationCycle_' in p.read_text(encoding='utf-8', errors='ignore')]
if len(masters) == 1:
    p = masters[0]
    s = p.read_text(encoding='utf-8')
    if 'OZON FREE POSITIONS HEARTBEAT' not in s:
        start = s.find('function runFinalAutomationCycle_')
        nxt = s.find('\nfunction ', start + 10)
        end = len(s) if nxt == -1 else nxt
        f = s[start:end]
        brace = f.find('{')
        if brace == -1:
            raise SystemExit('DEPLOY_FAIL: master opening brace not found')
        block = """
  /* OZON FREE POSITIONS HEARTBEAT */
  try {
    if (typeof ozonSellerPositionsFreeTick === 'function') {
      ozonSellerPositionsFreeTick();
    }
  } catch (ozonFreePositionsError) {
    Logger.log('Ozon free positions: ' + ozonFreePositionsError.message);
  }
"""
        f = f[:brace+1] + block + f[brace+1:]
        p.write_text(s[:start]+f+s[end:],encoding='utf-8')
        print('      patched master heartbeat:', p.name)
PY

printf '[4/6] Проверяю синтаксис всего проекта...\n'
while IFS= read -r -d '' f; do
  tmp="$WORK/$(basename "$f" | tr ' ' '_').js"
  cp "$f" "$tmp"
  node --check "$tmp" >/dev/null
  rm -f "$tmp"
done < <(find "$PROJECT" -type f \( -name '*.gs' -o -name '*.js' \) -print0)

printf '[5/6] Отправляю в LIVE Apps Script...\n'
(cd "$PROJECT" && run_clasp push -f)

printf '[6/6] Пытаюсь создать отдельный 4-часовой trigger и сделать первый замер...\n'
if (cd "$PROJECT" && run_clasp run setupOzonSellerPositionsFree); then
  echo '✅ Первый запуск и 4-часовой trigger созданы.'
else
  echo 'ℹ️ clasp run недоступен: модуль всё равно подцеплен к существующему master/radar heartbeat и сам ограничивает обращения до ~1 раза в 4 часа.'
fi

echo
echo '✅ Бесплатный Ozon positions fallback отправлен в LIVE Apps Script.'
echo "Rollback backup: $BACKUP"
echo 'Источник: официальный Ozon Seller API; storefront 403 больше не блокирует обновление.'
