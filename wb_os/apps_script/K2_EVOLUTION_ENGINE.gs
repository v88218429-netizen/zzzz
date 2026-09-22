/**
 * WB OS / K2 Evolution Engine v0.1.10
 *
 * Integrated mode: NO separate time trigger.
 * The existing finalAutomationTick master remains the only clock.
 *
 * Contract:
 *   master -> k2EvolutionFetchAndApply_()
 *          -> refreshAutomationStatusSheet_()
 *          -> k2EvolutionWatchdogNotify_()
 *
 * Heavy Google Sheet writes happen only when the K2 business-state snapshot
 * actually changes. Every successful API fetch still refreshes K2_LAST_SUCCESS_AT.
 */

var K2_EV = {
  VERSION: '0.1.10',
  AUTOMATION_SHEET: 'Автоматизация',
  LAST_HASH_KEY: 'K2_EV_LAST_SNAPSHOT_HASH',
  LAST_COUNT_KEY: 'K2_EV_LAST_COUNT',
  LAST_WATCHDOG_FP_KEY: 'K2_EV_LAST_WATCHDOG_FP',
  LAST_WATCHDOG_STATUS_KEY: 'K2_EV_LAST_WATCHDOG_STATUS',
  LAST_RUN_AT_KEY: 'K2_EV_LAST_RUN_AT',
  LAST_CHANGED_AT_KEY: 'K2_EV_LAST_CHANGED_AT',
  MAX_DROP_RATIO: 0.20,
  MIN_DROP_ABS: 5,
  WATCHDOG_MESSAGE_LIMIT: 3600
};


/**
 * Integrated K2 fetch/apply step for the existing master cycle.
 *
 * Returns:
 *   { itemCount, changed, hash, runtimeMs }
 *
 * Throws on empty/duplicate/suspiciously truncated snapshots.
 * On those failures the trusted live stock sheet is NOT overwritten.
 */
function k2EvolutionFetchAndApply_() {
  var startedMs = Date.now();
  var props = PropertiesService.getScriptProperties();

  var items = getK2WarehouseItems_();

  if (!items || !items.length) {
    throw new Error(
      'K2_EV_EMPTY: К2 вернул пустой список. ' +
      'Доверенные остатки оставлены без изменений.'
    );
  }

  var normalized = k2EvolutionNormalizeItems_(items);
  var validation = k2EvolutionValidateSnapshot_(normalized, props);

  if (!validation.ok) {
    throw new Error(validation.message);
  }

  var itemCount = normalized.length;
  var snapshotHash = k2EvolutionSnapshotHash_(normalized);
  var previousHash = props.getProperty(K2_EV.LAST_HASH_KEY) || '';
  var sheetHash = k2EvolutionCurrentSheetHash_();

  var sourceChanged = snapshotHash !== previousHash;
  var sheetDrift = sheetHash !== snapshotHash;
  var changed = sourceChanged || sheetDrift;

  if (changed) {
    writeK2StocksToSheet_(items);
    SpreadsheetApp.flush();

    props.setProperty(K2_EV.LAST_HASH_KEY, snapshotHash);

    if (sourceChanged) {
      props.setProperty(
        K2_EV.LAST_CHANGED_AT_KEY,
        new Date().toISOString()
      );
    }
  }

  // A successful source read is a successful sync even when business state
  // did not change. This is what keeps freshness/watchdog semantics correct.
  saveK2SyncSuccess_(itemCount);

  props.setProperty(K2_EV.LAST_COUNT_KEY, String(itemCount));
  props.setProperty(K2_EV.LAST_RUN_AT_KEY, new Date().toISOString());

  var runtimeMs = Math.max(0, Date.now() - startedMs);

  k2EvolutionDiagnostics_({
    ok: true,
    changed: changed,
    itemCount: itemCount,
    snapshotHash: snapshotHash,
    runtimeMs: runtimeMs,
    message: sourceChanged
      ? 'OK · snapshot changed · sheet updated'
      : (
          sheetDrift
            ? 'OK · trusted sheet drift repaired'
            : 'OK · no business-state change · heavy write skipped'
        )
  });

  /*
   * Cut over from legacy K2 clocks only AFTER this engine has completed one
   * successful live sync. If the new engine fails, the old K2 clock remains
   * available as a fallback instead of being deleted pre-emptively.
   */
  try {
    k2EvolutionCleanupLegacyTriggers_();
  } catch (cleanupError) {
    Logger.log(
      'K2 Evolution trigger cleanup skipped: ' +
      String(
        cleanupError && cleanupError.message
          ? cleanupError.message
          : cleanupError
      )
    );
  }

  // Ensure the independent 1-minute Ozon radar relays exist.
  // Safe to call repeatedly: helpers create only one trigger each.
  try {
    if (typeof ensureOzonRadarTelegramTrigger_ === 'function') {
      ensureOzonRadarTelegramTrigger_();
    }
    if (typeof ensureOzonRadarServerTrigger_ === 'function') {
      ensureOzonRadarServerTrigger_();
    }
  } catch (ozonRadarTriggerError) {
    Logger.log(
      'Ozon Radar trigger check failed: ' +
      String(
        ozonRadarTriggerError && ozonRadarTriggerError.message
          ? ozonRadarTriggerError.message
          : ozonRadarTriggerError
      )
    );
  }

  return {
    itemCount: itemCount,
    changed: changed,
    sourceChanged: sourceChanged,
    sheetDrift: sheetDrift,
    hash: snapshotHash,
    runtimeMs: runtimeMs
  };
}


/**
 * Fail-closed diagnostics for a caught K2 error.
 * Call from the master catch before/alongside notifyK2ApiError_().
 */
function k2EvolutionRecordFailure_(error) {
  k2EvolutionDiagnostics_({
    ok: false,
    changed: false,
    itemCount: 0,
    snapshotHash: '',
    runtimeMs: 0,
    message: String(
      error && error.message ? error.message : error || 'ERROR'
    ).substring(0, 500)
  });
}


/**
 * Normalize business state only.
 * updatedAt is intentionally excluded from the hash: a timestamp-only touch
 * must not force a full sheet rewrite.
 */
function k2EvolutionNormalizeItems_(items) {
  var out = [];

  for (var i = 0; i < items.length; i++) {
    var item = items[i] || {};

    var sku = String(
      item.sku === null || item.sku === undefined ? '' : item.sku
    ).trim();

    var name = String(item.name || '').trim();

    if (!sku && !name) {
      continue;
    }

    out.push({
      sku: sku,
      name: name,
      stock: k2EvolutionNumber_(item.stock),
      reserved: k2EvolutionNumber_(item.reserved),
      minStock: k2EvolutionNumber_(item.minStock)
    });
  }

  out.sort(function(a, b) {
    var ak = a.sku
      ? ('SKU:' + a.sku)
      : ('NAME:' + a.name);

    var bk = b.sku
      ? ('SKU:' + b.sku)
      : ('NAME:' + b.name);

    if (ak < bk) return -1;
    if (ak > bk) return 1;
    return 0;
  });

  return out;
}


function k2EvolutionValidateSnapshot_(normalized, props) {
  if (!normalized.length) {
    return {
      ok: false,
      message:
        'K2_EV_EMPTY_NORMALIZED: после нормализации нет строк.'
    };
  }

  var seen = {};
  var duplicateKeys = [];

  for (var i = 0; i < normalized.length; i++) {
    var row = normalized[i];
    var key = row.sku
      ? ('SKU:' + row.sku)
      : ('NAME:' + row.name);

    if (seen[key]) {
      duplicateKeys.push(key);
    }

    seen[key] = true;
  }

  if (duplicateKeys.length) {
    return {
      ok: false,
      message:
        'K2_EV_DUPLICATES: обнаружены дубли: ' +
        duplicateKeys.slice(0, 20).join(', ')
    };
  }

  var previousCount = Number(
    props.getProperty(K2_EV.LAST_COUNT_KEY) || 0
  );

  if (previousCount > 0 && normalized.length < previousCount) {
    var lost = previousCount - normalized.length;
    var dropRatio = lost / previousCount;

    if (
      lost >= K2_EV.MIN_DROP_ABS &&
      dropRatio >= K2_EV.MAX_DROP_RATIO
    ) {
      return {
        ok: false,
        message:
          'K2_EV_QUARANTINE: число SKU резко упало с ' +
          previousCount + ' до ' + normalized.length +
          ' (-' + Math.round(dropRatio * 100) + '%). ' +
          'Живой лист не перезаписан.'
      };
    }
  }

  return { ok: true };
}


function k2EvolutionSnapshotHash_(normalized) {
  var compact = [];

  for (var i = 0; i < normalized.length; i++) {
    var x = normalized[i];

    compact.push([
      x.sku,
      x.name,
      x.stock,
      x.reserved,
      x.minStock
    ]);
  }

  /*
   * JSON encoding avoids delimiter collisions when a SKU/name itself contains
   * "|" or a newline. Only business-state fields participate in the hash.
   */
  var digest = Utilities.computeDigest(
    Utilities.DigestAlgorithm.SHA_256,
    JSON.stringify(compact),
    Utilities.Charset.UTF_8
  );

  return Utilities
    .base64EncodeWebSafe(digest)
    .replace(/=+$/, '');
}


function k2EvolutionCurrentSheetHash_() {
  try {
    var ss = SpreadsheetApp.getActiveSpreadsheet();
    var sheetName =
      typeof K2_CFG !== 'undefined' &&
      K2_CFG &&
      K2_CFG.STOCK_SHEET
        ? K2_CFG.STOCK_SHEET
        : 'Остатки к2';

    var sheet = ss.getSheetByName(sheetName);

    if (!sheet || sheet.getLastRow() < 2) {
      return '';
    }

    var rowCount = sheet.getLastRow() - 1;
    var values = sheet
      .getRange(2, 1, rowCount, 5)
      .getValues();

    var normalized = [];

    for (var i = 0; i < values.length; i++) {
      var row = values[i];

      var sku = String(
        row[0] === null || row[0] === undefined
          ? ''
          : row[0]
      ).trim();

      var name = String(row[1] || '').trim();

      if (!sku && !name) {
        continue;
      }

      normalized.push({
        sku: sku,
        name: name,
        stock: k2EvolutionNumber_(row[2]),
        reserved: k2EvolutionNumber_(row[3]),
        minStock: k2EvolutionNumber_(row[4])
      });
    }

    normalized.sort(function(a, b) {
      var ak = a.sku
        ? ('SKU:' + a.sku)
        : ('NAME:' + a.name);

      var bk = b.sku
        ? ('SKU:' + b.sku)
        : ('NAME:' + b.name);

      if (ak < bk) return -1;
      if (ak > bk) return 1;
      return 0;
    });

    return k2EvolutionSnapshotHash_(normalized);
  } catch (error) {
    Logger.log(
      'K2 Evolution sheet-hash check failed: ' +
      String(
        error && error.message
          ? error.message
          : error
      )
    );

    /*
     * Unknown sheet state must not be treated as trusted. Returning an empty
     * hash makes the next successful K2 fetch rewrite/self-heal the sheet.
     */
    return '';
  }
}


/**
 * One compact diagnostics write per actual K2 API fetch.
 */
function k2EvolutionDiagnostics_(data) {
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var sheet = ss.getSheetByName(K2_EV.AUTOMATION_SHEET);

  if (!sheet) {
    return;
  }

  sheet.getRange('F20:F25').setValues([
    ['K2 engine · runtime ms'],
    ['K2 engine · snapshot changed'],
    ['K2 engine · fetched SKU'],
    ['K2 engine · hash'],
    ['K2 engine · version'],
    ['K2 engine · last result']
  ]);

  sheet.getRange('G20:G25').setValues([
    [data.runtimeMs || 0],
    [data.changed ? 'ДА' : 'НЕТ'],
    [data.itemCount || 0],
    [data.snapshotHash
      ? String(data.snapshotHash).substring(0, 16)
      : ''],
    [K2_EV.VERSION + ' · LIVE / MASTER'],
    [data.message || (data.ok ? 'OK' : 'ERROR')]
  ]);

  sheet.getRange('F20:G25').setWrap(true);
}


/**
 * Telegram watchdog state machine.
 *
 * It consumes the visible watchdog block already present in G9:G14.
 * Call AFTER refreshAutomationStatusSheet_(), so B2/G6 freshness is current.
 */
function k2EvolutionWatchdogNotify_() {
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var sheet = ss.getSheetByName(K2_EV.AUTOMATION_SHEET);

  if (!sheet) {
    return;
  }

  var values = sheet.getRange('G9:G14').getDisplayValues();

  var status = String(values[0][0] || '').trim();
  var missingList = String(values[1][0] || '').trim();
  var zeroList = String(values[2][0] || '').trim();
  var oldSourceCount = String(values[3][0] || '').trim();
  var belowMinCount = String(values[4][0] || '').trim();
  var belowMinList = String(values[5][0] || '').trim();

  var fingerprint = [
    status,
    'MISS=' + missingList,
    'ZERO=' + zeroList,
    'MIN=' + belowMinCount + ':' + belowMinList
  ].join('|');

  var props = PropertiesService.getScriptProperties();
  var previous = props.getProperty(K2_EV.LAST_WATCHDOG_FP_KEY) || '';
  var previousStatus =
    props.getProperty(K2_EV.LAST_WATCHDOG_STATUS_KEY) || '';

  if (fingerprint === previous) {
    return;
  }

  var message = '';

  if (
    previousStatus &&
    previousStatus.indexOf('❌') === 0 &&
    status.indexOf('✅') === 0
  ) {
    message += '✅ <b>K2 watchdog восстановлен</b>\n\n';
  } else {
    message += '🚨 <b>K2 watchdog: состояние изменилось</b>\n\n';
  }

  message +=
    'Статус: <b>' +
    k2EvolutionEscapeHtml_(status || '—') +
    '</b>\n';

  if (missingList && missingList !== '—') {
    message +=
      '\n<b>Пропавшие SKU:</b>\n' +
      k2EvolutionEscapeHtml_(
        k2EvolutionTrim_(missingList, 1200)
      ) +
      '\n';
  }

  if (zeroList && zeroList !== '—') {
    message +=
      '\n<b>Нет доступного остатка:</b>\n' +
      k2EvolutionEscapeHtml_(
        k2EvolutionTrim_(zeroList, 1400)
      ) +
      '\n';
  }

  if (
    belowMinCount &&
    belowMinCount !== '0' &&
    belowMinList &&
    belowMinList !== '—'
  ) {
    message +=
      '\n<b>Ниже min (' +
      k2EvolutionEscapeHtml_(belowMinCount) +
      '):</b>\n' +
      k2EvolutionEscapeHtml_(
        k2EvolutionTrim_(belowMinList, 1000)
      ) +
      '\n';
  }

  if (oldSourceCount && oldSourceCount !== '0') {
    message +=
      '\nℹ️ Записей источника без изменения >7 дней: <b>' +
      k2EvolutionEscapeHtml_(oldSourceCount) +
      '</b>';
  }

  if (message.length > K2_EV.WATCHDOG_MESSAGE_LIMIT) {
    message = message.substring(
      0,
      K2_EV.WATCHDOG_MESSAGE_LIMIT
    ) + '\n…';
  }

  sendTelegramMessage_(message);

  props.setProperty(K2_EV.LAST_WATCHDOG_FP_KEY, fingerprint);
  props.setProperty(K2_EV.LAST_WATCHDOG_STATUS_KEY, status);

  var visibleFingerprint = String(
    sheet.getRange('G16').getDisplayValue() || ''
  ).trim();

  sheet.getRange('G17:G18').setValues([
    [visibleFingerprint || fingerprint],
    [new Date()]
  ]);

  sheet.getRange('G18').setNumberFormat(
    'dd.MM.yyyy HH:mm:ss'
  );
}


function testK2EvolutionWatchdogTelegram() {
  PropertiesService
    .getScriptProperties()
    .deleteProperty(K2_EV.LAST_WATCHDOG_FP_KEY);

  k2EvolutionWatchdogNotify_();
}


/**
 * Migration helper. It deliberately creates NO K2 trigger.
 * It removes legacy standalone K2 triggers and preserves the master clock.
 */
function setupK2EvolutionIntegrated() {
  var triggers = ScriptApp.getProjectTriggers();
  var removed = 0;

  for (var i = 0; i < triggers.length; i++) {
    var fn = triggers[i].getHandlerFunction();

    if (
      fn === 'syncK2StocksAndNotify' ||
      fn === 'syncK2StocksOnly' ||
      fn === 'processK2StockEmails' ||
      fn === 'k2EvolutionTick'
    ) {
      ScriptApp.deleteTrigger(triggers[i]);
      removed++;
    }
  }

  if (typeof ensureFinalAutomationTrigger_ === 'function') {
    ensureFinalAutomationTrigger_();
  }

  SpreadsheetApp
    .getActiveSpreadsheet()
    .toast(
      'K2 Evolution ' + K2_EV.VERSION +
      ': standalone K2 triggers=' + removed +
      '; используется master.',
      'WB OS',
      8
    );

  return {
    ok: true,
    version: K2_EV.VERSION,
    removedStandaloneTriggers: removed
  };
}


function k2EvolutionNumber_(value) {
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


function k2EvolutionTrim_(value, limit) {
  var text = String(value || '');

  if (text.length <= limit) {
    return text;
  }

  return text.substring(0, limit) + '…';
}


function k2EvolutionEscapeHtml_(value) {
  return String(value || '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;');
}


/**
 * Self-healing source for the daily stock-history heartbeat.
 * Older deployments can have real rows in "История остатков ФФ" but miss
 * FF_STOCK_HISTORY_LAST_AT in Script Properties. In that case we recover
 * the latest K2 snapshot timestamp from the sheet and repair the property.
 */
function k2EvolutionHistoryLastAt_(props) {
  props = props || PropertiesService.getScriptProperties();

  var stored = String(
    props.getProperty('FF_STOCK_HISTORY_LAST_AT') || ''
  ).trim();

  if (stored) {
    var storedDate = new Date(stored);
    if (!isNaN(storedDate.getTime())) {
      return stored;
    }
  }

  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var sheet = ss.getSheetByName('История остатков ФФ');

  if (!sheet || sheet.getLastRow() < 2) {
    return '';
  }

  var lastRow = sheet.getLastRow();
  var scanRows = Math.min(5000, lastRow - 1);
  var startRow = lastRow - scanRows + 1;

  // B = snapshot time, C = fulfilment/source.
  var values = sheet
    .getRange(startRow, 2, scanRows, 2)
    .getValues();

  for (var i = values.length - 1; i >= 0; i--) {
    if (String(values[i][1] || '').trim() !== 'K2') {
      continue;
    }

    var raw = values[i][0];
    var date = raw instanceof Date ? raw : new Date(raw);

    if (!isNaN(date.getTime())) {
      var iso = date.toISOString();
      props.setProperty('FF_STOCK_HISTORY_LAST_AT', iso);
      return iso;
    }
  }

  return '';
}


function k2EvolutionCleanupLegacyTriggers_() {
  if (typeof ensureFinalAutomationTrigger_ !== 'function') {
    Logger.log(
      'K2 Evolution: master trigger helper отсутствует; legacy K2 triggers сохранены.'
    );
    return 0;
  }

  ensureFinalAutomationTrigger_();

  var legacy = {
    processK2StockEmails: true,
    syncK2StocksAndNotify: true,
    syncK2StocksOnly: true,
    k2EvolutionTick: true
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
      'K2 Evolution: удалено legacy K2-триггеров: ' + deleted
    );
  }

  return deleted;
}
