/**
 * Sellmonitor GitHub Central Worker v1.3.19
 * GitHub is source-of-truth/scheduler. This Apps Script project is only
 * the authorized Google adapter. Make is not part of the execution path.
 */
const SMC_GH = Object.freeze({
  VERSION: 'github-worker-1.3.19',
  CONTROL_CENTER_ID: '1sW51KKwQIvB7GZKyUhukqHXAL_CxJZKL-mjKWGbZLE0',
  CLIENTS_SHEET: 'Clients',
  LOG_SHEET: 'Log',
  MAX_CLIENTS_PER_TICK: 4,
  MAX_COMMANDS_PER_CLIENT: 1,
  MAX_RESULT_CHARS: 45000,
  WEBHOOK_SECRET: '__SELLMONITOR_GITHUB_WEBHOOK_SECRET__',
  WEBHOOK_SECRET_SHA256: '__SELLMONITOR_GITHUB_WEBHOOK_SECRET_SHA256__'
});


function sellmonitorGithubEnsureTrigger_() {
  var fn = 'sellmonitorGithubTick';
  var desiredVersion = 'worker-1m-v1';
  var props = PropertiesService.getScriptProperties();
  var triggers = ScriptApp.getProjectTriggers();
  var found = null;
  var mustRecreate = props.getProperty('SMC_GH_TRIGGER_VERSION') !== desiredVersion;
  for (var i = 0; i < triggers.length; i++) {
    if (triggers[i].getHandlerFunction() !== fn) continue;
    if (mustRecreate || found) ScriptApp.deleteTrigger(triggers[i]);
    else found = triggers[i];
  }
  if (mustRecreate || !found) {
    ScriptApp.newTrigger(fn).timeBased().everyMinutes(1).create();
    props.setProperty('SMC_GH_TRIGGER_VERSION', desiredVersion);
    return {ok: true, created: true, intervalMinutes: 1};
  }
  return {ok: true, created: false, intervalMinutes: 1};
}

function sellmonitorGithubBootstrap() {
  var trigger = sellmonitorGithubEnsureTrigger_();
  var platform = sellmonitorGithubPlatformReady();
  var tick = sellmonitorGithubTick();
  return {
    ok: Boolean(tick && tick.ok),
    version: SMC_GH.VERSION,
    trigger: trigger,
    platform: platform,
    tick: tick
  };
}

function sellmonitorGithubHealth() {
  var triggerState = sellmonitorGithubEnsureTrigger_();
  var triggerCount = ScriptApp.getProjectTriggers().filter(function(t) {
    return t.getHandlerFunction() === 'sellmonitorGithubTick';
  }).length;
  var cc = SpreadsheetApp.openById(SMC_GH.CONTROL_CENTER_ID);
  var clients = cc.getSheetByName(SMC_GH.CLIENTS_SHEET);
  if (!clients) throw new Error('CONTROL_CENTER Clients sheet missing');

  var summaries = [];
  if (clients.getLastRow() >= 2) {
    var width = Math.max(20, clients.getLastColumn());
    var head = clients.getRange(1, 1, 1, width).getDisplayValues()[0];
    var map = {};
    head.forEach(function(x, i) { if (x) map[String(x).trim()] = i; });
    var vals = clients.getRange(2, 1, clients.getLastRow() - 1, width).getDisplayValues();

    function field_(r, key) {
      return map[key] == null ? '' : String(r[map[key]] || '').trim();
    }

    vals.forEach(function(r) {
      var enabled = field_(r, 'enabled').toUpperCase();
      var spreadsheetId = field_(r, 'spreadsheet_id');
      var storeId = field_(r, 'store_id');
      var name = field_(r, 'client_name');
      var status = field_(r, 'status');
      if (['TRUE','1','ДА'].indexOf(enabled) < 0 || !spreadsheetId) return;
      if (storeId === 'new_store_template' || /MASTER TEMPLATE/i.test(name) || status === 'template_only') return;

      var item = {
        client_name: name,
        store_id: storeId,
        state: field_(r, 'state'),
        status: status,
        qc_status: field_(r, 'qc_status'),
        last_seen: field_(r, 'last_seen'),
        last_sync: field_(r, 'last_sync'),
        worker_version: field_(r, 'worker_version'),
        auth: {inner_access:false, refresh_token:false, legacy_alias:false},
        positions_rows: 0,
        stocks_rows: 0,
        queue: {PENDING:0, NEW:0, SCHEDULED:0, RUNNING:0, DONE:0, ERROR:0, CANCELLED_STALE_WORKER:0}
      };
      try {
        var cp = sellmonitorClientProperties_(spreadsheetId);
        item.auth = {
          inner_access: Boolean(cp.getProperty('SM_INNER_MCP_ACCESS_TOKEN')),
          refresh_token: Boolean(cp.getProperty('SM_INNER_MCP_REFRESH_TOKEN')),
          legacy_alias: Boolean(cp.getProperty('SM_MCP_ACCESS_TOKEN'))
        };
        var ss = SpreadsheetApp.openById(spreadsheetId);
        var pos = ss.getSheetByName('07_Контроль_позиций');
        var stocks = ss.getSheetByName('06_Остатки');
        var q = ss.getSheetByName('97_Управление');
        item.positions_rows = pos ? pos.getLastRow() : 0;
        item.stocks_rows = stocks ? stocks.getLastRow() : 0;
        if (q) {
          var to = Math.min(2023, q.getMaxRows());
          if (to >= 1200) {
            q.getRange(1200, 5, to - 1199, 1).getDisplayValues().forEach(function(x) {
              var st = String((x || [])[0] || '').trim();
              if (Object.prototype.hasOwnProperty.call(item.queue, st)) item.queue[st]++;
            });
          }
        }
      } catch (e) {
        item.diagnostic_error = String(e && e.message ? e.message : e).slice(0, 300);
      }
      summaries.push(item);
    });
  }

  return {
    ok: true,
    version: SMC_GH.VERSION,
    controlCenterId: cc.getId(),
    controlCenterName: cc.getName(),
    clientsRows: Math.max(0, clients.getLastRow() - 1),
    clients: summaries,
    runtime: 'GITHUB_ACTIONS',
    trigger: triggerState,
    trigger_count: triggerCount,
    trigger_ok: triggerCount === 1
  };
}

function sellmonitorGithubTick() {
  var lock = LockService.getScriptLock();
  if (!lock.tryLock(5000)) return {ok: true, skipped: true, reason: 'worker_lock_busy'};
  try {
    var cc = SpreadsheetApp.openById(SMC_GH.CONTROL_CENTER_ID);
    var sh = cc.getSheetByName(SMC_GH.CLIENTS_SHEET);
    if (!sh) throw new Error('CONTROL_CENTER Clients sheet missing');
    var lr = sh.getLastRow();
    if (lr < 2) return {ok: true, processedClients: 0, processedCommands: 0};

    var width = Math.max(20, sh.getLastColumn());
    var head = sh.getRange(1, 1, 1, width).getDisplayValues()[0];
    var map = {};
    head.forEach(function(x, i) { if (x) map[String(x).trim()] = i; });

    ['spreadsheet_id','enabled','client_name','store_id','state','status'].forEach(function(k) {
      if (map[k] == null) throw new Error('CONTROL_CENTER missing column: ' + k);
    });

    var rawRows = sh.getRange(2, 1, lr - 1, width).getValues();
    var clients = [];
    for (var i = 0; i < rawRows.length; i++) {
      var r = rawRows[i];
      var enabled = r[map.enabled] === true || String(r[map.enabled]).toUpperCase() === 'TRUE';
      var spreadsheetId = String(r[map.spreadsheet_id] || '').trim();
      var storeId = String(r[map.store_id] || '').trim();
      var name = String(r[map.client_name] || '').trim();
      var status = String(r[map.status] || '').trim();
      if (!enabled || !spreadsheetId) continue;
      if (storeId === 'new_store_template' || /MASTER TEMPLATE/i.test(name) || status === 'template_only') continue;
      clients.push({values:r,row:i + 2,spreadsheetId:spreadsheetId,storeId:storeId,name:name,status:status});
    }

    var processedClients = 0, processedCommands = 0, errors = [];
    var props = PropertiesService.getScriptProperties();
    var start = Number(props.getProperty('SMC_GH_CLIENT_CURSOR') || 0);
    if (!isFinite(start) || start < 0) start = 0;
    if (clients.length) {
      start = Math.floor(start) % clients.length;
      // Advance before running any remote command. If this execution hits the
      // Apps Script wall-clock limit, the next minute starts from another client.
      props.setProperty('SMC_GH_CLIENT_CURSOR', String((start + 1) % clients.length));
    }

    for (var step = 0; step < clients.length && processedClients < SMC_GH.MAX_CLIENTS_PER_TICK; step++) {
      var item = clients[(start + step) % clients.length];
      var r = item.values, row = item.row;
      try {
        var result = sellmonitorGithubProcessClient_(item.spreadsheetId);
        processedClients++;
        processedCommands += Number(result.processedCommands || 0);
        sellmonitorGithubSetControl_(sh, row, map, {
          last_seen: new Date(),
          worker_version: SMC_GH.VERSION,
          error: '',
          status: result.ready ? 'READY' : (result.processedCommands ? 'worker_ok' : 'idle'),
          state: result.ready ? 'READY' : 'ACTIVE',
          qc_status: result.ready ? 'PASS' : (map.qc_status != null ? r[map.qc_status] : '')
        });
      } catch (e) {
        processedClients++;
        errors.push({spreadsheetId: item.spreadsheetId, error: String(e.message || e)});
        sellmonitorGithubSetControl_(sh, row, map, {
          last_seen: new Date(),
          worker_version: SMC_GH.VERSION,
          status: 'ERROR',
          state: 'ERROR',
          error: String(e.message || e).slice(0, 1500)
        });
        sellmonitorGithubLog_(cc, item.spreadsheetId, 'GITHUB_WORKER', 'ERROR', String(e.message || e), {});
      }
    }

    SpreadsheetApp.flush();
    return {
      ok: errors.length === 0,
      version: SMC_GH.VERSION,
      startClient: clients.length ? clients[start].storeId : '',
      processedClients: processedClients,
      processedCommands: processedCommands,
      errors: errors
    };
  } finally {
    lock.releaseLock();
  }
}

function sellmonitorGithubQueueSlot_(ss, q) {
  var from = 1200, to = Math.min(2023, q.getMaxRows());
  if (to < from) return 0;
  var vals = q.getRange(from, 1, to - from + 1, 8).getValues();
  var reusable = null;

  function ts_(v) {
    if (v instanceof Date && !isNaN(v)) return v.getTime();
    var d = new Date(v || 0);
    return d instanceof Date && !isNaN(d) ? d.getTime() : 0;
  }

  for (var i = 0; i < vals.length; i++) {
    var id = String(vals[i][0] || '').trim();
    var st = String(vals[i][4] || '').trim();
    if (!id && !st) return from + i;
    if (!/^(DONE|ERROR|CANCELLED)/.test(st)) continue;
    var t = ts_(vals[i][6]) || ts_(vals[i][5]) || ts_(vals[i][1]);
    if (!reusable || t < reusable.t) reusable = {row:from+i, t:t, values:vals[i]};
  }
  if (!reusable) return 0;

  var archive = ss.getSheetByName('97_Архив_очереди');
  if (archive) {
    var ar = archive.getLastRow() + 1;
    if (archive.getMaxColumns() < 10) archive.insertColumnsAfter(archive.getMaxColumns(), 10 - archive.getMaxColumns());
    if (ar > archive.getMaxRows()) archive.insertRowsAfter(archive.getMaxRows(), Math.max(100, ar - archive.getMaxRows()));
    // Canonical archive layout: archived_at, source_row, then original queue A:H.
    // D-1 gates read id/status/result from C/G/J; raw A:H archival makes
    // completed source jobs invisible and causes false "PROD not registered".
    archive.getRange(ar, 1, 1, 10).setValues([[new Date(), reusable.row].concat(reusable.values)]);
  }
  q.getRange(reusable.row, 1, 1, 8).clearContent();
  SpreadsheetApp.flush();
  return reusable.row;
}

function sellmonitorGithubRefreshInnerCatalog_(ss, storeId) {
  var fin = ss.getSheetByName('84_Финансы_периоды');
  var raw = ss.getSheetByName('90_RAW_finance');
  var prod = ss.getSheetByName('04_Товары');
  if (!fin || !raw || !prod) throw new Error('Inner catalog: required sheets missing');

  var universe = {}, lr = fin.getLastRow(), chunk = 2500;
  for (var st = 2; st <= lr; st += chunk) {
    var n = Math.min(chunk, lr - st + 1);
    var a = fin.getRange(st,1,n,8).getValues();
    var trust = fin.getRange(st,39,n,1).getDisplayValues();
    for (var i=0; i<n; i++) {
      if (String(a[i][0] || '') !== String(storeId)) continue;
      if (String(trust[i][0] || '') !== 'FACTUAL_INNER_ANALYTICS') continue;
      var nm = String(a[i][6] || '').trim();
      if (!/^\d+$/.test(nm)) continue;
      var u = universe[nm] || (universe[nm] = {nm:nm, seller:''});
      if (a[i][7]) u.seller = String(a[i][7]);
    }
  }
  var ids = Object.keys(universe);
  if (!ids.length) throw new Error('Inner catalog: trusted finance universe empty ' + storeId);

  var need = {};
  ids.forEach(function(x){ need[x]=1; });
  var latest = {}, rr = raw.getLastRow(), scanned = 0;
  var maxScan = Math.min(Math.max(2000, ids.length * 8), 8000);
  while (rr > 1 && scanned < maxScan && Object.keys(latest).length < ids.length) {
    var count = Math.min(250, rr - 1, maxScan - scanned);
    var start = rr - count + 1;
    var vals = raw.getRange(start,1,count,11).getValues();
    for (var j=count-1; j>=0; j--) {
      var r = vals[j], nm = String(r[7] || '').trim();
      if (!need[nm] || latest[nm]) continue;
      if (String(r[0] || '') !== 'inner_product_list') continue;
      if (String(r[4] || '') !== 'FACTUAL_INNER_ANALYTICS') continue;
      if (String(r[5] || '').indexOf('|' + storeId + '|') < 0) continue;
      var obj = {};
      try { obj = JSON.parse(String(r[10] || '{}')); } catch (e) {}
      latest[nm] = {o:obj, sync:r[2], seller:String(r[8] || '')};
    }
    scanned += count;
    rr = start - 1;
  }

  var old = {};
  if (prod.getLastRow() > 1) {
    prod.getRange(2,2,prod.getLastRow()-1,26).getValues().forEach(function(r) {
      var nm = String(r[0] || '').trim();
      if (nm) old[nm] = {ops:r.slice(20,26)};
    });
  }

  var items = ids.map(function(nm) {
    var l = latest[nm] || {o:{},seller:''}, o = l.o || {}, u = universe[nm];
    var stock = o.stock && o.stock.count != null ? Number(o.stock.count) : '';
    var title = String(o.name || u.seller || ('SKU ' + nm));
    var seller = String(o.supplierArticle || l.seller || u.seller || '');
    var category = String(o.subjectName || o.categoryName || '');
    var status = latest[nm] ? (stock !== '' && stock <= 0 ? 'Нет остатка' : 'В продаже') : 'Архив';
    var buy = o.commonOrdersStat && o.commonOrdersStat.soldOrdersPercent != null
      ? Number(o.commonOrdersStat.soldOrdersPercent) / 100 : '';
    return {
      nm:nm,title:title,seller:seller,brand:String(o.brand || ''),category:category,
      rating:o.rating == null ? '' : Number(o.rating),
      reviews:o.reviewsCount == null ? '' : Number(o.reviewsCount),
      buy:buy,stock:stock,status:status,
      url:String(o.url || ('https://www.wildberries.ru/catalog/' + nm + '/detail.aspx')),
      sync:l.sync || '',
      ops:(old[nm] && old[nm].ops) || ['','','','','','']
    };
  });
  items.sort(function(a,b) {
    var aa = Number(a.stock), bb = Number(b.stock);
    if (!isFinite(aa)) aa = -1;
    if (!isFinite(bb)) bb = -1;
    return bb-aa || String(a.title).localeCompare(String(b.title));
  });

  var rows = items.map(function(x,i) {
    return [
      i+1,x.nm,x.title,x.brand,x.category,
      '','','','','','',
      x.rating,x.reviews,x.reviews,x.buy,x.stock,'',x.status,x.url,x.sync,x.seller
    ].concat(x.ops);
  });

  prod.getRange(2,1,Math.max(1,prod.getMaxRows()-1),27).clearContent();
  if (rows.length) prod.getRange(2,1,rows.length,27).setValues(rows);
  if (rows.length) prod.getRange(2,15,rows.length,1).setNumberFormat('0.0%');
  prod.setFrozenRows(1);
  SpreadsheetApp.flush();

  var stocks = sellmonitorGithubSyncStocksFromInner_(ss);
  return {
    ok:true, storeId:storeId, skuCount:rows.length,
    rawMetadataFound:Object.keys(latest).length,
    scannedRawRows:scanned,
    stocks:stocks,
    source:'Sellmonitor Inner normalized finance + RAW product metadata · lightweight core'
  };
}

function sellmonitorGithubSyncStocksFromInner_(ss) {
  var prod = ss.getSheetByName('04_Товары');
  var stocks = ss.getSheetByName('06_Остатки');
  if (!prod || !stocks) return {ok:false, reason:'04/06 sheets missing'};
  var lr = prod.getLastRow();
  var headers = [
    'Артикул WB',
    'Название товара',
    'Бренд',
    'Остаток, шт · SELLMONITOR INNER SNAPSHOT',
    'Остаток Sellmonitor · SNAPSHOT (raw)',
    'Статус карточки · SNAPSHOT',
    'Фулфилмент · SNAPSHOT',
    'Продажи rolling 30д · НЕТ CURRENT INNER ФАКТА',
    'Средние продажи/день · НЕТ CURRENT INNER ФАКТА',
    'Запас, дней · НЕТ CURRENT INNER ФАКТА',
    'Сигнал запаса · CURRENT SNAPSHOT'
  ];
  stocks.getRange(1,1,1,headers.length).setValues([headers]);
  stocks.getRange(2,1,Math.max(1,stocks.getMaxRows()-1),headers.length).clearContent();
  if (lr <= 1) {
    SpreadsheetApp.flush();
    return {ok:true, rows:0, source:'04_Товары · Sellmonitor Inner'};
  }
  var src = prod.getRange(2,1,lr-1,21).getValues();
  var rows = [];
  src.forEach(function(r) {
    var nm = String(r[1] || '').trim();
    if (!/^\d+$/.test(nm)) return;
    var stock = r[15];
    var status = String(r[17] || '').trim();
    var signal = '';
    if (stock !== '' && stock != null && isFinite(Number(stock))) {
      signal = Number(stock) <= 0 ? '🔴 НЕТ ОСТАТКА' : '🟢 ЕСТЬ ОСТАТОК · current snapshot';
    } else {
      signal = '⚪ НЕТ CURRENT STOCK ФАКТА';
    }
    rows.push([
      nm,
      r[2] || '',
      r[3] || '',
      stock,
      stock === '' || stock == null ? '' : String(stock),
      status,
      '',
      '',
      '',
      '',
      signal
    ]);
  });
  if (rows.length) {
    if (rows.length + 1 > stocks.getMaxRows()) stocks.insertRowsAfter(stocks.getMaxRows(), rows.length + 1 - stocks.getMaxRows());
    stocks.getRange(2,1,rows.length,headers.length).setValues(rows);
  }
  stocks.setFrozenRows(1);
  SpreadsheetApp.flush();
  return {ok:true, rows:rows.length, source:'04_Товары · Sellmonitor Inner current snapshot'};
}

function sellmonitorGithubMaintainSearchLane_(ss, q) {
  var from = 1780, to = Math.min(1840, q.getMaxRows());
  if (to < from) return {ok:false, reason:'search lane missing'};
  var vals = q.getRange(from,1,to-from+1,8).getValues();
  var blanks = 0, terminals = [];
  vals.forEach(function(r,i) {
    var id = String(r[0] || '').trim(), st = String(r[4] || '').trim();
    if (!id && !st) blanks++;
    else if (/^(DONE|ERROR|CANCELLED)/.test(st)) terminals.push({row:from+i, values:r});
  });
  if (blanks >= 5) return {ok:true, blanks:blanks, recycled:0};
  var archive = ss.getSheetByName('97_Архив_очереди');
  var recycled = 0;
  for (var i = 0; i < terminals.length && blanks < 10; i++) {
    if (archive) {
      var ar = archive.getLastRow()+1;
      if (archive.getMaxColumns() < 10) archive.insertColumnsAfter(archive.getMaxColumns(), 10 - archive.getMaxColumns());
      if (ar > archive.getMaxRows()) archive.insertRowsAfter(archive.getMaxRows(),100);
      archive.getRange(ar,1,1,10).setValues([[new Date(), terminals[i].row].concat(terminals[i].values)]);
    }
    q.getRange(terminals[i].row,1,1,8).clearContent();
    recycled++; blanks++;
  }
  if (recycled) SpreadsheetApp.flush();
  return {ok:true, blanks:blanks, recycled:recycled};
}

function sellmonitorGithubRefreshWbSearch_(ss, payload) {
  payload=payload||{};
  var auth=sellmonitorGithubResolveWbAuth_(ss), token=auth.token, tz=ss.getSpreadsheetTimeZone();
  var prod=ss.getSheetByName('04_Товары');
  var out=ss.getSheetByName('07_Поиск');
  if(!prod||!out)throw new Error('WB search: 04_Товары/07_Поиск missing');

  var catalog={}, ids=[];
  if(prod.getLastRow()>1){
    prod.getRange(2,1,prod.getLastRow()-1,21).getValues().forEach(function(r){
      var nm=String(r[1]||'').trim();
      if(!/^\\d+$/.test(nm))return;
      var status=String(r[17]||'').trim().toLowerCase();
      if(status==='архив')return;
      catalog[nm]={title:String(r[2]||''),brand:String(r[3]||''),sellerArticle:String(r[20]||'')};
      ids.push(Number(nm));
    });
  }
  if(!ids.length)throw new Error('WB search: no active numeric nmIds');

  var now=new Date(), endDate=new Date(now.getFullYear(),now.getMonth(),now.getDate()-1,12);
  var startDate=new Date(endDate); startDate.setDate(startDate.getDate()-6);
  var pastEnd=new Date(startDate); pastEnd.setDate(pastEnd.getDate()-1);
  var pastStart=new Date(pastEnd); pastStart.setDate(pastStart.getDate()-6);
  var from=String(payload.from||Utilities.formatDate(startDate,tz,'yyyy-MM-dd'));
  var to=String(payload.to||Utilities.formatDate(endDate,tz,'yyyy-MM-dd'));
  var pastFrom=String(payload.pastFrom||Utilities.formatDate(pastStart,tz,'yyyy-MM-dd'));
  var pastTo=String(payload.pastTo||Utilities.formatDate(pastEnd,tz,'yyyy-MM-dd'));
  var maxSku=Math.max(1,Math.min(ids.length,Number(payload.maxSku||ids.length)||ids.length));
  ids=ids.slice(0,maxSku);

  var rows=[], seen={}, calls=0;
  for(var off=0;off<ids.length;off+=50){
    var batch=ids.slice(off,off+50);
    var body={
      currentPeriod:{start:from,end:to},
      pastPeriod:{start:pastFrom,end:pastTo},
      nmIds:batch,
      topOrderBy:'openCard',
      includeSubstitutedSKUs:true,
      includeSearchTexts:true,
      orderBy:{field:'avgPosition',mode:'asc'},
      limit:30
    };
    var res=sellmonitorGithubWbJson_(
      'https://seller-analytics-api.wildberries.ru/api/v2/search-report/product/search-texts',
      'post',token,body
    );
    calls++;
    var items=res&&res.data&&Array.isArray(res.data.items)?res.data.items:[];
    items.forEach(function(x){
      var nm=String(x.nmId||'').trim(), q=String(x.text||'').trim();
      if(!/^\\d+$/.test(nm)||!q)return;
      var key=nm+'|'+q.toLowerCase();
      var pos=x.avgPosition&&x.avgPosition.current!=null?Number(x.avgPosition.current):'';
      var freq=x.frequency&&x.frequency.current!=null?Number(x.frequency.current):'';
      if(pos!==''&&!isFinite(pos))pos='';
      if(freq!==''&&!isFinite(freq))freq='';
      var old=seen[key];
      if(old&&Number(old[4]||0)>=Number(freq||0))return;
      var zone=pos===''?'Н/Д':pos<=3?'ТОП-3':pos<=10?'ТОП-10':pos<=30?'11–30':pos<=100?'31–100':'>100';
      var pri=pos!==''&&pos<=10?'УДЕРЖИВАТЬ':Number(freq||0)>=1000?'ВЫСОКИЙ · частотный запрос вне ТОП-10':Number(freq||0)>=300?'СРЕДНИЙ · есть потенциал роста':'НИЗКИЙ';
      var c=catalog[nm]||{};
      var r=[nm,String(x.name||c.title||''),q,pos,freq,'','',to,now,'','',zone,pri];
      seen[key]=r;
    });
  }
  Object.keys(seen).forEach(function(k){rows.push(seen[k]);});
  rows.sort(function(a,b){return Number(a[0])-Number(b[0])||Number(b[4]||0)-Number(a[4]||0)||Number(a[3]||9999)-Number(b[3]||9999);});

  var headers=['Артикул WB','Товар','Поисковый запрос','Позиция · SNAPSHOT','Частотность · SNAPSHOT','Конкуренция · SNAPSHOT','Товаров в выдаче · SNAPSHOT','Дата позиции · SNAPSHOT','Обновлено запроса · SNAPSHOT','Продажи товара rolling 30д · SNAPSHOT','Выручка товара rolling 30д, ₽ · SNAPSHOT','Зона позиции · расчёт из SNAPSHOT','Приоритет SEO · расчёт из SNAPSHOT'];
  out.getRange(1,1,1,headers.length).setValues([headers]);
  if(out.getMaxRows()>1)out.getRange(2,1,out.getMaxRows()-1,headers.length).clearContent();
  if(rows.length){
    if(rows.length+1>out.getMaxRows())out.insertRowsAfter(out.getMaxRows(),rows.length+1-out.getMaxRows());
    out.getRange(2,1,rows.length,headers.length).setValues(rows);
  }
  out.setFrozenRows(1);
  SpreadsheetApp.flush();
  sellmonitorClientProperties_(ss.getId()).setProperty('SMC_WB_SEARCH_SNAPSHOT_AT_MS',String(Date.now()));
  return {
    ok:true,storeId:auth.storeId,clientId:auth.clientId,from:from,to:to,pastFrom:pastFrom,pastTo:pastTo,
    skuCount:ids.length,apiCalls:calls,positionRows:rows.length,
    source:'WB Analytics /api/v2/search-report/product/search-texts',
    trustStatus:'FACTUAL_WB_ANALYTICS',secretsReturned:false
  };
}

function sellmonitorGithubEnsureSearchRefresh_(ss, q) {
  var cfg = ss.getSheetByName('99_Настройки');
  if (!cfg) return {ok:false, reason:'settings missing'};
  var vals = cfg.getRange(1,1,Math.max(1,cfg.getLastRow()),2).getDisplayValues();
  function setting_(key, def) {
    for (var i=0;i<vals.length;i++) if (String(vals[i][0] || '').trim()===key) return String(vals[i][1] || '').trim();
    return def || '';
  }
  var enabled=String(setting_('SEARCH_REFRESH_ENABLED','TRUE')).toUpperCase()!=='FALSE';
  var storeId=setting_('ACTIVE_STORE_ID','');
  if (!enabled || !storeId) return {ok:true, enabled:enabled, queued:false, storeId:storeId};

  var props=sellmonitorClientProperties_(ss.getId());
  var publicToken=String(props.getProperty('SM_MCP_ACCESS_TOKEN') || '');
  var merchantId=setting_('SELLMONITOR_MERCHANT_ID','');
  var legacy=Boolean(publicToken&&merchantId);
  var snapshotFile=legacy?'search_snapshot_config_v235':'__central_wb_search__';

  var hours=Math.max(1,Number(setting_('SEARCH_REFRESH_HOURS','6')) || 6);
  var lastMs=Number(props.getProperty('SMC_SEARCH_REFRESH_AT_MS') || 0);
  var now=Date.now();
  var due=!lastMs || (now-lastMs)>=hours*3600000;
  if (!due) return {ok:true,queued:false,storeId:storeId,lastRefreshAtMs:lastMs,hours:hours,source:snapshotFile};

  var from=1200,to=Math.min(2023,q.getMaxRows()),active=false;
  if (to>=from) {
    q.getRange(from,1,to-from+1,8).getValues().forEach(function(r) {
      var st=String(r[4] || ''), spec={};
      try { spec=JSON.parse(String(r[3] || '{}')); } catch(e) {}
      var file=String(spec.file || '');
      if ([snapshotFile,'search_position_monitor_sync_v238'].indexOf(file)<0) return;
      if (st==='PENDING'||st==='NEW'||st==='RUNNING'||st==='SCHEDULED') active=true;
    });
  }
  if (active) return {ok:true,queued:false,active:true,storeId:storeId,hours:hours,source:snapshotFile};

  if(!legacy){
    try{sellmonitorGithubResolveWbAuth_(ss);}
    catch(e){return {ok:false,queued:false,storeId:storeId,source:snapshotFile,reason:String(e&&e.message||e)};}
  }

  var slot=sellmonitorGithubQueueSlot_(ss,q);
  if (!slot) return {ok:false,reason:'no queue slot for search refresh',storeId:storeId};
  var stamp=Utilities.formatDate(new Date(),ss.getSpreadsheetTimeZone(),'yyyyMMdd-HHmmss');
  q.getRange(slot,1,1,8).setValues([[
    'AUTO-SEARCH-SNAPSHOT-'+storeId+'-'+stamp,
    new Date(),
    'RUN_REMOTE',
    JSON.stringify({file:snapshotFile,entrypoint:'REMOTE_MAIN',payload:{storeId:storeId}}),
    'PENDING','','',
    legacy
      ? 'Automatic factual search snapshot via Sellmonitor public MCP; monitor chains after success'
      : 'Automatic factual search snapshot via official WB Analytics; monitor chains after success'
  ]]);
  SpreadsheetApp.flush();
  return {ok:true,queued:true,row:slot,storeId:storeId,hours:hours,source:snapshotFile};
}

function sellmonitorGithubChainSearchMonitor_(ss,q,storeId) {
  var from=1200,to=Math.min(2023,q.getMaxRows());
  if (to>=from) {
    var rows=q.getRange(from,1,to-from+1,8).getValues();
    for (var i=0;i<rows.length;i++) {
      var st=String(rows[i][4] || ''), spec={};
      try { spec=JSON.parse(String(rows[i][3] || '{}')); } catch(e) {}
      if (String(spec.file || '')==='search_position_monitor_sync_v238' &&
          ['PENDING','NEW','RUNNING','SCHEDULED'].indexOf(st)>=0) {
        return {ok:true,queued:false,active:true,row:from+i};
      }
    }
  }
  var slot=sellmonitorGithubQueueSlot_(ss,q);
  if (!slot) return {ok:false,reason:'no queue slot for search monitor'};
  var stamp=Utilities.formatDate(new Date(),ss.getSpreadsheetTimeZone(),'yyyyMMdd-HHmmss');
  q.getRange(slot,1,1,8).setValues([[
    'AUTO-SEARCH-MONITOR-'+storeId+'-'+stamp,
    new Date(),
    'RUN_REMOTE',
    JSON.stringify({file:'search_position_monitor_sync_v238',entrypoint:'REMOTE_MAIN',payload:{storeId:storeId}}),
    'PENDING','','',
    'Chained after successful factual search snapshot'
  ]]);
  SpreadsheetApp.flush();
  return {ok:true,queued:true,row:slot};
}

function sellmonitorGithubEnsureCoreRefresh_(ss, q) {
  var set = ss.getSheetByName('99_Настройки');
  var stocks = ss.getSheetByName('06_Остатки');
  if (!set || !stocks) return {ok:false, reason:'core sheets missing'};

  function setting_(key) {
    var vals = set.getRange(1,1,Math.max(1,set.getLastRow()),2).getDisplayValues();
    for (var i=0; i<vals.length; i++) {
      if (String(vals[i][0] || '').trim() === key) return String(vals[i][1] || '').trim();
    }
    return '';
  }

  var storeId = setting_('ACTIVE_STORE_ID');
  if (!storeId) return {ok:false, reason:'ACTIVE_STORE_ID missing'};
  var merchantId = setting_('SELLMONITOR_MERCHANT_ID');
  var props = sellmonitorClientProperties_(ss.getId());
  var publicToken = String(props.getProperty('SM_MCP_ACCESS_TOKEN') || '');
  var innerToken = String(props.getProperty('SM_INNER_MCP_ACCESS_TOKEN') || '');
  var useLegacyPublic = Boolean(merchantId && publicToken);
  var coreFile = useLegacyPublic ? 'snapshot_products_safe_v129' : '__central_inner_catalog__';
  var idPrefix = useLegacyPublic ? 'AUTO-SNAPSHOT-' : 'AUTO-INNER-CATALOG-';

  var lastMs = Number(props.getProperty('SMC_CORE_REFRESH_AT_MS') || 0);
  var now = new Date();
  var emptyStocks = stocks.getLastRow() <= 1;
  var stale = !lastMs || (now.getTime() - lastMs) >= 60 * 60000;

  var from=1200,to=Math.min(2023,q.getMaxRows()),active=false;
  if (to >= from) {
    q.getRange(from,1,to-from+1,8).getValues().forEach(function(r) {
      var st=String(r[4] || ''), spec={};
      try { spec=JSON.parse(String(r[3] || '{}')); } catch(e) {}
      if (String(spec.file || '') !== coreFile) return;
      if (st==='PENDING'||st==='NEW'||st==='RUNNING'||st==='SCHEDULED') active=true;
    });
  }

  if ((!emptyStocks && !stale) || active) {
    return {
      ok:true, queued:false, active:active, emptyStocks:emptyStocks, stale:stale,
      lastRefreshAtMs:lastMs || null, source:coreFile
    };
  }
  if (!useLegacyPublic && !innerToken) {
    return {ok:false, reason:'Inner OAuth missing for inner-native core refresh', source:coreFile};
  }

  var slot=sellmonitorGithubQueueSlot_(ss,q);
  if (!slot) return {ok:false, reason:'worker-safe queue full with no terminal row to recycle'};
  q.getRange(slot,1,1,8).setValues([[
    idPrefix + storeId + '-' + Utilities.formatDate(now,ss.getSpreadsheetTimeZone(),'yyyyMMdd-HHmmss'),
    now,'RUN_REMOTE',
    JSON.stringify({file:coreFile,entrypoint:'REMOTE_MAIN',payload:{storeId:storeId}}),
    'PENDING','','',
    useLegacyPublic
      ? 'Hourly current product/stock snapshot via authorized public Sellmonitor MCP'
      : 'Hourly lightweight current catalog/stock refresh via authorized Sellmonitor Inner'
  ]]);
  SpreadsheetApp.flush();
  return {ok:true,queued:true,row:slot,emptyStocks:emptyStocks,stale:stale,source:coreFile};
}

/* ---------- WB ads cluster intelligence ---------- */

function sellmonitorGithubSetting_(ss, key, def) {
  var sh = ss.getSheetByName('99_Настройки');
  if (!sh) return def == null ? '' : def;
  var n = Math.max(1, sh.getLastRow());
  var rows = sh.getRange(1, 1, n, 2).getDisplayValues();
  for (var i = 0; i < rows.length; i++) {
    if (String(rows[i][0] || '').trim() === String(key)) return String(rows[i][1] || '').trim();
  }
  return def == null ? '' : def;
}

function sellmonitorGithubResolveWbAuth_(ss) {
  var cid = String(sellmonitorGithubSetting_(ss, 'FBS_CLIENT_ID', '') || '').trim();
  var storeId = String(sellmonitorGithubSetting_(ss, 'ACTIVE_STORE_ID', '') || '').trim();
  if (!cid) throw new Error('WB ads clusters: FBS_CLIENT_ID missing');
  var reg = ss.getSheetByName('97_FBS_Клиенты');
  if (!reg) throw new Error('WB ads clusters: 97_ФБС_Клиенты missing'.replace('ФБС','FBS'));
  var vals = reg.getRange(2, 1, Math.max(1, reg.getLastRow() - 1), 14).getDisplayValues();
  var prop = '';
  for (var i = 0; i < vals.length; i++) {
    if (String(vals[i][0] || '').trim() === cid) {
      prop = String(vals[i][11] || '').trim();
      break;
    }
  }
  var sp = sellmonitorClientProperties_(ss.getId());
  var token = String((prop && sp.getProperty(prop)) || sp.getProperty('FBS_CLIENT__' + cid + '__WB_API_TOKEN') || '');
  if (!token) throw new Error('WB ads clusters: WB token missing for ' + cid);
  return {clientId:cid, storeId:storeId, propertyName:prop, token:token};
}

function sellmonitorGithubWbJson_(url, method, token, payload) {
  var opt = {
    method: method || 'get',
    headers: {Authorization:token, Accept:'application/json'},
    muteHttpExceptions: true,
    followRedirects: true
  };
  if (payload != null) {
    opt.contentType = 'application/json';
    opt.payload = JSON.stringify(payload);
  }
  var r = null;
  for (var attempt = 0; attempt < 5; attempt++) {
    r = UrlFetchApp.fetch(url, opt);
    var code = r.getResponseCode();
    if (code === 429 && attempt < 4) {
      Utilities.sleep(Math.min(12000, 1200 * Math.pow(2, attempt)));
      continue;
    }
    var text = r.getContentText();
    if (code < 200 || code >= 300) {
      throw new Error('WB ads HTTP ' + code + ' ' + String(text || '').slice(0, 350));
    }
    if (!text) return {};
    try { return JSON.parse(text); }
    catch (e) { throw new Error('WB ads non-JSON response'); }
  }
  throw new Error('WB ads request exhausted retries');
}

function sellmonitorGithubEnsureSheet_(ss, name, headers) {
  var sh = ss.getSheetByName(name);
  if (!sh) sh = ss.insertSheet(name);
  if (sh.getMaxColumns() < headers.length) sh.insertColumnsAfter(sh.getMaxColumns(), headers.length - sh.getMaxColumns());
  var current = sh.getRange(1, 1, 1, headers.length).getDisplayValues()[0];
  var bad = current.length !== headers.length;
  for (var i = 0; i < headers.length && !bad; i++) if (String(current[i] || '') !== headers[i]) bad = true;
  if (bad) sh.getRange(1, 1, 1, headers.length).setValues([headers]);
  sh.setFrozenRows(1);
  return sh;
}

function sellmonitorGithubDateKey_(v, tz) {
  if (v instanceof Date && !isNaN(v)) return Utilities.formatDate(v, tz, 'yyyy-MM-dd');
  var s = String(v || '').trim();
  var m = s.match(/^(\d{4}-\d{2}-\d{2})/);
  if (m) return m[1];
  var d = new Date(s);
  return isNaN(d) ? '' : Utilities.formatDate(d, tz, 'yyyy-MM-dd');
}

function sellmonitorGithubNumber_(v) {
  if (typeof v === 'number') return isFinite(v) ? v : 0;
  var s = String(v == null ? '' : v).replace(/\s/g,'').replace('%','').replace(',','.');
  var n = Number(s);
  return isFinite(n) ? n : 0;
}

function sellmonitorGithubMedian_(a) {
  var v = (a || []).filter(function(x){ return isFinite(Number(x)); }).map(Number).sort(function(x,y){return x-y;});
  if (!v.length) return 0;
  var m = Math.floor(v.length / 2);
  return v.length % 2 ? v[m] : (v[m-1] + v[m]) / 2;
}

function sellmonitorGithubBaseContributionPerSale_(ss, nmId) {
  var sh = ss.getSheetByName('02_Недели');
  if (!sh || sh.getLastRow() < 3) return 0;
  var matches = sh.createTextFinder(String(nmId)).matchEntireCell(true).findAll();
  var rows = {};
  for (var i=0;i<matches.length;i++) {
    var cell=matches[i];
    if (cell.getColumn() !== 6) continue;
    var row=cell.getRow();
    var param=String(sh.getRange(row,9).getDisplayValue() || '');
    if (['fin_Profit','expAdCosts','fin_sales_total_qnt'].indexOf(param)>=0) rows[param]=row;
  }
  if (!rows.fin_Profit || !rows.expAdCosts || !rows.fin_sales_total_qnt) return 0;
  var lastCol=sh.getLastColumn(), start=14, width=Math.max(0,lastCol-start+1);
  if (!width) return 0;
  var headers=sh.getRange(2,start,1,width).getDisplayValues()[0];
  var profit=sh.getRange(rows.fin_Profit,start,1,width).getValues()[0];
  var ads=sh.getRange(rows.expAdCosts,start,1,width).getValues()[0];
  var sales=sh.getRange(rows.fin_sales_total_qnt,start,1,width).getValues()[0];
  var candidates=[];
  for (var c=0;c<width && candidates.length<8;c++) {
    if (!/^\d{2}\.\d{2}\.\d{4}$/.test(String(headers[c] || ''))) continue;
    var q=sellmonitorGithubNumber_(sales[c]);
    if (q<=0) continue;
    var x=(sellmonitorGithubNumber_(profit[c])+sellmonitorGithubNumber_(ads[c]))/q;
    if (isFinite(x) && x>0) candidates.push(x);
  }
  return sellmonitorGithubMedian_(candidates);
}


function sellmonitorGithubOrganicSearchMap_(ss, nmId) {
  var sh=ss.getSheetByName('07_Поиск'), out={};
  if(!sh || sh.getLastRow()<2)return out;
  var width=Math.min(sh.getLastColumn(),30), h=sh.getRange(1,1,1,width).getDisplayValues()[0], ix={};
  h.forEach(function(x,i){ix[String(x||'').trim()]=i;});
  var nmCol=ix['Артикул WB'], qCol=ix['Поисковый запрос'], pCol=ix['Позиция · SNAPSHOT'], fCol=ix['Частотность · SNAPSHOT'];
  if(nmCol==null || qCol==null)return out;
  var found=sh.createTextFinder(String(nmId)).matchEntireCell(true).findAll();
  found.forEach(function(cell){
    if(cell.getColumn()!==nmCol+1)return;
    var r=sh.getRange(cell.getRow(),1,1,width).getValues()[0], q=String(r[qCol]||'').trim();
    if(!q)return;
    out[q]={position:pCol==null?0:sellmonitorGithubNumber_(r[pCol]),frequency:fCol==null?0:sellmonitorGithubNumber_(r[fCol])};
  });
  return out;
}

function sellmonitorGithubCampaignPairs_(ss) {
  var sh=ss.getSheetByName('90_RAW_ads_campaigns');
  if (!sh || sh.getLastRow()<2) return [];
  var width=Math.min(sh.getLastColumn(),40);
  var h=sh.getRange(1,1,1,width).getDisplayValues()[0], ix={};
  h.forEach(function(x,i){ix[String(x || '').trim()]=i;});
  ['advert_id','nmId'].forEach(function(k){if(ix[k]==null)throw new Error('WB ads clusters: campaigns missing '+k);});
  var rows=sh.getRange(2,1,sh.getLastRow()-1,width).getValues(), out={}, list=[];
  rows.forEach(function(r){
    var ad=String(r[ix.advert_id] || '').trim(), nm=String(r[ix.nmId] || '').trim();
    if (!/^\d+$/.test(ad) || !/^\d+$/.test(nm)) return;
    var status=ix.status==null?'':String(r[ix.status] || '').trim();
    if (status && ['7','9','11'].indexOf(status)<0) return;
    var search=ix.search==null?true:(r[ix.search]===true || String(r[ix.search]).toUpperCase()==='TRUE');
    if (!search) return;
    var key=ad+'|'+nm;
    if(out[key])return;
    out[key]=true;
    list.push({
      advertId:Number(ad), nmId:Number(nm),
      sellerArticle:ix.seller_article==null?'':String(r[ix.seller_article] || ''),
      campaignName:ix.campaign_name==null?'':String(r[ix.campaign_name] || ''),
      paymentType:ix.payment_type==null?'':String(r[ix.payment_type] || ''),
      bidType:ix.bid_type==null?'':String(r[ix.bid_type] || ''),
      search:true,
      recommendations:ix.recommendations==null?false:(r[ix.recommendations]===true || String(r[ix.recommendations]).toUpperCase()==='TRUE'),
      bidSearchKopecks:ix.bid_search_kopecks==null?0:sellmonitorGithubNumber_(r[ix.bid_search_kopecks]),
      bidRecommendationsKopecks:ix.bid_recommendations_kopecks==null?0:sellmonitorGithubNumber_(r[ix.bid_recommendations_kopecks])
    });
  });
  return list;
}

function sellmonitorGithubParseClusterBidsByPair_(obj) {
  var out={};
  function walk(x) {
    if (!x) return;
    if (Array.isArray(x)) { x.forEach(walk); return; }
    if (typeof x !== 'object') return;
    var ad=Number(x.advertId || x.advert_id || 0), nm=Number(x.nmId || x.nm_id || 0);
    var q=String(x.normQuery || x.norm_query || x.query || '').trim();
    var bid=x.bidKopecks!=null?x.bidKopecks:(x.bid_kopecks!=null?x.bid_kopecks:(x.id_kopecks!=null?x.id_kopecks:x.bid));
    if (ad && nm && q && bid!=null && isFinite(Number(bid))) {
      var key=ad+'|'+nm;
      if(!out[key])out[key]={};
      out[key][q]=Number(bid);
    }
    Object.keys(x).forEach(function(k){ if (typeof x[k]==='object') walk(x[k]); });
  }
  walk(obj);
  return out;
}

function sellmonitorGithubParseClusterStatesByPair_(obj) {
  var out={};
  function put(key,arr,state) {
    if(!out[key])out[key]={};
    (arr || []).forEach(function(x){
      var q=typeof x==='string'?x:String((x||{}).normQuery || (x||{}).norm_query || (x||{}).query || '');
      if(q)out[key][q]=state;
    });
  }
  function walk(x) {
    if(!x || typeof x!=='object')return;
    var ad=Number(x.advertId || x.advert_id || 0), nm=Number(x.nmId || x.nm_id || 0);
    if(ad && nm && x.normQueries){
      var key=ad+'|'+nm;
      put(key,x.normQueries.active,'active');
      put(key,x.normQueries.excluded,'excluded');
    }
    Object.keys(x).forEach(function(k){if(typeof x[k]==='object')walk(x[k]);});
  }
  walk(obj);
  return out;
}

function sellmonitorGithubAnalyzeAdsClusters_(rows, baseContribution) {
  var g={};
  (rows || []).forEach(function(r){
    var q=String(r.normQuery || r.norm_query || '').trim();
    if(!q)return;
    var x=g[q] || (g[q]={normQuery:q,views:0,clicks:0,atbs:0,orders:0,shks:0,spend:0,bidKopecks:Number(r.clusterBidKopecks || r.cluster_bid_kopecks || 0),state:r.clusterState || r.cluster_state || ''});
    x.views+=Number(r.views||0);x.clicks+=Number(r.clicks||0);x.atbs+=Number(r.atbs||0);x.orders+=Number(r.orders||0);x.shks+=Number(r.shks||0);x.spend+=Number(r.spend||r.spendRub||r.spend_rub||0);
    if(Number(r.clusterBidKopecks||r.cluster_bid_kopecks||0)>0)x.bidKopecks=Number(r.clusterBidKopecks||r.cluster_bid_kopecks);
    if(r.clusterState||r.cluster_state)x.state=r.clusterState||r.cluster_state;
  });
  var base=Math.max(0,Number(baseContribution||0)), safeCpa=base>0?base*0.8:0, result=[];
  Object.keys(g).forEach(function(q){
    var x=g[q], ctr=x.views>0?x.clicks/x.views*100:0, cpc=x.clicks>0?x.spend/x.clicks:0;
    var cr=x.clicks>0?x.orders/x.clicks:0, cpa=x.orders>0?x.spend/x.orders:null;
    var safeCpc=safeCpa>0?safeCpa*cr:0, safeCpm=safeCpc>0?safeCpc*(ctr/100)*1000:0;
    var action='НАБЛЮДАТЬ', reason='Недостаточно данных для жёсткого решения';
    if(x.orders===0 && x.clicks>=20 && x.spend>=Math.max(250,base*0.5)){
      action='ИСКЛЮЧИТЬ / МИНУСОВАТЬ';reason='Достаточный объём кликов и расход без заказов';
    }else if(x.orders>0 && safeCpa>0 && cpa>safeCpa*1.15){
      action='СНИЗИТЬ СТАВКУ';reason='CPA выше безопасного рекламного бюджета на заказ';
    }else if(x.orders>=2 && safeCpa>0 && cpa<=safeCpa){
      action='ОСТАВИТЬ / МАСШТАБИРОВАТЬ';reason='CPA укладывается в базовую экономику товара';
    }else if(x.orders>0 && (!safeCpa || cpa<=base)){
      action='ОСТАВИТЬ';reason='Есть подтверждённые заказы; blanket-отключение не требуется';
    }
    var ratio=(cpc>0&&safeCpc>0)?Math.min(1,safeCpc/cpc):0;
    var targetBid=x.bidKopecks>0&&ratio>0?Math.max(1,Math.floor(x.bidKopecks*ratio)):0;
    result.push({
      normQuery:q,views:x.views,clicks:x.clicks,atbs:x.atbs,orders:x.orders,shks:x.shks,spend:x.spend,
      ctr:ctr,cpc:cpc,cr:cr,cpa:cpa,baseContribution:base,safeCpa:safeCpa,safeCpc:safeCpc,safeCpm:safeCpm,
      currentBidKopecks:x.bidKopecks,targetBidKopecks:targetBid,state:x.state,action:action,reason:reason
    });
  });
  result.sort(function(a,b){return b.spend-a.spend;});
  return result;
}

function sellmonitorGithubRefreshAdsClusters_(ss, payload) {
  payload=payload||{};
  var auth;
  try {
    auth=sellmonitorGithubResolveWbAuth_(ss);
  } catch(e) {
    if (/WB token missing for/.test(String(e && e.message || e))) {
      return {ok:true,skipped:true,reason:'WB_ANALYTICS_TOKEN_MISSING'};
    }
    throw e;
  }
  var token=auth.token, tz=ss.getSpreadsheetTimeZone();
  var pairs=sellmonitorGithubCampaignPairs_(ss);
  if(!pairs.length)return{ok:true,skipped:true,reason:'no active search campaign pairs'};
  var now=new Date(), fromDate=new Date(now);
  fromDate.setDate(fromDate.getDate()-Math.max(3,Math.min(31,Number(payload.days||14))));
  var from=String(payload.from || Utilities.formatDate(fromDate,tz,'yyyy-MM-dd'));
  var to=String(payload.to || Utilities.formatDate(now,tz,'yyyy-MM-dd'));
  var pairMap={};pairs.forEach(function(p){pairMap[p.advertId+'|'+p.nmId]=p;});
  var bidMaps={}, stateMaps={}, rawRows=[];
  for(var b=0;b<pairs.length;b+=100){
    var batch=pairs.slice(b,b+100);
    var snake=batch.map(function(p){return{advert_id:p.advertId,nm_id:p.nmId};});
    var camel=batch.map(function(p){return{advertId:p.advertId,nmId:p.nmId};});
    try{
      var bo=sellmonitorGithubWbJson_('https://advert-api.wildberries.ru/adv/v0/normquery/get-bids','post',token,{items:snake});
      var parsedBids=sellmonitorGithubParseClusterBidsByPair_(bo);
      Object.keys(parsedBids).forEach(function(k){bidMaps[k]=parsedBids[k];});
    }catch(e){}
    try{
      var lo=sellmonitorGithubWbJson_('https://advert-api.wildberries.ru/adv/v0/normquery/list','post',token,{items:camel});
      var parsedStates=sellmonitorGithubParseClusterStatesByPair_(lo);
      Object.keys(parsedStates).forEach(function(k){stateMaps[k]=parsedStates[k];});
    }catch(e){}
    var stats=sellmonitorGithubWbJson_('https://advert-api.wildberries.ru/adv/v1/normquery/stats','post',token,{from:from,to:to,items:camel});
    var items=stats.items || stats;
    if(!Array.isArray(items))items=[];
    items.forEach(function(item){
      var ad=Number(item.advertId||item.advert_id||0), daily=item.dailyStats||item.daily_stats||[];
      daily.forEach(function(day){
        var date=String(day.date||'').slice(0,10), st=day.stat||day.stats||day;
        var arr=Array.isArray(st)?st:[st];
        arr.forEach(function(x){
          if(!x)return;
          var nm=Number(x.nmId||x.nm_id||item.nmId||item.nm_id||0), key=ad+'|'+nm, p=pairMap[key]||{};
          var q=String(x.normQuery||x.norm_query||x.query||'').trim();if(!q)return;
          var bm=bidMaps[key]||{}, sm=stateMaps[key]||{};
          rawRows.push({
            storeId:auth.storeId,date:date,advertId:ad,nmId:nm,sellerArticle:p.sellerArticle||'',campaignName:p.campaignName||'',
            paymentType:p.paymentType||'',bidType:p.bidType||'',normQuery:q,
            views:Number(x.views||0),clicks:Number(x.clicks||0),atbs:Number(x.atbs||0),orders:Number(x.orders||0),shks:Number(x.shks||0),
            ctr:Number(x.ctr||0),cpc:Number(x.cpc||0),cpm:Number(x.cpm||0),avgPos:Number(x.avgPos||x.avg_pos||0),
            spend:Number(x.spend||0),clusterBidKopecks:Number(bm[q]||0),clusterState:String(sm[q]||''),
            campaignSearchBidKopecks:Number(p.bidSearchKopecks||0),campaignRecommendationsBidKopecks:Number(p.bidRecommendationsKopecks||0),
            campaignSearch:Boolean(p.search),campaignRecommendations:Boolean(p.recommendations)
          });
        });
      });
    });
    Utilities.sleep(6200);
  }

  var headers=['store_id','date','advert_id','nmId','seller_article','campaign_name','payment_type','bid_type','norm_query','views','clicks','atbs','orders','shks','ctr','cpc','cpm','avg_pos','spend_rub','cluster_bid_kopecks','cluster_state','campaign_search_bid_kopecks','campaign_recommendations_bid_kopecks','source','trust_status','loaded_at','period_from','period_to'];
  var sh=sellmonitorGithubEnsureSheet_(ss,'90_RAW_ads_clusters',headers), keep=[];
  if(sh.getLastRow()>1){
    var old=sh.getRange(2,1,sh.getLastRow()-1,headers.length).getValues();
    old.forEach(function(r){var d=sellmonitorGithubDateKey_(r[1],tz);if(String(r[0])!==auth.storeId || d<from || d>to)keep.push(r);});
  }
  var loaded=new Date();
  var newRows=rawRows.map(function(x){return[
    x.storeId,x.date,x.advertId,x.nmId,x.sellerArticle,x.campaignName,x.paymentType,x.bidType,x.normQuery,x.views,x.clicks,x.atbs,x.orders,x.shks,x.ctr,x.cpc,x.cpm,x.avgPos,x.spend,x.clusterBidKopecks,x.clusterState,x.campaignSearchBidKopecks,x.campaignRecommendationsBidKopecks,
    'WB Promotion /adv/v1/normquery/stats','FACTUAL_WB_ADS_CLUSTER',loaded,from,to
  ];});
  var all=keep.concat(newRows);
  if(sh.getMaxRows()>1)sh.getRange(2,1,sh.getMaxRows()-1,headers.length).clearContent();
  if(all.length){if(all.length+1>sh.getMaxRows())sh.insertRowsAfter(sh.getMaxRows(),all.length+1-sh.getMaxRows());sh.getRange(2,1,all.length,headers.length).setValues(all);}

  var diagHeaders=['store_id','period_from','period_to','advert_id','nmId','seller_article','campaign_name','norm_query','organic_position','query_frequency','spend_rub','views','clicks','atbs','orders','shks','ctr_pct','cpc_rub','cr_click_order_pct','cpa_rub','base_contribution_per_sale_rub','safe_cpa_rub','safe_cpc_rub','safe_cpm_rub','current_cluster_bid_kopecks','target_cluster_bid_kopecks','cluster_state','action','reason','evidence_status','refreshed_at'];
  var diag=sellmonitorGithubEnsureSheet_(ss,'84_Реклама_диагностика',diagHeaders), diagRows=[];
  pairs.forEach(function(p){
    var subset=rawRows.filter(function(x){return x.advertId===p.advertId&&x.nmId===p.nmId;});
    if(!subset.length)return;
    var base=sellmonitorGithubBaseContributionPerSale_(ss,p.nmId), a=sellmonitorGithubAnalyzeAdsClusters_(subset,base);
    var organic=sellmonitorGithubOrganicSearchMap_(ss,p.nmId);
    a.forEach(function(x){
      var org=organic[x.normQuery]||{}, action=x.action, reason=x.reason;
      if(Number(org.position||0)>0 && Number(org.position)<=10 && action==='ОСТАВИТЬ / МАСШТАБИРОВАТЬ'){
        action='ОСТАВИТЬ / СНИЗИТЬ ДЛЯ ТЕСТА';
        reason += '; органическая позиция уже ТОП-' + Math.round(Number(org.position));
      }
      diagRows.push([
        auth.storeId,from,to,p.advertId,p.nmId,p.sellerArticle,p.campaignName,x.normQuery,org.position||'',org.frequency||'',x.spend,x.views,x.clicks,x.atbs,x.orders,x.shks,x.ctr,x.cpc,x.cr*100,x.cpa==null?'':x.cpa,x.baseContribution,x.safeCpa,x.safeCpc,x.safeCpm,x.currentBidKopecks,x.targetBidKopecks,x.state,action,reason,'FACTUAL_WB_CLUSTER + FACTUAL_SEARCH_POSITION + CALCULATED_ECONOMICS',loaded
      ]);
    });
  });
  if(diag.getMaxRows()>1)diag.getRange(2,1,diag.getMaxRows()-1,diagHeaders.length).clearContent();
  if(diagRows.length){if(diagRows.length+1>diag.getMaxRows())diag.insertRowsAfter(diag.getMaxRows(),diagRows.length+1-diag.getMaxRows());diag.getRange(2,1,diagRows.length,diagHeaders.length).setValues(diagRows);}

  var traffic=ss.getSheetByName('90_RAW_ads_traffic'), totals={};
  if(traffic&&traffic.getLastRow()>1){
    var tw=Math.min(traffic.getLastColumn(),30), th=traffic.getRange(1,1,1,tw).getDisplayValues()[0], ti={};
    th.forEach(function(x,i){ti[String(x||'').trim()]=i;});
    var tv=traffic.getRange(2,1,traffic.getLastRow()-1,tw).getValues();
    tv.forEach(function(r){
      var d=sellmonitorGithubDateKey_(r[ti.date],tz),ad=Number(r[ti.advert_id]||0),nm=Number(r[ti.nmId]||0);
      if(d<from||d>to||!ad||!nm)return;var k=ad+'|'+nm,x=totals[k]||(totals[k]={spend:0,orders:0});
      x.spend+=sellmonitorGithubNumber_(r[ti.spend_rub]);x.orders+=sellmonitorGithubNumber_(r[ti.orders]);
    });
  }
  var zoneHeaders=['store_id','period_from','period_to','advert_id','nmId','seller_article','campaign_name','total_campaign_spend_rub','search_cluster_spend_rub','recommendations_spend_rub','recommendations_spend_status','total_campaign_orders','search_cluster_orders','search_enabled','recommendations_enabled','evidence_status','refreshed_at'];
  var zone=sellmonitorGithubEnsureSheet_(ss,'84_Реклама_зоны',zoneHeaders), zoneRows=[];
  pairs.forEach(function(p){
    var subset=rawRows.filter(function(x){return x.advertId===p.advertId&&x.nmId===p.nmId;}), searchSpend=0,searchOrders=0;
    subset.forEach(function(x){searchSpend+=Number(x.spend||0);searchOrders+=Number(x.orders||0);});
    var total=totals[p.advertId+'|'+p.nmId]||{spend:0,orders:0}, rec='', recStatus='Н/Д';
    if(p.search&&p.recommendations&&total.spend>=searchSpend){rec=Math.max(0,total.spend-searchSpend);recStatus='CALCULATED: total campaign spend - exact search cluster spend';}
    zoneRows.push([auth.storeId,from,to,p.advertId,p.nmId,p.sellerArticle,p.campaignName,total.spend,searchSpend,rec,recStatus,total.orders,searchOrders,p.search,p.recommendations,'SEARCH=FACTUAL_WB_CLUSTER; RECOMMENDATIONS=CALCULATED_WHEN_POSSIBLE',loaded]);
  });
  if(zone.getMaxRows()>1)zone.getRange(2,1,zone.getMaxRows()-1,zoneHeaders.length).clearContent();
  if(zoneRows.length){if(zoneRows.length+1>zone.getMaxRows())zone.insertRowsAfter(zone.getMaxRows(),zoneRows.length+1-zone.getMaxRows());zone.getRange(2,1,zoneRows.length,zoneHeaders.length).setValues(zoneRows);}
  SpreadsheetApp.flush();
  sellmonitorClientProperties_(ss.getId()).setProperty('SMC_ADS_CLUSTER_REFRESH_AT_MS',String(Date.now()));
  return{ok:true,storeId:auth.storeId,clientId:auth.clientId,from:from,to:to,pairs:pairs.length,rawRows:newRows.length,diagnosticRows:diagRows.length,zoneRows:zoneRows.length,secretsReturned:false};
}

function sellmonitorGithubEnsureAdsClusterRefresh_(ss, q) {
  var enabled=String(sellmonitorGithubSetting_(ss,'ADS_CLUSTER_REFRESH_ENABLED','TRUE')).toUpperCase()!=='FALSE';
  if(!enabled)return{ok:true,enabled:false,queued:false};
  if(!ss.getSheetByName('90_RAW_ads_campaigns'))return{ok:true,enabled:true,queued:false,reason:'90_RAW_ads_campaigns missing'};
  try {
    sellmonitorGithubResolveWbAuth_(ss);
  } catch(e) {
    if (/WB token missing for/.test(String(e && e.message || e))) {
      return {ok:true,enabled:true,queued:false,skipped:true,reason:'WB_ANALYTICS_TOKEN_MISSING'};
    }
    throw e;
  }
  var hours=Math.max(1,Number(sellmonitorGithubSetting_(ss,'ADS_CLUSTER_REFRESH_HOURS','6'))||6);
  var props=sellmonitorClientProperties_(ss.getId()),last=Number(props.getProperty('SMC_ADS_CLUSTER_REFRESH_AT_MS')||0),due=!last||(Date.now()-last)>=hours*3600000;
  var from=1200,to=Math.min(2023,q.getMaxRows()),active=false;
  if(to>=from)q.getRange(from,1,to-from+1,8).getValues().forEach(function(r){var st=String(r[4]||''),spec={};try{spec=JSON.parse(String(r[3]||'{}'));}catch(e){}if(['__central_ads_clusters__','wb_ads_cluster_intelligence_v1'].indexOf(String(spec.file||''))>=0&&['PENDING','NEW','RUNNING','SCHEDULED'].indexOf(st)>=0)active=true;});
  if(!due||active)return{ok:true,enabled:true,queued:false,active:active,lastRefreshAtMs:last||null,hours:hours};
  var slot=sellmonitorGithubQueueSlot_(ss,q);if(!slot)return{ok:false,reason:'no queue slot for ads cluster refresh'};
  var storeId=sellmonitorGithubSetting_(ss,'ACTIVE_STORE_ID',''),stamp=Utilities.formatDate(new Date(),ss.getSpreadsheetTimeZone(),'yyyyMMdd-HHmmss');
  q.getRange(slot,1,1,8).setValues([['AUTO-ADS-CLUSTERS-'+storeId+'-'+stamp,new Date(),'RUN_REMOTE',JSON.stringify({file:'wb_ads_cluster_intelligence_v1',entrypoint:'REMOTE_MAIN',payload:{storeId:storeId,days:14}}),'PENDING','','','Automatic WB search-cluster spend/bids/economics refresh']]);
  SpreadsheetApp.flush();
  return{ok:true,enabled:true,queued:true,row:slot,hours:hours};
}


function sellmonitorGithubProcessClient_(spreadsheetId) {
  var authState = sellmonitorGithubEnsureClientAuth_(spreadsheetId);
  var ss = SpreadsheetApp.openById(spreadsheetId);
  var q = ss.getSheetByName('97_Управление');
  var code = ss.getSheetByName('97_Код');
  if (!q || !code) throw new Error('Client missing 97_Управление/97_Код: ' + spreadsheetId);

  sellmonitorGithubEnsureUiOnboarding_(ss, q);
  var staleRepaired = sellmonitorGithubRepairStaleRunning_(q);
  var searchLane = sellmonitorGithubMaintainSearchLane_(ss, q);
  var coreRefresh = sellmonitorGithubEnsureCoreRefresh_(ss, q);
  var searchRefresh = sellmonitorGithubEnsureSearchRefresh_(ss, q);
  var adsClusterRefresh = sellmonitorGithubEnsureAdsClusterRefresh_(ss, q);
  var operational = sellmonitorGithubEnsureOperationalHistory_(ss, q);

  var processed = 0, last = null;
  while (processed < SMC_GH.MAX_COMMANDS_PER_CLIENT) {
    var row = sellmonitorGithubNextPendingRow_(q);
    if (!row) break;
    last = sellmonitorGithubExecuteQueueRow_(ss, q, code, row);
    processed++;
  }

  var ready = false;
  var connect = ss.getSheetByName('00_API_Подключение');
  if (connect) {
    var values = connect.getRange('A17:B25').getDisplayValues();
    var allOk = true;
    for (var i = 0; i < values.length; i++) {
      var label = String(values[i][0] || '');
      var value = String(values[i][1] || '').toUpperCase();
      if (!label) continue;
      if (/QC/.test(label)) {
        if (value !== 'READY' && value !== 'ГОТОВО' && value !== 'PASS') allOk = false;
      } else if (/^\d+\./.test(label)) {
        if (!/(ГОТОВО|READY|OK|ПОДКЛЮЧЕН|ACTIVE)/.test(value)) allOk = false;
      }
    }
    ready = allOk && values.length > 0 && sellmonitorGithubRecentExactReady_(ss, 7);
  }

  return {ok: true, spreadsheetId: spreadsheetId, auth: authState, processedCommands: processed, staleRepaired: staleRepaired, searchLane: searchLane, coreRefresh: coreRefresh, searchRefresh: searchRefresh, adsClusterRefresh: adsClusterRefresh, operational: operational, last: last, ready: ready};
}

function sellmonitorGithubRepairStaleRunning_(q) {
  var from = 1200, to = Math.min(2023, q.getMaxRows());
  if (to < from) return 0;
  var vals = q.getRange(from, 1, to - from + 1, 8).getValues();
  var now = new Date(), repaired = 0;

  function limitMinutes_(file) {
    file = String(file || '');
    if ([
      'mcp_inner_call_v183',
      'inner_harvest_to_raw_v217',
      'inner_product_harvest_fast_v311',
      'finance_period_normalize_incremental_v227',
      'rnp_store_aggregate_v231',
      'rnp_latest_period_inner_sync_v224',
      'calculator_full_sync_chunked_v211',
      'search_intelligence_sync_v240',
      'search_traffic_intelligence_v271',
      'search_intelligence_qc_v242',
      'traffic_daily_sync_v255'
    ].indexOf(file) >= 0) return 8;
    return 6;
  }

  for (var i = 0; i < vals.length; i++) {
    if (String(vals[i][4] || '') !== 'RUNNING') continue;
    var spec = {};
    try { spec = JSON.parse(String(vals[i][3] || '{}')); } catch (e) {}
    var file = String(spec.file || '');
    var started = vals[i][5] instanceof Date ? vals[i][5] :
      (vals[i][1] instanceof Date ? vals[i][1] : new Date(vals[i][5] || vals[i][1]));
    var age = started instanceof Date && !isNaN(started) ? (now.getTime() - started.getTime()) / 60000 : 999999;
    var lim = limitMinutes_(file);
    if (age < lim) continue;

    var row = from + i;
    q.getRange(row, 5).setValue('CANCELLED_STALE_WORKER');
    q.getRange(row, 7).setValue(now);
    q.getRange(row, 8).setValue('Central worker stale guard: ' + file + ' RUNNING ' + Math.round(age) + 'm >= ' + lim + 'm; task released for idempotent retry');
    repaired++;
  }
  if (repaired) SpreadsheetApp.flush();
  return repaired;
}

function sellmonitorGithubRecentExactReady_(ss, lookbackDays) {
  var sh = ss.getSheetByName('01_Дни');
  if (!sh) return false;
  var tz = ss.getSpreadsheetTimeZone();
  var lastCol = Math.max(14, sh.getLastColumn());
  var width = Math.min(lastCol - 13, 120);
  if (width <= 0) return false;

  var stat = sh.getRange(1, 14, 1, width).getDisplayValues()[0];
  var head = sh.getRange(2, 14, 1, width).getDisplayValues()[0];
  var byDay = {};
  for (var i = 0; i < head.length; i++) {
    var h = String(head[i] || '').trim();
    var m = h.match(/^(\d{2})\.(\d{2})\.(\d{4})$/);
    if (!m) continue; // ignore PREVIEW and non-day columns
    byDay[m[3] + '-' + m[2] + '-' + m[1]] = String(stat[i] || '').toUpperCase();
  }

  var n = Math.max(3, Math.min(14, Number(lookbackDays || 7)));
  var now = new Date();
  for (var d = 1; d <= n; d++) {
    var x = new Date(now.getFullYear(), now.getMonth(), now.getDate() - d, 12);
    var key = Utilities.formatDate(x, tz, 'yyyy-MM-dd');
    var st = byDay[key] || '';
    if (!/(🟢|ФАКТ|1\/1)/.test(st)) return false;
    if (/(🔴|НЕТ ФАКТА|Н\/Д|ПРЕДВ)/.test(st)) return false;
  }
  return true;
}


function sellmonitorGithubHydrateCurrentSnapshot_(ss, storeId) {
  var days = ss.getSheetByName('01_Дни');
  var prod = ss.getSheetByName('04_Товары');
  if (!days || !prod || days.getLastRow() < 3 || prod.getLastRow() < 2) {
    return {ok:true, skipped:true, reason:'days/products unavailable'};
  }

  var pm = {};
  var pv = prod.getRange(2,1,prod.getLastRow()-1,21).getValues();
  pv.forEach(function(r) {
    var nm = String(r[1] || '').trim();
    if (!nm) return;
    pm[nm] = {
      rating: r[11] === '' || r[11] == null ? '' : Number(r[11]),
      stock: r[15] === '' || r[15] == null ? '' : Number(r[15])
    };
  });

  var star = {};
  var rs = ss.getSheetByName('84_Рейтинг_срез');
  if (rs && rs.getLastRow() > 1) {
    rs.getRange(2,1,rs.getLastRow()-1,12).getValues().forEach(function(r) {
      var nm = String(r[0] || '').trim();
      if (!nm) return;
      star[nm] = {
        rating_rating:r[3], rating_5:r[4], rating_4:r[5], rating_3:r[6],
        rating_2:r[7], rating_1:r[8]
      };
    });
  }

  var n = days.getLastRow() - 2;
  var meta = days.getRange(3,1,n,9).getValues();
  var cur = days.getRange(3,13,n,1).getValues();
  var changed = 0, stockSum = 0, stockCount = 0;
  Object.keys(pm).forEach(function(nm) {
    var v = pm[nm].stock;
    if (v !== '' && isFinite(v)) { stockSum += Number(v); stockCount++; }
  });

  for (var i=0; i<n; i++) {
    var level = String(meta[i][0] || '');
    var nm = String(meta[i][5] || '').trim();
    var code = String(meta[i][8] || '').trim();
    var v = '';

    if (level === 'SKU' && nm && code === 'stocks_Store_4' && pm[nm] && pm[nm].stock !== '') {
      v = pm[nm].stock;
    } else if (level === 'МАГАЗИН' && code === 'stocks_Store_4' && stockCount) {
      v = stockSum;
    } else if (level === 'SKU' && nm && code === 'rating_rating' && pm[nm] && pm[nm].rating !== '') {
      v = pm[nm].rating;
    } else if (level === 'SKU' && nm && /^rating_[1-5]$/.test(code) && star[nm] && star[nm][code] !== '' && star[nm][code] != null) {
      v = star[nm][code];
    } else if (level === 'SKU' && nm && code === 'rating_rating' && star[nm] && star[nm].rating_rating !== '' && star[nm].rating_rating != null) {
      v = star[nm].rating_rating;
    } else {
      continue;
    }
    if (String(cur[i][0]) !== String(v)) {
      cur[i][0] = v;
      changed++;
    }
  }
  if (changed) {
    days.getRange(3,13,n,1).setValues(cur);
    SpreadsheetApp.flush();
  }
  return {ok:true, storeId:storeId, changed:changed, productCount:Object.keys(pm).length, stockSkuCount:stockCount};
}


function sellmonitorGithubSyncCoverageBoard_(ss, storeId) {
  var sh=ss.getSheetByName('00_Покрытие_фактов');
  var fin=ss.getSheetByName('84_Финансы_периоды');
  var set=ss.getSheetByName('99_Настройки');
  if (!sh || !fin || !set) return {ok:true,skipped:true,reason:'coverage inputs missing'};

  var tz=ss.getSpreadsheetTimeZone();
  var sv=set.getRange(1,1,Math.max(1,set.getLastRow()),2).getDisplayValues(), cfg={};
  sv.forEach(function(r){ if(r[0]) cfg[String(r[0]).trim()]=String(r[1]||'').trim(); });
  var storeName=cfg.STORE_NAME || storeId;
  function dk(v) {
    if (v instanceof Date) return Utilities.formatDate(v,tz,'yyyy-MM-dd');
    var s=String(v||'').trim();
    if (/^\d{4}-\d{2}-\d{2}/.test(s)) return s.slice(0,10);
    var m=s.match(/^(\d{1,2})\.(\d{1,2})\.(\d{4})/);
    return m ? m[3]+'-'+('0'+m[2]).slice(-2)+'-'+('0'+m[1]).slice(-2) : '';
  }
  function dateObj(s){ return new Date(s+'T12:00:00'); }

  var byDay={}, n=Math.max(0,fin.getLastRow()-1);
  if (n) {
    var core=fin.getRange(2,1,n,7).getValues();
    var trust=fin.getRange(2,39,n,1).getDisplayValues();
    for (var i=0;i<n;i++) {
      if (String(core[i][0]||'')!==storeId || String(trust[i][0]||'')!=='FACTUAL_INNER_ANALYTICS') continue;
      var f=dk(core[i][3]), t=dk(core[i][4]), days=Number(core[i][5]||0), nm=String(core[i][6]||'').trim();
      if (!f || !nm || days!==1) continue;
      var expected=new Date(dateObj(f)); expected.setDate(expected.getDate()+1);
      if (dk(expected)!==t) continue;
      (byDay[f]||(byDay[f]={}))[nm]=1;
    }
  }

  var today=new Date(), end=new Date(today.getFullYear(),today.getMonth(),today.getDate()-1,12);
  var start=new Date(end); start.setDate(start.getDate()-29);
  var days=[], maxSku=0, lastTrusted='', problem=[];
  for(var d=new Date(start); d<=end; d.setDate(d.getDate()+1)){
    var key=Utilities.formatDate(d,tz,'yyyy-MM-dd');
    var cnt=byDay[key]?Object.keys(byDay[key]).length:0;
    if(cnt>maxSku) maxSku=cnt;
    if(cnt>0) lastTrusted=key;
    days.push({key:key,count:cnt});
  }
  days.forEach(function(x){ if(!x.count) problem.push(x.key); });

  sh.getRange('B2').setValue(storeName);
  sh.getRange('E2').setValue(lastTrusted?dateObj(lastTrusted):'');
  sh.getRange('H2').setValue(problem.length);
  sh.getRange('A3').setValue('Окно контроля');
  sh.getRange('B3').setValue('Последние 30 дней');
  sh.getRange('D3').setValue('Макс. SKU-строк/день');
  sh.getRange('E3').setValue(maxSku);
  sh.getRange('G3').setValue('Проблемных дней');
  sh.getRange('H3').setValue(problem.length);
  sh.getRange('A4').setValue('Проблемные даты · 30 дней');
  sh.getRange('B4').setValue(problem.length?problem.slice(-12).map(function(x){return x.slice(8,10)+'.'+x.slice(5,7)+' 🔴 НЕТ ФАКТА';}).join('  |  '):'—');

  var hdr=[['Дата','Магазин','SKU-строк факта','Дневной max','Покрытие SKU','Статус факта','Strict fallback','Комментарий','Что делать','Источник']];
  sh.getRange(8,1,1,10).setValues(hdr);
  if (sh.getLastRow()>8) sh.getRange(9,1,sh.getLastRow()-8,10).clearContent();

  var out=[];
  days.slice().reverse().forEach(function(x){
    var pct=maxSku?x.count/maxSku:0;
    out.push([
      dateObj(x.key),storeName,x.count,maxSku,pct,
      x.count?'🟢 ФАКТ':'🔴 НЕТ ФАКТА','—',
      x.count?'Trusted daily Sellmonitor Inner':'Нет trusted daily FACTUAL_INNER_ANALYTICS',
      x.count?'—':'D-1/backfill должен восстановить день',
      'Sellmonitor Inner · FACTUAL_INNER_ANALYTICS'
    ]);
  });
  if(out.length) sh.getRange(9,1,out.length,10).setValues(out);
  sh.getRange(9,1,out.length,1).setNumberFormat('dd.mm.yyyy');
  sh.getRange(9,5,out.length,1).setNumberFormat('0.0%');
  SpreadsheetApp.flush();
  return {ok:true,storeId:storeId,lastTrusted:lastTrusted,maxSku:maxSku,problemDays:problem.length,windowDays:days.length};
}

function sellmonitorGithubEnsureOperationalHistory_(ss, q) {
  var set = ss.getSheetByName('99_Настройки');
  if (!set) return {ok:true, skipped:true, reason:'settings missing'};
  var vals = set.getRange(1,1,Math.max(1,set.getLastRow()),2).getDisplayValues();
  var cfg = {};
  vals.forEach(function(r){ if (r[0]) cfg[String(r[0]).trim()] = String(r[1] || '').trim(); });
  var storeId = cfg.ACTIVE_STORE_ID || '';
  if (!storeId) return {ok:true, skipped:true, reason:'ACTIVE_STORE_ID missing'};

  var props = sellmonitorClientProperties_(ss.getId());
  var now = Date.now();
  var coverage = {ok:true,skipped:true,reason:'fresh'};
  var coverageAt = Number(props.getProperty('SMC_COVERAGE_BOARD_AT_MS') || 0);
  if (!coverageAt || now-coverageAt >= 15*60000) {
    coverage = sellmonitorGithubSyncCoverageBoard_(ss, storeId);
    props.setProperty('SMC_COVERAGE_BOARD_AT_MS', String(now));
  }
  var lastHydrate = Number(props.getProperty('SMC_OPERATIONAL_HYDRATE_AT_MS') || 0);
  var hydrate = {ok:true, skipped:true, reason:'fresh'};
  if (!lastHydrate || now-lastHydrate >= 30*60000) {
    hydrate = sellmonitorGithubHydrateCurrentSnapshot_(ss, storeId);
    props.setProperty('SMC_OPERATIONAL_HYDRATE_AT_MS', String(now));
  }

  var from=1200, to=Math.min(2023,q.getMaxRows());
  var active = {};
  if (to >= from) {
    var qa=q.getRange(from,1,to-from+1,5).getValues();
    qa.forEach(function(r) {
      var st=String(r[4] || '');
      if (['PENDING','RUNNING','NEW','SCHEDULED'].indexOf(st)<0) return;
      try {
        var s=JSON.parse(String(r[3] || '{}'));
        if (s.file) active[String(s.file)] = true;
      } catch(e) {}
    });
  }

  function queue_(tag,file,payload) {
    if (active[file]) return 0;
    var row=sellmonitorGithubQueueSlot_(ss,q);
    if (!row) return 0;
    q.getRange(row,1,1,5).setValues([[
      'AUTO-'+tag+'-'+storeId+'-'+Utilities.formatDate(new Date(),ss.getSpreadsheetTimeZone(),'yyyyMMdd-HHmmss'),
      new Date(),'RUN_REMOTE',
      JSON.stringify({file:file,entrypoint:'REMOTE_MAIN',payload:payload || {storeId:storeId}}),
      'PENDING'
    ]]);
    active[file]=true;
    return row;
  }

  var queued = {};

  // Current operational facts bypass long full-sync/backfill lanes.
  var liveAt = Number(props.getProperty('SMC_LIVE_TODAY_AT_MS') || 0);
  if ((!liveAt || now-liveAt >= 15*60000) && !active.inner_live_today_sync_v260) {
    var lr=queue_('LIVE-TODAY','inner_live_today_sync_v260',{storeId:storeId});
    if (lr) { queued.liveToday=lr; props.setProperty('SMC_LIVE_TODAY_AT_MS',String(now)); }
  }

  var trafficAt = Number(props.getProperty('SMC_TRAFFIC_REFRESH_AT_MS') || 0);
  if ((!trafficAt || now-trafficAt >= 60*60000) && !active.traffic_refresh_enqueue_v256) {
    var tr=queue_('TRAFFIC-REFRESH','traffic_refresh_enqueue_v256',{storeId:storeId});
    if (tr) { queued.traffic=tr; props.setProperty('SMC_TRAFFIC_REFRESH_AT_MS',String(now)); }
  }

  var snapAt = Number(props.getProperty('SMC_SNAPSHOT_HISTORY_AT_MS') || 0);
  if ((!snapAt || now-snapAt >= 60*60000) && !active.rnp_snapshot_history_v304) {
    var sr=queue_('SNAPSHOT-HISTORY','rnp_snapshot_history_v304',{storeId:storeId});
    if (sr) { queued.snapshot=sr; props.setProperty('SMC_SNAPSHOT_HISTORY_AT_MS',String(now)); }
  }

  var enrAt = Number(props.getProperty('SMC_RNP_ENRICH_AT_MS') || 0);
  if ((!enrAt || now-enrAt >= 60*60000) && !active.rnp_enrichment_sync_v305) {
    var er=queue_('RNP-ENRICH','rnp_enrichment_sync_v305',{storeId:storeId,sheetIndex:0,offset:0,chunkRows:500});
    if (er) { queued.enrichment=er; props.setProperty('SMC_RNP_ENRICH_AT_MS',String(now)); }
  }

  SpreadsheetApp.flush();
  return {ok:true,storeId:storeId,coverage:coverage,hydrate:hydrate,queued:queued};
}

function sellmonitorGithubNextPendingRow_(q) {
  var from = 1200;
  var to = Math.min(2023, q.getMaxRows());
  if (to < from) return 0;
  var vals = q.getRange(from, 1, to - from + 1, 8).getValues();
  var now = new Date().getTime(), best = null;

  function priority_(id, file, spec) {
    id = String(id || '');
    file = String(file || '');
    spec = spec || {};

    // Emergency/current facts: never wait behind historical RNP, ads or SEO.
    if (/^FORCE-SEARCH-/.test(id)) return -2;
    if (
      file === 'snapshot_products_safe_v129'
      || file === '__central_inner_catalog__'
      || file === 'inner_sku_rnp_layout_v300'
      || /^AUTO-SNAPSHOT-/.test(id)
      || /^AUTO-INNER-CATALOG-/.test(id)
    ) return -1;

    // P0: dependencies that actually create yesterday's factual finance.
    // A D-1 gate must never outrank its own source/parser/normalizer.
    if (file === 'daily_prevday_close_v268') return 0;
    if (/^D1-/.test(id) && file === 'mcp_inner_call_v183') return 0;
    if ([
      'inner_product_harvest_fast_v311',
      'inner_harvest_to_raw_v217',
      'finance_period_normalize_incremental_v227',
      'traffic_daily_sync_v255',
      'wb_ads_bulk_ingest_v167'
    ].indexOf(file) >= 0) return 0;

    // P1: D-1 coordination + current operational facts + critical post steps.
    // Gate retries are intentionally below their dependencies so PROD/PARSE/NORM
    // and RNP post jobs can finish instead of being starved by a 30s retry loop.
    if ([
      'daily_prevday_gate_v269',
      'daily_prevday_gate_v312',
      'd1_gap_watchdog_v310',
      'inner_prevday_preview_v270',
      'inner_live_today_sync_v260',
      'rnp_finance_columns_fast_v307',
      'rnp_latest_period_inner_sync_v224',
      'rnp_period_headers_sync_v198',
      'rnp_store_aggregate_v231',
      'finance_daily_coverage_guard_v226',
      'coverage_freshness_sync_v161',
      'connection_status_sync_v192',
      'rnp_snapshot_history_v304',
      'rnp_enrichment_sync_v305'
    ].indexOf(file) >= 0) return 1;
    if (/^orders_/.test(file) || file === 'k2_inventory_pool_sync_v246') return 1;
    if ([
      '__central_wb_search__',
      'search_snapshot_config_v235',
      'search_position_monitor_sync_v238',
      'search_intelligence_sync_v240',
      'search_traffic_intelligence_v271',
      'search_intelligence_qc_v242'
    ].indexOf(file) >= 0) return 1;

    // P2: ads / traffic and the autopilot that schedules live refreshes.
    if (file === 'store_autopilot_v207') return 2;
    // P2: ads / traffic are operational facts and must not sit behind backfills.
    if (file === 'wb_ads_cluster_intelligence_v1' || /^(ads_|calculator_ads_|quality_ads_|traffic_|wb_ads_)/.test(file)) return 2;

    // P4: orchestration/backfill that can create more work.
    if ([
      'store_autopilot_v207',
      'queue_scheduler_tick_v263',
      'store_full_sync_gate_v248',
      'inner_backfill_gate_v250',
      'inner_backfill_enqueue_v185'
    ].indexOf(file) >= 0) return 4;

    // P6: competitor/cosmetic/derived work stays last.
    if (/^(search_competitor_|snapshot_)/.test(file) || /^SEARCH-COMP/.test(id)) return 6;
    if (/^search_/.test(file) || /^SEARCH-/.test(id)) return 5;

    return 4;
  }

  for (var i = 0; i < vals.length; i++) {
    var cmd = String(vals[i][2] || '');
    var st = String(vals[i][4] || '');
    if (cmd !== 'RUN_REMOTE') continue;
    if (st !== 'PENDING' && st !== 'NEW' && st !== 'SCHEDULED') continue;

    var due = vals[i][1] instanceof Date ? vals[i][1].getTime() : new Date(vals[i][1]).getTime();
    if (st === 'SCHEDULED' && isFinite(due) && due > now) continue;

    var spec = {};
    try { spec = JSON.parse(String(vals[i][3] || '{}')); } catch (e) {}
    var id = String(vals[i][0] || '');
    var file = String(spec.file || '');
    var p = priority_(id, file, spec);
    var ageKey = isFinite(due) ? due : 0;

    if (!best || p < best.priority || (p === best.priority && ageKey < best.ageKey) ||
        (p === best.priority && ageKey === best.ageKey && i < best.i)) {
      best = {row: from + i, priority: p, ageKey: ageKey, i: i};
    }
  }
  return best ? best.row : 0;
}

function sellmonitorGithubEnsureUiOnboarding_(ss, q) {
  var ui = ss.getSheetByName('00_API_Подключение');
  var set = ss.getSheetByName('99_Настройки');
  if (!ui || !set || ui.getRange('A15').getValue() !== true) return {ok:true, armed:false};

  function setting(k) {
    var v = set.getRange(1, 1, Math.max(1, set.getLastRow()), 2).getDisplayValues();
    for (var i = 0; i < v.length; i++) if (String(v[i][0]) === k) return String(v[i][1] || '').trim();
    return '';
  }

  var storeId = setting('ACTIVE_STORE_ID');
  var storeName = setting('STORE_NAME') || String(ui.getRange('B5').getDisplayValue() || '').trim();
  var profile = setting('FBS_CLIENT_ID');
  if (!storeId || !storeName || !profile) return {ok:false, armed:true, reason:'store/profile settings missing'};

  var from = 1200, to = Math.min(2023, q.getMaxRows());
  var vals = q.getRange(from, 1, to - from + 1, 5).getValues();
  var prefix = 'ONBOARD-' + storeId + '-';
  for (var i = 0; i < vals.length; i++) {
    var id = String(vals[i][0] || '');
    var st = String(vals[i][4] || '');
    if (id.indexOf(prefix) === 0 && (st === 'PENDING' || st === 'RUNNING' || st === 'NEW' || st === 'SCHEDULED')) {
      return {ok:true, armed:true, deduped:true, row:from+i};
    }
  }

  var slot = sellmonitorGithubQueueSlot_(ss, q);
  if (!slot) return {ok:false, armed:true, reason:'worker-safe queue full with no terminal row to recycle'};

  q.getRange(slot, 1, 1, 5).setValues([[
    prefix + Date.now(),
    new Date(),
    'RUN_REMOTE',
    JSON.stringify({file:'client_onboard_stage1_v1',entrypoint:'REMOTE_MAIN',payload:{storeId:storeId,storeName:storeName,profile:profile}}),
    'PENDING'
  ]]);
  SpreadsheetApp.flush();
  return {ok:true, armed:true, queued:true, row:slot};
}

function sellmonitorGithubExecuteQueueRow_(ss, q, codeSheet, row) {
  var data = q.getRange(row, 1, 1, 8).getValues()[0];
  var id = String(data[0] || ('GH-' + row));
  var cmd = String(data[2] || '');
  var specText = String(data[3] || '{}');
  if (cmd !== 'RUN_REMOTE') throw new Error('Unsupported queue command: ' + cmd);

  q.getRange(row, 5, 1, 2).setValues([['RUNNING', new Date()]]);
  SpreadsheetApp.flush();

  try {
    var spec = JSON.parse(specText || '{}');
    var file = String(spec.file || '').trim();
    var entrypoint = String(spec.entrypoint || 'REMOTE_MAIN').trim();
    if (!file) throw new Error('RUN_REMOTE file missing');
    if (file === '__central_ads_clusters__') {
      var adsClusterResult = sellmonitorGithubRefreshAdsClusters_(ss, spec.payload || {});
      q.getRange(row,5,1,4).setValues([['DONE', data[5] || new Date(), new Date(), sellmonitorGithubJson_(adsClusterResult)]]);
      sellmonitorGithubLog_(SpreadsheetApp.openById(SMC_GH.CONTROL_CENTER_ID), ss.getId(), file, 'DONE', id, adsClusterResult);
      return {id:id,file:file,ok:true,result:adsClusterResult};
    }
    if (file === '__central_wb_search__') {
      var wbSearchResult = sellmonitorGithubRefreshWbSearch_(ss, spec.payload || {});
      wbSearchResult.chainedMonitor = sellmonitorGithubChainSearchMonitor_(ss, q, String((spec.payload || {}).storeId || ''));
      q.getRange(row,5,1,4).setValues([['DONE', data[5] || new Date(), new Date(), sellmonitorGithubJson_(wbSearchResult)]]);
      sellmonitorGithubLog_(SpreadsheetApp.openById(SMC_GH.CONTROL_CENTER_ID), ss.getId(), file, 'DONE', id, wbSearchResult);
      return {id:id,file:file,ok:true,result:wbSearchResult};
    }
    if (file === '__central_inner_catalog__') {
      var centralResult = sellmonitorGithubRefreshInnerCatalog_(ss, String((spec.payload || {}).storeId || ''));
      sellmonitorClientProperties_(ss.getId()).setProperty('SMC_CORE_REFRESH_AT_MS', String(Date.now()));
      q.getRange(row,5,1,4).setValues([['DONE', data[5] || new Date(), new Date(), sellmonitorGithubJson_(centralResult)]]);
      sellmonitorGithubLog_(SpreadsheetApp.openById(SMC_GH.CONTROL_CENTER_ID), ss.getId(), file, 'DONE', id, centralResult);
      return {id:id,file:file,ok:true,result:centralResult};
    }
    if (file === 'k2_inventory_pool_sync_v246') {
      var cfg = ss.getSheetByName('99_Настройки');
      var hasK2 = false;
      if (cfg) {
        var cv = cfg.getRange(1,1,Math.max(1,cfg.getLastRow()),2).getDisplayValues();
        for (var ci=0; ci<cv.length; ci++) {
          if (String(cv[ci][0] || '').trim() === 'K2_SOURCE_SPREADSHEET_ID' && String(cv[ci][1] || '').trim()) {
            hasK2 = true; break;
          }
        }
      }
      if (!hasK2) {
        var skipped = {ok:true, skipped:true, reason:'K2_SOURCE_SPREADSHEET_ID not configured for this cabinet'};
        q.getRange(row,5,1,4).setValues([['DONE', data[5] || new Date(), new Date(), sellmonitorGithubJson_(skipped)]]);
        sellmonitorGithubLog_(SpreadsheetApp.openById(SMC_GH.CONTROL_CENTER_ID), ss.getId(), file, 'DONE', id, skipped);
        return {id:id, file:file, ok:true, result:skipped};
      }
    }
    if (!/^[A-Za-z_$][A-Za-z0-9_$]*$/.test(entrypoint)) throw new Error('Invalid entrypoint: ' + entrypoint);

    var source = sellmonitorGithubLoadSource_(codeSheet, file);
    source = sellmonitorGithubRebindSource_(source, ss.getId());
    var payload = spec.payload || {};
    var __SM_PAYLOAD__ = payload;
    var wrapped = '(function(__payload){\n' + source + '\n;return ' + entrypoint + '(__payload);\n})(__SM_PAYLOAD__)';
    var result = eval(wrapped);
    if (file === 'inner_sku_rnp_layout_v300' && result && result.ok === true) {
      result.innerStocks = sellmonitorGithubSyncStocksFromInner_(ss);
    }
    if (file === 'snapshot_products_safe_v129' && result && result.ok === true) {
      sellmonitorClientProperties_(ss.getId()).setProperty('SMC_CORE_REFRESH_AT_MS', String(Date.now()));
    }
    if (file === 'search_snapshot_config_v235' && result && result.ok === true) {
      var searchStoreId=String((spec.payload || {}).storeId || '');
      result.chainedMonitor=sellmonitorGithubChainSearchMonitor_(ss,q,searchStoreId);
    }
    if (file === 'search_position_monitor_sync_v238' && result && result.ok === true) {
      sellmonitorClientProperties_(ss.getId()).setProperty('SMC_SEARCH_REFRESH_AT_MS', String(Date.now()));
    }
    var resultText = sellmonitorGithubJson_(result);

    q.getRange(row, 5, 1, 4).setValues([['DONE', data[5] || new Date(), new Date(), resultText]]);
    sellmonitorGithubLog_(
      SpreadsheetApp.openById(SMC_GH.CONTROL_CENTER_ID),
      ss.getId(),
      file,
      'DONE',
      id,
      result
    );
    return {id: id, file: file, ok: true, result: result};
  } catch (e) {
    var msg = String(e && (e.stack || e.message) || e).slice(0, SMC_GH.MAX_RESULT_CHARS);
    q.getRange(row, 5, 1, 4).setValues([['ERROR', data[5] || new Date(), new Date(), msg]]);
    throw e;
  }
}

function sellmonitorGithubLoadSource_(codeSheet, file) {
  var canonical = {
    mcp_inner_call_v183: 1,
    store_autopilot_v207: 1,
    search_monitor_refresh_gate_v214: 1,
    daily_prevday_close_v268: 1,
    daily_prevday_gate_v269: 1,
    daily_prevday_gate_v312: 1,
    inner_product_harvest_fast_v311: 1,
    inner_sku_rnp_layout_v300: 1,
    rnp_snapshot_history_v304: 1,
    rnp_enrichment_sync_v305: 1,
    rnp_finance_columns_fast_v307: 1,
    traffic_refresh_enqueue_v256: 1,
    traffic_refresh_gate_v257: 1,
    traffic_daily_sync_v255: 1,
    wb_ads_bulk_ingest_v167: 1,
    inner_live_today_sync_v260: 1,
    orders_live_fast_v261: 1,
    d1_gap_watchdog_v310: 1
  };

  function collect_(sheet) {
    if (!sheet) return [];
    var lr = sheet.getLastRow();
    if (lr < 2) return [];
    var vals = sheet.getRange(2, 1, lr - 1, 6).getValues();
    var parts = [];
    vals.forEach(function(r, i) {
      var name = String(r[0] || '').trim();
      var enabled = r[4] === true || String(r[4]).toUpperCase() === 'TRUE';
      if (name !== file || !enabled) return;
      var part = Number(r[2]);
      if (!isFinite(part) || part <= 0) part = i + 1;
      parts.push({part: part, code: String(r[3] || '')});
    });
    parts.sort(function(a, b) { return a.part - b.part; });
    return parts;
  }

  var parts = [];
  if (canonical[file]) {
    var master = SpreadsheetApp.openById('1z5mewokRdEDfzXacyevXvCpHCuO_pbQs6nDJVhuextQ');
    parts = collect_(master.getSheetByName('97_Код'));
    if (!parts.length) throw new Error('Canonical production code missing: ' + file);
  } else {
    parts = collect_(codeSheet);
  }

  if (!parts.length) throw new Error('Active code not found: ' + file);
  return parts.map(function(x) { return x.code; }).join('\n');
}

function sellmonitorGithubRebindSource_(source, spreadsheetId) {
  var id = JSON.stringify(String(spreadsheetId));
  return String(source)
    .replace(/SpreadsheetApp\.getActiveSpreadsheet\(\)/g, 'SpreadsheetApp.openById(' + id + ')')
    .replace(/SpreadsheetApp\.getActive\(\)/g, 'SpreadsheetApp.openById(' + id + ')')
    .replace(/PropertiesService\.getScriptProperties\(\)/g, 'sellmonitorClientProperties_(' + id + ')')
    .replace(/LockService\.getDocumentLock\(\)/g, 'LockService.getScriptLock()');
}

function sellmonitorClientProperties_(spreadsheetId) {
  var primary = PropertiesService.getUserProperties();
  var legacy = PropertiesService.getScriptProperties();
  var prefix = 'SMC__' + String(spreadsheetId) + '__';
  function key(k) { return prefix + String(k); }
  function migrateOne(k) {
    var kk = key(k), v = primary.getProperty(kk);
    if (v != null) return v;
    v = legacy.getProperty(kk);
    if (v != null) {
      primary.setProperty(kk, String(v));
      return v;
    }
    // Sanych predates per-client namespacing. Its public Sellmonitor MCP token
    // is a real legacy credential and may be migrated once into the client scope.
    // Inner OAuth tokens are intentionally NOT treated as public-MCP tokens.
    if (
      String(spreadsheetId) === '1-aBDZ7c5xfmVwwiNmUi9-DyfIANXmfiM5-Ti2_zg4zI'
      && String(k) === 'SM_MCP_ACCESS_TOKEN'
    ) {
      v = legacy.getProperty('SM_MCP_ACCESS_TOKEN');
      if (v != null) {
        primary.setProperty(kk, String(v));
        return v;
      }
    }
    // Migrate only explicit client-scoped legacy keys; preserve old values.
    var legacyKeysByClient = {};
    legacyKeysByClient['1SmsoG8zKx3hbTtTzS-zLekTFiWEQN8eIwHOxXq-5RHo'] = [
      'FBS_CLIENT__AIR__WB_API_TOKEN'
    ];
    legacyKeysByClient['1cVT_H_e8a519k_Gtph6fALWnBbtBrAQ2Jb_gFO3bM64'] = [
      'FBS_CLIENT__FBS_14I5XGBA9NIG__WB_API_TOKEN'
    ];
    legacyKeysByClient['1-aBDZ7c5xfmVwwiNmUi9-DyfIANXmfiM5-Ti2_zg4zI'] = [
      'FBS_CLIENT__SANYCH__WB_API_TOKEN',
      'SM_INNER_MCP_ACCESS_TOKEN',
      'SM_INNER_MCP_REFRESH_TOKEN',
      'SM_INNER_MCP_EXPIRES_AT',
      'SM_INNER_MCP_CLIENT_ID',
      'SM_INNER_MCP_TOKEN_ENDPOINT'
    ];
    var allowedLegacyKeys = legacyKeysByClient[String(spreadsheetId)] || [];
    if (allowedLegacyKeys.indexOf(String(k)) >= 0) {
      v = legacy.getProperty(String(k));
      if (v != null) {
        primary.setProperty(kk, String(v));
        return v;
      }
    }
    return null;
  }
  return {
    getProperty: function(k) { return migrateOne(k); },
    setProperty: function(k, v) {
      primary.setProperty(key(k), String(v));
      return this;
    },
    deleteProperty: function(k) {
      primary.deleteProperty(key(k));
      legacy.deleteProperty(key(k));
      return this;
    },
    getProperties: function() {
      var out = {}, a = legacy.getProperties(), b = primary.getProperties();
      Object.keys(a).forEach(function(k) {
        if (k.indexOf(prefix) === 0) out[k.slice(prefix.length)] = a[k];
      });
      Object.keys(b).forEach(function(k) {
        if (k.indexOf(prefix) === 0) out[k.slice(prefix.length)] = b[k];
      });
      Object.keys(out).forEach(function(k) {
        if (primary.getProperty(key(k)) == null) primary.setProperty(key(k), String(out[k]));
      });
      return out;
    },
    setProperties: function(obj, deleteOthers) {
      if (deleteOthers) this.deleteAllProperties();
      var out = {};
      Object.keys(obj || {}).forEach(function(k) { out[key(k)] = String(obj[k]); });
      primary.setProperties(out, false);
      return this;
    },
    deleteAllProperties: function() {
      var a = legacy.getProperties(), b = primary.getProperties();
      Object.keys(a).forEach(function(k) { if (k.indexOf(prefix) === 0) legacy.deleteProperty(k); });
      Object.keys(b).forEach(function(k) { if (k.indexOf(prefix) === 0) primary.deleteProperty(k); });
      return this;
    }
  };
}

function sellmonitorGithubEnsureClientAuth_(spreadsheetId) {
  var props = sellmonitorClientProperties_(spreadsheetId);
  var access = String(props.getProperty('SM_INNER_MCP_ACCESS_TOKEN') || '');
  var refresh = String(props.getProperty('SM_INNER_MCP_REFRESH_TOKEN') || '');
  var expiresAt = Number(props.getProperty('SM_INNER_MCP_EXPIRES_AT') || 0);
  var refreshed = false;

  if (refresh && (!access || !expiresAt || expiresAt <= Date.now() + 120000)) {
    var cid = String(props.getProperty('SM_INNER_MCP_CLIENT_ID') || '');
    var endpoint = String(props.getProperty('SM_INNER_MCP_TOKEN_ENDPOINT') || '');
    if (!endpoint) {
      try {
        var meta = UrlFetchApp.fetch('https://sellmonitor.com/mcp/inner-analytics/.well-known/oauth-authorization-server', {
          method:'get', headers:{Accept:'application/json'}, muteHttpExceptions:true, followRedirects:true
        });
        if (meta.getResponseCode() >= 200 && meta.getResponseCode() < 300) {
          endpoint = String((JSON.parse(meta.getContentText()) || {}).token_endpoint || '');
          if (endpoint) props.setProperty('SM_INNER_MCP_TOKEN_ENDPOINT', endpoint);
        }
      } catch (e) {}
    }
    if (cid && endpoint) {
      var payload = 'grant_type=refresh_token&refresh_token=' + encodeURIComponent(refresh) +
        '&client_id=' + encodeURIComponent(cid);
      var rr = UrlFetchApp.fetch(endpoint, {
        method:'post',
        contentType:'application/x-www-form-urlencoded',
        payload:payload,
        headers:{Accept:'application/json'},
        muteHttpExceptions:true
      });
      var body = {};
      try { body = JSON.parse(rr.getContentText()); } catch (e) {}
      if (rr.getResponseCode() >= 200 && rr.getResponseCode() < 300 && body.access_token) {
        access = String(body.access_token);
        props.setProperty('SM_INNER_MCP_ACCESS_TOKEN', access);
        if (body.refresh_token) {
          refresh = String(body.refresh_token);
          props.setProperty('SM_INNER_MCP_REFRESH_TOKEN', refresh);
        }
        if (body.expires_in) {
          expiresAt = Date.now() + Number(body.expires_in) * 1000 - 60000;
          props.setProperty('SM_INNER_MCP_EXPIRES_AT', String(expiresAt));
        }
        refreshed = true;
      }
    }
  }

  // Public /mcp/sellmonitor and Inner Analytics use different OAuth credentials.
  // Never copy the Inner token into SM_MCP_ACCESS_TOKEN.

  return {
    ok: Boolean(access),
    inner_access: Boolean(access),
    refresh_token: Boolean(refresh),
    legacy_alias: Boolean(props.getProperty('SM_MCP_ACCESS_TOKEN')),
    refreshed: refreshed,
    expires_at: expiresAt || null
  };
}

function sellmonitorGithubSetControl_(sh, row, map, values) {
  Object.keys(values || {}).forEach(function(k) {
    if (map[k] == null) return;
    sh.getRange(row, map[k] + 1).setValue(values[k]);
  });
}

function sellmonitorGithubLog_(cc, spreadsheetId, stage, status, message, meta) {
  var sh = cc.getSheetByName(SMC_GH.LOG_SHEET);
  if (!sh) return;
  sh.appendRow([
    new Date(),
    spreadsheetId,
    stage,
    status,
    String(message || '').slice(0, 1500),
    sellmonitorGithubJson_(meta)
  ]);
}

function sellmonitorGithubJson_(value) {
  var text;
  try { text = JSON.stringify(value == null ? {} : value); }
  catch (e) { text = JSON.stringify({string: String(value)}); }
  return text.length > SMC_GH.MAX_RESULT_CHARS
    ? text.slice(0, SMC_GH.MAX_RESULT_CHARS) + '…'
    : text;
}


function sellmonitorGithubPlatformReady() {
  var cc = SpreadsheetApp.openById(SMC_GH.CONTROL_CENTER_ID);
  var clients = cc.getSheetByName(SMC_GH.CLIENTS_SHEET);
  var runtime = cc.getSheetByName('Worker_Runtime');
  var now = new Date();

  if (clients && clients.getLastRow() >= 2) {
    var width = Math.max(20, clients.getLastColumn());
    var head = clients.getRange(1, 1, 1, width).getDisplayValues()[0];
    var map = {};
    head.forEach(function(x, i) { if (x) map[String(x).trim()] = i; });
    var vals = clients.getRange(2, 1, clients.getLastRow() - 1, width).getValues();
    for (var i = 0; i < vals.length; i++) {
      if (String(vals[i][map.spreadsheet_id] || '') !== '1z5mewokRdEDfzXacyevXvCpHCuO_pbQs6nDJVhuextQ') continue;
      sellmonitorGithubSetControl_(clients, i + 2, map, {
        state: 'READY',
        status: 'template_ready',
        error: '',
        last_seen: now,
        last_sync: now,
        worker_version: SMC_GH.VERSION,
        qc_status: 'PASS',
        ready_at: now
      });
      break;
    }
  }

  if (runtime) {
    var rv = runtime.getRange(2, 1, Math.max(1, runtime.getLastRow() - 1), 6).getValues();
    rv.forEach(function(r, i) {
      var component = String(r[0] || '');
      if (component === 'Central executor') {
        runtime.getRange(i + 2, 2, 1, 5).setValues([[
          'READY',
          'GitHub Actions -> authorized Apps Script adapter; Make removed from runtime',
          true,
          now,
          'none'
        ]]);
      }
      if (component === 'Make OAuth router') {
        runtime.getRange(i + 2, 2, 1, 5).setValues([[
          'REMOVED_NOT_USED',
          'Not part of Sellmonitor production runtime',
          false,
          now,
          'none'
        ]]);
      }
      if (component === 'READY gate') {
        runtime.getRange(i + 2, 2).setValue('FAIL_CLOSED_PER_CLIENT');
        runtime.getRange(i + 2, 3).setValue(
          'Platform READY. Each client reaches READY only after WB + Sellmonitor OAuth + SKU + backfill + analytics + QC.'
        );
        runtime.getRange(i + 2, 5).setValue(now);
      }
    });
  }

  SpreadsheetApp.flush();
  return {ok: true, platform: 'READY', version: SMC_GH.VERSION};
}


function sellmonitorGithubWebJson_(obj) {
  return ContentService
    .createTextOutput(JSON.stringify(obj))
    .setMimeType(ContentService.MimeType.JSON);
}


function sellmonitorGithubOAuthHtml_(ok, title, detail) {
  var safeTitle = String(title || '').replace(/[<>&"]/g, function(c) {
    return {'<':'&lt;','>':'&gt;','&':'&amp;','"':'&quot;'}[c];
  });
  var safeDetail = String(detail || '').replace(/[<>&"]/g, function(c) {
    return {'<':'&lt;','>':'&gt;','&':'&amp;','"':'&quot;'}[c];
  });
  var bg = ok ? '#ecfdf5' : '#fff7ed';
  var border = ok ? '#10b981' : '#f97316';
  return HtmlService.createHtmlOutput(
    '<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">' +
    '<title>' + safeTitle + '</title></head><body style="font-family:-apple-system,BlinkMacSystemFont,Segoe UI,sans-serif;background:#f7f7f8;margin:0;padding:32px">' +
    '<div style="max-width:620px;margin:40px auto;background:white;border:1px solid #ddd;border-left:6px solid '+border+';border-radius:14px;padding:26px">' +
    '<h2 style="margin-top:0">' + safeTitle + '</h2><p style="line-height:1.5">' + safeDetail + '</p>' +
    '<p style="color:#666">Это окно можно закрыть. Таблица продолжит загрузку автоматически.</p></div></body></html>'
  );
}

function sellmonitorGithubOAuthCallback_(e) {
  try {
    var p = e && e.parameter ? e.parameter : {};
    var state = String(p.state || '');
    var dot = state.indexOf('.');
    if (dot < 20) throw new Error('OAuth state malformed');

    var spreadsheetId = state.slice(0, dot);
    var cc = SpreadsheetApp.openById(SMC_GH.CONTROL_CENTER_ID);
    var clients = cc.getSheetByName(SMC_GH.CLIENTS_SHEET);
    if (!clients) throw new Error('CONTROL_CENTER Clients missing');

    var vals = clients.getDataRange().getValues();
    var head = vals[0] || [], map = {};
    head.forEach(function(x, i) { if (x) map[String(x).trim()] = i; });
    var clientRow = 0, enabled = false;
    for (var i = 1; i < vals.length; i++) {
      if (String(vals[i][map.spreadsheet_id] || '') === spreadsheetId) {
        clientRow = i + 1;
        enabled = vals[i][map.enabled] === true || String(vals[i][map.enabled] || '').toUpperCase() === 'TRUE';
        break;
      }
    }
    if (!clientRow || !enabled) throw new Error('Unknown or disabled client');

    var props = sellmonitorClientProperties_(spreadsheetId);
    var expectedState = String(props.getProperty('SM_INNER_MCP_STATE') || '');
    if (!expectedState || expectedState !== state) throw new Error('OAuth state mismatch');
    if (p.error) throw new Error('Sellmonitor OAuth: ' + String(p.error_description || p.error));

    var code = String(p.code || '');
    if (!code) throw new Error('OAuth code missing');

    var cid = String(props.getProperty('SM_INNER_MCP_CLIENT_ID') || '');
    var redirect = String(props.getProperty('SM_INNER_MCP_REDIRECT_URI') || '');
    var verifier = String(props.getProperty('SM_INNER_MCP_CODE_VERIFIER') || '');
    if (!cid || !redirect || !verifier) throw new Error('OAuth session incomplete');

    function form(o) {
      return Object.keys(o).map(function(k) {
        return encodeURIComponent(k) + '=' + encodeURIComponent(o[k]);
      }).join('&');
    }
    function parseMcp(t) {
      t = String(t || '').trim();
      try { return JSON.parse(t); } catch (x) {}
      var a = [];
      t.split(/\r?\n/).forEach(function(l) {
        if (l.indexOf('data:') === 0) {
          var d = l.slice(5).trim();
          if (d && d !== '[DONE]') {
            try { a.push(JSON.parse(d)); } catch (x) {}
          }
        }
      });
      return a.length === 1 ? a[0] : (a[0] || null);
    }
    function dataOf(o) {
      if (Array.isArray(o)) o = o.filter(function(x){ return x && x.id === 2; })[0] || o[0];
      var r = o && o.result ? o.result : {}, c = r.content;
      if (Array.isArray(c)) {
        for (var j = 0; j < c.length; j++) {
          if (c[j] && c[j].type === 'text' && c[j].text) {
            try { return JSON.parse(c[j].text); } catch (x) { return {text:c[j].text}; }
          }
        }
      }
      return r.structuredContent || r;
    }

    var base = 'https://sellmonitor.com/mcp/inner-analytics';
    var tokenEndpoint = String(props.getProperty('SM_INNER_MCP_TOKEN_ENDPOINT') || '');
    if (!tokenEndpoint) {
      var metaResp = UrlFetchApp.fetch(base + '/.well-known/oauth-authorization-server', {
        method:'get', headers:{Accept:'application/json'}, muteHttpExceptions:true, followRedirects:true
      });
      var metaJson = {};
      try { metaJson = JSON.parse(metaResp.getContentText()); } catch (x) {}
      tokenEndpoint = String(metaJson.token_endpoint || '');
    }
    if (!tokenEndpoint) throw new Error('OAuth token endpoint missing from metadata');
    var tr = UrlFetchApp.fetch(tokenEndpoint, {
      method:'post',
      contentType:'application/x-www-form-urlencoded',
      payload:form({
        grant_type:'authorization_code',
        code:code,
        redirect_uri:redirect,
        client_id:cid,
        code_verifier:verifier
      }),
      headers:{Accept:'application/json'},
      muteHttpExceptions:true
    });
    var tj = {};
    try { tj = JSON.parse(tr.getContentText()); } catch (x) {}
    if (tr.getResponseCode() < 200 || tr.getResponseCode() >= 300 || !tj.access_token) {
      throw new Error('OAuth token exchange HTTP ' + tr.getResponseCode());
    }

    props.setProperty('SM_INNER_MCP_ACCESS_TOKEN', tj.access_token);
    if (tj.refresh_token) props.setProperty('SM_INNER_MCP_REFRESH_TOKEN', tj.refresh_token);
    if (tj.expires_in) props.setProperty('SM_INNER_MCP_EXPIRES_AT', String(Date.now() + Number(tj.expires_in) * 1000 - 60000));

    var token = tj.access_token;
    function post(body, sid) {
      var h = {Authorization:'Bearer ' + token, Accept:'application/json, text/event-stream'};
      if (sid) h['Mcp-Session-Id'] = sid;
      var rr = UrlFetchApp.fetch(base, {
        method:'post',
        contentType:'application/json',
        payload:JSON.stringify(body),
        headers:h,
        muteHttpExceptions:true
      });
      var hh = rr.getAllHeaders();
      return {
        http:rr.getResponseCode(),
        sid:String(hh['Mcp-Session-Id'] || hh['mcp-session-id'] || sid || ''),
        json:parseMcp(rr.getContentText()),
        text:rr.getContentText()
      };
    }

    var init = post({
      jsonrpc:'2.0', id:1, method:'initialize',
      params:{protocolVersion:'2025-06-18',capabilities:{},clientInfo:{name:'Sellmonitor Sheet Onboarding',version:'2.0'}}
    }, '');
    if (init.http < 200 || init.http >= 300) throw new Error('MCP initialize HTTP ' + init.http);
    post({jsonrpc:'2.0',method:'notifications/initialized',params:{}}, init.sid);

    var ss = SpreadsheetApp.openById(spreadsheetId);
    var ui = ss.getSheetByName('00_API_Подключение');
    var set = ss.getSheetByName('99_Настройки');
    var reg = ss.getSheetByName('85_Подключение');
    var q = ss.getSheetByName('97_Управление');
    if (!ui || !set || !reg || !q) throw new Error('Client onboarding sheets missing');

    function getSetting(k) {
      var n = Math.max(1, set.getLastRow());
      var a = set.getRange(1,1,n,2).getDisplayValues();
      for (var z = 0; z < a.length; z++) if (String(a[z][0]) === k) return String(a[z][1] || '').trim();
      return '';
    }
    function setSetting(k, v, d) {
      var n = Math.max(1, set.getLastRow());
      var a = set.getRange(1,1,n,1).getValues(), row = 0;
      for (var z = 0; z < a.length; z++) if (String(a[z][0]) === k) { row = z + 1; break; }
      if (!row) {
        row = set.getLastRow() + 1;
        set.getRange(row,1,1,3).setValues([[k,v,d || '']]);
      } else {
        set.getRange(row,2).setValue(v);
        if (d) set.getRange(row,3).setValue(d);
      }
    }
    function queueSlot() {
      var a = q.getRange(1200,1,824,10).getValues();
      for (var z = 0; z < a.length; z++) if (!String(a[z][0] || '') && !String(a[z][4] || '')) return 1200 + z;
      throw new Error('Queue full');
    }

    var storeName = getSetting('STORE_NAME') || String(ui.getRange('B5').getDisplayValue() || '').trim();
    var storeId = getSetting('ACTIVE_STORE_ID') || String(ui.getRange('B6').getDisplayValue() || '').trim();
    var profile = getSetting('FBS_CLIENT_ID');

    var call = post({
      jsonrpc:'2.0', id:2, method:'tools/call',
      params:{name:'list_marketplace_accounts',arguments:{marketplaceCode:'wb',nameQuery:storeName}}
    }, init.sid);
    if (call.http < 200 || call.http >= 300) throw new Error('list_marketplace_accounts HTTP ' + call.http);

    var data = dataOf(call.json), candidates = [];
    function walk(x) {
      if (x == null) return;
      if (Array.isArray(x)) { x.forEach(walk); return; }
      if (typeof x !== 'object') return;
      var id = x.marketplaceAccountId || x.marketplace_account_id || x.id;
      if (id && isFinite(Number(id))) {
        candidates.push({
          id:Number(id),
          name:String(x.name || x.title || x.accountName || x.marketplaceAccountName || x.storeName || ''),
          obj:x
        });
      }
      Object.keys(x).forEach(function(k){ if (x[k] && typeof x[k] === 'object') walk(x[k]); });
    }
    walk(data);
    var seen = {}, unique = [];
    candidates.forEach(function(x){ if (!seen[x.id]) { seen[x.id] = 1; unique.push(x); } });
    if (!unique.length) throw new Error('WB marketplaceAccountId not found');

    var needle = storeName.toLowerCase();
    var pick = unique.filter(function(x){ return x.name && x.name.toLowerCase().indexOf(needle) >= 0; })[0] || unique[0];
    var accountId = pick.id;
    var merchantId = Number(pick.obj.merchantId || pick.obj.merchant_id || 0) || '';

    reg.getRange('A14:L14').setValues([[
      storeId,true,storeName,'wb','',merchantId,'',profile,'2025-01-01','CONNECTED',profile,accountId
    ]]);

    setSetting('SELLMONITOR_INNER_ACCOUNT_ID', accountId);
    if (merchantId) setSetting('SELLMONITOR_MERCHANT_ID', merchantId);
    setSetting('SM_INNER_MCP_STATUS','AUTHORIZED','Sellmonitor Inner OAuth выполнен');
    setSetting('MCP_STATUS','AUTHORIZED','Sellmonitor OAuth выполнен');

    ui.getRange('B8').setValue(accountId);
    ui.getRange('B13').setValue('ГОТОВО');
    ui.getRange('E13').setValue('АВТОРИЗАЦИЯ ГОТОВА');
    ui.getRange('B20').setValue('ГОТОВО');
    ui.getRange('B21').setValue('ГОТОВО · ' + storeId + ' · account ' + accountId);

    var queue = q.getRange(1200,1,824,8).getValues();
    for (var z = 0; z < queue.length; z++) {
      var qid = String(queue[z][0] || '');
      var qst = String(queue[z][4] || '');
      if (qid.indexOf('OAUTH-CAPTURE-' + storeId + '-') === 0 && (qst === 'PENDING' || qst === 'SCHEDULED' || qst === 'NEW')) {
        q.getRange(1200 + z,5).setValue('CANCELLED_REPLACED_DIRECT_CALLBACK');
      }
    }

    var canaryPrefix = 'POST-OAUTH-CANARY-' + storeId + '-';
    var hasCanary = false;
    var q2 = q.getRange(1200,1,824,5).getValues();
    for (var z = 0; z < q2.length; z++) {
      if (String(q2[z][0] || '').indexOf(canaryPrefix) === 0 && ['PENDING','RUNNING','NEW','SCHEDULED','DONE'].indexOf(String(q2[z][4] || '')) >= 0) {
        hasCanary = true; break;
      }
    }
    if (!hasCanary) {
      var row = queueSlot();
      q.getRange(row,1,1,5).setValues([[
        canaryPrefix + Date.now(), new Date(), 'RUN_REMOTE',
        JSON.stringify({file:'client_post_oauth_canary_v300',entrypoint:'REMOTE_MAIN',payload:{storeId:storeId}}),
        'PENDING'
      ]]);
    }
    ui.getRange('B22').setValue('ПРОВЕРКА · OAuth persistence canary');

    if (map.sellmonitor_account_id != null) clients.getRange(clientRow, map.sellmonitor_account_id + 1).setValue(accountId);
    if (map.status != null) clients.getRange(clientRow, map.status + 1).setValue('oauth_authorized');
    if (map.state != null) clients.getRange(clientRow, map.state + 1).setValue('ACTIVE');
    if (map.error != null) clients.getRange(clientRow, map.error + 1).clearContent();

    SpreadsheetApp.flush();
    return sellmonitorGithubOAuthHtml_(true, 'Sellmonitor подключён', storeName + ': авторизация завершена.');
  } catch (err) {
    try {
      var state2 = String(e && e.parameter ? e.parameter.state || '' : '');
      var dot2 = state2.indexOf('.');
      if (dot2 > 20) {
        var sid2 = state2.slice(0, dot2);
        var ss2 = SpreadsheetApp.openById(sid2);
        var ui2 = ss2.getSheetByName('00_API_Подключение');
        if (ui2) {
          ui2.getRange('B13').setValue('ОШИБКА OAUTH');
          ui2.getRange('B20').setValue('ОШИБКА · ' + String(err.message || err).slice(0,180));
        }
      }
    } catch (ignored) {}
    return sellmonitorGithubOAuthHtml_(false, 'Авторизация не завершена', String(err && err.message ? err.message : err));
  }
}

function sellmonitorGithubSha256Hex_(value) {
  var digest = Utilities.computeDigest(
    Utilities.DigestAlgorithm.SHA_256,
    String(value || ''),
    Utilities.Charset.UTF_8
  );
  return digest.map(function(b) {
    var n = b < 0 ? b + 256 : b;
    return ('0' + n.toString(16)).slice(-2);
  }).join('');
}

function sellmonitorGithubWebAuth_(token) {
  token = String(token || '');
  var expectedHash = String(SMC_GH.WEBHOOK_SECRET_SHA256 || '').trim().toLowerCase();
  if (!expectedHash || expectedHash === '__sellmonitor_github_webhook_secret_sha256__') {
    throw new Error('SELLMONITOR_WEBHOOK_HASH_NOT_COMPILED');
  }
  if (sellmonitorGithubSha256Hex_(token) !== expectedHash) {
    throw new Error('UNAUTHORIZED');
  }
}


const SMC_PORTFOLIO_SOURCES = Object.freeze({
  weekly_summary: {
    spreadsheet_id: '1hU24PrecF2hbeLbKfPEKRQbjXhdd8kONLTMsR4yNIug',
    ranges: {
      comparison: 'Сравнение!A1:AN160',
      air: 'AIR 09.09–16.09!A1:R120',
      sanych: 'Саныч 09.09–16.09!A1:R160',
      hozyushka: 'Хозяюшка 09.09–16.09!A1:R120'
    }
  },
  own_27: {
    spreadsheet_id: '1VQwf-QPeSjexrEculDjWt_hpuCKu7PLzLZhMFjP2VZM',
    ranges: {
      summary: 'Сводная!A1:BI1200',
      unit_economics: 'Юнитка!A1:AQ500',
      ff_history: 'История остатков ФФ!A1:J800',
      orders_history: 'Заказы!A1:J40000',
      order_lifecycle: '_WB_ORDER_FEED!A1:L7000',
      supply_plan: '_ORDER_ANALYSIS_TMP!A1:L1000',
      ozon_cabinets: 'Ozon кабинеты!A1:X1200',
      ozon_orders: 'Ozon Заказы!A1:V5000'
    }
  },
  sanych_sellmonitor: {
    spreadsheet_id: '1-aBDZ7c5xfmVwwiNmUi9-DyfIANXmfiM5-Ti2_zg4zI',
    ranges: {
      stocks: '06_Остатки!A1:Z300',
      positions: '07_Контроль_позиций!A1:R6000'
    }
  },
  air_sellmonitor: {
    spreadsheet_id: '1SmsoG8zKx3hbTtTzS-zLekTFiWEQN8eIwHOxXq-5RHo',
    ranges: {
      stocks: '06_Остатки!A1:Z300',
      positions: '07_Контроль_позиций!A1:R6000'
    }
  },
  hozyushka_sellmonitor: {
    spreadsheet_id: '1cVT_H_e8a519k_Gtph6fALWnBbtBrAQ2Jb_gFO3bM64',
    ranges: {
      stocks: '06_Остатки!A1:Z300',
      positions: '07_Контроль_позиций!A1:R6000'
    }
  }
});

function sellmonitorGithubTrimRows_(values) {
  var end = values.length;
  while (end > 0) {
    var row = values[end - 1] || [];
    var nonEmpty = false;
    for (var i = 0; i < row.length; i++) {
      if (String(row[i] == null ? '' : row[i]).trim() !== '') {
        nonEmpty = true;
        break;
      }
    }
    if (nonEmpty) break;
    end--;
  }
  return values.slice(0, end);
}

function sellmonitorGithubCompactPositions_(values) {
  if (!values || !values.length) return values || [];
  var headerIdx = -1, idx = {};
  for (var i = 0; i < Math.min(values.length, 10); i++) {
    var labels = {};
    (values[i] || []).forEach(function(x, j) { labels[String(x || '').trim()] = j; });
    if (labels.nmId != null && labels['Частотность'] != null && labels['Текущая позиция'] != null) {
      headerIdx = i;
      idx = labels;
      break;
    }
  }
  if (headerIdx < 0) return values;
  var out = values.slice(0, headerIdx + 1);
  for (var r = headerIdx + 1; r < values.length; r++) {
    var row = values[r] || [];
    var sku = String(row[idx.nmId] || '').trim();
    var activeIdx = idx['Активен'];
    var active = activeIdx == null ? 'TRUE' : String(row[activeIdx] || '').trim().toUpperCase();
    var queryIdx = idx['Поисковый запрос'];
    var query = queryIdx == null ? '' : String(row[queryIdx] || '').trim();
    if (!/^\d+$/.test(sku)) continue;
    if (activeIdx != null && ['TRUE','1','ДА'].indexOf(active) < 0) continue;
    if (queryIdx != null && !query) continue;
    out.push(row);
  }
  return out;
}

function sellmonitorGithubReadRange_(ss, a1) {
  var bang = String(a1).indexOf('!');
  if (bang < 1) throw new Error('Invalid A1 range: ' + a1);
  var sheetName = String(a1).slice(0, bang);
  var localA1 = String(a1).slice(bang + 1);
  var sh = ss.getSheetByName(sheetName);
  if (!sh) throw new Error('Missing sheet: ' + sheetName + ' in ' + ss.getId());
  var requested = sh.getRange(localA1);
  var startRow = requested.getRow();
  var startCol = requested.getColumn();
  var lastRow = sh.getLastRow();
  var lastCol = sh.getLastColumn();
  if (lastRow < startRow || lastCol < startCol) return [];
  var rows = Math.min(requested.getNumRows(), lastRow - startRow + 1);
  var cols = Math.min(requested.getNumColumns(), lastCol - startCol + 1);
  return sellmonitorGithubTrimRows_(sh.getRange(startRow, startCol, rows, cols).getDisplayValues());
}

function sellmonitorGithubPortfolioSnapshot_(mode) {
  mode = String(mode || 'fast').toLowerCase();
  var heavyOwn27 = {
    orders_history: true,
    order_lifecycle: true,
    ozon_cabinets: true,
    ozon_orders: true
  };
  var out = {
    ok: true,
    generated_at: new Date().toISOString(),
    runtime: 'GITHUB_ACTIONS_GOOGLE_ADAPTER',
    sources: {},
    errors: []
  };
  Object.keys(SMC_PORTFOLIO_SOURCES).forEach(function(sourceId) {
    var cfg = SMC_PORTFOLIO_SOURCES[sourceId];
    var source = {
      spreadsheet_id: cfg.spreadsheet_id,
      modified_at: '',
      ranges: {}
    };
    try {
      var ss = SpreadsheetApp.openById(cfg.spreadsheet_id);
      try {
        source.modified_at = DriveApp.getFileById(cfg.spreadsheet_id).getLastUpdated().toISOString();
      } catch (_ignored) {}
      Object.keys(cfg.ranges).forEach(function(rangeKey) {
        if (mode !== 'full' && sourceId === 'own_27' && heavyOwn27[rangeKey]) return;
        var a1 = cfg.ranges[rangeKey];
        try {
          var values = sellmonitorGithubReadRange_(ss, a1);
          if (rangeKey === 'positions') values = sellmonitorGithubCompactPositions_(values);
          source.ranges[rangeKey] = {
            a1: a1,
            values: values
          };
        } catch (rangeError) {
          source.ranges[rangeKey] = {a1: a1, values: [], error: String(rangeError.message || rangeError)};
          out.errors.push({source: sourceId, range: rangeKey, error: String(rangeError.message || rangeError)});
        }
      });
    } catch (sourceError) {
      source.error = String(sourceError.message || sourceError);
      out.errors.push({source: sourceId, error: source.error});
    }
    out.sources[sourceId] = source;
  });
  return out;
}


function sellmonitorGithubPublishRuntimeSearch_(cabinet, snapshot) {
  var map = {
    air: {spreadsheet_id:'1SmsoG8zKx3hbTtTzS-zLekTFiWEQN8eIwHOxXq-5RHo', store_id:'air_wb', name:'AIR'},
    hozyushka: {spreadsheet_id:'1cVT_H_e8a519k_Gtph6fALWnBbtBrAQ2Jb_gFO3bM64', store_id:'hozyayushka_wb', name:'Хозяюшка'}
  };
  var cfg = map[String(cabinet || '').toLowerCase()];
  if (!cfg) return {ok:true, skipped:true, reason:'cabinet_not_runtime_search_target'};
  snapshot = snapshot || {};
  var observedAt = String(snapshot.created_at || new Date().toISOString());
  var data = snapshot.data && typeof snapshot.data === 'object' ? snapshot.data : {};
  var rows = [];
  Object.keys(data).forEach(function(k) {
    var x = data[k] || {};
    if (String(x.source || '') !== 'wb_search_report') return;
    var nm = String(x.nm_id || x.nmId || '').trim();
    var query = String(x.query || '').trim();
    var pos = Number(x.position);
    if (!/^\d+$/.test(nm) || !query || !isFinite(pos)) return;
    var freq = x.frequency == null || x.frequency === '' ? '' : Number(x.frequency);
    if (freq !== '' && !isFinite(freq)) freq = '';
    rows.push([true,nm,'',query,freq,pos,observedAt,'','','','','WB LIVE','','','','5',false,'WB runtime search_positions · factual']);
  });
  rows.sort(function(a,b){return Number(a[1])-Number(b[1]) || String(a[3]).localeCompare(String(b[3]));});
  if (!rows.length) return {ok:true, skipped:true, reason:'wb_search_report_no_query_rows', cabinet:cabinet, observed_at:observedAt};

  var ss=SpreadsheetApp.openById(cfg.spreadsheet_id);
  var sh=ss.getSheetByName('07_Контроль_позиций');
  if (!sh) sh=ss.insertSheet('07_Контроль_позиций');
  if (sh.getMaxColumns()<18) sh.insertColumnsAfter(sh.getMaxColumns(),18-sh.getMaxColumns());
  if (sh.getMaxRows()<rows.length+3) sh.insertRowsAfter(sh.getMaxRows(),rows.length+3-sh.getMaxRows());

  sh.getRange(1,1,Math.max(3,sh.getMaxRows()),18).clearContent();
  sh.getRange('A1:R1').merge().setValue('КОНТРОЛЬ ПОИСКОВЫХ ПОЗИЦИЙ · WB LIVE RUNTIME');
  sh.getRange(2,1,1,18).setValues([[
    'Обновление','обновлено '+Utilities.formatDate(new Date(),ss.getSpreadsheetTimeZone(),'dd.MM.yyyy HH:mm')+' · строк '+rows.length,
    'Источник','WB Analytics runtime','Активных',rows.length,'','','','','','','','','','','',''
  ]]);
  sh.getRange(3,1,1,18).setValues([[
    'Активен','nmId','Товар','Поисковый запрос','Частотность','Текущая позиция','Дата снимка',
    'Рекомендуемая цель','Плановая позиция','Эффективная цель','Отклонение','Статус',
    'Предыдущая позиция','Δ к прошлому','Дней вне цели','Порог алерта','Нужен алерт','Комментарий'
  ]]);
  sh.getRange(4,1,rows.length,18).setValues(rows);
  sh.getRange(4,1,rows.length,1).insertCheckboxes();
  sh.getRange(4,17,rows.length,1).insertCheckboxes();
  sh.setFrozenRows(3);
  SpreadsheetApp.flush();
  return {ok:true,cabinet:cabinet,store_id:cfg.store_id,rows:rows.length,observed_at:observedAt,source:'wb_search_report'};
}

function sellmonitorGithubPublishDashboard_(dashboard) {
  dashboard = dashboard || {};
  var cc = SpreadsheetApp.openById(SMC_GH.CONTROL_CENTER_ID);
  var name = 'WB_AI_Manager';
  var sh = cc.getSheetByName(name) || cc.insertSheet(name);
  sh.clear({contentsOnly: true});
  var rows = [];
  function push() {
    var a = Array.prototype.slice.call(arguments);
    while (a.length < 8) a.push('');
    rows.push(a.slice(0, 8));
  }

  push('WB AI MANAGER', '', '', '', '', '', '', '');
  push('Обновлено', dashboard.generated_at || new Date().toISOString(), 'Статус', dashboard.overall_status || '', '', '', '', '');
  push('', '', '', '', '', '', '', '');
  push('КАБИНЕТ', 'WB', 'СТАТУС', 'РЕШЕНИЙ', 'CRITICAL', 'WARNING', 'ОШИБОК АГЕНТОВ', '');
  (dashboard.cabinets || []).forEach(function(x) {
    push(x.name || x.id || '', x.wb_connected ? 'OK' : 'НЕТ', x.status || '', x.decisions || 0, x.critical || 0, x.warnings || 0, x.agent_errors || 0, '');
  });

  push('', '', '', '', '', '', '', '');
  push('ПРИОРИТЕТ', 'КАБИНЕТ', 'РЕШЕНИЕ', 'ДИАГНОЗ', 'УВЕРЕННОСТЬ', 'FOLLOW-UP', 'ОБЪЕКТ', '');
  (dashboard.top_actions || []).slice(0, 25).forEach(function(x) {
    push(x.priority || '', x.cabinet || '', x.title || '', x.diagnosis || '', x.confidence || '', x.follow_up || '', x.entity_id || '', '');
  });

  push('', '', '', '', '', '', '', '');
  push('QUERY MANAGER', 'ЗНАЧЕНИЕ', '', '', '', '', '', '');
  var qs = dashboard.query_summary || {};
  [
    ['Статус', qs.query_status],
    ['Запросов', qs.query_rows],
    ['Нужно обновить факты', qs.refresh_fact],
    ['Protect top', qs.protect_top],
    ['Fix gap', qs.fix_gap],
    ['Avoid overbuy', qs.avoid_overbuy],
    ['Готовых действий', qs.ready_query_actions],
    ['Блокеров данных', qs.data_blockers]
  ].forEach(function(x) { push(x[0], x[1] == null ? '' : x[1], '', '', '', '', '', ''); });

  push('', '', '', '', '', '', '', '');
  push('ЗАДАЧИ ПО ЗАПРОСАМ', 'SKU', 'ЗАПРОС', 'ОЧЕРЕДЬ', 'РЕШЕНИЕ', 'СТАВКА СЕЙЧАС', 'ЦЕЛЬ', 'РЕЖИМ');
  (dashboard.query_tasks || []).slice(0, 30).forEach(function(x) {
    push('', x.sku || '', x.query || '', x.queue || '', x.decision || '', x.current_search_bid_rub == null ? '' : x.current_search_bid_rub, x.target_search_bid_rub == null ? '' : x.target_search_bid_rub, x.execution_mode || '');
  });

  push('', '', '', '', '', '', '', '');
  push('БЛОКЕРЫ ДАННЫХ', 'SKU', 'ЗАПРОС', 'БЛОКЕРЫ', '', '', '', '');
  (dashboard.data_blockers || []).slice(0, 30).forEach(function(x) {
    push('', x.sku || '', x.query || '', (x.blockers || []).join(', '), '', '', '', '');
  });

  if (rows.length) {
    sh.getRange(1, 1, rows.length, 8).setValues(rows);
    sh.getRange(1, 1, 1, 8).setFontWeight('bold').setFontSize(14);
    sh.getRange(4, 1, 1, 8).setFontWeight('bold');
    sh.setFrozenRows(4);
    sh.setColumnWidth(1, 130);
    sh.setColumnWidth(2, 130);
    sh.setColumnWidth(3, 320);
    sh.setColumnWidth(4, 360);
    sh.setColumnWidth(5, 120);
    sh.setColumnWidth(6, 320);
    sh.setColumnWidth(7, 150);
    sh.setColumnWidth(8, 130);
    sh.getDataRange().setVerticalAlignment('top').setWrap(true);
  }
  SpreadsheetApp.flush();
  return {ok: true, sheet: name, rows: rows.length, updated_at: new Date().toISOString()};
}

function doGet(e) {
  try {
    var params = e && e.parameter ? e.parameter : {};
    if ((params.code || params.error) && params.state) {
      return sellmonitorGithubOAuthCallback_(e);
    }
    var token = params.token || '';
    var action = String(params.action || 'health');

    if (action === 'oauth_callback') {
      return sellmonitorGithubOAuthCallback_(e);
    }

    if (action === 'bootstrap_once') {
      var props = PropertiesService.getScriptProperties();
      if (props.getProperty('SMC_BOOTSTRAP_ONCE_USED') === '1') {
        return sellmonitorGithubWebJson_({ok: false, error: 'BOOTSTRAP_ALREADY_USED'});
      }
      props.setProperty('SMC_BOOTSTRAP_ONCE_USED', '1');
      var bootstrapTrigger = sellmonitorGithubEnsureTrigger_();
      var bootstrapReady = sellmonitorGithubPlatformReady();
      var bootstrapTick = sellmonitorGithubTick();
      return sellmonitorGithubWebJson_({
        ok: Boolean(bootstrapTick && bootstrapTick.ok),
        action: action,
        trigger: bootstrapTrigger,
        platform: bootstrapReady,
        tick: bootstrapTick
      });
    }

    if (action === 'tick_once_v2') {
      var onceProps = PropertiesService.getScriptProperties();
      var onceKey = 'SMC_TICK_ONCE_V2_USED';
      if (onceProps.getProperty(onceKey) === '1') {
        return sellmonitorGithubWebJson_({ok: false, error: 'TICK_ONCE_V2_ALREADY_USED'});
      }
      onceProps.setProperty(onceKey, '1');
      var onceTrigger = sellmonitorGithubEnsureTrigger_();
      var onceReady = sellmonitorGithubPlatformReady();
      var onceTick = sellmonitorGithubTick();
      return sellmonitorGithubWebJson_({
        ok: Boolean(onceTick && onceTick.ok),
        action: action,
        trigger: onceTrigger,
        platform: onceReady,
        tick: onceTick
      });
    }

    if (action === 'tick_once_v3') {
      var onceProps3 = PropertiesService.getScriptProperties();
      var onceKey3 = 'SMC_TICK_ONCE_V3_USED';
      if (onceProps3.getProperty(onceKey3) === '1') {
        return sellmonitorGithubWebJson_({ok: false, error: 'TICK_ONCE_V3_ALREADY_USED'});
      }
      onceProps3.setProperty(onceKey3, '1');
      var onceTrigger3 = sellmonitorGithubEnsureTrigger_();
      var onceReady3 = sellmonitorGithubPlatformReady();
      var onceTick3 = sellmonitorGithubTick();
      return sellmonitorGithubWebJson_({
        ok: Boolean(onceTick3 && onceTick3.ok),
        action: action,
        trigger: onceTrigger3,
        platform: onceReady3,
        tick: onceTick3
      });
    }

    sellmonitorGithubWebAuth_(token);

    if (action === 'tick') {
      var trigger = sellmonitorGithubEnsureTrigger_();
      var ready = sellmonitorGithubPlatformReady();
      var tick = sellmonitorGithubTick();
      return sellmonitorGithubWebJson_({
        ok: Boolean(tick && tick.ok),
        action: action,
        trigger: trigger,
        platform: ready,
        tick: tick
      });
    }

    return sellmonitorGithubWebJson_({
      ok: true,
      service: 'sellmonitor-github-worker',
      version: SMC_GH.VERSION,
      health: sellmonitorGithubHealth()
    });
  } catch (err) {
    return sellmonitorGithubWebJson_({
      ok: false,
      error: String(err && err.message ? err.message : err)
    });
  }
}

function sellmonitorGithubSeedWbTokens_(payload) {
  payload = payload || {};
  var tokens = payload.tokens || {};
  var specs = [
    {sheetId:'1SmsoG8zKx3hbTtTzS-zLekTFiWEQN8eIwHOxXq-5RHo', clientId:'AIR', key:'FBS_CLIENT__AIR__WB_API_TOKEN', token:String(tokens.AIR || '')},
    {sheetId:'1cVT_H_e8a519k_Gtph6fALWnBbtBrAQ2Jb_gFO3bM64', clientId:'FBS_14I5XGBA9NIG', key:'FBS_CLIENT__FBS_14I5XGBA9NIG__WB_API_TOKEN', token:String(tokens.FBS_14I5XGBA9NIG || '')},
    {sheetId:'1-aBDZ7c5xfmVwwiNmUi9-DyfIANXmfiM5-Ti2_zg4zI', clientId:'SANYCH', key:'FBS_CLIENT__SANYCH__WB_API_TOKEN', token:String(tokens.SANYCH || '')}
  ];
  var seeded = [];
  specs.forEach(function(x) {
    if (!x.token) return;
    if (x.token.length < 20) throw new Error('WB token payload invalid for ' + x.clientId);
    sellmonitorClientProperties_(x.sheetId).setProperty(x.key, x.token);
    seeded.push(x.clientId);
  });
  return {ok:true, action:'seed_wb_tokens', seeded:seeded, count:seeded.length, secretsReturned:false};
}


function doPost(e) {
  try {
    var body = JSON.parse((e && e.postData && e.postData.contents) || '{}');
    var action = String(body.action || 'tick');

    sellmonitorGithubWebAuth_(body.token || body.key);

    if (action === 'seed_wb_tokens') {
      return sellmonitorGithubWebJson_(sellmonitorGithubSeedWbTokens_(body));
    }

    if (action === 'health') {
      return sellmonitorGithubWebJson_({
        ok: true,
        action: action,
        result: sellmonitorGithubHealth()
      });
    }

    if (action === 'publish_dashboard') {
      return sellmonitorGithubWebJson_(sellmonitorGithubPublishDashboard_(body.dashboard || {}));
    }

    if (action === 'publish_search_positions') {
      return sellmonitorGithubWebJson_(sellmonitorGithubPublishRuntimeSearch_(body.cabinet || '', body.snapshot || {}));
    }

    if (action === 'all' || action === 'portfolio_snapshot') {
      return sellmonitorGithubWebJson_(sellmonitorGithubPortfolioSnapshot_('fast'));
    }

    if (action === 'all_full' || action === 'portfolio_snapshot_full') {
      return sellmonitorGithubWebJson_(sellmonitorGithubPortfolioSnapshot_('full'));
    }

    if (action === 'platform_ready') {
      return sellmonitorGithubWebJson_({
        ok: true,
        action: action,
        result: sellmonitorGithubPlatformReady()
      });
    }

    if (action === 'tick') {
      var trigger = sellmonitorGithubEnsureTrigger_();
      var ready = sellmonitorGithubPlatformReady();
      var tick = sellmonitorGithubTick();
      return sellmonitorGithubWebJson_({
        ok: Boolean(tick && tick.ok),
        action: action,
        trigger: trigger,
        platform: ready,
        tick: tick
      });
    }

    throw new Error('UNKNOWN_ACTION: ' + action);
  } catch (err) {
    return sellmonitorGithubWebJson_({
      ok: false,
      error: String(err && (err.stack || err.message) ? (err.stack || err.message) : err)
    });
  }
}
