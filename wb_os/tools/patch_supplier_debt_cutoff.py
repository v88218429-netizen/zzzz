#!/usr/bin/env python3
import pathlib
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


if __name__ == "__main__":
    main()
