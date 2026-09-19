#!/usr/bin/env python3
from __future__ import annotations
import argparse, os, re, shutil, subprocess, sys
from pathlib import Path

def ver(exe):
    try:
        s=subprocess.check_output([str(exe),"-c","import sys;print('.'.join(map(str,sys.version_info[:3])))"],text=True,stderr=subprocess.DEVNULL,timeout=8).strip()
        return tuple(map(int,s.split(".")[:3]))
    except Exception:
        return None

def ok(v): return bool(v and (3,11) <= v[:2] <= (3,13))

def candidates():
    xs=["python3.13","python3.12","python3.11","/opt/homebrew/bin/python3.13","/opt/homebrew/bin/python3.12","/opt/homebrew/bin/python3.11","/usr/local/bin/python3.13","/usr/local/bin/python3.12","/usr/local/bin/python3.11"]
    for x in xs:
        p=shutil.which(x) if not x.startswith("/") else x
        if p and Path(p).exists() and ok(ver(p)): yield Path(p)

def ensure_venv(root):
    v=root/".venv"; py=v/"bin"/"python"
    if py.exists() and ok(ver(py)): return
    if v.exists(): shutil.rmtree(v,ignore_errors=True)
    base=next(candidates(),None)
    if base is None and sys.platform=="darwin" and shutil.which("brew"):
        print("[Avito AI update] Устанавливаю Python 3.13 через Homebrew...",flush=True)
        subprocess.check_call(["brew","install","python@3.13"])
        base=next(candidates(),None)
    if base is None: raise RuntimeError("Не найден совместимый Python 3.11–3.13")
    subprocess.check_call([str(base),"-m","venv",str(v)])
    if not ok(ver(py)): raise RuntimeError("Не удалось создать совместимое окружение")

def replace(path, old, new):
    s=path.read_text(encoding="utf-8")
    if old not in s: return False
    path.write_text(s.replace(old,new),encoding="utf-8")
    return True

def main():
    ap=argparse.ArgumentParser(); ap.add_argument("--root",required=True); ap.add_argument("--target-version",required=True); a=ap.parse_args()
    root=Path(a.root).resolve()

    # Critical hotfix: prepare compatible environment BEFORE the old bootstrap continues.
    ensure_venv(root)

    # Make future launches Python 3.14-safe.
    bp=root/"bootstrap.py"
    s=bp.read_text(encoding="utf-8")
    old='''def ensure_venv() -> Path:
    venv = ROOT / ".venv"
    py = venv / "bin" / "python"
    if not py.exists():
        say("Первый запуск: создаю рабочее окружение...")
        subprocess.check_call([sys.executable, "-m", "venv", str(venv)])
    return py
'''
    new='''def _pyver(exe):
    try:
        out=subprocess.check_output([str(exe),"-c","import sys;print('.'.join(map(str,sys.version_info[:3])))"],text=True,stderr=subprocess.DEVNULL,timeout=8).strip()
        return tuple(map(int,out.split(".")[:3]))
    except Exception:
        return None

def _pyok(v):
    return bool(v and (3,11) <= v[:2] <= (3,13))

def _find_python():
    candidates=["python3.13","python3.12","python3.11","/opt/homebrew/bin/python3.13","/opt/homebrew/bin/python3.12","/opt/homebrew/bin/python3.11","/usr/local/bin/python3.13","/usr/local/bin/python3.12","/usr/local/bin/python3.11"]
    for c in candidates:
        p=shutil.which(c) if not c.startswith("/") else c
        if p and Path(p).exists() and _pyok(_pyver(p)):
            return Path(p)
    if sys.platform=="darwin" and shutil.which("brew"):
        say("Совместимый Python не найден. Устанавливаю Python 3.13...")
        subprocess.check_call(["brew","install","python@3.13"])
        return _find_python()
    raise RuntimeError("Нужен Python 3.11–3.13")

def ensure_venv() -> Path:
    venv=ROOT/".venv"; py=venv/"bin"/"python"
    if py.exists() and _pyok(_pyver(py)):
        return py
    if venv.exists():
        say("Пересоздаю несовместимое окружение...")
        shutil.rmtree(venv,ignore_errors=True)
    base=_find_python()
    say("Первый запуск: создаю рабочее окружение...")
    subprocess.check_call([str(base),"-m","venv",str(venv)])
    return py
'''
    if old in s:
        s=s.replace(old,new)
    bp.write_text(s,encoding="utf-8")

    # Fix global-mode inheritance bug.
    store=root/"app/core/store.py"
    replace(store,"(name,1,'shadow',purpose,'',json.dumps(cfg,ensure_ascii=False),now_iso())","(name,1,'inherit',purpose,'',json.dumps(cfg,ensure_ascii=False),now_iso())")
    sup=root/"app/agents/supervisor.py"
    replace(sup,"mode = Mode(cfg.get('mode') or global_mode.value)","raw_mode = cfg.get('mode') or 'inherit'\n            mode = global_mode if raw_mode == 'inherit' else Mode(raw_mode)")

    # Existing bundled DB used default shadow everywhere; migrate only untouched defaults.
    st=store.read_text(encoding="utf-8")
    marker="def seed_agents():\n    init_db()"
    if "migration_agent_mode_inherit_v060" not in st and marker in st:
        st=st.replace(marker,"def seed_agents():\n    init_db()\n    with _lock, _conn() as c:\n        done=c.execute(\"SELECT value FROM settings WHERE key='migration_agent_mode_inherit_v060'\").fetchone()\n        if not done:\n            c.execute(\"UPDATE agent_configs SET mode='inherit',updated_at=? WHERE mode='shadow' AND instructions=''\",(now_iso(),))\n            c.execute(\"INSERT OR REPLACE INTO settings(key,value,updated_at) VALUES (?,?,?)\",('migration_agent_mode_inherit_v060',json.dumps(True),now_iso()))")
        store.write_text(st,encoding="utf-8")

    # Repair duplicated JS block that could break navigation/update controls.
    ui=root/"app/static/index.html"
    u=ui.read_text(encoding="utf-8")
    bad="document.querySelectorAll('.nav button').forEach(b=>b.onclick=()=>{\nasync function checkUpdate(){let u=await j('/api/update/check',{method:'POST'});$('updateStatus').textContent=u.error?('Ошибка проверки: '+u.error):(u.update_available?('Доступна версия '+u.remote_version):('Установлена актуальная версия '+u.version));toast(u.update_available?'Есть обновление':'Версия актуальна');}\nasync function applyUpdate(){let u=await j('/api/update/apply',{method:'POST'});if(u.updated){$('updateStatus').textContent='Обновление установлено. Система перезапускается…';toast('Обновлено до '+u.after);}else{toast(u.error?('Ошибка: '+u.error):'Обновление не требуется');}}\ndocument.querySelectorAll('.nav button').forEach(x=>x.classList.remove('active'));b.classList.add('active');document.querySelectorAll('.tabscreen').forEach(x=>x.classList.remove('active'));$(b.dataset.tab).classList.add('active');$('pageTitle').textContent=b.textContent.trim();});"
    good="document.querySelectorAll('.nav button').forEach(b=>b.onclick=()=>{document.querySelectorAll('.nav button').forEach(x=>x.classList.remove('active'));b.classList.add('active');document.querySelectorAll('.tabscreen').forEach(x=>x.classList.remove('active'));$(b.dataset.tab).classList.add('active');$('pageTitle').textContent=b.textContent.trim();});"
    if bad in u: u=u.replace(bad,good)
    if '<option value="inherit">INHERIT GLOBAL</option>' not in u:
        u=u.replace('<option value="read">READ</option><option value="shadow">SHADOW</option>','<option value="inherit">INHERIT GLOBAL</option><option value="read">READ</option><option value="shadow">SHADOW</option>',1)
    ui.write_text(u,encoding="utf-8")

    (root/"VERSION").write_text(a.target_version.strip()+"\n",encoding="utf-8")
    print("[Avito AI update] critical v0.6 hotfix installed",flush=True)
    return 0

if __name__=="__main__":
    raise SystemExit(main())
