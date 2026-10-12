#!/bin/bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"

if [ ! -x .venv/bin/python ]; then
  ./scripts/bootstrap_macos.sh
fi
[ -f .env ] || cp .env.example .env
chmod 600 .env
PY="$ROOT/.venv/bin/python"

HAS_WB="$("$PY" - <<'PY'
from pathlib import Path
for row in Path('.env').read_text(encoding='utf-8').splitlines():
    if row.startswith('WB_API_TOKEN='):
        print('yes' if row.split('=',1)[1].strip() else 'no')
        break
else:
    print('no')
PY
)"
if [ "$HAS_WB" != "yes" ]; then
  ./scripts/launch/configure_connections.command
fi

"$PY" - <<'PY'
from pathlib import Path
p=Path('.env'); d={}
for row in p.read_text(encoding='utf-8').splitlines():
    if '=' in row and not row.lstrip().startswith('#'):
        k,v=row.split('=',1); d[k]=v
if not d.get('WB_API_TOKEN','').strip():
    raise SystemExit('WB_API_TOKEN не задан: фоновая служба не установлена.')
d.update({
    'WB_MODE':'live','APP_HOST':'127.0.0.1','APP_PORT':'8787',
    'DATA_DIR':str((Path.cwd()/'data').resolve()),
    'FORCE_READ_ONLY':'true','OPERATION_MODE':'shadow','AUTO_ACTIONS':'false',
})
d.setdefault('LLM_PROVIDER','rules')
p.write_text('\n'.join(f'{k}={v}' for k,v in d.items())+'\n',encoding='utf-8')
p.chmod(0o600)
Path('data').mkdir(exist_ok=True)
PY

"$PY" - <<'PY'
from pathlib import Path
import os, plistlib
root=Path.cwd()
plist=Path.home()/'Library/LaunchAgents/com.wb.ai-control-center.plist'
plist.parent.mkdir(parents=True,exist_ok=True)
spec={
  'Label':'com.wb.ai-control-center',
  'ProgramArguments':[
    str(root/'.venv/bin/python'),'-m','uvicorn','--loop','asyncio',
    'wb_control_center.api:app','--host','127.0.0.1','--port','8787',
  ],
  'WorkingDirectory':str(root),
  'EnvironmentVariables':{'PYTHONPATH':str(root/'src')},
  'RunAtLoad':True,'KeepAlive':True,
  'StandardOutPath':str(root/'data/launchd.out.log'),
  'StandardErrorPath':str(root/'data/launchd.err.log'),
}
plist.write_bytes(plistlib.dumps(spec))
plist.chmod(0o600)
print(plist)
PY

UID_NOW="$(id -u)"
PLIST="$HOME/Library/LaunchAgents/com.wb.ai-control-center.plist"
launchctl bootout "gui/$UID_NOW" "$PLIST" 2>/dev/null || true
launchctl bootstrap "gui/$UID_NOW" "$PLIST"
launchctl kickstart -k "gui/$UID_NOW/com.wb.ai-control-center"

"$PY" - <<'PY'
import json,time,urllib.request
url='http://127.0.0.1:8787/health'
for _ in range(60):
    try:
        with urllib.request.urlopen(url,timeout=2) as response:
            data=json.loads(response.read().decode('utf-8'))
        if data.get('status')=='ok':
            connected=bool(data.get('wb_connected'))
            print(f"Служба отвечает; подключение WB: {'есть' if connected else 'нет'}.")
            print('Панель: http://127.0.0.1:8787/dashboard')
            if not connected:
                raise SystemExit('Служба работает, но вход в WB не подтверждён. Смотри data/launchd.err.log')
            break
    except Exception:
        time.sleep(1)
else:
    raise SystemExit('Служба не ответила за 60 секунд. Смотри data/launchd.err.log')
PY
