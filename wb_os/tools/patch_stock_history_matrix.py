#!/usr/bin/env python3
import pathlib
import sys


SHEET_BLOCK = """  var sheet = ss.getSheetByName(MASTER_AUTOMATION_CFG.HISTORY_SHEET);
  if (!sheet) {
    sheet = ss.insertSheet(MASTER_AUTOMATION_CFG.HISTORY_SHEET);
  }
"""

DEDUP_BLOCK = """  /* WB_OS_DAILY_MATRIX_DEDUP_V1 */
  if (
    !forceDuplicate &&
    typeof ffDailyHistoryHasDate_ === 'function' &&
    ffDailyHistoryHasDate_(sheet, today)
  ) {
    props.setProperty('FF_STOCK_HISTORY_LAST_DAY', today);
    props.setProperty('FF_STOCK_HISTORY_LAST_AT', now.toISOString());

    try {
      if (typeof refreshDailyStockMatrix_ === 'function') {
        refreshDailyStockMatrix_();
      }
    } catch (matrixExistingError) {
      Logger.log(
        'Остатки ФФ по дням · refresh existing day: ' +
        (matrixExistingError.stack || matrixExistingError.message)
      );
    }

    Logger.log(
      'История остатков: найден существующий снимок за ' +
      today +
      '; дубль не создаём.'
    );
    return false;
  }
"""

PROP_LINE = """  props.setProperty('FF_STOCK_HISTORY_LAST_ROWS', String(rows.length));
"""

REFRESH_BLOCK = """  /* WB_OS_DAILY_MATRIX_REFRESH_V1 */
  try {
    if (typeof refreshDailyStockMatrix_ === 'function') {
      refreshDailyStockMatrix_();
    }
  } catch (matrixError) {
    /*
     * Матрица — только представление. Ошибка её перестроения не должна
     * отменять уже сохранённый сырой дневной снимок.
     */
    Logger.log(
      'Остатки ФФ по дням · refresh error: ' +
      (matrixError.stack || matrixError.message)
    );
  }
"""


def find_core(root: pathlib.Path) -> pathlib.Path:
    candidates = [
        root / "Код.gs",
        root / "Код.js",
    ]
    for p in candidates:
        if p.exists():
            return p
    raise SystemExit("Код.gs/Код.js not found")


def main():
    if len(sys.argv) != 2:
        raise SystemExit("usage: patch_stock_history_matrix.py <source_root>")

    root = pathlib.Path(sys.argv[1]).resolve()
    path = find_core(root)
    text = path.read_text(encoding="utf-8")

    if "WB_OS_DAILY_MATRIX_DEDUP_V1" not in text:
        if SHEET_BLOCK not in text:
            raise SystemExit("history sheet block not found")
        text = text.replace(
            SHEET_BLOCK,
            SHEET_BLOCK + "\n" + DEDUP_BLOCK,
            1,
        )

    if "WB_OS_DAILY_MATRIX_REFRESH_V1" not in text:
        if PROP_LINE not in text:
            raise SystemExit("history property marker not found")
        text = text.replace(
            PROP_LINE,
            PROP_LINE + "\n" + REFRESH_BLOCK,
            1,
        )

    path.write_text(text, encoding="utf-8")
    print(f"patched {path}")


if __name__ == "__main__":
    main()
