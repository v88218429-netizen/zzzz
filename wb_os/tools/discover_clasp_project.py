#!/usr/bin/env python3
import json
import os
import pathlib
import sys

TARGET_SHEET = "1VQwf-QPeSjexrEculDjWt_hpuCKu7PLzLZhMFjP2VZM"
MARKERS = [
    TARGET_SHEET,
    "function runFinalAutomationCycle_",
    "function getK2WarehouseItems_",
    "refreshAutomationStatusSheet_",
]

HOME = pathlib.Path.home()
ROOTS = [
    HOME / "Desktop",
    HOME / "Documents",
    HOME / "Projects",
    HOME / "Code",
    HOME / "Developer",
    HOME,
]
SKIP = {
    "Library", ".Trash", ".git", "node_modules", ".cache",
    ".npm", ".gradle", ".cargo", ".rustup", "Applications",
    "Movies", "Music", "Pictures",
}

def iter_clasp_files(root: pathlib.Path):
    if not root.exists():
        return
    root_depth = len(root.parts)
    for current, dirs, files in os.walk(root):
        p = pathlib.Path(current)
        depth = len(p.parts) - root_depth
        dirs[:] = [d for d in dirs if d not in SKIP and not d.lower().startswith("backup")]
        if depth > 6:
            dirs[:] = []
            continue
        if ".clasp.json" in files:
            yield p / ".clasp.json"

def project_root_from_clasp(clasp_file: pathlib.Path):
    try:
        data = json.loads(clasp_file.read_text(encoding="utf-8"))
    except Exception:
        return None
    root_dir = str(data.get("rootDir") or "").strip()
    if root_dir:
        return (clasp_file.parent / root_dir).resolve()
    return clasp_file.parent.resolve()

def score_project(clasp_file: pathlib.Path):
    source_root = project_root_from_clasp(clasp_file)
    if not source_root or not source_root.exists():
        return 0, source_root

    score = 0
    found = set()
    total_bytes = 0

    for ext in ("*.gs", "*.js"):
        for f in source_root.rglob(ext):
            try:
                if f.stat().st_size > 2_000_000:
                    continue
                text = f.read_text(encoding="utf-8", errors="ignore")
                total_bytes += len(text)
                for marker in MARKERS:
                    if marker in text:
                        found.add(marker)
            except Exception:
                continue
            if total_bytes > 15_000_000:
                break

    score = len(found)
    return score, source_root

seen = set()
candidates = []

for root in ROOTS:
    for clasp in iter_clasp_files(root) or []:
        key = str(clasp.resolve())
        if key in seen:
            continue
        seen.add(key)
        score, source_root = score_project(clasp)
        if score >= 3:
            candidates.append((score, clasp.parent.resolve(), source_root))

candidates.sort(key=lambda x: (-x[0], len(str(x[1]))))

exact = [x for x in candidates if x[0] == len(MARKERS)]

if len(exact) != 1:
    names = [x[1].name for x in exact or candidates[:8]]
    print(
        "WB_OS_DISCOVERY_FAILED: expected exactly one live clasp project; "
        f"exact={len(exact)}, candidates={len(candidates)}, names={names}",
        file=sys.stderr,
    )
    sys.exit(2)

# stdout is intentionally only the path and is consumed into a shell variable.
print(str(exact[0][1]))
