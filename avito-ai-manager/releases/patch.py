#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import hashlib
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.request
from pathlib import Path

BASE = "https://raw.githubusercontent.com/v88218429-netizen/zzzz/mainggg/avito-ai-manager/releases/v0.7.0"
PARTS = 8
ARCHIVE_SHA256 = "2bbb2635b4d7a73a10cee23e58f9db62680953d87b5e1259f819b70b60d59f02"
EXPECTED_B64_LEN = 42212


def pyver(exe: Path):
    try:
        out = subprocess.check_output(
            [str(exe), "-c", "import sys;print('.'.join(map(str,sys.version_info[:3])))"],
            text=True,
            stderr=subprocess.DEVNULL,
            timeout=8,
        ).strip()
        return tuple(map(int, out.split(".")[:3]))
    except Exception:
        return None


def compatible(v):
    return bool(v and (3, 11) <= v[:2] <= (3, 13))


def find_python():
    names = [
        "python3.13", "python3.12", "python3.11",
        "/opt/homebrew/bin/python3.13", "/opt/homebrew/bin/python3.12", "/opt/homebrew/bin/python3.11",
        "/usr/local/bin/python3.13", "/usr/local/bin/python3.12", "/usr/local/bin/python3.11",
    ]
    for name in names:
        found = shutil.which(name) if not name.startswith("/") else name
        if found and Path(found).exists() and compatible(pyver(Path(found))):
            return Path(found)
    return None


def ensure_venv(root: Path) -> Path:
    venv = root / ".venv"
    py = venv / "bin" / "python"
    if py.exists() and compatible(pyver(py)):
        return py
    if venv.exists():
        print("[Avito AI update] Удаляю несовместимое Python-окружение...", flush=True)
        shutil.rmtree(venv, ignore_errors=True)
    base = find_python()
    if base is None and sys.platform == "darwin" and shutil.which("brew"):
        print("[Avito AI update] Устанавливаю совместимый Python 3.13...", flush=True)
        subprocess.check_call(["brew", "install", "python@3.13"])
        base = find_python()
    if base is None:
        raise RuntimeError("Не найден Python 3.11–3.13. Установите python@3.13 через Homebrew.")
    subprocess.check_call([str(base), "-m", "venv", str(venv)])
    if not py.exists() or not compatible(pyver(py)):
        raise RuntimeError("Не удалось создать совместимое Python-окружение")
    return py


def download_release() -> bytes:
    chunks = []
    for i in range(PARTS):
        url = f"{BASE}/part{i:02d}.txt?ts={int(time.time())}"
        req = urllib.request.Request(url, headers={"User-Agent": "AvitoAI-Updater/0.7"})
        with urllib.request.urlopen(req, timeout=30) as response:
            chunks.append(response.read().decode("ascii").strip())
    encoded = "".join(chunks)
    if len(encoded) != EXPECTED_B64_LEN:
        raise RuntimeError(f"Неполный релиз: {len(encoded)} != {EXPECTED_B64_LEN}")
    archive = base64.b64decode(encoded, validate=True)
    digest = hashlib.sha256(archive).hexdigest()
    if digest != ARCHIVE_SHA256:
        raise RuntimeError(f"SHA-256 релиза не совпал: {digest}")
    return archive


def make_backup(root: Path) -> Path:
    backup_dir = root / "data" / "update_backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup = backup_dir / f"before-v0.7.0-{int(time.time())}.tar.gz"
    excluded = {".venv", ".env", "data"}
    with tarfile.open(backup, "w:gz") as tf:
        for item in root.iterdir():
            if item.name in excluded or item.name.startswith(".update-"):
                continue
            tf.add(item, arcname=item.name)
    return backup


def copy_release(stage: Path, root: Path):
    for src in stage.iterdir():
        name = src.name
        if name in {".env", ".venv"}:
            continue
        if name == "data":
            target_data = root / "data"
            target_data.mkdir(parents=True, exist_ok=True)
            for child in src.iterdir():
                if child.name.startswith("avito_ai.db"):
                    continue
                dst = target_data / child.name
                if child.is_dir():
                    shutil.copytree(child, dst, dirs_exist_ok=True)
                else:
                    shutil.copy2(child, dst)
            continue
        dst = root / name
        if src.is_dir():
            shutil.copytree(src, dst, dirs_exist_ok=True)
        else:
            shutil.copy2(src, dst)


def restore_backup(root: Path, backup: Path):
    with tarfile.open(backup, "r:gz") as tf:
        tf.extractall(root)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True)
    ap.add_argument("--target-version", required=True)
    args = ap.parse_args()
    root = Path(args.root).resolve()

    # This runs before the old bootstrap tries to install dependencies.
    py = ensure_venv(root)
    archive = download_release()
    backup = make_backup(root)

    with tempfile.TemporaryDirectory(prefix="avito-ai-v070-") as td:
        td = Path(td)
        archive_path = td / "release.tar.gz"
        archive_path.write_bytes(archive)
        stage = td / "stage"
        stage.mkdir()
        with tarfile.open(archive_path, "r:gz") as tf:
            tf.extractall(stage)
        try:
            copy_release(stage, root)
            subprocess.check_call([str(py), "-m", "compileall", "-q", str(root / "app"), str(root / "bootstrap.py"), str(root / "launcher.py")])
        except Exception:
            print("[Avito AI update] Ошибка установки. Восстанавливаю предыдущую версию...", flush=True)
            restore_backup(root, backup)
            raise

    (root / "VERSION").write_text(args.target_version.strip() + "\n", encoding="utf-8")
    print("[Avito AI update] v0.7.0 установлена: live Avito + LLM + CRM + actions", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
