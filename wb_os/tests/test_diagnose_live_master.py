#!/usr/bin/env python3
import pathlib
import subprocess
import sys
import tempfile

SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "tools" / "diagnose_live_master.py"

SOURCE = r"""
function runFinalAutomationCycle_(forceTelegram, forceAll) {
  var cycleErrors = [];
  cycleErrors.push('K2: do not rewrite this cycle');
}

function refreshAutomationStatusSheet_() {
  try {
    var props = PropertiesService.getScriptProperties();
    var rows = [['Модуль'], ['K2'], ['Иваново'], ['WB + Ozon'], ['Supplier'], ['История'], ['Master']];
    sheet.getRange(1, 1, rows.length, 4).setValues(rows);
    sheet.setFrozenRows(1);
    sheet.autoResizeColumns(1, 4);
  } catch (error) {
    Logger.log('status error: ' + error.message);
  }
}
"""


def patch(root: pathlib.Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), str(root)],
        text=True,
        capture_output=True,
    )


def main() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = pathlib.Path(td)
        master = root / "Master.gs"
        master.write_text(SOURCE, encoding="utf-8")

        result = patch(root)
        assert result.returncode == 0, result.stdout + result.stderr
        first = master.read_text(encoding="utf-8")
        assert first.count("WB_OS_MASTER_ERROR_DIAGNOSTICS_V1") == 1
        assert "MASTER_LAST_ERROR" in first
        assert "finalAutomationTick" in first
        assert "sheet.getRange(7, 5)" in first
        assert "sheet.getRange(8, 5)" in first
        assert "cycleErrors.push('K2: do not rewrite this cycle')" in first
        with tempfile.TemporaryDirectory() as syntax_dir:
            syntax_file = pathlib.Path(syntax_dir) / "Master.js"
            syntax_file.write_text(first, encoding="utf-8")
            check = subprocess.run(
                ["node", "--check", str(syntax_file)],
                text=True,
                capture_output=True,
            )
            assert check.returncode == 0, check.stdout + check.stderr

        result = patch(root)
        assert result.returncode == 0, result.stdout + result.stderr
        assert master.read_text(encoding="utf-8") == first

        duplicate = root / "Duplicate.gs"
        duplicate.write_text(first, encoding="utf-8")
        result = patch(root)
        assert result.returncode != 0
        assert "expected exactly one status renderer" in (result.stdout + result.stderr)

    print("WB OS master diagnostics tests: OK")


if __name__ == "__main__":
    main()
