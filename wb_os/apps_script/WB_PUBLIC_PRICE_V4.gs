/**
 * WB OS / WB Public Customer Price Engine v0.1.11
 *
 * Purpose:
 * - read WB nmID values from "Сводная";
 * - fetch public card data from cards/v4/detail in batches;
 * - calibrate public customer price against already-known "Цена для клиента";
 * - only after calibration passes, refresh customer prices in column K;
 * - never overwrite a known customer price with zero/missing data;
 * - write a hidden shadow/source sheet for diagnostics.
 *
 * Seller discounted price in column J continues to come from the official
 * seller Prices API sheet "Цены". This module is only for the public buyer
 * price / SPP layer.
 */

var WB_PUBLIC_PRICE_V4 = {
  VERSION: '0.1.11',
  SUMMARY_SHEET: 'Сводная',
  SOURCE_SHEET: '_WB_PUBLIC_PRICE_V4',
  HEADER_ROW: 11,
  FIRST_DATA_ROW: 12,
  NMID_COL: 3,
  SELLER_PRICE_COL: 10,
  CLIENT_PRICE_COL: 11,
  INTERVAL_MINUTES: 30,
  BATCH_SIZE: 80,
  DEST: '-1257786',
  LAST_SUCCESS_KEY: 'WB_PUBLIC_PRICE_V4_LAST_SUCCESS_AT',
  LAST_MODE_KEY: 'WB_PUBLIC_PRICE_V4_MODE',
  MIN_CALIBRATION_ROWS: 10,
  MAX_MEDIAN_REL_ERROR: 0.03,
  MIN_GOOD_SHARE: 0.80,
  GOOD_REL_ERROR: 0.05
};


function syncWbPublicCustomerPricesV4_(force) {
  var lock = LockService.getScriptLock();

  if (!lock.tryLock(5000)) {
    Logger.log(
      'WB Public Price v4: price lock busy, запуск пропущен.'
    );

    return {
      ok: true,
      skipped: true,
      reason: 'price_lock_busy'
    };
  }

  try {
    return wbPriceV4SyncUnlocked_(force);
  } finally {
    lock.releaseLock();
  }
}


function wbPriceV4SyncUnlocked_(force) {
  var props = PropertiesService.getScriptProperties();

  if (!force && !wbPriceV4Due_(props)) {
    return {
      ok: true,
      skipped: true,
      reason: 'not_due'
    };
  }

  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var summary = ss.getSheetByName(WB_PUBLIC_PRICE_V4.SUMMARY_SHEET);

  if (!summary) {
    throw new Error('WB_PRICE_V4: не найден лист «Сводная».');
  }

  wbPriceV4AssertSummaryLayout_(summary);

  var lastRow = summary.getLastRow();

  if (lastRow < WB_PUBLIC_PRICE_V4.FIRST_DATA_ROW) {
    return { ok: true, skipped: true, reason: 'no_rows' };
  }

  var numRows =
    lastRow - WB_PUBLIC_PRICE_V4.FIRST_DATA_ROW + 1;

  var data = summary
    .getRange(
      WB_PUBLIC_PRICE_V4.FIRST_DATA_ROW,
      1,
      numRows,
      WB_PUBLIC_PRICE_V4.CLIENT_PRICE_COL
    )
    .getValues();

  var clientRange = summary.getRange(
    WB_PUBLIC_PRICE_V4.FIRST_DATA_ROW,
    WB_PUBLIC_PRICE_V4.CLIENT_PRICE_COL,
    numRows,
    1
  );

  var clientFormulas = clientRange.getFormulas();

  var ids = [];
  var seen = {};

  for (var i = 0; i < data.length; i++) {
    var nm = String(data[i][WB_PUBLIC_PRICE_V4.NMID_COL - 1] || '').trim();

    if (!/^\d+$/.test(nm) || seen[nm]) {
      continue;
    }

    seen[nm] = true;
    ids.push(nm);
  }

  if (!ids.length) {
    return { ok: true, skipped: true, reason: 'no_nmids' };
  }

  var fetched = wbPriceV4FetchAll_(ids);
  var calibration = wbPriceV4Calibrate_(data, fetched);

  wbPriceV4WriteShadow_(ss, data, fetched, calibration);

  if (!calibration.ok) {
    wbPriceV4Diagnostics_({
      ok: false,
      fetched: Object.keys(fetched).length,
      changed: 0,
      preservedMissing: 0,
      mode: calibration.mode,
      calibrationRows: calibration.rows,
      medianRelativeError: calibration.medianRelativeError,
      goodShare: calibration.goodShare,
      message: 'SHADOW_FAIL'
    });

    throw new Error(
      'WB_PRICE_V4_SHADOW_FAIL: calibration=' +
      JSON.stringify(calibration)
    );
  }

  var output = [];
  var changed = 0;
  var preservedMissing = 0;

  for (var r = 0; r < data.length; r++) {
    var row = data[r];
    var nmId = String(
      row[WB_PUBLIC_PRICE_V4.NMID_COL - 1] || ''
    ).trim();

    var currentClient = wbPriceV4Number_(
      row[WB_PUBLIC_PRICE_V4.CLIENT_PRICE_COL - 1]
    );

    var existingFormula = String(
      clientFormulas[r] && clientFormulas[r][0]
        ? clientFormulas[r][0]
        : ''
    );

    var rawExistingClient =
      row[WB_PUBLIC_PRICE_V4.CLIENT_PRICE_COL - 1];

    var preservedValue =
      existingFormula ||
      (
        rawExistingClient === null ||
        rawExistingClient === undefined
          ? ''
          : rawExistingClient
      );

    if (!nmId || !fetched[nmId]) {
      output.push([preservedValue]);
      continue;
    }

    var candidate = wbPriceV4Candidate_(
      fetched[nmId],
      calibration.mode
    );

    if (!(candidate > 0)) {
      output.push([preservedValue]);
      preservedMissing++;
      continue;
    }

    output.push([candidate]);

    if (
      !currentClient ||
      Math.abs(candidate - currentClient) > 0.009
    ) {
      changed++;
    }
  }

  summary
    .getRange(
      WB_PUBLIC_PRICE_V4.FIRST_DATA_ROW,
      WB_PUBLIC_PRICE_V4.CLIENT_PRICE_COL,
      output.length,
      1
    )
    .setValues(output);

  props.setProperty(
    WB_PUBLIC_PRICE_V4.LAST_SUCCESS_KEY,
    new Date().toISOString()
  );

  props.setProperty(
    WB_PUBLIC_PRICE_V4.LAST_MODE_KEY,
    calibration.mode
  );

  wbPriceV4Diagnostics_({
    ok: true,
    fetched: Object.keys(fetched).length,
    changed: changed,
    preservedMissing: preservedMissing,
    mode: calibration.mode,
    calibrationRows: calibration.rows,
    medianRelativeError: calibration.medianRelativeError,
    goodShare: calibration.goodShare,
    message: 'OK'
  });

  /*
   * Keep the previous SPP monitor alive until v4 has actually fetched,
   * calibrated and written successfully. Only then is it safe to remove the
   * old periodic price trigger.
   */
  try {
    wbPriceV4CleanupLegacyTriggers_();
  } catch (cleanupError) {
    Logger.log(
      'WB Public Price trigger cleanup skipped: ' +
      String(
        cleanupError && cleanupError.message
          ? cleanupError.message
          : cleanupError
      )
    );
  }

  return {
    ok: true,
    skipped: false,
    fetched: Object.keys(fetched).length,
    changed: changed,
    preservedMissing: preservedMissing,
    mode: calibration.mode,
    calibrationRows: calibration.rows,
    medianRelativeError: calibration.medianRelativeError,
    goodShare: calibration.goodShare
  };
}


function wbPriceV4AssertSummaryLayout_(sheet) {
  var headers = sheet
    .getRange(
      WB_PUBLIC_PRICE_V4.HEADER_ROW,
      1,
      1,
      WB_PUBLIC_PRICE_V4.CLIENT_PRICE_COL
    )
    .getDisplayValues()[0];

  var expected = [
    {
      col: WB_PUBLIC_PRICE_V4.NMID_COL,
      names: ['Артикул WB']
    },
    {
      col: WB_PUBLIC_PRICE_V4.SELLER_PRICE_COL,
      names: ['Цена']
    },
    {
      col: WB_PUBLIC_PRICE_V4.CLIENT_PRICE_COL,
      names: ['Цена для клиента']
    }
  ];

  for (var i = 0; i < expected.length; i++) {
    var actual = String(
      headers[expected[i].col - 1] || ''
    ).trim();

    if (expected[i].names.indexOf(actual) === -1) {
      throw new Error(
        'WB_PRICE_V4_LAYOUT_MISMATCH: колонка ' +
        expected[i].col +
        ' ожидалась как «' +
        expected[i].names.join(' / ') +
        '», получено «' +
        actual +
        '». Запись в Сводную запрещена.'
      );
    }
  }
}


function forceSyncWbPublicCustomerPricesV4() {
  var result = syncWbPublicCustomerPricesV4_(true);

  SpreadsheetApp
    .getActiveSpreadsheet()
    .toast(
      'WB public prices v4: ' + JSON.stringify(result),
      'WB OS',
      10
    );

  return result;
}


function wbPriceV4Due_(props) {
  var raw = String(
    props.getProperty(WB_PUBLIC_PRICE_V4.LAST_SUCCESS_KEY) || ''
  ).trim();

  if (!raw) {
    return true;
  }

  var date = new Date(raw);

  if (isNaN(date.getTime())) {
    return true;
  }

  return (
    (Date.now() - date.getTime()) / 60000 >=
    WB_PUBLIC_PRICE_V4.INTERVAL_MINUTES
  );
}


function wbPriceV4FetchAll_(ids) {
  var requests = [];
  var batches = [];

  for (
    var i = 0;
    i < ids.length;
    i += WB_PUBLIC_PRICE_V4.BATCH_SIZE
  ) {
    var batch = ids.slice(
      i,
      i + WB_PUBLIC_PRICE_V4.BATCH_SIZE
    );

    batches.push(batch);

    requests.push({
      url:
        'https://card.wb.ru/cards/v4/detail' +
        '?appType=1' +
        '&curr=rub' +
        '&dest=' + encodeURIComponent(WB_PUBLIC_PRICE_V4.DEST) +
        '&lang=ru' +
        '&nm=' + encodeURIComponent(batch.join(';')),
      method: 'get',
      muteHttpExceptions: true,
      followRedirects: true,
      headers: {
        Accept: 'application/json',
        'User-Agent':
          'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) ' +
          'AppleWebKit/537.36 Chrome/153 Safari/537.36'
      }
    });
  }

  var responses = UrlFetchApp.fetchAll(requests);
  var out = {};

  for (var j = 0; j < responses.length; j++) {
    var response = responses[j];
    var code = response.getResponseCode();

    if (code !== 200) {
      throw new Error(
        'WB_PRICE_V4_HTTP_' + code +
        ': batch=' + j +
        '; body=' +
        response.getContentText().substring(0, 300)
      );
    }

    var json;

    try {
      json = JSON.parse(response.getContentText());
    } catch (error) {
      throw new Error(
        'WB_PRICE_V4_BAD_JSON: batch=' + j
      );
    }

    var products =
      (json && json.products) ||
      (json && json.data && json.data.products) ||
      [];

    if (!Array.isArray(products)) {
      throw new Error(
        'WB_PRICE_V4_BAD_SHAPE: batch=' + j
      );
    }

    for (var p = 0; p < products.length; p++) {
      var product = products[p] || {};
      var id = String(product.id || product.nmId || '').trim();

      if (!id) {
        continue;
      }

      var price = wbPriceV4ExtractPrice_(product);

      out[id] = {
        id: id,
        name: String(product.name || ''),
        basic: price.basic,
        product: price.product,
        logistics: price.logistics,
        productPlusLogistics: price.productPlusLogistics,
        totalField: price.totalField,
        quantity:
          Number(product.totalQuantity || 0) || 0
      };
    }
  }

  return out;
}


function wbPriceV4ExtractPrice_(product) {
  var sizes = Array.isArray(product.sizes)
    ? product.sizes
    : [];

  var all = [];
  var withStock = [];

  for (var i = 0; i < sizes.length; i++) {
    var size = sizes[i] || {};
    var p = size.price;

    if (!p) {
      continue;
    }

    var candidate = {
      product: wbPriceV4Kopecks_(p.product),
      basic: wbPriceV4Kopecks_(p.basic),
      logistics: wbPriceV4Kopecks_(p.logistics),
      totalField: wbPriceV4Kopecks_(p.total)
    };

    if (
      !(candidate.product > 0) &&
      !(candidate.totalField > 0) &&
      !(candidate.basic > 0)
    ) {
      continue;
    }

    all.push(candidate);

    var stocks = Array.isArray(size.stocks)
      ? size.stocks
      : [];

    var qty = 0;

    for (var s = 0; s < stocks.length; s++) {
      qty += Number(
        stocks[s].qty ||
        stocks[s].quantity ||
        0
      ) || 0;
    }

    if (
      qty > 0 ||
      (
        sizes.length === 1 &&
        Number(product.totalQuantity || 0) > 0
      )
    ) {
      withStock.push(candidate);
    }
  }

  var pool = withStock.length
    ? withStock
    : all;

  if (!pool.length) {
    var fallbackProduct = wbPriceV4Kopecks_(
      product.salePriceU
    );

    var fallbackBasic = wbPriceV4Kopecks_(
      product.priceU
    );

    return {
      product: fallbackProduct,
      basic: fallbackBasic,
      logistics: 0,
      productPlusLogistics: fallbackProduct,
      totalField: fallbackProduct
    };
  }

  function minPositive_(getter) {
    var best = 0;

    for (var j = 0; j < pool.length; j++) {
      var value = Number(getter(pool[j])) || 0;

      if (value > 0 && (!(best > 0) || value < best)) {
        best = value;
      }
    }

    return best;
  }

  var minProduct = minPositive_(function(item) {
    return item.product;
  });

  var minBasic = minPositive_(function(item) {
    return item.basic;
  });

  var minLogistics = minPositive_(function(item) {
    return item.logistics;
  });

  var minProductPlusLogistics = minPositive_(function(item) {
    if (!(item.product > 0)) {
      return 0;
    }

    return item.product + (item.logistics || 0);
  });

  var minTotalField = minPositive_(function(item) {
    return item.totalField;
  });

  return {
    product: minProduct,
    basic: minBasic,
    logistics: minLogistics,
    productPlusLogistics:
      minProductPlusLogistics || minProduct,
    totalField:
      minTotalField || minProduct
  };
}
function wbPriceV4Kopecks_(value) {
  var n = Number(value);

  if (!isFinite(n) || n <= 0) {
    return 0;
  }

  return n / 100;
}


function wbPriceV4Candidate_(item, mode) {
  if (!item) {
    return 0;
  }

  var value = 0;

  if (mode === 'total_field') {
    value = item.totalField || 0;
  } else if (mode === 'product_plus_logistics') {
    value =
      item.productPlusLogistics ||
      item.product ||
      0;
  } else {
    value = item.product || 0;
  }

  /*
   * The proven legacy SPP parser writes Math.round(clientPrice) to column K.
   * Keep the exact same cell semantics so v4 cannot introduce fractional
   * rubles and artificial SPP deltas.
   */
  return value > 0
    ? Math.round(value)
    : 0;
}


function wbPriceV4Calibrate_(summaryData, fetched) {
  var modes = [
    'product',
    'product_plus_logistics',
    'total_field'
  ];

  var scored = [];

  for (var m = 0; m < modes.length; m++) {
    var mode = modes[m];
    var errors = [];
    var good = 0;

    for (var i = 0; i < summaryData.length; i++) {
      var row = summaryData[i];

      var nmId = String(
        row[WB_PUBLIC_PRICE_V4.NMID_COL - 1] || ''
      ).trim();

      var known = wbPriceV4Number_(
        row[WB_PUBLIC_PRICE_V4.CLIENT_PRICE_COL - 1]
      );

      if (!(known > 0) || !fetched[nmId]) {
        continue;
      }

      var candidate = wbPriceV4Candidate_(
        fetched[nmId],
        mode
      );

      if (!(candidate > 0)) {
        continue;
      }

      var rel = Math.abs(candidate - known) / known;
      errors.push(rel);

      if (rel <= WB_PUBLIC_PRICE_V4.GOOD_REL_ERROR) {
        good++;
      }
    }

    errors.sort(function(a, b) {
      return a - b;
    });

    var median = errors.length
      ? errors[Math.floor(errors.length / 2)]
      : 999;

    var goodShare = errors.length
      ? good / errors.length
      : 0;

    scored.push({
      mode: mode,
      rows: errors.length,
      medianRelativeError: median,
      goodShare: goodShare
    });
  }

  var passing = scored.filter(function(item) {
    return (
      item.rows >=
        WB_PUBLIC_PRICE_V4.MIN_CALIBRATION_ROWS &&
      item.medianRelativeError <=
        WB_PUBLIC_PRICE_V4.MAX_MEDIAN_REL_ERROR &&
      item.goodShare >=
        WB_PUBLIC_PRICE_V4.MIN_GOOD_SHARE
    );
  });

  var pool = passing.length
    ? passing
    : scored;

  pool.sort(function(a, b) {
    if (a.goodShare !== b.goodShare) {
      return b.goodShare - a.goodShare;
    }

    if (
      a.medianRelativeError !==
      b.medianRelativeError
    ) {
      return (
        a.medianRelativeError -
        b.medianRelativeError
      );
    }

    return b.rows - a.rows;
  });

  var best = pool[0];

  return {
    ok: passing.length > 0,
    mode: best.mode,
    rows: best.rows,
    medianRelativeError: best.medianRelativeError,
    goodShare: best.goodShare,
    allModes: scored
  };
}


function wbPriceV4WriteShadow_(
  ss,
  summaryData,
  fetched,
  calibration
) {
  var sheet = ss.getSheetByName(
    WB_PUBLIC_PRICE_V4.SOURCE_SHEET
  );

  if (!sheet) {
    sheet = ss.insertSheet(
      WB_PUBLIC_PRICE_V4.SOURCE_SHEET
    );
    sheet.hideSheet();
  }

  var rows = [[
    'timestamp',
    'nmID',
    'name',
    'seller_price_J',
    'existing_client_K',
    'v4_product_min',
    'v4_logistics_min',
    'v4_product_plus_logistics_min',
    'v4_total_field_min',
    'selected_mode',
    'selected_client_price',
    'calibration_ok'
  ]];

  var now = new Date();

  for (var i = 0; i < summaryData.length; i++) {
    var row = summaryData[i];

    var nmId = String(
      row[WB_PUBLIC_PRICE_V4.NMID_COL - 1] || ''
    ).trim();

    if (!nmId || !fetched[nmId]) {
      continue;
    }

    var item = fetched[nmId];

    rows.push([
      now,
      nmId,
      item.name,
      wbPriceV4Number_(
        row[WB_PUBLIC_PRICE_V4.SELLER_PRICE_COL - 1]
      ),
      wbPriceV4Number_(
        row[WB_PUBLIC_PRICE_V4.CLIENT_PRICE_COL - 1]
      ),
      item.product,
      item.logistics,
      item.productPlusLogistics,
      item.totalField,
      calibration.mode,
      wbPriceV4Candidate_(item, calibration.mode),
      calibration.ok ? 'YES' : 'NO'
    ]);
  }

  if (sheet.getLastRow() > 0) {
    var signature = sheet
      .getRange(1, 1, 1, 2)
      .getDisplayValues()[0];

    var first = String(signature[0] || '').trim();
    var second = String(signature[1] || '').trim();

    if (
      (first || second) &&
      (first !== 'timestamp' || second !== 'nmID')
    ) {
      throw new Error(
        'WB_PRICE_V4_SHADOW_NAME_COLLISION: лист «' +
        WB_PUBLIC_PRICE_V4.SOURCE_SHEET +
        '» уже существует и не похож на shadow-лист WB OS. ' +
        'Очистка запрещена.'
      );
    }
  }

  sheet.clearContents();

  if (sheet.getMaxRows() < rows.length) {
    sheet.insertRowsAfter(
      sheet.getMaxRows(),
      rows.length - sheet.getMaxRows()
    );
  }

  if (sheet.getMaxColumns() < rows[0].length) {
    sheet.insertColumnsAfter(
      sheet.getMaxColumns(),
      rows[0].length - sheet.getMaxColumns()
    );
  }

  sheet
    .getRange(1, 1, rows.length, rows[0].length)
    .setValues(rows);

  sheet
    .getRange(2, 1, Math.max(1, rows.length - 1), 1)
    .setNumberFormat('dd.MM.yyyy HH:mm:ss');
}


function wbPriceV4Number_(value) {
  if (
    value === null ||
    value === undefined ||
    value === ''
  ) {
    return 0;
  }

  var n = Number(
    String(value)
      .replace(/\u00A0/g, '')
      .replace(/\s/g, '')
      .replace(',', '.')
  );

  return isFinite(n) ? n : 0;
}


function wbPriceV4RecordFailure_(error) {
  wbPriceV4Diagnostics_({
    ok: false,
    fetched: 0,
    changed: 0,
    preservedMissing: 0,
    mode: '',
    calibrationRows: 0,
    medianRelativeError: '',
    goodShare: '',
    message: String(
      error && error.message
        ? error.message
        : error || 'ERROR'
    ).substring(0, 500)
  });
}


function wbPriceV4Diagnostics_(data) {
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var sheet = ss.getSheetByName('Автоматизация');

  if (!sheet) {
    return;
  }

  sheet.getRange('F27:F35').setValues([
    ['WB public price · status'],
    ['WB public price · version'],
    ['WB public price · fetched'],
    ['WB public price · changed K'],
    ['WB public price · preserved missing'],
    ['WB public price · mode'],
    ['WB public price · calibration rows'],
    ['WB public price · median error'],
    ['WB public price · good share']
  ]);

  sheet.getRange('G27:G35').setValues([
    [data.ok ? '✅ OK' : '❌ ERROR'],
    [WB_PUBLIC_PRICE_V4.VERSION + ' · LIVE / MASTER'],
    [data.fetched || 0],
    [data.changed || 0],
    [data.preservedMissing || 0],
    [data.mode || '—'],
    [data.calibrationRows || 0],
    [
      data.medianRelativeError === ''
        ? ''
        : data.medianRelativeError
    ],
    [
      data.goodShare === ''
        ? ''
        : data.goodShare
    ]
  ]);

  sheet.getRange('F27:G35').setWrap(true);

  if (!data.ok && data.message) {
    sheet.getRange('F36:G36').setValues([[
      'WB public price · last error',
      String(data.message).substring(0, 500)
    ]]);
    sheet.getRange('F36:G36').setWrap(true);
  } else {
    sheet.getRange('F36:G36').clearContent();
  }
}


function wbPriceV4CleanupLegacyTriggers_() {
  /*
   * The legacy SPP monitor also owns Telegram change alerts and _SPP_HISTORY.
   * Until that alert layer is migrated into v4, keep its scheduled trigger.
   * It only writes cells for prices it successfully fetches, so it does not
   * erase v4-only fills for products it cannot parse.
   */
  var hasLegacyAlertLayer =
    typeof sppBuildAlerts_ === 'function' &&
    typeof sppSendAlerts_ === 'function' &&
    typeof sppReadHistoryMap_ === 'function' &&
    typeof sppWriteHistory_ === 'function';

  if (hasLegacyAlertLayer) {
    Logger.log(
      'WB Public Price: старый SPP trigger сохранён ради Telegram/history alerts.'
    );
    return 0;
  }

  if (typeof ensureFinalAutomationTrigger_ !== 'function') {
    Logger.log(
      'WB Public Price: master trigger helper отсутствует; старый SPP trigger сохранён.'
    );
    return 0;
  }

  ensureFinalAutomationTrigger_();

  var legacy = {
    sppMonitorScheduledTick: true
  };

  var triggers = ScriptApp.getProjectTriggers();
  var deleted = 0;

  for (var i = 0; i < triggers.length; i++) {
    var handler = triggers[i].getHandlerFunction();

    if (legacy[handler]) {
      ScriptApp.deleteTrigger(triggers[i]);
      deleted++;
    }
  }

  if (deleted) {
    Logger.log(
      'WB Public Price v4: удалено legacy price-триггеров: ' + deleted
    );
  }

  return deleted;
}
