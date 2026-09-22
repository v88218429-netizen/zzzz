#!/bin/bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
WORK="$(mktemp -d)"
REMOTE="$WORK/remote"
FAKE_BIN="$WORK/bin"
mkdir -p "$REMOTE" "$FAKE_BIN"
trap 'rm -rf "$WORK"' EXIT

export REPO_ROOT="$ROOT"
export FAKE_REMOTE="$REMOTE"

python3 - "$ROOT" "$REMOTE" <<'PY'
from pathlib import Path
import importlib.util
import json
import sys

root = Path(sys.argv[1])
remote = Path(sys.argv[2])

def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

patch_test = load(
    root / "wb_os/tests/test_patch_live_master.py",
    "patch_test",
)
audit_test = load(
    root / "wb_os/tests/test_audit_and_repair_live.py",
    "audit_test",
)

(remote / "Master.js").write_text(
    patch_test.LEGACY,
    encoding="utf-8",
)
(remote / "K2_stable.js").write_text(
    audit_test.K2_STABLE,
    encoding="utf-8",
)
(remote / "K2_old.js").write_text(
    audit_test.K2_OLD,
    encoding="utf-8",
)
(remote / "Spp.js").write_text(
    audit_test.SPP,
    encoding="utf-8",
)
(remote / "Supplier.js").write_text(
    audit_test.SUPPLIER,
    encoding="utf-8",
)
(remote / "LegacyPrice.js").write_text(
    audit_test.LEGACY_PRICE,
    encoding="utf-8",
)
(remote / "appsscript.json").write_text(
    json.dumps(
        {
            "timeZone": "Europe/Moscow",
            "exceptionLogging": "STACKDRIVER",
            "runtimeVersion": "V8",
        },
        ensure_ascii=False,
        indent=2,
    ),
    encoding="utf-8",
)
PY

cat > "$FAKE_BIN/curl" <<'SH'
#!/bin/bash
set -euo pipefail

url=""
out=""

while [ "$#" -gt 0 ]; do
  case "$1" in
    -o)
      out="$2"
      shift 2
      ;;
    http://*|https://*)
      url="$1"
      shift
      ;;
    *)
      shift
      ;;
  esac
done

if [ -z "$url" ] || [ -z "$out" ]; then
  echo "fake curl: missing url/out" >&2
  exit 2
fi

case "$url" in
  */wb_os/apps_script/K2_EVOLUTION_ENGINE.gs)
    src="$REPO_ROOT/wb_os/apps_script/K2_EVOLUTION_ENGINE.gs"
    ;;
  */wb_os/apps_script/WB_PUBLIC_PRICE_V4.gs)
    src="$REPO_ROOT/wb_os/apps_script/WB_PUBLIC_PRICE_V4.gs"
    ;;
  */wb_os/tools/patch_live_master.py)
    src="$REPO_ROOT/wb_os/tools/patch_live_master.py"
    ;;
  */wb_os/tools/audit_and_repair_live.py)
    src="$REPO_ROOT/wb_os/tools/audit_and_repair_live.py"
    ;;
  *)
    echo "fake curl: unexpected url $url" >&2
    exit 3
    ;;
esac

cp "$src" "$out"
SH
chmod +x "$FAKE_BIN/curl"

cat > "$FAKE_BIN/clasp" <<'SH'
#!/bin/bash
set -euo pipefail

cmd="${1:-}"
shift || true

case "$cmd" in
  pull)
    rsync -a "$FAKE_REMOTE/" "$PWD/"
    echo "Pulled fake live project."
    ;;
  push)
    rm -rf "$FAKE_REMOTE"
    mkdir -p "$FAKE_REMOTE"
    rsync -a --exclude='.clasp.json' "$PWD/" "$FAKE_REMOTE/"
    echo "Pushed fake live project."
    ;;
  run)
    fn="${1:-}"
    echo "Fake clasp run: $fn"
    ;;
  *)
    echo "fake clasp: unsupported command $cmd" >&2
    exit 4
    ;;
esac
SH
chmod +x "$FAKE_BIN/clasp"

export PATH="$FAKE_BIN:$PATH"

SHA="${GITHUB_SHA:-0000000000000000000000000000000000000000}"

OUTPUT="$WORK/deploy.log"

bash "$ROOT/wb_os/run_core_repair.command"   "https://script.google.com/home/projects/FAKE_SCRIPT_ID/edit"   "$SHA"   >"$OUTPUT" 2>&1

cat "$OUTPUT"

grep -q "CORE REPAIR DEPLOYED + REMOTE VERIFIED" "$OUTPUT"
grep -q "full project pull-back: exact semantic match" "$OUTPUT"
grep -q "repair second pass: no-op" "$OUTPUT"

grep -Rqs "K2 Evolution Engine v0.1.17" "$REMOTE"
grep -Rqs "WB Public Customer Price Engine v0.1.18" "$REMOTE"
grep -Rqs "function wbOsEnsureMasterTrigger()" "$REMOTE"
grep -Rqs "function wbOsAssertTrustedNeedsSources_" "$REMOTE"

python3 "$ROOT/wb_os/tools/audit_and_repair_live.py" "$REMOTE" >/dev/null
python3 "$ROOT/wb_os/tools/patch_live_master.py" "$REMOTE" >/dev/null

echo "WB OS core repair end-to-end fake-live deploy: OK"
