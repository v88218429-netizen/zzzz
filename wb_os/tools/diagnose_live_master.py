#!/usr/bin/env python3
"""Remove the dangling Ozon hook and expose Master errors in the status sheet.

Only the Master source file is changed. The patch does not run, reschedule, or
modify K2, the FBS scanner, or any source task.
"""

from __future__ import annotations

import pathlib
import sys


MARKER = "WB_OS_MASTER_ERROR_DIAGNOSTICS_V1"
INSERT_BEFORE = "    sheet.autoResizeColumns(1, 4);"
BLOCK = r"""    /* WB_OS_MASTER_ERROR_DIAGNOSTICS_V1 */
    try {
      var masterErrorText = String(
        props.getProperty('MASTER_LAST_ERROR') || ''
      ).trim();

      masterErrorText = masterErrorText
        .replace(/(Bearer\s+)[^\s]+/gi, '$1[скрыто]')
        .replace(/((?:token|password|secret|cookie|authorization|api[_ -]?key|client[_ -]?secret)\s*[:=]\s*)[^,\s;]+/gi, '$1[скрыто]')
        .slice(0, 1000);

      sheet.getRange(7, 5).setValue(
        masterErrorText
          ? 'Последняя ошибка Master: ' + masterErrorText
          : 'Ошибок Master нет'
      );

      var masterTriggerCount = ScriptApp
        .getProjectTriggers()
        .filter(function(trigger) {
          return trigger.getHandlerFunction() === 'finalAutomationTick';
        })
        .length;

      sheet.getRange(8, 5).setValue(
        'Триггеров finalAutomationTick: ' + masterTriggerCount
      );
    } catch (diagnosticError) {
      Logger.log(
        'WB OS Master diagnostic display failed: ' +
        String(diagnosticError && diagnosticError.message
          ? diagnosticError.message
          : diagnosticError)
      );
    }
"""
MISSING_OZON_HOOK = r"""    /* Ozon LIVE radar · ensure dedicated 1-minute trigger */
    try {
      ensureOzonRadarServerTrigger_();
    } catch (ozonRadarTriggerError) {
      cycleErrors.push(
        'Ozon radar trigger: ' + ozonRadarTriggerError.message
      );
      Logger.log(
        'Ozon radar trigger: ' +
        (ozonRadarTriggerError.stack || ozonRadarTriggerError.message)
      );
    }
"""
OZON_COMMENT = "    /* Ozon Radar bridge is not present in this Apps Script project. */\n\n"


def patch_project(root: pathlib.Path) -> pathlib.Path:
    candidates: list[tuple[pathlib.Path, str]] = []
    for suffix in ("*.gs", "*.js"):
        for path in root.rglob(suffix):
            try:
                content = path.read_text(encoding="utf-8")
            except (OSError, UnicodeError):
                continue
            if "function refreshAutomationStatusSheet_()" in content:
                candidates.append((path, content))

    if len(candidates) != 1:
        names = [str(path.relative_to(root)) for path, _ in candidates]
        raise SystemExit(
            "MASTER_DIAGNOSTICS_FAIL: expected exactly one status renderer; "
            f"found={len(candidates)} files={names}"
        )

    path, content = candidates[0]
    marker_count = content.count(MARKER)
    if marker_count > 1:
        raise SystemExit("MASTER_DIAGNOSTICS_FAIL: duplicate marker")

    changed = False
    if MISSING_OZON_HOOK in content:
        content = content.replace(MISSING_OZON_HOOK, OZON_COMMENT, 1)
        changed = True
    elif "ensureOzonRadarServerTrigger_();" in content and OZON_COMMENT not in content:
        raise SystemExit(
            "MASTER_DIAGNOSTICS_FAIL: Ozon hook found but its surrounding block differs"
        )

    start = content.index("function refreshAutomationStatusSheet_()")
    end = content.find("\nfunction ", start + 1)
    if end < 0:
        end = len(content)
    function = content[start:end]
    if marker_count == 0:
        if function.count(INSERT_BEFORE) != 1:
            raise SystemExit(
                "MASTER_DIAGNOSTICS_FAIL: status renderer insertion point is ambiguous"
            )
        function = function.replace(INSERT_BEFORE, BLOCK + "\n" + INSERT_BEFORE, 1)
        changed = True

    updated = content[:start] + function + content[end:]
    if changed:
        path.write_text(updated, encoding="utf-8")
        print("MASTER_DIAGNOSTICS_PATCHED:", path.relative_to(root))
    else:
        print("MASTER_DIAGNOSTICS_ALREADY_PRESENT:", path.relative_to(root))
    return path


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: diagnose_live_master.py <apps-script-source-root>")
    patch_project(pathlib.Path(sys.argv[1]).resolve())
