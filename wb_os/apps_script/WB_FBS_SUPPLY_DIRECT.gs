/**
 * WB FBS DIRECT SUPPLY SYNC
 *
 * Permanent path: Google Apps Script -> Wildberries Marketplace API.
 * Railway / external export services are NOT required.
 *
 * This module:
 * 1) loads /api/v3/supplies directly from WB;
 * 2) refreshes "🚚 Поставки FBS";
 * 3) backfills order-level supply timings in AT:AY;
 * 4) reconstructs first sorted time from "🕘 История статусов" and fills BF:BG;
 * 5) is throttled and lock-protected, so it is safe to call from the fast order loop.
 */

var WB_FBS_DIRECT_SUPPLY_CFG = {
  spreadsheetId: '1_rMW6w4a7ZKhsWAyVo4B4Hn2x1cXwJWVW7K4JAHpjI8',
  ordersSheet: '📦 FBS Заказы',
  suppliesSheet: '🚚 Поставки FBS',
  historySheet: '🕘 История статусов',
  workerSheet: '🛰 Worker Control',
  apiUrl: 'https://marketplace-api.wildberries.ru/api/v3/supplies',
  syncEveryMinutes: 30,
  orderTailRows: 20000,
  historyTailRows: 50000,
  maxPages: 30,
  pageLimit: 1000
};

function wbFbsSupplySpreadsheet_() {
  return SpreadsheetApp.openById(WB_FBS_DIRECT_SUPPLY_CFG.spreadsheetId);
}

function wbFbsSupplyToken_() {
  var stores = [
    PropertiesService.getScriptProperties(),
    PropertiesService.getDocumentProperties(),
    PropertiesService.getUserProperties()
  ];
  var keys = [
    'WB_API_TOKEN_AP',
    'WB_API_TOKEN',
    'WB_TOKEN',
    'WB_API_KEY',
    'WILDBERRIES_API_TOKEN'
  ];
  for (var s = 0; s < stores.length; s++) {
    var props = stores[s];
    if (!props) continue;
    for (var i = 0; i < keys.length; i++) {
      var token = String(props.getProperty(keys[i]) || '').trim();
      if (token) return token;
    }
  }

  // Compatibility with older FBS builds if one of these helpers exists.
  var helpers = [
    'getWbApiToken_',
    'getWbToken_',
    'wbGetApiToken_',
    'getWBApiToken_'
  ];
  for (var h = 0; h < helpers.length; h++) {
    try {
      var fn = this[helpers[h]];
      if (typeof fn === 'function') {
        var t = String(fn() || '').trim();
        if (t) return t;
      }
    } catch (ignored) {}
  }

  throw new Error(
    'WB_FBS_DIRECT: WB API token not found in Script Properties ' +
    '(expected WB_API_TOKEN_AP or WB_API_TOKEN).'
  );
}

function wbFbsSupplyFetchPage_(token, cursor) {
  var url =
    WB_FBS_DIRECT_SUPPLY_CFG.apiUrl +
    '?limit=' + encodeURIComponent(WB_FBS_DIRECT_SUPPLY_CFG.pageLimit) +
    '&next=' + encodeURIComponent(cursor || 0);

  var attempts = 0;
  while (attempts < 5) {
    attempts++;
    var res = UrlFetchApp.fetch(url, {
      method: 'get',
      headers: {Authorization: token},
      muteHttpExceptions: true
    });
    var code = res.getResponseCode();

    if (code === 429 && attempts < 5) {
      var headers = res.getHeaders ? res.getHeaders() : {};
      var retry = Number(headers['Retry-After'] || headers['retry-after'] || 2);
      Utilities.sleep(Math.max(1000, Math.min(15000, (isFinite(retry) ? retry : 2) * 1000)));
      continue;
    }

    if (code < 200 || code >= 300) {
      throw new Error(
        'WB_FBS_DIRECT: supplies HTTP ' + code + ' — ' +
        String(res.getContentText() || '').slice(0, 400)
      );
    }

    var payload;
    try {
      payload = JSON.parse(res.getContentText());
    } catch (e) {
      throw new Error('WB_FBS_DIRECT: invalid supplies JSON: ' + e.message);
    }
    return payload || {};
  }
  throw new Error('WB_FBS_DIRECT: supplies rate-limit retries exhausted.');
}

function wbFbsSupplyFetchAll_(token) {
  var cursor = 0;
  var seen = {};
  var byId = {};
  var ordered = [];

  for (var page = 0; page < WB_FBS_DIRECT_SUPPLY_CFG.maxPages; page++) {
    if (seen[String(cursor)]) break;
    seen[String(cursor)] = true;

    var payload = wbFbsSupplyFetchPage_(token, cursor);
    var supplies = payload.supplies || [];

    for (var i = 0; i < supplies.length; i++) {
      var s = supplies[i] || {};
      var id = String(s.id || '').trim();
      if (!id || byId[id]) continue;
      byId[id] = s;
      ordered.push(s);
    }

    if (!supplies.length) break;
    var next = Number(payload.next || 0);
    if (!next || next === cursor) break;
    cursor = next;
  }

  ordered.sort(function(a, b) {
    var at = wbFbsSupplyDateMs_(a.createdAt);
    var bt = wbFbsSupplyDateMs_(b.createdAt);
    if (at !== bt) return at - bt;
    return String(a.id || '').localeCompare(String(b.id || ''));
  });

  return {rows: ordered, byId: byId};
}

function wbFbsSupplyDate_(value) {
  if (!value) return '';
  if (value instanceof Date) return isNaN(value.getTime()) ? '' : value;
  var d = new Date(String(value));
  return isNaN(d.getTime()) ? '' : d;
}

function wbFbsSupplyDateMs_(value) {
  var d = wbFbsSupplyDate_(value);
  return d instanceof Date ? d.getTime() : 0;
}

function wbFbsSupplyHours_(fromValue, toValue) {
  var a = wbFbsSupplyDateMs_(fromValue);
  var b = wbFbsSupplyDateMs_(toValue);
  if (!a || !b) return '';
  return (b - a) / 3600000;
}

function wbFbsSupplyCountOrders_(ordersSheet) {
  var last = ordersSheet.getLastRow();
  var counts = {};
  if (last < 2) return counts;
  var ids = ordersSheet.getRange(2, 11, last - 1, 1).getDisplayValues();
  for (var i = 0; i < ids.length; i++) {
    var id = String(ids[i][0] || '').trim();
    if (!id) continue;
    counts[id] = (counts[id] || 0) + 1;
  }
  return counts;
}

function wbFbsSupplyEnsureRows_(sheet, neededRows) {
  if (sheet.getMaxRows() < neededRows) {
    sheet.insertRowsAfter(sheet.getMaxRows(), neededRows - sheet.getMaxRows());
  }
}

function wbFbsSupplyWriteSupplySheet_(ss, supplyRows, orderCounts, checkedAt) {
  var sh = ss.getSheetByName(WB_FBS_DIRECT_SUPPLY_CFG.suppliesSheet);
  if (!sh) sh = ss.insertSheet(WB_FBS_DIRECT_SUPPLY_CFG.suppliesSheet);

  var header = [
    'ID поставки',
    'createdAt',
    'closedAt',
    'scanDt',
    'done',
    'Последняя проверка',
    'Заказов в поставке',
    'closed → scan, мин',
    'Статус контроля'
  ];

  wbFbsSupplyEnsureRows_(sh, Math.max(2, supplyRows.length + 1));
  sh.getRange(1, 1, 1, header.length).setValues([header]);

  var out = [];
  for (var i = 0; i < supplyRows.length; i++) {
    var s = supplyRows[i] || {};
    var id = String(s.id || '').trim();
    var created = wbFbsSupplyDate_(s.createdAt);
    var closed = wbFbsSupplyDate_(s.closedAt);
    var scan = wbFbsSupplyDate_(s.scanDt);
    var mins = '';
    if (closed && scan) {
      mins = Math.round((scan.getTime() - closed.getTime()) / 60000);
    }

    var status = 'ОТКРЫТА';
    if (scan) status = '✅ SCAN WB';
    else if (closed) status = '⏳ ЗАКРЫТА · ЖДЁМ SCAN';
    else if (s.done === true || String(s.done).toLowerCase() === 'true') status = '⚠ DONE БЕЗ ДАТ';

    out.push([
      id,
      created || '',
      closed || '',
      scan || '',
      s.done === true || String(s.done).toLowerCase() === 'true',
      checkedAt,
      Number(orderCounts[id] || 0),
      mins,
      status
    ]);
  }

  var oldLast = sh.getLastRow();
  if (oldLast > 1) sh.getRange(2, 1, oldLast - 1, header.length).clearContent();
  if (out.length) sh.getRange(2, 1, out.length, header.length).setValues(out);

  if (out.length) {
    sh.getRange(2, 2, out.length, 3).setNumberFormat('dd.MM.yyyy HH:mm:ss');
    sh.getRange(2, 6, out.length, 1).setNumberFormat('dd.MM.yyyy HH:mm:ss');
    sh.getRange(2, 7, out.length, 2).setNumberFormat('0');
  }
  sh.setFrozenRows(1);
  return out.length;
}

function wbFbsSupplySortedMap_(ss) {
  var sh = ss.getSheetByName(WB_FBS_DIRECT_SUPPLY_CFG.historySheet);
  var map = {};
  if (!sh || sh.getLastRow() < 2) return map;

  var last = sh.getLastRow();
  var start = Math.max(2, last - WB_FBS_DIRECT_SUPPLY_CFG.historyTailRows + 1);
  var values = sh.getRange(start, 1, last - start + 1, 8).getValues();

  for (var i = 0; i < values.length; i++) {
    var fixedAt = values[i][0];
    var orderId = String(values[i][1] || '').trim();
    var newWb = String(values[i][7] || '').trim().toLowerCase();
    if (!orderId || newWb !== 'sorted') continue;

    var d = wbFbsSupplyDate_(fixedAt);
    if (!d) continue;
    if (!map[orderId] || d.getTime() < map[orderId].getTime()) map[orderId] = d;
  }
  return map;
}

function wbFbsSupplyBackfillOrders_(ss, suppliesById) {
  var sh = ss.getSheetByName(WB_FBS_DIRECT_SUPPLY_CFG.ordersSheet);
  if (!sh || sh.getLastRow() < 2) return {checked: 0, matched: 0, sorted: 0};

  var last = sh.getLastRow();
  var start = Math.max(2, last - WB_FBS_DIRECT_SUPPLY_CFG.orderTailRows + 1);
  var n = last - start + 1;

  var orderIds = sh.getRange(start, 1, n, 1).getDisplayValues(); // A
  var bToK = sh.getRange(start, 2, n, 10).getValues(); // B:K
  var complete = sh.getRange(start, 20, n, 1).getValues(); // T
  var lastChange = sh.getRange(start, 22, n, 1).getValues(); // V
  var timings = sh.getRange(start, 46, n, 6).getValues(); // AT:AY
  var sortedCols = sh.getRange(start, 58, n, 2).getValues(); // BF:BG
  var sortedMap = wbFbsSupplySortedMap_(ss);

  var matched = 0;
  var sortedFilled = 0;

  for (var i = 0; i < n; i++) {
    var orderDate = bToK[i][0];       // B
    var wbStatus = String(bToK[i][4] || '').toLowerCase(); // F
    var supplyId = String(bToK[i][9] || '').trim();        // K
    var completedAt = complete[i][0]; // T
    var supply = supplyId ? suppliesById[supplyId] : null;

    if (supply) {
      matched++;
      var closed = wbFbsSupplyDate_(supply.closedAt);
      var scan = wbFbsSupplyDate_(supply.scanDt);

      if (closed) timings[i][0] = closed; // AT
      if (scan) timings[i][1] = scan;     // AU

      if (closed && completedAt) timings[i][2] = wbFbsSupplyHours_(completedAt, closed); // AV
      if (scan && completedAt) timings[i][3] = wbFbsSupplyHours_(completedAt, scan);     // AW
      if (scan && closed) timings[i][4] = wbFbsSupplyHours_(closed, scan);                // AX
      if (scan && orderDate) timings[i][5] = wbFbsSupplyHours_(orderDate, scan);          // AY
    }

    var orderId = String(orderIds[i][0] || '').trim();
    var firstSorted = sortedCols[i][0];

    if (!firstSorted && orderId && sortedMap[orderId]) {
      firstSorted = sortedMap[orderId];
      sortedCols[i][0] = firstSorted;
      sortedFilled++;
    } else if (!firstSorted && wbStatus === 'sorted') {
      // Fallback for a newly observed sorted state before the history row is flushed.
      firstSorted = wbFbsSupplyDate_(lastChange[i][0]); // V
      if (firstSorted) {
        sortedCols[i][0] = firstSorted;
        sortedFilled++;
      }
    }

    var scanForSorted = timings[i][1];
    if (firstSorted && scanForSorted) {
      var h = wbFbsSupplyHours_(scanForSorted, firstSorted);
      if (h !== '' && h >= 0) sortedCols[i][1] = h;
    }
  }

  sh.getRange(start, 46, n, 6).setValues(timings);
  sh.getRange(start, 58, n, 2).setValues(sortedCols);

  sh.getRange(start, 46, n, 2).setNumberFormat('dd.MM.yyyy HH:mm:ss');
  sh.getRange(start, 48, n, 4).setNumberFormat('0.00');
  sh.getRange(start, 58, n, 1).setNumberFormat('dd.MM.yyyy HH:mm:ss');
  sh.getRange(start, 59, n, 1).setNumberFormat('0.00');

  return {checked: n, matched: matched, sorted: sortedFilled};
}

function wbFbsSupplyWriteHealth_(ss, state, detail) {
  var sh = ss.getSheetByName(WB_FBS_DIRECT_SUPPLY_CFG.workerSheet);
  if (!sh) return;
  var now = new Date();
  var labels = [
    ['Supply Sync', state],
    ['Supply Last Check', now],
    ['Supply Detail', String(detail || '').slice(0, 500)],
    ['Supply Source', 'DIRECT WB /api/v3/supplies']
  ];
  sh.getRange(14, 1, labels.length, 2).setValues(labels);
  sh.getRange(15, 2).setNumberFormat('dd.MM.yyyy HH:mm:ss');
}

function wbFbsSupplySyncCore_() {
  var ss = wbFbsSupplySpreadsheet_();
  var token = wbFbsSupplyToken_();
  var checkedAt = new Date();

  var fetched = wbFbsSupplyFetchAll_(token);
  var orders = ss.getSheetByName(WB_FBS_DIRECT_SUPPLY_CFG.ordersSheet);
  if (!orders) throw new Error('WB_FBS_DIRECT: missing ' + WB_FBS_DIRECT_SUPPLY_CFG.ordersSheet);

  var counts = wbFbsSupplyCountOrders_(orders);
  var supplyCount = wbFbsSupplyWriteSupplySheet_(ss, fetched.rows, counts, checkedAt);
  var backfill = wbFbsSupplyBackfillOrders_(ss, fetched.byId);

  SpreadsheetApp.flush();

  var result = {
    ok: true,
    at: checkedAt.toISOString(),
    supplies: supplyCount,
    orderRowsChecked: backfill.checked,
    orderRowsMatched: backfill.matched,
    sortedBackfilled: backfill.sorted
  };

  PropertiesService.getScriptProperties().setProperties({
    WB_FBS_DIRECT_LAST_SUCCESS_AT: checkedAt.toISOString(),
    WB_FBS_DIRECT_LAST_STATUS: 'OK',
    WB_FBS_DIRECT_LAST_RESULT: JSON.stringify(result)
  });

  wbFbsSupplyWriteHealth_(ss, 'OK', JSON.stringify(result));
  Logger.log('WB_FBS_DIRECT_OK ' + JSON.stringify(result));
  return result;
}

function wbFbsSupplyMaybeSync_() {
  var props = PropertiesService.getScriptProperties();
  var last = Date.parse(String(props.getProperty('WB_FBS_DIRECT_LAST_SUCCESS_AT') || ''));
  if (isFinite(last) &&
      (Date.now() - last) < WB_FBS_DIRECT_SUPPLY_CFG.syncEveryMinutes * 60000) {
    return {ok: true, skipped: true, reason: 'fresh'};
  }

  var lock = LockService.getUserLock();
  if (!lock.tryLock(1000)) return {ok: true, skipped: true, reason: 'locked'};

  try {
    // Re-check after acquiring the lock.
    last = Date.parse(String(props.getProperty('WB_FBS_DIRECT_LAST_SUCCESS_AT') || ''));
    if (isFinite(last) &&
        (Date.now() - last) < WB_FBS_DIRECT_SUPPLY_CFG.syncEveryMinutes * 60000) {
      return {ok: true, skipped: true, reason: 'fresh_after_lock'};
    }
    return wbFbsSupplySyncCore_();
  } catch (e) {
    var msg = String(e && e.message ? e.message : e);
    props.setProperties({
      WB_FBS_DIRECT_LAST_ERROR_AT: new Date().toISOString(),
      WB_FBS_DIRECT_LAST_STATUS: 'ERROR',
      WB_FBS_DIRECT_LAST_ERROR: msg.slice(0, 1000)
    });
    try {
      wbFbsSupplyWriteHealth_(wbFbsSupplySpreadsheet_(), 'ERROR', msg);
    } catch (ignored) {}
    Logger.log('WB_FBS_DIRECT_ERROR ' + msg);
    return {ok: false, error: msg};
  } finally {
    lock.releaseLock();
  }
}

function wbFbsSupplySyncNow() {
  var lock = LockService.getUserLock();
  if (!lock.tryLock(30000)) throw new Error('WB_FBS_DIRECT: another sync is running');
  try {
    return JSON.stringify(wbFbsSupplySyncCore_());
  } finally {
    lock.releaseLock();
  }
}

function wbFbsSupplyEnsureTrigger() {
  var handler = 'wbFbsSupplyMaybeSync_';
  var triggers = ScriptApp.getProjectTriggers();
  var keep = null;

  for (var i = 0; i < triggers.length; i++) {
    if (triggers[i].getHandlerFunction() !== handler) continue;
    if (!keep) keep = triggers[i];
    else ScriptApp.deleteTrigger(triggers[i]);
  }

  if (!keep) {
    ScriptApp.newTrigger(handler).timeBased().everyMinutes(30).create();
  }
  return 'WB_FBS_DIRECT_TRIGGER_OK';
}
