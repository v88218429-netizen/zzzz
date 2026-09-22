/**
 * WB Seller Price Safe Refresh v0.1.0
 *
 * Safely refreshes the official seller-price source sheet "Цены" through the
 * existing DOM library. The legacy DOM exporter clears the sheet BEFORE its
 * API calls, so this wrapper snapshots the current sheet and restores it if
 * the refresh fails or produces a suspicious result.
 */

var WB_SELLER_PRICE_SAFE = {
  VERSION: '0.1.0',
  SHEET: 'Цены',
  REFRESH_EVERY_MINUTES: 60,
  MAX_ACCEPTED_AGE_MINUTES: 180,
  POST_REFRESH_MAX_AGE_MINUTES: 10,
  MIN_ROW_RATIO: 0.60,
  LAST_SUCCESS_KEY: 'WB_SELLER_PRICE_LAST_SUCCESS_AT',
  LAST_ROWS_KEY: 'WB_SELLER_PRICE_LAST_ROWS',
  LAST_ERROR_KEY: 'WB_SELLER_PRICE_LAST_ERROR'
};


function wbSellerPriceRefreshIfDue_(force) {
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var sheet = ss.getSheetByName(
    WB_SELLER_PRICE_SAFE.SHEET
  );

  if (!sheet) {
    throw new Error(
      'WB_SELLER_PRICE_SOURCE_MISSING: лист «' +
      WB_SELLER_PRICE_SAFE.SHEET +
      '» не найден.'
    );
  }

  var initial = wbSellerPriceSnapshot_(sheet);

  if (
    !force &&
    initial.ok &&
    initial.ageMinutes >= 0 &&
    initial.ageMinutes <=
      WB_SELLER_PRICE_SAFE.REFRESH_EVERY_MINUTES
  ) {
    return {
      ok: true,
      skipped: true,
      reason: 'fresh',
      ageMinutes: initial.ageMinutes,
      rows: initial.dataRows
    };
  }

  if (
    typeof DOM === 'undefined' ||
    !DOM ||
    typeof DOM.init !== 'function' ||
    typeof DOM.exportPricesToSheet !== 'function'
  ) {
    throw new Error(
      'WB_SELLER_PRICE_DOM_MISSING: DOM library price exporter недоступен.'
    );
  }

  var lock = LockService.getScriptLock();

  if (!lock.tryLock(30000)) {
    throw new Error(
      'WB_SELLER_PRICE_LOCK_BUSY: другой writer уже выполняется.'
    );
  }

  try {
    sheet = ss.getSheetByName(
      WB_SELLER_PRICE_SAFE.SHEET
    );

    var before = wbSellerPriceSnapshot_(sheet);

    if (
      !force &&
      before.ok &&
      before.ageMinutes >= 0 &&
      before.ageMinutes <=
        WB_SELLER_PRICE_SAFE.REFRESH_EVERY_MINUTES
    ) {
      return {
        ok: true,
        skipped: true,
        reason: 'fresh_after_lock',
        ageMinutes: before.ageMinutes,
        rows: before.dataRows
      };
    }

    var backup = wbSellerPriceBackup_(sheet);

    try {
      DOM.init(ss);
      DOM.exportPricesToSheet();
      SpreadsheetApp.flush();

      var afterSheet = ss.getSheetByName(
        WB_SELLER_PRICE_SAFE.SHEET
      );

      var after = wbSellerPriceSnapshot_(
        afterSheet
      );

      wbSellerPriceValidateRefresh_(
        before,
        after
      );

      var props =
        PropertiesService.getScriptProperties();

      props.setProperties({
        WB_SELLER_PRICE_LAST_SUCCESS_AT:
          new Date().toISOString(),
        WB_SELLER_PRICE_LAST_ROWS:
          String(after.dataRows)
      });

      props.deleteProperty(
        WB_SELLER_PRICE_SAFE.LAST_ERROR_KEY
      );

      return {
        ok: true,
        skipped: false,
        rows: after.dataRows,
        ageMinutes: after.ageMinutes
      };
    } catch (error) {
      wbSellerPriceRestore_(sheet, backup);
      SpreadsheetApp.flush();

      PropertiesService
        .getScriptProperties()
        .setProperty(
          WB_SELLER_PRICE_SAFE.LAST_ERROR_KEY,
          String(
            error && error.message
              ? error.message
              : error
          ).substring(0, 500)
        );

      throw new Error(
        'WB_SELLER_PRICE_REFRESH_ROLLBACK: ' +
        String(
          error && error.message
            ? error.message
            : error
        )
      );
    }
  } finally {
    lock.releaseLock();
  }
}


function wbSellerPriceSnapshot_(sheet) {
  if (!sheet) {
    return {
      ok: false,
      reason: 'missing_sheet',
      dataRows: 0,
      cabinetCount: 0,
      latestAtMs: 0,
      ageMinutes: 999999
    };
  }

  var lastRow = sheet.getLastRow();

  if (lastRow < 2) {
    return {
      ok: false,
      reason: 'no_data',
      dataRows: 0,
      cabinetCount: 0,
      latestAtMs: 0,
      ageMinutes: 999999
    };
  }

  var headers = sheet
    .getRange(1, 1, 1, 8)
    .getDisplayValues()[0];

  if (
    String(headers[0] || '').trim() !==
      'Дата выгрузки' ||
    String(headers[1] || '').trim() !==
      'Кабинет' ||
    String(headers[2] || '').trim() !==
      'Артикул WB' ||
    String(headers[7] || '').trim() !==
      'Цена со скидкой'
  ) {
    return {
      ok: false,
      reason: 'layout_mismatch',
      dataRows: Math.max(0, lastRow - 1),
      cabinetCount: 0,
      latestAtMs: 0,
      ageMinutes: 999999
    };
  }

  var values = sheet
    .getRange(2, 1, lastRow - 1, 3)
    .getValues();

  var latestAtMs = 0;
  var cabinets = Object.create(null);
  var dataRows = 0;

  for (var i = 0; i < values.length; i++) {
    var nmId = String(
      values[i][2] || ''
    ).trim();

    if (!nmId) {
      continue;
    }

    dataRows++;

    var cabinet = String(
      values[i][1] || ''
    ).trim();

    if (cabinet) {
      cabinets[cabinet] = true;
    }

    var atMs = wbSellerPriceToMillis_(
      values[i][0]
    );

    if (atMs > latestAtMs) {
      latestAtMs = atMs;
    }
  }

  var ageMinutes = latestAtMs > 0
    ? (Date.now() - latestAtMs) / 60000
    : 999999;

  return {
    ok:
      dataRows > 0 &&
      latestAtMs > 0 &&
      ageMinutes >= -5,
    reason: '',
    dataRows: dataRows,
    cabinetCount: Object.keys(cabinets).length,
    latestAtMs: latestAtMs,
    ageMinutes: ageMinutes
  };
}


function wbSellerPriceIsFreshForRepair_(
  sheet
) {
  var snap = wbSellerPriceSnapshot_(sheet);

  return Boolean(
    snap.ok &&
    snap.ageMinutes >= 0 &&
    snap.ageMinutes <=
      WB_SELLER_PRICE_SAFE.MAX_ACCEPTED_AGE_MINUTES
  );
}


function wbSellerPriceBackup_(sheet) {
  var lastRow = Math.max(
    1,
    sheet.getLastRow()
  );

  var width = 11;
  var range = sheet.getRange(
    1,
    1,
    lastRow,
    width
  );

  var formulas = range.getFormulas();

  for (var r = 0; r < formulas.length; r++) {
    for (var c = 0; c < formulas[r].length; c++) {
      if (formulas[r][c]) {
        throw new Error(
          'WB_SELLER_PRICE_BACKUP_UNSAFE: в листе «Цены» обнаружены формулы; автообновление запрещено.'
        );
      }
    }
  }

  return {
    rows: lastRow,
    width: width,
    values: range.getValues()
  };
}


function wbSellerPriceRestore_(
  sheet,
  backup
) {
  var rowsToClear = Math.max(
    sheet.getLastRow(),
    backup.rows
  );

  if (rowsToClear > 0) {
    sheet
      .getRange(
        1,
        1,
        rowsToClear,
        backup.width
      )
      .clearContent();
  }

  sheet
    .getRange(
      1,
      1,
      backup.values.length,
      backup.width
    )
    .setValues(backup.values);

  sheet
    .getRange(1, 1, 1, backup.width)
    .setBackground('#4285f4')
    .setFontColor('#ffffff')
    .setFontWeight('bold');

  sheet.setFrozenRows(1);
}


function wbSellerPriceValidateRefresh_(
  before,
  after
) {
  if (!after.ok) {
    throw new Error(
      'WB_SELLER_PRICE_BAD_RESULT: ' +
      (after.reason || 'invalid snapshot')
    );
  }

  if (
    after.ageMinutes < -5 ||
    after.ageMinutes >
      WB_SELLER_PRICE_SAFE.POST_REFRESH_MAX_AGE_MINUTES
  ) {
    throw new Error(
      'WB_SELLER_PRICE_STALE_AFTER_REFRESH: age=' +
      Math.round(after.ageMinutes) +
      ' min.'
    );
  }

  if (
    before &&
    before.dataRows >= 20
  ) {
    var minRows = Math.floor(
      before.dataRows *
      WB_SELLER_PRICE_SAFE.MIN_ROW_RATIO
    );

    if (after.dataRows < minRows) {
      throw new Error(
        'WB_SELLER_PRICE_SUSPICIOUS_DROP: before=' +
        before.dataRows +
        '; after=' +
        after.dataRows +
        '; min=' +
        minRows
      );
    }
  }

  if (
    before &&
    before.cabinetCount > 0 &&
    after.cabinetCount <
      before.cabinetCount
  ) {
    throw new Error(
      'WB_SELLER_PRICE_CABINET_DROP: before=' +
      before.cabinetCount +
      '; after=' +
      after.cabinetCount
    );
  }
}


function wbSellerPriceToMillis_(value) {
  if (
    Object.prototype.toString.call(value) ===
      '[object Date]'
  ) {
    var time = value.getTime();
    return isFinite(time) ? time : 0;
  }

  var s = String(value || '').trim();

  if (!s) {
    return 0;
  }

  var match = s.match(
    /^(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2}):(\d{2})$/
  );

  if (match) {
    var iso =
      match[1] + '-' +
      match[2] + '-' +
      match[3] + 'T' +
      match[4] + ':' +
      match[5] + ':' +
      match[6] + '+03:00';

    var parsed = new Date(iso).getTime();

    return isFinite(parsed)
      ? parsed
      : 0;
  }

  var fallback = new Date(s).getTime();

  return isFinite(fallback)
    ? fallback
    : 0;
}
