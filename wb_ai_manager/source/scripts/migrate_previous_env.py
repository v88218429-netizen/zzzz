#!/usr/bin/env python3
from pathlib import Path
root=Path(__file__).resolve().parents[1]; target=root/'.env'
if target.exists(): raise SystemExit(0)
candidates=[]
for base in [root.parent,Path.home()/'Downloads',Path.home()/'Desktop']:
    if not base.exists(): continue
    for env in base.glob('**/.env'):
        if env==target: continue
        if 'wb-ai' in str(env).lower() or 'WB_AI' in str(env): candidates.append(env)
for src in sorted(candidates,key=lambda x:x.stat().st_mtime,reverse=True):
    txt=src.read_text(errors='ignore')
    if 'WB_API_TOKEN=' in txt or 'GOOGLE_SHEETS_BRIDGE_URL=' in txt:
        target.write_text(txt, encoding='utf-8'); target.chmod(0o600); print('Migrated settings from',src); break
