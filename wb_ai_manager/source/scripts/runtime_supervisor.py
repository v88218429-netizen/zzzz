#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import signal
import shutil
import subprocess
import sys
import time
import urllib.request
import webbrowser
import zipfile
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))


_BOOT_UPDATABLE = {
    'src','scripts','tests','docs','skills','plugin','google_apps_script','config',
    'START_WB_AI_MANAGER.command','START_HERE.md','README.md','RELEASE_NOTES.md','VERIFICATION.txt','CLAUDE.md','VERSION',
    'pyproject.toml','plugin.json','mcp.json','.mcp.json.example','.env.example',
    'Dockerfile','docker-compose.yml','.gitignore',
}
_BOOT_PERSISTENT = {Path('.env'),Path('data'),Path('.venv'),Path('.updates'),Path('config/catalog.csv'),Path('config/watch_queries.yaml')}


def bootstrap_port()->int:
    raw=os.environ.get('APP_PORT','').strip()
    if not raw:
        env_path=ROOT/'.env'
        if env_path.exists():
            try:
                for line in env_path.read_text(encoding='utf-8').splitlines():
                    if line.strip().startswith('APP_PORT='):
                        raw=line.split('=',1)[1].strip().strip('"\'')
                        break
            except Exception:
                pass
    try: return int(raw or 8787)
    except ValueError: return 8787


def preflight_restore_interrupted_update()->bool:
    """Stdlib-only recovery that runs before importing any application package.

    A full/patch update publishes files one-at-a-time atomically, but a process can still
    die between files. If the transaction marker says an update was in progress, restore
    the recorded backup before importing wb_control_center so mixed-version imports can
    never prevent rollback.
    """
    status_path=ROOT/'.updates/status.json'
    try:
        status=json.loads(status_path.read_text(encoding='utf-8'))
    except Exception:
        return False
    if not isinstance(status,dict) or not status.get('applying'):
        return False
    raw=status.get('backup')
    backup=Path(str(raw)).expanduser() if raw else None
    if backup is not None and not backup.is_absolute(): backup=(ROOT/backup).resolve()
    if backup is None or not backup.exists():
        candidates=sorted((ROOT/'.updates/backups').glob('*.zip'),key=lambda x:x.stat().st_mtime,reverse=True)
        backup=candidates[0] if candidates else None
    if backup is None or not backup.exists():
        raise RuntimeError('interrupted update detected but no rollback backup is available')
    # Remove only update-owned code/config. Tenant data/secrets remain untouched.
    for top in _BOOT_UPDATABLE:
        target=ROOT/top
        if top=='config':
            if target.exists() and target.is_dir():
                for child in sorted(target.rglob('*'),key=lambda x:len(x.parts),reverse=True):
                    rel=child.relative_to(ROOT)
                    if rel in _BOOT_PERSISTENT: continue
                    if child.is_file() or child.is_symlink(): child.unlink(missing_ok=True)
                    elif child.is_dir():
                        try: child.rmdir()
                        except OSError: pass
            continue
        if target.exists():
            if target.is_dir(): shutil.rmtree(target)
            else: target.unlink()
    with zipfile.ZipFile(backup,'r') as zf:
        base=ROOT.resolve()
        for info in zf.infolist():
            dest=(ROOT/info.filename).resolve()
            if base not in dest.parents and dest!=base:
                raise RuntimeError('unsafe path in rollback backup')
        zf.extractall(ROOT)
        for info in zf.infolist():
            mode=(info.external_attr>>16)&0o777
            target=ROOT/info.filename
            if mode and target.exists() and not target.is_symlink(): target.chmod(mode)
    return True


def health_info(port:int)->dict|None:
    try:
        with urllib.request.urlopen(f'http://127.0.0.1:{port}/health',timeout=1) as r:
            if r.status != 200:
                return None
            data=json.loads(r.read().decode('utf-8'))
            if isinstance(data,dict) and data.get('status') == 'ok' and data.get('app_version'):
                return data
    except Exception:
        return None
    return None


def wait_health(port:int,child:subprocess.Popen|None=None,seconds:int=45)->bool:
    deadline=time.time()+seconds
    while time.time()<deadline:
        if child is not None and child.poll() is not None:
            return False
        if health_info(port):
            return True
        time.sleep(.5)
    return False


def post_run_all(port:int)->None:
    try:
        req=urllib.request.Request(f'http://127.0.0.1:{port}/run-all?background=true',data=b'',method='POST')
        urllib.request.urlopen(req,timeout=3).read()
    except Exception: pass


def stop_child(child:subprocess.Popen|None)->None:
    if not child or child.poll() is not None:return
    child.terminate()
    try: child.wait(timeout=12)
    except subprocess.TimeoutExpired:
        child.kill(); child.wait(timeout=5)


def main()->int:
    # Duplicate-instance check must happen before recovery touches installation files.
    boot_port=bootstrap_port()
    existing=health_info(boot_port)
    if existing:
        print(f"WB AI Manager уже запущен на порту {boot_port} (версия {existing.get('app_version')}). Открываю текущий экземпляр.",flush=True)
        webbrowser.open(f'http://127.0.0.1:{boot_port}/dashboard#decisions')
        return 0
    try:
        preflight_restored=preflight_restore_interrupted_update()
    except Exception as exc:
        print(f'Не могу выполнить preflight-восстановление прерванного обновления: {exc}',flush=True)
        return 2

    # Import application code only after stdlib recovery restored a coherent package.
    from wb_control_center.config import Settings
    from wb_control_center.updater import UpdateManager
    settings=Settings()
    manager=UpdateManager(ROOT,settings.auto_update_manifest_url,settings.auto_update_channel)
    stopping=False
    child=None

    def on_signal(sig,frame):
        nonlocal stopping
        stopping=True
        stop_child(child)
    signal.signal(signal.SIGINT,on_signal); signal.signal(signal.SIGTERM,on_signal)

    # Complete recovery with dependency refresh + smoke after the stdlib preflight.
    try:
        recovered=manager.recover_interrupted_update()
        if preflight_restored or recovered.get('recovered_interrupted_update'):
            print('Обнаружено прерванное обновление: резервная копия восстановлена и проверена.',flush=True)
    except Exception as exc:
        print(f'Не могу безопасно завершить восстановление прерванного обновления: {exc}',flush=True)
        return 2

    # Startup update only when this supervisor owns the installation lifecycle.
    if settings.auto_update_enabled:
        check=manager.check()
        manifest=check.get('manifest')
        if check.get('available') and manifest:
            print(f"Доступно обновление {manifest.version}. Устанавливаю безопасно…",flush=True)
            try:
                manager.apply(manifest)
                print('Обновление установлено. Перезапускаю надсмотрщик…',flush=True)
                os.execv(sys.executable,[sys.executable,str(Path(__file__).resolve())])
            except Exception as exc:
                print(f'Обновление не установлено, продолжаю на предыдущей версии: {exc}',flush=True)

    last_check=time.time()
    opened=False
    crashes=0
    while not stopping:
        if child is None or child.poll() is not None:
            if child is not None:
                crashes+=1
                print(f'Рабочий процесс остановился (код {child.returncode}). Перезапускаю…',flush=True)
                time.sleep(min(10,2+crashes))
            cmd=[sys.executable,'-m','uvicorn','wb_control_center.api:app','--host',settings.app_host,'--port',str(settings.app_port)]
            env=os.environ.copy(); env['PYTHONPATH']=str(ROOT/'src')
            child=subprocess.Popen(cmd,cwd=ROOT,env=env)
            if wait_health(settings.app_port,child):
                crashes=0
                post_run_all(settings.app_port)
                if not opened:
                    webbrowser.open(f'http://127.0.0.1:{settings.app_port}/dashboard#decisions')
                    opened=True
            else:
                code=child.poll() if child else None
                stop_child(child); child=None
                crashes+=1
                print(f'Не удалось запустить веб-процесс на порту {settings.app_port} (код {code}). Повтор через несколько секунд…',flush=True)
                time.sleep(min(10,2+crashes))
                continue

        if settings.auto_update_enabled and time.time()-last_check >= settings.auto_update_check_seconds:
            last_check=time.time()
            check=manager.check(); manifest=check.get('manifest')
            if check.get('available') and manifest:
                print(f"Найдена новая версия {manifest.version}. Останавливаю агент для безопасного обновления…",flush=True)
                stop_child(child); child=None
                try:
                    manager.apply(manifest)
                    print('Обновление прошло проверку. Перезапускаю на новой версии…',flush=True)
                    os.execv(sys.executable,[sys.executable,str(Path(__file__).resolve())])
                except Exception as exc:
                    print(f'Обновление отклонено/откачено: {exc}. Возвращаю рабочий процесс.',flush=True)
                    continue
        time.sleep(2)
    stop_child(child)
    return 0

if __name__=='__main__':
    raise SystemExit(main())
