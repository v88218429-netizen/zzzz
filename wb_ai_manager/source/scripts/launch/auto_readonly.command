#!/bin/bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"; cd "$ROOT"
[ -x .venv/bin/python ] || ./scripts/bootstrap_macos.sh
PY="$ROOT/.venv/bin/python"
PYTHONPATH="$ROOT/src" "$PY" scripts/migrate_previous_env.py || true
[ -f .env ] || cp .env.example .env
HAS_WB_TOKEN=$("$PY" - <<'PYTOKEN'
from pathlib import Path
for line in Path('.env').read_text(encoding='utf-8').splitlines():
    if line.startswith('WB_API_TOKEN='):
        print('yes' if line.split('=',1)[1].strip() else 'no')
        break
else:
    print('no')
PYTOKEN
)
if [ "$HAS_WB_TOKEN" != "yes" ]; then
  echo 'WB token не задан. Открываю одноразовую настройку подключений…'
  ./scripts/launch/configure_connections.command
fi
"$PY" - <<'PYENV'
from pathlib import Path
p=Path('.env'); d={}
for line in p.read_text().splitlines():
    if '=' in line and not line.lstrip().startswith('#'): d[line.split('=',1)[0]]=line.split('=',1)[1]
d.update(WB_MODE='live',LLM_PROVIDER='rules',OPERATION_MODE='shadow',FORCE_READ_ONLY='true',AUTO_ACTIONS='false',ENABLE_PUBLIC_WB_SEARCH='true',DATA_DIR='./data',APP_HOST='127.0.0.1',APP_PORT='8787')
p.write_text('\n'.join(f'{k}={v}' for k,v in d.items())+'\n', encoding='utf-8'); p.chmod(0o600)
PYENV
echo 'Автоагент источников: обновляю 27 / Юнитку / сводки через текущую Google-сессию…'
PYTHONPATH="$ROOT/src" "$PY" scripts/auto_refresh_sheets.py || true
export PYTHONPATH="$ROOT/src"
echo 'Надсмотрщик включён: агент сам перезапускается после сбоя и проверяет обновления раз в минуту.'
exec "$PY" scripts/runtime_supervisor.py
