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
  if (sheet.getMaxRows() < neededRows) {
    sheet.insertRowsAfter(sheet.getMaxRows(), neededRows - sheet.getMaxRows());
  }
}

function wbFbsSupplyMatricesEqual_(expected, actual) {
  if (!Array.isArray(expected) || !Array.isArray(actual) || expected.length !== actual.length) return false;
  for (var r = 0; r < expected.length; r++) {
    var left = expected[r] || [], right = actual[r] || [];
    if (left.length !== right.length) return false;
    for (var c = 0; c < left.length; c++) {
      var a = left[c], b = right[c];
      if (a instanceof Date || b instanceof Date) {
        if (!(a instanceof Date) || !(b instanceof Date) || a.getTime() !== b.getTime()) return false;
      } else if ((a == null || a === '') && (b == null || b === '')) {
        continue;
      } else if (a !== b) {
        return false;
      }
    }
  }
  return true;
}

function wbFbsSupplyWriteSupplySheet_(ss, supplyRows, orderCounts, checkedAt) {
  var sh = ss.getSheetByName(WB_FBS_DIRECT_SUPPLY_CFG.suppliesSheet);
  if (!sh) sh = ss.insertSheet(WB_FBS_DIRECT_SUPPLY_CFG.suppliesSheet);
  var header = ['ID поставки','createdAt','closedAt','scanDt','done','Последняя проверка',
    'Заказов в поставке','closed → scan, мин','Статус контроля'];
  var oldLast = sh.getLastRow();
  var oldRows = oldLast > 1 ? sh.getRange(2,1,oldLast-1,header.length).getValues() : [];
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
  wbFbsSupplyEnsureRows_(sh,Math.max(2,out.length+1));
  sh.getRange(1,1,1,header.length).setValues([header]);
  if(oldLast>1)sh.getRange(2,1,oldLast-1,header.length).clearContent();
  if(out.length)sh.getRange(2,1,out.length,header.length).setValues(out);
  if(out.length){
    sh.getRange(2,2,out.length,3).setNumberFormat('dd.MM.yyyy HH:mm:ss');
    sh.getRange(2,6,out.length,1).setNumberFormat('dd.MM.yyyy HH:mm:ss');
    sh.getRange(2,7,out.length,2).setNumberFormat('0');
  }
  SpreadsheetApp.flush();
  var headerReadback=sh.getRange(1,1,1,header.length).getDisplayValues();
  if(!wbFbsSupplyMatricesEqual_([header],[headerReadback[0]||[]]))
    throw new Error('WB_FBS_DIRECT: supply header readback mismatch');
  var rowsReadback=out.length?sh.getRange(2,1,out.length,header.length).getValues():[];
  if(!wbFbsSupplyMatricesEqual_(out,rowsReadback))
    throw new Error('WB_FBS_DIRECT: supply rows readback mismatch');
  var staleCount=Math.max(0,(oldLast-1)-out.length);
  if(staleCount){
    var staleIds=sh.getRange(out.length+2,1,staleCount,1).getDisplayValues();
    if(staleIds.some(function(row){return String((row||[])[0]||'').trim()!=='';}))
      throw new Error('WB_FBS_DIRECT: stale supply rows remain after refresh');
  }
  sh.setFrozenRows(1);
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
    sh.getRange(start, 46, n, 6).setValues(timings);
    sh.getRange(start, 46, n, 2).setNumberFormat('dd.MM.yyyy HH:mm:ss');
    sh.getRange(start, 48, n, 4).setNumberFormat('0.00');
  }
  if (sortedChanged) {
    sh.getRange(start, 58, n, 2).setValues(sortedCols);
    sh.getRange(start, 58, n, 1).setNumberFormat('dd.MM.yyyy HH:mm:ss');
    sh.getRange(start, 59, n, 1).setNumberFormat('0.00');
  }
  if(timingChanged||sortedChanged)SpreadsheetApp.flush();
  if(timingChanged&&!wbFbsSupplyMatricesEqual_(timings,sh.getRange(start,46,n,6).getValues()))
    throw new Error('WB_FBS_DIRECT: order timing readback mismatch');
  if(sortedChanged&&!wbFbsSupplyMatricesEqual_(sortedCols,sh.getRange(start,58,n,2).getValues()))
    throw new Error('WB_FBS_DIRECT: sorted history readback mismatch');

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
  sh.getRange(14, 1, labels.length, 2).setValues(labels);
  sh.getRange(15, 2).setNumberFormat('dd.MM.yyyy HH:mm:ss');
}

function wbFbsSupplySyncCore_(progress) {
  var cfg=WB_FBS_DIRECT_SUPPLY_CFG,ss=wbFbsSupplySpreadsheet_(),token=wbFbsSupplyToken_(),checkedAt=new Date();
  wbFbsSupplySetStage_('CORE_START', 'continuing=' + Boolean(progress));
  progress=progress||null;
  var continuing=!!(progress&&progress.client===cfg.client&&Number(progress.nextEndRow)>=2);
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
  SpreadsheetApp.flush();
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
    backfillRemainingRows:Math.max(0,backfill.startRow-2)
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

function wbFbsSupplySyncAllStores_() {
  // One cabinet per invocation keeps the official WB/API and Sheets work below the
  // Apps Script execution ceiling. GitHub Actions calls this function once per cabinet.
  var storeNames = ['SANYCH', 'AIR', 'HOZYAYUSHKA'];
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
    index = Number(scriptProps.getProperty(cursorKey) || 0);
    if (!isFinite(index) || index < 0 || index >= storeNames.length || Math.floor(index) !== index) index = 0;
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
