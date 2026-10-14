"""Derive a GitHub-to-Apps-Script bridge token from existing CLASPRC_JSON.

Never log or persist the derived token. It is SHA256-HMAC bound to this service
and is not the original OAuth refresh token.
"""
import hashlib
import hmac
import json
import os
from pathlib import Path

LABEL = b"AI_VIDEO_MONITOR_SHEETS_BRIDGE_V1"


def bridge_token():
    config = json.loads(os.environ["CLASPRC_JSON"])
    refresh = config["tokens"]["default"]["refresh_token"]
    if not refresh:
        raise RuntimeError("CLASPRC_JSON missing refresh token")
    return hmac.new(refresh.encode("utf-8"), LABEL, hashlib.sha256).hexdigest()


def prepare_script():
    """Prepare a temporary remote-deployment workspace for clasp."""
    source = Path("google_apps_script_ai_monitor")
    target = Path("/tmp/ai_video_monitor_clasp")
    target.mkdir(parents=True, exist_ok=True)
    for name in ("appsscript.json", ".clasp.json"):
        (target / name).write_text((source / name).read_text())
    template = (source / "Code.gs").read_text()
    expected_sha = hashlib.sha256(bridge_token().encode()).hexdigest()
    if "__TOKEN_HASH__" not in template:
        raise RuntimeError("Bridge token placeholder missing")
    (target / "Code.gs").write_text(template.replace("__TOKEN_HASH__", expected_sha))
    # OAuth input is a GitHub secret; never print it. Create files only on runner.
    authdir = Path("/tmp/ai_video_monitor_oauth")
    authdir.mkdir(parents=True, exist_ok=True)
    authfile = authdir / ".clasprc.json"
    authfile.write_text(os.environ["CLASPRC_JSON"])
    authfile.chmod(0o600)
    print("BRIDGE_PREPARED=OK")
    return target, authdir


if __name__ == "__main__":
    prepare_script()
