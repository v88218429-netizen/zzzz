#!/bin/bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"
if [ ! -x .venv/bin/python ]; then
  ./scripts/bootstrap_macos.sh
fi
[ -f .env ] || cp .env.example .env
PY="$ROOT/.venv/bin/python"
EXISTING=$("$PY" - <<'PY'
from pathlib import Path
import re
s=Path('.env').read_text(encoding='utf-8')
m=re.search(r'^WB_API_TOKEN=(.*)$',s,re.M)
print((m.group(1) if m else '').strip())
PY
)
TOKEN="$EXISTING"
if [ -z "$TOKEN" ]; then
  printf "Вставь WB API token (ввод скрыт): "
  IFS= read -r -s TOKEN
  printf "\n"
fi
if [ -z "$TOKEN" ]; then
  echo "WB token пустой. Ничего не запускаю."
  exit 1
fi
"$PY" - "$TOKEN" <<'PY'
from pathlib import Path
import re,sys
p=Path('.env'); s=p.read_text(encoding='utf-8')
vals={
 'WB_MODE':'live','WB_API_TOKEN':sys.argv[1],'LLM_PROVIDER':'rules',
 'OPERATION_MODE':'shadow','AUTO_ACTIONS':'false','FORCE_READ_ONLY':'true','ENABLE_PUBLIC_WB_SEARCH':'true','DATA_DIR':'./data',
 'APP_HOST':'127.0.0.1','APP_PORT':'8787'
}
for k,v in vals.items():
    if re.search(rf'^{re.escape(k)}=.*$',s,re.M): s=re.sub(rf'^{re.escape(k)}=.*$', lambda _m: f'{k}={v}', s, flags=re.M)
    else: s += f'\n{k}={v}\n'
p.write_text(s,encoding='utf-8')
PY
chmod 600 .env
source .venv/bin/activate
echo "=== Проверяю подключение к WB ==="
wb-control doctor
echo
echo "=== Первый полный SHADOW-аудит ==="
wb-control run-all || true
echo
echo "Режим SHADOW: реальные данные читаются, WB write-запросы не исполняются."
(sleep 2; open http://127.0.0.1:8787/dashboard) >/dev/null 2>&1 &
echo "Откроется http://127.0.0.1:8787/dashboard"
echo "API/ручной запуск: http://127.0.0.1:8787/docs"
echo "Остановить: Ctrl+C."
exec wb-control run
