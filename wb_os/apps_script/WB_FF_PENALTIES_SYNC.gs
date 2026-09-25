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
  FP_PREFIX: 'FF_PODMENY_SYNC_FP_',
  VISION_URL: 'https://wb-ai-manager-live-production.up.railway.app/api/photo-analysis',
  VISION_MAX_ROWS_PER_RUN: 8,
  LEGACY_SHEETS: [
    '16-25 июля',
    '26 июля - 11 августа',
    '12-23 августа',
    '23 августа - 6 сентября',
    '07-20 сентября'
  ]
};

function ffPenaltiesAutoSync_() {
  var lock = LockService.getScriptLock();
  if (!lock.tryLock(5000)) {
    return { skipped: true, reason: 'lock_busy' };
  }
  try {
    var result = ffPodmenySyncCurrentPeriod_(true);
    try {
      ffVisionAnalyzePendingSheets_();
    } catch (visionError) {
      Logger.log(
        'FF vision analysis: ' +
        (visionError.stack || visionError.message || visionError)
      );
    }
    return result;
  } finally {
    lock.releaseLock();
  }
}

function ffPenaltiesSyncNow() {
  var result = ffPodmenySyncCurrentPeriod_(false);
  var vision = ffVisionAnalyzePendingSheets_();
  return { sync: result, vision: vision };
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

  ffPodmenyEnsureAiColumns_(sheet);

  var state = ffPodmenyExistingState_(sheet);
  rows.forEach(function(row) {
    var key = ffPodmenyKey_(row[0], row[6], row[4], row[1]);
    var saved = state[key];
    if (!saved) return;
    row[17] = saved.comment || '';
    for (var i = 0; i < 10; i++) {
      row[18 + i] = saved.ai[i] == null ? '' : saved.ai[i];
    }
  });

  var fp = ffPodmenyFingerprint_(period.title, rows);
  var props = PropertiesService.getScriptProperties();
  var fpKey = cfg.FP_PREFIX + period.title;

  if (
    skipIfUnchanged &&
    props.getProperty(fpKey) === fp &&
    sheet.getLastRow() >= rows.length + 1
  ) {
    return {
      skipped: true,
      reason: 'source_unchanged',
      period: period.title,
      rows: rows.length
    };
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
    if (date.getTime() < start.getTime() || date.getTime() > end.getTime()) {
      return;
    }

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

    if (!photos.length) return;

    out.push([
      shop,                         // A
      date,                         // B
      type,                         // C
      oldArticle,                   // D
      r[2] || '',                   // E nmId
      r[13] || '',                  // F сумма
      oldSticker,                   // G sticker
      oldArticle,                   // H что заказано
      newArticle || 'Не указано',   // I что пришло
      ffPodmenyImage_(photos[0]),   // J
      ffPodmenyImage_(photos[1]),   // K
      ffPodmenyImage_(photos[2]),   // L
      ffPodmenyImage_(photos[3]),   // M
      ffPodmenyImage_(photos[4]),   // N
      '',                           // O ID заказа
      '',                           // P Report ID
      '',                           // Q RRD ID
      '',                           // R комментарий ФФ
      'ЖДЁТ VISION',                // S
      '',                           // T вердикт
      '',                           // U уверенность
      '',                           // V подмена
      '',                           // W повреждение
      '',                           // X степень
      '',                           // Y характер
      'Фото загружены; ожидается визуальный анализ.', // Z
      'ДА',                         // AA проверить
      ''                            // AB дата
    ]);
  });

  return out;
}

function ffPodmenyImage_(url) {
  if (!url) return '';
  return '=IMAGE("' + String(url).replace(/"/g, '""') + '")';
}

function ffPodmenyExistingState_(sheet) {
  var out = {};
  if (sheet.getLastRow() < 2) return out;
  ffPodmenyEnsureAiColumns_(sheet);

  var v = sheet
    .getRange(2, 1, sheet.getLastRow() - 1, 28)
    .getValues();

  v.forEach(function(r) {
    var key = ffPodmenyKey_(r[0], r[6], r[4], r[1]);
    if (!key) return;
    out[key] = {
      comment: r[17] || '',
      ai: r.slice(18, 28)
    };
  });

  return out;
}

function ffPodmenyKey_(shop, sticker, nmId, date) {
  var d = ffPodmenyDate_(date);
  var ds = d
    ? Utilities.formatDate(d, FF_PODMENY_SYNC_CFG.TZ, 'yyyy-MM-dd')
    : '';
  return [shop || '', sticker || '', nmId || '', ds].join('|');
}

function ffPodmenyCurrentPeriod_() {
  var cfg = FF_PODMENY_SYNC_CFG;
  var todayText = Utilities.formatDate(new Date(), cfg.TZ, 'yyyy-MM-dd');
  var today = ffPodmenyDateFromYmd_(todayText);
  var anchor = ffPodmenyDateFromYmd_(cfg.PERIOD_ANCHOR);
  var days = Math.floor(
    (today.getTime() - anchor.getTime()) / 86400000
  );
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

function ffPodmenyDateFromYmd_(s) {
  var p = String(s).split('-');
  return new Date(
    Number(p[0]),
    Number(p[1]) - 1,
    Number(p[2]),
    12, 0, 0
  );
}

function ffPodmenyDate_(value) {
  if (
    Object.prototype.toString.call(value) === '[object Date]' &&
    !isNaN(value)
  ) {
    return new Date(
      value.getFullYear(),
      value.getMonth(),
      value.getDate(),
      12, 0, 0
    );
  }
  var s = String(value || '').trim();
  var m = s.match(/^(\d{4})-(\d{2})-(\d{2})/);
  if (m) {
    return new Date(+m[1], +m[2] - 1, +m[3], 12, 0, 0);
  }
  m = s.match(/^(\d{1,2})\.(\d{1,2})\.(\d{4})/);
  if (m) {
    return new Date(+m[3], +m[2] - 1, +m[1], 12, 0, 0);
  }
  return null;
}

function ffPodmenyFingerprint_(title, rows) {
  var payload = [title];
  rows.forEach(function(r) {
    payload.push(
      r.slice(0, 17).map(function(v) {
        if (
          Object.prototype.toString.call(v) === '[object Date]' &&
          !isNaN(v)
        ) {
          return Utilities.formatDate(
            v,
            FF_PODMENY_SYNC_CFG.TZ,
            'yyyy-MM-dd'
          );
        }
        return String(v == null ? '' : v);
      }).join('\u001f')
    );
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

function ffPodmenyEnsureAiColumns_(sheet) {
  if (sheet.getMaxColumns() < 28) {
    sheet.insertColumnsAfter(
      sheet.getMaxColumns(),
      28 - sheet.getMaxColumns()
    );
  }

  var headers = [[
    'ИИ: статус',
    'ИИ: вердикт',
    'ИИ: уверенность',
    'ИИ: подмена',
    'ИИ: повреждение',
    'ИИ: степень',
    'ИИ: характер повреждения',
    'ИИ: что видно/вывод',
    'ИИ: проверить человеку',
    'ИИ: дата анализа'
  ]];

  sheet.getRange(1, 19, 1, 10).setValues(headers);
  sheet.getRange(1, 19, 1, 10)
    .setFontWeight('bold')
    .setHorizontalAlignment('center')
    .setVerticalAlignment('middle')
    .setWrap(true)
    .setBackground('#cfe2f3');
}

function ffPodmenyWrite_(sheet, rows) {
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
    'Комментарий ФФ',
    'ИИ: статус',
    'ИИ: вердикт',
    'ИИ: уверенность',
    'ИИ: подмена',
    'ИИ: повреждение',
    'ИИ: степень',
    'ИИ: характер повреждения',
    'ИИ: что видно/вывод',
    'ИИ: проверить человеку',
    'ИИ: дата анализа'
  ]];

  var needRows = Math.max(500, rows.length + 20);
  if (sheet.getMaxRows() < needRows) {
    sheet.insertRowsAfter(
      sheet.getMaxRows(),
      needRows - sheet.getMaxRows()
    );
  }
  ffPodmenyEnsureAiColumns_(sheet);

  sheet
    .getRange(1, 1, sheet.getMaxRows(), 28)
    .clearContent();
  sheet.getRange(1, 1, 1, 28).setValues(headers);

  if (rows.length) {
    sheet.getRange(2, 1, rows.length, 28).setValues(rows);
    sheet
      .getRange(2, 2, rows.length, 1)
      .setNumberFormat('dd.mm.yyyy');
    sheet
      .getRange(2, 6, rows.length, 1)
      .setNumberFormat('#,##0.00 "₽"');
    sheet
      .getRange(2, 21, rows.length, 1)
      .setNumberFormat('0"%"');
    sheet
      .getRange(2, 1, rows.length, 28)
      .setVerticalAlignment('middle')
      .setWrap(true);
    for (var i = 2; i <= rows.length + 1; i++) {
      sheet.setRowHeight(i, 132);
    }
  }

  sheet.setFrozenRows(1);
  sheet.getRange(1, 1, 1, 18)
    .setFontWeight('bold')
    .setHorizontalAlignment('center')
    .setVerticalAlignment('middle')
    .setWrap(true)
    .setBackground('#d9ead3');
  ffPodmenyEnsureAiColumns_(sheet);
  sheet.setRowHeight(1, 42);

  var widths = [
    110,105,220,220,105,110,135,250,250,
    155,155,155,155,155,115,150,130,260,
    120,210,100,100,115,110,180,300,125,140
  ];
  widths.forEach(function(px, i) {
    sheet.setColumnWidth(i + 1, px);
  });

  var oldFilter = sheet.getFilter();
  if (oldFilter) oldFilter.remove();
  sheet
    .getRange(1, 1, Math.max(rows.length + 1, 2), 28)
    .createFilter();
}

function ffVisionAnalyzePendingSheets_() {
  var cfg = FF_PODMENY_SYNC_CFG;
  var dst = SpreadsheetApp.openById(cfg.TARGET_SPREADSHEET_ID);
  var current = ffPodmenyCurrentPeriod_().title;
  var names = cfg.LEGACY_SHEETS.slice();
  if (names.indexOf(current) === -1) names.push(current);

  var budget = cfg.VISION_MAX_ROWS_PER_RUN;
  var analyzed = 0;
  var errors = [];

  names.forEach(function(name) {
    if (budget <= 0) return;
    var sheet = dst.getSheetByName(name);
    if (!sheet || sheet.getLastRow() < 2) return;
    ffPodmenyEnsureAiColumns_(sheet);

    var done = ffVisionAnalyzeSheet_(sheet, budget);
    analyzed += done.analyzed;
    budget -= done.attempted;
    if (done.errors.length) {
      errors = errors.concat(done.errors);
    }
  });

  return {
    ok: errors.length === 0,
    analyzed: analyzed,
    errors: errors
  };
}

function ffVisionAnalyzeSheet_(sheet, budget) {
  var lastRow = sheet.getLastRow();
  var headers = sheet
    .getRange(1, 1, 1, Math.min(sheet.getMaxColumns(), 28))
    .getDisplayValues()[0];

  var modern = String(headers[0] || '') === 'Магазин';
  var values = sheet
    .getRange(2, 1, lastRow - 1, 28)
    .getValues();
  var formulas = sheet
    .getRange(2, 1, lastRow - 1, 28)
    .getFormulas();

  var attempted = 0;
  var analyzed = 0;
  var errors = [];

  for (var i = 0; i < values.length && attempted < budget; i++) {
    var row = values[i];
    if (!row[0]) continue;

    var status = String(row[18] || '').trim();
    if (
      status === 'ПРОАНАЛИЗИРОВАНО' ||
      status === 'НЕТ ФОТО'
    ) {
      continue;
    }

    var photoStart = modern ? 9 : 8;
    var photoUrls = ffVisionExtractPhotoUrls_(
      formulas[i].slice(photoStart, photoStart + 5),
      row.slice(photoStart, photoStart + 5)
    );

    if (!photoUrls.length) {
      sheet.getRange(i + 2, 19, 1, 10).setValues([[
        'НЕТ ФОТО',
        'НЕДОСТАТОЧНО ДАННЫХ',
        '',
        'ВОЗМОЖНО',
        'НЕ ОЦЕНЕНО',
        'НЕИЗВЕСТНО',
        '',
        'Нет прямых WB-фото для анализа.',
        'ДА',
        new Date()
      ]]);
      continue;
    }

    attempted++;

    var payload = modern
      ? {
          shop: row[0],
          category: row[2],
          nm_id: row[4],
          sticker: row[6],
          expected: row[7],
          reported_received: row[8],
          photo_urls: photoUrls
        }
      : {
          shop: '',
          category: row[1],
          nm_id: row[3],
          sticker: row[5],
          expected: row[6],
          reported_received: row[7],
          photo_urls: photoUrls
        };

    try {
      var result = ffVisionCall_(payload);
      ffVisionWriteResult_(sheet, i + 2, result);
      if (String(result.status || '') === 'analyzed') {
        analyzed++;
      }
    } catch (err) {
      errors.push(
        sheet.getName() + ' row ' + (i + 2) + ': ' +
        String(err && err.message ? err.message : err)
      );
      sheet.getRange(i + 2, 19).setValue('ОШИБКА VISION');
      sheet
        .getRange(i + 2, 26)
        .setValue(String(err && err.message ? err.message : err).slice(0, 500));
      sheet.getRange(i + 2, 27).setValue('ДА');
      sheet.getRange(i + 2, 28).setValue(new Date());
    }
  }

  return {
    attempted: attempted,
    analyzed: analyzed,
    errors: errors
  };
}

function ffVisionExtractPhotoUrls_(formulas, values) {
  var out = [];

  for (var i = 0; i < formulas.length; i++) {
    var formula = String(formulas[i] || '');
    var value = String(values[i] || '');
    var m = formula.match(/=IMAGE\("([^"]+)"/i);
    var url = m ? m[1] : '';

    if (!url && /^https?:\/\//i.test(value)) {
      url = value;
    }

    if (
      url &&
      /^https:\/\/static-basket-[a-z0-9-]+\.wb\.ru\//i.test(url) &&
      out.indexOf(url) === -1
    ) {
      out.push(url);
    }
  }

  return out.slice(0, 5);
}

function ffVisionCall_(payload) {
  var key = PropertiesService
    .getScriptProperties()
    .getProperty('ACCESS_KEY') || '';

  var response = UrlFetchApp.fetch(
    FF_PODMENY_SYNC_CFG.VISION_URL,
    {
      method: 'post',
      contentType: 'application/json',
      headers: {
        'X-Bridge-Key': key
      },
      payload: JSON.stringify(payload),
      muteHttpExceptions: true
    }
  );

  var code = response.getResponseCode();
  var body = response.getContentText();
  var data;

  try {
    data = JSON.parse(body || '{}');
  } catch (err) {
    throw new Error('Vision HTTP ' + code + ': invalid JSON');
  }

  if (code < 200 || code >= 300) {
    throw new Error(
      'Vision HTTP ' + code + ': ' +
      String(data.detail || data.error || body).slice(0, 400)
    );
  }

  return data;
}

function ffVisionWriteResult_(sheet, rowNumber, result) {
  var status = String(result.status || '');
  var displayStatus = status === 'analyzed'
    ? 'ПРОАНАЛИЗИРОВАНО'
    : status === 'waiting_key'
      ? 'ЖДЁТ КЛЮЧ'
      : 'НЕДОСТАТОЧНО ДАННЫХ';

  var damageTypes = result.damage_types || [];
  if (!Array.isArray(damageTypes)) damageTypes = [];

  var evidence = String(result.visible_evidence || '');
  var receivedGuess = String(result.received_guess || '');
  if (receivedGuess) {
    evidence += (evidence ? ' ' : '') +
      'Фактически видно: ' + receivedGuess + '.';
  }

  sheet.getRange(rowNumber, 19, 1, 10).setValues([[
    displayStatus,
    result.verdict || '',
    result.confidence_pct == null ? '' : Number(result.confidence_pct),
    result.substitution || '',
    result.damage || '',
    result.damage_severity || '',
    damageTypes.join(', '),
    evidence,
    result.human_review ? 'ДА' : 'НЕТ',
    new Date()
  ]]);

  sheet
    .getRange(rowNumber, 21)
    .setNumberFormat('0"%"');
}
