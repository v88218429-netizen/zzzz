#!/usr/bin/env python3
import pathlib
import subprocess
import sys
import tempfile

PATCHER = pathlib.Path(__file__).resolve().parents[1] / "tools" / "patch_live_master.py"

LEGACY = r"""
function runFinalAutomationCycle_(forceTelegram, forceAll) {
  var cycleErrors = [];
  var k2Updated = false;
  var historyDue = shouldTakeDailyStockSnapshot_();

  try {
    /* 1. K2 */
    if (forceAll || historyDue || shouldRunByProperty_('K2_LAST_SUCCESS_AT', MASTER_AUTOMATION_CFG.K2_EVERY_MINUTES)) {
      try {
        var items = getK2WarehouseItems_();
        if (!items || !items.length) {
          throw new Error('К2 вернул пустой список остатков.');
        }
        writeK2StocksToSheet_(items);
        saveK2SyncSuccess_(items.length);
        k2Updated = true;
      } catch (error) {
        cycleErrors.push('K2: ' + error.message);
        if (getMasterConfigurationState_().telegramReady) {
          notifyK2ApiError_(error);
        }
      }
    }

    /* 2. Иваново */
    if (forceAll) {
      exportFulfilmentStocks();
    }

    /* 5. Один дневной снимок остатков К2 + Иваново около 12:00 МСК. */
    if (historyDue) {
      appendDailyStockHistory_(false);
    }

    saveMasterCycleResult_(cycleErrors);
    refreshAutomationStatusSheet_();
  } finally {
    releaseMasterRunGuard_();
  }
}

function getMasterConfigurationState_() {
  var props = PropertiesService.getScriptProperties();
  return {
    k2Ready: Boolean(
      String(props.getProperty('K2_USERNAME') || '').trim() &&
      String(props.getProperty('K2_PASSWORD') || '')
    ),
    telegramReady: true
  };
}

function shouldRunByProperty_(propertyName, intervalMinutes) {
  return true;
}

function refreshAutomationStatusSheet_() {
  var props = PropertiesService.getScriptProperties();
  var row = buildAutomationStatusRow_('История остатков', props.getProperty('FF_STOCK_HISTORY_LAST_AT'), 1560);
}
"""

REQUIRED = [
    "k2EvolutionFetchAndApply_()",
    "k2EvolutionRecordFailure_(error)",
    "k2EvolutionWatchdogNotify_();",
    "k2EvolutionHistoryLastAt_(props)",
    "syncWbPublicCustomerPricesV4_(forceAll);",
    "wbPriceV4RecordFailure_(priceError)",
    "function wbOsEnsureMasterTrigger()",
    "historyK2Fresh = true;",
    "historyIvanovoFresh = true;",
    "if (historyDue && historyK2Fresh && historyIvanovoFresh) {",
]

def run_patch(root: pathlib.Path):
    p = subprocess.run(
        [sys.executable, str(PATCHER), str(root)],
        text=True,
        capture_output=True,
    )
    if p.returncode != 0:
        raise AssertionError(
            f"patch failed rc={p.returncode}\nstdout={p.stdout}\nstderr={p.stderr}"
        )

def main():
    with tempfile.TemporaryDirectory() as td:
        root = pathlib.Path(td)
        master = root / "Master.gs"
        master.write_text(LEGACY, encoding="utf-8")

        # First migration.
        run_patch(root)
        once = master.read_text(encoding="utf-8")
        for marker in REQUIRED:
            assert marker in once, marker
        assert "writeK2StocksToSheet_(items);" not in once
        assert once.count("k2EvolutionWatchdogNotify_();") == 1
        assert "K2_SESSION_COOKIE" in once
        assert "K2_CLIENT_ID" in once
        master_start = once.index("function runFinalAutomationCycle_")
        master_end = once.index("\nfunction getMasterConfigurationState_", master_start)
        master_text = once[master_start:master_end]
        assert "getMasterConfigurationState_().k2Ready" not in master_text
        assert "MASTER_TRIGGER_OK" in once

        # Second migration must be a true no-op, not a failure or duplicate.
        run_patch(root)
        twice = master.read_text(encoding="utf-8")
        assert twice == once
        assert twice.count("k2EvolutionWatchdogNotify_();") == 1

        # A project with more than one candidate must fail closed.
        (root / "Duplicate.gs").write_text(once, encoding="utf-8")
        p = subprocess.run(
            [sys.executable, str(PATCHER), str(root)],
            text=True,
            capture_output=True,
        )
        assert p.returncode != 0
        assert "expected exactly one master source" in (p.stderr + p.stdout)

    # A K2_SESSION_COOKIE string elsewhere in the file must NOT satisfy
    # the readiness post-condition when getMasterConfigurationState_ itself
    # is not session-aware.
    with tempfile.TemporaryDirectory() as td:
        root = pathlib.Path(td)
        bad = LEGACY.replace(
            """k2Ready: Boolean(
      String(props.getProperty('K2_USERNAME') || '').trim() &&
      String(props.getProperty('K2_PASSWORD') || '')
    ),""",
            """k2Ready: Boolean(
      String(props.getProperty('K2_USERNAME') || '').trim()
    ),"""
        )
        bad += """
function unrelatedHelper_() {
  return 'K2_SESSION_COOKIE K2_CLIENT_ID';
}
"""
        master = root / "Master.gs"
        master.write_text(bad, encoding="utf-8")

        p = subprocess.run(
            [sys.executable, str(PATCHER), str(root)],
            text=True,
            capture_output=True,
        )
        assert p.returncode != 0
        assert "session-aware readiness missing inside" in (
            p.stderr + p.stdout
        )

    print("WB OS patcher tests: OK")

if __name__ == "__main__":
    main()
