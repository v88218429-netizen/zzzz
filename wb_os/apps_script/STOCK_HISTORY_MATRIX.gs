/**
 * WB OS · daily fulfilment stock matrix
 *
 * Source: "История остатков ФФ".
 * View: one row per fulfilment/article, one column per day, plus SPARKLINE.
 *
 * Metric in day columns: "Доступно" (fact minus reserve / source available).
 * If legacy history contains several snapshots for one day, the value nearest
 * to 12:00 Moscow time is used. The raw history is never deleted or rewritten.
 */
var FF_DAILY_MATRIX_CFG = {
  VERSION: '1.0.0',
  HISTORY_SHEET: 'История остатков ФФ',
  MATRIX_SHEET: 'Остатки ФФ по дням',
  TIMEZONE: 'Europe/Moscow',
  HEADER_COLUMNS: 4
};


function refreshDailyStockMatrixNow() {
  refreshDailyStockMatrix_();
  return 'FF_DAILY_MATRIX_OK';
}


function refreshDailyStockMatrix_() {
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var history = ss.getSheetByName(FF_DAILY_MATRIX_CFG.HISTORY_SHEET);

  if (!history || history.getLastRow() < 2) {
    throw new Error('История остатков ФФ пуста.');
  }

  var values = history
    .getRange(2, 1, history.getLastRow() - 1, 10)
    .getDisplayValues();

  var datesMap = {};
  var itemMap = {};
  var chosen = {};

  for (var i = 0; i < values.length; i++) {
    var row = values[i];
    var date = String(row[0] || '').trim();
    var snapshotAt = String(row[1] || '').trim();
    var fulfilment = String(row[2] || '').trim();
    var sku = String(row[3] || '').trim();
    var barcode = String(row[4] || '').trim();
    var name = String(row[5] || '').trim();
    var available = ffDailyParseNumber_(row[8]);

    if (!date || !fulfilment) {
      continue;
    }

    var article = sku || barcode || name;
    if (!article) {
      continue;
    }

    var key = ffDailyItemKey_(fulfilment, article, name);
    var pickKey = key + '\u001e' + date;
    var distance = ffDailyNoonDistanceSeconds_(snapshotAt);

    datesMap[date] = true;

    if (!itemMap[key]) {
      itemMap[key] = {
        fulfilment: fulfilment,
        article: article,
        name: name,
        latestDate: date,
        latestSnapshotAt: snapshotAt
      };
    } else if (
      date > itemMap[key].latestDate ||
      (
        date === itemMap[key].latestDate &&
        snapshotAt > itemMap[key].latestSnapshotAt
      )
    ) {
      itemMap[key].article = article;
      itemMap[key].name = name || itemMap[key].name;
      itemMap[key].latestDate = date;
      itemMap[key].latestSnapshotAt = snapshotAt;
    }

    if (
      !chosen[pickKey] ||
      distance < chosen[pickKey].distance ||
      (
        distance === chosen[pickKey].distance &&
        snapshotAt > chosen[pickKey].snapshotAt
      )
    ) {
      chosen[pickKey] = {
        distance: distance,
        snapshotAt: snapshotAt,
        available: available
      };
    }
  }

  var dates = Object.keys(datesMap).sort();
  var keys = Object.keys(itemMap).sort(function(a, b) {
    var x = itemMap[a];
    var y = itemMap[b];

    var ffCmp = x.fulfilment.localeCompare(y.fulfilment, 'ru');
    if (ffCmp !== 0) return ffCmp;

    var nameCmp = String(x.name || '').localeCompare(
      String(y.name || ''),
      'ru'
    );
    if (nameCmp !== 0) return nameCmp;

    return String(x.article || '').localeCompare(
      String(y.article || ''),
      'ru'
    );
  });

  var sheet = ss.getSheetByName(FF_DAILY_MATRIX_CFG.MATRIX_SHEET);
  if (!sheet) {
    sheet = ss.insertSheet(FF_DAILY_MATRIX_CFG.MATRIX_SHEET);
  }

  var totalColumns = FF_DAILY_MATRIX_CFG.HEADER_COLUMNS + dates.length;
  var totalRows = Math.max(2, keys.length + 1);

  if (sheet.getMaxColumns() < totalColumns) {
    sheet.insertColumnsAfter(
      sheet.getMaxColumns(),
      totalColumns - sheet.getMaxColumns()
    );
  }

  if (sheet.getMaxRows() < totalRows) {
    sheet.insertRowsAfter(
      sheet.getMaxRows(),
      totalRows - sheet.getMaxRows()
    );
  }

  sheet.clear();

  var headers = [
    'Фулфилмент',
    'Артикул',
    'Название',
    'Динамика'
  ];

  for (var d = 0; d < dates.length; d++) {
    headers.push(ffDailyDisplayDate_(dates[d]));
  }

  sheet
    .getRange(1, 1, 1, headers.length)
    .setValues([headers])
    .setFontWeight('bold')
    .setBackground('#4285f4')
    .setFontColor('#ffffff')
    .setHorizontalAlignment('center');

  var output = [];

  for (var k = 0; k < keys.length; k++) {
    var itemKey = keys[k];
    var item = itemMap[itemKey];
    var out = [
      item.fulfilment,
      item.article,
      item.name,
      ''
    ];

    for (var j = 0; j < dates.length; j++) {
      var selected = chosen[itemKey + '\u001e' + dates[j]];
      out.push(selected ? selected.available : '');
    }

    output.push(out);
  }

  if (output.length) {
    sheet
      .getRange(2, 1, output.length, totalColumns)
      .setValues(output);

    var lastDateColumn = ffDailyColumnLetter_(totalColumns);
    var sparklineFormulas = [];

    for (var r = 0; r < output.length; r++) {
      var sheetRow = r + 2;
      sparklineFormulas.push([
        '=SPARKLINE(E' + sheetRow + ':' +
        lastDateColumn + sheetRow + ')'
      ]);
    }

    sheet
      .getRange(2, 4, sparklineFormulas.length, 1)
      .setFormulas(sparklineFormulas);

    sheet
      .getRange(2, 5, output.length, dates.length)
      .setNumberFormat('0');
  }

  sheet.setFrozenRows(1);
  sheet.setFrozenColumns(4);

  sheet.setColumnWidth(1, 100);
  sheet.setColumnWidth(2, 150);
  sheet.setColumnWidth(3, 390);
  sheet.setColumnWidth(4, 180);

  if (dates.length) {
    sheet.setColumnWidths(5, dates.length, 72);
  }

  sheet.getRange(1, 4).setNote(
    'Спарклайн по доступному остатку за все сохранённые дни.'
  );

  if (dates.length) {
    sheet.getRange(1, 5, 1, dates.length).setNotes([
      dates.map(function(date) {
        return 'Снимок доступного остатка около 12:00 МСК за ' + date + '.';
      })
    ]);
  }

  var oldFilter = sheet.getFilter();
  if (oldFilter) {
    oldFilter.remove();
  }

  if (output.length) {
    sheet
      .getRange(1, 1, output.length + 1, totalColumns)
      .createFilter();
  }

  PropertiesService
    .getScriptProperties()
    .setProperty('FF_DAILY_MATRIX_LAST_REFRESH_AT', new Date().toISOString());

  Logger.log(
    'Остатки ФФ по дням: строк=' +
    output.length +
    '; дней=' +
    dates.length
  );
}


function ffDailyHistoryHasDate_(historySheet, dateText) {
  if (!historySheet || historySheet.getLastRow() < 2) {
    return false;
  }

  var lastRow = historySheet.getLastRow();
  var rowsToScan = Math.min(lastRow - 1, 1000);
  var startRow = lastRow - rowsToScan + 1;
  var dates = historySheet
    .getRange(startRow, 1, rowsToScan, 1)
    .getDisplayValues();

  for (var i = dates.length - 1; i >= 0; i--) {
    var value = String(dates[i][0] || '').trim();
    if (value === dateText) {
      return true;
    }
    if (value && value < dateText) {
      break;
    }
  }

  return false;
}


function ffDailyItemKey_(fulfilment, article, name) {
  var normalizedArticle = String(article || '')
    .trim()
    .toLowerCase();

  if (/^0*\d+$/.test(normalizedArticle)) {
    normalizedArticle = normalizedArticle.replace(/^0+(?=\d)/, '');
  }

  if (!normalizedArticle) {
    normalizedArticle = String(name || '')
      .trim()
      .toLowerCase()
      .replace(/ё/g, 'е')
      .replace(/\s+/g, ' ');
  }

  return (
    String(fulfilment || '').trim().toLowerCase() +
    '\u001f' +
    normalizedArticle
  );
}


function ffDailyNoonDistanceSeconds_(snapshotAt) {
  var text = String(snapshotAt || '').trim();
  var match = text.match(/(\d{1,2}):(\d{2}):(\d{2})$/);

  if (!match) {
    return 999999999;
  }

  var seconds =
    Number(match[1]) * 3600 +
    Number(match[2]) * 60 +
    Number(match[3]);

  return Math.abs(seconds - 12 * 3600);
}


function ffDailyDisplayDate_(dateText) {
  var match = String(dateText || '').match(
    /^(\d{4})-(\d{2})-(\d{2})$/
  );

  if (!match) {
    return String(dateText || '');
  }

  return match[3] + '.' + match[2] + '.' + match[1].slice(2);
}


function ffDailyParseNumber_(value) {
  if (value === null || value === undefined || value === '') {
    return 0;
  }

  var cleaned = String(value)
    .replace(/\u00A0/g, '')
    .replace(/\s/g, '')
    .replace(',', '.')
    .replace(/[^\d.-]/g, '');

  if (!cleaned) {
    return 0;
  }

  var number = Number(cleaned);
  return isFinite(number) ? number : 0;
}


function ffDailyColumnLetter_(column) {
  var result = '';
  var value = Number(column);

  while (value > 0) {
    value--;
    result = String.fromCharCode(65 + (value % 26)) + result;
    value = Math.floor(value / 26);
  }

  return result;
}
