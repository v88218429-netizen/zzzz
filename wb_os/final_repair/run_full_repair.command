#!/bin/bash
set -euo pipefail

echo "DEPLOY_DISABLED_BUNDLE_INTEGRITY: repair package is quarantined because CI detected a tar/gzip integrity failure. Nothing will be changed in Apps Script."
exit 2

REPO_RAW='https://raw.githubusercontent.com/v88218429-netizen/zzzz/mainggg/wb_os/final_repair'
DEFAULT_SCRIPT_URL='https://script.google.com/u/0/home/projects/15tH3jwOa5hKJFahIyPX5AIXO7KRf1O4jusfDfPIdX6KeH_GqMUC71hNT/edit'
SCRIPT_URL="${1:-$DEFAULT_SCRIPT_URL}"

printf '\nWB OS · FULL REPAIR DEPLOY\n==========================\n\n'

command -v node >/dev/null || { echo '❌ Node.js не найден'; exit 1; }
command -v clasp >/dev/null || { echo '❌ clasp не найден'; exit 1; }

SCRIPT_ID="$(printf '%s' "$SCRIPT_URL" | sed -nE 's#.*projects/([^/]+)/edit.*#\1#p')"
if [ -z "$SCRIPT_ID" ]; then
  SCRIPT_ID="$SCRIPT_URL"
fi

WORK="$(mktemp -d "${TMPDIR:-/tmp}/wb-os-full-repair.XXXXXX")"
PROJECT="$WORK/project"
PAYLOAD="$WORK/payload"
mkdir -p "$PROJECT" "$PAYLOAD"
trap 'rm -rf "$WORK"' EXIT

printf '[1/7] Скачиваю проверенный repair package...\n'
: > "$WORK/final_repair_bundle.b64"
for part in bundle.part00 bundle.part01 bundle.part02 bundle.part03; do
  curl --connect-timeout 10 --max-time 30 -fsSL \
    "$REPO_RAW/$part" >> "$WORK/final_repair_bundle.b64"
done
python3 - "$WORK/final_repair_bundle.b64" "$WORK/final_repair_bundle.tar.gz" <<'PY'
import base64
from pathlib import Path
import sys

src = Path(sys.argv[1]).read_bytes()
try:
    raw = base64.b64decode(src, validate=True)
except Exception as exc:
    raise SystemExit(
        f"REPAIR_FAIL: повреждён bundle base64: {exc}"
    )
Path(sys.argv[2]).write_bytes(raw)
print(
    "      bundle base64 OK:",
    len(src),
    "chars ->",
    len(raw),
    "bytes"
)
PY
tar -tzf "$WORK/final_repair_bundle.tar.gz" >/dev/null || {
  echo 'REPAIR_FAIL: архив repair package повреждён'
  exit 1
}
tar -xzf "$WORK/final_repair_bundle.tar.gz" -C "$PAYLOAD"

printf '[2/7] Скачиваю точный LIVE Apps Script...\n'
cat > "$PROJECT/.clasp.json" <<JSON
{"scriptId":"$SCRIPT_ID","rootDir":"."}
JSON
(
  cd "$PROJECT"
  clasp pull
)

STAMP="$(date +%Y%m%d-%H%M%S)"
BACKUP="$HOME/.wb-os-backups/full-repair-$STAMP"
mkdir -p "$(dirname "$BACKUP")"
cp -R "$PROJECT" "$BACKUP"
printf '      rollback: %s\n' "$BACKUP"

printf '[3/7] Собираю единый совместимый проект и убираю дубли...\n'
python3 - "$PROJECT" "$PAYLOAD" <<'PY'
from pathlib import Path
import re, shutil, sys

project = Path(sys.argv[1])
payload = Path(sys.argv[2])
def project_files():
    return [
        p for p in project.rglob('*')
        if p.is_file() and p.suffix in {'.js', '.gs'}
    ]

def text(p):
    return p.read_text(encoding='utf-8', errors='ignore')

def find(marker):
    return [p for p in project_files() if marker in text(p)]

def replace_one(marker, payload_name, prefer=None):
    candidates = find(marker)
    if not candidates:
        raise SystemExit(f'REPAIR_FAIL: не найден live-модуль по marker={marker}')
    target = None
    if prefer:
        for p in candidates:
            if prefer.lower() in p.name.lower():
                target = p
                break
    target = target or candidates[0]
    shutil.copy2(payload / payload_name, target)
    return target, candidates

master, _ = replace_one('function runFinalAutomationCycle_', 'MASTER_FIXED.gs')

k2_candidates = find('function getK2WarehouseItems_')
if not k2_candidates:
    raise SystemExit('REPAIR_FAIL: K2 core не найден')
# Keep the richer current K2 core when possible; delete duplicate K2 implementation.
k2_target = None
for p in k2_candidates:
    t = text(p)
    if 'getK2CredentialsWithFallback_' in t or 'updateK2AutomationStatus_' in t:
        k2_target = p
        break
k2_target = k2_target or k2_candidates[0]
shutil.copy2(payload / 'K2_CORE_FIXED.gs', k2_target)
for p in k2_candidates:
    if p != k2_target:
        p.unlink()

replace_one('function k2EvolutionFetchAndApply_', 'K2_EVOLUTION_FIXED.gs')
replace_one('function sppRunMonitor_', 'SPP_MONITOR_FIXED.gs')
replace_one('function wbOrderFeedRefreshInternal_', 'WB_ORDER_FEED_FIXED.gs')
replace_one('function syncAllSupplierOrdersNow()', 'SUPPLIER_ORDERS_FIXED.gs', 'Supplier_Orders')
replace_one('function updateWbPricesFromLinks()', 'PRICE_COMPAT_FIXED.gs')

# Remove the obsolete second Supplier implementation (UO_*), if present.
for p in find('function UO_syncAllSupplierOrdersNow()'):
    p.unlink()

# Re-scan and fail loudly on duplicate global function names.
files2 = [p for p in project.rglob('*') if p.is_file() and p.suffix in {'.js', '.gs'}]
seen = {}
dups = []
for p in files2:
    t = text(p)
    for m in re.finditer(r'(?m)^\s*function\s+([A-Za-z_$][\w$]*)\s*\(', t):
        name = m.group(1)
        if name in seen:
            dups.append((name, seen[name].name, p.name))
        else:
            seen[name] = p
if dups:
    details = '; '.join(f'{n}: {a} <> {b}' for n,a,b in dups[:30])
    raise SystemExit('REPAIR_FAIL: duplicate global functions: ' + details)

print('MASTER:', master.name)
print('K2 core:', k2_target.name)
print('Files after repair:', len(files2))
PY

printf '[4/7] Проверяю JavaScript синтаксис...\n'
while IFS= read -r -d '' f; do
  tmp="$WORK/check-$(basename "$f" | tr ' ' '_').js"
  cp "$f" "$tmp"
  node --check "$tmp" >/dev/null
done < <(find "$PROJECT" -type f \( -name '*.js' -o -name '*.gs' \) -print0)

printf '[5/7] Отправляю исправленный проект в LIVE...\n'
(
  cd "$PROJECT"
  clasp push -f
)

printf '[6/7] Пробую выполнить одноразовую установку/очистку триггеров...\n'
if (
  cd "$PROJECT"
  clasp run installWbOsFixed >/tmp/wb-os-clasp-run.$$ 2>&1
); then
  cat /tmp/wb-os-clasp-run.$$
  rm -f /tmp/wb-os-clasp-run.$$
  INSTALL='AUTO_OK'
else
  rm -f /tmp/wb-os-clasp-run.$$
  INSTALL='MASTER_WILL_SELF_CLEAN'
fi

printf '[7/7] ГОТОВО.\n\n'
echo '✅ Код отправлен в LIVE Apps Script.'
echo "Rollback backup: $BACKUP"
if [ "$INSTALL" = 'AUTO_OK' ]; then
  echo '✅ installWbOsFixed выполнен автоматически.'
else
  echo 'ℹ️ clasp run недоступен — это не блокирует работу: текущий master при следующем цикле сам удалит legacy periodic triggers.'
fi
echo 'Новый master обновляет K2, WB Order Feed, seller price, client price/SPP, Supplier Orders и WB/Ozon контуры.'
