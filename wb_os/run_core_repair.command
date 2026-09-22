#!/bin/bash
# CI trigger: direct-file repair; archive deploy is quarantined.
set -euo pipefail

SOURCE_REF="${2:-}"
if ! printf '%s' "$SOURCE_REF" | grep -Eq '^[0-9a-fA-F]{40}
WORK="$(mktemp -d "${TMPDIR:-/tmp}/wb-os-core-repair.XXXXXX")"
BACKUPS="$HOME/.wb-os-backups"
mkdir -p "$BACKUPS"
trap 'rm -rf "$WORK"' EXIT

echo "WB OS · CORE REPAIR"
echo "==================="
echo "Source ref: $SOURCE_REF"

if command -v clasp >/dev/null 2>&1; then
  run_clasp() { clasp "$@"; }
elif command -v npx >/dev/null 2>&1; then
  run_clasp() { npx --yes @google/clasp "$@"; }
else
  echo "❌ Не найден clasp или npx."
  exit 2
fi

echo "[1/9] Скачиваю 4 прямых source-файла без архивов..."
curl --connect-timeout 10 --max-time 30 -fsSL   "$REPO_RAW/wb_os/apps_script/K2_EVOLUTION_ENGINE.gs"   -o "$WORK/K2_EVOLUTION_ENGINE.gs"
curl --connect-timeout 10 --max-time 30 -fsSL   "$REPO_RAW/wb_os/apps_script/WB_PUBLIC_PRICE_V4.gs"   -o "$WORK/WB_PUBLIC_PRICE_V4.gs"
curl --connect-timeout 10 --max-time 30 -fsSL   "$REPO_RAW/wb_os/tools/patch_live_master.py"   -o "$WORK/patch_live_master.py"
curl --connect-timeout 10 --max-time 30 -fsSL   "$REPO_RAW/wb_os/tools/audit_and_repair_live.py"   -o "$WORK/audit_and_repair_live.py"

grep -q "K2 Evolution Engine v0.1.7" "$WORK/K2_EVOLUTION_ENGINE.gs" || {
  echo "❌ Неверная версия K2 payload"; exit 3;
}
grep -q "WB Public Customer Price Engine v0.1.5" "$WORK/WB_PUBLIC_PRICE_V4.gs" || {
  echo "❌ Неверная версия WB Price payload"; exit 3;
}
grep -q "syncWbPublicCustomerPricesV4_(forceAll);" "$WORK/patch_live_master.py" || {
  echo "❌ Patcher без WB Price migration"; exit 3;
}
python3 -m py_compile "$WORK/patch_live_master.py" "$WORK/audit_and_repair_live.py"
for f in "$WORK/K2_EVOLUTION_ENGINE.gs" "$WORK/WB_PUBLIC_PRICE_V4.gs"; do
  cp "$f" "$WORK/check.js"
  node --check "$WORK/check.js"
done
rm -f "$WORK/check.js"
echo "      payload OK"

SCRIPT_URL="${1:-}"
if [ -z "$SCRIPT_URL" ]; then
  echo "❌ Передай URL Apps Script первым аргументом."
  exit 4
fi
SCRIPT_ID="$(printf '%s' "$SCRIPT_URL" | sed -nE 's#.*script\.google\.com/[^ ]*/projects/([^/ ?#]+).*#\1#p')"
if [ -z "$SCRIPT_ID" ]; then
  SCRIPT_ID="$(printf '%s' "$SCRIPT_URL" | tr -d '[:space:]')"
fi
if [ -z "$SCRIPT_ID" ]; then
  echo "❌ Не удалось определить Script ID."
  exit 4
fi

PROJECT="$WORK/live-project"
mkdir -p "$PROJECT"
cat > "$PROJECT/.clasp.json" <<EOF
{"scriptId":"$SCRIPT_ID","rootDir":"."}
EOF

echo "[2/9] Проверяю clasp auth..."
if [ ! -f "$HOME/.clasprc.json" ]; then
  run_clasp login
fi

echo "[3/9] Pull точного LIVE проекта..."
(
  cd "$PROJECT"
  run_clasp pull
)

MASTER_COUNT="$(grep -RIl "function runFinalAutomationCycle_" "$PROJECT" --include='*.js' --include='*.gs' 2>/dev/null | wc -l | tr -d ' ')"
if [ "$MASTER_COUNT" != "1" ]; then
  echo "❌ LIVE safety: master-файлов $MASTER_COUNT вместо 1. Push запрещён."
  exit 5
fi
if ! grep -Rq "function getK2WarehouseItems_" "$PROJECT" --include='*.js' --include='*.gs'; then
  echo "❌ LIVE safety: K2 core не найден. Push запрещён."
  exit 5
fi

echo "[4/9] Делаю rollback backup..."
TS="$(date +%Y%m%d-%H%M%S)"
BACKUP="$BACKUPS/core-repair-$TS"
mkdir -p "$BACKUP"
rsync -a "$PROJECT/" "$BACKUP/"
echo "      $BACKUP"

echo "[5/9] Патчу master, K2/price и устраняю конфликты global namespace..."
replace_or_create() {
  local marker="$1"
  local source="$2"
  local fallback="$3"
  local matches
  local count

  matches="$(grep -RIl "$marker" "$PROJECT" --include='*.js' --include='*.gs' 2>/dev/null || true)"
  if [ -n "$matches" ]; then
    count="$(printf '%s\n' "$matches" | sed '/^$/d' | wc -l | tr -d ' ')"
  else
    count=0
  fi

  if [ "$count" -gt 1 ]; then
    echo "❌ LIVE safety: marker '$marker' найден в $count файлах. Push запрещён."
    printf '%s\n' "$matches"
    exit 6
  fi

  if [ "$count" = "1" ]; then
    cp "$source" "$matches"
    echo "      replaced: $(basename "$matches")"
  else
    cp "$source" "$PROJECT/$fallback"
    echo "      created: $fallback"
  fi
}

replace_or_create   "function k2EvolutionFetchAndApply_"   "$WORK/K2_EVOLUTION_ENGINE.gs"   "K2_EVOLUTION_ENGINE.js"

replace_or_create   "function syncWbPublicCustomerPricesV4_"   "$WORK/WB_PUBLIC_PRICE_V4.gs"   "WB_PUBLIC_PRICE_V4.js"

python3 "$WORK/patch_live_master.py" "$PROJECT"
python3 "$WORK/audit_and_repair_live.py" "$PROJECT"

echo "[6/9] Проверяю весь собранный LIVE-проект..."
COUNT=0
while IFS= read -r -d '' f; do
  TMP_CHECK="$WORK/check-$COUNT.js"
  cp "$f" "$TMP_CHECK"
  node --check "$TMP_CHECK"
  COUNT=$((COUNT + 1))
done < <(find "$PROJECT" -type f \( -name '*.js' -o -name '*.gs' \) -print0)

critical_count() {
  local marker="$1"
  grep -RIl "$marker" "$PROJECT" --include='*.js' --include='*.gs' 2>/dev/null | wc -l | tr -d ' '
}

[ "$(critical_count "function runFinalAutomationCycle_")" = "1" ] || {
  echo "❌ master duplicate"; exit 7;
}
[ "$(critical_count "function k2EvolutionFetchAndApply_")" = "1" ] || {
  echo "❌ K2 Evolution duplicate"; exit 7;
}
[ "$(critical_count "function syncWbPublicCustomerPricesV4_")" = "1" ] || {
  echo "❌ WB Public Price duplicate"; exit 7;
}
[ "$(critical_count "function getK2WarehouseItems_")" = "1" ] || {
  echo "❌ K2 core duplicate"; exit 7;
}

MASTER_FILE="$(grep -RIl "function runFinalAutomationCycle_" "$PROJECT" --include='*.js' --include='*.gs')"
grep -q "k2EvolutionFetchAndApply_()" "$MASTER_FILE" || { echo "❌ master не вызывает K2 Evolution"; exit 7; }
grep -q "syncWbPublicCustomerPricesV4_(forceAll);" "$MASTER_FILE" || { echo "❌ master не вызывает WB Public Price"; exit 7; }
if grep -q "getMasterConfigurationState_().k2Ready" "$MASTER_FILE"; then
  echo "❌ legacy K2 credentials guard остался в master"; exit 7
fi
python3 "$WORK/patch_live_master.py" "$PROJECT" >/dev/null || {
  echo "❌ Повторная post-condition проверка master не прошла"
  exit 7
}

echo "      syntax OK: $COUNT files"
echo "      critical functions: exactly 1 each"
echo "      whole-project global namespace: collision-free"

rollback_live_() {
  echo "⚠️ Пытаюсь автоматически вернуть rollback-бэкап..."
  rsync -a --delete "$BACKUP/" "$PROJECT/"
  if (
    cd "$PROJECT"
    run_clasp push -f
  ); then
    echo "✅ Rollback отправлен обратно в Apps Script."
    return 0
  fi

  echo "🚨 CRITICAL: автоматический rollback push не прошёл."
  echo "   Локальный backup сохранён: $BACKUP"
  return 1
}

project_fingerprint_() {
  local dir="$1"
  (
    cd "$dir"
    find . -type f \( -name '*.js' -o -name '*.gs' \) -print0 \
      | sort -z \
      | xargs -0 shasum -a 256 \
      | shasum -a 256 \
      | awk '{print $1}'
  )
}

echo "[7/9] Push в LIVE..."
if ! (
  cd "$PROJECT"
  run_clasp push -f
); then
  echo "❌ clasp push завершился ошибкой."
  rollback_live_ || true
  exit 8
fi

echo "[8/9] Pull-back и проверка того, что реально лежит в LIVE..."
VERIFY="$WORK/remote-verify"
VERIFY_CHECK="$WORK/remote-verify-check"
mkdir -p "$VERIFY"
cat > "$VERIFY/.clasp.json" <<EOF
{"scriptId":"$SCRIPT_ID","rootDir":"."}
EOF

if ! (
  cd "$VERIFY"
  run_clasp pull
); then
  echo "⚠️ Push прошёл, но повторный pull для remote-verify не удался."
  echo "   Автоматический rollback не запускаю при сетевой/Google ошибке проверки."
  echo "   Backup: $BACKUP"
  exit 9
fi

cp -R "$VERIFY" "$VERIFY_CHECK"
BEFORE_VERIFY="$(project_fingerprint_ "$VERIFY_CHECK")"

VERIFY_OK=1
python3 "$WORK/patch_live_master.py" "$VERIFY_CHECK" >/dev/null || VERIFY_OK=0
python3 "$WORK/audit_and_repair_live.py" "$VERIFY_CHECK" >/dev/null || VERIFY_OK=0

AFTER_VERIFY="$(project_fingerprint_ "$VERIFY_CHECK")"
if [ "$BEFORE_VERIFY" != "$AFTER_VERIFY" ]; then
  echo "❌ LIVE после push всё ещё требовал бы дополнительных изменений."
  VERIFY_OK=0
fi

REMOTE_COUNT=0
while IFS= read -r -d '' f; do
  TMP_CHECK="$WORK/remote-check-$REMOTE_COUNT.js"
  cp "$f" "$TMP_CHECK"
  node --check "$TMP_CHECK" >/dev/null || VERIFY_OK=0
  REMOTE_COUNT=$((REMOTE_COUNT + 1))
done < <(find "$VERIFY" -type f \( -name '*.js' -o -name '*.gs' \) -print0)

grep -Rq "K2 Evolution Engine v0.1.7" "$VERIFY" --include='*.js' --include='*.gs' || VERIFY_OK=0
grep -Rq "WB Public Customer Price Engine v0.1.5" "$VERIFY" --include='*.js' --include='*.gs' || VERIFY_OK=0

if [ "$VERIFY_OK" != "1" ]; then
  echo "❌ Remote post-verify не прошёл. Откатываю LIVE."
  rollback_live_ || true
  exit 10
fi

echo "      remote pull-back OK: $REMOTE_COUNT source files"
echo "      remote project is already a no-op for patcher/auditor"

echo "[9/9] Мягкая попытка запустить master сейчас..."
RUN_LOG="$WORK/clasp-run.log"
if (
  cd "$PROJECT"
  run_clasp run runFinalAutomationNow
) >"$RUN_LOG" 2>&1; then
  cat "$RUN_LOG"
  echo "      master run: OK"
else
  echo "      clasp run недоступен/Execution API не настроен."
  echo "      Deploy и remote-verify уже прошли; master запустит существующий trigger."
fi

echo
echo "✅ CORE REPAIR DEPLOYED + REMOTE VERIFIED"
echo "Rollback backup: $BACKUP"
echo "Изменены: master, K2 Evolution, WB Public Price; duplicate K2 core disabled only when safe; Supplier private helpers namespaced if needed."
; then
  echo "❌ Вторым аргументом нужен точный 40-символьный commit SHA."
  echo "   Deploy с moving branch (например mainggg) запрещён."
  exit 2
fi
REPO_RAW="https://raw.githubusercontent.com/v88218429-netizen/zzzz/$SOURCE_REF"
WORK="$(mktemp -d "${TMPDIR:-/tmp}/wb-os-core-repair.XXXXXX")"
BACKUPS="$HOME/.wb-os-backups"
mkdir -p "$BACKUPS"
trap 'rm -rf "$WORK"' EXIT

echo "WB OS · CORE REPAIR"
echo "==================="
echo "Source ref: $SOURCE_REF"

if command -v clasp >/dev/null 2>&1; then
  run_clasp() { clasp "$@"; }
elif command -v npx >/dev/null 2>&1; then
  run_clasp() { npx --yes @google/clasp "$@"; }
else
  echo "❌ Не найден clasp или npx."
  exit 2
fi

echo "[1/8] Скачиваю 3 прямых source-файла без архивов..."
curl --connect-timeout 10 --max-time 30 -fsSL   "$REPO_RAW/wb_os/apps_script/K2_EVOLUTION_ENGINE.gs"   -o "$WORK/K2_EVOLUTION_ENGINE.gs"
curl --connect-timeout 10 --max-time 30 -fsSL   "$REPO_RAW/wb_os/apps_script/WB_PUBLIC_PRICE_V4.gs"   -o "$WORK/WB_PUBLIC_PRICE_V4.gs"
curl --connect-timeout 10 --max-time 30 -fsSL   "$REPO_RAW/wb_os/tools/patch_live_master.py"   -o "$WORK/patch_live_master.py"
curl --connect-timeout 10 --max-time 30 -fsSL   "$REPO_RAW/wb_os/tools/audit_and_repair_live.py"   -o "$WORK/audit_and_repair_live.py"

grep -q "K2 Evolution Engine v0.1.7" "$WORK/K2_EVOLUTION_ENGINE.gs" || {
  echo "❌ Неверная версия K2 payload"; exit 3;
}
grep -q "WB Public Customer Price Engine v0.1.4" "$WORK/WB_PUBLIC_PRICE_V4.gs" || {
  echo "❌ Неверная версия WB Price payload"; exit 3;
}
grep -q "syncWbPublicCustomerPricesV4_(forceAll);" "$WORK/patch_live_master.py" || {
  echo "❌ Patcher без WB Price migration"; exit 3;
}
python3 -m py_compile "$WORK/patch_live_master.py" "$WORK/audit_and_repair_live.py"
for f in "$WORK/K2_EVOLUTION_ENGINE.gs" "$WORK/WB_PUBLIC_PRICE_V4.gs"; do
  cp "$f" "$WORK/check.js"
  node --check "$WORK/check.js"
done
rm -f "$WORK/check.js"
echo "      payload OK"

SCRIPT_URL="${1:-}"
if [ -z "$SCRIPT_URL" ]; then
  echo "❌ Передай URL Apps Script первым аргументом."
  exit 4
fi
SCRIPT_ID="$(printf '%s' "$SCRIPT_URL" | sed -nE 's#.*script\.google\.com/[^ ]*/projects/([^/ ?#]+).*#\1#p')"
if [ -z "$SCRIPT_ID" ]; then
  SCRIPT_ID="$(printf '%s' "$SCRIPT_URL" | tr -d '[:space:]')"
fi
if [ -z "$SCRIPT_ID" ]; then
  echo "❌ Не удалось определить Script ID."
  exit 4
fi

PROJECT="$WORK/live-project"
mkdir -p "$PROJECT"
cat > "$PROJECT/.clasp.json" <<EOF
{"scriptId":"$SCRIPT_ID","rootDir":"."}
EOF

echo "[2/8] Проверяю clasp auth..."
if [ ! -f "$HOME/.clasprc.json" ]; then
  run_clasp login
fi

echo "[3/8] Pull точного LIVE проекта..."
(
  cd "$PROJECT"
  run_clasp pull
)

MASTER_COUNT="$(grep -RIl "function runFinalAutomationCycle_" "$PROJECT" --include='*.js' --include='*.gs' 2>/dev/null | wc -l | tr -d ' ')"
if [ "$MASTER_COUNT" != "1" ]; then
  echo "❌ LIVE safety: master-файлов $MASTER_COUNT вместо 1. Push запрещён."
  exit 5
fi
if ! grep -Rq "function getK2WarehouseItems_" "$PROJECT" --include='*.js' --include='*.gs'; then
  echo "❌ LIVE safety: K2 core не найден. Push запрещён."
  exit 5
fi

echo "[4/8] Делаю rollback backup..."
TS="$(date +%Y%m%d-%H%M%S)"
BACKUP="$BACKUPS/core-repair-$TS"
mkdir -p "$BACKUP"
rsync -a "$PROJECT/" "$BACKUP/"
echo "      $BACKUP"

echo "[5/8] Патчу master, K2/price и устраняю конфликты global namespace..."
replace_or_create() {
  local marker="$1"
  local source="$2"
  local fallback="$3"
  local matches
  local count

  matches="$(grep -RIl "$marker" "$PROJECT" --include='*.js' --include='*.gs' 2>/dev/null || true)"
  if [ -n "$matches" ]; then
    count="$(printf '%s\n' "$matches" | sed '/^$/d' | wc -l | tr -d ' ')"
  else
    count=0
  fi

  if [ "$count" -gt 1 ]; then
    echo "❌ LIVE safety: marker '$marker' найден в $count файлах. Push запрещён."
    printf '%s\n' "$matches"
    exit 6
  fi

  if [ "$count" = "1" ]; then
    cp "$source" "$matches"
    echo "      replaced: $(basename "$matches")"
  else
    cp "$source" "$PROJECT/$fallback"
    echo "      created: $fallback"
  fi
}

replace_or_create   "function k2EvolutionFetchAndApply_"   "$WORK/K2_EVOLUTION_ENGINE.gs"   "K2_EVOLUTION_ENGINE.js"

replace_or_create   "function syncWbPublicCustomerPricesV4_"   "$WORK/WB_PUBLIC_PRICE_V4.gs"   "WB_PUBLIC_PRICE_V4.js"

python3 "$WORK/patch_live_master.py" "$PROJECT"
python3 "$WORK/audit_and_repair_live.py" "$PROJECT"

echo "[6/8] Проверяю весь собранный LIVE-проект..."
COUNT=0
while IFS= read -r -d '' f; do
  TMP_CHECK="$WORK/check-$COUNT.js"
  cp "$f" "$TMP_CHECK"
  node --check "$TMP_CHECK"
  COUNT=$((COUNT + 1))
done < <(find "$PROJECT" -type f \( -name '*.js' -o -name '*.gs' \) -print0)

critical_count() {
  local marker="$1"
  grep -RIl "$marker" "$PROJECT" --include='*.js' --include='*.gs' 2>/dev/null | wc -l | tr -d ' '
}

[ "$(critical_count "function runFinalAutomationCycle_")" = "1" ] || {
  echo "❌ master duplicate"; exit 7;
}
[ "$(critical_count "function k2EvolutionFetchAndApply_")" = "1" ] || {
  echo "❌ K2 Evolution duplicate"; exit 7;
}
[ "$(critical_count "function syncWbPublicCustomerPricesV4_")" = "1" ] || {
  echo "❌ WB Public Price duplicate"; exit 7;
}
[ "$(critical_count "function getK2WarehouseItems_")" = "1" ] || {
  echo "❌ K2 core duplicate"; exit 7;
}

MASTER_FILE="$(grep -RIl "function runFinalAutomationCycle_" "$PROJECT" --include='*.js' --include='*.gs')"
grep -q "k2EvolutionFetchAndApply_()" "$MASTER_FILE" || { echo "❌ master не вызывает K2 Evolution"; exit 7; }
grep -q "syncWbPublicCustomerPricesV4_(forceAll);" "$MASTER_FILE" || { echo "❌ master не вызывает WB Public Price"; exit 7; }
if grep -q "getMasterConfigurationState_().k2Ready" "$MASTER_FILE"; then
  echo "❌ legacy K2 credentials guard остался в master"; exit 7
fi
grep -q "K2_SESSION_COOKIE" "$MASTER_FILE" || { echo "❌ session-aware K2 readiness не установлена"; exit 7; }

echo "      syntax OK: $COUNT files"
echo "      critical functions: exactly 1 each"
echo "      whole-project global namespace: collision-free"

echo "[7/8] Push в LIVE..."
(
  cd "$PROJECT"
  run_clasp push -f
)

echo "[8/8] Мягкая попытка запустить master сейчас..."
if (
  cd "$PROJECT"
  run_clasp run runFinalAutomationNow
) >/tmp/wb-os-core-run.$$ 2>&1; then
  cat /tmp/wb-os-core-run.$$
  echo "      master run: OK"
else
  echo "      clasp run недоступен; это не ошибка deploy."
  echo "      Сработает существующий finalAutomationTick."
fi
rm -f /tmp/wb-os-core-run.$$ || true

echo
echo "✅ CORE REPAIR DEPLOYED"
echo "Rollback backup: $BACKUP"
echo "Изменены: master, K2 Evolution, WB Public Price; duplicate K2 core disabled; Supplier private helpers namespaced if needed."
