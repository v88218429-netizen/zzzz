#!/usr/bin/env python3
import pathlib
import re
import sys


SUPPLIER_OLD = """  orderSheets.forEach(function(sheet) {
    var sheetTotals = collectTotalsFromFfSheet_(sheet, targetKeys);

    Object.keys(sheetTotals).forEach(function(key) {
"""
SUPPLIER_NEW = """  orderSheets.forEach(function(sheet) {
    var sheetTotals = collectTotalsFromFfSheet_(sheet, targetKeys);
    var orderNumber = getOrderNumber_(sheet.getName());

    Object.keys(sheetTotals).forEach(function(key) {
"""

SUPPLIER_DEBT_OLD = """      result[key].plan += sheetTotals[key].plan || 0;
      result[key].debt += sheetTotals[key].debt || 0;
"""
SUPPLIER_DEBT_NEW = """      result[key].plan += sheetTotals[key].plan || 0;

      /*
       * SUPPLIER_DEBT_CUTOFF_ORDER_10
       * Orders 1..10 are operationally closed. Historical under-delivery
       * remains in the source order sheets, but is not a live supplier debt.
       */
      if (orderNumber > 10) {
        result[key].debt += sheetTotals[key].debt || 0;
      }
"""

UO_BLOCK_OLD = """      var item = sheetTotals[key];

      result[key].plan += item.plan || 0;
      result[key].accepted += item.accepted || 0;
      result[key].debt += item.debt || 0;

      if ((item.plan || 0) > 0 || (item.accepted || 0) > 0 || (item.debt || 0) > 0) {
        result[key].orders.push({
          number: orderNumber,
          name: orderName,
          plan: item.plan || 0,
          accepted: item.accepted || 0,
          debt: item.debt || 0
        });
      }
"""
UO_BLOCK_NEW = """      var item = sheetTotals[key];
      var effectiveDebt = orderNumber > 10
        ? (item.debt || 0)
        : 0;

      result[key].plan += item.plan || 0;
      result[key].accepted += item.accepted || 0;

      /*
       * SUPPLIER_DEBT_CUTOFF_ORDER_10
       * Orders 1..10 are considered closed by business rule.
       * Keep their historical plan/acceptance for audit and notes, but never
       * carry their shortage into the live supplier debt.
       */
      result[key].debt += effectiveDebt;

      if ((item.plan || 0) > 0 || (item.accepted || 0) > 0 || effectiveDebt > 0) {
        result[key].orders.push({
          number: orderNumber,
          name: orderName,
          plan: item.plan || 0,
          accepted: item.accepted || 0,
          debt: effectiveDebt
        });
      }
"""

NOTE_OLD = """  var lines = [
    '📦 ЖИВОЙ РАСЧЁТ ДОЛГА ПОСТАВЩИКА',
    'Заказано всего: ' + UO_formatSupplierQty_(totalPlan) + ' шт.',
"""
NOTE_NEW = """  var lines = [
    '📦 ЖИВОЙ РАСЧЁТ ДОЛГА ПОСТАВЩИКА',
    'Заказы 1–10 закрыты и в текущий долг не входят.',
    'Заказано всего: ' + UO_formatSupplierQty_(totalPlan) + ' шт.',
"""

HELPERS = r"""
/*
 * SUPPLIER_ORDER_SYNC_RELIABILITY_V1
 * - no zero-qty product appends;
 * - accepted/debt formulas restored for appended and newly-created rows;
 * - Europa pots automatically require 3 hangers per pot.
 */
function supplierRestoreFfRowFormulasV1_(sheet, rowNumber, headerMap) {
  var acceptedColumn = optionalHeaderColumn_(headerMap, ['Принято ФФ факт, шт']);
  var receipt1 = optionalHeaderColumn_(headerMap, ['1 приход']);
  var receipt2 = optionalHeaderColumn_(headerMap, ['2 приход']);
  var receipt3 = optionalHeaderColumn_(headerMap, ['3 приход']);
  var planColumn = optionalHeaderColumn_(headerMap, ['План, шт', 'План заказ, шт', 'План']);
  var debtColumn = optionalHeaderColumn_(headerMap, ['Долг поставщика']);

  if (
    acceptedColumn !== null &&
    receipt1 !== null &&
    receipt2 !== null &&
    receipt3 !== null
  ) {
    var receiptColumns = [receipt1, receipt2, receipt3];
    var firstReceipt = Math.min.apply(null, receiptColumns);
    var lastReceipt = Math.max.apply(null, receiptColumns);
    var receiptRange =
      columnIndexToLetter_(firstReceipt) + rowNumber + ':' +
      columnIndexToLetter_(lastReceipt) + rowNumber;

    sheet
      .getRange(rowNumber, acceptedColumn + 1)
      .setFormula(
        '=IF(COUNTA(' + receiptRange + ')=0;"";SUM(' + receiptRange + '))'
      )
      .setNumberFormat('0;-0;;@');
  }

  if (planColumn !== null && acceptedColumn !== null && debtColumn !== null) {
    var planCell = columnIndexToLetter_(planColumn) + rowNumber;
    var acceptedCell = columnIndexToLetter_(acceptedColumn) + rowNumber;

    sheet
      .getRange(rowNumber, debtColumn + 1)
      .setFormula(
        '=IF(' + planCell + '="";"";' + planCell + '-' + acceptedCell + ')'
      )
      .setNumberFormat('0;-0;;@');
  }
}

function supplierRestoreAcceptedFormulasV1_(sheet, headerRow, headerMap) {
  var lastRow = sheet.getLastRow();
  if (lastRow <= headerRow) {
    return;
  }

  for (var rowNumber = headerRow + 1; rowNumber <= lastRow; rowNumber++) {
    var productName = String(sheet.getRange(rowNumber, 1).getDisplayValue() || '').trim();
    if (!productName) {
      continue;
    }
    supplierRestoreFfRowFormulasV1_(sheet, rowNumber, headerMap);
  }
}

function supplierApplyHangerDependenciesV1_(sheet, headerRow, headerMap) {
  var planColumn = optionalHeaderColumn_(headerMap, ['План, шт', 'План заказ, шт', 'План']);
  if (planColumn === null) {
    return;
  }

  var lastRow = sheet.getLastRow();
  if (lastRow <= headerRow) {
    return;
  }

  var values = sheet
    .getRange(headerRow + 1, 1, lastRow - headerRow, 1)
    .getDisplayValues();

  var rowByKey = {};
  values.forEach(function(row, index) {
    var name = String(row[0] || '').trim();
    if (!name) {
      return;
    }
    rowByKey[normalizeProductName_(name)] = headerRow + 1 + index;
  });

  var planLetter = columnIndexToLetter_(planColumn);

  function applyDependency(hangerName, potName1, potName2) {
    var hangerRow = rowByKey[normalizeProductName_(hangerName)];
    var potRow1 = rowByKey[normalizeProductName_(potName1)];
    var potRow2 = rowByKey[normalizeProductName_(potName2)];

    if (!hangerRow || !potRow1 || !potRow2) {
      return;
    }

    var potCell1 = planLetter + potRow1;
    var potCell2 = planLetter + potRow2;

    sheet
      .getRange(hangerRow, planColumn + 1)
      .setFormula(
        '=IF((' + potCell1 + '+' + potCell2 + ')=0;"";3*(' +
        potCell1 + '+' + potCell2 + '))'
      )
      .setNumberFormat('0;-0;;@');

    supplierRestoreFfRowFormulasV1_(sheet, hangerRow, headerMap);
  }

  applyDependency(
    'Подвеска для кашпо (47 см), белый НОВИНКА',
    'Кашпо "Европа" №2 (3,5л) белый',
    'Кашпо "Европа" №1 (5л) белый'
  );

  applyDependency(
    'Подвеска для кашпо 46см',
    'Кашпо "Европа" №2 (3,5л) коричневая',
    'Кашпо "Европа" №1 (5л) коричневая'
  );
}

function supplierRepairNewOrderSheetV1_(sheet) {
  var headerRow = findFfHeaderRowByAliases_(sheet, [
    'План, шт',
    'План заказ, шт',
    'План'
  ]);
  var headers = sheet
    .getRange(headerRow, 1, 1, sheet.getLastColumn())
    .getDisplayValues()[0];
  var headerMap = buildHeaderMap_(headers);

  supplierRestoreAcceptedFormulasV1_(sheet, headerRow, headerMap);
  supplierApplyHangerDependenciesV1_(sheet, headerRow, headerMap);
}
"""


def find(root: pathlib.Path, names):
    for name in names:
        p = root / name
        if p.exists():
            return p
    raise SystemExit("not found: " + " / ".join(names))


def patch(path, replacements):
    text = path.read_text(encoding="utf-8")
    changed = False
    for old, new, marker in replacements:
        if marker in text:
            continue
        if old not in text:
            raise SystemExit(f"pattern not found in {path}: {marker}")
        text = text.replace(old, new, 1)
        changed = True
    if changed:
        path.write_text(text, encoding="utf-8")
    print(("patched " if changed else "already patched ") + str(path))


def patch_supplier_reliability(path: pathlib.Path):
    text = path.read_text(encoding="utf-8")
    changed = False

    if "SUPPLIER_FF_EDIT_NO_DEBOUNCE_V1" not in text:
        old = """    if (now - lastRun < 10000) {
      Logger.log('Supplier full sync skipped by debounce: ' + reason);
      return;
    }
"""
        new = """    /* SUPPLIER_FF_EDIT_NO_DEBOUNCE_V1
     * A direct FF edit must never be dropped by the debounce window.
     */
    if (reason !== 'ffEdit' && now - lastRun < 10000) {
      Logger.log('Supplier full sync skipped by debounce: ' + reason);
      return;
    }
"""
        if old not in text:
            raise SystemExit(f"pattern not found in {path}: SUPPLIER_FF_EDIT_NO_DEBOUNCE_V1")
        text = text.replace(old, new, 1)
        changed = True

    if "SUPPLIER_ZERO_ORDER_APPEND_GUARD_V1" not in text:
        pattern = re.compile(
            r"""    var ffRow = rowByKey\[key\];\n\n"""
            r"""    if \(!ffRow\) \{\n"""
            r"""      ffRow = ffSheet\.getLastRow\(\) \+ 1;\n"""
            r"""      ffSheet\.getRange\(ffRow, 1\)\.setValue\(productName\);\n"""
            r"""      rowByKey\[key\] = ffRow;\n"""
            r"""    \}\n"""
        )
        replacement = """    var ffRow = rowByKey[key];

    /* SUPPLIER_ZERO_ORDER_APPEND_GUARD_V1
     * Unknown products with an empty order must not pollute FF sheets.
     */
    if (!ffRow && value <= 0) {
      continue;
    }

    if (!ffRow) {
      ffRow = ffSheet.getLastRow() + 1;

      if (ffRow > ffSheet.getMaxRows()) {
        ffSheet.insertRowsAfter(ffSheet.getMaxRows(), 50);
      }

      ffSheet.getRange(ffRow, 1).setValue(productName);
      rowByKey[key] = ffRow;
      supplierRestoreFfRowFormulasV1_(ffSheet, ffRow, headerMap);
    }
"""
        text2, count = pattern.subn(replacement, text, count=1)
        if count != 1:
            raise SystemExit(f"pattern not found in {path}: SUPPLIER_ZERO_ORDER_APPEND_GUARD_V1")
        text = text2
        changed = True

    if "SUPPLIER_HANGER_FAST_REPAIR_V1" not in text:
        old = """  updateFfPlanTotalFormula_(ffSheet, ffHeaderRow, planColumn);
}
"""
        new = """  /* SUPPLIER_HANGER_FAST_REPAIR_V1 */
  supplierApplyHangerDependenciesV1_(ffSheet, ffHeaderRow, headerMap);
  updateFfPlanTotalFormula_(ffSheet, ffHeaderRow, planColumn);
}
"""
        if old not in text:
            raise SystemExit(f"pattern not found in {path}: SUPPLIER_HANGER_FAST_REPAIR_V1")
        text = text.replace(old, new, 1)
        changed = True

    if "SUPPLIER_NEW_ORDER_REPAIR_V1" not in text:
        old = """  clearFfOrderSheetForNewOrder_(copiedSheet);

  Logger.log('Создан лист ФФУ: ' + newName + ', шаблон: ' + templateSheet.getName());
"""
        new = """  clearFfOrderSheetForNewOrder_(copiedSheet);

  /* SUPPLIER_NEW_ORDER_REPAIR_V1 */
  supplierRepairNewOrderSheetV1_(copiedSheet);

  Logger.log('Создан лист ФФУ: ' + newName + ', шаблон: ' + templateSheet.getName());
"""
        if old not in text:
            raise SystemExit(f"pattern not found in {path}: SUPPLIER_NEW_ORDER_REPAIR_V1")
        text = text.replace(old, new, 1)
        changed = True

    if "SUPPLIER_ORDER_SYNC_RELIABILITY_V1" not in text:
        marker = "\nfunction refreshOnlySummaryRowsFromFf_"
        pos = text.find(marker)
        if pos == -1:
            raise SystemExit(f"pattern not found in {path}: helper insertion")
        text = text[:pos] + "\n" + HELPERS + text[pos:]
        changed = True

    if changed:
        path.write_text(text, encoding="utf-8")
        print("patched reliability " + str(path))
    else:
        print("already patched reliability " + str(path))


def main():
    if len(sys.argv) != 2:
        raise SystemExit("usage: patch_supplier_debt_cutoff.py <source_root>")

    root = pathlib.Path(sys.argv[1]).resolve()
    supplier = find(root, ["Supplier_Orders.gs", "Supplier_Orders.gs.js", "Supplier_Orders.js"])
    uo = find(root, ["учет заказов.gs", "учет заказов.js"])

    patch(supplier, [
        (SUPPLIER_OLD, SUPPLIER_NEW, "var orderNumber = getOrderNumber_(sheet.getName());"),
        (SUPPLIER_DEBT_OLD, SUPPLIER_DEBT_NEW, "SUPPLIER_DEBT_CUTOFF_ORDER_10"),
    ])

    patch(uo, [
        (UO_BLOCK_OLD, UO_BLOCK_NEW, "SUPPLIER_DEBT_CUTOFF_ORDER_10"),
        (NOTE_OLD, NOTE_NEW, "Заказы 1–10 закрыты и в текущий долг не входят."),
    ])

    patch_supplier_reliability(supplier)


if __name__ == "__main__":
    main()
