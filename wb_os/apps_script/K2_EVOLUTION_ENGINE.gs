/**
 * WB OS / K2 Evolution Engine v0.1.0
 *
 * This module is intentionally small and reuses the existing K2 baseline:
 *   getK2WarehouseItems_()
 *   writeK2StocksToSheet_()
 *   checkStockNeedsAndNotifyTelegram()
 *   notifyK2ApiError_()
 *   saveK2SyncSuccess_()
 *   sendTelegramMessage_()
 *
 * Goal: keep the K2 sync autonomous while dramatically reducing needless
 * Google Sheets work. When the K2 snapshot did not change, the engine only
 * refreshes a compact heartbeat and health block. Heavy sheet rewrites and
 * downstream recalculation happen only on a real data change.
 */

var K2_EV = {
  VERSION: '0.1.0',
  TRIGGER_FN: 'k2EvolutionTick',
  INTERVAL_MINUTES: 10,
  AUTOMATION_SHEET: 'Автоматизация',
  STOCK_SHEET: 'Остатки к2',
  LAST_HASH_KEY: 'K2_EV_LAST_SNAPSHOT_HASH',
  LAST_COUNT_KEY: 'K2_EV_LAST_COUNT',
  LAST_WATCHDOG_FP_KEY: 'K2_EV_LAST_WATCHDOG_FP',
  LAST_WATCHDOG_STATUS_KEY: 'K2_EV_LAST_WATCHDOG_STATUS',
  LAST_RUN_AT_KEY: 'K2_EV_LAST_RUN_AT',
  LAST_CHANGED_AT_KEY: 'K2_EV_LAST_CHANGED_AT',
  UI_READY_KEY: 'K2_EV_UI_READY',
  MAX_DROP_RATIO: 0.20,
  MIN_DROP_ABS: 5,
  WATCHDOG_MESSAGE_LIMIT: 3600
};


/**
 * One-time migration.
 * Replaces the old K2 periodic trigger with the evolution engine.
 * It does not clear notification state or credentials.
 */
function setupK2Evolution() {
  var triggers = ScriptApp.getProjectTriggers();

  for (var i = 0; i < triggers.length; i++) {
    var fn = triggers[i].getHandlerFunction();

    if (
      fn === 'syncK2StocksAndNotify' ||
      fn === K2_EV.TRIGGER_FN ||
      fn === 'processK2StockEmails'
    ) {
      ScriptApp.deleteTrigger(triggers[i]);
    }
  }

  ScriptApp
    .newTrigger(K2_EV.TRIGGER_FN)
    .timeBased()
    .everyMinutes(K2_EV.INTERVAL_MINUTES)
    .create();

  ensureK2EvolutionUi_();
  k2EvolutionTick();

  SpreadsheetApp
    .getActiveSpreadsheet()
    .toast(
      'K2 Evolution v' + K2_EV.VERSION +
      ': проверка каждые ' + K2_EV.INTERVAL_MINUTES + ' минут.',
      'WB OS',
      8
    );
}


/**
 * Main periodic loop.
 *
 * Cheap path:
 *   K2 API -> normalize -> hash -> heartbeat -> watchdog -> stop.
 *
 * Heavy path only when snapshot changed:
 *   K2 API -> validation -> sheet update -> formulas -> needs -> watchdog.
 */
function k2EvolutionTick() {
  var startedMs = Date.now();
  var lock = LockService.getScriptLock();

  if (!lock.tryLock(5000)) {
    return;
  }

  var props = PropertiesService.getScriptProperties();
  var changed = false;
  var snapshotHash = '';
  var itemCount = 0;

  try {
    ensureK2EvolutionUi_();

    var items = getK2WarehouseItems_();

    if (!items || !items.length) {
      throw new Error(
        'K2_EV_EMPTY: К2 вернул пустой список. ' +
        'Старые корректные данные оставлены без изменений.'
      );
    }

    var normalized = k2EvolutionNormalizeItems_(items);
    var validation = k2EvolutionValidateSnapshot_(normalized, props);

    if (!validation.ok) {
      throw new Error(validation.message);
    }

    itemCount = normalized.length;
    snapshotHash = k2EvolutionSnapshotHash_(normalized);

    var previousHash =
      props.getProperty(K2_EV.LAST_HASH_KEY) || '';

    changed = snapshotHash !== previousHash;

    if (changed) {
      writeK2StocksToSheet_(items);
      SpreadsheetApp.flush();

      // A short recalculation window. The old baseline waited 4 seconds on
      // every run. We only wait after a real stock change.
      Utilities.sleep(1200);

      checkStockNeedsAndNotifyTelegram();

      props.setProperty(
        K2_EV.LAST_HASH_KEY,
        snapshotHash
      );

      props.setProperty(
        K2_EV.LAST_CHANGED_AT_KEY,
        new Date().toISOString()
      );
    }

    props.setProperty(
      K2_EV.LAST_COUNT_KEY,
      String(itemCount)
    );

    props.setProperty(
      K2_EV.LAST_RUN_AT_KEY,
      new Date().toISOString()
    );

    k2EvolutionHeartbeat_(true, changed, itemCount, snapshotHash, startedMs);

    // Watchdog is deliberately checked on every successful fetch. It is
    // cheap and catches freshness/status transitions even when stock itself
    // did not change.
    SpreadsheetApp.flush();
    k2EvolutionWatchdogNotify_();

    saveK2SyncSuccess_(itemCount);

  } catch (error) {
    k2EvolutionHeartbeat_(false, changed, itemCount, snapshotHash, startedMs, error);

    try {
      notifyK2ApiError_(error);
    } catch (notifyError) {
      Logger.log(
        'K2 Evolution: не удалось отправить ошибку: ' +
        notifyError.message
      );
    }

    throw error;

  } finally {
    lock.releaseLock();
  }
}


/**
 * Normalize only fields that define business state.
 * Sorting makes the snapshot hash independent of API row order.
 */
function k2EvolutionNormalizeItems_(items) {
  var out = [];

  for (var i = 0; i < items.length; i++) {
    var item = items[i] || {};

    var sku = String(
      item.sku === null || item.sku === undefined
        ? ''
        : item.sku
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
      minStock: k2EvolutionNumber_(item.minStock),
      updatedAt: String(item.updatedAt || '')
    });
  }

  out.sort(function(a, b) {
    var ak = a.sku || ('NAME:' + a.name);
    var bk = b.sku || ('NAME:' + b.name);

    if (ak < bk) return -1;
    if (ak > bk) return 1;
    return 0;
  });

  return out;
}


/**
 * Fail closed on structural anomalies before overwriting the live stock tab.
 */
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
      x.minStock,
      x.updatedAt
    ].join('|'));
  }

  var digest = Utilities.computeDigest(
    Utilities.DigestAlgorithm.SHA_256,
    compact.join('\n'),
    Utilities.Charset.UTF_8
  );

  return Utilities
    .base64EncodeWebSafe(digest)
    .replace(/=+$/, '');
}


/**
 * Compact heartbeat. One 2x/4x write replaces many cell operations.
 */
function k2EvolutionHeartbeat_(
  ok,
  changed,
  itemCount,
  snapshotHash,
  startedMs,
  error
) {
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var sheet = ss.getSheetByName(K2_EV.AUTOMATION_SHEET);

  if (!sheet) {
    return;
  }

  var now = new Date();
  var durationMs = Math.max(0, Date.now() - startedMs);

  // Existing process heartbeat row for K2.
  sheet.getRange('B2:D2').setValues([[
    now,
    '0 мин.',
    ok ? '✅ работает' : '🔴 ошибка'
  ]]);

  // Evolution diagnostics.
  sheet.getRange('G20:G25').setValues([
    [durationMs],
    [changed ? 'ДА' : 'НЕТ'],
    [itemCount || 0],
    [snapshotHash ? snapshotHash.substring(0, 16) : ''],
    [K2_EV.VERSION],
    [ok ? 'OK' : String(
      error && error.message
        ? error.message
        : error || 'ERROR'
    ).substring(0, 500)]
  ]);

  sheet.getRange('B2').setNumberFormat(
    'dd.MM.yyyy HH:mm:ss'
  );
}


/**
 * Adds labels once. Safe to call repeatedly.
 */
function ensureK2EvolutionUi_() {
  var props = PropertiesService.getScriptProperties();

  if (props.getProperty(K2_EV.UI_READY_KEY) === '1') {
    return;
  }

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

  sheet.getRange('F20:G25').setWrap(true);

  props.setProperty(K2_EV.UI_READY_KEY, '1');
}


/**
 * Telegram state machine for the watchdog block already present in
 * Автоматизация!G9:G14.
 *
 * It sends only when the effective state changes.
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

  var previous =
    props.getProperty(K2_EV.LAST_WATCHDOG_FP_KEY) || '';

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
    message +=
      '✅ <b>K2 watchdog восстановлен</b>\n\n';
  } else {
    message +=
      '🚨 <b>K2 watchdog: состояние изменилось</b>\n\n';
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

  props.setProperty(
    K2_EV.LAST_WATCHDOG_FP_KEY,
    fingerprint
  );

  props.setProperty(
    K2_EV.LAST_WATCHDOG_STATUS_KEY,
    status
  );

  // Keep the visible block synchronized with the actual notification state.
  sheet.getRange('G17:G18').setValues([
    [fingerprint],
    [new Date()]
  ]);

  sheet.getRange('G18').setNumberFormat(
    'dd.MM.yyyy HH:mm:ss'
  );
}


/**
 * Optional manual test without touching K2 stock values.
 */
function testK2EvolutionWatchdogTelegram() {
  PropertiesService
    .getScriptProperties()
    .deleteProperty(K2_EV.LAST_WATCHDOG_FP_KEY);

  k2EvolutionWatchdogNotify_();
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
