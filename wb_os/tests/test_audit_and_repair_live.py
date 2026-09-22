#!/usr/bin/env python3
import pathlib
import subprocess
import sys
import tempfile

AUDITOR = (
    pathlib.Path(__file__).resolve().parents[1]
    / "tools"
    / "audit_and_repair_live.py"
)

MASTER = r"""
function runFinalAutomationCycle_() {}
function findSummaryHeaderRow_() { return 1; }
function hasHeaderAlias_() { return true; }
function requireHeaderColumn_() { return 1; }
function optionalHeaderColumn_() { return 1; }
function columnIndexToLetter_() { return 'A'; }
"""

K2_STABLE = r"""
function getK2WarehouseItems_() { return []; }
function getK2CredentialsWithFallback_() { return {}; }
function updateK2AutomationStatus_() {}
function syncK2StocksOnly() { return 'legacy-only'; }
function syncK2StocksAndNotify() { return 'legacy-notify'; }
function parseNumber_() { return 1; }
"""

K2_OLD = r"""
function getK2WarehouseItems_() { return []; }
function parseNumber_() { return 2; }
"""

EVOLUTION = r"""
function k2EvolutionFetchAndApply_() {}
"""

PRICE = r"""
function syncWbPublicCustomerPricesV4_() {}
"""

SPP = r"""
function sppMonitorScheduledTick() {
  sppRunMonitor_(true);
}
function sppRunMonitor_(sendNotifications) {
  return sendNotifications;
}
"""

LEGACY_PRICE = r"""
function updateWbPricesFromLinks() {
  var sheet = SpreadsheetApp.getActiveSpreadsheet().getActiveSheet();
  sheet.getRange(1, 11).setValue(123);
}

function columnToIndex_(col) {
  return 11;
}
"""

SUPPLIER = r"""
var SUPPLIER_ORDERS_CFG = {};
function syncAllSupplierOrdersNow() {
  return findSummaryHeaderRow_();
}
function findSummaryHeaderRow_() { return 2; }
function hasHeaderAlias_() { return false; }
function requireHeaderColumn_() { return 2; }
function optionalHeaderColumn_() { return 2; }
function columnIndexToLetter_() { return 'B'; }
function parseNumber_() { return 3; }
"""

def run(root):
    return subprocess.run(
        [sys.executable, str(AUDITOR), str(root)],
        text=True,
        capture_output=True,
    )

def main():
    with tempfile.TemporaryDirectory() as td:
        root = pathlib.Path(td)
        (root / "Master.js").write_text(MASTER, encoding="utf-8")
        (root / "K2_stable.js").write_text(K2_STABLE, encoding="utf-8")
        (root / "K2_old.js").write_text(K2_OLD, encoding="utf-8")
        (root / "Evolution.js").write_text(EVOLUTION, encoding="utf-8")
        (root / "Price.js").write_text(PRICE, encoding="utf-8")
        (root / "Spp.js").write_text(SPP, encoding="utf-8")
        (root / "Supplier.js").write_text(SUPPLIER, encoding="utf-8")
        (root / "LegacyPrice.js").write_text(LEGACY_PRICE, encoding="utf-8")

        p = run(root)
        if p.returncode != 0:
            raise AssertionError(
                f"audit failed\nstdout={p.stdout}\nstderr={p.stderr}"
            )

        assert "AUDIT_OK" in p.stdout
        assert "legacy duplicate K2 core disabled" in (
            root / "K2_old.js"
        ).read_text(encoding="utf-8")

        k2_stable = (root / "K2_stable.js").read_text(encoding="utf-8")
        assert "WB_OS_MASTER_TRIGGER_BOOTSTRAP_syncK2StocksOnly" in k2_stable
        assert "WB_OS_MASTER_TRIGGER_BOOTSTRAP_syncK2StocksAndNotify" in k2_stable
        assert "ensureFinalAutomationTrigger_();" in k2_stable

        spp = (root / "Spp.js").read_text(encoding="utf-8")
        assert "WB_OS_MASTER_TRIGGER_BOOTSTRAP_sppMonitorScheduledTick" in spp
        assert "ensureFinalAutomationTrigger_();" in spp
        assert "sppRunMonitor_(true);" in spp

        supplier = (root / "Supplier.js").read_text(encoding="utf-8")
        legacy_price = (root / "LegacyPrice.js").read_text(encoding="utf-8")
        assert "return forceSyncWbPublicCustomerPricesV4();" in legacy_price
        assert "getActiveSheet()" not in legacy_price

        supplier = (root / "Supplier.js").read_text(encoding="utf-8")
        assert "function supplierFindSummaryHeaderRow_" in supplier
        assert "function supplierParseNumber_" in supplier
        assert "return supplierFindSummaryHeaderRow_();" in supplier

        # Second pass must stay valid/idempotent.
        p2 = run(root)
        if p2.returncode != 0:
            raise AssertionError(
                f"second audit failed\nstdout={p2.stdout}\nstderr={p2.stderr}"
            )

    # Ambiguous multiple "stable" K2 cores must fail closed.
    with tempfile.TemporaryDirectory() as td:
        root = pathlib.Path(td)
        (root / "Master.js").write_text(MASTER, encoding="utf-8")
        (root / "K2_a.js").write_text(K2_STABLE, encoding="utf-8")
        (root / "K2_b.js").write_text(K2_STABLE, encoding="utf-8")
        (root / "Evolution.js").write_text(EVOLUTION, encoding="utf-8")
        (root / "Price.js").write_text(PRICE, encoding="utf-8")

        p = run(root)
        assert p.returncode != 0
        assert "canonical core is ambiguous" in (p.stdout + p.stderr)

    # A duplicate K2 file with unique behavior must not be erased.
    with tempfile.TemporaryDirectory() as td:
        root = pathlib.Path(td)
        (root / "Master.js").write_text(MASTER, encoding="utf-8")
        (root / "K2_stable.js").write_text(K2_STABLE, encoding="utf-8")
        (root / "K2_old.js").write_text(
            K2_OLD + "\nfunction uniqueLegacyOnly_() {}\n",
            encoding="utf-8",
        )
        (root / "Evolution.js").write_text(EVOLUTION, encoding="utf-8")
        (root / "Price.js").write_text(PRICE, encoding="utf-8")

        p = run(root)
        assert p.returncode != 0
        assert "cannot be disabled safely" in (p.stdout + p.stderr)
        assert "uniqueLegacyOnly_" in (p.stdout + p.stderr)

    # Duplicate top-level config/global variables must also fail closed.
    with tempfile.TemporaryDirectory() as td:
        root = pathlib.Path(td)
        (root / "Master.js").write_text(
            MASTER + "\nvar SAME_CFG = {};\n",
            encoding="utf-8",
        )
        (root / "K2_stable.js").write_text(K2_STABLE, encoding="utf-8")
        (root / "Evolution.js").write_text(EVOLUTION, encoding="utf-8")
        (root / "Price.js").write_text(
            PRICE + "\nvar SAME_CFG = {};\n",
            encoding="utf-8",
        )

        p = run(root)
        assert p.returncode != 0
        assert "duplicate top-level globals remain" in (p.stdout + p.stderr)
        assert "SAME_CFG" in (p.stdout + p.stderr)

    print("WB OS whole-project audit tests: OK")

if __name__ == "__main__":
    main()
