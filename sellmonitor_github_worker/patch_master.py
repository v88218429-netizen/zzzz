#!/usr/bin/env python3
from pathlib import Path
import sys

if len(sys.argv) != 2:
    raise SystemExit("usage: patch_master.py <apps-script-source-root>")

root = Path(sys.argv[1]).resolve()
marker = "SELLMONITOR_GITHUB_MASTER_HOOK_V1"
candidates = []

for ext in ("*.gs", "*.js"):
    for path in root.rglob(ext):
        try:
            text = path.read_text(encoding="utf-8")
        except Exception:
            continue
        if "function runFinalAutomationCycle_" in text and "saveMasterCycleResult_(cycleErrors);" in text:
            candidates.append((path, text))

if len(candidates) != 1:
    raise SystemExit(f"expected exactly one master source, found={len(candidates)}")

path, text = candidates[0]
if marker in text:
    print(f"PATCH_ALREADY_PRESENT: {path.name}")
    raise SystemExit(0)

needle = "    saveMasterCycleResult_(cycleErrors);"
if text.count(needle) != 1:
    raise SystemExit(f"expected exactly one master save point, found={text.count(needle)}")

block = """    /* SELLMONITOR_GITHUB_MASTER_HOOK_V1 */
    try {
      var smGithubTrigger = sellmonitorGithubEnsureTrigger_();
      if (smGithubTrigger && smGithubTrigger.created) {
        sellmonitorGithubPlatformReady();
        sellmonitorGithubTick();
      }
    } catch (sellmonitorGithubError) {
      cycleErrors.push(
        'Sellmonitor GitHub worker: ' + sellmonitorGithubError.message
      );
      Logger.log(
        'Sellmonitor GitHub worker: ' +
        (sellmonitorGithubError.stack || sellmonitorGithubError.message)
      );
    }

"""

text = text.replace(needle, block + needle, 1)
path.write_text(text, encoding="utf-8")
print(f"PATCH_OK: {path.name}")

# deploy-bump 2026-10-04
