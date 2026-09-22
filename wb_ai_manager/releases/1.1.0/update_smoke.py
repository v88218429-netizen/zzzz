from __future__ import annotations

import base64
import hashlib
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BUNDLE_B64 = ROOT / "scripts" / ".v11_bundle.b64"
BUNDLE_SHA256 = "1aa4666105d8cff30aa1d59c505938422db66b3c1686b92b37f5bd061a9afd9f"


def safe_extract(zf: zipfile.ZipFile, dest: Path) -> None:
    base = dest.resolve()
    for info in zf.infolist():
        target = (dest / info.filename).resolve()
        if target != base and base not in target.parents:
            raise RuntimeError(f"unsafe bundle path: {info.filename}")
    zf.extractall(dest)


def run(cmd: list[str]) -> None:
    p = subprocess.run(cmd, cwd=ROOT, text=True, capture_output=True, timeout=300)
    if p.returncode != 0:
        raise RuntimeError((p.stdout + "\n" + p.stderr).strip()[-8000:])


def bootstrap() -> None:
    if not BUNDLE_B64.exists():
        return
    raw = base64.b64decode(BUNDLE_B64.read_text(encoding="ascii"), validate=True)
    got = hashlib.sha256(raw).hexdigest()
    if got != BUNDLE_SHA256:
        raise RuntimeError(f"v1.1 bundle checksum mismatch: {got}")
    with tempfile.TemporaryDirectory(prefix="wb-v11-bootstrap-") as td:
        zpath = Path(td) / "bundle.zip"
        zpath.write_bytes(raw)
        with zipfile.ZipFile(zpath, "r") as zf:
            safe_extract(zf, ROOT)
    for p in [ROOT / "START_WB_AI_MANAGER.command", *list((ROOT / "scripts" / "launch").glob("*.command")), *list((ROOT / "scripts").glob("*.sh"))]:
        if p.exists():
            p.chmod(p.stat().st_mode | 0o111)
    BUNDLE_B64.unlink(missing_ok=True)
    if (ROOT / "VERSION").read_text(encoding="utf-8").strip() != "1.1.0":
        raise RuntimeError("v1.1 bundle did not install VERSION=1.1.0")
    py = ROOT / ".venv" / "bin" / "python"
    python = str(py if py.exists() else Path(sys.executable))
    if py.exists():
        run([python, "-m", "pip", "install", "-e", "."])
    run([python, "-m", "compileall", "-q", str(ROOT / "src"), str(ROOT / "scripts")])
    run([python, str(ROOT / "scripts" / "update_smoke.py")])


if __name__ == "__main__":
    bootstrap()
    print("v1.1 bootstrap: ok")
