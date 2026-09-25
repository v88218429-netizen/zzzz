var FF_PODMENY_SYNC_CFG = {
  SOURCE_SPREADSHEET_ID: '1pYc7t197EU8Rru5CzXA5Fwa91_K2o0SBKKbxOY_MqCk',
  TARGET_SPREADSHEET_ID: '1Hj6jxJGN1cnJQbcsov0P2kbysNkO_EbERRgNMcajd74',
  SOURCES: [
    { sheet: 'Подмены ИП АП', shop: 'Саныч' },
    { sheet: 'Подмены ИП АА', shop: 'AIR' },
    { sheet: 'Подмены ИП ЮВ', shop: 'Хозяюшка' }
  ],
  PERIOD_ANCHOR: '2026-09-21',
  PERIOD_DAYS: 14,
  TZ: 'Europe/Moscow',
  FP_PREFIX: 'FF_PODMENY_SYNC_FP_'
};

function ffPenaltiesAutoSync_() {
  var lock = LockService.getScriptLock();
  if (!lock.tryLock(5000)) {
    return { skipped: true, reason: 'lock_busy' };
  }
  try {
    return ffPodmenySyncCurrentPeriod_(true);
  } finally {
    lock.releaseLock();
  }
}

function ffPenaltiesSyncNow() {
  return ffPodmenySyncCurrentPeriod_(false);
}

function ffPodmenySyncCurrentPeriod_(skipIfUnchanged) {
  var cfg = FF_PODMENY_SYNC_CFG;
  var period = ffPodmenyCurrentPeriod_();
  var src = SpreadsheetApp.openById(cfg.SOURCE_SPREADSHEET_ID);
  var dst = SpreadsheetApp.openById(cfg.TARGET_SPREADSHEET_ID);

  var rows = [];
  cfg.SOURCES.forEach(function(sourceCfg) {
    var sh = src.getSheetByName(sourceCfg.sheet);
    if (!sh) return;
    rows = rows.concat(
      ffPodmenyReadSource_(sh, sourceCfg.shop, period.start, period.end)
    );
  });

  rows.sort(function(a, b) {
    var dt = b[1].getTime() - a[1].getTime();
    if (dt) return dt;
    return String(a[0]).localeCompare(String(b[0]), 'ru');
  });

  var sheet = dst.getSheetByName(period.title);
  if (!sheet) sheet = dst.insertSheet(period.title);

  var comments = ffPodmenyExistingComments_(sheet);
  rows.forEach(function(row) {
    var key = ffPodmenyKey_(row[0], row[6], row[4], row[1]);
    if (Object.prototype.hasOwnProperty.call(comments, key)) {
      row[17] = comments[key];
    }
  });

  var fp = ffPodmenyFingerprint_(period.title, rows);
  var props = PropertiesService.getScriptProperties();
  var fpKey = cfg.FP_PREFIX + period.title;

  if (skipIfUnchanged && props.getProperty(fpKey) === fp) {
    return { skipped: true, reason: 'source_unchanged', period: period.title, rows: rows.length };
  }

  ffPodmenyWrite_(sheet, rows);
  props.setProperty(fpKey, fp);

  return { ok: true, period: period.title, rows: rows.length };
}

function ffPodmenyReadSource_(sheet, shop, start, end) {
  var lastRow = sheet.getLastRow();
  if (lastRow < 2) return [];

  var values = sheet.getRange(2, 1, lastRow - 1, 15).getValues();
  var out = [];

  values.forEach(function(r) {
    var date = ffPodmenyDate_(r[0]);
    if (!date) return;
    if (date.getTime() < start.getTime() || date.getTime() > end.getTime()) return;

    var type = String(r[1] || '').trim();
    if (type.toLowerCase().indexOf('подмен') === -1) return;

    var oldSticker = String(r[3] || '').trim();
    var oldArticle = r[7] || '';
    var newArticle = r[12] || '';
    var photos = String(r[14] || '')
      .split(/\s+/)
      .map(function(x) { return x.trim(); })
      .filter(function(x) { return /^https?:\/\//i.test(x); })
      .slice(0, 5);

    if (!photos.length) return; // ФФ-таблица только для подмен с фото-доказательствами.

    out.push([
      shop,                         // A
      date,                         // B
      type,                         // C
      oldArticle,                   // D
      r[2] || '',                   // E nmId
      r[13] || '',                  // F сумма
      oldSticker,                   // G стикер
      oldArticle,                   // H что заказано
      newArticle || 'Не указано',   // I что пришло
      ffPodmenyImage_(photos[0]),    // J
      ffPodmenyImage_(photos[1]),    // K
      ffPodmenyImage_(photos[2]),    // L
      ffPodmenyImage_(photos[3]),    // M
      ffPodmenyImage_(photos[4]),    // N
      '',                           // O ID заказа
      '',                           // P Report ID
      '',                           // Q RRD ID
      ''                            // R комментарий ФФ
    ]);
  });

  return out;
}

function ffPodmenyImage_(url) {
  if (!url) return '';
  return '=IMAGE("' + String(url).replace(/"/g, '""') + '")';
}

function ffPodmenyExistingComments_(sheet) {
  var out = {};
  if (sheet.getLastRow() < 2) return out;
  var v = sheet.getRange(2, 1, sheet.getLastRow() - 1, 18).getValues();
  v.forEach(function(r) {
    var key = ffPodmenyKey_(r[0], r[6], r[4], r[1]);
    if (key && r[17] !== '' && r[17] !== null) out[key] = r[17];
  });
  return out;
}

function ffPodmenyKey_(shop, sticker, nmId, date) {
  var d = ffPodmenyDate_(date);
  var ds = d ? Utilities.formatDate(d, FF_PODMENY_SYNC_CFG.TZ, 'yyyy-MM-dd') : '';
  return [shop || '', sticker || '', nmId || '', ds].join('|');
}

function ffPodmenyCurrentPeriod_() {
  var cfg = FF_PODMENY_SYNC_CFG;
  var todayText = Utilities.formatDate(new Date(), cfg.TZ, 'yyyy-MM-dd');
  var today = ffPodmenyDateFromYmd_(todayText);
  var anchor = ffPodmenyDateFromYmd_(cfg.PERIOD_ANCHOR);
  var days = Math.floor((today.getTime() - anchor.getTime()) / 86400000);
  var block = Math.floor(days / cfg.PERIOD_DAYS);
  var start = new Date(anchor.getTime() + block * cfg.PERIOD_DAYS * 86400000);
  var end = new Date(start.getTime() + (cfg.PERIOD_DAYS - 1) * 86400000);
  return {
    start: start,
    end: end,
    title:
      Utilities.formatDate(start, cfg.TZ, 'dd.MM') +
      '–' +
      Utilities.formatDate(end, cfg.TZ, 'dd.MM')
  };
}

function ffPodmenyDateFromYmd_(s) {
  var p = String(s).split('-');
  return new Date(Number(p[0]), Number(p[1]) - 1, Number(p[2]), 12, 0, 0);
}

function ffPodmenyDate_(value) {
  if (Object.prototype.toString.call(value) === '[object Date]' && !isNaN(value)) {
    return new Date(value.getFullYear(), value.getMonth(), value.getDate(), 12, 0, 0);
  }
  var s = String(value || '').trim();
  var m = s.match(/^(\d{4})-(\d{2})-(\d{2})/);
  if (m) return new Date(+m[1], +m[2]-1, +m[3], 12, 0, 0);
  m = s.match(/^(\d{1,2})\.(\d{1,2})\.(\d{4})/);
  if (m) return new Date(+m[3], +m[2]-1, +m[1], 12, 0, 0);
  return null;
}

function ffPodmenyFingerprint_(title, rows) {
  var payload = [title];
  rows.forEach(function(r) {
    payload.push(r.slice(0, 17).map(function(v) {
      if (Object.prototype.toString.call(v) === '[object Date]' && !isNaN(v)) {
        return Utilities.formatDate(v, FF_PODMENY_SYNC_CFG.TZ, 'yyyy-MM-dd');
      }
      return String(v == null ? '' : v);
    }).join('\u001f'));
  });

  var digest = Utilities.computeDigest(
    Utilities.DigestAlgorithm.SHA_256,
    payload.join('\u001e'),
    Utilities.Charset.UTF_8
  );
  return digest.map(function(b) {
    var v = b < 0 ? b + 256 : b;
    return ('0' + v.toString(16)).slice(-2);
  }).join('');
}

function ffPodmenyWrite_(sheet, rows) {
  var headers = [[
    'Магазин','Дата штрафа','Категория штрафа','Артикул продавца',
    'Артикул WB','Сумма штрафа','Стикер МП','Что было заказано',
    'Что пришло','Фото 1','Фото 2','Фото 3','Фото 4','Фото 5',
    'ID заказа','Report ID','RRD ID','Комментарий ФФ'
  ]];

  var needRows = Math.max(500, rows.length + 20);
  if (sheet.getMaxRows() < needRows) {
    sheet.insertRowsAfter(sheet.getMaxRows(), needRows - sheet.getMaxRows());
  }
  if (sheet.getMaxColumns() < 18) {
    sheet.insertColumnsAfter(sheet.getMaxColumns(), 18 - sheet.getMaxColumns());
  }

  sheet.getRange(1, 1, sheet.getMaxRows(), 18).clearContent();
  sheet.getRange(1, 1, 1, 18).setValues(headers);

  if (rows.length) {
    sheet.getRange(2, 1, rows.length, 18).setValues(rows);
    sheet.getRange(2, 2, rows.length, 1).setNumberFormat('dd.mm.yyyy');
    sheet.getRange(2, 6, rows.length, 1).setNumberFormat('#,##0.00 "₽"');
    sheet.getRange(2, 1, rows.length, 18).setVerticalAlignment('middle').setWrap(true);
    for (var i = 2; i <= rows.length + 1; i++) sheet.setRowHeight(i, 132);
  }

  sheet.setFrozenRows(1);
  sheet.getRange(1, 1, 1, 18)
    .setFontWeight('bold')
    .setHorizontalAlignment('center')
    .setVerticalAlignment('middle')
    .setWrap(true)
    .setBackground('#d9ead3');
  sheet.setRowHeight(1, 42);

  var widths=[110,105,220,220,105,110,135,250,250,155,155,155,155,155,115,150,130,260];
  widths.forEach(function(px,i){ sheet.setColumnWidth(i+1,px); });

  var oldFilter=sheet.getFilter();
  if(oldFilter) oldFilter.remove();
  sheet.getRange(1,1,Math.max(rows.length+1,2),18).createFilter();
}
