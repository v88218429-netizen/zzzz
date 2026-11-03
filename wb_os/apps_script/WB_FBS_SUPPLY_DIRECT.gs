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

// Canonical Sanych production workbook; the former ID was an alternate copy.
var WB_FBS_DIRECT_SUPPLY_BASE_CFG = {
  ordersSheet: '📦 FBS Заказы',
  suppliesSheet: '🚚 Поставки FBS',
  historySheet: '🕘 История статусов',
  workerSheet: '🛰 Worker Control',
  apiUrl: 'https://marketplace-api.wildberries.ru/api/v3/supplies',
  syncEveryMinutes: 30,
  orderTailRows: 2500,
  historyTailRows: 50000,
  maxPages: 30,
  pageLimit: 1000
};
var WB_FBS_DIRECT_SUPPLY_STORES = {
  SANYCH: Object.assign({}, WB_FBS_DIRECT_SUPPLY_BASE_CFG, {
    client: 'SANYCH',
    propertiesSpreadsheetId: '1-aBDZ7c5xfmVwwiNmUi9-DyfIANXmfiM5-Ti2_zg4zI',
    tokenKey: 'FBS_CLIENT__SANYCH__WB_API_TOKEN',
    spreadsheetId: '1v0qt-thJDOw3tLFdZVGhdXJdBM8rDPRz9mHlcHmuuyM'
  }),
  AIR: Object.assign({}, WB_FBS_DIRECT_SUPPLY_BASE_CFG, {
    client: 'AIR',
    orderTailRows: 1000,
    propertiesSpreadsheetId: '1SmsoG8zKx3hbTtTzS-zLekTFiWEQN8eIwHOxXq-5RHo',
    tokenKey: 'FBS_CLIENT__AIR__WB_API_TOKEN',
    spreadsheetId: '1qJvhEOIku7sOMydfrUz0PR5dkcU0sLjv433pa1MFBcM'
  }),
  HOZYAYUSHKA: Object.assign({}, WB_FBS_DIRECT_SUPPLY_BASE_CFG, {
    client: 'HOZYAYUSHKA',
    propertiesSpreadsheetId: '1cVT_H_e8a519k_Gtph6fALWnBbtBrAQ2Jb_gFO3bM64',
    tokenKey: 'FBS_CLIENT__FBS_14I5XGBA9NIG__WB_API_TOKEN',
    spreadsheetId: '1f9dxXeZxkDth8h2C9GA7L7WqAr1-Ok-Oi5YtufLYOOk'
  })
};
var WB_FBS_DIRECT_SUPPLY_CFG = WB_FBS_DIRECT_SUPPLY_STORES.SANYCH;

function wbFbsSupplySpreadsheet_() {
  return SpreadsheetApp.openById(WB_FBS_DIRECT_SUPPLY_CFG.spreadsheetId);
}

function wbFbsSupplyToken_() {
  var cfg = WB_FBS_DIRECT_SUPPLY_CFG;
  if (typeof sellmonitorClientProperties_ !== 'function') {
    throw new Error('WB_FBS_DIRECT: client credential store is unavailable');
  }
  var token = String(
    sellmonitorClientProperties_(cfg.propertiesSpreadsheetId).getProperty(cfg.tokenKey) || ''
  ).trim();
  if (token.length < 20) throw new Error('WB_FBS_DIRECT: WB API token missing for ' + cfg.client);
  return token;
}

function wbFbsSupplySetStage_(stage, detail) {
  try {
    PropertiesService.getScriptProperties().setProperty('WB_FBS_DIRECT_STAGE', JSON.stringify({
      client: String(WB_FBS_DIRECT_SUPPLY_CFG.client || ''),
      stage: String(stage || ''),
      at: new Date().toISOString(),
      detail: String(detail || '').slice(0, 240)
    }));
  } catch (ignored) {}
}

function wbFbsSupplyFetchPage_(token, cursor) {
  var url =
    WB_FBS_DIRECT_SUPPLY_CFG.apiUrl +
    '?limit=' + encodeURIComponent(WB_FBS_DIRECT_SUPPLY_CFG.pageLimit) +
    '&next=' + encodeURIComponent(cursor || 0);

  var attempts = 0;
  while (attempts < 2) {
    attempts++;
    var res = null;
    try {
      res = UrlFetchApp.fetch(url, {
        method: 'get',
        headers: {Authorization: token},
        muteHttpExceptions: true,
        timeoutSeconds: 30
      });
    } catch (fetchError) {
      var transportMessage = String(fetchError && fetchError.message ? fetchError.message : fetchError);
      if (attempts >= 2) {
        throw new Error('WB_FBS_DIRECT: supplies transport failure after 2 attempts: ' + transportMessage.slice(0, 240));
      }
      var transportWait = Math.min(15000, 1000 * Math.pow(2, attempts - 1));
      Logger.log('WB_FBS_DIRECT_SUPPLY_RETRY type=transport attempt=' + attempts + ' wait_ms=' + transportWait);
      Utilities.sleep(transportWait);
      continue;
    }
    var code = res.getResponseCode();

    if ((code === 408 || code === 429 || code >= 500) && attempts < 2) {
      var headers = res.getHeaders ? res.getHeaders() : {};
      var retry = Number(headers['Retry-After'] || headers['retry-after'] || Math.pow(2, attempts - 1));
      var retryWait = Math.max(1000, Math.min(15000, (isFinite(retry) ? retry : 2) * 1000));
      Logger.log('WB_FBS_DIRECT_SUPPLY_RETRY http=' + code + ' attempt=' + attempts + ' wait_ms=' + retryWait);
      Utilities.sleep(retryWait);
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
  throw new Error('WB_FBS_DIRECT: supplies transport/HTTP retries exhausted.');
}

function wbFbsSupplyFetchAll_(token) {
  var cursor = 0, seen = {}, byId = {}, ordered = [], scanned = 0, pages = 0, complete = false;
  var startedAt = Date.now(), maxDurationMs = 180000, maxPageRequestMs = 75000;
  for (var page = 0; page < WB_FBS_DIRECT_SUPPLY_CFG.maxPages; page++) {
    if (Date.now() - startedAt + maxPageRequestMs > maxDurationMs) {
      throw new Error('WB_FBS_DIRECT: supplies pagination TIMEOUT budget exceeded');
    }
    var cursorKey = String(cursor);
    if (seen[cursorKey]) throw new Error('WB_FBS_DIRECT: repeated supplies cursor');
    seen[cursorKey] = true;
    var pageStartedAt = Date.now();
    wbFbsSupplySetStage_('WB_PAGE_REQUEST', 'page=' + (page + 1) + ' cursor=' + String(cursor));
    var payload = wbFbsSupplyFetchPage_(token, cursor);
    wbFbsSupplySetStage_('WB_PAGE_RESPONSE', 'page=' + (page + 1) + ' supplies=' + ((payload.supplies || []).length) + ' elapsed_ms=' + (Date.now() - pageStartedAt));
    Logger.log('WB_FBS_DIRECT_SUPPLY_PAGE page=' + (page + 1) + ' supplies=' + ((payload.supplies || []).length) + ' elapsed_ms=' + (Date.now() - pageStartedAt));
    var supplies = payload.supplies || [];
    pages++;
    scanned += supplies.length;
    for (var i = 0; i < supplies.length; i++) {
      var supply = supplies[i] || {};
      var id = String(supply.id || '').trim();
      if (!id) throw new Error('WB_FBS_DIRECT: supply without id');
      if (byId[id]) continue;
      byId[id] = supply;
      ordered.push(supply);
    }
    if (!supplies.length) { complete = true; break; }
    var next = Number(payload.next || 0);
    if (!next || next === cursor) { complete = true; break; }
    cursor = next;
  }
  if (!complete) throw new Error('WB_FBS_DIRECT: supplies page limit exceeded; refusing partial write');
  ordered.sort(function(a, b) {
    var at = wbFbsSupplyDateMs_(a.createdAt), bt = wbFbsSupplyDateMs_(b.createdAt);
    if (at !== bt) return at - bt;
    return String(a.id || '').localeCompare(String(b.id || ''));
  });
  return {rows: ordered, byId: byId, scannedCount: scanned, pageCount: pages};
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
  var currentRows = sheet.getMaxRows();
  if (currentRows < neededRows) {
    wbFbsSupplySheetsApiBatchUpdate_([{
      appendDimension: {
        sheetId: sheet.getSheetId(),
        dimension: 'ROWS',
        length: neededRows - currentRows
      }
    }]);
  }
}

function wbFbsSupplyDateSerial_(value) {
  var local = Utilities.formatDate(value, Session.getScriptTimeZone(), 'yyyy-MM-dd HH:mm:ss.SSS');
  var parts = local.match(/^(\d{4})-(\d{2})-(\d{2}) (\d{2}):(\d{2}):(\d{2})\.(\d{3})$/);
  if (!parts) throw new Error('WB_FBS_DIRECT: invalid local date for Sheets comparison');
  var localUtcMs = Date.UTC(
    Number(parts[1]), Number(parts[2]) - 1, Number(parts[3]),
    Number(parts[4]), Number(parts[5]), Number(parts[6]), Number(parts[7])
  );
  return localUtcMs / 86400000 + 25569;
}

function wbFbsSupplyMatricesEqual_(expected, actual) {
  if (!Array.isArray(expected) || !Array.isArray(actual) || expected.length !== actual.length) return false;
  for (var r = 0; r < expected.length; r++) {
    var left = expected[r] || [], right = actual[r] || [];
    if (left.length !== right.length) return false;
    for (var c = 0; c < left.length; c++) {
      var a = left[c], b = right[c];
      if (a instanceof Date || b instanceof Date) {
        var aSerial = a instanceof Date ? wbFbsSupplyDateSerial_(a) : (typeof a === 'number' ? a : NaN);
        var bSerial = b instanceof Date ? wbFbsSupplyDateSerial_(b) : (typeof b === 'number' ? b : NaN);
        if (!isFinite(aSerial) || !isFinite(bSerial) || Math.abs(aSerial - bSerial) > 1e-8) return false;
      } else if ((a == null || a === '') && (b == null || b === '')) {
        continue;
      } else if (a !== b) {
        return false;
      }
    }
  }
  return true;
}

function wbFbsSupplySheetsApiRetry_(operation, call) {
  var maxAttempts = 4;
  for (var attempt = 1; attempt <= maxAttempts; attempt++) {
    try {
      return call();
    } catch (error) {
      var message = String(error && error.message ? error.message : error);
      var lower = message.toLowerCase();
      var retryable = lower.indexOf('service is currently unavailable') >= 0 ||
        lower.indexOf('service unavailable') >= 0 ||
        lower.indexOf('temporarily unavailable') >= 0 ||
        lower.indexOf('backend error') >= 0 ||
        lower.indexOf('internal error') >= 0 ||
        lower.indexOf('rate limit') >= 0 ||
        lower.indexOf('too many requests') >= 0 ||
        lower.indexOf('http 429') >= 0 ||
        lower.indexOf('http 500') >= 0 ||
        lower.indexOf('http 502') >= 0 ||
        lower.indexOf('http 503') >= 0 ||
        lower.indexOf('http 504') >= 0;
      if (!retryable || attempt >= maxAttempts) throw error;
      var waitMs = Math.min(8000, 1000 * Math.pow(2, attempt - 1));
      Logger.log('WB_FBS_SHEETS_API_RETRY operation=' + operation +
        ' attempt=' + attempt + ' wait_ms=' + waitMs +
        ' reason=' + message.slice(0, 180));
      Utilities.sleep(waitMs);
    }
  }
}

function wbFbsSupplySheetsApiRead_(rangeA1, rowCount, columnCount) {
  var response = wbFbsSupplySheetsApiRetry_('values.get', function() {
    return Sheets.Spreadsheets.Values.get(
      String(WB_FBS_DIRECT_SUPPLY_CFG.spreadsheetId),
      String(rangeA1),
      {valueRenderOption: 'UNFORMATTED_VALUE', dateTimeRenderOption: 'SERIAL_NUMBER'}
    );
  });
  var rows = (response && response.values) || [];
  var normalized = [];
  for (var r = 0; r < rowCount; r++) {
    var row = (rows[r] || []).slice(0, columnCount);
    while (row.length < columnCount) row.push('');
    normalized.push(row);
  }
  return normalized;
}

function wbFbsSupplySheetsApiWrite_(rangeA1, values) {
  var normalized = (values || []).map(function(row) {
    return (row || []).map(function(value) {
      return value instanceof Date ? wbFbsSupplyDateSerial_(value) : value;
    });
  });
  return wbFbsSupplySheetsApiRetry_('values.update', function() {
    return Sheets.Spreadsheets.Values.update(
      {values: normalized},
      String(WB_FBS_DIRECT_SUPPLY_CFG.spreadsheetId),
      String(rangeA1),
      {valueInputOption: 'RAW'}
    );
  });
}

function wbFbsSupplySheetsApiClear_(rangeA1) {
  return wbFbsSupplySheetsApiRetry_('values.clear', function() {
    return Sheets.Spreadsheets.Values.clear(
      {},
      String(WB_FBS_DIRECT_SUPPLY_CFG.spreadsheetId),
      String(rangeA1)
    );
  });
}

function wbFbsSupplySheetsApiBatchUpdate_(requests) {
  if (!requests || !requests.length) return;
  var spreadsheetId = String(WB_FBS_DIRECT_SUPPLY_CFG.spreadsheetId);
  return wbFbsSupplySheetsApiRetry_('batchUpdate', function() {
    return Sheets.Spreadsheets.batchUpdate({requests: requests}, spreadsheetId);
  });
}

function wbFbsSupplySheetsApiFormat_(requests) {
  return wbFbsSupplySheetsApiBatchUpdate_(requests);
}

function wbFbsSupplyWriteSupplySheet_(ss, supplyRows, orderCounts, checkedAt) {
  var sh = ss.getSheetByName(WB_FBS_DIRECT_SUPPLY_CFG.suppliesSheet);
  if (!sh) sh = ss.insertSheet(WB_FBS_DIRECT_SUPPLY_CFG.suppliesSheet);
  var header = ['ID поставки','createdAt','closedAt','scanDt','done','Последняя проверка',
    'Заказов в поставке','closed → scan, мин','Статус контроля'];
  wbFbsSupplySetStage_('SUPPLY_READ_EXISTING_START', '');
  var oldLast = sh.getLastRow();
  var oldRows = oldLast > 1 ? sh.getRange(2,1,oldLast-1,header.length).getValues() : [];
  wbFbsSupplySetStage_('SUPPLY_READ_EXISTING_DONE', 'rows=' + oldRows.length);
  if (!supplyRows.length && oldRows.some(function(r){return String(r[0]||'').trim();})) {
    throw new Error('WB_FBS_DIRECT: WB returned no supplies while the sheet contains existing rows; refusing empty refresh');
  }
  var current = Object.create(null), used = Object.create(null), out = [];
  for (var i=0;i<supplyRows.length;i++) {
    var item=supplyRows[i]||{}, id=String(item.id||'').trim();
    if (!id) continue;
    current[id]=item;
  }
  function makeCurrentRow_(item) {
    var id=String(item.id||'').trim();
    var created=wbFbsSupplyDate_(item.createdAt), closed=wbFbsSupplyDate_(item.closedAt), scan=wbFbsSupplyDate_(item.scanDt);
    var mins=(closed&&scan)?Math.round((scan.getTime()-closed.getTime())/60000):'';
    var status='ОТКРЫТА';
    if(scan)status='✅ SCAN WB';
    else if(closed)status='⏳ ЗАКРЫТА · ЖДЁМ SCAN';
    else if(item.done===true||String(item.done).toLowerCase()==='true')status='⚠ DONE БЕЗ ДАТ';
    return [id,created||'',closed||'',scan||'',item.done===true||String(item.done).toLowerCase()==='true',
      checkedAt,Number(orderCounts[id]||0),mins,status];
  }
  var preservedNotReturned=0,preservedDuplicateRows=0;
  for (var j=0;j<oldRows.length;j++) {
    var old=oldRows[j].slice(0,header.length),oldId=String(old[0]||'').trim();
    if(!oldId){out.push(old);continue;}
    if(current[oldId]&&!used[oldId]){
      out.push(makeCurrentRow_(current[oldId]));used[oldId]=true;continue;
    }
    old[6]=Number(orderCounts[oldId]||0);
    if(current[oldId]){
      old[8]='ДУБЛЬ ID · СТАРАЯ СТРОКА СОХРАНЕНА';preservedDuplicateRows++;
    }else{
      old[8]='НЕ ПОДТВЕРЖДЕНА В ТЕКУЩЕМ ОТВЕТЕ WB';preservedNotReturned++;
    }
    out.push(old);
  }
  for(var id in current){
    if(Object.prototype.hasOwnProperty.call(current,id)&&!used[id]){
      out.push(makeCurrentRow_(current[id]));used[id]=true;
    }
  }
  out.sort(function(a,b){
    var at=wbFbsSupplyDateMs_(a[1]),bt=wbFbsSupplyDateMs_(b[1]);
    if(at!==bt)return at-bt;
    return String(a[0]||'').localeCompare(String(b[0]||''));
  });
  wbFbsSupplySetStage_('SUPPLY_ENSURE_ROWS_START', 'rows=' + out.length);
  wbFbsSupplyEnsureRows_(sh,Math.max(2,out.length+1));
  wbFbsSupplySetStage_('SUPPLY_ENSURE_ROWS_DONE', '');
  wbFbsSupplySetStage_('SUPPLY_WRITE_HEADER_START', '');
  var sheetApiId = sh.getSheetId();
  var supplySheetA1 = "'" + String(WB_FBS_DIRECT_SUPPLY_CFG.suppliesSheet).replace(/'/g, "''") + "'!";
  wbFbsSupplySheetsApiWrite_(supplySheetA1 + 'A1:I1', [header]);
  wbFbsSupplySetStage_('SUPPLY_WRITE_HEADER_DONE', '');
  var staleRows=Math.max(0,(oldLast-1)-out.length);
  if(staleRows){
    var staleStartRowForClear=out.length+2;
    wbFbsSupplySetStage_('SUPPLY_CLEAR_STALE_START',
      'rows=' + staleRows + ' start=' + staleStartRowForClear);
    wbFbsSupplySheetsApiClear_(supplySheetA1 + 'A' + staleStartRowForClear + ':I' + (staleStartRowForClear + staleRows - 1));
    wbFbsSupplySetStage_('SUPPLY_CLEAR_STALE_DONE', '');
  }
  if(out.length){
    wbFbsSupplySetStage_('SUPPLY_WRITE_ROWS_START', 'rows=' + out.length);
    wbFbsSupplySheetsApiWrite_(supplySheetA1 + 'A2:I' + (out.length + 1), out);
    wbFbsSupplySetStage_('SUPPLY_WRITE_ROWS_DONE', 'rows=' + out.length);
  }
  // Existing rows already have these formats; formatting the whole supply
  // table on every refresh can stall large workbooks. Format only newly appended
  // rows, and still initialize formats on a first write to an empty sheet.
  var formatStartRowIndex = Math.max(1, oldLast);
  var formatEndRowIndex = out.length + 1;
  if (formatStartRowIndex < formatEndRowIndex) {
    wbFbsSupplySetStage_('SUPPLY_FORMAT_START',
      'start_row=' + (formatStartRowIndex + 1) + ' rows=' +
      (formatEndRowIndex - formatStartRowIndex));
    wbFbsSupplySheetsApiFormat_([
      {repeatCell: {range: {sheetId: sheetApiId, startRowIndex: formatStartRowIndex, endRowIndex: formatEndRowIndex,
        startColumnIndex: 1, endColumnIndex: 4},
        cell: {userEnteredFormat: {numberFormat: {type: 'DATE_TIME', pattern: 'dd.MM.yyyy HH:mm:ss'}}},
        fields: 'userEnteredFormat.numberFormat'}},
      {repeatCell: {range: {sheetId: sheetApiId, startRowIndex: formatStartRowIndex, endRowIndex: formatEndRowIndex,
        startColumnIndex: 5, endColumnIndex: 6},
        cell: {userEnteredFormat: {numberFormat: {type: 'DATE_TIME', pattern: 'dd.MM.yyyy HH:mm:ss'}}},
        fields: 'userEnteredFormat.numberFormat'}},
      {repeatCell: {range: {sheetId: sheetApiId, startRowIndex: formatStartRowIndex, endRowIndex: formatEndRowIndex,
        startColumnIndex: 6, endColumnIndex: 8},
        cell: {userEnteredFormat: {numberFormat: {type: 'NUMBER', pattern: '0'}}},
        fields: 'userEnteredFormat.numberFormat'}}
    ]);
    wbFbsSupplySetStage_('SUPPLY_FORMAT_DONE', '');
  } else {
    wbFbsSupplySetStage_('SUPPLY_FORMAT_SKIP', 'existing_rows_keep_format');
  }
  wbFbsSupplySetStage_('SUPPLY_API_WRITE_DONE', 'rows=' + out.length);
  // Verify header and written rows with one contiguous Sheets API read. Separate
  // calls can stall independently under transient spreadsheet service latency,
  // leaving a successful write stuck before timing backfill can continue.
  var expectedReadback = [header].concat(out);
  var readbackChunkRows = 1000;
  var fullReadback = [];
  wbFbsSupplySetStage_('SUPPLY_READBACK_START',
    'rows=' + expectedReadback.length + ' chunk_rows=' + readbackChunkRows);
  for (var readStart = 1; readStart <= expectedReadback.length; readStart += readbackChunkRows) {
    var readEnd = Math.min(expectedReadback.length, readStart + readbackChunkRows - 1);
    var rangeA1 = supplySheetA1 + 'A' + readStart + ':I' + readEnd;
    wbFbsSupplySetStage_('SUPPLY_READBACK_CHUNK_START',
      'range=' + readStart + ':' + readEnd);
    var readChunk = wbFbsSupplySheetsApiRead_(rangeA1, readEnd - readStart + 1, header.length);
    var expectedChunk = expectedReadback.slice(readStart - 1, readEnd);
    if (!wbFbsSupplyMatricesEqual_(expectedChunk, readChunk)) {
      throw new Error('WB_FBS_DIRECT: supply sheet readback mismatch range=' +
        readStart + ':' + readEnd);
    }
    fullReadback = fullReadback.concat(readChunk);
    wbFbsSupplySetStage_('SUPPLY_READBACK_CHUNK_DONE',
      'range=' + readStart + ':' + readEnd);
  }
  wbFbsSupplySetStage_('SUPPLY_READBACK_DONE', 'rows=' + fullReadback.length);
  var rowsReadback=fullReadback.slice(1);
  var staleCount=Math.max(0,(oldLast-1)-out.length);
  if(staleCount){
    wbFbsSupplySetStage_('SUPPLY_STALE_READBACK_START', 'rows=' + staleCount);
    var staleStartRow=out.length+2;
    var staleEndRow=staleStartRow+staleCount-1;
    var staleIds=wbFbsSupplySheetsApiRead_(supplySheetA1 + 'A' + staleStartRow + ':A' + staleEndRow, staleCount, 1);
    wbFbsSupplySetStage_('SUPPLY_STALE_READBACK_DONE', '');
    if(staleIds.some(function(row){return String((row||[])[0]||'').trim()!=='';}))
      throw new Error('WB_FBS_DIRECT: stale supply rows remain after refresh');
  }
  wbFbsSupplySetStage_('SUPPLY_FREEZE_HEADER_START', '');
  wbFbsSupplySheetsApiFormat_([{
    updateSheetProperties: {
      properties: {sheetId: sheetApiId, gridProperties: {frozenRowCount: 1}},
      fields: 'gridProperties.frozenRowCount'
    }
  }]);
  wbFbsSupplySetStage_('SUPPLY_WRITE_COMPLETE', 'rows=' + out.length);
  return {totalRows:out.length,apiRows:supplyRows.length,
    preservedNotReturned:preservedNotReturned,preservedDuplicateRows:preservedDuplicateRows,
    readbackVerified:true};
}

function wbFbsSupplySortedMap_(ss) {
  var sh = ss.getSheetByName(WB_FBS_DIRECT_SUPPLY_CFG.historySheet);
  var map = {};
  if (!sh || sh.getLastRow() < 2) return map;

  var last = sh.getLastRow();
  var start = Math.max(2, last - WB_FBS_DIRECT_SUPPLY_CFG.historyTailRows + 1);
  var count = last - start + 1;
  var fixedAtValues = sh.getRange(start, 1, count, 1).getValues();
  var orderIdValues = sh.getRange(start, 2, count, 1).getDisplayValues();
  var newWbValues = sh.getRange(start, 8, count, 1).getDisplayValues();

  for (var i = 0; i < count; i++) {
    var fixedAt = fixedAtValues[i][0];
    var orderId = String(orderIdValues[i][0] || '').trim();
    var newWb = String(newWbValues[i][0] || '').trim().toLowerCase();
    if (!orderId || newWb !== 'sorted') continue;

    var d = wbFbsSupplyDate_(fixedAt);
    if (!d) continue;
    if (!map[orderId] || d.getTime() < map[orderId].getTime()) map[orderId] = d;
  }
  return map;
}

function wbFbsSupplyBackfillOrders_(ss, suppliesById, options) {
  var sh = ss.getSheetByName(WB_FBS_DIRECT_SUPPLY_CFG.ordersSheet);
  if (!sh || sh.getLastRow() < 2) return {checked: 0, matched: 0, sorted: 0, startRow: 2, endRow: 1, complete: true};

  var last = sh.getLastRow();
  var requestedEnd = options && Number(options.endRow);
  var end = isFinite(requestedEnd) && requestedEnd >= 2 ? Math.min(last, Math.floor(requestedEnd)) : last;
  var n = Math.min(WB_FBS_DIRECT_SUPPLY_CFG.orderTailRows, end - 1);
  var start = end - n + 1;

  var aToB = sh.getRange(start, 1, n, 2).getValues(); // A:B
  var wbStatuses = sh.getRange(start, 6, n, 1).getDisplayValues(); // F
  var supplyIds = sh.getRange(start, 11, n, 1).getDisplayValues(); // K
  var complete = sh.getRange(start, 20, n, 1).getValues(); // T
  var lastChange = sh.getRange(start, 22, n, 1).getValues(); // V
  var timings = sh.getRange(start, 46, n, 6).getValues(); // AT:AY
  var sortedCols = sh.getRange(start, 58, n, 2).getValues(); // BF:BG
  var timingsBefore = timings.map(function(row) { return row.slice(); });
  var sortedBefore = sortedCols.map(function(row) { return row.slice(); });
  var sortedMap = wbFbsSupplySortedMap_(ss);

  var matched = 0;
  var sortedFilled = 0;

  for (var i = 0; i < n; i++) {
    var orderDate = aToB[i][1];       // B
    var wbStatus = String(wbStatuses[i][0] || '').toLowerCase(); // F
    var supplyId = String(supplyIds[i][0] || '').trim();   // K
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

    var orderId = String(aToB[i][0] || '').trim();
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

  var timingChanged=!wbFbsSupplyMatricesEqual_(timingsBefore,timings);
  var sortedChanged=!wbFbsSupplyMatricesEqual_(sortedBefore,sortedCols);
  if (timingChanged) {
    var ordersA1 = "'" + String(WB_FBS_DIRECT_SUPPLY_CFG.ordersSheet).replace(/'/g, "''") + "'!";
    wbFbsSupplySheetsApiWrite_(ordersA1 + 'AT' + start + ':AY' + end, timings);
    wbFbsSupplySheetsApiFormat_([{
      repeatCell: {range: {sheetId: sh.getSheetId(), startRowIndex: start - 1, endRowIndex: end,
        startColumnIndex: 45, endColumnIndex: 47},
        cell: {userEnteredFormat: {numberFormat: {type: 'DATE_TIME', pattern: 'dd.MM.yyyy HH:mm:ss'}}},
        fields: 'userEnteredFormat.numberFormat'}
    }, {
      repeatCell: {range: {sheetId: sh.getSheetId(), startRowIndex: start - 1, endRowIndex: end,
        startColumnIndex: 47, endColumnIndex: 51},
        cell: {userEnteredFormat: {numberFormat: {type: 'NUMBER', pattern: '0.00'}}},
        fields: 'userEnteredFormat.numberFormat'}
    }]);
  }
  if (sortedChanged) {
    var orderSheetA1 = "'" + String(WB_FBS_DIRECT_SUPPLY_CFG.ordersSheet).replace(/'/g, "''") + "'!";
    wbFbsSupplySheetsApiWrite_(orderSheetA1 + 'BF' + start + ':BG' + end, sortedCols);
    wbFbsSupplySheetsApiFormat_([{
      repeatCell: {range: {sheetId: sh.getSheetId(), startRowIndex: start - 1, endRowIndex: end,
        startColumnIndex: 57, endColumnIndex: 58},
        cell: {userEnteredFormat: {numberFormat: {type: 'DATE_TIME', pattern: 'dd.MM.yyyy HH:mm:ss'}}},
        fields: 'userEnteredFormat.numberFormat'}
    }, {
      repeatCell: {range: {sheetId: sh.getSheetId(), startRowIndex: start - 1, endRowIndex: end,
        startColumnIndex: 58, endColumnIndex: 59},
        cell: {userEnteredFormat: {numberFormat: {type: 'NUMBER', pattern: '0.00'}}},
        fields: 'userEnteredFormat.numberFormat'}
    }]);
  }
  var ordersApiA1 = "'" + String(WB_FBS_DIRECT_SUPPLY_CFG.ordersSheet).replace(/'/g, "''") + "'!";
  if (timingChanged) {
    var timingReadback = wbFbsSupplySheetsApiRead_(ordersApiA1 + 'AT' + start + ':AY' + end, n, 6);
    if (!wbFbsSupplyMatricesEqual_(timings, timingReadback))
      throw new Error('WB_FBS_DIRECT: order timing readback mismatch');
  }
  if (sortedChanged) {
    var sortedReadback = wbFbsSupplySheetsApiRead_(ordersApiA1 + 'BF' + start + ':BG' + end, n, 2);
    if (!wbFbsSupplyMatricesEqual_(sortedCols, sortedReadback))
      throw new Error('WB_FBS_DIRECT: sorted history readback mismatch');
  }

  return {checked: n, matched: matched, sorted: sortedFilled,
    timingReadbackVerified:true,sortedReadbackVerified:true,
    startRow: start, endRow: end, complete: start <= 2};
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
  var workerA1 = "'" + String(WB_FBS_DIRECT_SUPPLY_CFG.workerSheet).replace(/'/g, "''") + "'!";
  wbFbsSupplySheetsApiWrite_(workerA1 + 'A14:B17', labels);
  wbFbsSupplySheetsApiFormat_([{
    repeatCell: {range: {sheetId: sh.getSheetId(), startRowIndex: 14, endRowIndex: 15,
      startColumnIndex: 1, endColumnIndex: 2},
      cell: {userEnteredFormat: {numberFormat: {type: 'DATE_TIME', pattern: 'dd.MM.yyyy HH:mm:ss'}}},
      fields: 'userEnteredFormat.numberFormat'}
  }]);
}

function wbFbsSupplySyncCore_(progress) {
  var cfg=WB_FBS_DIRECT_SUPPLY_CFG,ss=wbFbsSupplySpreadsheet_(),token=wbFbsSupplyToken_(),checkedAt=new Date();
  wbFbsSupplySetStage_('CORE_START', 'continuing=' + Boolean(progress));
  progress=progress||null;
  var continuing=!!(progress&&progress.client===cfg.client&&Number(progress.nextEndRow)>=2);
  var scriptProps=PropertiesService.getScriptProperties();
  var fullScanKey='WB_FBS_DIRECT_FULL_BACKFILL_DONE_'+cfg.client;
  var fullScanAtKey='WB_FBS_DIRECT_FULL_BACKFILL_AT_'+cfg.client;
  var fullScanRowKey='WB_FBS_DIRECT_FULL_BACKFILL_ROW_'+cfg.client;
  var ordersForCheckpoint=ss.getSheetByName(cfg.ordersSheet);
  if(!ordersForCheckpoint)throw new Error('WB_FBS_DIRECT: missing '+cfg.ordersSheet);
  var currentOrderLastRow=ordersForCheckpoint.getLastRow();
  var fullScanDone=scriptProps.getProperty(fullScanKey)==='1';
  var fullScanAt=Date.parse(scriptProps.getProperty(fullScanAtKey)||'');
  var fullScanLastRow=Number(scriptProps.getProperty(fullScanRowKey)||0);
  var fullSweepDue=!fullScanDone||!isFinite(fullScanAt)||fullScanAt<=0||
    Date.now()-fullScanAt>=7*24*60*60*1000||
    (fullScanLastRow>0&&currentOrderLastRow-fullScanLastRow>WB_FBS_DIRECT_SUPPLY_CFG.orderTailRows);
  var fullSweep=continuing||fullSweepDue;
  wbFbsSupplySetStage_('FETCH_START', 'max_pages=' + WB_FBS_DIRECT_SUPPLY_CFG.maxPages);
  var fetched=wbFbsSupplyFetchAll_(token);
  wbFbsSupplySetStage_('FETCH_DONE', 'pages=' + fetched.pageCount + ' supplies=' + fetched.rows.length);
  var orders=ss.getSheetByName(cfg.ordersSheet);
  if(!orders)throw new Error('WB_FBS_DIRECT: missing '+cfg.ordersSheet);
  wbFbsSupplySetStage_('COUNT_ORDERS_START', 'continuing=' + continuing);
  var counts=continuing?null:wbFbsSupplyCountOrders_(orders);
  wbFbsSupplySetStage_('COUNT_ORDERS_DONE', 'rows=' + (counts ? Object.keys(counts).length : 0));
  wbFbsSupplySetStage_('SUPPLY_SHEET_WRITE_START', 'api_supplies=' + fetched.rows.length);
  var written=continuing?{totalRows:null,preservedNotReturned:null,preservedDuplicateRows:null,readbackVerified:false}:
    wbFbsSupplyWriteSupplySheet_(ss,fetched.rows,counts,checkedAt);
  wbFbsSupplySetStage_('SUPPLY_SHEET_WRITE_DONE', 'readback=' + Boolean(written.readbackVerified));
  wbFbsSupplySetStage_('ORDER_BACKFILL_START', 'continuing=' + continuing);
  var backfill=wbFbsSupplyBackfillOrders_(ss,fetched.byId,continuing?{endRow:progress.nextEndRow}:null);
  if(!fullSweep&&!continuing)backfill.complete=true;
  // A resumed historical backfill may have been left by an older run that
  // skipped the supply-sheet write. Refresh and read it back at the final
  // chunk so a completed cabinet always has a verified current supply list.
  if(continuing&&backfill.complete){
    counts=wbFbsSupplyCountOrders_(orders);
    written=wbFbsSupplyWriteSupplySheet_(ss,fetched.rows,counts,checkedAt);
  }
  wbFbsSupplySetStage_('ORDER_BACKFILL_DONE', 'checked=' + backfill.checked + ' matched=' + backfill.matched + ' complete=' + backfill.complete);
  var unmatchedOrderSupplyIds=continuing&&!backfill.complete?null:Object.keys(counts).filter(function(id){
    return !Object.prototype.hasOwnProperty.call(fetched.byId,id);
  }).length;
  var result={
    ok:true,client:cfg.client,checkedAt:checkedAt.toISOString(),apiPages:fetched.pageCount,
    apiSupplies:fetched.rows.length,apiSuppliesScanned:fetched.scannedCount,
    suppliesWritten:written.totalRows,preservedNotReturned:written.preservedNotReturned,
    preservedDuplicateRows:written.preservedDuplicateRows,
    supplyReadbackVerified:written.readbackVerified===true,
    orderRowsChecked:backfill.checked,orderRowsMatched:backfill.matched,
    timingReadbackVerified:backfill.timingReadbackVerified===true,
    sortedReadbackVerified:backfill.sortedReadbackVerified===true,
    orderSupplyIdsNotInCurrentApi:unmatchedOrderSupplyIds,
    sortedBackfilled:backfill.sorted,backfillStartRow:backfill.startRow,
    backfillEndRow:backfill.endRow,backfillComplete:backfill.complete,
    backfillFullSweep:fullSweep,orderLastRow:currentOrderLastRow,
    backfillRemainingRows:fullSweep?Math.max(0,backfill.startRow-2):0
  };
  var clientProps=sellmonitorClientProperties_(cfg.propertiesSpreadsheetId);
  clientProps.setProperties({
    WB_FBS_DIRECT_LAST_STATUS:backfill.complete?'OK':'BACKFILLING',
    WB_FBS_DIRECT_LAST_RESULT:JSON.stringify(result)
  });
  if(backfill.complete)clientProps.setProperty('WB_FBS_DIRECT_LAST_SUCCESS_AT',checkedAt.toISOString());
  wbFbsSupplyWriteHealth_(ss,backfill.complete?'OK':'RUNNING',JSON.stringify(result));
  Logger.log('WB_FBS_DIRECT_'+(backfill.complete?'OK ':'BACKFILLING ')+JSON.stringify(result));
  return result;
}

function wbFbsSupplySyncAllStores_(requestedClient) {
  // One cabinet per invocation keeps the official WB/API and Sheets work below the
  // Apps Script execution ceiling. GitHub Actions supplies the cabinet explicitly;
  // time-based callers may omit it and use the persisted round-robin cursor.
  var storeNames = ['SANYCH', 'AIR', 'HOZYAYUSHKA'];
  requestedClient = String(requestedClient || '').trim().toUpperCase();
  if (requestedClient && storeNames.indexOf(requestedClient) < 0) {
    throw new Error('WB_FBS_DIRECT: unsupported client ' + requestedClient);
  }
  var scriptProps = PropertiesService.getScriptProperties();
  var leaseKey = 'WB_FBS_DIRECT_ALL_STORES_LEASE';
  var cursorKey = 'WB_FBS_DIRECT_NEXT_STORE_INDEX';
  var leaseToken = Utilities.getUuid();
  var lock = LockService.getUserLock();
  if (!lock.tryLock(10000)) throw new Error('WB_FBS_DIRECT: credential store is busy');

  var index = 0;
  var progressKey = 'WB_FBS_DIRECT_BACKFILL_PROGRESS';
  wbFbsSupplySetStage_('LEASE_ACQUIRE_START', '');
  try {
    var active = null;
    try { active = JSON.parse(scriptProps.getProperty(leaseKey) || 'null'); } catch (ignored) {}
    if (active && Number(active.expiresAt) > Date.now()) {
      throw new Error('WB_FBS_DIRECT: supply sync already running');
    }
    if (requestedClient) {
      index = storeNames.indexOf(requestedClient);
    } else {
      index = Number(scriptProps.getProperty(cursorKey) || 0);
      if (!isFinite(index) || index < 0 || index >= storeNames.length || Math.floor(index) !== index) index = 0;
    }
    scriptProps.setProperty(leaseKey, JSON.stringify({
      token: leaseToken,
      expiresAt: Date.now() + 8 * 60 * 1000
    }));
    wbFbsSupplySetStage_('LEASE_ACQUIRED', 'store_index=' + index);
  } finally {
    lock.releaseLock();
  }

  var original = WB_FBS_DIRECT_SUPPLY_CFG;
  var cfg = WB_FBS_DIRECT_SUPPLY_STORES[storeNames[index]];
  WB_FBS_DIRECT_SUPPLY_CFG = cfg;
  try {
    try {
      var progress = null;
      try { progress = JSON.parse(scriptProps.getProperty(progressKey) || 'null'); } catch (ignored) {}
      if (!progress || progress.client !== cfg.client) progress = null;
      wbFbsSupplySetStage_('CORE_CALL_START', 'store_index=' + index + ' client=' + cfg.client);
      var result = wbFbsSupplySyncCore_(progress);
      wbFbsSupplySetStage_('CORE_CALL_DONE', 'store_index=' + index + ' ok=' + Boolean(result && result.ok));
      if (!result || result.ok !== true) {
        throw new Error('WB_FBS_DIRECT: client sync did not return ok=true');
      }
      var totals = {
        checked: Number(progress && progress.totalChecked || 0) + Number(result.orderRowsChecked || 0),
        matched: Number(progress && progress.totalMatched || 0) + Number(result.orderRowsMatched || 0),
        sorted: Number(progress && progress.totalSorted || 0) + Number(result.sortedBackfilled || 0)
      };
      result.backfillRowsCheckedTotal = totals.checked;
      result.backfillRowsMatchedTotal = totals.matched;
      result.backfillSortedTotal = totals.sorted;
      var complete = result.backfillComplete === true;
      if (complete) {
        if(result.backfillFullSweep===true){
          scriptProps.setProperty('WB_FBS_DIRECT_FULL_BACKFILL_DONE_'+cfg.client,'1');
          scriptProps.setProperty('WB_FBS_DIRECT_FULL_BACKFILL_AT_'+cfg.client,String(result.checkedAt||new Date().toISOString()));
          scriptProps.setProperty('WB_FBS_DIRECT_FULL_BACKFILL_ROW_'+cfg.client,String(result.orderLastRow||0));
        }
        scriptProps.deleteProperty(progressKey);
        scriptProps.setProperty(cursorKey, String((index + 1) % storeNames.length));
      } else {
        scriptProps.setProperty(progressKey, JSON.stringify({
          client: cfg.client,
          nextEndRow: result.backfillStartRow - 1,
          totalChecked: totals.checked,
          totalMatched: totals.matched,
          totalSorted: totals.sorted
        }));
        scriptProps.setProperty(cursorKey, String(index));
      }
      return {
        ok: true,
        stores: [result],
        backfillComplete: complete,
        nextClient: complete ? storeNames[(index + 1) % storeNames.length] : cfg.client
      };
    } catch (e) {
      var message = String(e && e.message ? e.message : e).slice(0, 1000);
      try {
        sellmonitorClientProperties_(cfg.propertiesSpreadsheetId).setProperties({
          WB_FBS_DIRECT_LAST_ERROR_AT: new Date().toISOString(),
          WB_FBS_DIRECT_LAST_STATUS: 'ERROR',
          WB_FBS_DIRECT_LAST_ERROR: message
        });
      } catch (ignoredProps) {}
      try {
        wbFbsSupplyWriteHealth_(wbFbsSupplySpreadsheet_(), 'ERROR', message);
      } catch (ignoredHealth) {}
      wbFbsSupplySetStage_('ERROR', message);
      Logger.log('WB_FBS_DIRECT_ERROR client=' + cfg.client + ' ' + message);
      return {ok: false, stores: [{ok: false, client: cfg.client, error: message}]};
    }
  } finally {
    WB_FBS_DIRECT_SUPPLY_CFG = original;
    if (lock.tryLock(10000)) {
      try {
        var held = null;
        try { held = JSON.parse(scriptProps.getProperty(leaseKey) || 'null'); } catch (ignored) {}
        if (held && held.token === leaseToken) scriptProps.deleteProperty(leaseKey);
        wbFbsSupplySetStage_('LEASE_RELEASED', 'store_index=' + index);
      } finally {
        lock.releaseLock();
      }
    }
  }
}

function wbFbsSupplyMaybeSync_() {
  var props = sellmonitorClientProperties_(WB_FBS_DIRECT_SUPPLY_CFG.propertiesSpreadsheetId);
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
