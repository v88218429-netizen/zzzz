from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any


PERSISTENT_RELATIVE = {
    Path('.env'),
    Path('data'),
    Path('.venv'),
    Path('.updates'),
    Path('config/catalog.csv'),
    Path('config/watch_queries.yaml'),
}

UPDATABLE_TOP_LEVEL = {
    'src','scripts','tests','docs','skills','plugin','google_apps_script','config',
    'START_WB_AI_MANAGER.command','START_HERE.md','README.md','CLAUDE.md','VERSION',
    'pyproject.toml','plugin.json','mcp.json','.mcp.json.example','.env.example',
    'Dockerfile','docker-compose.yml','.gitignore',
}


def _version_tuple(value: str) -> tuple[int, ...]:
    out=[]
    for part in str(value).strip().lstrip('v').split('.'):
        digits=''.join(ch for ch in part if ch.isdigit())
        out.append(int(digits or 0))
    return tuple(out or [0])


def current_version(root: Path) -> str:
    try:
        return (root / 'VERSION').read_text(encoding='utf-8').strip() or '0.0.0'
    except Exception:
        return '0.0.0'


@dataclass
class UpdateManifest:
    version: str
    archive_url: str = ''
    sha256: str = ''
    files: list[dict[str, Any]] | None = None
    channel: str = 'stable'
    notes: str = ''
    minimum_current_version: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any], channel: str = 'stable') -> 'UpdateManifest':
        payload=data
        if isinstance(data.get('channels'), dict):
            payload=data['channels'].get(channel) or data['channels'].get('stable') or {}
        return cls(
            version=str(payload.get('version') or ''),
            archive_url=str(payload.get('archive_url') or ''),
            sha256=str(payload.get('sha256') or '').lower(),
            files=(payload.get('files') if isinstance(payload.get('files'), list) else []),
            channel=str(payload.get('channel') or channel),
            notes=str(payload.get('notes') or ''),
            minimum_current_version=(str(payload['minimum_current_version']) if payload.get('minimum_current_version') else None),
        )


class UpdateManager:
    def __init__(self, root: Path, manifest_url: str, channel: str = 'stable'):
        self.root=root.resolve()
        self.manifest_url=(manifest_url or '').strip()
        self.channel=channel or 'stable'
        self.state_dir=self.root / '.updates'
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.status_path=self.state_dir / 'status.json'

    def _write_status(self, **fields: Any) -> dict[str, Any]:
        old=self.status()
        old.update(fields)
        old['current_version']=current_version(self.root)
        old['updated_at_epoch']=time.time()
        self.status_path.write_text(json.dumps(old,ensure_ascii=False,indent=2),encoding='utf-8')
        return old

    def status(self) -> dict[str, Any]:
        try:
            x=json.loads(self.status_path.read_text(encoding='utf-8'))
            return x if isinstance(x,dict) else {}
        except Exception:
            return {'current_version': current_version(self.root)}

    def fetch_manifest(self) -> UpdateManifest:
        if not self.manifest_url:
            raise RuntimeError('manifest URL is empty')
        sep='&' if '?' in self.manifest_url else '?'
        url=f"{self.manifest_url}{sep}t={int(time.time())}"
        req=urllib.request.Request(url,headers={'User-Agent':'WB-AI-Manager-Updater/1.0','Cache-Control':'no-cache'})
        with urllib.request.urlopen(req,timeout=12) as resp:
            data=json.loads(resp.read().decode('utf-8'))
        if not isinstance(data,dict):
            raise ValueError('update manifest must be an object')
        m=UpdateManifest.from_dict(data,self.channel)
        archive_ok=bool(m.archive_url and len(m.sha256)==64)
        files_ok=isinstance(m.files,list) and all(isinstance(x,dict) and x.get('path') and (x.get('delete') or (x.get('url') and len(str(x.get('sha256') or ''))==64)) for x in m.files)
        # An empty patch list is valid for the currently published baseline version.
        if not m.version or not (archive_ok or files_ok):
            raise ValueError('update manifest is incomplete')
        return m

    def check(self) -> dict[str, Any]:
        try:
            m=self.fetch_manifest()
            cur=current_version(self.root)
            available=_version_tuple(m.version) > _version_tuple(cur)
            return self._write_status(
                last_check_epoch=time.time(),
                available=available,
                latest_version=m.version,
                latest_notes=m.notes,
                last_error=None,
            ) | {'manifest':m}
        except Exception as exc:
            return self._write_status(last_check_epoch=time.time(),available=False,last_error=str(exc))

    @staticmethod
    def _sha256(path: Path) -> str:
        h=hashlib.sha256()
        with path.open('rb') as f:
            for chunk in iter(lambda:f.read(1024*1024),b''):
                h.update(chunk)
        return h.hexdigest()

    @staticmethod
    def _safe_extract(zf: zipfile.ZipFile, dest: Path) -> None:
        base=dest.resolve()
        for info in zf.infolist():
            p=(dest / info.filename).resolve()
            if base not in p.parents and p != base:
                raise ValueError('unsafe path in update archive')
        zf.extractall(dest)

    @staticmethod
    def _payload_root(extracted: Path) -> Path:
        if (extracted/'VERSION').exists():
            return extracted
        dirs=[p for p in extracted.iterdir() if p.is_dir() and not p.name.startswith('__MACOSX')]
        if len(dirs)==1 and (dirs[0]/'VERSION').exists():
            return dirs[0]
        for p in extracted.rglob('VERSION'):
            if p.parent.joinpath('pyproject.toml').exists():
                return p.parent
        raise ValueError('update archive does not contain a WB AI Manager root')

    def _backup(self) -> Path:
        stamp=time.strftime('%Y%m%d-%H%M%S')
        backup=self.state_dir/'backups'/f"{current_version(self.root)}-{stamp}.zip"
        backup.parent.mkdir(parents=True,exist_ok=True)
        with zipfile.ZipFile(backup,'w',zipfile.ZIP_DEFLATED) as z:
            for top in sorted(UPDATABLE_TOP_LEVEL):
                p=self.root/top
                if not p.exists():
                    continue
                if p.is_file():
                    z.write(p,p.relative_to(self.root))
                else:
                    for f in p.rglob('*'):
                        if f.is_file() and '__pycache__' not in f.parts:
                            rel=f.relative_to(self.root)
                            if rel in PERSISTENT_RELATIVE:
                                continue
                            z.write(f,rel)
        return backup

    def _restore(self, backup: Path) -> None:
        # Remove update-owned paths first so files introduced by a failed release
        # cannot survive the rollback. User data and secrets are never touched.
        for top in UPDATABLE_TOP_LEVEL:
            p=self.root/top
            if top == 'config':
                # Preserve tenant-owned config files, but remove every other config
                # artifact introduced by the failed release before extracting backup.
                if p.exists() and p.is_dir():
                    keep={x for x in PERSISTENT_RELATIVE if x.parts and x.parts[0]=='config'}
                    for child in sorted(p.rglob('*'), key=lambda x: len(x.parts), reverse=True):
                        rel=child.relative_to(self.root)
                        if rel in keep:
                            continue
                        if child.is_file() or child.is_symlink():
                            child.unlink(missing_ok=True)
                        elif child.is_dir():
                            try: child.rmdir()
                            except OSError: pass
                continue
            if p.exists():
                if p.is_dir(): shutil.rmtree(p)
                else: p.unlink()
        with zipfile.ZipFile(backup,'r') as z:
            self._safe_extract(z,self.root)

    def _overlay(self, payload: Path) -> None:
        for top in UPDATABLE_TOP_LEVEL:
            src=payload/top
            if not src.exists():
                continue
            dst=self.root/top
            if top=='config' and src.is_dir():
                dst.mkdir(parents=True,exist_ok=True)
                for f in src.rglob('*'):
                    if not f.is_file():
                        continue
                    rel=Path('config')/f.relative_to(src)
                    if rel in PERSISTENT_RELATIVE:
                        continue
                    target=self.root/rel
                    target.parent.mkdir(parents=True,exist_ok=True)
                    shutil.copy2(f,target)
                continue
            if dst.exists():
                if dst.is_dir(): shutil.rmtree(dst)
                else: dst.unlink()
            if src.is_dir(): shutil.copytree(src,dst)
            else:
                dst.parent.mkdir(parents=True,exist_ok=True)
                shutil.copy2(src,dst)

    def _smoke(self) -> tuple[bool,str]:
        py=self.root/'.venv/bin/python'
        python=str(py if py.exists() else Path(sys.executable))
        commands=[
            [python,'-m','compileall','-q',str(self.root/'src')],
            [python,str(self.root/'scripts/update_smoke.py')],
        ]
        for cmd in commands:
            p=subprocess.run(cmd,cwd=self.root,text=True,capture_output=True,timeout=120)
            if p.returncode!=0:
                return False,(p.stdout+'\n'+p.stderr).strip()[-6000:]
        return True,'ok'

    def _apply_file_patch(self, files: list[dict[str, Any]], temp_dir: Path) -> None:
        for item in files:
            rel=Path(str(item.get('path') or ''))
            if rel.is_absolute() or '..' in rel.parts or not rel.parts or rel.parts[0] not in UPDATABLE_TOP_LEVEL:
                raise ValueError(f'unsafe update path: {rel}')
            if rel in PERSISTENT_RELATIVE:
                raise ValueError(f'update is not allowed to modify persistent user path: {rel}')
            target=self.root/rel
            if item.get('delete'):
                if target.exists():
                    if target.is_dir(): shutil.rmtree(target)
                    else: target.unlink()
                continue
            url=str(item.get('url') or '')
            expected=str(item.get('sha256') or '').lower()
            local=temp_dir/rel
            local.parent.mkdir(parents=True,exist_ok=True)
            req=urllib.request.Request(url,headers={'User-Agent':'WB-AI-Manager-Updater/1.0','Cache-Control':'no-cache'})
            with urllib.request.urlopen(req,timeout=30) as resp, local.open('wb') as f:
                shutil.copyfileobj(resp,f)
            got=self._sha256(local)
            if got != expected:
                raise ValueError(f'checksum mismatch for {rel}: expected {expected}, got {got}')
            target.parent.mkdir(parents=True,exist_ok=True)
            shutil.copy2(local,target)
            if item.get('executable'):
                target.chmod(target.stat().st_mode | 0o111)

    def apply(self, manifest: UpdateManifest | None = None) -> dict[str, Any]:
        manifest=manifest or self.fetch_manifest()
        cur=current_version(self.root)
        if _version_tuple(manifest.version) <= _version_tuple(cur):
            return self._write_status(available=False,latest_version=manifest.version,last_result='already_current',last_error=None)
        if manifest.minimum_current_version and _version_tuple(cur) < _version_tuple(manifest.minimum_current_version):
            raise RuntimeError(f'current version {cur} is below minimum {manifest.minimum_current_version}')
        self._write_status(applying=True,target_version=manifest.version,last_error=None)
        backup=self._backup()
        try:
            with tempfile.TemporaryDirectory(prefix='wb-ai-update-') as td:
                td=Path(td)
                if manifest.files:
                    self._apply_file_patch(manifest.files, td/'files')
                    # Patch releases must explicitly update VERSION as one of their files.
                    if current_version(self.root) != manifest.version:
                        raise ValueError('patch did not update VERSION to manifest version')
                else:
                    archive=td/'update.zip'; extracted=td/'extracted'; extracted.mkdir()
                    req=urllib.request.Request(manifest.archive_url,headers={'User-Agent':'WB-AI-Manager-Updater/1.0','Cache-Control':'no-cache'})
                    with urllib.request.urlopen(req,timeout=60) as resp, archive.open('wb') as f:
                        shutil.copyfileobj(resp,f)
                    got=self._sha256(archive)
                    if got.lower()!=manifest.sha256.lower():
                        raise ValueError(f'checksum mismatch: expected {manifest.sha256}, got {got}')
                    with zipfile.ZipFile(archive,'r') as z:
                        self._safe_extract(z,extracted)
                    payload=self._payload_root(extracted)
                    if current_version(payload)!=manifest.version:
                        raise ValueError('archive VERSION does not match manifest')
                    self._overlay(payload)
            py=self.root/'.venv/bin/python'
            if py.exists():
                p=subprocess.run([str(py),'-m','pip','install','-e','.'],cwd=self.root,text=True,capture_output=True,timeout=300)
                if p.returncode!=0:
                    raise RuntimeError('dependency refresh failed: '+(p.stdout+'\n'+p.stderr)[-5000:])
            ok,why=self._smoke()
            if not ok:
                raise RuntimeError('post-update smoke failed: '+why)
            self._write_status(applying=False,available=False,last_result='updated',installed_version=manifest.version,last_success_epoch=time.time(),backup=str(backup),last_error=None)
            return self.status()
        except Exception as exc:
            try:
                self._restore(backup)
                py=self.root/'.venv/bin/python'
                if py.exists(): subprocess.run([str(py),'-m','pip','install','-e','.'],cwd=self.root,capture_output=True,timeout=300)
                rollback='ok'
            except Exception as rex:
                rollback=f'failed: {rex}'
            self._write_status(applying=False,last_result='rollback',rollback=rollback,last_error=str(exc),backup=str(backup))
            raise
