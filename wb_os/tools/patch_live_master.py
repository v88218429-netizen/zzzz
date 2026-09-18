#!/usr/bin/env python3
import pathlib
import re
import sys

if len(sys.argv) != 2:
    raise SystemExit("usage: patch_live_master.py <apps-script-source-root>")

root = pathlib.Path(sys.argv[1]).resolve()

files = []
for ext in ("*.gs", "*.js"):
    files.extend(root.rglob(ext))

candidates = []
for f in files:
    try:
        text = f.read_text(encoding="utf-8")
    except Exception:
        continue

    has_master = "function runFinalAutomationCycle_" in text
    has_k2 = (
        "/* 1. K2 */" in text
        or "/* 1. K2 · WB OS delta engine */" in text
        or "k2EvolutionFetchAndApply_()" in text
    )
    has_iv = "/* 2. Иваново */" in text
    has_refresh = "refreshAutomationStatusSheet_();" in text

    if has_master and has_k2 and has_iv and has_refresh:
        candidates.append((f, text))

if len(candidates) != 1:
    names = [str(f.relative_to(root)) for f, _ in candidates]
    raise SystemExit(
        "PATCH_FAIL: expected exactly one master source; "
        f"found={len(candidates)} files={names}"
    )

path, text = candidates[0]

start = text.index("function runFinalAutomationCycle_")
end_marker = "\nfunction shouldRunByProperty_"
end = text.find(end_marker, start)
if end == -1:
    raise SystemExit("PATCH_FAIL: cannot bound runFinalAutomationCycle_")

before = text[:start]
func = text[start:end]
after = text[end:]

# Step 1: migrate the K2 block only when it is still legacy.
if "k2EvolutionFetchAndApply_()" not in func:
    pattern = re.compile(
        r"\n\s*/\* 1\. K2 \*/.*?\n\s*/\* 2\. Иваново \*/",
        re.S,
    )

    replacement = r'''
    /* 1. K2 · WB OS delta engine */
    if (forceAll || historyDue || shouldRunByProperty_('K2_LAST_SUCCESS_AT', MASTER_AUTOMATION_CFG.K2_EVERY_MINUTES)) {
      try {
        if (!getMasterConfigurationState_().k2Ready) {
          throw new Error(
            'K2 не настроен: отсутствуют K2_USERNAME/K2_PASSWORD. ' +
            'Запусти один раз saveK2Credentials().'
          );
        }

        var k2Result = k2EvolutionFetchAndApply_();
        k2Updated = Boolean(k2Result && k2Result.changed);

        Logger.log(
          'Master: K2 проверен, позиций: ' +
          (k2Result ? k2Result.itemCount : 0) +
          '; business-state changed=' + k2Updated
        );
      } catch (error) {
        cycleErrors.push('K2: ' + error.message);
        Logger.log(error.stack || error.message);

        try {
          k2EvolutionRecordFailure_(error);
        } catch (diagError) {
          Logger.log('K2 Evolution diagnostics: ' + diagError.message);
        }

        if (getMasterConfigurationState_().telegramReady) {
          notifyK2ApiError_(error);
        }
      }
    }

    /* 2. Иваново */'''

    func, count = pattern.subn(replacement, func, count=1)
    if count != 1:
        raise SystemExit(f"PATCH_FAIL: K2 block replacements={count}")

# Step 2: independently migrate the post-refresh watchdog.
needle = "    refreshAutomationStatusSheet_();"
watchdog = """    refreshAutomationStatusSheet_();

    /*
     * K2 watchdog reads the now-refreshed heartbeat and sends Telegram only
     * when the effective state/fingerprint actually changes.
     */
    try {
      k2EvolutionWatchdogNotify_();
    } catch (watchdogError) {
      Logger.log(
        'K2 Evolution watchdog Telegram: ' +
        (watchdogError.stack || watchdogError.message)
      );
    }"""

if "k2EvolutionWatchdogNotify_();" not in func:
    if func.count(needle) != 1:
        raise SystemExit(
            "PATCH_FAIL: expected one refreshAutomationStatusSheet_ call "
            f"inside master, found={func.count(needle)}"
        )
    func = func.replace(needle, watchdog, 1)

new_text = before + func + after

# Step 3: independently migrate history heartbeat self-healing.
old_history = "buildAutomationStatusRow_('История остатков', props.getProperty('FF_STOCK_HISTORY_LAST_AT'), 1560)"
new_history = "buildAutomationStatusRow_('История остатков', k2EvolutionHistoryLastAt_(props), 1560)"
if old_history in new_text:
    new_text = new_text.replace(old_history, new_history, 1)
elif new_history not in new_text:
    raise SystemExit("PATCH_FAIL: history status source not found")

# Hard post-conditions. A successful patch is fully migrated, not half-done.
required = [
    "k2EvolutionFetchAndApply_()",
    "k2EvolutionRecordFailure_(error)",
    "k2EvolutionWatchdogNotify_();",
    "k2EvolutionHistoryLastAt_(props)",
]
missing = [marker for marker in required if marker not in new_text]
if missing:
    raise SystemExit("PATCH_FAIL: post-condition missing: " + ", ".join(missing))

path.write_text(new_text, encoding="utf-8")

print("PATCH_OK:", path.relative_to(root))
