#!/bin/bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"
[ -x .venv/bin/python ] || ./scripts/bootstrap_macos.sh
PY="$ROOT/.venv/bin/python"
[ -f .env ] || cp .env.example .env

clear
printf '\n=== WB AI Manager · Подключения ===\n\n'
printf 'Все секреты сохраняются только локально в .env (chmod 600).\n'
printf 'Изменения в WB в этой сборке физически заблокированы.\n\n'

read_current() {
  "$PY" - "$1" <<'PY'
from pathlib import Path
import re,sys
s=Path('.env').read_text(encoding='utf-8')
k=sys.argv[1]
m=re.search(rf'^{re.escape(k)}=(.*)$',s,re.M)
print((m.group(1) if m else '').strip())
PY
}
write_value() {
  "$PY" - "$1" "$2" <<'PY'
from pathlib import Path
import re,sys
p=Path('.env'); s=p.read_text(encoding='utf-8'); k=sys.argv[1]; v=sys.argv[2]
if re.search(rf'^{re.escape(k)}=.*$',s,re.M): s=re.sub(rf'^{re.escape(k)}=.*$', lambda _m: f'{k}={v}', s, flags=re.M)
else: s += f'\n{k}={v}\n'
p.write_text(s,encoding='utf-8')
PY
}

WB=$(read_current WB_API_TOKEN)
if [ -n "$WB" ]; then echo 'WB API:             ✅ token уже сохранён'; else echo 'WB API:             ○ token не задан'; fi
GSURL=$(read_current GOOGLE_SHEETS_BRIDGE_URL)
GSKEY=$(read_current GOOGLE_SHEETS_BRIDGE_KEY)
if [ -n "$GSURL" ] && [ -n "$GSKEY" ]; then echo 'Google Sheets:      ✅ bridge уже сохранён'; else echo 'Google Sheets:      ○ bridge не настроен'; fi

echo
if [ -z "$WB" ]; then
  printf 'WB token ещё не задан. Вставь READ-ONLY WB API token (ввод скрыт, Enter = пропустить): '
  IFS= read -r -s VALUE; printf '\n'
  [ -n "$VALUE" ] && write_value WB_API_TOKEN "$VALUE"
else
  printf 'Обновить WB token? [y/N]: '
  IFS= read -r ANS
  if [[ "$ANS" =~ ^[YyДд]$ ]]; then
    printf 'Вставь READ-ONLY WB API token (ввод скрыт): '
    IFS= read -r -s VALUE; printf '\n'
    [ -n "$VALUE" ] && write_value WB_API_TOKEN "$VALUE"
  fi
fi

echo
printf 'Настроить Google Sheets bridge? [y/N]: '
IFS= read -r ANS
if [[ "$ANS" =~ ^[YyДд]$ ]]; then
  echo '1) Открой google_apps_script/WB_AI_SHEETS_BRIDGE.gs'
  echo '2) Вставь код в Google Apps Script и Deploy → Web app'
  echo '3) В ACCESS_KEY в скрипте поставь длинный секрет'
  echo
  printf 'Вставь Web App URL (/exec): '
  IFS= read -r URL
  printf 'Вставь тот же ACCESS_KEY (ввод скрыт): '
  IFS= read -r -s KEY; printf '\n'
  [ -n "$URL" ] && write_value GOOGLE_SHEETS_BRIDGE_URL "$URL"
  [ -n "$KEY" ] && write_value GOOGLE_SHEETS_BRIDGE_KEY "$KEY"
fi

write_value FORCE_READ_ONLY true
write_value OPERATION_MODE shadow
write_value AUTO_ACTIONS false
chmod 600 .env

echo
echo '✅ Подключения сохранены локально. Запусти WB AI Manager и открой раздел «Подключения».'
printf 'Нажми Enter…'; IFS= read -r _
