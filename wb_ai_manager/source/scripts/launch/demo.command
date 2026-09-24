#!/bin/bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"
if [ ! -x .venv/bin/python ]; then
  ./scripts/bootstrap_macos.sh
fi
[ -f .env ] || cp .env.example .env
PY="$ROOT/.venv/bin/python"
"$PY" - <<'PY'
from pathlib import Path
import re
p=Path('.env'); s=p.read_text(encoding='utf-8')
vals={
 'WB_MODE':'demo','LLM_PROVIDER':'rules','OPERATION_MODE':'shadow',
 'AUTO_ACTIONS':'false','FORCE_READ_ONLY':'true','ENABLE_PUBLIC_WB_SEARCH':'false','DATA_DIR':'./data/demo','APP_HOST':'127.0.0.1','APP_PORT':'8787'
}
for k,v in vals.items():
    if re.search(rf'^{re.escape(k)}=.*$', s, flags=re.M):
        s=re.sub(rf'^{re.escape(k)}=.*$', f'{k}={v}', s, flags=re.M)
    else: s += f'\n{k}={v}\n'
p.write_text(s,encoding='utf-8'); p.chmod(0o600)
PY
source .venv/bin/activate
echo "=== 1/2: однократный DEMO-прогон 19 агентов ==="
wb-control demo
echo
echo "=== 2/2: запускаю DEMO dashboard ==="
(sleep 2; open http://127.0.0.1:8787/dashboard) >/dev/null 2>&1 &
echo "Откроется http://127.0.0.1:8787/dashboard"
echo "Остановить: Ctrl+C в этом окне. Реальный WB не используется."
exec wb-control run
