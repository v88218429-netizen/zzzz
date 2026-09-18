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

    if (
        "function runFinalAutomationCycle_" in text
        and "/* 1. K2 */" in text
        and "/* 2. Иваново */" in text
        and "refreshAutomationStatusSheet_();" in text
    ):
        candidates.append((f, text))

if len(candidates) != 1:
    names = [str(f.relative_to(root)) for f, _ in candidates]
    raise SystemExit(
        "PATCH_FAIL: expected exactly one master source; "
        f"found={len(candidates)} files={names}"
    )

path, text = candidates[0]

if "k2EvolutionFetchAndApply_()" in text:
    print("PATCH_OK: master already uses K2 Evolution:", path.relative_to(root))
    raise SystemExit(0)

start = text.index("function runFinalAutomationCycle_")
end_marker = "\nfunction shouldRunByProperty_"
end = text.find(end_marker, start)
if end == -1:
    raise SystemExit("PATCH_FAIL: cannot bound runFinalAutomationCycle_")

before = text[:start]
func = text[start:end]
after = text[end:]

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

func2, count = pattern.subn(replacement, func, count=1)
if count != 1:
    raise SystemExit(f"PATCH_FAIL: K2 block replacements={count}")

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

if "k2EvolutionWatchdogNotify_();" not in func2:
    if func2.count(needle) != 1:
        raise SystemExit(
            "PATCH_FAIL: expected one refreshAutomationStatusSheet_ call "
            f"inside master, found={func2.count(needle)}"
        )
    func2 = func2.replace(needle, watchdog, 1)

new_text = before + func2 + after
path.write_text(new_text, encoding="utf-8")

print("PATCH_OK:", path.relative_to(root))
