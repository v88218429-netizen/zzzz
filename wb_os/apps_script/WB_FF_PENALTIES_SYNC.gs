var FF_PENALTIES_SYNC_CFG = {
  SOURCE_SPREADSHEET_ID: '1pYc7t197EU8Rru5CzXA5Fwa91_K2o0SBKKbxOY_MqCk',
  TARGET_SPREADSHEET_ID: '1Hj6jxJGN1cnJQbcsov0P2kbysNkO_EbERRgNMcajd74',
  SOURCE_SHEET: 'ФФ — штрафы 14 дней',
  SUBSTITUTIONS_SHEET: 'Подмены ИП АА',
  ALLOWED_SHOPS: ['Саныч', 'AIR', 'Хозяюшка'],
  PERIOD_ANCHOR: '2026-09-21',
  PERIOD_DAYS: 14,
  TZ: 'Europe/Moscow',
  LAST_SYNC_PROPERTY: 'FF_PENALTIES_LAST_SYNC_DATE'
};

function ffPenaltiesAutoSync_() {
  var todayKey = Utilities.formatDate(
    new Date(),
    FF_PENALTIES_SYNC_CFG.TZ,
    'yyyy-MM-dd'
  );

  var props = PropertiesService.getScriptProperties();
  if (props.getProperty(FF_PENALTIES_SYNC_CFG.LAST_SYNC_PROPERTY) === todayKey) {
    return { skipped: true, reason: 'already_synced_today', date: todayKey };
  }

  var lock = LockService.getScriptLock();
  if (!lock.tryLock(5000)) {
    return { skipped: true, reason: 'lock_busy', date: todayKey };
  }

  try {
    var result = ffPenaltiesSyncCurrentPeriod_();
    props.setProperty(FF_PENALTIES_SYNC_CFG.LAST_SYNC_PROPERTY, todayKey);
    return result;
  } finally {
    lock.releaseLock();
  }
}

function ffPenaltiesSyncNow() {
  return ffPenaltiesSyncCurrentPeriod_();
}

function ffPenaltiesSyncCurrentPeriod_() {
  var cfg = FF_PENALTIES_SYNC_CFG;
  var period = ffPenaltiesCurrentPeriod_();

  var sourceBook = SpreadsheetApp.openById(cfg.SOURCE_SPREADSHEET_ID);
  var targetBook = SpreadsheetApp.openById(cfg.TARGET_SPREADSHEET_ID);

  var sourceSheet = sourceBook.getSheetByName(cfg.SOURCE_SHEET);
  if (!sourceSheet) {
    throw new Error('FF penalties: source sheet not found: ' + cfg.SOURCE_SHEET);
  }

  var subSheet = sourceBook.getSheetByName(cfg.SUBSTITUTIONS_SHEET);
  var subMap = ffPenaltiesReadSubstitutions_(subSheet);

  var targetSheet = targetBook.getSheetByName(period.title);
  if (!targetSheet) {
    targetSheet = targetBook.insertSheet(period.title);
    targetSheet.setFrozenRows(1);
  }

  var comments = ffPenaltiesReadExistingComments_(targetSheet);
  var rows = ffPenaltiesBuildRows_(
    sourceSheet,
    subMap,
    comments,
    period.start,
    period.end
  );

  ffPenaltiesWriteSheet_(targetSheet, rows);

  Logger.log(
    'FF penalties sync: period=' + period.title +
    '; rows=' + rows.length
  );

  return {
    ok: true,
    period: period.title,
    rows: rows.length,
    start: Utilities.formatDate(period.start, cfg.TZ, 'yyyy-MM-dd'),
    end: Utilities.formatDate(period.end, cfg.TZ, 'yyyy-MM-dd')
  };
}

function ffPenaltiesCurrentPeriod_() {
  var cfg = FF_PENALTIES_SYNC_CFG;
  var todayText = Utilities.formatDate(new Date(), cfg.TZ, 'yyyy-MM-dd');

  var today = ffPenaltiesDateFromYmd_(todayText);
  var anchor = ffPenaltiesDateFromYmd_(cfg.PERIOD_ANCHOR);

  var days = Math.floor((today.getTime() - anchor.getTime()) / 86400000);
  var block = Math.floor(days / cfg.PERIOD_DAYS);

  var start = new Date(
    anchor.getTime() + block * cfg.PERIOD_DAYS * 86400000
  );
  var end = new Date(
    start.getTime() + (cfg.PERIOD_DAYS - 1) * 86400000
  );

  return {
    start: start,
    end: end,
    title:
      Utilities.formatDate(start, cfg.TZ, 'dd.MM') +
      '–' +
      Utilities.formatDate(end, cfg.TZ, 'dd.MM')
  };
}

function ffPenaltiesDateFromYmd_(text) {
  var p = String(text).split('-');
  return new Date(Number(p[0]), Number(p[1]) - 1, Number(p[2]), 12, 0, 0);
}

function ffPenaltiesNormalizeDate_(value) {
  if (Object.prototype.toString.call(value) === '[object Date]' && !isNaN(value)) {
    return new Date(value.getFullYear(), value.getMonth(), value.getDate(), 12, 0, 0);
  }

  var s = String(value || '').trim();
  if (!s) return null;

  var m = s.match(/^(\d{4})-(\d{2})-(\d{2})/);
  if (m) {
    return new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3]), 12, 0, 0);
  }

  m = s.match(/^(\d{1,2})\.(\d{1,2})\.(\d{4})/);
  if (m) {
    return new Date(Number(m[3]), Number(m[2]) - 1, Number(m[1]), 12, 0, 0);
  }

  return null;
}

function ffPenaltiesReadSubstitutions_(sheet) {
  var out = {};
  if (!sheet || sheet.getLastRow() < 2) return out;

  var values = sheet.getRange(2, 1, sheet.getLastRow() - 1, 15).getValues();

  values.forEach(function(row) {
    var sticker = String(row[3] || '').trim();
    if (!sticker) return;

    out[sticker] = {
      ordered: row[7] || '',
      received: row[12] || '',
      photos: String(row[14] || '')
        .split(/\r?\n/)
        .map(function(x) { return x.trim(); })
        .filter(function(x) { return Boolean(x); })
        .slice(0, 5)
    };
  });

  return out;
}

function ffPenaltiesRowKey_(shop, rrdId, orderId, reportId) {
  var s = String(shop || '').trim();
  var r = String(rrdId || '').trim();
  if (r) return s + '|rrd|' + r;

  return (
    s +
    '|order|' +
    String(orderId || '').trim() +
    '|report|' +
    String(reportId || '').trim()
  );
}

function ffPenaltiesReadExistingComments_(sheet) {
  var out = {};
  var lastRow = sheet.getLastRow();
  if (lastRow < 2) return out;

  var values = sheet.getRange(2, 1, lastRow - 1, 18).getValues();
  values.forEach(function(row) {
    var key = ffPenaltiesRowKey_(row[0], row[16], row[14], row[15]);
    var comment = row[17];
    if (key && comment !== '' && comment !== null) {
      out[key] = comment;
    }
  });

  return out;
}

function ffPenaltiesImageFormula_(url) {
  var s = String(url || '').trim();
  if (!s) return '';
  return '=IMAGE("' + s.replace(/"/g, '""') + '")';
}

function ffPenaltiesBuildRows_(
  sourceSheet,
  subMap,
  comments,
  periodStart,
  periodEnd
) {
  var cfg = FF_PENALTIES_SYNC_CFG;
  var lastRow = sourceSheet.getLastRow();
  if (lastRow < 5) return [];

  var values = sourceSheet
    .getRange(5, 1, lastRow - 4, 20)
    .getValues();

  var allowed = {};
  cfg.ALLOWED_SHOPS.forEach(function(x) { allowed[x] = true; });

  var dedup = {};

  values.forEach(function(row) {
    var shop = String(row[0] || '').trim();
    if (!allowed[shop]) return;

    var fineDate = ffPenaltiesNormalizeDate_(row[1]);
    if (!fineDate) return;
    if (fineDate.getTime() < periodStart.getTime()) return;
    if (fineDate.getTime() > periodEnd.getTime()) return;

    var sticker = String(row[6] || '').trim();
    var substitution = shop === 'AIR' ? subMap[sticker] : null;

    var ordered = substitution && substitution.ordered
      ? substitution.ordered
      : (row[7] || '');

    var received = substitution
      ? (substitution.received || '')
      : (row[8] || '');

    var photos = substitution ? substitution.photos : [];

    var orderId = row[14] || '';
    var reportId = row[15] || '';
    var rrdId = row[16] || '';

    var key = ffPenaltiesRowKey_(shop, rrdId, orderId, reportId);
    var targetComment =
      Object.prototype.hasOwnProperty.call(comments, key)
        ? comments[key]
        : (row[18] || '');

    dedup[key] = [
      shop,
      fineDate,
      row[2] || '',
      row[3] || '',
      row[4] || '',
      row[5] || '',
      row[6] || '',
      ordered,
      received,
      ffPenaltiesImageFormula_(photos[0]),
      ffPenaltiesImageFormula_(photos[1]),
      ffPenaltiesImageFormula_(photos[2]),
      ffPenaltiesImageFormula_(photos[3]),
      ffPenaltiesImageFormula_(photos[4]),
      orderId,
      reportId,
      rrdId,
      targetComment
    ];
  });

  var rows = Object.keys(dedup).map(function(k) { return dedup[k]; });

  rows.sort(function(a, b) {
    var dt = b[1].getTime() - a[1].getTime();
    if (dt) return dt;

    var shop = String(a[0]).localeCompare(String(b[0]), 'ru');
    if (shop) return shop;

    return String(a[2]).localeCompare(String(b[2]), 'ru');
  });

  return rows;
}

function ffPenaltiesWriteSheet_(sheet, rows) {
  var headers = [[
    'Магазин',
    'Дата штрафа',
    'Категория штрафа',
    'Артикул продавца',
    'Артикул WB',
    'Сумма штрафа',
    'Стикер МП',
    'Что было заказано',
    'Что пришло',
    'Фото 1',
    'Фото 2',
    'Фото 3',
    'Фото 4',
    'Фото 5',
    'ID заказа',
    'Report ID',
    'RRD ID',
    'Комментарий ФФ'
  ]];

  if (sheet.getMaxRows() < Math.max(500, rows.length + 20)) {
    sheet.insertRowsAfter(
      sheet.getMaxRows(),
      Math.max(500, rows.length + 20) - sheet.getMaxRows()
    );
  }

  if (sheet.getMaxColumns() < 18) {
    sheet.insertColumnsAfter(
      sheet.getMaxColumns(),
      18 - sheet.getMaxColumns()
    );
  }

  var clearRows = Math.max(sheet.getLastRow() - 1, rows.length, 1);
  sheet.getRange(2, 1, clearRows, 18).clearContent();

  sheet.getRange(1, 1, 1, 18).setValues(headers);
  if (rows.length) {
    sheet.getRange(2, 1, rows.length, 18).setValues(rows);
  }

  sheet.setFrozenRows(1);

  var header = sheet.getRange(1, 1, 1, 18);
  header
    .setFontWeight('bold')
    .setHorizontalAlignment('center')
    .setVerticalAlignment('middle')
    .setWrap(true)
    .setBackground('#d9ead3');

  sheet.setRowHeight(1, 42);

  if (rows.length) {
    sheet
      .getRange(2, 1, rows.length, 18)
      .setVerticalAlignment('middle')
      .setWrap(true);

    sheet.getRange(2, 2, rows.length, 1).setNumberFormat('dd.mm.yyyy');
    sheet.getRange(2, 6, rows.length, 1).setNumberFormat('#,##0.00 "₽"');

    for (var r = 2; r <= rows.length + 1; r++) {
      sheet.setRowHeight(r, 132);
    }
  }

  var widths = [
    110, 105, 250, 220, 105, 110, 135, 250, 250,
    155, 155, 155, 155, 155, 115, 150, 130, 260
  ];
  widths.forEach(function(px, idx) {
    sheet.setColumnWidth(idx + 1, px);
  });

  var oldFilter = sheet.getFilter();
  if (oldFilter) oldFilter.remove();

  var filterRows = Math.max(rows.length + 1, 2);
  sheet.getRange(1, 1, filterRows, 18).createFilter();
}
