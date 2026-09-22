#!/bin/bash
set -euo pipefail

SCRIPT_URL="${1:-}"
SOURCE_REF="${2:-}"

if [ -z "$SCRIPT_URL" ]; then
  echo "❌ Передай URL Apps Script первым аргументом."
  exit 2
fi

if [ "${#SOURCE_REF}" -ne 40 ] || ! printf '%s' "$SOURCE_REF" | grep -Eq '^[0-9a-fA-F]+$'; then
  echo "❌ Вторым аргументом нужен точный 40-символьный commit SHA."
  echo "   Deploy с moving branch (например mainggg) запрещён."
  exit 2
fi

if ! command -v clasp >/dev/null 2>&1; then
  echo "❌ clasp не найден. Этот deploy ничего устанавливать автоматически не будет."
  exit 2
fi

for cmd in python3 node curl rsync; do
  if ! command -v "$cmd" >/dev/null 2>&1; then
    echo "❌ Не найден обязательный инструмент: $cmd"
    exit 2
  fi
done

REPO_RAW="https://raw.githubusercontent.com/v88218429-netizen/zzzz/$SOURCE_REF"
WORK="$(mktemp -d "${TMPDIR:-/tmp}/wb-os-core-repair.XXXXXX")"
PROJECT="$WORK/live-project"
VERIFY="$WORK/remote-verify"
VERIFY_CHECK="$WORK/remote-verify-check"
BACKUPS="$HOME/.wb-os-backups"
mkdir -p "$PROJECT" "$VERIFY" "$BACKUPS"
trap 'rm -rf "$WORK"' EXIT

echo
echo "WB OS · CORE REPAIR"
echo "==================="
echo "Source commit: $SOURCE_REF"
echo

SCRIPT_ID="$(python3 - "$SCRIPT_URL" <<'PY'
import re
import sys

value = sys.argv[1].strip()
match = re.search(r"/projects/([^/?#]+)", value)
print(match.group(1) if match else value)
PY
)"

if [ -z "$SCRIPT_ID" ]; then
  echo "❌ Не удалось определить Script ID."
  exit 2
fi

write_clasp_config_() {
  local dir="$1"
  cat > "$dir/.clasp.json" <<EOF
{"scriptId":"$SCRIPT_ID","rootDir":"."}
EOF
}

count_marker_() {
  local dir="$1"
  local marker="$2"
  python3 - "$dir" "$marker" <<'PY'
from pathlib import Path
import sys

root = Path(sys.argv[1])
marker = sys.argv[2]
count = 0
for p in root.rglob("*"):
    if p.is_file() and p.suffix in {".js", ".gs"}:
        if marker in p.read_text(encoding="utf-8", errors="ignore"):
            count += 1
print(count)
PY
}

find_marker_file_() {
  local dir="$1"
  local marker="$2"
  python3 - "$dir" "$marker" <<'PY'
from pathlib import Path
import sys

root = Path(sys.argv[1])
marker = sys.argv[2]
hits = []
for p in root.rglob("*"):
    if p.is_file() and p.suffix in {".js", ".gs"}:
        if marker in p.read_text(encoding="utf-8", errors="ignore"):
            hits.append(p)
if len(hits) != 1:
    raise SystemExit(2)
print(hits[0])
PY
}

replace_or_create_() {
  local dir="$1"
  local marker="$2"
  local source="$3"
  local fallback="$4"

  python3 - "$dir" "$marker" "$source" "$fallback" <<'PY'
from pathlib import Path
import shutil
import sys

root = Path(sys.argv[1])
marker = sys.argv[2]
source = Path(sys.argv[3])
fallback = sys.argv[4]

hits = []
for p in root.rglob("*"):
    if p.is_file() and p.suffix in {".js", ".gs"}:
        if marker in p.read_text(encoding="utf-8", errors="ignore"):
            hits.append(p)

if len(hits) > 1:
    raise SystemExit(
        "LIVE safety: marker found in more than one file: "
        + marker + " -> " + repr([str(x) for x in hits])
    )

target = hits[0] if hits else root / fallback
shutil.copy2(source, target)
print(target.name)
PY
}

project_fingerprint_() {
  local dir="$1"
  python3 - "$dir" <<'PY'
from pathlib import Path
import hashlib
import sys

root = Path(sys.argv[1])
h = hashlib.sha256()

files = sorted(
    p for p in root.rglob("*")
    if p.is_file() and p.suffix in {".js", ".gs"}
)

for p in files:
    rel = p.relative_to(root).as_posix().encode("utf-8")
    h.update(len(rel).to_bytes(4, "big"))
    h.update(rel)
    data = p.read_bytes()
    h.update(len(data).to_bytes(8, "big"))
    h.update(data)

print(h.hexdigest())
PY
}

project_semantic_fingerprint_() {
  local dir="$1"

  python3 - "$dir" <<'PY'
from pathlib import Path
import hashlib
import json
import sys

root = Path(sys.argv[1])
entries = []

# Apps Script source identity is the project file name, not whether clasp
# happened to represent server-side source as .gs or .js locally.
by_key = {}
for p in root.rglob("*"):
    if not p.is_file():
        continue

    suffix = p.suffix.lower()
    rel = p.relative_to(root)

    if suffix in {".js", ".gs"}:
        key = rel.with_suffix("").as_posix()
        kind = "server"
        data = p.read_text(
            encoding="utf-8",
            errors="strict",
        ).replace("\r\n", "\n").replace("\r", "\n")
    elif suffix == ".html":
        key = rel.as_posix()
        kind = "html"
        data = p.read_text(
            encoding="utf-8",
            errors="strict",
        ).replace("\r\n", "\n").replace("\r", "\n")
    elif rel.as_posix() == "appsscript.json":
        key = "appsscript.json"
        kind = "manifest"
        obj = json.loads(p.read_text(encoding="utf-8"))
        data = json.dumps(
            obj,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    else:
        continue

    compound = kind + ":" + key
    if compound in by_key:
        raise SystemExit(
            "duplicate semantic project key: " + compound
        )

    by_key[compound] = data

h = hashlib.sha256()
for key in sorted(by_key):
    kb = key.encode("utf-8")
    db = by_key[key].encode("utf-8")
    h.update(len(kb).to_bytes(4, "big"))
    h.update(kb)
    h.update(len(db).to_bytes(8, "big"))
    h.update(db)

print(h.hexdigest())
PY
}

syntax_check_project_() {
  local dir="$1"
  local count=0

  while IFS= read -r -d '' f; do
    local tmp="$WORK/node-check-$count.js"
    cp "$f" "$tmp"
    node --check "$tmp" >/dev/null
    count=$((count + 1))
  done < <(find "$dir" -type f \( -name '*.js' -o -name '*.gs' \) -print0)

  if [ "$count" -eq 0 ]; then
    echo "❌ В проекте нет Apps Script source-файлов."
    return 1
  fi

  printf '%s' "$count"
}

rollback_live_() {
  echo "⚠️ Пытаюсь автоматически вернуть rollback-бэкап..."
  rsync -a --delete "$BACKUP/" "$PROJECT/"

  if (
    cd "$PROJECT"
    clasp push -f
  ); then
    echo "✅ Rollback отправлен обратно в Apps Script."
    return 0
  fi

  echo "🚨 CRITICAL: автоматический rollback push не прошёл."
  echo "   Локальный backup сохранён: $BACKUP"
  return 1
}

echo "[1/9] Скачиваю 4 source-файла из зафиксированного commit..."
curl --connect-timeout 10 --max-time 30 -fsSL   "$REPO_RAW/wb_os/apps_script/K2_EVOLUTION_ENGINE.gs"   -o "$WORK/K2_EVOLUTION_ENGINE.gs"
curl --connect-timeout 10 --max-time 30 -fsSL   "$REPO_RAW/wb_os/apps_script/WB_PUBLIC_PRICE_V4.gs"   -o "$WORK/WB_PUBLIC_PRICE_V4.gs"
curl --connect-timeout 10 --max-time 30 -fsSL   "$REPO_RAW/wb_os/tools/patch_live_master.py"   -o "$WORK/patch_live_master.py"
curl --connect-timeout 10 --max-time 30 -fsSL   "$REPO_RAW/wb_os/tools/audit_and_repair_live.py"   -o "$WORK/audit_and_repair_live.py"

grep -q "K2 Evolution Engine v0.1.11" "$WORK/K2_EVOLUTION_ENGINE.gs"
grep -q "WB Public Customer Price Engine v0.1.11" "$WORK/WB_PUBLIC_PRICE_V4.gs"
grep -q "session-aware readiness missing inside" "$WORK/patch_live_master.py"
grep -q "duplicate top-level globals remain" "$WORK/audit_and_repair_live.py"

python3 -m py_compile   "$WORK/patch_live_master.py"   "$WORK/audit_and_repair_live.py"

for f in "$WORK/K2_EVOLUTION_ENGINE.gs" "$WORK/WB_PUBLIC_PRICE_V4.gs"; do
  cp "$f" "$WORK/payload-check.js"
  node --check "$WORK/payload-check.js" >/dev/null
done
rm -f "$WORK/payload-check.js"
echo "      payload OK"

echo "[2/9] Проверяю clasp auth..."
if [ ! -f "$HOME/.clasprc.json" ]; then
  echo "❌ Нет $HOME/.clasprc.json. Сначала нужен clasp login."
  exit 3
fi

echo "[3/9] Pull точного LIVE Apps Script..."
write_clasp_config_ "$PROJECT"
(
  cd "$PROJECT"
  clasp pull
)

MASTER_COUNT="$(count_marker_ "$PROJECT" "function runFinalAutomationCycle_")"
K2_CORE_COUNT="$(count_marker_ "$PROJECT" "function getK2WarehouseItems_")"

if [ "$MASTER_COUNT" != "1" ]; then
  echo "❌ LIVE safety: master-файлов $MASTER_COUNT вместо 1. Push запрещён."
  exit 4
fi

if [ "$K2_CORE_COUNT" -lt 1 ]; then
  echo "❌ LIVE safety: K2 core не найден. Push запрещён."
  exit 4
fi

echo "[4/9] Делаю rollback backup..."
TS="$(date +%Y%m%d-%H%M%S)"
BACKUP="$BACKUPS/core-repair-$TS"
mkdir -p "$BACKUP"
rsync -a "$PROJECT/" "$BACKUP/"
echo "      $BACKUP"

echo "[5/9] Патчу master/K2/price и проверяю единый global namespace..."
TARGET_K2_EV="$(replace_or_create_   "$PROJECT"   "function k2EvolutionFetchAndApply_"   "$WORK/K2_EVOLUTION_ENGINE.gs"   "K2_EVOLUTION_ENGINE.js")"
echo "      K2 Evolution: $TARGET_K2_EV"

TARGET_PRICE="$(replace_or_create_   "$PROJECT"   "function syncWbPublicCustomerPricesV4_"   "$WORK/WB_PUBLIC_PRICE_V4.gs"   "WB_PUBLIC_PRICE_V4.js")"
echo "      WB Public Price: $TARGET_PRICE"

python3 "$WORK/patch_live_master.py" "$PROJECT"
python3 "$WORK/audit_and_repair_live.py" "$PROJECT"

echo "[6/9] Проверяю полностью собранный проект ДО push..."
SOURCE_COUNT="$(syntax_check_project_ "$PROJECT")"

for marker in   "function runFinalAutomationCycle_"   "function getK2WarehouseItems_"   "function k2EvolutionFetchAndApply_"   "function syncWbPublicCustomerPricesV4_"
do
  COUNT="$(count_marker_ "$PROJECT" "$marker")"
  if [ "$COUNT" != "1" ]; then
    echo "❌ Critical marker count=$COUNT: $marker"
    exit 5
  fi
done

MASTER_FILE="$(find_marker_file_ "$PROJECT" "function runFinalAutomationCycle_")"

if grep -q "getMasterConfigurationState_().k2Ready" "$MASTER_FILE"; then
  echo "❌ legacy K2 credentials guard остался в master."
  exit 5
fi

# Idempotence is a hard requirement: running repair a second time on the
# already-repaired project must not change a byte.
BEFORE_IDEMPOTENCE="$(project_fingerprint_ "$PROJECT")"
python3 "$WORK/patch_live_master.py" "$PROJECT" >/dev/null
python3 "$WORK/audit_and_repair_live.py" "$PROJECT" >/dev/null
AFTER_IDEMPOTENCE="$(project_fingerprint_ "$PROJECT")"

if [ "$BEFORE_IDEMPOTENCE" != "$AFTER_IDEMPOTENCE" ]; then
  echo "❌ Repair неидемпотентен: второй проход снова изменил проект."
  exit 5
fi

LOCAL_PROJECT_FINGERPRINT="$(project_semantic_fingerprint_ "$PROJECT")"

echo "      syntax OK: $SOURCE_COUNT source files"
echo "      critical functions: exactly 1 each"
echo "      repair second pass: no-op"
echo "      full project fingerprint captured"

echo "[7/9] Push в LIVE..."
if ! (
  cd "$PROJECT"
  clasp push -f
); then
  echo "❌ clasp push завершился ошибкой."
  rollback_live_ || true
  exit 6
fi

echo "[8/9] Pull-back: проверяю то, что реально сохранилось в LIVE..."
write_clasp_config_ "$VERIFY"

if ! (
  cd "$VERIFY"
  clasp pull
); then
  echo "⚠️ Push прошёл, но повторный pull для remote-verify не удался."
  echo "   При сетевой ошибке проверки rollback автоматически не запускается."
  echo "   Backup: $BACKUP"
  exit 7
fi

rsync -a "$VERIFY/" "$VERIFY_CHECK/"
REMOTE_PROJECT_FINGERPRINT="$(project_semantic_fingerprint_ "$VERIFY")"
BEFORE_VERIFY="$(project_fingerprint_ "$VERIFY_CHECK")"
VERIFY_OK=1

if [ "$REMOTE_PROJECT_FINGERPRINT" != "$LOCAL_PROJECT_FINGERPRINT" ]; then
  echo "❌ Pull-back отличается от полного проекта, который был отправлен."
  echo "   Это может означать потерю/изменение НЕцелевого модуля."
  VERIFY_OK=0
fi

python3 "$WORK/patch_live_master.py" "$VERIFY_CHECK" >/dev/null || VERIFY_OK=0
python3 "$WORK/audit_and_repair_live.py" "$VERIFY_CHECK" >/dev/null || VERIFY_OK=0

AFTER_VERIFY="$(project_fingerprint_ "$VERIFY_CHECK")"
if [ "$BEFORE_VERIFY" != "$AFTER_VERIFY" ]; then
  echo "❌ LIVE после push всё ещё требовал дополнительных изменений."
  VERIFY_OK=0
fi

if ! syntax_check_project_ "$VERIFY" >/dev/null; then
  VERIFY_OK=0
fi

for marker in   "function runFinalAutomationCycle_"   "function getK2WarehouseItems_"   "function k2EvolutionFetchAndApply_"   "function syncWbPublicCustomerPricesV4_"
do
  COUNT="$(count_marker_ "$VERIFY" "$marker")"
  if [ "$COUNT" != "1" ]; then
    echo "❌ Remote critical marker count=$COUNT: $marker"
    VERIFY_OK=0
  fi
done

if ! grep -Rqs "K2 Evolution Engine v0.1.11" "$VERIFY"; then
  echo "❌ Remote K2 Evolution version mismatch."
  VERIFY_OK=0
fi

if ! grep -Rqs "WB Public Customer Price Engine v0.1.11" "$VERIFY"; then
  echo "❌ Remote WB Public Price version mismatch."
  VERIFY_OK=0
fi

if [ "$VERIFY_OK" != "1" ]; then
  echo "❌ Remote post-verify не прошёл. Откатываю LIVE."
  rollback_live_ || true
  exit 8
fi

echo "      remote project: verified"
echo "      full project pull-back: exact semantic match"
echo "      patcher/auditor second pass: no-op"

echo "[9/9] Мягкая попытка запустить master сейчас..."
RUN_LOG="$WORK/clasp-run.log"

if (
  cd "$PROJECT"
  clasp run setupFinalAutomation
) >"$RUN_LOG" 2>&1; then
  cat "$RUN_LOG"
  echo "      setupFinalAutomation: OK"
else
  echo "      clasp run недоступен или Execution API не настроен."
  echo "      Это не отменяет deploy: push + pull-back verify уже прошли."
  echo "      Существующий finalAutomationTick запустит master."
  echo "      Если master-trigger отсутствует, активный legacy K2/SPP trigger теперь восстановит его автоматически."
fi

echo
echo "✅ CORE REPAIR DEPLOYED + REMOTE VERIFIED"
echo "Rollback backup: $BACKUP"
