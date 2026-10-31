/**
 * Sellmonitor GitHub Central Worker v1.3.56
 * GitHub is source-of-truth/scheduler. This Apps Script project is only
 * the authorized Google adapter. Make is not part of the execution path.
 */
const SMC_GH = Object.freeze({
  VERSION: 'github-worker-1.3.56',
  CONTROL_CENTER_ID: '1sW51KKwQIvB7GZKyUhukqHXAL_CxJZKL-mjKWGbZLE0',
  CLIENTS_SHEET: 'Clients',
  LOG_SHEET: 'Log',
  MAX_CLIENTS_PER_TICK: 1,
  MAX_COMMANDS_PER_CLIENT: 1,
  MAX_RESULT_CHARS: 45000,
  WEBHOOK_SECRET: '__SELLMONITOR_GITHUB_WEBHOOK_SECRET__',
  WEBHOOK_SECRET_SHA256: '__SELLMONITOR_GITHUB_WEBHOOK_SECRET_SHA256__'
});

const SMC_INNER_FINANCE = Object.freeze({
  initialDate: '2026-10-07',
  historicalStartDate: '2026-09-01',
  historicalEndDateExclusive: '2026-10-07',
  retryIntervalMs: 60 * 1000,
  schemaVersion: 'inner-finance-month-v1',
  endpoint: 'https://sellmonitor.com/mcp/inner-analytics'
});

const SMC_INNER_FINANCE_STORES = Object.freeze({
  sanych_wb: {spreadsheetId:'1-aBDZ7c5xfmVwwiNmUi9-DyfIANXmfiM5-Ti2_zg4zI',storeId:'sanych_wb',shopKey:'ap',storeName:'Дом Саныча',accountId:17410},
  air_wb: {spreadsheetId:'1SmsoG8zKx3hbTtTzS-zLekTFiWEQN8eIwHOxXq-5RHo',storeId:'air_wb',shopKey:'aa',storeName:'AIR',accountId:9562},
  hozyayushka_wb: {spreadsheetId:'1cVT_H_e8a519k_Gtph6fALWnBbtBrAQ2Jb_gFO3bM64',storeId:'hozyayushka_wb',shopKey:'yv',storeName:'Хозяюшка',accountId:27179}
});

const SMC_INNER_FINANCE_HEADERS = [
  'store_id','account_id','mall','period_from','period_to_exclusive','days','nmId','seller_article',
  'orders_qty','orders_revenue_rub','paid_order_qty','net_sales_qty','returns_qty','returns_revenue_rub',
  'revenue_rub','customer_revenue_rub','marketplace_discount_rub','prime_cost_rub','commission_rub',
  'logistic_rub','delivery_logistic_rub','return_logistic_rub','storage_rub','ad_rub','fines_rub',
  'paid_acceptance_rub','deduction_rub','tax_rub','other_expenses_rub','expenses_total_rub','profit_rub',
  'payout_rub','margin','roi','buyout','avg_profit_per_unit_rub','stock_qty_snapshot',
  'stock_prime_cost_snapshot_rub','trust_status','source','loaded_at','product_group','product_subgroup',
  'store_name'
];


function sellmonitorGithubEnsureTrigger_() {
  var fn = 'sellmonitorGithubTick';
  var desiredVersion = 'worker-1m-v2';
  var props = PropertiesService.getScriptProperties();
  var triggers = ScriptApp.getProjectTriggers();
  var found = null;
  var mustRecreate = props.getProperty('SMC_GH_TRIGGER_VERSION') !== desiredVersion;
  for (var i = 0; i < triggers.length; i++) {
    if (triggers[i].getHandlerFunction() !== fn) continue;
    if (mustRecreate || found) ScriptApp.deleteTrigger(triggers[i]);
    else found = triggers[i];
  }
  var created = false;
  if (mustRecreate || !found) {
    ScriptApp.newTrigger(fn).timeBased().everyMinutes(1).create();
    props.setProperty('SMC_GH_TRIGGER_VERSION', desiredVersion);
    created = true;
  }

  var financeFn = 'sellmonitorGithubInnerFinanceTick';
  var financeVersion = 'inner-finance-1m-v1';
  var financeTriggers = ScriptApp.getProjectTriggers();
  var financeFound = null;
  var financeMustRecreate = props.getProperty('SMC_INNER_FINANCE_TRIGGER_VERSION') !== financeVersion;
  for (var j = 0; j < financeTriggers.length; j++) {
    if (financeTriggers[j].getHandlerFunction() !== financeFn) continue;
    if (financeMustRecreate || financeFound) ScriptApp.deleteTrigger(financeTriggers[j]);
    else financeFound = financeTriggers[j];
  }
  var financeCreated = false;
  if (financeMustRecreate || !financeFound) {
    ScriptApp.newTrigger(financeFn).timeBased().everyMinutes(1).create();
    props.setProperty('SMC_INNER_FINANCE_TRIGGER_VERSION', financeVersion);
    financeCreated = true;
  }
  return {
    ok:true,created:created,intervalMinutes:1,
    innerFinanceCreated:financeCreated,innerFinanceIntervalMinutes:1
  };
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
  var triggers = ScriptApp.getProjectTriggers();
  var projectTriggerHandlers = triggers.map(function(t) { return String(t.getHandlerFunction() || ''); }).filter(Boolean).sort();
  var triggerCount = triggers.filter(function(t) {
    return t.getHandlerFunction() === 'sellmonitorGithubTick';
  }).length;
  var innerFinanceTriggerCount = triggers.filter(function(t) {
    return t.getHandlerFunction() === 'sellmonitorGithubInnerFinanceTick';
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
        search_rows: 0,
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
        var search = ss.getSheetByName('07_Поиск');
        var stocks = ss.getSheetByName('06_Остатки');
        var q = ss.getSheetByName('97_Управление');
        item.positions_rows = pos ? pos.getLastRow() : 0;
        item.search_rows = search ? search.getLastRow() : 0;
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
    inner_finance_trigger_count:innerFinanceTriggerCount,
    project_trigger_handlers:projectTriggerHandlers,
    fbs_ingest_stages: sellmonitorGithubFbsStageSnapshot_(),
    trigger_ok: triggerCount === 1 && innerFinanceTriggerCount === 1
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
      var external = sellmonitorGithubExternalDataPlaneInfo_(item.storeId);
      if (external) {
        // Keep paused clients visible without spending a productive worker slot.
        sellmonitorGithubSetControl_(sh, row, map, {
          last_seen:new Date(),
          worker_version:SMC_GH.VERSION,
          status:external.status,
          state:external.state,
          error:external.error,
          qc_status:external.qcStatus
        });
        continue;
      }
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

function sellmonitorGithubInnerFinanceTick() {
  // Serialize finance ticks with an expiring lease, but release the shared script lock
  // before network calls so one slow queue command cannot starve finance collection.
  var financeLock = LockService.getScriptLock();
  if (!financeLock.tryLock(30000)) return {ok:true,skipped:true,reason:'shared_gateway_lock_busy'};
  var financeLeaseToken = '';
  var financeLockHeld = true;
  var leaseProps = PropertiesService.getScriptProperties();
  var leaseUntil = Number(leaseProps.getProperty('SMC_INNER_FINANCE_ACTIVE_UNTIL_MS') || 0);
  if (leaseUntil > Date.now()) {
    financeLock.releaseLock();
    financeLockHeld = false;
    return {ok:true,skipped:true,reason:'inner_finance_lease_busy'};
  }
  financeLeaseToken = Utilities.getUuid();
  leaseProps.setProperties({
    SMC_INNER_FINANCE_ACTIVE_UNTIL_MS:String(Date.now() + 8 * 60 * 1000),
    SMC_INNER_FINANCE_ACTIVE_TOKEN:financeLeaseToken
  },false);
  financeLock.releaseLock();
  financeLockHeld = false;
  var cc = null;
  var cfg = null;
  try {
    cc = SpreadsheetApp.openById(SMC_GH.CONTROL_CENTER_ID);
    var storeIds = Object.keys(SMC_INNER_FINANCE_STORES);
    if (!storeIds.length) return {ok:true,processed:false,reason:'no_inner_finance_stores'};
    var props = PropertiesService.getScriptProperties();
    var cursor = Number(props.getProperty('SMC_INNER_FINANCE_STORE_CURSOR') || 0);
    if (!isFinite(cursor) || cursor < 0) cursor = 0;
    cursor = Math.floor(cursor) % storeIds.length;
    var storeId = storeIds[cursor];
    props.setProperty('SMC_INNER_FINANCE_STORE_CURSOR',String((cursor + 1) % storeIds.length));
    cfg = SMC_INNER_FINANCE_STORES[storeId];
    var sync = sellmonitorGithubMaybeSyncInnerFinance_(cfg,cc) || {};
    if (sync.attempted) {
      sellmonitorGithubLog_(
        cc,cfg.spreadsheetId,'INNER_FINANCE_MONTHLY_PARTITION',
        sync.ok ? 'DONE' : 'ERROR',sync.ok ? sync.date : sync.error,sync
      );
    }
    SpreadsheetApp.flush();
    return Object.assign({ok:sync.ok !== false,version:SMC_GH.VERSION,store_id:storeId},sync);
  } catch (e) {
    var message = String(e && e.message ? e.message : e).slice(0,1500);
    if (cc && cfg) sellmonitorGithubLog_(cc,cfg.spreadsheetId,'INNER_FINANCE_MONTHLY_PARTITION','ERROR',message,{});
    var schemaDiagnostic = String(PropertiesService.getScriptProperties().getProperty('SMC_INNER_MCP_TOOL_SCHEMA_V5') || PropertiesService.getScriptProperties().getProperty('SMC_INNER_MCP_TOOL_SCHEMA_V4') || '');
    return {ok:false,version:SMC_GH.VERSION,store_id:cfg ? cfg.storeId : '',error:message,schema_diagnostic:schemaDiagnostic.slice(0,1100)};
  } finally {
    if (financeLockHeld) financeLock.releaseLock();
    if (financeLeaseToken) {
      var releaseProps = PropertiesService.getScriptProperties();
      if (String(releaseProps.getProperty('SMC_INNER_FINANCE_ACTIVE_TOKEN') || '') === financeLeaseToken) {
        releaseProps.deleteProperty('SMC_INNER_FINANCE_ACTIVE_UNTIL_MS');
        releaseProps.deleteProperty('SMC_INNER_FINANCE_ACTIVE_TOKEN');
      }
    }
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
  var prod=ss.getSheetByName('04_Товары'),out=ss.getSheetByName('07_Поиск');
  if(!prod||!out)throw new Error('WB search: 04_Товары/07_Поиск missing');

  var catalog={},activeIds=[],allIds=[];
  if(prod.getLastRow()>1){
    prod.getRange(2,1,prod.getLastRow()-1,21).getValues().forEach(function(r){
      var nm=String(r[1]||'').trim();if(!/^\d+$/.test(nm))return;
      var status=String(r[17]||'').trim().toLowerCase();
      catalog[nm]={title:String(r[2]||r[20]||''),brand:String(r[3]||''),sellerArticle:String(r[20]||''),status:status};
      allIds.push(Number(nm));if(status!=='архив')activeIds.push(Number(nm));
    });
  }
  var ids=activeIds.length?activeIds:allIds;
  if(!ids.length)throw new Error('WB search: no numeric nmIds in current catalog');

  var now=new Date(),endDate=new Date(now.getFullYear(),now.getMonth(),now.getDate()-1,12),startDate=new Date(endDate);startDate.setDate(startDate.getDate()-6);
  var pastEnd=new Date(startDate);pastEnd.setDate(pastEnd.getDate()-1);var pastStart=new Date(pastEnd);pastStart.setDate(pastStart.getDate()-6);
  var from=String(payload.from||Utilities.formatDate(startDate,tz,'yyyy-MM-dd')),to=String(payload.to||Utilities.formatDate(endDate,tz,'yyyy-MM-dd')),
      pastFrom=String(payload.pastFrom||Utilities.formatDate(pastStart,tz,'yyyy-MM-dd')),pastTo=String(payload.pastTo||Utilities.formatDate(pastEnd,tz,'yyyy-MM-dd'));

  var rows=[],seen={},calls=0,officialError='';
  try {
    for(var off=0;off<ids.length;off+=50){
      var batch=ids.slice(off,off+50),body={currentPeriod:{start:from,end:to},pastPeriod:{start:pastFrom,end:pastTo},nmIds:batch,topOrderBy:'openCard',includeSubstitutedSKUs:true,includeSearchTexts:true,orderBy:{field:'avgPosition',mode:'asc'},limit:30};
      var res=sellmonitorGithubWbJson_('https://seller-analytics-api.wildberries.ru/api/v2/search-report/product/search-texts','post',token,body);calls++;
      var items=res&&res.data&&Array.isArray(res.data.items)?res.data.items:[];
      items.forEach(function(x){
        var nm=String(x.nmId||'').trim(),q=String(x.text||'').trim();if(!/^\d+$/.test(nm)||!q)return;
        var pos=x.avgPosition&&x.avgPosition.current!=null?Number(x.avgPosition.current):'',freq=x.frequency&&x.frequency.current!=null?Number(x.frequency.current):'';
        if(pos!==''&&!isFinite(pos))pos='';if(freq!==''&&!isFinite(freq))freq='';
        var key=nm+'|'+q.toLowerCase(),old=seen[key];if(old&&Number(old[4]||0)>=Number(freq||0))return;
        var zone=pos===''?'Н/Д':pos<=3?'ТОП-3':pos<=10?'ТОП-10':pos<=30?'11–30':pos<=100?'31–100':'>100',
            pri=pos!==''&&pos<=10?'УДЕРЖИВАТЬ':Number(freq||0)>=1000?'ВЫСОКИЙ · частотный запрос вне ТОП-10':Number(freq||0)>=300?'СРЕДНИЙ · есть потенциал роста':'НИЗКИЙ',cc=catalog[nm]||{};
        seen[key]=[nm,String(x.name||cc.title||''),q,pos,freq,'','',to,now,'','',zone,pri];
      });
      if(off+50<ids.length)Utilities.sleep(1200);
    }
  } catch(e) {
    officialError=String(e&&e.message||e);
    if(!/Jam subscription|Authorization error|HTTP 403/.test(officialError))throw e;
  }

  var trust='FACTUAL_WB_ANALYTICS',source='WB Analytics /api/v2/search-report/product/search-texts';
  if(officialError){
    trust='FACTUAL_PUBLIC_SERP';source='WB public SERP';
    function normalizeQuery(s){s=String(s||'').toLowerCase().replace(/ё/g,'е').replace(/[^0-9a-zа-я]+/g,' ');s=s.replace(/\b\d+(?:[.,]\d+)?\s*(?:шт|штук|кг|г|л|мл|м|см|мм)\b/g,' ').replace(/\b(?:набор|комплект|упаковка|уп)\b/g,' ');return s.split(/\s+/).filter(function(x){return x.length>1&&['для','из','на','по','с'].indexOf(x)<0;}).slice(0,7).join(' ').trim();}
    function products_(o){if(!o||typeof o!=='object')return[];var a=[o.products,o.data&&o.data.products,o.data&&o.data.items,o.result&&o.result.products,o.result&&o.result.items];for(var i=0;i<a.length;i++)if(Array.isArray(a[i]))return a[i];return[];}
    function pid_(o){var n=Number(o&&(o.id||o.nmId||o.nmID));return isFinite(n)&&n>0?n:0;}
    function publicGet_(query,page){
      var bases=['https://search.wb.ru/exactmatch/ru/common/v4/search','https://search.wb.ru/exactmatch/ru/common/v5/search','https://www.wildberries.ru/__internal/search/exactmatch/ru/common/v18/search'],last='';
      var qs='?appType=1&curr=rub&dest=-1257786&query='+encodeURIComponent(query)+'&resultset=catalog&limit=100&page='+page+'&sort=popular&spp=30&suppressSpellcheck=false';
      for(var bi=0;bi<bases.length;bi++){for(var at=0;at<3;at++){try{var rr=UrlFetchApp.fetch(bases[bi]+qs,{method:'get',muteHttpExceptions:true,followRedirects:true,headers:{Accept:'application/json,text/plain,*/*','User-Agent':'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/129 Safari/537.36',Referer:'https://www.wildberries.ru/'}}),code=rr.getResponseCode(),txt=rr.getContentText();if(code===200){try{return JSON.parse(txt||'{}');}catch(j){last='non-json';break;}}last='HTTP '+code;if((code===429||code>=500)&&at<2){Utilities.sleep(800*Math.pow(2,at));continue;}break;}catch(ex){last=String(ex&&ex.message||ex);if(at<2){Utilities.sleep(800*Math.pow(2,at));continue;}}}}throw new Error('PUBLIC_SERP_BLOCKED '+last);
    }
    var limit=Math.min(ids.length,Math.max(10,Math.min(60,Number(payload.publicMaxSku||60)||60))),publicIds=ids.slice(0,limit),queries={};
    publicIds.forEach(function(n){var q=normalizeQuery((catalog[String(n)]||{}).title||'');if(q)(queries[q]||(queries[q]=[])).push(n);});
    var qkeys=Object.keys(queries),publicCalls=0;
    for(var qi=0;qi<qkeys.length;qi++){var q=qkeys[qi],targets=queries[q],found={};for(var page=1;page<=2;page++){var obj=publicGet_(q,page),ps=products_(obj);publicCalls++;if(!ps.length)break;for(var pi=0;pi<ps.length;pi++){var nm=pid_(ps[pi]);if(targets.indexOf(nm)>=0&&!found[nm])found[nm]={position:(page-1)*ps.length+pi+1,name:String(ps[pi].name||ps[pi].title||'')};}if(Object.keys(found).length===targets.length)break;Utilities.sleep(120);}Object.keys(found).forEach(function(nm){var x=found[nm],cc=catalog[nm]||{},pos=x.position,zone=pos<=3?'ТОП-3':pos<=10?'ТОП-10':pos<=30?'11–30':pos<=100?'31–100':'>100',pri=pos<=10?'УДЕРЖИВАТЬ':'PUBLIC SERP · контролировать';seen[nm+'|'+q.toLowerCase()]=[nm,x.name||cc.title||'',q,pos,'','','',to,now,'','',zone,pri];});if(qi<qkeys.length-1)Utilities.sleep(180);}
    calls=publicCalls;
    if(Object.keys(seen).length<3)throw new Error('PUBLIC_SERP_BLOCKED too few factual rows='+Object.keys(seen).length);
  }

  Object.keys(seen).forEach(function(k){rows.push(seen[k]);});
  rows.sort(function(a,b){return Number(a[0])-Number(b[0])||Number(b[4]||0)-Number(a[4]||0)||Number(a[3]||9999)-Number(b[3]||9999);});
  var headers=['Артикул WB','Товар','Поисковый запрос','Позиция · SNAPSHOT','Частотность · SNAPSHOT','Конкуренция · SNAPSHOT','Товаров в выдаче · SNAPSHOT','Дата позиции · SNAPSHOT','Обновлено запроса · SNAPSHOT','Продажи товара rolling 30д · SNAPSHOT','Выручка товара rolling 30д, ₽ · SNAPSHOT','Зона позиции · расчёт из SNAPSHOT','Приоритет SEO · расчёт из SNAPSHOT'];
  out.getRange(1,1,1,headers.length).setValues([headers]);if(out.getMaxRows()>1)out.getRange(2,1,out.getMaxRows()-1,headers.length).clearContent();
  if(rows.length){if(rows.length+1>out.getMaxRows())out.insertRowsAfter(out.getMaxRows(),rows.length+1-out.getMaxRows());out.getRange(2,1,rows.length,headers.length).setValues(rows);}
  out.setFrozenRows(1);SpreadsheetApp.flush();sellmonitorClientProperties_(ss.getId()).setProperty('SMC_WB_SEARCH_SNAPSHOT_AT_MS',String(Date.now()));
  return{ok:true,storeId:auth.storeId,clientId:auth.clientId,from:from,to:to,pastFrom:pastFrom,pastTo:pastTo,skuCount:ids.length,apiCalls:calls,positionRows:rows.length,source:source,trustStatus:trust,officialError:officialError?officialError.slice(0,220):'',secretsReturned:false};
}

function sellmonitorGithubRefreshGithubSearch_(ss, payload) {
  payload = payload || {};
  var storeId = String(payload.storeId || '').trim();
  var map = {
    air_wb: {cabinet:'air', file:'air.json'},
    hozyayushka_wb: {cabinet:'hozyushka', file:'hozyushka.json'}
  };
  var cfg = map[storeId];
  if (!cfg) throw new Error('GitHub search snapshot unsupported store: ' + storeId);
  var url = 'https://raw.githubusercontent.com/v88218429-netizen/zzzz/mainggg/wb_data/search/' + cfg.file + '?t=' + Date.now();
  var r = UrlFetchApp.fetch(url, {method:'get', muteHttpExceptions:true, followRedirects:true, headers:{Accept:'application/json'}});
  var code = r.getResponseCode();
  var text = r.getContentText();
  if (code !== 200) throw new Error('GitHub search snapshot HTTP ' + code + ' ' + String(text || '').slice(0,250));
  var snap = {};
  try { snap = JSON.parse(text || '{}'); }
  catch(e) { throw new Error('GitHub search snapshot non-JSON'); }
  var trust=String(snap.trust_status || '');
  if (['FACTUAL_WB_ANALYTICS','FACTUAL_PUBLIC_SERP'].indexOf(trust)<0) throw new Error('GitHub search snapshot trust gate failed: ' + trust);
  var createdAt = new Date(String(snap.created_at || ''));
  if (!(createdAt instanceof Date) || isNaN(createdAt)) throw new Error('GitHub search snapshot created_at invalid');
  var ageHours = (Date.now() - createdAt.getTime()) / 3600000;
  if (ageHours > 24) throw new Error('GitHub search snapshot stale: ' + Math.round(ageHours) + 'h');
  var result = sellmonitorGithubPublishRuntimeSearch_(cfg.cabinet, snap);
  if (!result || result.ok !== true || Number(result.search_rows || 0) <= 0) {
    throw new Error('GitHub search snapshot materialization failed: ' + sellmonitorGithubJson_(result || {}));
  }
  result.github_source = url.replace(/\?t=.*/, '');
  result.age_hours = Math.round(ageHours * 10) / 10;
  result.store_id = storeId;
  return result;
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
  if (!legacy && storeId === 'sanych_wb') {
    return {ok:true,queued:false,storeId:storeId,source:'existing_sanych_search',reason:'Preserve proven Sanych search lane; GitHub snapshot lane targets AIR/Хозяюшка'};
  }
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
      : 'Automatic factual search snapshot via GitHub-derived WB facts (Analytics or public SERP); monitor chains after success'
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
    var x=g[q] || (g[q]={normQuery:q,views:0,viewsComplete:true,spendComplete:true,ordersComplete:true,clicks:0,atbs:0,orders:0,shks:0,spend:0,bidKopecks:Number(r.clusterBidKopecks || r.cluster_bid_kopecks || 0),state:r.clusterState || r.cluster_state || ''});
    if(r.views==null||r.views==='')x.viewsComplete=false;
    else x.views+=Number(r.views);
    if(r.orders==null||r.orders==='')x.ordersComplete=false;
    else x.orders+=Number(r.orders);
    var spendValue=r.spend!=null&&r.spend!==''?r.spend:(r.spendRub!=null&&r.spendRub!==''?r.spendRub:r.spend_rub);
    if(spendValue==null||spendValue==='')x.spendComplete=false;
    else x.spend+=Number(spendValue);
    x.clicks+=Number(r.clicks||0);x.atbs+=Number(r.atbs||0);x.shks+=Number(r.shks||0);
    if(Number(r.clusterBidKopecks||r.cluster_bid_kopecks||0)>0)x.bidKopecks=Number(r.clusterBidKopecks||r.cluster_bid_kopecks);
    if(r.clusterState||r.cluster_state)x.state=r.clusterState||r.cluster_state;
  });
  var base=Math.max(0,Number(baseContribution||0)), safeCpa=base>0?base*0.8:0, result=[];
  Object.keys(g).forEach(function(q){
    var x=g[q], ctr=x.viewsComplete&&x.views>0?x.clicks/x.views*100:null, cpc=x.spendComplete&&x.clicks>0?x.spend/x.clicks:null;
    var cr=x.ordersComplete&&x.clicks>0?x.orders/x.clicks:null, cpa=x.spendComplete&&x.ordersComplete&&x.orders>0?x.spend/x.orders:null;
    var safeCpc=safeCpa>0&&cr!=null?safeCpa*cr:null, safeCpm=safeCpc!=null&&ctr!=null?safeCpc*(ctr/100)*1000:null;
    var action='НАБЛЮДАТЬ', reason='Недостаточно данных для жёсткого решения';
    if(!x.spendComplete||!x.ordersComplete){
      action='ТРЕБУЕТ ДАННЫХ';reason='Недоступны точные расходы или заказы рекламного кластера';
    }else if(x.orders===0 && x.clicks>=20 && x.spend>=Math.max(250,base*0.5)){
      action='ИСКЛЮЧИТЬ / МИНУСОВАТЬ';reason='Достаточный объём кликов и расход без заказов';
    }else if(x.orders>0 && safeCpa>0 && cpa>safeCpa*1.15){
      action='СНИЗИТЬ СТАВКУ';reason='CPA выше безопасного рекламного бюджета на заказ';
    }else if(x.orders>=2 && safeCpa>0 && cpa<=safeCpa){
      action='ОСТАВИТЬ / МАСШТАБИРОВАТЬ';reason='CPA укладывается в базовую экономику товара';
    }else if(x.orders>0 && (!safeCpa || cpa<=base)){
      action='ОСТАВИТЬ';reason='Есть подтверждённые заказы; blanket-отключение не требуется';
    }
    var ratio=(cpc!=null&&cpc>0&&safeCpc!=null&&safeCpc>0)?Math.min(1,safeCpc/cpc):0;
    var targetBid=x.bidKopecks>0&&ratio>0?Math.max(1,Math.floor(x.bidKopecks*ratio)):0;
    result.push({
      normQuery:q,views:x.viewsComplete?x.views:null,clicks:x.clicks,atbs:x.atbs,orders:x.ordersComplete?x.orders:null,shks:x.shks,spend:x.spendComplete?x.spend:null,
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
            views:x.views==null?null:Number(x.views),clicks:Number(x.clicks||0),atbs:Number(x.atbs||0),orders:x.orders==null?null:Number(x.orders),shks:Number(x.shks||0),
            ctr:x.ctr==null?null:Number(x.ctr),cpc:x.cpc==null?null:Number(x.cpc),cpm:x.cpm==null?null:Number(x.cpm),avgPos:x.avgPos==null&&x.avg_pos==null?null:Number(x.avgPos==null?x.avg_pos:x.avgPos),
            spend:x.spend==null?null:Number(x.spend),clusterBidKopecks:Number(bm[q]||0),clusterState:String(sm[q]||''),
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
        auth.storeId,from,to,p.advertId,p.nmId,p.sellerArticle,p.campaignName,x.normQuery,org.position||'',org.frequency||'',x.spend,x.views,x.clicks,x.atbs,x.orders,x.shks,x.ctr,x.cpc,x.cr*100,x.cpa==null?'':x.cpa,x.baseContribution,x.safeCpa,x.safeCpc,x.safeCpm,x.currentBidKopecks,x.targetBidKopecks,x.state,action,reason,'FACTUAL_WB_CLUSTER; ORGANIC_POSITION='+ (Number(org.position||0)>0?'OBSERVED':'UNAVAILABLE') +'; ECONOMICS='+ (base>0?'DERIVED':'INCOMPLETE'),loaded
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
  var from=1200,to=Math.min(2023,q.getMaxRows()),active=false,factualAdsActive=false;
  if(to>=from)q.getRange(from,1,to-from+1,8).getValues().forEach(function(r){var st=String(r[4]||''),id=String(r[0]||''),spec={};try{spec=JSON.parse(String(r[3]||'{}'));}catch(e){}var file=String(spec.file||''),isActive=['PENDING','NEW','RUNNING','SCHEDULED'].indexOf(st)>=0;if(!isActive)return;if(['__central_ads_clusters__','wb_ads_cluster_intelligence_v1'].indexOf(file)>=0)active=true;if(file==='wb_ads_bulk_ingest_v167'||/^ADS-VERIFY-/.test(id))factualAdsActive=true;});
  if(factualAdsActive)return{ok:true,enabled:true,queued:false,active:active,skipped:true,reason:'FACTUAL_ADS_PRIORITY'};
  if(!due||active)return{ok:true,enabled:true,queued:false,active:active,lastRefreshAtMs:last||null,hours:hours};
  var slot=sellmonitorGithubQueueSlot_(ss,q);if(!slot)return{ok:false,reason:'no queue slot for ads cluster refresh'};
  var storeId=sellmonitorGithubSetting_(ss,'ACTIVE_STORE_ID',''),stamp=Utilities.formatDate(new Date(),ss.getSpreadsheetTimeZone(),'yyyyMMdd-HHmmss');
  q.getRange(slot,1,1,8).setValues([['AUTO-ADS-CLUSTERS-'+storeId+'-'+stamp,new Date(),'RUN_REMOTE',JSON.stringify({file:'wb_ads_cluster_intelligence_v1',entrypoint:'REMOTE_MAIN',payload:{storeId:storeId,days:14}}),'PENDING','','','Automatic WB search-cluster spend/bids/economics refresh']]);
  SpreadsheetApp.flush();
  return{ok:true,enabled:true,queued:true,row:slot,hours:hours};
}



function sellmonitorGithubCoreBacklog_(q, ss) {
  var from=1200,to=Math.min(2023,q.getMaxRows()),now=Date.now(),count=0,files=[];
  if(to>=from)q.getRange(from,1,to-from+1,8).getValues().forEach(function(r){
    var st=String(r[4]||'');
    if(['PENDING','NEW','RUNNING','SCHEDULED'].indexOf(st)<0)return;
    var due=r[1] instanceof Date?r[1].getTime():Date.parse(String(r[1]||''));
    if(st==='SCHEDULED'&&isFinite(due)&&due>now)return;
    var id=String(r[0]||''),spec={};try{spec=JSON.parse(String(r[3]||'{}'));}catch(e){}
    var f=String(spec.file||'');
    var critical=/^D1-/.test(id)||/^D1-GAP-/.test(id)||[
      'daily_prevday_close_v268','daily_prevday_gate_v269','daily_prevday_gate_v312','d1_gap_watchdog_v310',
      'mcp_inner_call_v183','inner_product_harvest_fast_v311','inner_harvest_to_raw_v217','finance_period_normalize_incremental_v227',
      'inner_live_today_sync_v260','orders_live_fast_v261',
      'traffic_refresh_enqueue_v256','traffic_refresh_gate_v257','traffic_daily_sync_v255','wb_ads_bulk_ingest_v167',
      'rnp_finance_columns_fast_v307','rnp_latest_period_inner_sync_v224','rnp_period_headers_sync_v198','rnp_store_aggregate_v231',
      'finance_daily_coverage_guard_v226','coverage_freshness_sync_v161','connection_status_sync_v192'
    ].indexOf(f)>=0;
    if(critical){count++;if(files.indexOf(f)<0)files.push(f);}
  });

  var coverageGaps=0;
  if(ss){
    var cov=ss.getSheetByName('00_Покрытие_фактов');
    if(cov){
      var v=Number(cov.getRange('H2').getValue());
      coverageGaps=isFinite(v)?Math.max(0,v):0;
      if(coverageGaps){count+=coverageGaps;if(files.indexOf('COVERAGE_GAP')<0)files.push('COVERAGE_GAP');}
    }
  }
  return{count:count,activeCount:Math.max(0,count-coverageGaps),files:files,coverageGaps:coverageGaps};
}

function sellmonitorGithubSyncCoreQc_(ss,storeId) {
  var qc=ss.getSheetByName('13_Контроль_качества'),fin=ss.getSheetByName('84_Финансы_периоды'),ads=ss.getSheetByName('84_Статус_реклама_WB'),q=ss.getSheetByName('97_Управление');
  if(!qc||!fin||!q)return{ok:true,skipped:true};
  var tz=ss.getSpreadsheetTimeZone();
  function dk(v){if(v instanceof Date)return Utilities.formatDate(v,tz,'yyyy-MM-dd');var s=String(v||'');if(/^\d{4}-\d{2}-\d{2}/.test(s))return s.slice(0,10);var m=s.match(/^(\d{1,2})\.(\d{1,2})\.(\d{4})/);return m?m[3]+'-'+('0'+m[2]).slice(-2)+'-'+('0'+m[1]).slice(-2):'';}
  var days={},n=Math.max(0,fin.getLastRow()-1);
  if(n){var core=fin.getRange(2,1,n,7).getValues(),trust=fin.getRange(2,39,n,1).getDisplayValues();for(var i=0;i<n;i++){if(String(core[i][0]||'')!==storeId||String(trust[i][0]||'')!=='FACTUAL_INNER_ANALYTICS'||Number(core[i][5]||0)!==1)continue;var f=dk(core[i][3]),t=dk(core[i][4]),nm=String(core[i][6]||'');if(!f||!nm)continue;var nx=new Date(f+'T12:00:00');nx.setDate(nx.getDate()+1);if(dk(nx)!==t)continue;(days[f]||(days[f]={}))[nm]=1;}}
  var dkeys=Object.keys(days).sort(),last=dkeys.length?dkeys[dkeys.length-1]:'',trustedRows=0;dkeys.forEach(function(d){trustedRows+=Object.keys(days[d]).length;});
  var adDays={},lastAds='';if(ads&&ads.getLastRow()>1){ads.getRange(2,1,ads.getLastRow()-1,4).getValues().forEach(function(r){if(String(r[0]||'')!==storeId)return;var st=String(r[2]||''),d=dk(r[1]);if(d&&/ТОЧНО WB ADS/.test(st))adDays[d]=1;});var ak=Object.keys(adDays).sort();lastAds=ak.length?ak[ak.length-1]:'';}
  var backlog=sellmonitorGithubCoreBacklog_(q,ss);
  var labels=qc.getRange(1,1,Math.max(1,qc.getLastRow()),1).getDisplayValues();
  function setRow(label,value,status,meaning){for(var i=0;i<labels.length;i++){if(String(labels[i][0]||'')===label){qc.getRange(i+1,2,1,3).setValues([[value,status,meaning]]);return;}}}
  setRow('RAW / normalized finance',trustedRows+' trusted SKU-day · last '+(last||'NO_DATA'),last?'OK · FACTUAL_INNER_ANALYTICS':'БЛОКЕР','Trusted daily finance only; partial periods are excluded.');
  var y=new Date();y.setDate(y.getDate()-1);var yk=Utilities.formatDate(y,tz,'yyyy-MM-dd');
  setRow('Последний trusted Inner день',last||'NO_DATA',last===yk?'OK':'ОЖИДАЕТ ЗАКРЫТИЯ INNER','Actual latest FACTUAL_INNER_ANALYTICS daily period.');
  setRow('Активная очередь CORE',backlog.activeCount+' active · '+backlog.coverageGaps+' coverage gaps',backlog.activeCount?'В РАБОТЕ':(backlog.coverageGaps?'ОЖИДАЕТ ИСТОЧНИК':'OK'),'Active factual jobs are distinct from source-lag coverage gaps; derived work waits only for active core.');
  if(ads)setRow('WB Ads factual source',Object.keys(adDays).length+' exact days · до '+(lastAds||'NO_DATA'),lastAds?'OK · FACTUAL_WB_ADS':'НЕТ ФАКТА','Official WB Promotion factual status.');
  return{ok:true,lastTrusted:last,lastAds:lastAds,coreBacklog:backlog.count};
}

function sellmonitorGithubCaptureHourlyLive_(ss,storeId) {
  var live=ss.getSheetByName('84_Live_сегодня');
  if(!live||live.getLastRow()<2)return{ok:true,skipped:true,reason:'live sheet empty'};
  var tz=ss.getSpreadsheetTimeZone(),now=new Date(),day=Utilities.formatDate(now,tz,'yyyy-MM-dd'),hour=Number(Utilities.formatDate(now,tz,'H')),hourKey=day+' '+('0'+hour).slice(-2)+':00';
  function dk(v){if(v instanceof Date)return Utilities.formatDate(v,tz,'yyyy-MM-dd');var s=String(v||'');return /^\d{4}-\d{2}-\d{2}/.test(s)?s.slice(0,10):'';}
  function N(v){if(v===''||v==null)return 0;var n=Number(v);return isFinite(n)?n:0;}
  var a=live.getRange(2,1,live.getLastRow()-1,22).getValues(),agg={rows:0,orders:0,orderRub:0,paid:0,sales:0,returns:0,revenue:0,innerAd:0,prime:0,expenses:0,profit:0,payout:0,stock:0},trust={},fresh='';
  a.forEach(function(r){if(String(r[1]||'')!==storeId||dk(r[0])!==day)return;agg.rows++;agg.orders+=N(r[4]);agg.orderRub+=N(r[5]);agg.paid+=N(r[6]);agg.sales+=N(r[7]);agg.returns+=N(r[8]);agg.revenue+=N(r[9]);agg.innerAd+=N(r[10]);agg.prime+=N(r[11]);agg.expenses+=N(r[12]);agg.profit+=N(r[13]);agg.payout+=N(r[16]);agg.stock+=N(r[17]);trust[String(r[19]||'')]=1;try{var j=JSON.parse(String(r[21]||'{}')),x=j&&j.accounts&&j.accounts[0]&&j.accounts[0].dataIsUpTo;if(x&&String(x)>fresh)fresh=String(x);}catch(e){}});
  if(!agg.rows)return{ok:true,skipped:true,reason:'no current store live rows'};

  var ad={available:false,views:0,clicks:0,spend:0,atbs:0,orders:0,shks:0,salesRub:0},ar=ss.getSheetByName('90_RAW_ads_traffic');
  if(ar&&ar.getLastRow()>1){var av=ar.getRange(2,1,ar.getLastRow()-1,20).getValues(),ded={};av.forEach(function(r){if(String(r[0]||'')!==storeId||dk(r[1])!==day||String(r[16]||'')!=='FACTUAL_WB_ADS')return;var k=String(r[2]||'')+'|'+String(r[3]||''),tm=r[17] instanceof Date?r[17].getTime():Date.parse(String(r[17]||''));if(!ded[k]||tm>=ded[k].tm)ded[k]={tm:isFinite(tm)?tm:0,r:r};});Object.keys(ded).forEach(function(k){var r=ded[k].r;ad.available=true;ad.views+=N(r[5]);ad.clicks+=N(r[6]);ad.spend+=N(r[9]);ad.atbs+=N(r[10]);ad.orders+=N(r[11]);ad.shks+=N(r[13]);ad.salesRub+=N(r[14]);});}

  var tr={available:false,open:0,cart:0,orders:0,organic:0},ts=ss.getSheetByName('84_Трафик_дни');
  if(ts&&ts.getLastRow()>1){ts.getRange(2,1,ts.getLastRow()-1,20).getValues().forEach(function(r){if(String(r[0]||'')!==storeId||dk(r[1])!==day)return;var src=String(r[4]||''),zone=String(r[5]||'');if(src==='TOTAL'&&zone==='ALL_ENTRYPOINTS'){tr.available=true;tr.open+=N(r[8]);tr.cart+=N(r[9]);tr.orders+=N(r[10]);}else if(src==='ORGANIC_DERIVED'&&zone==='ALL_ENTRYPOINTS'){tr.organic+=N(r[8]);}});}

  var headers=['hour_key','snapshot_at','date','hour','store_id','live_rows','orders_count','orders_sum_rub','paid_orders_count','sales_count','returns_count','revenue_rub','inner_ad_rub','prime_cost_rub','expenses_rub','profit_rub','payout_rub','stock_count_sum','live_trust','source_data_is_up_to','ads_fact_available','ads_views','ads_clicks','ads_spend_rub','ads_atbs','ads_orders','ads_shks','ads_sales_rub','traffic_fact_available','traffic_open','traffic_cart','traffic_orders','organic_clicks','baseline_same_hour_n','baseline_orders_median','baseline_revenue_median','orders_vs_baseline_pct','revenue_vs_baseline_pct','alert_level','alert_reason'];
  var sh=sellmonitorGithubEnsureSheet_(ss,'84_Live_часы',headers),last=sh.getLastRow(),hist=last>1?sh.getRange(2,1,last-1,headers.length).getValues():[];
  var same=[],prev=null,rowIndex=0;
  hist.forEach(function(r,i){if(String(r[4]||'')!==storeId)return;if(String(r[0]||'')===hourKey)rowIndex=i+2;var rd=dk(r[2]),rh=Number(r[3]);if(rd<day&&rh===hour)same.push(r);if(rd===day&&rh===hour-1)prev=r;});
  same=same.slice(-14);
  function med(vals){vals=vals.filter(function(x){return isFinite(Number(x));}).map(Number).sort(function(a,b){return a-b;});if(!vals.length)return'';var m=Math.floor(vals.length/2);return vals.length%2?vals[m]:(vals[m-1]+vals[m])/2;}
  var bOrd=med(same.map(function(r){return r[6];})),bRev=med(same.map(function(r){return r[11];})),op=bOrd!==''&&bOrd>0?(agg.orders/bOrd-1):'',rp=bRev!==''&&bRev>0?(agg.revenue/bRev-1):'',level=same.length?'OK':'BASELINE_PENDING',reasons=[];
  if(prev&&N(prev[6])>agg.orders){level='DATA_ANOMALY';reasons.push('cumulative orders decreased vs previous hour');}
  if(hour>=8&&same.length){if(bOrd>=5&&op!==''&&op<=-0.40){level='CRITICAL';reasons.push('orders '+Math.round(op*100)+'% vs same-hour median');}else if(bOrd>=5&&op!==''&&op<=-0.25&&level!=='CRITICAL'){level='WARNING';reasons.push('orders '+Math.round(op*100)+'% vs same-hour median');}if(bRev>=5000&&rp!==''&&rp<=-0.40){level='CRITICAL';reasons.push('revenue '+Math.round(rp*100)+'% vs same-hour median');}else if(bRev>=5000&&rp!==''&&rp<=-0.25&&level==='OK'){level='WARNING';reasons.push('revenue '+Math.round(rp*100)+'% vs same-hour median');}}
  var row=[hourKey,now,new Date(day+'T12:00:00'),hour,storeId,agg.rows,agg.orders,agg.orderRub,agg.paid,agg.sales,agg.returns,agg.revenue,agg.innerAd,agg.prime,agg.expenses,agg.profit,agg.payout,agg.stock,Object.keys(trust).join(','),fresh,ad.available,ad.available?ad.views:'',ad.available?ad.clicks:'',ad.available?ad.spend:'',ad.available?ad.atbs:'',ad.available?ad.orders:'',ad.available?ad.shks:'',ad.available?ad.salesRub:'',tr.available,tr.available?tr.open:'',tr.available?tr.cart:'',tr.available?tr.orders:'',tr.available?tr.organic:'',same.length,bOrd,bRev,op,rp,level,reasons.join('; ')];
  if(rowIndex)sh.getRange(rowIndex,1,1,row.length).setValues([row]);else sh.getRange(Math.max(2,sh.getLastRow()+1),1,1,row.length).setValues([row]);
  sh.setFrozenRows(1);sh.getRange('B:B').setNumberFormat('dd.MM.yyyy HH:mm');sh.getRange('C:C').setNumberFormat('dd.MM.yyyy');sh.getRange('AK:AL').setNumberFormat('0.0%');

  if(['WARNING','CRITICAL','DATA_ANOMALY'].indexOf(level)>=0){var ah=['alert_key','created_at','store_id','date','hour','level','reason','orders','revenue','ads_spend','baseline_n','ack'];var al=sellmonitorGithubEnsureSheet_(ss,'84_Live_алерты',ah),key=storeId+'|'+hourKey,found=0;if(al.getLastRow()>1){var keys=al.getRange(2,1,al.getLastRow()-1,1).getDisplayValues();for(var i=0;i<keys.length;i++)if(String(keys[i][0])===key){found=i+2;break;}}var rr=[key,now,storeId,new Date(day+'T12:00:00'),hour,level,reasons.join('; '),agg.orders,agg.revenue,ad.available?ad.spend:'',same.length,false];if(found)al.getRange(found,1,1,rr.length).setValues([rr]);else al.getRange(Math.max(2,al.getLastRow()+1),1,1,rr.length).setValues([rr]);}
  SpreadsheetApp.flush();
  return{ok:true,storeId:storeId,hourKey:hourKey,orders:agg.orders,revenue:agg.revenue,profit:agg.profit,adsFact:ad.available,trafficFact:tr.available,baselineN:same.length,alertLevel:level,alertReason:reasons.join('; ')};
}

function sellmonitorGithubClientStep_(label, fn) {
  try {
    return fn();
  } catch (e) {
    var message = String(e && e.message ? e.message : e);
    if (message.indexOf('CLIENT_STEP[') === 0) throw e;
    throw new Error('CLIENT_STEP[' + label + ']: ' + message);
  }
}

function sellmonitorGithubGridTargetRows_(lastRow, minimumRows, reserveRows) {
  var last = Math.max(0, Number(lastRow) || 0);
  var minimum = Math.max(1, Number(minimumRows) || 1);
  var reserve = Math.max(0, Number(reserveRows) || 0);
  return Math.max(minimum, last + reserve);
}

function sellmonitorGithubRepairTrafficCapacity_(ss) {
  var storeId = String(sellmonitorGithubSetting_(ss, 'ACTIVE_STORE_ID', '') || '').trim();
  var stores = {sanych_wb:true, air_wb:true, hozyayushka_wb:true};
  if (!stores[storeId]) return {ok:true, skipped:true, storeId:storeId, reason:'not_target'};
  // Sanych's historical workbook exceeds the editable Sheets document limit.
  // Its traffic is now written to bounded monthly partitions; do not let a
  // best-effort grid trim block unrelated queued work or risk altering history.
  if (storeId === 'sanych_wb') {
    return {ok:true, skipped:true, storeId:storeId, reason:'external_monthly_partitions'};
  }
  var raw = ss.getSheetByName('90_RAW_traffic');
  var daily = ss.getSheetByName('84_Трафик_дни');
  var view = ss.getSheetByName('14_Трафик');
  var codeSheet = ss.getSheetByName('97_Код');
  var backup = ss.getSheetByName('97_Резерв');
  if (!raw || !daily || !view || !codeSheet || !backup) {
    throw new Error('Traffic capacity repair: required sheets missing for ' + storeId);
  }

  var codeRows = codeSheet.getRange(2, 1, Math.max(1, codeSheet.getLastRow() - 1), 5).getValues();
  var sourceRow = 0, source = '', active = false;
  for (var i = 0; i < codeRows.length; i++) {
    if (String(codeRows[i][0] || '').trim() !== 'traffic_daily_sync_v255') continue;
    sourceRow = i + 2;
    source = String(codeRows[i][3] || '');
    active = codeRows[i][4] === true || String(codeRows[i][4]).toUpperCase() === 'TRUE';
    break;
  }
  if (!sourceRow || !active || !source) {
    return {ok:true, skipped:true, storeId:storeId, reason:'active traffic_daily_sync_v255 not found'};
  }

  var dailyPatched = source.indexOf('var dailyClear=Math.max(1,daily.getLastRow()-1,norm.length)') >= 0 &&
    source.indexOf('var dailyNeed=norm.length+1') >= 0;
  var viewPatched = source.indexOf('var viewClear=Math.max(1,view.getLastRow()-2,vr.length)') >= 0 &&
    source.indexOf('var viewNeed=vr.length+2') >= 0;
  var patchStatus = dailyPatched && viewPatched ? 'already_patched' : 'not_needed';
  var backupRow = null;
  if (!dailyPatched || !viewPatched) {
    var dailyOld = 'daily.getRange(2,1,Math.max(1,daily.getMaxRows()-1),20).clearContent();if(norm.length)daily.getRange(2,1,norm.length,20).setValues(norm);';
    var dailyNew = 'var dailyClear=Math.max(1,daily.getLastRow()-1,norm.length);daily.getRange(2,1,dailyClear,20).clearContent();if(norm.length){var dailyNeed=norm.length+1;if(dailyNeed>daily.getMaxRows())daily.insertRowsAfter(daily.getMaxRows(),dailyNeed-daily.getMaxRows());daily.getRange(2,1,norm.length,20).setValues(norm);}';
    var viewOld = 'view.getRange(3,1,Math.max(1,view.getMaxRows()-2),30).clearContent();if(vr.length)view.getRange(3,1,vr.length,30).setValues(vr);';
    var viewNew = 'var viewClear=Math.max(1,view.getLastRow()-2,vr.length);view.getRange(3,1,viewClear,30).clearContent();if(vr.length){var viewNeed=vr.length+2;if(viewNeed>view.getMaxRows())view.insertRowsAfter(view.getMaxRows(),viewNeed-view.getMaxRows());view.getRange(3,1,vr.length,30).setValues(vr);}';
    var marker = "if(!sid||!cid)throw new Error('storeId/clientId missing');";
    if ((dailyPatched || source.split(dailyOld).length === 2) &&
        (viewPatched || source.split(viewOld).length === 2) &&
        source.split(marker).length === 2) {
      var next = source.replace(marker, marker + "if(p.dryRun===true)return{ok:true,dryRun:true,storeId:sid,clientId:cid,grid:{raw:{rows:raw.getMaxRows(),last:raw.getLastRow(),cols:raw.getMaxColumns()},daily:{rows:daily.getMaxRows(),last:daily.getLastRow(),cols:daily.getMaxColumns()},view:{rows:view.getMaxRows(),last:view.getLastRow(),cols:view.getMaxColumns()}}};");
      if (!dailyPatched) next = next.replace(dailyOld, dailyNew);
      if (!viewPatched) next = next.replace(viewOld, viewNew);
      backupRow = backup.getLastRow() + 1;
      if (backupRow > backup.getMaxRows()) {
        backup.insertRowsAfter(backup.getMaxRows(), Math.max(100, backupRow - backup.getMaxRows()));
      }
      backup.getRange(backupRow, 1, 1, 6).setValues([[
        new Date(), 'sheet:' + ss.getId(), 'traffic_daily_sync_v255', 'SERVER_JS', '1', source
      ]]);
      SpreadsheetApp.flush();
      codeSheet.getRange(sourceRow, 4).setValue(next);
      SpreadsheetApp.flush();
      source = next;
      patchStatus = 'patched_and_backed_up';
    } else {
      return {
        ok:true, skipped:true, storeId:storeId, sourceRow:sourceRow,
        reason:'traffic source differs from the reviewed writer; no rows or grid cells changed'
      };
    }
  }

  // Only trailing blank rows are removed. getLastRow includes formulas and values;
  // the writer expands the sheets before later writes, so collection remains open-ended.
  function trim_(sheet) {
    var before = {rows:sheet.getMaxRows(), last:sheet.getLastRow()};
    var target = sellmonitorGithubGridTargetRows_(before.last, 1000, 500);
    if (target < before.rows) sheet.deleteRows(target + 1, before.rows - target);
    return {
      before:before,
      after:{rows:sheet.getMaxRows(), last:sheet.getLastRow()},
      rowsRemoved:Math.max(0, before.rows - sheet.getMaxRows()),
      reserveRows:Math.max(0, sheet.getMaxRows() - sheet.getLastRow())
    };
  }
  var rawResult = trim_(raw);
  var dailyResult = trim_(daily);
  var viewResult = trim_(view);
  SpreadsheetApp.flush();
  return {
    ok:true, storeId:storeId, sourceRow:sourceRow, patchStatus:patchStatus, backupRow:backupRow,
    raw:rawResult, daily:dailyResult, view:viewResult,
    writer:'preserve every populated row; trim only trailing blank grid rows; grow before next write'
  };
}

function sellmonitorGithubExternalDataPlaneInfo_(storeId) {
  if (String(storeId || '').trim() !== 'sanych_wb') return null;
  return {
    status:'EXTERNAL_PARTITIONS_PARTIAL',
    state:'ACTIVE',
    qcStatus:'BLOCKED',
    error:'Legacy workbook exceeds Sheets write capacity; client-queue writes are paused. Traffic and finance are partitioned; remaining daily datasets still require migration.'
  };
}

function sellmonitorGithubProcessClient_(spreadsheetId) {
  var authState = sellmonitorGithubClientStep_('client_auth', function() {
    return sellmonitorGithubEnsureClientAuth_(spreadsheetId);
  });
  var ss = sellmonitorGithubClientStep_('open_client', function() {
    return SpreadsheetApp.openById(spreadsheetId);
  });
  var q = ss.getSheetByName('97_Управление');
  var code = ss.getSheetByName('97_Код');
  if (!q || !code) throw new Error('Client missing 97_Управление/97_Код: ' + spreadsheetId);

  var trafficCapacity = sellmonitorGithubClientStep_('traffic_capacity_repair', function() {
    return sellmonitorGithubRepairTrafficCapacity_(ss);
  });

  sellmonitorGithubClientStep_('ui_onboarding', function() {
    return sellmonitorGithubEnsureUiOnboarding_(ss, q);
  });
  var staleRepaired = sellmonitorGithubClientStep_('stale_queue_repair', function() {
    return sellmonitorGithubRepairStaleRunning_(q);
  });
  var searchLane = sellmonitorGithubClientStep_('search_lane_maintenance', function() {
    return sellmonitorGithubMaintainSearchLane_(ss, q);
  });
  var coreRefresh = sellmonitorGithubClientStep_('core_refresh_schedule', function() {
    return sellmonitorGithubEnsureCoreRefresh_(ss, q);
  });
  var operational = sellmonitorGithubClientStep_('operational_history', function() {
    return sellmonitorGithubEnsureOperationalHistory_(ss, q);
  });
  var qcSync = sellmonitorGithubClientStep_('core_quality_sync', function() {
    return sellmonitorGithubSyncCoreQc_(ss, String(sellmonitorGithubSetting_(ss,'ACTIVE_STORE_ID','')));
  });
  var coreBacklog = sellmonitorGithubClientStep_('core_backlog', function() {
    return sellmonitorGithubCoreBacklog_(q,ss);
  });
  var searchRefresh = coreBacklog.activeCount ? {ok:true,queued:false,skipped:true,reason:'ACTIVE_CORE_BACKLOG'} :
    sellmonitorGithubClientStep_('search_refresh_schedule', function() {
      return sellmonitorGithubEnsureSearchRefresh_(ss, q);
    });
  var adsClusterRefresh = coreBacklog.activeCount ? {ok:true,queued:false,skipped:true,reason:'ACTIVE_CORE_BACKLOG'} :
    sellmonitorGithubClientStep_('ads_cluster_refresh_schedule', function() {
      return sellmonitorGithubEnsureAdsClusterRefresh_(ss, q);
    });

  var processed = 0, last = null;
  while (processed < SMC_GH.MAX_COMMANDS_PER_CLIENT) {
    var row = sellmonitorGithubClientStep_('next_queue_row', function() {
      return sellmonitorGithubNextPendingRow_(q);
    });
    if (!row) break;
    last = sellmonitorGithubClientStep_('queue_row_' + row, function() {
      return sellmonitorGithubExecuteQueueRow_(ss, q, code, row);
    });
    processed++;
  }

  var ready = false;
  var connect = ss.getSheetByName('00_API_Подключение');
  if (connect) {
    var values = sellmonitorGithubClientStep_('read_connection_status', function() {
      return connect.getRange('A17:B25').getDisplayValues();
    });
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
    ready = allOk && values.length > 0 && sellmonitorGithubClientStep_('recent_exact_ready', function() {
      return sellmonitorGithubRecentExactReady_(ss, 7);
    });
  }

  return {ok: true, spreadsheetId: spreadsheetId, auth: authState, trafficCapacity: trafficCapacity, processedCommands: processed, staleRepaired: staleRepaired, searchLane: searchLane, coreRefresh: coreRefresh, operational: operational, qcSync: qcSync, coreBacklog: coreBacklog, searchRefresh: searchRefresh, adsClusterRefresh: adsClusterRefresh, last: last, ready: ready};
}

function sellmonitorGithubRepairStaleRunning_(q) {
  var from = 1200, to = Math.min(2023, q.getMaxRows());
  if (to < from) return 0;
  var vals = q.getRange(from, 1, to - from + 1, 8).getValues();
  var now = new Date(), repaired = 0, retried = 0;

  // A new central tick owns the same ScriptLock as the previous tick. Therefore
  // a pre-existing RUNNING row older than a short grace period cannot still be
  // executing in the central worker; it is an orphan left by timeout/termination.
  var retrySafe = {
    mcp_inner_call_v183:1,
    inner_product_harvest_fast_v311:1,
    inner_harvest_to_raw_v217:1,
    finance_period_normalize_incremental_v227:1,
    inner_live_today_sync_v260:1,
    orders_live_fast_v261:1,
    traffic_daily_sync_v255:1,
    wb_ads_bulk_ingest_v167:1,
    rnp_enrichment_sync_v305:1,
    rnp_snapshot_history_v304:1,
    rnp_finance_columns_fast_v307:1,
    rnp_latest_period_inner_sync_v224:1,
    rnp_store_aggregate_v231:1,
    search_position_monitor_sync_v238:1,
    search_intelligence_sync_v240:1,
    search_traffic_intelligence_v271:1,
    search_intelligence_qc_v242:1,
    wb_ads_cluster_intelligence_v1:1,
    __central_github_search__:1,
    __central_wb_search__:1,
    __central_ads_clusters__:1
  };

  for (var i = 0; i < vals.length; i++) {
    if (String(vals[i][4] || '') !== 'RUNNING') continue;
    var spec = {};
    try { spec = JSON.parse(String(vals[i][3] || '{}')); } catch (e) {}
    var file = String(spec.file || '');
    var started = vals[i][5] instanceof Date ? vals[i][5] :
      (vals[i][1] instanceof Date ? vals[i][1] : new Date(vals[i][5] || vals[i][1]));
    var age = started instanceof Date && !isNaN(started) ? (now.getTime() - started.getTime()) / 60000 : 999999;
    if (age < 2) continue;

    var row = from + i;
    if (retrySafe[file]) {
      q.getRange(row, 5, 1, 4).setValues([[
        'PENDING','',new Date(),
        'Recovered orphan RUNNING after '+Math.round(age)+'m; same canonical id requeued, no duplicate task.'
      ]]);
      retried++;
    } else {
      q.getRange(row, 5, 1, 4).setValues([[
        'CANCELLED_INTERRUPTED_WORKER','',new Date(),
        'Interrupted RUNNING recovered after '+Math.round(age)+'m; non-idempotent job not auto-replayed.'
      ]]);
    }
    repaired++;
  }
  if (repaired) SpreadsheetApp.flush();
  return {repaired:repaired,retriedSameId:retried};
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

  // D-1 coverage is core work. Keep exactly one watchdog alive even when a
  // previous repair stopped cleanly on PARTIAL_INNER_ANALYTICS.
  var d1At = Number(props.getProperty('SMC_D1_WATCHDOG_AT_MS') || 0);
  if ((!d1At || now-d1At >= 15*60000) && !active.d1_gap_watchdog_v310) {
    var d1r=queue_('D1-GAP','d1_gap_watchdog_v310',{storeId:storeId,lookback:7});
    if (d1r) { queued.d1Watchdog=d1r; props.setProperty('SMC_D1_WATCHDOG_AT_MS',String(now)); }
  }

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
    var er=queue_('RNP-ENRICH','rnp_enrichment_sync_v305',{storeId:storeId,sheetIndex:0,offset:0,chunkRows:150});
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

    // Authentication/onboarding must outrank every data job; without it no factual source can recover.
    if (file === 'client_onboard_stage1_v1') return -4;
    // Emergency/current facts: never wait behind historical RNP, ads or SEO.
    if (/^ADS-VERIFY-/.test(id)) return -3;
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
      'finance_period_normalize_incremental_v227'
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
      'connection_status_sync_v192'
    ].indexOf(file) >= 0) return 1;
    if (/^orders_/.test(file) || file === 'k2_inventory_pool_sync_v246') return 1;

    // P2: current factual advertising/traffic + snapshot history. Materializing an
    // already-fetched search snapshot into the store-aware history is local and cheap.
    if (['traffic_daily_sync_v255','wb_ads_bulk_ingest_v167','traffic_refresh_enqueue_v256','traffic_refresh_gate_v257','rnp_snapshot_history_v304','search_position_monitor_sync_v238'].indexOf(file) >= 0) return 2;
    if (/^(ads_|calculator_ads_|quality_ads_|traffic_|wb_ads_)/.test(file) && file !== 'wb_ads_cluster_intelligence_v1') return 2;

    // P3: historical enrichment is useful but must never delay live/D-1.
    if (file === 'rnp_enrichment_sync_v305') return 3;
    if (file === 'store_autopilot_v207') return 3;

    // P5: search snapshots/intelligence are derived monitoring work.
    if ([
      '__central_github_search__',
      '__central_wb_search__',
      'search_snapshot_config_v235',
      'search_intelligence_sync_v240',
      'search_traffic_intelligence_v271',
      'search_intelligence_qc_v242'
    ].indexOf(file) >= 0) return 5;

    // P6: ads-cluster/competitor/cosmetic derived work stays last.
    if (file === 'wb_ads_cluster_intelligence_v1' || file === '__central_ads_clusters__') return 6;

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
    if (file === '__central_github_search__') {
      var githubSearchResult = sellmonitorGithubRefreshGithubSearch_(ss, spec.payload || {});
      q.getRange(row,5,1,4).setValues([['DONE', data[5] || new Date(), new Date(), sellmonitorGithubJson_(githubSearchResult)]]);
      sellmonitorGithubLog_(SpreadsheetApp.openById(SMC_GH.CONTROL_CENTER_ID), ss.getId(), file, 'DONE', id, githubSearchResult);
      return {id:id,file:file,ok:true,result:githubSearchResult};
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
    if (file === 'inner_live_today_sync_v260' && result && result.ok === true) {
      result.hourly = sellmonitorGithubCaptureHourlyLive_(ss, String((spec.payload || {}).storeId || sellmonitorGithubSetting_(ss,'ACTIVE_STORE_ID','')));
    }
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
    var queueFile = String(file || 'unknown').replace(/[^A-Za-z0-9_-]/g,'').slice(0,80);
    var taggedMessage = 'QUEUE_FILE[' + queueFile + ']: ' + msg;
    q.getRange(row, 5, 1, 4).setValues([['ERROR', data[5] || new Date(), new Date(), taggedMessage]]);
    throw new Error(taggedMessage);
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

function sellmonitorGithubParseInnerMcp_(text) {
  text = String(text || '').trim();
  try { return JSON.parse(text); } catch (ignored) {}
  var messages = [];
  text.split(/\r?\n/).forEach(function(line) {
    if (line.indexOf('data:') !== 0) return;
    var payload = line.slice(5).trim();
    if (!payload || payload === '[DONE]') return;
    try { messages.push(JSON.parse(payload)); } catch (ignored) {}
  });
  return messages.length ? messages[0] : null;
}

function sellmonitorGithubMcpData_(response) {
  var item = response;
  if (Array.isArray(item)) item = item.filter(function(x) { return x && x.id === 2; })[0] || item[0];
  if (item && item.error) throw new Error('INNER_MCP_RPC_ERROR');
  var result = item && item.result ? item.result : {};
  if (Array.isArray(result.content)) {
    for (var i = 0; i < result.content.length; i++) {
      var part = result.content[i];
      if (!part || part.type !== 'text' || !part.text) continue;
      try { return JSON.parse(part.text); } catch (ignored) { throw new Error('INNER_MCP_RESPONSE_NOT_JSON'); }
    }
  }
  return result.structuredContent || result;
}

function sellmonitorGithubStartInnerFinanceOAuth_(cfg) {
  var props = sellmonitorClientProperties_(cfg.spreadsheetId);
  var existingUrl = String(props.getProperty('SM_INNER_MCP_AUTH_URL') || '').trim();
  if (existingUrl && props.getProperty('SM_INNER_MCP_STATE') &&
      props.getProperty('SM_INNER_MCP_CODE_VERIFIER') && props.getProperty('SM_INNER_MCP_CLIENT_ID')) {
    return existingUrl;
  }

  var base = SMC_INNER_FINANCE.endpoint, metadata = {};
  var metadataResponse = UrlFetchApp.fetch(base + '/.well-known/oauth-authorization-server', {
    method:'get',headers:{Accept:'application/json'},muteHttpExceptions:true,followRedirects:true
  });
  if (metadataResponse.getResponseCode() < 200 || metadataResponse.getResponseCode() >= 300) {
    throw new Error('INNER_OAUTH_METADATA_HTTP_' + metadataResponse.getResponseCode());
  }
  try { metadata = JSON.parse(metadataResponse.getContentText() || '{}'); }
  catch (e) { throw new Error('INNER_OAUTH_METADATA_INVALID'); }

  var redirect = String(ScriptApp.getService().getUrl() || '').trim();
  if (!/^https:\/\/script\.google\.com\/macros\/s\//.test(redirect)) {
    throw new Error('CENTRAL_OAUTH_REDIRECT_URL_INVALID');
  }
  var cid = String(props.getProperty('SM_INNER_MCP_CLIENT_ID') || '').trim();
  var tokenEndpoint = String(props.getProperty('SM_INNER_MCP_TOKEN_ENDPOINT') || metadata.token_endpoint || '').trim();
  if (!cid) {
    var registerEndpoint = String(metadata.registration_endpoint || (base + '/oauth/register'));
    var registration = UrlFetchApp.fetch(registerEndpoint, {
      method:'post',contentType:'application/json',
      payload:JSON.stringify({
        client_name:'Sellmonitor Control Center · ' + cfg.storeName,
        redirect_uris:[redirect],
        token_endpoint_auth_method:'none',
        grant_types:['authorization_code','refresh_token'],
        response_types:['code'],
        scope:'mcp'
      }),
      headers:{Accept:'application/json'},muteHttpExceptions:true,followRedirects:false
    });
    var registrationBody = {};
    try { registrationBody = JSON.parse(registration.getContentText() || '{}'); } catch (e) {}
    if (registration.getResponseCode() < 200 || registration.getResponseCode() >= 300 || !registrationBody.client_id) {
      throw new Error('INNER_OAUTH_REGISTRATION_HTTP_' + registration.getResponseCode());
    }
    cid = String(registrationBody.client_id);
  }
  if (!tokenEndpoint) throw new Error('INNER_OAUTH_TOKEN_ENDPOINT_MISSING');

  var verifier = Utilities.getUuid().replace(/-/g,'') + Utilities.getUuid().replace(/-/g,'');
  var challengeBytes = Utilities.computeDigest(Utilities.DigestAlgorithm.SHA_256,verifier,Utilities.Charset.UTF_8);
  var challenge = Utilities.base64EncodeWebSafe(challengeBytes).replace(/=+$/,'');
  var state = cfg.spreadsheetId + '.' + Utilities.getUuid().replace(/-/g,'') + Utilities.getUuid().replace(/-/g,'');
  var authorizeEndpoint = String(metadata.authorization_endpoint || (base + '/oauth/authorize'));
  var query = {
    response_type:'code',client_id:cid,redirect_uri:redirect,scope:'mcp',state:state,
    code_challenge:challenge,code_challenge_method:'S256'
  };
  var url = authorizeEndpoint + '?' + Object.keys(query).map(function(k) {
    return encodeURIComponent(k) + '=' + encodeURIComponent(query[k]);
  }).join('&');
  props.setProperties({
    SM_INNER_MCP_CLIENT_ID:cid,
    SM_INNER_MCP_REDIRECT_URI:redirect,
    SM_INNER_MCP_CODE_VERIFIER:verifier,
    SM_INNER_MCP_STATE:state,
    SM_INNER_MCP_TOKEN_ENDPOINT:tokenEndpoint,
    SM_INNER_MCP_AUTH_URL:url
  },false);
  return url;
}

function sellmonitorGithubInnerMcpCallWithToken_(token, tool, args) {
  var endpoint = SMC_INNER_FINANCE.endpoint;
  function post(body, sessionId) {
    var headers = {Authorization:'Bearer ' + token,Accept:'application/json, text/event-stream'};
    if (sessionId) headers['Mcp-Session-Id'] = sessionId;
    var response = UrlFetchApp.fetch(endpoint, {
      method:'post',contentType:'application/json',payload:JSON.stringify(body),
      headers:headers,muteHttpExceptions:true
    });
    var responseHeaders = response.getAllHeaders();
    var session = String(responseHeaders['Mcp-Session-Id'] || responseHeaders['mcp-session-id'] || sessionId || '');
    return {http:response.getResponseCode(),session:session,json:sellmonitorGithubParseInnerMcp_(response.getContentText())};
  }
  var init = post({
    jsonrpc:'2.0',id:1,method:'initialize',
    params:{protocolVersion:'2025-06-18',capabilities:{},clientInfo:{name:'Sellmonitor Control Center',version:SMC_GH.VERSION}}
  },'');
  if (init.http < 200 || init.http >= 300) throw new Error('INNER_MCP_INITIALIZE_HTTP_' + init.http);
  post({jsonrpc:'2.0',method:'notifications/initialized',params:{}},init.session);
  var call = post({jsonrpc:'2.0',id:2,method:'tools/call',params:{name:tool,arguments:args || {}}},init.session);
  if (call.http < 200 || call.http >= 300) throw new Error('INNER_MCP_CALL_HTTP_' + call.http);
  return sellmonitorGithubMcpData_(call.json);
}

function sellmonitorGithubInnerMcpCall_(spreadsheetId, tool, args) {
  var props = sellmonitorClientProperties_(spreadsheetId);
  var auth = sellmonitorGithubEnsureClientAuth_(spreadsheetId);
  if (!auth.ok) throw new Error('INNER_MCP_AUTH_REQUIRED');
  var token = String(props.getProperty('SM_INNER_MCP_ACCESS_TOKEN') || '');
  try {
    return sellmonitorGithubInnerMcpCallWithToken_(token,tool,args);
  } catch (e) {
    if (!/HTTP_401/.test(String(e.message || e))) throw e;
    props.deleteProperty('SM_INNER_MCP_ACCESS_TOKEN');
    auth = sellmonitorGithubEnsureClientAuth_(spreadsheetId);
    if (!auth.ok) throw new Error('INNER_MCP_REFRESH_REQUIRED');
    token = String(props.getProperty('SM_INNER_MCP_ACCESS_TOKEN') || '');
    return sellmonitorGithubInnerMcpCallWithToken_(token,tool,args);
  }
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
    props.deleteProperty('SM_INNER_MCP_STATE');
    props.deleteProperty('SM_INNER_MCP_CODE_VERIFIER');
    props.deleteProperty('SM_INNER_MCP_AUTH_URL');
    props.deleteProperty('SMC_INNER_FINANCE_OAUTH_URL_LOGGED');

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


const SMC_WB_TRAFFIC = Object.freeze({
  SPREADSHEET_ID: '11ULokTx74QjziZjThJ0lfxFiMQW42-WV4315cG7eIuU',
  DATASETS: {
    ads: {
      sheet: '12_ADS_CAMPAIGN_DAY',
      width: 15,
      source_width: 13,
      key_columns: [0, 1, 2, 3],
      source: 'WB /adv/v3/fullstats',
      quality: 'EXACT_CAMPAIGN_NM_DAY',
      numeric_columns: [2, 3, 4, 5, 6, 7, 8, 9],
      headers: ['store_id','date','advert_id','nmId','views','clicks','atbs','orders','spend_rub','order_sum_rub','source','quality','seller_article','load_timestamp','coverage_status']
    },
    funnel: {
      sheet: '13_FUNNEL_DAY',
      width: 11,
      source_width: 9,
      key_columns: [0, 1, 2],
      source: 'WB Analytics products/history',
      quality: null,
      numeric_columns: [2, 4, 5, 6, 7],
      headers: ['store_id','date','nmId','vendorCode','open_count','cart_count','order_count','order_sum_rub','source','load_timestamp','coverage_status']
    },
    organic: {
      sheet: '19_ORGANIC_DERIVED',
      width: 16,
      source_width: 14,
      key_columns: [0, 1, 2],
      source: 'WB Analytics products/history + WB /adv/v3/fullstats',
      quality: null,
      numeric_columns: [4, 5, 6, 11, 12, 13],
      headers: ['store_id','date','nmId','vendorCode','total_open_count','paid_clicks','organic_clicks','source_total','source_paid','formula','derivation_status','campaigns_checked','campaigns_without_period_statistics','campaigns_with_null_payload','load_timestamp','coverage_status']
    },
    ads_poll: {
      sheet: '15_ADS_POLL_SNAPSHOT',
      width: 19,
      source_width: 17,
      key_columns: [0, 1, 2],
      source: 'WB /adv/v3/fullstats',
      grain: 'DAILY_CUMULATIVE_OBSERVED_AT_POLL',
      source_column: 15,
      grain_column: 16,
      numeric_columns: [3,4,5,6,7,8,9,10,11,12,13,14],
      headers: ['store_id','observed_at','date','campaign_sku_rows','campaigns_with_rows','views','clicks','cart_adds','orders','spend_rub','order_sum_rub','ctr','cpc_rub','drr','roas','source','measurement_grain','load_timestamp','coverage_status']
    },
    funnel_poll: {
      sheet: '16_FUNNEL_POLL_SNAPSHOT',
      width: 15,
      source_width: 13,
      key_columns: [0, 1, 2],
      source: 'WB Analytics products/history',
      grain: 'DAILY_CUMULATIVE_OBSERVED_AT_POLL',
      source_column: 11,
      grain_column: 12,
      numeric_columns: [3,4,5,6,7,8,9,10],
      headers: ['store_id','observed_at','date','sku_rows','open_count','cart_count','order_count','order_sum_rub','open_to_cart','cart_to_order','open_to_order','source','measurement_grain','load_timestamp','coverage_status']
    }
  }
});


const SMC_WB_TRAFFIC_PARTITIONS = Object.freeze({
  registryId: '1E7HSGJgamzKZkdNdDNyp0f1iI2mLhhbpfi04-sOytBs',
  registrySheet: 'Части',
  schemaVersion: 'traffic-month-v1',
  shops: {
    ap: {storeId:'sanych_wb', name:'Саныч'},
    aa: {storeId:'air_wb', name:'AIR'},
    yv: {storeId:'hozyayushka_wb', name:'Хозяюшка'}
  }
});

function sellmonitorGithubTrafficPartHeaders_() {
  return ['part_id','platform','shop_id','dataset','layer','period_start','period_end',
    'part_number','schema_version','file_id','file_url','source_endpoint','source_kind',
    'run_id','fetched_at','row_count','distinct_key_count','min_date','max_date',
    'checksum','status','qc_status','error','supersedes_part_id'];
}

function sellmonitorGithubTrafficPartKey_(row, indexes) {
  return indexes.map(function(index) {
    var value = row[index];
    if (value instanceof Date && !isNaN(value.getTime())) {
      return Utilities.formatDate(value, 'GMT', 'yyyy-MM-dd');
    }
    return String(value == null ? '' : value).trim();
  }).join('|');
}

function sellmonitorGithubTrafficPartChecksum_(rows) {
  var bytes = Utilities.computeDigest(
    Utilities.DigestAlgorithm.SHA_256, JSON.stringify(rows), Utilities.Charset.UTF_8
  );
  return bytes.map(function(b) { return ('0' + ((b + 256) % 256).toString(16)).slice(-2); }).join('');
}

function sellmonitorGithubTrafficPartRegistry_(partRow) {
  var registry = SpreadsheetApp.openById(SMC_WB_TRAFFIC_PARTITIONS.registryId);
  var sheet = registry.getSheetByName(SMC_WB_TRAFFIC_PARTITIONS.registrySheet);
  if (!sheet) throw new Error('WB_TRAFFIC_PART_REGISTRY_TAB_MISSING');
  var headers = sellmonitorGithubTrafficPartHeaders_();
  if (sheet.getMaxColumns() < headers.length) {
    sheet.insertColumnsAfter(sheet.getMaxColumns(), headers.length - sheet.getMaxColumns());
  }
  var existingHeaders = sheet.getRange(1, 1, 1, headers.length).getDisplayValues()[0];
  for (var h = 0; h < 7; h++) {
    if (String(existingHeaders[h] || '').trim() !== headers[h]) {
      throw new Error('WB_TRAFFIC_PART_REGISTRY_HEADER_MISMATCH:' + (h + 1));
    }
  }
  sheet.getRange(1, 1, 1, headers.length).setValues([headers]);
  var last = sheet.getLastRow(), found = [];
  if (last > 1) {
    sheet.getRange(2, 1, last - 1, headers.length).getValues().forEach(function(row, i) {
      if (String(row[0] || '').trim() === String(partRow[0])) found.push(i + 2);
    });
  }
  if (found.length > 1) throw new Error('WB_TRAFFIC_PART_REGISTRY_DUPLICATE_PART_ID:' + partRow[0]);
  var target = found.length ? found[0] : Math.max(2, last + 1);
  if (target > sheet.getMaxRows()) sheet.insertRowsAfter(sheet.getMaxRows(), target - sheet.getMaxRows());
  sheet.getRange(target, 1, 1, headers.length).setValues([partRow]);
  return {row:target, created:found.length === 0};
}

function sellmonitorGithubTrafficPartFile_(shop, dataset, month, cfg) {
  var shopCfg = SMC_WB_TRAFFIC_PARTITIONS.shops[shop];
  if (!shopCfg) throw new Error('WB_TRAFFIC_PART_UNKNOWN_STORE:' + shop);
  var partId = 'WB-' + shopCfg.storeId + '-' + dataset + '-' + month + '-v1';
  var props = PropertiesService.getScriptProperties();
  var propKey = 'WB_TRAFFIC_PART_V1_' + shopCfg.storeId + '_' + dataset + '_' + month;
  var fileId = String(props.getProperty(propKey) || '').trim();
  var registry = SpreadsheetApp.openById(SMC_WB_TRAFFIC_PARTITIONS.registryId);
  var registrySheet = registry.getSheetByName(SMC_WB_TRAFFIC_PARTITIONS.registrySheet);
  if (!registrySheet) throw new Error('WB_TRAFFIC_PART_REGISTRY_TAB_MISSING');
  if (!fileId && registrySheet.getLastRow() > 1) {
    var found = [];
    registrySheet.getRange(2, 1, registrySheet.getLastRow() - 1, 10).getDisplayValues().forEach(function(row) {
      if (String(row[0] || '').trim() === partId) found.push(String(row[9] || '').trim());
    });
    if (found.length > 1) throw new Error('WB_TRAFFIC_PART_REGISTRY_DUPLICATE_PART_ID:' + partId);
    if (found.length === 1) {
      if (!found[0]) throw new Error('WB_TRAFFIC_PART_REGISTRY_FILE_ID_MISSING:' + partId);
      fileId = found[0];
    }
  }
  // Keep auxiliary datasets beside their base monthly facts instead of creating
  // another Drive file for every data type. This also lets the scheduled worker
  // append organic and polling observations without requesting DriveApp access.
  var baseDataset = dataset === 'organic' || dataset === 'funnel_poll' ? 'funnel'
    : dataset === 'ads_poll' ? 'ads' : '';
  if (!fileId && baseDataset && registrySheet.getLastRow() > 1) {
    var basePartId = 'WB-' + shopCfg.storeId + '-' + baseDataset + '-' + month + '-v1';
    var baseMatches = [];
    registrySheet.getRange(2, 1, registrySheet.getLastRow() - 1, 10).getDisplayValues().forEach(function(row) {
      if (String(row[0] || '').trim() === basePartId) baseMatches.push(String(row[9] || '').trim());
    });
    if (baseMatches.length > 1) throw new Error('WB_TRAFFIC_BASE_PART_REGISTRY_DUPLICATE:' + basePartId);
    if (baseMatches.length === 1) {
      if (!baseMatches[0]) throw new Error('WB_TRAFFIC_BASE_PART_FILE_ID_MISSING:' + basePartId);
      fileId = baseMatches[0];
      props.setProperty(propKey, fileId);
    }
  }
  if (!fileId && baseDataset) {
    throw new Error('WB_TRAFFIC_BASE_PART_REQUIRED:' + shopCfg.storeId + ':' + baseDataset + ':' + month);
  }
  var headers = cfg.headers.concat(['sync_run_id','record_key']);
  var ss, created = false;
  if (fileId) {
    ss = SpreadsheetApp.openById(fileId);
  } else {
    // Keep each bounded monthly partition addressable through the storage registry.
    // SpreadsheetApp.create needs no Drive-wide permission; moving new files into
    // Drive folders used to block scheduled publication without a broad OAuth grant.
    ss = SpreadsheetApp.create('WB — ' + shopCfg.name + ' — ' + dataset + ' — ' + month);
    fileId = ss.getId();
    props.setProperty(propKey, fileId);
    created = true;
  }
  var sheetName = dataset === 'organic' ? 'facts_organic'
    : dataset === 'ads_poll' ? 'facts_ads_poll'
    : dataset === 'funnel_poll' ? 'facts_funnel_poll' : 'facts';
  var sheet = ss.getSheetByName(sheetName);
  if (!sheet) {
    if (sheetName === 'facts' && created) {
      var tabs = ss.getSheets();
      sheet = tabs.length ? tabs[0] : ss.insertSheet('facts');
      if (sheet.setName) sheet.setName('facts');
    } else {
      sheet = ss.insertSheet(sheetName);
    }
  }
  if (sheet.getMaxColumns() < headers.length) {
    sheet.insertColumnsAfter(sheet.getMaxColumns(), headers.length - sheet.getMaxColumns());
  }
  if (sheet.getMaxRows() < 2) sheet.insertRowsAfter(sheet.getMaxRows(), 2 - sheet.getMaxRows());
  if (!sheet.getLastRow()) sheet.getRange(1, 1, 1, headers.length).setValues([headers]);
  var liveHeaders = sheet.getRange(1, 1, 1, headers.length).getDisplayValues()[0];
  if (headers.some(function(h, i) { return String(liveHeaders[i] || '').trim() !== h; })) {
    throw new Error('WB_TRAFFIC_PART_HEADER_MISMATCH:' + partId);
  }
  return {id:fileId, partId:partId, sheet:sheet, headers:headers};
}

function sellmonitorGithubInnerMetric_(object, path, divisor) {
  var value = object;
  for (var i = 0; i < path.length; i++) {
    if (value == null || !Object.prototype.hasOwnProperty.call(value,path[i])) return '';
    value = value[path[i]];
  }
  if (value == null || value === '') return '';
  var number = Number(value);
  return isFinite(number) ? number / (divisor || 1) : '';
}

function sellmonitorGithubInnerFinanceRows_(cfg, from, to, products, loadedAt, sourceState) {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(String(from)) || !/^\d{4}-\d{2}-\d{2}$/.test(String(to))) {
    throw new Error('INNER_FINANCE_DATE_INVALID');
  }
  var days = Math.round((new Date(to + 'T12:00:00Z') - new Date(from + 'T12:00:00Z')) / 86400000);
  if (days !== 1) throw new Error('INNER_FINANCE_DAILY_PERIOD_REQUIRED');
  if (!Array.isArray(products) || !products.length) throw new Error('INNER_FINANCE_EMPTY_PRODUCTS');
  var partial = !!(sourceState && sourceState.partial === true);
  var freshness = sourceState && sourceState.freshness ? sourceState.freshness : {};
  function fresh(name) {
    return !partial || sellmonitorGithubInnerCategoryFresh_(freshness, name);
  }
  var productsFresh = fresh('products');
  var ordersFresh = fresh('orders');
  var postOrdersFresh = fresh('postaggregated_orders');
  var stocksFresh = fresh('stocks');
  var financeRowsFresh = fresh('financial_operations') && fresh('financial_report_rows');
  var wbExpensesFresh = fresh('wb_expenses');
  var postExpensesFresh = fresh('postaggregated_expenses');
  var fullExpenseFresh = financeRowsFresh && wbExpensesFresh && postExpensesFresh;
  if (partial && !productsFresh) throw new Error('INNER_FINANCE_PARTIAL_PRODUCTS_STALE');
  if (partial && !ordersFresh && !postOrdersFresh && !stocksFresh) {
    throw new Error('INNER_FINANCE_PARTIAL_NO_FRESH_FACT_CATEGORIES');
  }
  function metric(product, path, divisor, allowed) {
    return allowed ? sellmonitorGithubInnerMetric_(product,path,divisor) : '';
  }
  var now = loadedAt || new Date();
  var loadedAtValue = now instanceof Date && !isNaN(now.getTime()) ? now.toISOString() : String(now);
  var rows = [], seen = {};
  products.forEach(function(product) {
    var nmId = String(product && (product.id != null ? product.id : product.article) || '').trim();
    if (!nmId) throw new Error('INNER_FINANCE_PRODUCT_ID_MISSING');
    if (seen[nmId]) throw new Error('INNER_FINANCE_DUPLICATE_PRODUCT:' + nmId);
    seen[nmId] = true;
    var other = metric(product,['otherExpenses','price'],100,wbExpensesFresh);
    var additional = metric(product,['additionalExpenses','price'],100,wbExpensesFresh);
    var otherTotal = other === '' || additional === '' ? '' : other + additional;
    rows.push([
      cfg.storeId,cfg.accountId,'wb',from,to,days,nmId,
      productsFresh ? String(product.supplierArticle || '') : '',
      metric(product,['orderedAmount','count'],null,ordersFresh),
      metric(product,['orderedAmount','price'],100,ordersFresh),
      metric(product,['payedAmountFromOrders','count'],null,postOrdersFresh),
      metric(product,['netSalesAmount','count'],null,postOrdersFresh),
      metric(product,['returnedAmount','count'],null,postOrdersFresh),
      metric(product,['returnedAmount','price'],100,financeRowsFresh),
      metric(product,['revenueAmount','price'],100,financeRowsFresh),
      metric(product,['revenueWithMarketplaceDiscount','price'],100,financeRowsFresh),
      metric(product,['marketplaceDiscountAmount','price'],100,financeRowsFresh),
      metric(product,['primeCostExpenses','price'],100,postExpensesFresh),
      metric(product,['commission','price'],100,financeRowsFresh),
      metric(product,['logistic','price'],100,financeRowsFresh),
      metric(product,['deliveryLogistic','price'],100,financeRowsFresh),
      metric(product,['returnLogistic','price'],100,financeRowsFresh),
      metric(product,['storage','price'],100,wbExpensesFresh),
      metric(product,['ad','price'],100,wbExpensesFresh),
      metric(product,['fines','price'],100,wbExpensesFresh),
      metric(product,['paidAcceptance','price'],100,wbExpensesFresh),
      metric(product,['deduction','price'],100,wbExpensesFresh),
      metric(product,['tax','price'],100,wbExpensesFresh),
      otherTotal,
      metric(product,['expensesAmount','price'],100,fullExpenseFresh),
      metric(product,['profitAmount','price'],100,fullExpenseFresh),
      metric(product,['payoutAmount','price'],100,financeRowsFresh),
      metric(product,['marginality','percent'],100,fullExpenseFresh),
      metric(product,['returnOnInvestment','percent'],100,fullExpenseFresh),
      metric(product,['payedAmountFromOrders','percent'],100,postOrdersFresh && ordersFresh),
      metric(product,['averageProfitPrice'],100,fullExpenseFresh),
      metric(product,['stock','count'],null,stocksFresh),
      metric(product,['stockPrimeCost'],100,stocksFresh && postExpensesFresh),
      partial ? 'PARTIAL_INNER_ANALYTICS' : 'FACTUAL_INNER_ANALYTICS',
      partial ? 'SELLMONITOR_INNER_MCP_PARTIAL' : 'SELLMONITOR_INNER_MCP',
      loadedAtValue,'','',cfg.storeName
    ]);
  });
  if (rows.some(function(row) { return row.length !== SMC_INNER_FINANCE_HEADERS.length; })) {
    throw new Error('INNER_FINANCE_SCHEMA_WIDTH_MISMATCH');
  }
  return rows;
}
function sellmonitorGithubInnerFinanceChecksum_(rows) {
  var canonical = rows.map(function(row) {
    return row.map(function(value,index) {
      // loaded_at (40) and sync_run_id (44) change on a safe retry; neither
      // is source data. Excluding them keeps read-back checks stable while
      // every business field and the immutable record_key remain covered.
      if (index === 40 || index === 44) return '';
      if (value == null || value === '') return '';
      // Sheets may store numeric-looking WB nmId strings as numbers; hash the ID consistently.
      if (index === 3 || index === 4) return sellmonitorGithubInnerFinanceStoredDateKey_(value);
      if (index === 6) return String(value).trim();
      if (value instanceof Date && !isNaN(value.getTime())) {
        return Utilities.formatDate(value,'GMT',"yyyy-MM-dd'T'HH:mm:ss'Z'");
      }
      if (typeof value === 'number' && isFinite(value)) {
        var rounded = Math.round(value * 1000000) / 1000000;
        return Object.is(rounded,-0) ? 0 : rounded;
      }
      return value;
    });
  });
  return sellmonitorGithubTrafficPartChecksum_(canonical);
}

function sellmonitorGithubInnerFinanceStoredDateKey_(value) {
  if (value instanceof Date && !isNaN(value.getTime())) {
    return Utilities.formatDate(value,'Europe/Moscow','yyyy-MM-dd');
  }
  var s=String(value==null?'':value).trim();
  var iso=s.match(/^(\d{4}-\d{2}-\d{2})/);
  if(iso)return iso[1];
  var us=s.match(/^(\d{1,2})\/(\d{1,2})\/(\d{4})$/);
  if(us)return us[3]+'-'+('0'+us[1]).slice(-2)+'-'+('0'+us[2]).slice(-2);
  var ru=s.match(/^(\d{1,2})\.(\d{1,2})\.(\d{4})$/);
  if(ru)return ru[3]+'-'+('0'+ru[2]).slice(-2)+'-'+('0'+ru[1]).slice(-2);
  throw new Error('INNER_FINANCE_STORED_DATE_INVALID');
}

function sellmonitorGithubInnerFinanceStoredKey_(row) {
  return [
    String(row[0]==null?'':row[0]).trim(),
    sellmonitorGithubInnerFinanceStoredDateKey_(row[3]),
    sellmonitorGithubInnerFinanceStoredDateKey_(row[4]),
    String(row[6]==null?'':row[6]).trim()
  ].join('|');
}

function sellmonitorGithubInnerFinanceStoredPayload_(row) {
  var payload=row.slice(0,44);
  payload[3]=sellmonitorGithubInnerFinanceStoredDateKey_(payload[3]);
  payload[4]=sellmonitorGithubInnerFinanceStoredDateKey_(payload[4]);
  payload.splice(40,1); // loaded_at changes on an identical source retry
  return JSON.stringify(payload);
}

function sellmonitorGithubWriteInnerFinancePart_(cfg, rows, from, runId) {
  if (!rows.length) throw new Error('INNER_FINANCE_EMPTY_WRITE_REFUSED');
  var month = String(from).slice(0,7);
  var partCfg = {
    headers:SMC_INNER_FINANCE_HEADERS,
    schemaVersion:SMC_INNER_FINANCE.schemaVersion,
    key_columns:[0,3,4,6],
    source:'Sellmonitor Inner product analytics',
    sourceKind:'SELLMONITOR_INNER_MCP'
  };
  var part = sellmonitorGithubTrafficPartFile_(cfg.shopKey,'inner_finance',month,partCfg);
  var sheet = part.sheet, width = part.headers.length, oldLast = sheet.getLastRow(), byKey = {};
  var storedRows = {}, duplicateRows = [];
  if (oldLast > 1) {
    sheet.getRange(2,1,oldLast - 1,width).getValues().forEach(function(row, offset) {
      if (!row[0]) return;
      var key = sellmonitorGithubInnerFinanceStoredKey_(row);
      if (!key || key.indexOf('||') >= 0) throw new Error('INNER_FINANCE_STORED_KEY_INVALID');
      if (Object.prototype.hasOwnProperty.call(byKey,key)) {
        if (sellmonitorGithubInnerFinanceStoredPayload_(storedRows[key]) !== sellmonitorGithubInnerFinanceStoredPayload_(row)) {
          throw new Error('INNER_FINANCE_DUPLICATE_STORED_CONFLICT:' + key);
        }
        duplicateRows.push({row_index:offset+2,key:key,row:row});
        return;
      }
      byKey[key] = row;
      storedRows[key] = row;
    });
  }

  var archivedDuplicates = 0;
  if (duplicateRows.length) {
    var partSpreadsheet = SpreadsheetApp.openById(part.id);
    var archiveName = 'facts_duplicate_archive';
    var archiveSheet = partSpreadsheet.getSheetByName(archiveName);
    if (!archiveSheet) archiveSheet = partSpreadsheet.insertSheet(archiveName);
    var archiveHeaders = ['archived_at','source_row','duplicate_key','archive_run_id'].concat(part.headers);
    if (archiveSheet.getLastRow() === 0) {
      if (archiveSheet.getMaxColumns() < archiveHeaders.length) {
        archiveSheet.insertColumnsAfter(archiveSheet.getMaxColumns(), archiveHeaders.length - archiveSheet.getMaxColumns());
      }
      archiveSheet.getRange(1,1,1,archiveHeaders.length).setValues([archiveHeaders]);
    } else {
      var archiveHead = archiveSheet.getRange(1,1,1,archiveHeaders.length).getDisplayValues()[0];
      if (archiveHeaders.some(function(value,index) { return String(archiveHead[index] || '').trim() !== value; })) {
        throw new Error('INNER_FINANCE_DUPLICATE_ARCHIVE_HEADER_MISMATCH');
      }
    }
    var archivedAt = new Date();
    var archiveRows = duplicateRows.map(function(x) {
      return [archivedAt,x.row_index,x.key,String(runId)].concat(x.row);
    });
    var archiveStart = archiveSheet.getLastRow() + 1;
    var archiveEnd = archiveStart + archiveRows.length - 1;
    if (archiveSheet.getMaxRows() < archiveEnd) archiveSheet.insertRowsAfter(archiveSheet.getMaxRows(), archiveEnd - archiveSheet.getMaxRows());
    archiveSheet.getRange(archiveStart,1,archiveRows.length,archiveHeaders.length).setValues(archiveRows);
    var archiveReadback = archiveSheet.getRange(archiveStart,1,archiveRows.length,archiveHeaders.length).getValues();
    if (JSON.stringify(archiveReadback) !== JSON.stringify(archiveRows)) {
      throw new Error('INNER_FINANCE_DUPLICATE_ARCHIVE_READBACK_MISMATCH');
    }
    archivedDuplicates = duplicateRows.length;
  }

  var added = 0, updated = 0, partialPreserved = 0, duplicateInputKeys = 0, inputByKey = {};
  rows.forEach(function(row) {
    if (row.length !== SMC_INNER_FINANCE_HEADERS.length || row[0] !== cfg.storeId || row[3] !== from) {
      throw new Error('INNER_FINANCE_ROW_OUTSIDE_TARGET_PERIOD');
    }
    var key = sellmonitorGithubInnerFinanceStoredKey_(row);
    if (!key || key.indexOf('||') >= 0) throw new Error('INNER_FINANCE_ROW_KEY_INVALID');
    if (Object.prototype.hasOwnProperty.call(inputByKey,key)) {
      if (sellmonitorGithubInnerFinanceStoredPayload_(inputByKey[key]) !== sellmonitorGithubInnerFinanceStoredPayload_(row)) {
        throw new Error('INNER_FINANCE_DUPLICATE_INPUT_CONFLICT:' + key);
      }
      duplicateInputKeys++;
      return;
    }
    inputByKey[key] = row;
    if (Object.prototype.hasOwnProperty.call(byKey,key)) {
      var existing = byKey[key];
      if (row[38] === 'PARTIAL_INNER_ANALYTICS' && String(existing[38] || '') === 'FACTUAL_INNER_ANALYTICS') {
        partialPreserved++;
        return;
      }
      if (row[38] === 'PARTIAL_INNER_ANALYTICS') {
        for (var metricIndex = 8; metricIndex <= 37; metricIndex++) {
          if (row[metricIndex] === '' && existing[metricIndex] !== '') row[metricIndex] = existing[metricIndex];
        }
      }
      updated++;
    } else {
      added++;
    }
    byKey[key] = row.concat([String(runId),key]);
  });
  var merged = Object.keys(byKey).map(function(key) { return byKey[key]; });
  merged.sort(function(a,b) {
    return sellmonitorGithubInnerFinanceStoredDateKey_(a[3]).localeCompare(sellmonitorGithubInnerFinanceStoredDateKey_(b[3])) ||
      String(a[6]).localeCompare(String(b[6]));
  });
  var allTrusted = merged.every(function(row) { return String(row[38] || '') === 'FACTUAL_INNER_ANALYTICS'; });
  var datasetClass = allTrusted ? 'trusted_inner_daily_rows' : 'mixed_trust_inner_daily_rows';
  var partialRows = merged.length - merged.filter(function(row) { return String(row[38] || '') === 'FACTUAL_INNER_ANALYTICS'; }).length;
  var needed = merged.length + 1;
  if (sheet.getMaxRows() < needed) sheet.insertRowsAfter(sheet.getMaxRows(),needed - sheet.getMaxRows());
  if (merged.length) sheet.getRange(2,1,merged.length,width).setValues(merged);
  if (oldLast > needed) sheet.getRange(needed + 1,1,oldLast - needed,width).clearContent();
  SpreadsheetApp.flush();
  var readback = merged.length ? sheet.getRange(2,1,merged.length,width).getValues() : [];
  var digest = sellmonitorGithubInnerFinanceChecksum_(merged);
  if (sellmonitorGithubInnerFinanceChecksum_(readback) !== digest) throw new Error('INNER_FINANCE_PART_READBACK_CHECKSUM_MISMATCH');
  var dates = merged.map(function(row) { return sellmonitorGithubInnerFinanceStoredDateKey_(row[3]); }).filter(Boolean).sort();
  var monthParts = month.split('-');
  var monthEnd = Utilities.formatDate(new Date(Date.UTC(Number(monthParts[0]),Number(monthParts[1]),0)),'GMT','yyyy-MM-dd');
  var url = 'https://docs.google.com/spreadsheets/d/' + part.id + '/edit';
  var registryRow = [
    part.partId,'WB',cfg.storeId,'inner_finance',datasetClass,
    month + '-01',monthEnd,1,SMC_INNER_FINANCE.schemaVersion,part.id,url,
    'analytics_get_product_list','SELLMONITOR_INNER_MCP',String(runId),new Date(),merged.length,
    Object.keys(byKey).length,dates[0] || '',dates[dates.length - 1] || '',digest,'OK','PASS','',''
  ];
  sellmonitorGithubTrafficPartRegistry_(registryRow);
  return {
    part_id:part.partId,file_id:part.id,url:url,rows:merged.length,distinct_keys:Object.keys(byKey).length,
    added:added,updated:updated,min_date:dates[0] || '',max_date:dates[dates.length - 1] || '',
    checksum:digest,qc_status:'PASS',trusted_rows:merged.length - partialRows,partial_rows:partialRows,
    partial_preserved:partialPreserved,archived_duplicate_rows:archivedDuplicates,deduplicated_input_keys:duplicateInputKeys
  };
}
function sellmonitorGithubInnerProducts_(data) {
  if (!data) throw new Error('INNER_FINANCE_SOURCE_EMPTY');
  var value = data.data;
  if (Array.isArray(value)) return value;
  if (value && Array.isArray(value.products)) return value.products;
  if (value && Array.isArray(value.items)) return value.items;
  if (Array.isArray(data.products)) return data.products;
  return [];
}

function sellmonitorGithubInnerFreshness_(data) {
  var candidates = [
    data && data.freshness,
    data && data.sourceFreshness,
    data && data.source_freshness,
    data && data.data && data.data.freshness,
    data && data.data && data.data.sourceFreshness,
    data && data.data && data.data.source_freshness
  ];
  for (var i = 0; i < candidates.length; i++) {
    var value = candidates[i];
    if (!value) continue;
    if (typeof value === 'string') {
      try { value = JSON.parse(value); } catch (ignored) { continue; }
    }
    if (!value || typeof value !== 'object') continue;
    if (value.categories && typeof value.categories === 'object') return value.categories;
    if (Array.isArray(value.accounts)) {
      for (var j = 0; j < value.accounts.length; j++) {
        var account = value.accounts[j];
        if (account && account.categories && typeof account.categories === 'object') {
          return account.categories;
        }
      }
    }
    if (value.orders || value.products || value.stocks || value.financial_report_rows) return value;
  }
  return {};
}

function sellmonitorGithubInnerCategoryFresh_(freshness, name) {
  var category = freshness && freshness[name];
  return !!category && category.covered === true && category.stale !== true;
}
function sellmonitorGithubNextDate_(dateKey) {
  var parts = String(dateKey).split('-').map(Number);
  return Utilities.formatDate(new Date(Date.UTC(parts[0],parts[1] - 1,parts[2] + 1)),'GMT','yyyy-MM-dd');
}

function sellmonitorGithubMaybeSyncInnerFinance_(cfg,cc) {
  var props = sellmonitorClientProperties_(cfg.spreadsheetId);
  var auth = sellmonitorGithubEnsureClientAuth_(cfg.spreadsheetId);
  if (!auth.ok || !auth.refresh_token || !props.getProperty('SM_INNER_MCP_CLIENT_ID')) {
    try {
      var authorizationUrl = sellmonitorGithubStartInnerFinanceOAuth_(cfg);
      if (cc && String(props.getProperty('SMC_INNER_FINANCE_OAUTH_URL_LOGGED') || '') !== authorizationUrl) {
        sellmonitorGithubLog_(cc,cfg.spreadsheetId,'INNER_OAUTH_REQUIRED','WAITING_USER',
          'Sellmonitor Inner требует подтверждения доступа для продолжения выгрузки.',
          {store_id:cfg.storeId,authorization_url:authorizationUrl});
        props.setProperty('SMC_INNER_FINANCE_OAUTH_URL_LOGGED',authorizationUrl);
      }
      return {skipped:true,reason:'authorization_required'};
    } catch (authError) {
      var authMessage = String(authError && authError.message ? authError.message : authError).slice(0,1000);
      if (cc) sellmonitorGithubLog_(cc,cfg.spreadsheetId,'INNER_OAUTH_REQUIRED','ERROR',authMessage,{store_id:cfg.storeId});
      return {skipped:true,reason:'authorization_setup_failed',error:authMessage};
    }
  }
  var dateKey = String(props.getProperty('SMC_INNER_FINANCE_NEXT_DATE') || SMC_INNER_FINANCE.initialDate);
  var cutoff = Utilities.formatDate(new Date(Date.now() - 86400000),'Europe/Moscow','yyyy-MM-dd');
  if (!/^\d{4}-\d{2}-\d{2}$/.test(dateKey)) throw new Error('INNER_FINANCE_NEXT_DATE_INVALID');
  var isHistorical = false;
  var isPartialReplay = false;
  var partialQueueKey = 'SMC_INNER_FINANCE_PARTIAL_DATES';
  var partialReplayAtKey = 'SMC_INNER_FINANCE_PARTIAL_REPLAY_AT_MS';
  var partialDates = [];
  try {
    var savedPartialDates = JSON.parse(props.getProperty(partialQueueKey) || '[]');
    if (Array.isArray(savedPartialDates)) {
      partialDates = savedPartialDates.map(function(x) { return String(x || ''); })
        .filter(function(x) { return /^\d{4}-\d{2}-\d{2}$/.test(x); });
    }
  } catch (ignored) {}
  var now = Date.now();
  var lastPartialReplay = Number(props.getProperty(partialReplayAtKey) || 0);
  var partialReplayIntervalMs = 15 * 60 * 1000;
  var replayDate = partialDates.length && now - lastPartialReplay >= partialReplayIntervalMs ? partialDates[0] : '';
  if (replayDate) {
    dateKey = replayDate;
    isPartialReplay = true;
  } else if (dateKey > cutoff) {
    dateKey = String(props.getProperty('SMC_INNER_FINANCE_BACKFILL_NEXT_DATE') || SMC_INNER_FINANCE.historicalStartDate);
    if (!/^\d{4}-\d{2}-\d{2}$/.test(dateKey)) throw new Error('INNER_FINANCE_BACKFILL_DATE_INVALID');
    if (dateKey >= SMC_INNER_FINANCE.historicalEndDateExclusive) {
      return {skipped:true,reason:'caught_up',next_date:String(props.getProperty('SMC_INNER_FINANCE_NEXT_DATE') || SMC_INNER_FINANCE.initialDate),latest_complete_date:cutoff};
    }
    isHistorical = true;
  }
  now = Date.now();
  var lastAttempt = Number(props.getProperty('SMC_INNER_FINANCE_LAST_ATTEMPT_MS') || 0);
  if (lastAttempt && now - lastAttempt < SMC_INNER_FINANCE.retryIntervalMs) {
    return {skipped:true,reason:'retry_cooldown',next_date:dateKey};
  }

  props.setProperty('SMC_INNER_FINANCE_LAST_ATTEMPT_MS',String(now));
  if (isPartialReplay) props.setProperty(partialReplayAtKey,String(now));
  try {
    var nextDay = sellmonitorGithubNextDate_(dateKey);
    var data = sellmonitorGithubInnerMcpCall_(cfg.spreadsheetId,'analytics_get_product_list',{
      marketplaceCode:'wb',marketplaceAccountId:cfg.accountId,
      from:dateKey,to:nextDay,after:0,limit:2000
    });
    var partial = !!(data && data.partial === true);
    var freshness = sellmonitorGithubInnerFreshness_(data);
    var products = sellmonitorGithubInnerProducts_(data);
    var minimum = Number(cfg.minProductCount || 50);
    if (products.length < minimum) throw new Error('INNER_FINANCE_SOURCE_COVERAGE_LOW:' + products.length + '<' + minimum);
    if (partial) {
      if (!Object.keys(freshness).length) throw new Error('INNER_FINANCE_PARTIAL_FRESHNESS_MISSING');
      if (!sellmonitorGithubInnerCategoryFresh_(freshness,'products')) throw new Error('INNER_FINANCE_PARTIAL_PRODUCTS_STALE');
      var hasFreshFactCategory =
        sellmonitorGithubInnerCategoryFresh_(freshness,'orders') ||
        sellmonitorGithubInnerCategoryFresh_(freshness,'postaggregated_orders') ||
        sellmonitorGithubInnerCategoryFresh_(freshness,'stocks');
      if (!hasFreshFactCategory) throw new Error('INNER_FINANCE_PARTIAL_NO_FRESH_FACT_CATEGORIES');
    }
    var freshCategories = Object.keys(freshness).filter(function(name) {
      return sellmonitorGithubInnerCategoryFresh_(freshness,name);
    });
    var rows = sellmonitorGithubInnerFinanceRows_(cfg,dateKey,nextDay,products,new Date(),{partial:partial,freshness:freshness});
    var runId = 'INNER-' + cfg.storeId + '-' + dateKey + '-' + now;
    var part = sellmonitorGithubWriteInnerFinancePart_(cfg,rows,dateKey,runId);

    var nextPartialDates = partialDates.filter(function(d) { return d !== dateKey; });
    if (partial) nextPartialDates.push(dateKey);
    if (nextPartialDates.length) {
      props.setProperty(partialQueueKey,JSON.stringify(nextPartialDates));
    } else {
      props.deleteProperty(partialQueueKey);
    }
    if (partial || isPartialReplay) props.setProperty(partialReplayAtKey,String(now));

    if (!isPartialReplay) {
      if (isHistorical) props.setProperty('SMC_INNER_FINANCE_BACKFILL_NEXT_DATE',nextDay);
      else props.setProperty('SMC_INNER_FINANCE_NEXT_DATE',nextDay);
    }
    props.setProperty('SMC_INNER_FINANCE_LAST_SUCCESS_AT',new Date().toISOString());
    props.setProperty('SMC_INNER_FINANCE_LAST_ERROR','');
    return {
      ok:true,attempted:true,date:dateKey,next_date:String(props.getProperty('SMC_INNER_FINANCE_NEXT_DATE') || nextDay),
      historical:isHistorical,partial_replay:isPartialReplay,partial:partial,fresh_categories:freshCategories,
      source_rows:products.length,finance_rows:rows.length,part:part
    };
  } catch (e) {
    var message = String(e && e.message ? e.message : e).slice(0,1000);
    props.setProperty('SMC_INNER_FINANCE_LAST_ERROR',message);
    return {ok:false,attempted:true,date:dateKey,error:message};
  }
}
function sellmonitorGithubWriteWbTrafficPartitions_(prepared, runId, skipDailyView) {
  var output = {};
  ['ads','funnel','organic','ads_poll','funnel_poll'].forEach(function(dataset) {
    var cfg = SMC_WB_TRAFFIC.DATASETS[dataset], table = prepared[dataset] || {rows:[]};
    var dateIndex = (dataset === 'ads' || dataset === 'funnel' || dataset === 'organic') ? 1 : 2;
    var groups = {};
    table.rows.forEach(function(row, index) {
      var rawShop = String(row[0] || '').trim();
      var shopCfg = SMC_WB_TRAFFIC_PARTITIONS.shops[rawShop];
      if (!shopCfg) throw new Error('WB_TRAFFIC_PART_UNKNOWN_STORE:' + rawShop);
      var day = row[dateIndex] instanceof Date && !isNaN(row[dateIndex].getTime())
        ? Utilities.formatDate(row[dateIndex], 'Europe/Moscow', 'yyyy-MM-dd')
        : String(row[dateIndex] || '').trim();
      if (!/^\d{4}-\d{2}-\d{2}$/.test(day)) throw new Error('WB_TRAFFIC_PART_INVALID_DATE:' + dataset + ':' + index);
      var month = day.slice(0, 7), groupKey = rawShop + '|' + month;
      (groups[groupKey] || (groups[groupKey] = {shop:rawShop, month:month, input:[]})).input.push(row);
    });
    var groupKeys = Object.keys(groups).sort();
    var datasetResult = {written:0, added:0, updated:0, distinct_keys:0, parts:[], skipped:groupKeys.length === 0};
    groupKeys.forEach(function(groupKey) {
      var group = groups[groupKey], part = sellmonitorGithubTrafficPartFile_(group.shop, dataset, group.month, cfg);
      var sheet = part.sheet, width = part.headers.length;
      var keyIndexes = cfg.key_columns.slice();
      var oldLast = sheet.getLastRow(), byKey = {};
      if (oldLast > 1) {
        sheet.getRange(2, 1, oldLast - 1, width).getValues().forEach(function(row, offset) {
          if (!row[0]) return;
          var key = sellmonitorGithubTrafficPartKey_(row, keyIndexes);
          if (!key || Object.prototype.hasOwnProperty.call(byKey, key)) {
            throw new Error('WB_TRAFFIC_PART_DUPLICATE_EXISTING_KEY:' + part.partId + ':' + key);
          }
          byKey[key] = row;
        });
      }
      var added = 0, updated = 0;
      group.input.forEach(function(row) {
        var key = sellmonitorGithubTrafficPartKey_(row, keyIndexes);
        if (!key || key.indexOf('||') >= 0) throw new Error('WB_TRAFFIC_PART_INVALID_KEY:' + part.partId);
        var full = row.slice();
        full.push(String(runId), key);
        if (Object.prototype.hasOwnProperty.call(byKey, key)) updated++;
        else added++;
        byKey[key] = full;
      });
      var dateFirst = Object.keys(byKey).map(function(key) { return byKey[key]; });
      dateFirst.sort(function(a,b) {
        var ad = String(a[dateIndex] instanceof Date ? Utilities.formatDate(a[dateIndex], 'GMT', 'yyyy-MM-dd') : a[dateIndex] || '');
        var bd = String(b[dateIndex] instanceof Date ? Utilities.formatDate(b[dateIndex], 'GMT', 'yyyy-MM-dd') : b[dateIndex] || '');
        return ad.localeCompare(bd) || sellmonitorGithubTrafficPartKey_(a,keyIndexes).localeCompare(sellmonitorGithubTrafficPartKey_(b,keyIndexes));
      });
      var needed = dateFirst.length + 1;
      if (sheet.getMaxRows() < needed) sheet.insertRowsAfter(sheet.getMaxRows(), needed - sheet.getMaxRows());
      if (dateFirst.length) sheet.getRange(2, 1, dateFirst.length, width).setValues(dateFirst);
      if (oldLast > needed) sheet.getRange(needed + 1, 1, oldLast - needed, width).clearContent();
      var dates = dateFirst.map(function(row) {
        return String(row[dateIndex] instanceof Date ? Utilities.formatDate(row[dateIndex], 'GMT', 'yyyy-MM-dd') : row[dateIndex] || '');
      }).filter(Boolean).sort();
      var startDay = group.month + '-01';
      var parts = group.month.split('-'), next = new Date(Date.UTC(Number(parts[0]), Number(parts[1]), 0));
      var endDay = Utilities.formatDate(next, 'GMT', 'yyyy-MM-dd');
      var digest = sellmonitorGithubTrafficPartChecksum_(dateFirst);
      var url = 'https://docs.google.com/spreadsheets/d/' + part.id + '/edit';
      var registryRow = [
        part.partId,'WB',SMC_WB_TRAFFIC_PARTITIONS.shops[group.shop].storeId,dataset,
        'normalized_source_rows',startDay,endDay,1,SMC_WB_TRAFFIC_PARTITIONS.schemaVersion,
        part.id,url,cfg.source,dataset === 'organic' ? 'DERIVED_FROM_WB_OFFICIAL_APIS' : 'WB_OFFICIAL_API_NORMALIZED',String(runId),new Date(),
        dateFirst.length,Object.keys(byKey).length,dates[0] || '',dates[dates.length - 1] || '',
        digest,'OK','PASS','',''
      ];
      sellmonitorGithubTrafficPartRegistry_(registryRow);
      var summary = {
        part_id:part.partId,shop_id:SMC_WB_TRAFFIC_PARTITIONS.shops[group.shop].storeId,
        dataset:dataset,month:group.month,file_id:part.id,url:url,
        rows:dateFirst.length,distinct_keys:Object.keys(byKey).length,
        added:added,updated:updated,min_date:dates[0] || '',max_date:dates[dates.length - 1] || '',
        checksum:digest,qc_status:'PASS'
      };
      datasetResult.parts.push(summary);
      datasetResult.written += group.input.length;
      datasetResult.added += added;
      datasetResult.updated += updated;
      datasetResult.distinct_keys += group.input.length;
    });
    output[dataset] = datasetResult;
  });
  output.daily_view = skipDailyView === true
    ? {ok:true,skipped:true,reason:'daily_view_deferred_until_all_datasets_are_published'}
    : sellmonitorGithubWriteWbTrafficDailyView_(prepared, runId);
  return {ok:skipDailyView === true || (output.daily_view.lite_tables && output.daily_view.lite_tables.ok === true),
    schema_version:SMC_WB_TRAFFIC_PARTITIONS.schemaVersion, run_id:String(runId), datasets:output};
}

function sellmonitorGithubWriteWbTrafficDailyView_(prepared, runId, options) {
  options = options || {};
  var headers = ['Дата','Магазин','Показы рекламы','Клики рекламы','Расход рекламы, ₽','Заказы рекламы',
    'Переходы в карточку','В корзину','Заказы воронки','Сумма заказов воронки, ₽',
    'Платные клики · сверка','Органические клики · расчёт','Статус органики',
    'Строк рекламы','Строк воронки','Строк органики','Идентификатор сбора'];
  var registry = SpreadsheetApp.openById(SMC_WB_TRAFFIC_PARTITIONS.registryId);
  var sheet = registry.getSheetByName('Дневная_витрина');
  if (!sheet) sheet = registry.insertSheet('Дневная_витрина');
  if (sheet.getMaxColumns() < headers.length) sheet.insertColumnsAfter(sheet.getMaxColumns(), headers.length - sheet.getMaxColumns());
  var last = sheet.getLastRow();
  if (!last) {
    if (sheet.getMaxRows() < 1) sheet.insertRowsAfter(sheet.getMaxRows(), 1 - sheet.getMaxRows());
    sheet.getRange(1,1,1,headers.length).setValues([headers]);
    last = 1;
  } else {
    var existingHeaders = sheet.getRange(1,1,1,headers.length).getDisplayValues()[0];
    if (headers.some(function(value,index){return String(existingHeaders[index]||'').trim()!==value;})) {
      throw new Error('WB_TRAFFIC_DAILY_VIEW_HEADER_MISMATCH');
    }
  }
  var shopNames = {};
  Object.keys(SMC_WB_TRAFFIC_PARTITIONS.shops).forEach(function(alias){
    shopNames[alias] = SMC_WB_TRAFFIC_PARTITIONS.shops[alias].name;
  });
  var grouped = {};
  function keyFor(row) {
    var alias = String(row[0] || '').trim();
    if (!shopNames[alias]) throw new Error('WB_TRAFFIC_DAILY_VIEW_UNKNOWN_STORE:' + alias);
    var day = row[1] instanceof Date && !isNaN(row[1].getTime())
      ? Utilities.formatDate(row[1], 'Europe/Moscow', 'yyyy-MM-dd')
      : String(row[1] || '').trim().slice(0,10);
    if (!/^\d{4}-\d{2}-\d{2}$/.test(day)) throw new Error('WB_TRAFFIC_DAILY_VIEW_INVALID_DATE:' + day);
    var key = alias + '|' + day;
    if (!grouped[key]) grouped[key] = {
      alias:alias, day:day, name:shopNames[alias], runId:String(runId),
      ads:{rows:0,values:{views:{sum:0,unknown:false},clicks:{sum:0,unknown:false},spend:{sum:0,unknown:false},orders:{sum:0,unknown:false}}},
      funnel:{rows:0,values:{opens:{sum:0,unknown:false},carts:{sum:0,unknown:false},orders:{sum:0,unknown:false},rub:{sum:0,unknown:false}}},
      organic:{rows:0,complete:true,values:{total:{sum:0,unknown:false},paid:{sum:0,unknown:false},clicks:{sum:0,unknown:false}}}
    };
    return grouped[key];
  }
  function addMetric(target, raw) {
    if (raw === '' || raw == null || typeof raw === 'boolean' || !isFinite(Number(raw))) {
      target.unknown = true;
      return;
    }
    target.sum += Number(raw);
  }
  (prepared.ads && prepared.ads.rows || []).forEach(function(row){
    var group=keyFor(row); group.ads.rows++;
    addMetric(group.ads.values.views,row[4]); addMetric(group.ads.values.clicks,row[5]);
    addMetric(group.ads.values.orders,row[7]); addMetric(group.ads.values.spend,row[8]);
  });
  (prepared.funnel && prepared.funnel.rows || []).forEach(function(row){
    var group=keyFor(row); group.funnel.rows++;
    addMetric(group.funnel.values.opens,row[4]); addMetric(group.funnel.values.carts,row[5]);
    addMetric(group.funnel.values.orders,row[6]); addMetric(group.funnel.values.rub,row[7]);
  });
  (prepared.organic && prepared.organic.rows || []).forEach(function(row){
    var group=keyFor(row); group.organic.rows++;
    if (String(row[10] || '') !== 'DERIVED_COMPLETE') group.organic.complete=false;
    addMetric(group.organic.values.total,row[4]); addMetric(group.organic.values.paid,row[5]);
    addMetric(group.organic.values.clicks,row[6]);
  });
  function metricValue(dataset, name) {
    var item=dataset.values[name];
    return dataset.rows && !item.unknown ? item.sum : '';
  }
  function sumOf(dataset,name) { return dataset.values[name].sum; }
  function allKnown(dataset) {
    return dataset.rows>0 && Object.keys(dataset.values).every(function(name){return !dataset.values[name].unknown;});
  }
  var freshRows=[];
  if (options.precomputedRows != null) {
    if (!Array.isArray(options.precomputedRows)) throw new Error('WB_TRAFFIC_DAILY_VIEW_PRECOMPUTED_NOT_ARRAY');
    var precomputedStore = String(options.storeName || '').trim();
    if (!precomputedStore) throw new Error('WB_TRAFFIC_DAILY_VIEW_PRECOMPUTED_STORE_REQUIRED');
    var seenPrecomputed = {};
    freshRows = options.precomputedRows.map(function(row,index) {
      if (!Array.isArray(row) || row.length !== headers.length) throw new Error('WB_TRAFFIC_DAILY_VIEW_PRECOMPUTED_WIDTH:' + index);
      var day = String(row[0] || '').trim().slice(0,10);
      if (!/^\d{4}-\d{2}-\d{2}$/.test(day)) throw new Error('WB_TRAFFIC_DAILY_VIEW_PRECOMPUTED_DATE:' + index);
      if (String(row[1] || '').trim() !== precomputedStore) throw new Error('WB_TRAFFIC_DAILY_VIEW_PRECOMPUTED_STORE:' + index);
      var key = precomputedStore + '|' + day;
      if (seenPrecomputed[key]) throw new Error('WB_TRAFFIC_DAILY_VIEW_PRECOMPUTED_DUPLICATE:' + key);
      seenPrecomputed[key] = true;
      [2,3,4,5,6,7,8,9,10,11,13,14,15].forEach(function(column) {
        var value = row[column];
        if (value === '' || value == null) return;
        if (typeof value === 'boolean' || !isFinite(Number(value))) throw new Error('WB_TRAFFIC_DAILY_VIEW_PRECOMPUTED_NUMBER:' + index + ':' + column);
      });
      if (['ПОЛНЫЙ РАСЧЁТ','РАСХОЖДЕНИЕ · значения скрыты','НЕПОЛНО · не считать нулём'].indexOf(String(row[12] || '')) < 0) {
        throw new Error('WB_TRAFFIC_DAILY_VIEW_PRECOMPUTED_STATUS:' + index);
      }
      if (String(row[16] || '') !== String(runId)) throw new Error('WB_TRAFFIC_DAILY_VIEW_PRECOMPUTED_RUN:' + index);
      var copied = row.slice();
      copied[0] = day;
      return copied;
    });
  } else {
  Object.keys(grouped).sort(function(a,b){return grouped[a].day.localeCompare(grouped[b].day)||grouped[a].name.localeCompare(grouped[b].name);}).forEach(function(key){
    var g=grouped[key], organicStatus='НЕПОЛНО · не считать нулём', paidClicks='', organicClicks='';
    var trafficMetricsKnown = g.ads.rows>0 && !g.ads.values.clicks.unknown &&
      g.funnel.rows>0 && !g.funnel.values.opens.unknown && g.organic.rows>0 && g.organic.complete &&
      !g.organic.values.total.unknown && !g.organic.values.paid.unknown && !g.organic.values.clicks.unknown;
    var reconciles = trafficMetricsKnown &&
      sumOf(g.ads,'clicks')===sumOf(g.organic,'paid') &&
      sumOf(g.funnel,'opens')===sumOf(g.organic,'total') &&
      sumOf(g.organic,'clicks')===sumOf(g.organic,'total')-sumOf(g.organic,'paid');
    if (reconciles) {
      organicStatus='ПОЛНЫЙ РАСЧЁТ'; paidClicks=sumOf(g.organic,'paid'); organicClicks=sumOf(g.organic,'clicks');
    } else if (g.ads.rows && g.funnel.rows && g.organic.rows && allKnown(g.ads) && allKnown(g.funnel) && allKnown(g.organic) && g.organic.complete) {
      organicStatus='РАСХОЖДЕНИЕ · значения скрыты';
    }
    freshRows.push([
      g.day,g.name,
      metricValue(g.ads,'views'),metricValue(g.ads,'clicks'),metricValue(g.ads,'spend'),metricValue(g.ads,'orders'),
      metricValue(g.funnel,'opens'),metricValue(g.funnel,'carts'),metricValue(g.funnel,'orders'),metricValue(g.funnel,'rub'),
      paidClicks,organicClicks,organicStatus,g.ads.rows,g.funnel.rows,g.organic.rows,String(runId)
    ]);
  });
  }
  if (options.storeName) freshRows = freshRows.filter(function(row){return String(row[1]||'')===options.storeName;});
  if (!freshRows.length && options.archiveOnly === true && last <= 1) return {ok:true,archive_only:true,fresh_rows:0,rows:0,added:0,updated:0,max_date:''};
  var byKey={};
  if (last>1) sheet.getRange(2,1,last-1,headers.length).getValues().forEach(function(row,index){
    if (String(row[0]||'').trim()==='') return;
    var date=row[0] instanceof Date && !isNaN(row[0].getTime())
      ? Utilities.formatDate(row[0], 'Europe/Moscow', 'yyyy-MM-dd') : String(row[0]).trim().slice(0,10);
    var name=String(row[1]||'').trim(), key=name+'|'+date;
    if (!date || !name || Object.prototype.hasOwnProperty.call(byKey,key)) throw new Error('WB_TRAFFIC_DAILY_VIEW_DUPLICATE_KEY:' + key);
    byKey[key]=row;
  });
  var added=0,updated=0;
  freshRows.forEach(function(row){
    var key=row[1]+'|'+row[0];
    if (Object.prototype.hasOwnProperty.call(byKey,key)) updated++; else added++;
    byKey[key]=row;
  });
  var allRows=Object.keys(byKey).map(function(key){return byKey[key];});
  allRows.sort(function(a,b){
    var ad=a[0] instanceof Date?Utilities.formatDate(a[0],'Europe/Moscow','yyyy-MM-dd'):String(a[0]||'');
    var bd=b[0] instanceof Date?Utilities.formatDate(b[0],'Europe/Moscow','yyyy-MM-dd'):String(b[0]||'');
    return ad.localeCompare(bd)||String(a[1]||'').localeCompare(String(b[1]||''));
  });
  if (options.writeArchive !== false) {
  var needed=allRows.length+1;
    if (sheet.getMaxRows()<needed) sheet.insertRowsAfter(sheet.getMaxRows(),needed-sheet.getMaxRows());
    sheet.getRange(2,1,allRows.length,headers.length).setValues(allRows);
    if (last>needed) sheet.getRange(needed+1,1,last-needed,headers.length).clearContent();
    SpreadsheetApp.flush();
    var check=sheet.getRange(2,1,allRows.length,headers.length).getValues(), seen={};
    check.forEach(function(row){var key=String(row[1]||'')+'|'+String(row[0]||'');if(seen[key])throw new Error('WB_TRAFFIC_DAILY_VIEW_READBACK_DUPLICATE:' + key);seen[key]=true;});
    if (check.length!==allRows.length) throw new Error('WB_TRAFFIC_DAILY_VIEW_READBACK_ROW_COUNT');
  }
  if (options.archiveOnly === true) return {ok:true,archive_only:true,fresh_rows:freshRows.length,rows:allRows.length,added:added,updated:updated,max_date:allRows.length?String(allRows[allRows.length-1][0]):'',reconciled:freshRows.filter(function(row){return row[12]==='ПОЛНЫЙ РАСЧЁТ';}).length};
  var liteTables = sellmonitorGithubWriteWbTrafficLiteTables_(allRows, options.storeName);
  var dashboard = options.storeName
    ? {ok:true,skipped:true,reason:'summary_scope_only',stores:[options.storeName]}
    : sellmonitorGithubWriteWbTrafficDashboards_(freshRows, runId, options.storeName);
  return {ok:liteTables.ok===true,fresh_rows:freshRows.length,no_daily_facts:freshRows.length===0,rows:allRows.length,added:added,updated:updated,max_date:allRows.length?String(allRows[allRows.length-1][0]):'',
    reconciled:freshRows.filter(function(row){return row[12]==='ПОЛНЫЙ РАСЧЁТ';}).length,lite_tables:liteTables,dashboard:dashboard};
}

function sellmonitorGithubWriteWbTrafficLiteTables_(dailyRows, onlyStore) {
  var stores = {
    'Саныч':'1kdeQ-N80Ul0WvekuPxM1QS8FRz0I3g5eOpsgcK0g9Ls',
    'Хозяюшка':'1H_UpoQa0ZsY3hnnS6R4cM2xXiqezFRYa0ZDLhVYfZbQ',
    'AIR':'1fc6fYdNojj02T597-T0pyCjpiWmhvAr45r1_jiyabIk'
  };
  var headers = ['Дата','Показы рекламы','Клики рекламы','Расход рекламы, ₽','Заказы рекламы',
    'Переходы в карточку','Добавления в корзину','Заказы воронки','Сумма заказов воронки, ₽',
    'Клики рекламы для сверки','Органические клики (расчёт)','Статус расчёта органики',
    'Строк рекламы','Строк воронки','Строк расчёта органики','Идентификатор сбора','Источники'];
  function dayValue(value) {
    return value instanceof Date && !isNaN(value.getTime())
      ? Utilities.formatDate(value,'Europe/Moscow','yyyy-MM-dd')
      : String(value || '').trim().slice(0,10);
  }
  var written = [], skipped = [], failed = [];
  Object.keys(stores).forEach(function(name) {
    if (onlyStore && name !== onlyStore) return;
    try {
      var book = SpreadsheetApp.openById(stores[name]);
      var sheet = book.getSheetByName('04_Трафик');
      if (!sheet) throw new Error('WB_TRAFFIC_LITE_TAB_MISSING:' + name);
      var rows = dailyRows.filter(function(row){return String(row[1]||'')===name;}).map(function(row) {
        return [dayValue(row[0]),row[2],row[3],row[4],row[5],row[6],row[7],row[8],row[9],
          row[10],row[11],row[12],row[13],row[14],row[15],row[16],
          'WB Promotion /adv/v3/fullstats; WB Analytics products/history'];
      });
      if (!rows.length) {
        skipped.push(name);
        return;
      }
      rows.sort(function(a,b){return String(a[0]).localeCompare(String(b[0]));});
      var seen = {};
      rows.forEach(function(row) {
        var key=String(row[0])+'|'+name;
        if (seen[key]) throw new Error('WB_TRAFFIC_LITE_DUPLICATE:' + key);
        seen[key]=true;
      });
      var last=sheet.getLastRow();
      var currentRows=last>1?sheet.getRange(2,1,last-1,headers.length).getValues():[];
      var currentByDate={};
      currentRows.forEach(function(row,index){
        var key=dayValue(row[0]);
        if(!key)return;
        if(Object.prototype.hasOwnProperty.call(currentByDate,key))throw new Error('WB_TRAFFIC_LITE_EXISTING_DUPLICATE:'+key);
        currentByDate[key]=row;
      });
      var archiveName='Архив_04Трафик_изменённые_факты';
      var archiveHeaders=['Архивировано','Причина'].concat(headers,['Контрольная сумма версии']);
      var materialIndexes=[0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,16];
      function versionSignature(row){
        return JSON.stringify(materialIndexes.map(function(index){return row[index]==null?'':row[index];}));
      }
      var archiveRows=[];
      rows.forEach(function(row){
        var prior=currentByDate[row[0]];
        if(prior&&versionSignature(prior)!==versionSignature(row)){
          archiveRows.push([new Date(),'Исправление факта после сверки источника'].concat(prior,versionSignature(prior)));
        }
      });
      if(archiveRows.length){
        var archive=book.getSheetByName(archiveName);
        if(!archive)archive=book.insertSheet(archiveName);
        if(!archive.getLastRow()){
          archive.getRange(1,1,1,archiveHeaders.length).setValues([archiveHeaders]);
        }else{
          var liveArchiveHeaders=archive.getRange(1,1,1,archiveHeaders.length).getDisplayValues()[0];
          if(archiveHeaders.some(function(value,index){return String(liveArchiveHeaders[index]||'').trim()!==value;}))
            throw new Error('WB_TRAFFIC_LITE_ARCHIVE_HEADER_MISMATCH:'+name);
        }
        var archiveLast=archive.getLastRow(), savedVersions={};
        if(archiveLast>1){
          archive.getRange(2,archiveHeaders.length,archiveLast-1,1).getDisplayValues().forEach(function(row){savedVersions[String(row[0]||'')]=true;});
        }
        var pendingArchive=archiveRows.filter(function(row){if(savedVersions[row[archiveHeaders.length-1]])return false;savedVersions[row[archiveHeaders.length-1]]=true;return true;});
        if(pendingArchive.length){
          archive.getRange(archiveLast+1,1,pendingArchive.length,archiveHeaders.length).setValues(pendingArchive);
          SpreadsheetApp.flush();
          var archivedCheck=archive.getRange(archiveLast+1,archiveHeaders.length,pendingArchive.length,1).getDisplayValues();
          if(archivedCheck.some(function(row,index){return String(row[0]||'')!==pendingArchive[index][archiveHeaders.length-1];}))
            throw new Error('WB_TRAFFIC_LITE_ARCHIVE_READBACK_MISMATCH:'+name);
        }
      }
      if (last) {
        var existingHeaders=sheet.getRange(1,1,1,headers.length).getDisplayValues()[0];
        if (headers.some(function(value,index){return String(existingHeaders[index]||'').trim()!==value;})) {
          throw new Error('WB_TRAFFIC_LITE_HEADER_MISMATCH:' + name);
        }
      } else {
        sheet.getRange(1,1,1,headers.length).setValues([headers]);
        last=1;
      }
      var needed=rows.length+1;
      if (sheet.getMaxRows()<needed) sheet.insertRowsAfter(sheet.getMaxRows(),needed-sheet.getMaxRows());
      if (last>1) sheet.getRange(2,1,last-1,headers.length).clearContent();
      sheet.getRange(2,1,rows.length,headers.length).setValues(rows);
      if (last>needed) sheet.getRange(needed+1,1,last-needed,headers.length).clearContent();
      sheet.setFrozenRows(1);
      sheet.getRange(2,4,rows.length,1).setNumberFormat('#,##0.00');
      sheet.getRange(2,9,rows.length,1).setNumberFormat('#,##0.00');
      SpreadsheetApp.flush();
      var check=sheet.getRange(2,1,rows.length,headers.length).getValues(), readSeen={};
      check.forEach(function(row) {
        var key=dayValue(row[0])+'|'+name;
        if (readSeen[key]) throw new Error('WB_TRAFFIC_LITE_READBACK_DUPLICATE:' + key);
        readSeen[key]=true;
      });
      if (check.length!==rows.length) throw new Error('WB_TRAFFIC_LITE_READBACK_ROW_COUNT:' + name);
      if (dayValue(check[check.length-1][0])!==rows[rows.length-1][0]) {
        throw new Error('WB_TRAFFIC_LITE_READBACK_MAX_DATE:' + name);
      }
      written.push(name);
    } catch(err) {
      failed.push(name+':'+String(err && err.message ? err.message : err).slice(0,240));
    }
  });
  return {ok:failed.length===0,written:written.length,stores:written,skipped:skipped,failed:failed};
}

function sellmonitorGithubWriteWbTrafficDashboards_(dailyRows, runId, onlyStore) {
  var stores = {
    'Саныч':'1-aBDZ7c5xfmVwwiNmUi9-DyfIANXmfiM5-Ti2_zg4zI',
    'AIR':'1SmsoG8zKx3hbTtTzS-zLekTFiWEQN8eIwHOxXq-5RHo',
    'Хозяюшка':'1cVT_H_e8a519k_Gtph6fALWnBbtBrAQ2Jb_gFO3bM64'
  };
  var expectedLabels = ['Трафик всего','Органика','Реклама · переходы','Реклама, ₽'];
  var runDate = Utilities.formatDate(new Date(runId), 'Europe/Moscow', 'yyyy-MM-dd');
  var byStore = {};
  dailyRows.forEach(function(row) {
    var day=row[0] instanceof Date && !isNaN(row[0].getTime())
      ? Utilities.formatDate(row[0],'Europe/Moscow','yyyy-MM-dd')
      : String(row[0]||'').trim().slice(0,10);
    if (day===runDate) byStore[String(row[1])] = row;
  });
  var written = 0, missing = [], failed = [];
  Object.keys(stores).forEach(function(name) {
    if (onlyStore && name !== onlyStore) return;
    try {
      var ss = SpreadsheetApp.openById(stores[name]);
      var sheet = ss.getSheetByName('00_Дашборд');
      if (!sheet) throw new Error('WB_TRAFFIC_DASHBOARD_TAB_MISSING:' + name);
      var labels = sheet.getRange(7,9,4,1).getDisplayValues().map(function(row){return String(row[0]||'').trim();});
      if (labels.some(function(label,index){return label!==expectedLabels[index];})) {
        throw new Error('WB_TRAFFIC_DASHBOARD_LABEL_MISMATCH:' + name);
      }
      var row = byStore[name] || null;
      var complete = row && row[12] === 'ПОЛНЫЙ РАСЧЁТ';
      function valueOrDash(value) { return value === '' || value == null ? '—' : Number(value); }
      var values = [
        [row ? valueOrDash(row[6]) : '—', row ? 'WB Analytics · ' + runDate : 'Нет строк за ' + runDate],
        [complete ? valueOrDash(row[11]) : '—', complete ? 'Расчёт из воронки минус реклама · ' + runDate : 'Неполные исходные клики · ' + runDate],
        [row ? valueOrDash(row[3]) : '—', row ? 'WB /adv/v3/fullstats · ' + runDate : 'Нет строк за ' + runDate],
        [row ? valueOrDash(row[4]) : '—', row ? 'WB /adv/v3/fullstats · ' + runDate : 'Нет строк за ' + runDate]
      ];
      sheet.getRange(7,10,4,2).setValues(values);
      SpreadsheetApp.flush();
      var readback = sheet.getRange(7,10,4,2).getValues();
      if (JSON.stringify(readback)!==JSON.stringify(values)) throw new Error('WB_TRAFFIC_DASHBOARD_READBACK_MISMATCH:' + name);
      written++;
      if (!row || !complete) missing.push(name);
    } catch(err) {
      failed.push(name+':'+String(err && err.message ? err.message : err).slice(0,240));
    }
  });
  return {ok:missing.length===0 && failed.length===0,written:written,date:runDate,partial_stores:missing,failed_stores:failed};
}

function sellmonitorGithubPublishWbFinance_(body) {
  body = body || {};
  var shopName = String(body.shop || '').trim();
  var shops = {
    'Саныч': {id:'sanych_wb', sourceShop:'AP'},
    'Хозяюшка': {id:'hozyayushka_wb', sourceShop:'YV'},
    'AIR': {id:'air_wb', sourceShop:'AA'}
  };
  var shopCfg = shops[shopName];
  if (!shopCfg) throw new Error('WB_FINANCE_UNKNOWN_SHOP');
  var month = String(body.month || '').trim();
  if (!/^\d{4}-\d{2}$/.test(month) || Number(month.slice(5)) < 1 || Number(month.slice(5)) > 12) {
    throw new Error('WB_FINANCE_MONTH_INVALID');
  }
  var rows = body.rows;
  if (!Array.isArray(rows) || !rows.length || rows.length > 10000) throw new Error('WB_FINANCE_BATCH_INVALID');
  var runId = String(body.sync_run_id || '').trim();
  if (!runId || isNaN(new Date(runId).getTime())) throw new Error('WB_FINANCE_RUN_ID_INVALID');
  var columns = [
    'Магазин','Report ID','Отчет с','Отчет по','Дата создания','RR Date','RRD ID','Тип документа','Операция','Категория',
    'Штраф, ₽','Удержание, ₽','Хранение, ₽','Приемка, ₽','Доплата, ₽','Всего списаний, ₽','Причина WB','nmId','Артикул продавца','Товар',
    'Склад','Схема','Order ID','Order UID','SRID','Sticker ID','Дата заказа','Дата продажи','Логистика, ₽','К выплате, ₽','Статус проверки','Комментарий',
    'sync_run_id','record_key'
  ];
  var dateIndex = 5, idIndex = 6;
  var inputByKey = {}, normalized = [];
  rows.forEach(function(sourceRow, i) {
    if (!Array.isArray(sourceRow) || sourceRow.length !== 32 || String(sourceRow[0] || '').trim() !== shopName) {
      throw new Error('WB_FINANCE_ROW_INVALID:' + i);
    }
    var day = String(sourceRow[dateIndex] || '').trim().slice(0, 10);
    var rrdId = String(sourceRow[idIndex] || '').trim();
    if (!/^\d{4}-\d{2}-\d{2}$/.test(day) || day.slice(0, 7) !== month || !/^\d+$/.test(rrdId)) {
      throw new Error('WB_FINANCE_ID_OR_DATE_INVALID:' + i);
    }
    var key = [shopCfg.id, day, rrdId].join('|');
    if (Object.prototype.hasOwnProperty.call(inputByKey, key)) throw new Error('WB_FINANCE_DUPLICATE_INPUT:' + key);
    var row = sourceRow.map(function(value) { return value == null ? '' : value; });
    [10,11,12,13,14,15,28,29].forEach(function(col) {
      if (row[col] === '') return;
      var value = Number(String(row[col]).replace(',', '.'));
      if (!isFinite(value)) throw new Error('WB_FINANCE_NUMBER_INVALID:' + i + ':' + col);
      row[col] = value;
    });
    row.forEach(function(value, col) {
      if (typeof value !== 'string' || [10,11,12,13,14,15,28,29].indexOf(col) >= 0) return;
      if (/^[=+\-@]/.test(value)) row[col] = "'" + value;
    });
    row[dateIndex] = day;
    row.push(runId, key);
    inputByKey[key] = row;
    normalized.push(row);
  });

  var partId = 'WB-' + shopCfg.id + '-finance-charges-' + month + '-v1';
  var props = PropertiesService.getScriptProperties();
  var propKey = 'WB_FINANCE_PART_V1_' + shopCfg.id + '_' + month;
  var fileId = String(props.getProperty(propKey) || '').trim();
  var registry = SpreadsheetApp.openById(SMC_WB_TRAFFIC_PARTITIONS.registryId);
  var registrySheet = registry.getSheetByName(SMC_WB_TRAFFIC_PARTITIONS.registrySheet);
  if (!registrySheet) throw new Error('WB_FINANCE_PART_REGISTRY_TAB_MISSING');
  if (!fileId && registrySheet.getLastRow() > 1) {
    var matches = [];
    registrySheet.getRange(2, 1, registrySheet.getLastRow() - 1, 10).getDisplayValues().forEach(function(row) {
      if (String(row[0] || '').trim() === partId) matches.push(String(row[9] || '').trim());
    });
    if (matches.length > 1) throw new Error('WB_FINANCE_PART_REGISTRY_DUPLICATE:' + partId);
    if (matches.length === 1) {
      if (!matches[0]) throw new Error('WB_FINANCE_PART_FILE_ID_MISSING:' + partId);
      fileId = matches[0];
    }
  }
  var ss;
  if (fileId) ss = SpreadsheetApp.openById(fileId);
  else {
    ss = SpreadsheetApp.create('WB — ' + shopName + ' — начисления — ' + month);
    fileId = ss.getId();
    props.setProperty(propKey, fileId);
  }
  var sheet = ss.getSheetByName('facts');
  if (!sheet) {
    var existingSheets = ss.getSheets();
    sheet = existingSheets.length ? existingSheets[0] : ss.insertSheet('facts');
    if (sheet.setName) sheet.setName('facts');
  }
  if (sheet.getMaxColumns() < columns.length) sheet.insertColumnsAfter(sheet.getMaxColumns(), columns.length - sheet.getMaxColumns());
  if (!sheet.getLastRow()) sheet.getRange(1, 1, 1, columns.length).setValues([columns]);
  var liveHeaders = sheet.getRange(1, 1, 1, columns.length).getDisplayValues()[0];
  if (columns.some(function(h, i) { return String(liveHeaders[i] || '').trim() !== h; })) throw new Error('WB_FINANCE_PART_HEADER_MISMATCH:' + partId);

  var oldLast = sheet.getLastRow(), byKey = {}, added = 0, updated = 0;
  if (oldLast > 1) {
    sheet.getRange(2, 1, oldLast - 1, columns.length).getValues().forEach(function(row) {
      if (!row[0]) return;
      var key = String(row[columns.length - 1] || '').trim();
      if (!key || Object.prototype.hasOwnProperty.call(byKey, key)) throw new Error('WB_FINANCE_PART_DUPLICATE_EXISTING_KEY:' + partId + ':' + key);
      byKey[key] = row;
    });
  }
  normalized.forEach(function(row) {
    var key = String(row[columns.length - 1]);
    if (Object.prototype.hasOwnProperty.call(byKey, key)) updated++;
    else added++;
    byKey[key] = row;
  });
  var allRows = Object.keys(byKey).map(function(key) { return byKey[key]; });
  allRows.sort(function(a,b) {
    return String(a[dateIndex] || '').localeCompare(String(b[dateIndex] || '')) ||
      String(a[columns.length - 1] || '').localeCompare(String(b[columns.length - 1] || ''));
  });
  if (sheet.getMaxRows() < allRows.length + 1) sheet.insertRowsAfter(sheet.getMaxRows(), allRows.length + 1 - sheet.getMaxRows());
  if (allRows.length) sheet.getRange(2, 1, allRows.length, columns.length).setValues(allRows);
  if (oldLast > allRows.length + 1) sheet.getRange(allRows.length + 2, 1, oldLast - allRows.length - 1, columns.length).clearContent();
  var dates = allRows.map(function(row) { return String(row[dateIndex] || ''); }).filter(Boolean).sort();
  var monthParts = month.split('-');
  var monthEnd = Utilities.formatDate(new Date(Date.UTC(Number(monthParts[0]), Number(monthParts[1]), 0)), 'GMT', 'yyyy-MM-dd');
  var checksum = sellmonitorGithubTrafficPartChecksum_(allRows);
  sellmonitorGithubTrafficPartRegistry_([
    partId,'WB',shopCfg.id,'finance_charges','normalized_source_rows',month + '-01',monthEnd,1,
    'finance-charge-month-v1',fileId,'https://docs.google.com/spreadsheets/d/' + fileId + '/edit',
    'WB Finance API /api/finance/v1/sales-reports/detailed','WB_OFFICIAL_FINANCE_API',runId,new Date(),
    allRows.length,Object.keys(byKey).length,dates[0] || '',dates[dates.length - 1] || '',checksum,'OK','PASS','',''
  ]);
  SpreadsheetApp.flush();
  return {ok:true,action:'publish_wb_finance',part_id:partId,shop_id:shopCfg.id,month:month,file_id:fileId,
    url:'https://docs.google.com/spreadsheets/d/' + fileId + '/edit',written:normalized.length,
    added:added,updated:updated,distinct_keys:normalized.length,min_date:dates[0] || '',max_date:dates[dates.length - 1] || '',qc_status:'PASS'};
}


function sellmonitorGithubPublishWbPenaltySnapshot_(body) {
  body = body || {};
  var targetId = '1Hj6jxJGN1cnJQbcsov0P2kbysNkO_EbERRgNMcajd74';
  var headers = [
    'Магазин','Дата штрафа','Причина / категория','Артикул продавца','Артикул WB',
    'Сумма штрафа, ₽','Стикер МП','Что было заказано','Что пришло',
    'Фото 1','Фото 2','Фото 3','Фото 4','Фото 5','Order ID','Report ID','RRD ID'
  ];
  var input = body.rows;
  var runId = String(body.sync_run_id || '').trim();
  if (!runId || isNaN(new Date(runId).getTime())) throw new Error('WB_PENALTY_RUN_ID_INVALID');
  if (!Array.isArray(input) || !input.length || input.length > 25000) throw new Error('WB_PENALTY_ROWS_INVALID');
  var seen = {}, rows = [];
  input.forEach(function(sourceRow, i) {
    if (!Array.isArray(sourceRow) || sourceRow.length !== headers.length) throw new Error('WB_PENALTY_ROW_WIDTH:' + i);
    var row = sourceRow.map(function(v) { return v == null ? '' : v; });
    if (!String(row[0] || '').trim()) throw new Error('WB_PENALTY_SHOP_MISSING:' + i);
    var dateText = String(row[1] || '').trim().slice(0, 10);
    var date;
    if (/^\d{4}-\d{2}-\d{2}$/.test(dateText)) {
      date = new Date(dateText + 'T12:00:00+03:00');
    } else if (/^\d{2}\.\d{2}\.\d{4}$/.test(dateText)) {
      var p = dateText.split('.');
      date = new Date(p[2] + '-' + p[1] + '-' + p[0] + 'T12:00:00+03:00');
    }
    if (!(date instanceof Date) || isNaN(date.getTime())) throw new Error('WB_PENALTY_DATE_INVALID:' + i);
    row[1] = date;
    [4,5,14,16].forEach(function(col) {
      if (row[col] === '') return;
      var n = Number(String(row[col]).replace(',', '.'));
      if (!isFinite(n)) throw new Error('WB_PENALTY_NUMBER_INVALID:' + i + ':' + col);
      row[col] = n;
    });
    var key = [row[0], dateText, row[15], row[16]].join('|');
    if (seen[key]) throw new Error('WB_PENALTY_DUPLICATE_KEY:' + i);
    seen[key] = true;
    rows.push(row);
  });
  var ss = SpreadsheetApp.openById(targetId);
  var sheet = ss.getSheetByName('_FF_14д');
  if (!sheet) throw new Error('WB_PENALTY_TARGET_TAB_MISSING');
  if (sheet.getMaxColumns() < headers.length) sheet.insertColumnsAfter(sheet.getMaxColumns(), headers.length - sheet.getMaxColumns());
  var needed = rows.length + 1;
  if (sheet.getMaxRows() < needed) sheet.insertRowsAfter(sheet.getMaxRows(), needed - sheet.getMaxRows());
  var oldLast = sheet.getLastRow();
  var headerCell = sheet.getRange(1, 1);
  if (headerCell.getFormula()) headerCell.clearContent();
  sheet.getRange(1, 1, 1, headers.length).setValues([headers]);
  sheet.getRange(2, 1, rows.length, headers.length).setValues(rows);
  if (oldLast > needed) sheet.getRange(needed + 1, 1, oldLast - needed, headers.length).clearContent();
  var dates = rows.map(function(row) { return row[1]; }).sort(function(a,b) { return a.getTime() - b.getTime(); });
  return {ok:true,action:'publish_wb_penalties_snapshot',spreadsheet_id:targetId,sheet:'_FF_14д',
    written:rows.length,distinct_keys:Object.keys(seen).length,min_date:Utilities.formatDate(dates[0],ss.getSpreadsheetTimeZone(),'yyyy-MM-dd'),
    max_date:Utilities.formatDate(dates[dates.length - 1],ss.getSpreadsheetTimeZone(),'yyyy-MM-dd'),sync_run_id:runId};
}

function sellmonitorGithubNormalizeWbTrafficRows_(dataset, inputRows, observedAt) {
  var cfg = SMC_WB_TRAFFIC.DATASETS[dataset];
  if (!cfg) throw new Error('WB_TRAFFIC_UNKNOWN_DATASET');
  if (!Array.isArray(inputRows)) throw new Error('WB_TRAFFIC_ROWS_NOT_ARRAY');
  if (inputRows.length > 25000) throw new Error('WB_TRAFFIC_ROW_LIMIT_EXCEEDED');
  var stores = {ap:true, aa:true, yv:true};
  var seen = {};
  var maxDate = '';
  var rows = inputRows.map(function(sourceRow, rowIndex) {
    if (!Array.isArray(sourceRow) || sourceRow.length !== cfg.source_width) {
      throw new Error('WB_TRAFFIC_INVALID_ROW_WIDTH:' + rowIndex);
    }
    var storeId = String(sourceRow[0] || '').trim();
    var day = String(sourceRow[1] || '').trim();
    var nmId = Number(sourceRow[dataset === 'ads' ? 3 : 2]);
    if (!stores[storeId]) throw new Error('WB_TRAFFIC_UNKNOWN_STORE:' + rowIndex);
    if (!/^\d{4}-\d{2}-\d{2}$/.test(day) ||
        isNaN(new Date(day + 'T00:00:00Z').getTime()) ||
        new Date(day + 'T00:00:00Z').toISOString().slice(0, 10) !== day) {
      throw new Error('WB_TRAFFIC_INVALID_DATE:' + rowIndex);
    }
    if (!isFinite(nmId) || nmId <= 0 || Math.floor(nmId) !== nmId) {
      throw new Error('WB_TRAFFIC_INVALID_NMID:' + rowIndex);
    }
    var actualSource = dataset === 'ads' ? String(sourceRow[10] || '')
      : dataset === 'organic' ? String(sourceRow[7] || '') + ' + ' + String(sourceRow[8] || '')
      : String(sourceRow[8] || '');
    if (actualSource !== cfg.source) {
      throw new Error('WB_TRAFFIC_SOURCE_MISMATCH:' + rowIndex);
    }
    if (cfg.quality && String(sourceRow[11] || '') !== cfg.quality) {
      throw new Error('WB_TRAFFIC_QUALITY_MISMATCH:' + rowIndex);
    }
    var key = cfg.key_columns.map(function(i) { return String(sourceRow[i] || ''); }).join('|');
    if (seen[key]) throw new Error('WB_TRAFFIC_DUPLICATE_KEY:' + key);
    seen[key] = true;
    var row = sourceRow.slice();
    var textColumn = dataset === 'ads' ? 12 : 3;
    var textValue = String(row[textColumn] == null ? '' : row[textColumn]);
    if (/^[=+\-@]/.test(textValue)) row[textColumn] = "'" + textValue;
    cfg.numeric_columns.forEach(function(i) {
      if (row[i] === '' || row[i] == null) {
        row[i] = '';
      } else {
        var n = Number(row[i]);
        if (!isFinite(n)) throw new Error('WB_TRAFFIC_INVALID_NUMBER:' + rowIndex + ':' + i);
        row[i] = n;
      }
    });
    row[1] = new Date(day + 'T12:00:00+03:00');
    row.push(new Date(observedAt), 'SOURCE_OK');
    if (day > maxDate) maxDate = day;
    return row;
  });
  return {rows: rows, max_date: maxDate};
}


function sellmonitorGithubNormalizeWbPollRows_(dataset, inputRows, loadedAt) {
  var cfg = SMC_WB_TRAFFIC.DATASETS[dataset];
  if (!cfg || !cfg.grain) throw new Error('WB_TRAFFIC_UNKNOWN_POLL_DATASET');
  if (!Array.isArray(inputRows)) throw new Error('WB_TRAFFIC_POLL_ROWS_NOT_ARRAY');
  if (inputRows.length > 25000) throw new Error('WB_TRAFFIC_POLL_ROW_LIMIT_EXCEEDED');
  var stores = {ap:true, aa:true, yv:true}, seen = {};
  var loadDate = new Date(loadedAt);
  if (!(loadDate instanceof Date) || isNaN(loadDate.getTime())) throw new Error('WB_TRAFFIC_POLL_LOAD_TIME_INVALID');
  var rows = inputRows.map(function(sourceRow, rowIndex) {
    if (!Array.isArray(sourceRow) || sourceRow.length !== cfg.source_width) throw new Error('WB_TRAFFIC_POLL_INVALID_ROW_WIDTH:' + rowIndex);
    var storeId = String(sourceRow[0] || '').trim();
    var observed = new Date(String(sourceRow[1] || ''));
    var day = String(sourceRow[2] || '').trim();
    if (!stores[storeId]) throw new Error('WB_TRAFFIC_POLL_UNKNOWN_STORE:' + rowIndex);
    if (!(observed instanceof Date) || isNaN(observed.getTime())) throw new Error('WB_TRAFFIC_POLL_INVALID_OBSERVED_AT:' + rowIndex);
    if (!/^\d{4}-\d{2}-\d{2}$/.test(day) || isNaN(new Date(day + 'T00:00:00Z').getTime()) ||
        new Date(day + 'T00:00:00Z').toISOString().slice(0,10) !== day) throw new Error('WB_TRAFFIC_POLL_INVALID_DATE:' + rowIndex);
    if (String(sourceRow[cfg.source_column] || '') !== cfg.source) throw new Error('WB_TRAFFIC_POLL_SOURCE_MISMATCH:' + rowIndex);
    if (String(sourceRow[cfg.grain_column] || '') !== cfg.grain) throw new Error('WB_TRAFFIC_POLL_GRAIN_MISMATCH:' + rowIndex);
    var key = storeId + '|' + observed.toISOString() + '|' + day;
    if (seen[key]) throw new Error('WB_TRAFFIC_POLL_DUPLICATE_KEY:' + key);
    seen[key] = true;
    var row = sourceRow.slice();
    cfg.numeric_columns.forEach(function(i) {
      if (row[i] === '' || row[i] == null) row[i] = '';
      else {
        var n = Number(row[i]);
        if (!isFinite(n)) throw new Error('WB_TRAFFIC_POLL_INVALID_NUMBER:' + rowIndex + ':' + i);
        row[i] = n;
      }
    });
    row[1] = observed;
    row[2] = new Date(day + 'T12:00:00+03:00');
    row.push(loadDate, 'SOURCE_OK');
    return row;
  });
  return {rows:rows};
}


function sellmonitorGithubAppendWbPollRows_(ss, dataset, table) {
  var cfg = SMC_WB_TRAFFIC.DATASETS[dataset];
  var sh = ss.getSheetByName(cfg.sheet);
  if (!sh) sh = ss.insertSheet(cfg.sheet);
  if (sh.getMaxColumns() < cfg.width) sh.insertColumnsAfter(sh.getMaxColumns(), cfg.width - sh.getMaxColumns());
  if (sh.getMaxRows() < table.rows.length + 1) sh.insertRowsAfter(sh.getMaxRows(), table.rows.length + 1 - sh.getMaxRows());
  if (!sh.getLastRow()) sh.getRange(1,1,1,cfg.width).setValues([cfg.headers]);
  var headers = sh.getRange(1,1,1,cfg.width).getDisplayValues()[0];
  if (cfg.headers.some(function(h,i){return String(headers[i]||'').trim() !== h;})) throw new Error('WB_TRAFFIC_POLL_HEADER_MISMATCH:' + cfg.sheet);

  function iso_(value) {
    var d = value instanceof Date ? value : new Date(value);
    if (!(d instanceof Date) || isNaN(d.getTime())) throw new Error('WB_TRAFFIC_POLL_EXISTING_TIME_INVALID');
    return d.toISOString();
  }
  function day_(value) {
    if (value instanceof Date && !isNaN(value.getTime())) return Utilities.formatDate(value, ss.getSpreadsheetTimeZone(), 'yyyy-MM-dd');
    var s = String(value||'').trim();
    if (/^\d{4}-\d{2}-\d{2}$/.test(s)) return s;
    var d = new Date(s);
    if (!(d instanceof Date) || isNaN(d.getTime())) throw new Error('WB_TRAFFIC_POLL_EXISTING_DATE_INVALID');
    return Utilities.formatDate(d, ss.getSpreadsheetTimeZone(), 'yyyy-MM-dd');
  }
  function key_(row) { return String(row[0]||'') + '|' + iso_(row[1]) + '|' + day_(row[2]); }

  var last = sh.getLastRow(), existing = {};
  if (last > 1) {
    sh.getRange(2,1,last-1,3).getValues().forEach(function(row,i){
      if (!row[0]) return;
      var key = key_(row);
      if (existing[key]) throw new Error('WB_TRAFFIC_POLL_DUPLICATE_EXISTING_KEY:' + key);
      existing[key] = i + 2;
    });
  }
  var additions = [], updates = [];
  table.rows.forEach(function(row){
    var key = key_(row), target = existing[key];
    if (target) updates.push({row:target,values:row});
    else additions.push(row);
  });
  updates.forEach(function(x){sh.getRange(x.row,1,1,cfg.width).setValues([x.values]);});
  if (additions.length) {
    var first = sh.getLastRow() + 1;
    if (sh.getMaxRows() < first + additions.length - 1) sh.insertRowsAfter(sh.getMaxRows(), first + additions.length - 1 - sh.getMaxRows());
    sh.getRange(first,1,additions.length,cfg.width).setValues(additions);
    if (sh.getRange(first,2,additions.length,1).setNumberFormat) {
      sh.getRange(first,2,additions.length,1).setNumberFormat('dd.MM.yyyy HH:mm:ss');
      sh.getRange(first,3,additions.length,1).setNumberFormat('dd.MM.yyyy');
      sh.getRange(first,cfg.width-1,additions.length,1).setNumberFormat('dd.MM.yyyy HH:mm:ss');
    }
  }
  return {written:table.rows.length,added:additions.length,updated:updates.length,skipped:false};
}



// JEM-independent, factual WB advertising search-cluster history.
// These are advertising clusters, NEVER organic search rank or individual queries.

// Source-authenticated current campaign/SKU settings, stored separately
// from historical day-grained advertising metrics.

// Dedicated per-cabinet view. Ads clusters are query families, NOT measured
// organic ranking; the separate organic position sheet remains untouched.
// Batch adjacent updates to avoid thousands of individual spreadsheet RPCs.
function sellmonitorGithubBatchUpdateRows_(sh, updates, width) {
  if (!updates.length) return;
  updates.sort(function(a,b){ return a.index-b.index; });
  var first=0, buffer=[];
  function flush_() {
    if(buffer.length) sh.getRange(first,1,buffer.length,width).setValues(buffer);
    buffer=[];
  }
  updates.forEach(function(entry) {
    if(buffer.length && entry.index !== first+buffer.length) flush_();
    if(!buffer.length) first=entry.index;
    buffer.push(entry.row);
  });
  flush_();
}

function sellmonitorGithubClearDuplicateRows_(sh, rowIndexes, width) {
  var rows = Array.from(new Set(rowIndexes)).sort(function(a,b){return a-b;});
  if (!rows.length) return 0;
  var first = rows[0], previous = rows[0];
  function clear_(start, end) {
    sh.getRange(start, 1, end - start + 1, width).clearContent();
  }
  for (var i = 1; i < rows.length; i++) {
    if (rows[i] !== previous + 1) {
      clear_(first, previous);
      first = rows[i];
    }
    previous = rows[i];
  }
  clear_(first, previous);
  return rows.length;
}

function sellmonitorGithubPublishCabinetAdClusters_(shop, records, observedAt) {
  var bookId = ({
    aa:'1SmsoG8zKx3hbTtTzS-zLekTFiWEQN8eIwHOxXq-5RHo',
    yv:'1cVT_H_e8a519k_Gtph6fALWnBbtBrAQ2Jb_gFO3bM64'
  })[shop];
  if (!bookId) return {ok:true,skipped:true,reason:'central_hub_is_sanych_view'};
  var ss=SpreadsheetApp.openById(bookId);
  var headers=['Дата','Артикул WB','Кампания','Рекламный поисковый кластер',
    'Показы','Клики','Корзины','Заказы','Единиц','Расход ₽',
    'Средняя позиция рекламы','CTR %','CPC ₽','CPM ₽',
    'Источник','Достоверность','Время наблюдения'];
  var name='18_WB_Рекламные_кластеры';
  var sh=ss.getSheetByName(name);
  if (!sh) sh=ss.insertSheet(name);
  if (sh.getMaxColumns()<headers.length)
    sh.insertColumnsAfter(sh.getMaxColumns(),headers.length-sh.getMaxColumns());
  if (!sh.getLastRow()) sh.getRange(1,1,1,headers.length).setValues([headers]);
  var current=sh.getRange(1,1,1,headers.length).getDisplayValues()[0];
  if (headers.some(function(h,i){return h!==String(current[i]||'');}))
    throw new Error('WB_CABINET_CLUSTER_HEADER_MISMATCH');
  function key(row){return [row[0],row[1],row[2],String(row[3]||'').toLowerCase()].join('|');}
  var seen={}, duplicateRowsByKey={};
  if (sh.getLastRow()>1) sh.getRange(2,1,sh.getLastRow()-1,4).getValues().forEach(function(row,i){
    if (!row[0]) return;
    var rawDate=row[0] instanceof Date ? Utilities.formatDate(row[0],ss.getSpreadsheetTimeZone(),'yyyy-MM-dd') : String(row[0]||'');
    var k=[rawDate,row[1],row[2],String(row[3]||'').toLowerCase()].join('|');
    if (Object.prototype.hasOwnProperty.call(seen,k)) {
      if (!duplicateRowsByKey[k]) duplicateRowsByKey[k]=[];
      duplicateRowsByKey[k].push(i+2);
      return;
    }
    seen[k]=i+2;
  });
  var updates=[],adds=[],incomingKeys={};
  records.forEach(function(r){
    // Source CSV order: shop, date, advert, nm, query, 10 stats, source, quality.
    var day=String(r[1]||'');
    var cluster=String(r[4]||'');
    var values=[day,Number(r[3]),Number(r[2]),cluster].concat(r.slice(5,17)).concat([observedAt]);
    if (/^[=+\-@]/.test(cluster)) values[3]="'"+cluster;
    var k=key([day,Number(r[3]),Number(r[2]),cluster]);
    incomingKeys[k]=true;
    var found=seen[k];
    if(found) updates.push({index:found,row:values});
    else adds.push(values);
  });
  sellmonitorGithubBatchUpdateRows_(sh,updates,headers.length);
  var duplicateRowsToClear=[];
  Object.keys(incomingKeys).forEach(function(k){
    (duplicateRowsByKey[k]||[]).forEach(function(index){duplicateRowsToClear.push(index);});
  });
  var duplicatesRemoved=sellmonitorGithubClearDuplicateRows_(sh,duplicateRowsToClear,headers.length);
  if (adds.length){
    var first=sh.getLastRow()+1;
    if (sh.getMaxRows()<first+adds.length-1)
      sh.insertRowsAfter(sh.getMaxRows(),first+adds.length-1-sh.getMaxRows());
    sh.getRange(first,1,adds.length,headers.length).setValues(adds);
  }
  sh.setFrozenRows(1);
  SpreadsheetApp.flush();
  return {ok:true,shop:shop,written:records.length,added:adds.length,updated:updates.length,
          duplicate_rows_removed:duplicatesRemoved};
}

// Feed existing AIR/Hozyushka campaign diagnostics using official facts,
// never interpreting advertising settings as JEM search position statistics.
function sellmonitorGithubPublishClientCampaignCatalog_(shop, input, observedAt) {
  var id=({aa:'1SmsoG8zKx3hbTtTzS-zLekTFiWEQN8eIwHOxXq-5RHo',
    yv:'1cVT_H_e8a519k_Gtph6fALWnBbtBrAQ2Jb_gFO3bM64'})[shop];
  if(!id)return {ok:true,skipped:true};
  var ss=SpreadsheetApp.openById(id);
  var headers=['advert_id','nmId','seller_article','campaign_name','status',
    'payment_type','bid_type','search','recommendations','bid_search_kopecks',
    'bid_recommendations_kopecks','subject_id','subject_name','updated_at',
    'catalog_status','loaded_at'];
  var sh=ss.getSheetByName('90_RAW_ads_campaigns');
  if(!sh)sh=ss.insertSheet('90_RAW_ads_campaigns');
  if(sh.getMaxColumns()<headers.length)
    sh.insertColumnsAfter(sh.getMaxColumns(),headers.length-sh.getMaxColumns());
  if(!sh.getLastRow())sh.getRange(1,1,1,headers.length).setValues([headers]);
  var actual=sh.getRange(1,1,1,headers.length).getDisplayValues()[0];
  if(headers.some(function(h,i){return String(actual[i]||'')!==h;}))
    throw new Error('WB_CLIENT_CAMPAIGN_HEADER_MISMATCH');
  var previous={},positions={};
  if(sh.getLastRow()>1)sh.getRange(2,1,sh.getLastRow()-1,headers.length).getValues().forEach(function(r,i){
    if(!r[0]||!r[1])return;
    var key=String(r[0])+'|'+String(r[1]);
    if(Object.prototype.hasOwnProperty.call(previous,key))throw new Error('WB_CLIENT_CAMPAIGN_EXISTING_DUPLICATE');
    previous[key]=r;positions[key]=i+2;
  });
  var newKeys={},updates=[],adds=[];
  (input||[]).forEach(function(row,i){
    if(!Array.isArray(row)||row.length!==16||row[0]!==shop)throw new Error('WB_CLIENT_CAMPAIGN_BAD_ROW:'+i);
    var ad=Number(row[1]),nm=Number(row[2]),status=Number(row[4]);
    if(!Number.isSafeInteger(ad)||ad<=0||!Number.isSafeInteger(nm)||nm<=0||!Number.isInteger(status))
      throw new Error('WB_CLIENT_CAMPAIGN_BAD_ID:'+i);
    var key=ad+'|'+nm;
    if(Object.prototype.hasOwnProperty.call(newKeys,key))throw new Error('WB_CLIENT_CAMPAIGN_DUPLICATE:'+i);
    newKeys[key]=true;
    var old=previous[key]||[];
    var name=String(row[3]||'');
    if(/^[=+@-]/.test(name))name="'"+name;
    function bid(v){if(v==null||v==='')return '';var n=Number(v);if(!Number.isSafeInteger(n)||n<0)throw new Error('WB_CLIENT_CAMPAIGN_BAD_BID');return n;}
    var r=[ad,nm,old[2]||'',name,status,String(row[6]||''),String(row[5]||''),
      row[10]===true||String(row[10]).toLowerCase()==='true',
      row[11]===true||String(row[11]).toLowerCase()==='true',
      bid(row[8]),bid(row[9]),old[11]||'',old[12]||'',String(row[12]||''),
      'LIVE_WB_V2_NO_JEM',new Date(observedAt)];
    if(positions[key])updates.push({index:positions[key],row:r});else adds.push(r);
  });
  sellmonitorGithubBatchUpdateRows_(sh,updates,headers.length);
  if(adds.length){
    var start=sh.getLastRow()+1;
    if(sh.getMaxRows()<start+adds.length-1)
      sh.insertRowsAfter(sh.getMaxRows(),start+adds.length-1-sh.getMaxRows());
    sh.getRange(start,1,adds.length,headers.length).setValues(adds);
  }
  SpreadsheetApp.flush();
  return {ok:true,written:input.length,updated:updates.length,added:adds.length};
}

function sellmonitorGithubPublishCampaignSettings_(body) {
  body = body || {};
  var shop = String(body.shop || '');
  if (['ap','aa','yv'].indexOf(shop) < 0 ||
      body.source !== 'WB /api/advert/v2/adverts' ||
      body.quality !== 'FACT_CURRENT_CAMPAIGN_SKU_SETTINGS') {
    throw new Error('WB_CAMPAIGN_SETTINGS_PROVENANCE_INVALID');
  }
  var input = body.rows;
  if (!Array.isArray(input) || input.length < 1 || input.length > 30000) {
    throw new Error('WB_CAMPAIGN_SETTINGS_INVALID_ROWS');
  }
  var observedAt = new Date(String(body.observed_at || ''));
  if (isNaN(observedAt.getTime())) throw new Error('WB_CAMPAIGN_SETTINGS_OBSERVATION_INVALID');
  var ss = SpreadsheetApp.openById(SMC_WB_TRAFFIC.SPREADSHEET_ID);
  var headers = ['store_id','advert_id','nmId','campaign_name','status',
    'bid_type','payment_type','currency','search_bid_kopecks',
    'recommendation_bid_kopecks','search_placement','recommendation_placement',
    'updated_at','observed_at','source','quality','loaded_at'];
  var name = '19_AD_CAMPAIGN_SETTINGS';
  var sh = ss.getSheetByName(name);
  if (!sh) sh = ss.insertSheet(name);
  if (sh.getMaxColumns() < headers.length)
    sh.insertColumnsAfter(sh.getMaxColumns(),headers.length-sh.getMaxColumns());
  if (!sh.getLastRow()) sh.getRange(1,1,1,headers.length).setValues([headers]);
  var actual = sh.getRange(1,1,1,headers.length).getDisplayValues()[0];
  if (headers.some(function(h,i){return h!==String(actual[i]||'');}))
    throw new Error('WB_CAMPAIGN_SETTINGS_HEADER_MISMATCH');
  var existing={};
  if (sh.getLastRow()>1) {
    sh.getRange(2,1,sh.getLastRow()-1,3).getValues().forEach(function(row,i){
      if (!row[0]) return;
      var key=[row[0],row[1],row[2]].join('|');
      if (Object.prototype.hasOwnProperty.call(existing,key))
        throw new Error('WB_CAMPAIGN_SETTINGS_EXISTING_DUPLICATE');
      existing[key]=i+2;
    });
  }
  var seen={}, updates=[], additions=[];
  input.forEach(function(row,i){
    if (!Array.isArray(row)||row.length!==16||row[0]!==shop||
        row[14]!==body.source||row[15]!==body.quality)
      throw new Error('WB_CAMPAIGN_SETTINGS_INVALID_ROW:'+i);
    var advert=Number(row[1]), nm=Number(row[2]), status=Number(row[4]);
    if (!Number.isSafeInteger(advert)||advert<=0||!Number.isSafeInteger(nm)||nm<=0||
        !Number.isInteger(status)) throw new Error('WB_CAMPAIGN_SETTINGS_INVALID_ID:'+i);
    [8,9].forEach(function(col){
      if (row[col]!=='' && row[col]!==null && row[col]!==undefined &&
          (!Number.isSafeInteger(Number(row[col])) || Number(row[col])<0))
        throw new Error('WB_CAMPAIGN_SETTINGS_INVALID_BID:'+i);
    });
    if (String(row[13]) !== body.observed_at)
      throw new Error('WB_CAMPAIGN_SETTINGS_TIME_MISMATCH:'+i);
    var normalized=row.slice();
    normalized[1]=advert; normalized[2]=nm; normalized[4]=status;
    normalized[3]=String(normalized[3]||'');
    if (/^[=+\-@]/.test(normalized[3])) normalized[3]="'"+normalized[3];
    [8,9].forEach(function(col) {
      normalized[col]=row[col]==='' || row[col]==null ? '' : Number(row[col]);
    });
    normalized[13]=observedAt;
    normalized.push(new Date());
    var key=[shop,advert,nm].join('|');
    if (Object.prototype.hasOwnProperty.call(seen,key))
      throw new Error('WB_CAMPAIGN_SETTINGS_DUPLICATE:'+i);
    seen[key]=true;
    if (Object.prototype.hasOwnProperty.call(existing,key))
      updates.push({index:existing[key],row:normalized});
    else additions.push(normalized);
  });
  sellmonitorGithubBatchUpdateRows_(sh,updates,headers.length);
  if (additions.length) {
    var first=sh.getLastRow()+1;
    if (sh.getMaxRows()<first+additions.length-1)
      sh.insertRowsAfter(sh.getMaxRows(),first+additions.length-1-sh.getMaxRows());
    sh.getRange(first,1,additions.length,headers.length).setValues(additions);
  }
  var clientCatalog=sellmonitorGithubPublishClientCampaignCatalog_(shop,input,body.observed_at);
  if(!clientCatalog.skipped&&clientCatalog.written!==input.length)
    throw new Error('WB_CLIENT_CAMPAIGN_ROW_COUNT_MISMATCH');
  SpreadsheetApp.flush();
  return {ok:true, action:'publish_campaign_settings', shop:shop,
          written:input.length, added:additions.length, updated:updates.length,
          client_catalog:clientCatalog};
}

function sellmonitorGithubPublishAdClusters_(body) {
  body = body || {};
  var shop = String(body.cabinet || '').trim();
  if (['ap','aa','yv'].indexOf(shop) < 0) throw new Error('WB_CLUSTER_UNKNOWN_STORE');
  if (String(body.source || '') !== 'WB /adv/v1/normquery/stats' ||
      String(body.quality || '') !== 'FACT_AD_CLUSTER_DAY_NOT_SEARCH_PHRASE') {
    throw new Error('WB_CLUSTER_PROVENANCE_MISMATCH');
  }
  var input = body.rows;
  if (!Array.isArray(input) || !input.length || input.length > 1000) {
    throw new Error('WB_CLUSTER_INVALID_BATCH');
  }
  var observedAt = new Date(String(body.observed_at || ''));
  if (isNaN(observedAt.getTime())) throw new Error('WB_CLUSTER_INVALID_OBSERVED_AT');
  var ss = SpreadsheetApp.openById(SMC_WB_TRAFFIC.SPREADSHEET_ID);
  var headers = ['store_id','date','advert_id','nmId','norm_query',
    'views','clicks','atbs','orders','ordered_units','spend_rub','ad_avg_pos',
    'ctr_percent','cpc_rub','cpm_rub','source','quality','load_timestamp','coverage_status'];
  var sheetName = '18_AD_CLUSTERS_DAY';
  var sh = ss.getSheetByName(sheetName);
  if (!sh) sh = ss.insertSheet(sheetName);
  if (sh.getMaxColumns() < headers.length) {
    sh.insertColumnsAfter(sh.getMaxColumns(), headers.length - sh.getMaxColumns());
  }
  if (sh.getLastRow() === 0) sh.getRange(1,1,1,headers.length).setValues([headers]);
  var actual = sh.getRange(1,1,1,headers.length).getDisplayValues()[0];
  if (actual.some(function(h,i){return h !== headers[i];})) throw new Error('WB_CLUSTER_HEADER_MISMATCH');
  var tz = ss.getSpreadsheetTimeZone();
  function dateText(v) {
    if (v instanceof Date && !isNaN(v.getTime())) return Utilities.formatDate(v, tz, 'yyyy-MM-dd');
    return String(v || '').trim();
  }
  function rowKey(row) {
    return [String(row[0]),dateText(row[1]),String(row[2]),String(row[3]),
            String(row[4]).trim().toLowerCase()].join('|');
  }
  var existing = {};
  var last = sh.getLastRow();
  if (last > 1) {
    sh.getRange(2,1,last-1,5).getValues().forEach(function(row,i){
      if (!row[0]) return;
      var key = rowKey(row);
      if (Object.prototype.hasOwnProperty.call(existing,key)) throw new Error('WB_CLUSTER_EXISTING_DUPLICATE');
      existing[key] = i + 2;
    });
  }
  var incoming = {}, rows = [];
  input.forEach(function(row, i) {
    if (!Array.isArray(row) || row.length !== 17 || row[0] !== shop ||
        row[15] !== body.source || row[16] !== body.quality) {
      throw new Error('WB_CLUSTER_ROW_INVALID:' + i);
    }
    var date = String(row[1] || '');
    var asDate = new Date(date + 'T12:00:00+03:00');
    if (!/^\d{4}-\d{2}-\d{2}$/.test(date) ||
        isNaN(asDate.getTime()) || asDate.toISOString().slice(0,10) !== date) {
      throw new Error('WB_CLUSTER_DATE_INVALID:' + i);
    }
    [2,3].forEach(function(col){
      var n=Number(row[col]);
      if (!isFinite(n)||n<=0||Math.floor(n)!==n) throw new Error('WB_CLUSTER_ID_INVALID:' + i);
    });
    var query=String(row[4]||'').trim();
    if (!query) throw new Error('WB_CLUSTER_QUERY_EMPTY:' + i);
    var data=row.slice();
    if (/^[=+\-@]/.test(query)) data[4]="'"+query;
    for(var col=5;col<=14;col++){
      if(data[col]==null||data[col]===''){data[col]='';continue;}
      var value=Number(data[col]);
      if(!isFinite(value)||value<0) throw new Error('WB_CLUSTER_NUMBER_INVALID:' + i + ':' + col);
      data[col]=value;
    }
    data[1] = asDate;
    data.push(observedAt, 'SOURCE_OK');
    var key = rowKey(row);
    if (Object.prototype.hasOwnProperty.call(incoming,key)) throw new Error('WB_CLUSTER_DUPLICATE_INPUT:' + i);
    incoming[key] = true;
    rows.push({key:key, values:data});
  });
  var appended=[], updates=[];
  rows.forEach(function(item) {
    var location = existing[item.key];
    if (location) {
      updates.push({index:location,row:item.values});
    } else {
      appended.push(item.values);
    }
  });
  sellmonitorGithubBatchUpdateRows_(sh,updates,headers.length);
  var updated=updates.length;
  if (appended.length) {
    var first=sh.getLastRow()+1;
    if (sh.getMaxRows()<first+appended.length-1)
      sh.insertRowsAfter(sh.getMaxRows(),first+appended.length-1-sh.getMaxRows());
    sh.getRange(first,1,appended.length,headers.length).setValues(appended);
  }
  var cabinet= sellmonitorGithubPublishCabinetAdClusters_(shop,input,body.observed_at);
  if (cabinet.written !== input.length && !cabinet.skipped)
    throw new Error('WB_CABINET_CLUSTER_COUNT_MISMATCH');
  SpreadsheetApp.flush();
  return {ok:true,action:'publish_ad_clusters',cabinet:shop,
          written:rows.length,added:appended.length,updated:updated,
          cabinet_publish:cabinet,
          source:body.source,quality:body.quality};
}

function sellmonitorGithubPublishWbTraffic_(body) {
  body = body || {};
  var status = body.sync_status || {};
  var shops = status.shops || {};
  ['ap','aa','yv'].forEach(function(shop) {
    if (!shops[shop] || shops[shop].ok !== true) {
      throw new Error('WB_TRAFFIC_SHOP_NOT_VERIFIED:' + shop);
    }
  });
  if (status.ok !== true || !status.finishedAt || isNaN(new Date(status.finishedAt).getTime())) {
    throw new Error('WB_TRAFFIC_SYNC_NOT_VERIFIED');
  }
  var datasets = body.datasets || {};
  var prepared = {};
  ['ads','funnel','organic'].forEach(function(name) {
    var sourceRows = datasets[name];
    if (sourceRows == null) sourceRows = [];
    prepared[name] = sellmonitorGithubNormalizeWbTrafficRows_(name, sourceRows, status.finishedAt);
  });
  ['ads_poll','funnel_poll'].forEach(function(name) {
    var sourceRows = datasets[name];
    if (sourceRows == null) sourceRows = [];
    prepared[name] = sellmonitorGithubNormalizeWbPollRows_(name, sourceRows, status.finishedAt);
  });
  if (body.daily_view_only === true) {
    var dailyView = sellmonitorGithubWriteWbTrafficDailyView_(prepared, status.finishedAt, {archiveOnly:true});
    return {ok:dailyView.archive_only === true,
      action:'publish_wb_traffic',sync_finished_at:status.finishedAt,
      partitions:{daily_view:dailyView}};
  }
  if (body.daily_view_store === true) {
    var storeName = String(body.store_name || '').trim();
    if (['Саныч','AIR','Хозяюшка'].indexOf(storeName) < 0) throw new Error('WB_TRAFFIC_DAILY_VIEW_STORE_INVALID');
    var dailyRows = body.daily_view_rows;
    if (dailyRows != null && !Array.isArray(dailyRows)) throw new Error('WB_TRAFFIC_DAILY_VIEW_ROWS_NOT_ARRAY');
    var storeView = sellmonitorGithubWriteWbTrafficDailyView_(prepared, status.finishedAt, {
      storeName:storeName,writeArchive:false,precomputedRows:dailyRows
    });
    return {ok:storeView.lite_tables && storeView.lite_tables.ok === true,
      action:'publish_wb_traffic',sync_finished_at:status.finishedAt,
      partitions:{daily_view:storeView}};
  }
  var partitions = body.skip_partitions === true
    ? {ok:true, skipped:true, reason:'already_published_in_bounded_chunks'}
    : sellmonitorGithubWriteWbTrafficPartitions_(prepared, status.finishedAt, body.skip_daily_view === true);
  if (body.partition_only === true) {
    return {ok:partitions.ok === true, action:'publish_wb_traffic', sync_finished_at:status.finishedAt, partitions:partitions};
  }
  var ss = SpreadsheetApp.openById(SMC_WB_TRAFFIC.SPREADSHEET_ID);
  var result = {ok:true, action:'publish_wb_traffic', sync_finished_at:status.finishedAt, datasets:{}, partitions:partitions};
  ['ads','funnel'].forEach(function(name) {
    var cfg = SMC_WB_TRAFFIC.DATASETS[name];
    var table = prepared[name];
    if (!table.rows.length) {
      result.datasets[name] = {written:0, skipped:true, reason:'empty_snapshot_preserved', max_date:''};
      return;
    }
    var sh = ss.getSheetByName(cfg.sheet);
    if (!sh) throw new Error('WB_TRAFFIC_TARGET_TAB_MISSING:' + cfg.sheet);
    var liveHeaders = sh.getRange(1, 1, 1, cfg.width).getDisplayValues()[0];
    if (cfg.headers.some(function(h, i) { return String(liveHeaders[i] || '').trim() !== h; })) {
      throw new Error('WB_TRAFFIC_HEADER_MISMATCH:' + cfg.sheet);
    }
    var oldLastRow = sh.getLastRow();
    var lastNeeded = table.rows.length + 1;
    if (sh.getMaxRows() < lastNeeded) sh.insertRowsAfter(sh.getMaxRows(), lastNeeded - sh.getMaxRows());
    sh.getRange(2, 1, table.rows.length, cfg.width).setValues(table.rows);
    if (oldLastRow > lastNeeded) {
      sh.getRange(lastNeeded + 1, 1, oldLastRow - lastNeeded, cfg.width).clearContent();
    }
    result.datasets[name] = {written:table.rows.length, skipped:false, max_date:table.max_date};
  });
  ['ads_poll','funnel_poll'].forEach(function(name) {
    var table = prepared[name];
    if (!table.rows.length) {
      result.datasets[name] = {written:0,added:0,updated:0,skipped:true,reason:'no_verified_current_day_observation'};
      return;
    }
    result.datasets[name] = sellmonitorGithubAppendWbPollRows_(ss, name, table);
  });
  var qualitySheetName = '17_TRAFFIC_QUALITY';
  var qualityHeaders = [
    'Магазин','Время сбора','Общий статус','Период рекламы','Статус рекламы',
    'Пояснение рекламы','Период воронки','Статус воронки','Пояснение воронки',
    'Кампании с пустым ответом','Идентификаторы кампаний'
  ];
  var qualitySheet = ss.getSheetByName(qualitySheetName);
  if (!qualitySheet) qualitySheet = ss.insertSheet(qualitySheetName);
  if (!qualitySheet.getLastRow()) {
    qualitySheet.getRange(1,1,1,qualityHeaders.length).setValues([qualityHeaders]);
  }
  var liveQualityHeaders = qualitySheet.getRange(1,1,1,qualityHeaders.length).getDisplayValues()[0];
  if (qualityHeaders.some(function(h,i){return String(liveQualityHeaders[i]||'').trim()!==h;})) {
    throw new Error('WB_TRAFFIC_QUALITY_HEADER_MISMATCH');
  }
  var qualityRows = [];
  ['ap','aa','yv'].forEach(function(shop) {
    var detail = shops[shop] || {};
    var errors = detail.errors || {};
    var warnings = detail.warnings || {};
    var nullCount = Number(detail.campaigns_with_null_payload || 0);
    var adsStatus = errors.promotion ? 'СБОЙ' : (nullCount ? 'ЧАСТИЧНО' : 'АКТУАЛЬНО');
    var adsNote = String(errors.promotion || warnings.promotion || '');
    if (!errors.promotion && nullCount) {
      adsNote = 'WB вернул пустой ответ для ' + nullCount +
        ' кампании/кампаний; строки не дополнялись нулями.';
    }
    var funnelStatus = errors.funnel ? 'СБОЙ' : 'АКТУАЛЬНО';
    var funnelNote = String(errors.funnel || warnings.funnel || '');
    var overall = status.complete === true ? 'ПОЛНО' :
      (status.ok === true ? 'ЧАСТИЧНО' : 'СБОЙ');
    qualityRows.push([
      shop, status.finishedAt, overall,
      Array.isArray(status.period) ? status.period.join(' — ') : '',
      adsStatus, adsNote,
      Array.isArray(status.funnel_period) ? status.funnel_period.join(' — ') : '',
      funnelStatus, funnelNote,
      nullCount,
      (detail.campaigns_with_null_payload_sample || []).join(', ')
    ]);
  });
  var oldQualityLastRow = qualitySheet.getLastRow();
  if (qualitySheet.getMaxRows() < qualityRows.length + 1) {
    qualitySheet.insertRowsAfter(qualitySheet.getMaxRows(), qualityRows.length + 1 - qualitySheet.getMaxRows());
  }
  qualitySheet.getRange(2,1,qualityRows.length,qualityHeaders.length).setValues(qualityRows);
  if (oldQualityLastRow > qualityRows.length + 1) {
    qualitySheet.getRange(qualityRows.length + 2,1,oldQualityLastRow-qualityRows.length-1,qualityHeaders.length).clearContent();
  }
  result.quality = {sheet:qualitySheetName,written:qualityRows.length,complete:status.complete === true};
  SpreadsheetApp.flush();
  return result;
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
  var periodEnd = String(snapshot.period_end || observedAt.slice(0,10));
  var data = snapshot.data && typeof snapshot.data === 'object' ? snapshot.data : {};
  var rows = [], searchRows = [];
  Object.keys(data).forEach(function(k) {
    var x = data[k] || {};
    var rowSource = String(x.source || '');
    if (['wb_search_report','wb_public_search'].indexOf(rowSource) < 0) return;
    var nm = String(x.nm_id || x.nmId || '').trim();
    var title = String(x.name || x.title || '').trim();
    var query = String(x.query || '').trim();
    var pos = Number(x.position);
    if (!/^\d+$/.test(nm) || !query || !isFinite(pos)) return;
    var freq = x.frequency == null || x.frequency === '' ? '' : Number(x.frequency);
    if (freq !== '' && !isFinite(freq)) freq = '';
    var zone = pos <= 3 ? 'ТОП-3' : pos <= 10 ? 'ТОП-10' : pos <= 30 ? '11–30' : pos <= 100 ? '31–100' : '>100';
    var publicSerp = rowSource === 'wb_public_search';
    var pri = publicSerp
      ? (pos <= 10 ? 'УДЕРЖИВАТЬ · PUBLIC SERP' : 'ПОЗИЦИЯ · частотность Н/Д')
      : (pos <= 10 ? 'УДЕРЖИВАТЬ' : Number(freq || 0) >= 1000 ? 'ВЫСОКИЙ · частотный запрос вне ТОП-10' : Number(freq || 0) >= 300 ? 'СРЕДНИЙ · есть потенциал роста' : 'НИЗКИЙ');
    searchRows.push([nm,title,query,pos,freq,'','',periodEnd,observedAt,'','',zone,pri]);
    rows.push([true,nm,title,query,freq,pos,observedAt,'','','','',publicSerp ? 'WB PUBLIC SERP' : 'WB LIVE','','','','5',false,publicSerp ? 'WB public SERP · factual position · frequency unavailable' : 'WB Analytics search_positions · factual']);
  });
  searchRows.sort(function(a,b){return Number(a[0])-Number(b[0]) || Number(b[4]||0)-Number(a[4]||0) || Number(a[3])-Number(b[3]);});
  rows.sort(function(a,b){return Number(a[1])-Number(b[1]) || String(a[3]).localeCompare(String(b[3]));});
  if (!rows.length) return {ok:true, skipped:true, reason:'wb_search_no_query_rows', cabinet:cabinet, observed_at:observedAt};

  var ss=SpreadsheetApp.openById(cfg.spreadsheet_id);

  var search=ss.getSheetByName('07_Поиск');
  if (!search) search=ss.insertSheet('07_Поиск');
  var searchHeaders=['Артикул WB','Товар','Поисковый запрос','Позиция · SNAPSHOT','Частотность · SNAPSHOT','Конкуренция · SNAPSHOT','Товаров в выдаче · SNAPSHOT','Дата позиции · SNAPSHOT','Обновлено запроса · SNAPSHOT','Продажи товара rolling 30д · SNAPSHOT','Выручка товара rolling 30д, ₽ · SNAPSHOT','Зона позиции · расчёт из SNAPSHOT','Приоритет SEO · расчёт из SNAPSHOT'];
  if (search.getMaxColumns()<searchHeaders.length) search.insertColumnsAfter(search.getMaxColumns(),searchHeaders.length-search.getMaxColumns());
  if (search.getMaxRows()<searchRows.length+1) search.insertRowsAfter(search.getMaxRows(),searchRows.length+1-search.getMaxRows());
  search.getRange(1,1,1,searchHeaders.length).setValues([searchHeaders]);
  if (search.getMaxRows()>1) search.getRange(2,1,search.getMaxRows()-1,searchHeaders.length).clearContent();
  search.getRange(2,1,searchRows.length,searchHeaders.length).setValues(searchRows);
  search.setFrozenRows(1);

  var sh=ss.getSheetByName('07_Контроль_позиций');
  if (!sh) sh=ss.insertSheet('07_Контроль_позиций');
  if (sh.getMaxColumns()<18) sh.insertColumnsAfter(sh.getMaxColumns(),18-sh.getMaxColumns());
  if (sh.getMaxRows()<rows.length+3) sh.insertRowsAfter(sh.getMaxRows(),rows.length+3-sh.getMaxRows());

  sh.getRange(1,1,Math.max(3,sh.getMaxRows()),18).clearContent();
  sh.getRange('A1:R1').breakApart();
  sh.getRange('A1:R1').merge().setValue('КОНТРОЛЬ ПОИСКОВЫХ ПОЗИЦИЙ · WB LIVE RUNTIME');
  var snapshotTrust = String(snapshot.trust_status || '');
  var sourceLabel = snapshotTrust === 'FACTUAL_PUBLIC_SERP' ? 'WB public SERP · factual' : 'WB Analytics · factual';
  sh.getRange(2,1,1,18).setValues([[
    'Обновление','обновлено '+Utilities.formatDate(new Date(),ss.getSpreadsheetTimeZone(),'dd.MM.yyyy HH:mm')+' · строк '+rows.length,
    'Источник',sourceLabel,'Активных',rows.length,'','','','','','','','','','','',''
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

  var props=sellmonitorClientProperties_(ss.getId());
  props.setProperty('SMC_WB_SEARCH_SNAPSHOT_AT_MS',String(Date.now()));
  var q=ss.getSheetByName('97_Управление');
  var chained=q ? sellmonitorGithubChainSearchMonitor_(ss,q,cfg.store_id) : {ok:false,queued:false,reason:'97_Управление missing'};
  SpreadsheetApp.flush();
  return {
    ok:true,cabinet:cabinet,store_id:cfg.store_id,rows:rows.length,search_rows:searchRows.length,
    observed_at:observedAt,period_end:periodEnd,
    source:snapshotTrust === 'FACTUAL_PUBLIC_SERP' ? 'wb_public_search' : 'wb_search_report',
    trust_status:snapshotTrust || 'FACTUAL_WB_ANALYTICS',chained_monitor:chained,secretsReturned:false
  };
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
      var onceKey3 = 'SMC_TICK_ONCE_V4_USED';
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



const SMC_OZON_SUMMARY = Object.freeze({
  sourceId: '1VQwf-QPeSjexrEculDjWt_hpuCKu7PLzLZhMFjP2VZM',
  sourceSheet: 'Ozon Заказы',
  timezone: 'Europe/Moscow',
  stores: {
    '1': {key:'dom', name:'ДОМ', id:'1g57et2zqlVfbs0_BoRobb5G-4Z2MTXZd028V7u4Kd-Q'},
    '2': {key:'hozyayka', name:'Хозяюшка', id:'1Rl-IVihrZ56wKwDsf76wspZ53a8ntN3hCVN1gd4Tguo'}
  }
});

function sellmonitorGithubOzonDateKey_(value) {
  var d = value instanceof Date ? value : new Date(String(value || ''));
  if (!isFinite(d.getTime())) return '';
  return Utilities.formatDate(d, SMC_OZON_SUMMARY.timezone, 'yyyy-MM-dd');
}

function sellmonitorGithubOzonShiftDay_(key, offset) {
  var p = String(key).split('-').map(Number);
  var d = new Date(Date.UTC(p[0], p[1]-1, p[2]+offset));
  return Utilities.formatDate(d, 'UTC', 'yyyy-MM-dd');
}

function sellmonitorGithubOzonWeekStart_(key) {
  var p = String(key).split('-').map(Number);
  var d = new Date(Date.UTC(p[0], p[1]-1, p[2]));
  return sellmonitorGithubOzonShiftDay_(key, -((d.getUTCDay()+6)%7));
}

function sellmonitorGithubOzonMonthEnd_(key) {
  var p = String(key).split('-').map(Number);
  return Utilities.formatDate(new Date(Date.UTC(p[0], p[1], 0)), 'UTC', 'yyyy-MM-dd');
}

function sellmonitorGithubOzonHash_(rows) {
  var bytes = Utilities.computeDigest(Utilities.DigestAlgorithm.SHA_256, JSON.stringify(rows), Utilities.Charset.UTF_8);
  return bytes.map(function(b) { var n = (b + 256) % 256; return ('0' + n.toString(16)).slice(-2); }).join('');
}

function sellmonitorGithubOzonEnsureRows_(sheet, lastRow) {
  if (sheet.getMaxRows() < lastRow) sheet.insertRowsAfter(sheet.getMaxRows(), lastRow - sheet.getMaxRows());
}

function sellmonitorGithubOzonArchive_(archive, target, rangeA1, oldRows, sourceUpdated, archiveId) {
  if (!oldRows || !oldRows.length) return 0;
  var start = Math.max(2, archive.getLastRow()+1);
  sellmonitorGithubOzonEnsureRows_(archive, start + oldRows.length - 1);
  var stamp = new Date();
  var payload = oldRows.map(function(row, i) {
    return [archiveId, stamp, target, rangeA1, i+1, sourceUpdated, JSON.stringify(row)];
  });
  archive.getRange(start, 1, payload.length, 7).setValues(payload);
  var check = archive.getRange(start, 1, payload.length, 7).getValues();
  if (check.length !== payload.length || check.some(function(r, i) { return String(r[0]) !== archiveId || String(r[6]) !== JSON.stringify(oldRows[i]); })) {
    throw new Error('OZON_ARCHIVE_READBACK_MISMATCH:' + target + ':' + rangeA1);
  }
  return payload.length;
}

function sellmonitorGithubOzonNormalizeRows_(rows, idColumnsAsText) {
  return (rows || []).map(function(row) {
    return row.map(function(value, column) {
      if (value instanceof Date) return Utilities.formatDate(value, SMC_OZON_SUMMARY.timezone, 'yyyy-MM-dd');
      if (idColumnsAsText && (column === 3 || column === 4) && typeof value === 'number') return String(value);
      return value === null ? '' : value;
    });
  });
}

function sellmonitorGithubOzonLastValueRow_(sheet, col) {
  var last=sheet.getLastRow();
  if(last<1)return 0;
  var values=sheet.getRange(1,col,last,1).getDisplayValues();
  for(var i=values.length-1;i>=0;i--)if(String(values[i][0]||'').trim()!=='')return i+1;
  return 0;
}

function sellmonitorGithubOzonRows_(sheet, row, col, width) {
  var last = sellmonitorGithubOzonLastValueRow_(sheet,col);
  if (last < row) return [];
  return sheet.getRange(row, col, last-row+1, width).getValues();
}

function sellmonitorGithubOzonAgg_(records, storeId, today) {
  var per = {day:{}, week:{}, month:{}}, products = [];
  function bucket(type, start, end, rec) {
    var k = type+'|'+start+'|'+end, x=per[type][k];
    if (!x) x=per[type][k]={type:type,start:start,end:end,orders:{},posts:{},offers:{},units:0,lines:0,days:{}};
    x.orders[rec.order]=1; x.posts[rec.posting]=1; x.offers[rec.offer+'|'+rec.sku]=1;
    x.units+=rec.qty; x.lines++; x.days[rec.day]=1;
  }
  var productMap={}, daySeen={}, sourceDays=[];
  records.forEach(function(r) {
    if (r.cabinet !== storeId) return;
    var week=sellmonitorGithubOzonWeekStart_(r.day), month=r.day.slice(0,7)+'-01';
    bucket('day',r.day,r.day,r);
    bucket('week',week,sellmonitorGithubOzonShiftDay_(week,6),r);
    bucket('month',month,sellmonitorGithubOzonMonthEnd_(month),r);
    sourceDays.push(r.day);
    ['day','week','month'].forEach(function(type) {
      var start=type==='day'?r.day:(type==='week'?week:month);
      var end=type==='day'?r.day:(type==='week'?sellmonitorGithubOzonShiftDay_(week,6):sellmonitorGithubOzonMonthEnd_(month));
      var k=type+'|'+start+'|'+end+'|'+r.offer+'|'+r.sku, p=productMap[k];
      if(!p)p=productMap[k]={type:type,start:start,end:end,offer:r.offer,sku:r.sku,title:r.title,orders:{},posts:{},units:0,lines:0};
      if(p.title!==r.title && r.title && p.title.indexOf(r.title)<0)p.title+=' / '+r.title;
      p.orders[r.order]=1;p.posts[r.posting]=1;p.units+=r.qty;p.lines++;
    });
  });
  sourceDays.sort();
  var minDay=sourceDays[0]||'', maxDay=sourceDays[sourceDays.length-1]||'';
  function status(x) {
    if (x.start < minDay) return 'Частичный период: источник начинается с '+minDay;
    if (x.end >= today) return 'Предварительно: период включает текущую дату';
    return 'Закрытая дата/период; строки выгрузки';
  }
  function values(x) {
    return [x.type==='day'?'День':x.type==='week'?'Неделя':'Месяц',x.start,x.end,
      Object.keys(x.orders).length,Object.keys(x.posts).length,x.units,
      x.type==='day'?Object.keys(x.offers).length:'',x.lines,Object.keys(x.days).length,status(x)];
  }
  var daily=Object.keys(per.day).map(function(k){return per.day[k];}).sort(function(a,b){return a.start.localeCompare(b.start);});
  var weekly=Object.keys(per.week).map(function(k){return per.week[k];}).sort(function(a,b){return a.start.localeCompare(b.start);});
  var monthly=Object.keys(per.month).map(function(k){return per.month[k];}).sort(function(a,b){return a.start.localeCompare(b.start);});
  var summary=[
    ['Дневные итоги','Начало','Конец','Заказы, уникальные','Отправления, уникальные','Штук','SKU/offer','Строк товаров','Дней с данными','Состояние'],
    ...daily.map(values),
    ['','','','','','','','','',''],
    ['Недельные итоги','Начало','Конец','Заказы, уникальные','Отправления, уникальные','Штук','SKU/offer','Строк товаров','Дней с данными','Состояние'],
    ...weekly.map(values),
    ['','','','','','','','','',''],
    ['Месячные итоги','Начало','Конец','Заказы, уникальные','Отправления, уникальные','Штук','SKU/offer','Строк товаров','Дней с данными','Состояние'],
    ...monthly.map(values),
    ['Контроль: заказ и отправление считаются по уникальным ключам; строки и штуки — по позициям. Отсутствие строки не считается нулём.','','','','','','','','','']
  ];
  var productItems=Object.keys(productMap).map(function(k){return productMap[k];})
    .sort(function(a,b){
      var rank={day:0,week:1,month:2};
      return rank[a.type]-rank[b.type]||a.start.localeCompare(b.start)||a.offer.localeCompare(b.offer)||a.sku.localeCompare(b.sku);
    });
  var periodBuckets={day:per.day,week:per.week,month:per.month}, products=[], currentGroup='';
  productItems.forEach(function(p){
    var groupKey=p.type+'|'+p.start+'|'+p.end, bucket=periodBuckets[p.type][p.type+'|'+p.start+'|'+p.end];
    if(groupKey!==currentGroup){
      currentGroup=groupKey;
      products.push(['ИТОГО МАГАЗИНА · '+(p.type==='day'?'ДЕНЬ':p.type==='week'?'НЕДЕЛЯ':'МЕСЯЦ'),p.start,p.end,'','','ИТОГО МАГАЗИНА',
        Object.keys(bucket.orders).length,Object.keys(bucket.posts).length,bucket.units,bucket.lines,
        p.start<minDay?'Частичный период: источник начинается с '+minDay:(p.end>=today?'Предварительно: период включает текущую дату':'Строки выгрузки'), 'Ozon Seller API — Ozon Заказы']);
    }
    products.push([p.type==='day'?'День':p.type==='week'?'Неделя':'Месяц',p.start,p.end,p.offer,p.sku,p.title,
      Object.keys(p.orders).length,Object.keys(p.posts).length,p.units,p.lines,
      p.start<minDay?'Частичный период: источник начинается с '+minDay:(p.end>=today?'Предварительно: период включает текущую дату':'Строки выгрузки'), 'Ozon Seller API — Ozon Заказы']);
  });
  var fullDays=daily.filter(function(x){return x.end<today;}).map(function(x){return [x.start,Object.keys(x.orders).length,Object.keys(x.posts).length,x.units];});
  return {summary:summary,products:products,fullDays:fullDays,minDay:minDay,maxDay:maxDay,sourceRows:records.filter(function(r){return r.cabinet===storeId;}).length};
}

function sellmonitorGithubPublishOzonSummaryOrders_(payload) { 
  var apiMode=!!(payload&&payload.source==='ozon_seller_api');
  var sourceFallback=false,sourceActivityVerified=false,records=[],ignored=0;
  var sourceStatus='',sourceLastSuccess='',sourceUpdated='',coverageFrom='',coverageTo='';
  if(apiMode){
    if(payload.complete!==true)throw new Error('OZON_API_EXPORT_INCOMPLETE_PRESERVE');
    var completed=(payload.completed_accounts||[]).map(String).sort();
    if(JSON.stringify(completed)!==JSON.stringify(['1','2']))throw new Error('OZON_API_ACCOUNT_COVERAGE_INCOMPLETE_PRESERVE');
    sourceUpdated=String(payload.exported_at||'').trim();
    if(!sourceUpdated||!isFinite(new Date(sourceUpdated).getTime()))throw new Error('OZON_API_EXPORT_TIMESTAMP_INVALID');
    sourceLastSuccess=sourceUpdated;
    coverageFrom=String(payload.coverage_from||'').trim();
    coverageTo=String(payload.coverage_to||'').trim();
    if(!/^\d{4}-\d{2}-\d{2}$/.test(coverageFrom)||!/^\d{4}-\d{2}-\d{2}$/.test(coverageTo)||coverageFrom>'2026-09-01'||coverageTo<coverageFrom)throw new Error('OZON_API_COVERAGE_WINDOW_INVALID');
    sourceStatus='🟢 Ozon Seller API · GitHub · FBO/FBS, все страницы получены';
    (payload.records||[]).forEach(function(r,index){
      var cabinet=String(r.cabinet||'').trim(),day=String(r.day||'').trim(),posting=String(r.posting||'').trim(),order=String(r.order||'').trim();
      var offer=String(r.offer||'').trim(),sku=String(r.sku||'').trim(),title=String(r.title||'').trim(),qty=Number(r.qty);
      if(!SMC_OZON_SUMMARY.stores[cabinet]||!/^\d{4}-\d{2}-\d{2}$/.test(day)||day<coverageFrom||day>coverageTo||!posting||!order||!offer||!sku||!isFinite(qty)||qty<=0)throw new Error('OZON_API_INVALID_RECORD:'+String(index+1));
      records.push({cabinet:cabinet,day:day,posting:posting,order:order,offer:offer,sku:sku,title:title,qty:qty});
    });
    if(!records.length)throw new Error('OZON_API_EMPTY_EXPORT_PRESERVE');
  } else {
  var source=SpreadsheetApp.openById(SMC_OZON_SUMMARY.sourceId);
  var sourceSheet=source.getSheetByName(SMC_OZON_SUMMARY.sourceSheet);
  if(!sourceSheet)throw new Error('OZON_SOURCE_TAB_MISSING');
  var last=sourceSheet.getLastRow();
  if(last<2)throw new Error('OZON_SOURCE_EMPTY_PRESERVE');
  var automation=source.getSheetByName('Автоматизация');
  if(!automation)throw new Error('OZON_SOURCE_HEALTH_TAB_MISSING');
  var automationRows=automation.getRange(1,1,Math.max(1,automation.getLastRow()),4).getDisplayValues();
  var ozonHealth=null;
  for(var ai=0;ai<automationRows.length;ai++){
    if(String(automationRows[ai][0]||'').trim().toLowerCase()==='ozon заказы'){ozonHealth=automationRows[ai];break;}
  }
  var sourceLastSuccess=ozonHealth?String(ozonHealth[1]||'').trim():'';
  var sourceStatus=ozonHealth?String(ozonHealth[3]||'').trim():'СТРОКА СТАТУСА НЕ НАЙДЕНА';
  var sourceFallback=false, sourceActivityVerified=false;
  if(!ozonHealth||!sourceLastSuccess||!/работает/i.test(sourceStatus)){
    return {ok:false,action:'publish_ozon_summary_orders',source_id:SMC_OZON_SUMMARY.sourceId,
      source_status:sourceStatus,source_last_success:sourceLastSuccess||null,
      reason:'OZON_SOURCE_EXPORT_NOT_VERIFIED; summary workbooks preserved'};
  }
  var sourceUpdated=sourceLastSuccess;
  var raw=sourceSheet.getRange(1,1,last,19).getValues();
  var expected=['Кабинет','Номер отправления','Номер заказа','Принят в обработку','SKU','Артикул','Товар','Кол-во'];
  var indexes=[0,2,4,7,15,16,17,18];
  indexes.forEach(function(ix,i){if(String(raw[0][ix]||'').trim()!==expected[i])throw new Error('OZON_SOURCE_HEADER_MISMATCH:'+expected[i]+':'+String(raw[0][ix]||''));});
  var seen={}, records=[], ignored=0;
  for(var ri=1;ri<raw.length;ri++){
    var row=raw[ri];
    if(row.every(function(v){return v===null||v==='';}))continue;
    var cabinet=String(row[0]||'').trim();
    if(!SMC_OZON_SUMMARY.stores[cabinet]){ignored++;continue;}
    var day=sellmonitorGithubOzonDateKey_(row[7]),posting=String(row[2]||'').trim(),order=String(row[4]||'').trim();
    var offer=String(row[16]||'').trim(),sku=String(row[15]||'').trim(),title=String(row[17]||'').trim(),qty=Number(row[18]);
    if(!day||!posting||!order||!offer||!sku||!isFinite(qty)||qty<0)throw new Error('OZON_SOURCE_INVALID_ROW:'+String(ri+1));
    var lineKey=cabinet+'|'+posting+'|'+sku;
    if(seen[lineKey])throw new Error('OZON_SOURCE_DUPLICATE_POSTING_SKU:'+String(ri+1));
    seen[lineKey]=1;
    records.push({cabinet:cabinet,day:day,posting:posting,order:order,offer:offer,sku:sku,title:title,qty:qty});
  }
  if(!records.length)throw new Error('OZON_SOURCE_NO_VALID_ROWS_PRESERVE');

  }
  var today=Utilities.formatDate(new Date(),SMC_OZON_SUMMARY.timezone,'yyyy-MM-dd'), results=[];
  Object.keys(SMC_OZON_SUMMARY.stores).forEach(function(cabinet){
    var cfg=SMC_OZON_SUMMARY.stores[cabinet];
    try {
    var ss=SpreadsheetApp.openById(cfg.id),daily=ss.getSheetByName('11_API_Заказы'),skuSheet=ss.getSheetByName('12_API_Заказы_SKU');
    if(!daily||!skuSheet)throw new Error('OZON_TARGET_TAB_MISSING:'+cfg.name);
    var storeRecords=records.filter(function(r){return r.cabinet===cabinet;});
    if(!storeRecords.length){results.push({store:cfg.name,ok:true,skipped:true,reason:'empty_source_preserved'});return;}
    var agg=sellmonitorGithubOzonAgg_(records,cabinet,today);
    if(sourceActivityVerified){
      agg.summary.forEach(function(row){
        if(['День','Неделя','Месяц'].indexOf(String(row[0]||''))<0)return;
        row[9]=(String(row[9]||'').indexOf('Частичный период:')===0?String(row[9])+'; ':'')+'Предварительно: heartbeat API красный, полнота выгрузки не подтверждена';
      });
      agg.products.forEach(function(row){
        if(!row[0])return;
        row[10]=(String(row[10]||'').indexOf('Частичный период:')===0?String(row[10])+'; ':'')+'Предварительно: heartbeat API красный, полнота выгрузки не подтверждена';
      });
    }
    var dailyHeader=['Дневные итоги','Начало','Конец','Заказы, уникальные','Отправления, уникальные','Штук','SKU/offer','Строки товаров','Дней с данными','Состояние'];
    var skuHeader=['Тип периода','Начало','Конец','offer_id','SKU','Наименование','Заказы, уникальные','Отправления, уникальные','Штук','Строк товаров','Состояние периода','Источник'];
    if(JSON.stringify(daily.getRange(4,1,1,10).getDisplayValues()[0])!==JSON.stringify(dailyHeader))throw new Error('OZON_TARGET_DAILY_HEADER_MISMATCH:'+cfg.name);
    if(JSON.stringify(skuSheet.getRange(4,1,1,12).getDisplayValues()[0])!==JSON.stringify(skuHeader))throw new Error('OZON_TARGET_SKU_HEADER_MISMATCH:'+cfg.name);
    var dailyRows=agg.summary.slice(1), dailyStart=5;
    var oldDaily=sellmonitorGithubOzonRows_(daily,dailyStart,1,10), oldSku=sellmonitorGithubOzonRows_(skuSheet,5,1,12), oldHelper=sellmonitorGithubOzonRows_(daily,2,12,4);
    var oldStamp=daily.getRange(2,1).getValue(), newStamp=apiMode
      ?'Источник: Ozon Seller API через GitHub; полная выгрузка всех страниц за '+coverageFrom+'–'+coverageTo+'; обновлено '+sourceUpdated.slice(0,10)+'; текущий день предварительный; дата — «Принят в обработку» (UTC+3); заказ = кабинет + номер заказа, отправление = кабинет + posting number.'
      :(sourceActivityVerified
        ?'Источник: Ozon Заказы; последняя запись в выгрузке: '+sourceUpdated+'; heartbeat API красный, полнота выгрузки не подтверждена — показатели предварительные; дата — «Принят в обработку» (UTC+3); заказ = кабинет + номер заказа, отправление = кабинет + posting number.'
        :'Источник: Ozon Заказы; последняя подтверждённая выгрузка '+sourceUpdated+(sourceFallback?'; API heartbeat красный, используются только строки, совпавшие с последним проверенным снимком':'')+'; дата — «Принят в обработку» (UTC+3); заказ = кабинет + номер заказа, отправление = кабинет + posting number.');
    var helper=[['Полные дни — данные графика','Заказы','Отправления','Штук']].concat(agg.fullDays);
    function hasTypedOzonPeriodDates_(rows){
      var periodRows=(rows||[]).filter(function(row){return ['День','Неделя','Месяц'].indexOf(String(row[0]||''))>=0||String(row[0]||'').indexOf('ИТОГО МАГАЗИНА ·')===0;});
      return periodRows.length>0&&periodRows.every(function(row){return row[1] instanceof Date&&row[2] instanceof Date;});
    }
    function toTypedOzonPeriodDates_(rows,columns){
      return (rows||[]).map(function(row){var copy=row.slice();columns.forEach(function(column){if(/^\d{4}-\d{2}-\d{2}$/.test(String(copy[column]||'')))copy[column]=Utilities.parseDate(String(copy[column]),SMC_OZON_SUMMARY.timezone,'yyyy-MM-dd');});return copy;});
    }
    var dailyDatesTyped=hasTypedOzonPeriodDates_(oldDaily),skuDatesTyped=hasTypedOzonPeriodDates_(oldSku);
    var helperDatesTyped=oldHelper.length>0&&oldHelper.every(function(row){return row[0] instanceof Date;});
    var dailyRowsForWrite=toTypedOzonPeriodDates_(dailyRows,[1,2]);
    var skuRowsForWrite=toTypedOzonPeriodDates_(agg.products,[1,2]);
    var helperRowsForWrite=toTypedOzonPeriodDates_(helper.slice(1),[0]);
    var sameDaily=dailyDatesTyped&&JSON.stringify(sellmonitorGithubOzonNormalizeRows_(oldDaily))===JSON.stringify(sellmonitorGithubOzonNormalizeRows_(dailyRows)), sameSku=skuDatesTyped&&JSON.stringify(sellmonitorGithubOzonNormalizeRows_(oldSku,true))===JSON.stringify(sellmonitorGithubOzonNormalizeRows_(agg.products,true)), sameHelper=helperDatesTyped&&JSON.stringify(sellmonitorGithubOzonNormalizeRows_(oldHelper))===JSON.stringify(sellmonitorGithubOzonNormalizeRows_(helper.slice(1))), sameStamp=String(oldStamp||'')===newStamp;
    if(sourceFallback&&!sourceActivityVerified){
      var oldSkuWithoutTotals=oldSku.filter(function(row){return String(row[0]||'').indexOf('ИТОГО МАГАЗИНА ·')!==0;});
      var fallbackSame=JSON.stringify(sellmonitorGithubOzonNormalizeRows_(oldDaily))===JSON.stringify(sellmonitorGithubOzonNormalizeRows_(dailyRows))&&
        JSON.stringify(sellmonitorGithubOzonNormalizeRows_(oldSkuWithoutTotals,true))===JSON.stringify(sellmonitorGithubOzonNormalizeRows_(agg.products.filter(function(row){return String(row[0]||'').indexOf('ИТОГО МАГАЗИНА ·')!==0;}),true))&&
        JSON.stringify(sellmonitorGithubOzonNormalizeRows_(oldHelper))===JSON.stringify(sellmonitorGithubOzonNormalizeRows_(helper.slice(1)));
      if(!fallbackSame||fallbackExpected[cabinet].sha!==sellmonitorGithubOzonHash_({summary:oldDaily,products:oldSku,helper:[['Полные дни — данные графика','Заказы','Отправления','Штук']].concat(oldHelper)}))
        throw new Error('OZON_VERIFIED_SNAPSHOT_FALLBACK_CONTENT_MISMATCH:'+cfg.name);
    }
    var archive=ss.getSheetByName('13_API_Заказы_Архив');
    if(!archive){archive=ss.insertSheet('13_API_Заказы_Архив');archive.getRange(1,1,1,7).setValues([['Архив ID','Время','Лист','Диапазон','Строка в диапазоне','Последний успешный экспорт источника','Старое значение JSON']]);}
    else if(archive.getLastRow()===0)archive.getRange(1,1,1,7).setValues([['Архив ID','Время','Лист','Диапазон','Строка в диапазоне','Время источника','Старое значение JSON']]);
    var archiveId=Utilities.getUuid(), archived=0;
    if(!sameStamp) archived+=sellmonitorGithubOzonArchive_(archive,'11_API_Заказы','A2',[[oldStamp]],sourceUpdated,archiveId);
    if(!sameDaily) archived+=sellmonitorGithubOzonArchive_(archive,'11_API_Заказы','A5:J'+(4+oldDaily.length),oldDaily,sourceUpdated,archiveId);
    if(!sameSku) archived+=sellmonitorGithubOzonArchive_(archive,'12_API_Заказы_SKU','A5:L'+(4+oldSku.length),oldSku,sourceUpdated,archiveId);
    if(!sameHelper) archived+=sellmonitorGithubOzonArchive_(archive,'11_API_Заказы','L1:O'+(1+oldHelper.length),oldHelper,sourceUpdated,archiveId);
    var written=0;
    if(sourceActivityVerified){
      var activity=sourceActivityByCabinet[cabinet];
      if(Number(activity.rows)!==Number(agg.sourceRows)||String(activity.maxDay)!==String(agg.maxDay))throw new Error('OZON_SOURCE_ACTIVITY_READBACK_MISMATCH:'+cfg.name);
    }
    if(!sameStamp)daily.getRange(2,1,1,1).setValue(newStamp);
    if(!sameDaily){
      var need=dailyStart+dailyRows.length-1;sellmonitorGithubOzonEnsureRows_(daily,need);
      var clear=Math.max(oldDaily.length,dailyRows.length);
      if(clear)daily.getRange(dailyStart,1,clear,10).clearContent();
      if(dailyRows.length)daily.getRange(dailyStart,1,dailyRowsForWrite.length,10).setValues(dailyRowsForWrite);
      written+=dailyRows.length;
    }
    if(!sameSku){
      var skuRows=agg.products,skuStart=5,skuNeed=skuStart+skuRows.length-1;sellmonitorGithubOzonEnsureRows_(skuSheet,skuNeed);
      var skuClear=Math.max(oldSku.length,skuRows.length);
      if(skuClear)skuSheet.getRange(skuStart,1,skuClear,12).clearContent();
      if(skuRows.length)skuSheet.getRange(skuStart,1,skuRowsForWrite.length,12).setValues(skuRowsForWrite);
      written+=skuRows.length;
    }
    if(!sameHelper){
      var helpNeed=1+helper.length;sellmonitorGithubOzonEnsureRows_(daily,helpNeed);
      var helpClear=Math.max(oldHelper.length,helper.length-1);
      if(helpClear)daily.getRange(2,12,helpClear,4).clearContent();
      daily.getRange(1,12,1,4).setValues([helper[0]]);\n      if(helperRowsForWrite.length)daily.getRange(2,12,helperRowsForWrite.length,4).setValues(helperRowsForWrite);
    }
    var newDaily=sellmonitorGithubOzonRows_(daily,dailyStart,1,10),newSku=sellmonitorGithubOzonRows_(skuSheet,5,1,12),newHelper=sellmonitorGithubOzonRows_(daily,1,12,4);
    if(JSON.stringify(sellmonitorGithubOzonNormalizeRows_(newDaily))!==JSON.stringify(sellmonitorGithubOzonNormalizeRows_(dailyRows)))throw new Error('OZON_DAILY_READBACK_MISMATCH:'+cfg.name);
    var readSkuNorm=sellmonitorGithubOzonNormalizeRows_(newSku,true), expectedSkuNorm=sellmonitorGithubOzonNormalizeRows_(agg.products,true);
    if(JSON.stringify(readSkuNorm)!==JSON.stringify(expectedSkuNorm)){
      var mismatch={row:-1,col:-1,actualType:'missing',expectedType:'missing'};
      for(var sri=0;sri<Math.max(readSkuNorm.length,expectedSkuNorm.length)&&mismatch.row<0;sri++){
        var ar=readSkuNorm[sri]||[],er=expectedSkuNorm[sri]||[];
        for(var sci=0;sci<Math.max(ar.length,er.length);sci++){
          if(JSON.stringify(ar[sci])!==JSON.stringify(er[sci])){
            mismatch={row:sri+5,col:sci+1,actualType:typeof ar[sci],expectedType:typeof er[sci]};
            break;
          }
        }
      }
      throw new Error('OZON_SKU_READBACK_MISMATCH:'+cfg.name+':row='+mismatch.row+':col='+mismatch.col+':actualType='+mismatch.actualType+':expectedType='+mismatch.expectedType);
    }
    if(JSON.stringify(sellmonitorGithubOzonNormalizeRows_(newHelper))!==JSON.stringify(sellmonitorGithubOzonNormalizeRows_(helper)))throw new Error('OZON_CHART_READBACK_MISMATCH:'+cfg.name);
    skuSheet.setFrozenRows(4);
    skuSheet.setHiddenGridlines(true);
    skuSheet.getRange(4,1,1,12).setBackground('#17365D').setFontColor('#FFFFFF').setFontWeight('bold').setWrap(true);
    skuSheet.getRange(5,2,Math.max(1,newSku.length),2).setNumberFormat('dd.mm.yyyy');
    if(newDaily.length)daily.getRange(dailyStart,2,newDaily.length,2).setNumberFormat('dd.mm.yyyy');
    if(newHelper.length>1)daily.getRange(2,12,newHelper.length-1,1).setNumberFormat('dd.mm.yyyy');
    skuSheet.getRange(5,7,Math.max(1,newSku.length),2).setNumberFormat('#,##0');
    skuSheet.getRange(5,9,Math.max(1,newSku.length),2).setNumberFormat('#,##0.###');
    var groupRanges=[];
    newSku.forEach(function(row,index){if(String(row[0]||'').indexOf('ИТОГО МАГАЗИНА ·')===0)groupRanges.push('A'+(index+5)+':L'+(index+5));});
    if(groupRanges.length)skuSheet.getRangeList(groupRanges).setBackground('#D9EAF7').setFontColor('#17365D').setFontWeight('bold');
    if(!skuSheet.getFilter())skuSheet.getRange(4,1,Math.max(1,newSku.length+1),12).createFilter();
    var charts=daily.getCharts();
    if(charts.length){var range=daily.getRange(1,12,helper.length,4);charts.forEach(function(ch){daily.updateChart(ch.modify().clearRanges().addRange(range).build());});}
    var articleTargets=[
      {name:'01_Дни_артикулы',kind:'day',label:'Дни'},
      {name:'02_Недели_артикулы',kind:'week',label:'Недели'},
      {name:'03_Месяцы_артикулы',kind:'month',label:'Месяцы'}
    ];
    var articleViews=[];
    articleTargets.forEach(function(target){
      var view=ss.getSheetByName(target.name);
      if(!view)throw new Error('OZON_ARTICLE_TAB_MISSING:'+target.name);
      var totalLabel='ИТОГО МАГАЗИНА · '+(target.kind==='day'?'ДЕНЬ':target.kind==='week'?'НЕДЕЛЯ':'МЕСЯЦ');
      var viewRows=agg.products.filter(function(row){return String(row[0]||'')===(target.kind==='day'?'День':target.kind==='week'?'Неделя':'Месяц')||String(row[0]||'')===totalLabel;});
      if(!viewRows.length)throw new Error('OZON_ARTICLE_PERIOD_EMPTY:'+target.kind+':'+cfg.name);
      var viewTitle=['Заказы Ozon по артикулам · '+target.label+' · '+cfg.name];
      var viewDescription=['Источник: Ozon Seller API через GitHub; все страницы FBO/FBS; строка итога магазина стоит перед товарами каждого периода.'];
      var viewCoverage=['Покрытие '+agg.minDay+' — '+agg.maxDay+'; текущий и неполный период отмечен в «Состояние периода». Финансы и реклама на уровне артикула не предоставляются этим источником.'];
      var viewHeader=skuHeader;
      var oldLast=Math.max(1,view.getLastRow()),oldWidth=Math.max(12,view.getLastColumn());
      var oldViewValues=view.getRange(1,1,oldLast,oldWidth).getValues(),oldViewFormulas=view.getRange(1,1,oldLast,oldWidth).getFormulas();
      var oldView=oldViewValues.map(function(row,rowIndex){return row.map(function(value,columnIndex){return {value:value,formula:oldViewFormulas[rowIndex][columnIndex]};});});
      var oldBody=oldLast>4?view.getRange(5,1,oldLast-4,12).getValues():[];
      var oldHeader=view.getRange(4,1,1,12).getDisplayValues()[0];
      var oldMeta=view.getRange(1,1,3,1).getDisplayValues().map(function(r){return String(r[0]||'');});
      var datesTyped=oldBody.every(function(row){return row[1] instanceof Date&&row[2] instanceof Date;});
      var storedViewRows=viewRows.map(function(row){var copy=row.slice();copy[1]=Utilities.parseDate(String(row[1]),SMC_OZON_SUMMARY.timezone,'yyyy-MM-dd');copy[2]=Utilities.parseDate(String(row[2]),SMC_OZON_SUMMARY.timezone,'yyyy-MM-dd');return copy;});
      var sameView=datesTyped&&JSON.stringify(oldHeader)===JSON.stringify(viewHeader)&&
        JSON.stringify(sellmonitorGithubOzonNormalizeRows_(oldBody,true))===JSON.stringify(sellmonitorGithubOzonNormalizeRows_(viewRows,true))&&
        oldMeta[0]===viewTitle[0]&&oldMeta[1]===viewDescription[0]&&oldMeta[2]===viewCoverage[0];
      if(!sameView){
        var viewArchiveId=archiveId;
        var archiveColNumber=oldWidth,archiveColName='';while(archiveColNumber>0){var archiveRemainder=(archiveColNumber-1)%26;archiveColName=String.fromCharCode(65+archiveRemainder)+archiveColName;archiveColNumber=Math.floor((archiveColNumber-1)/26);}
        archived+=sellmonitorGithubOzonArchive_(archive,target.name,'A1:'+archiveColName+oldLast,oldView,sourceUpdated,viewArchiveId);
        var viewNeed=4+viewRows.length;
        sellmonitorGithubOzonEnsureRows_(view,viewNeed);
        if(view.getFilter())view.getFilter().remove();
        view.getRange(1,1,Math.max(oldLast,viewNeed),oldWidth).clearContent();
        view.getRange(1,1,3,1).setValues([[viewTitle[0]],[viewDescription[0]],[viewCoverage[0]]]);
        view.getRange(4,1,1,12).setValues([viewHeader]);
        view.getRange(5,1,storedViewRows.length,12).setValues(storedViewRows);
        view.setFrozenRows(4);
        view.setHiddenGridlines(true);
        view.getRange(4,1,1,12).setBackground('#17365D').setFontColor('#FFFFFF').setFontWeight('bold').setWrap(true);
        view.getRange(5,2,viewRows.length,2).setNumberFormat('dd.mm.yyyy');
        view.getRange(5,7,viewRows.length,2).setNumberFormat('#,##0');
        view.getRange(5,9,viewRows.length,2).setNumberFormat('#,##0.###');
        var viewGroupRanges=[];
        viewRows.forEach(function(row,index){if(String(row[0]||'').indexOf('ИТОГО МАГАЗИНА ·')===0)viewGroupRanges.push('A'+(index+5)+':L'+(index+5));});
        if(viewGroupRanges.length)view.getRangeList(viewGroupRanges).setBackground('#D9EAF7').setFontColor('#17365D').setFontWeight('bold');
        view.getRange(4,1,viewRows.length+1,12).createFilter();
      }
      var checkedView=sellmonitorGithubOzonRows_(view,5,1,12);
      if(JSON.stringify(sellmonitorGithubOzonNormalizeRows_(checkedView,true))!==JSON.stringify(sellmonitorGithubOzonNormalizeRows_(viewRows,true)))throw new Error('OZON_ARTICLE_READBACK_MISMATCH:'+cfg.name+':'+target.name);
      articleViews.push({sheet:target.name,rows:checkedView.length,sha256:sellmonitorGithubOzonHash_(checkedView),readback:true});
    });
    var log=ss.getSheetByName('14_API_Заказы_Журнал');
    if(!log){log=ss.insertSheet('14_API_Заказы_Журнал');log.getRange(1,1,1,12).setValues([['Время запуска','Магазин','Источник','Последний успешный экспорт','Строк источника','Период с','Период по','Строк сводки','Строк SKU','Строк в архиве','SHA-256 сводки','Проверка']]);}
    else if(log.getLastRow()===0)log.getRange(1,1,1,12).setValues([['Время','Магазин','Источник','Время изменения источника','Строк источника','Период с','Период по','Строк сводки','Строк SKU','Строк в архиве','SHA-256 сводки','Проверка']]);
    var finalHash=sellmonitorGithubOzonHash_({summary:newDaily,products:newSku,helper:newHelper,articleViews:articleViews});
    var articleLog=articleViews.map(function(x){return x.sheet+'='+x.rows+' строк; SHA-256='+x.sha256;}).join('; ');
    var logStatus=(apiMode?'OK: FBO/FBS API; все страницы периода получены; запись перечитана':'OK: запись и чтение сводки совпали; пустые дни не заменены нулями')+'; листы дней/недель/месяцев перечитаны: '+articleLog;
    var logRow=[new Date(),cfg.name,apiMode?'Ozon Seller API через GitHub':SMC_OZON_SUMMARY.sourceSheet,sourceUpdated,agg.sourceRows,agg.minDay,agg.maxDay,newDaily.length,newSku.length,archived,finalHash,logStatus];
    var logAt=log.getLastRow()+1;sellmonitorGithubOzonEnsureRows_(log,logAt);log.getRange(logAt,1,1,12).setValues([logRow]);
    var logRead=log.getRange(logAt,1,1,12).getValues()[0];
    if(String(logRead[10])!==finalHash||String(logRead[11])!==logRow[11])throw new Error('OZON_LOG_READBACK_MISMATCH:'+cfg.name);
    results.push({store:cfg.name,ok:true,source_rows:agg.sourceRows,source_min:agg.minDay,source_max:agg.maxDay,daily_rows:newDaily.length,sku_rows:newSku.length,article_views:articleViews,archived_rows:archived,source_last_success:sourceUpdated,sha256:finalHash,readback:true,charts:charts.length});
    } catch(err) {
      results.push({store:cfg.name,ok:false,error:String(err && err.message || err),source_updated:sourceUpdated});
    }
  });
  return {ok:results.every(function(x){return x.ok===true;}),action:'publish_ozon_summary_orders',source_id:SMC_OZON_SUMMARY.sourceId,source_rows:records.length,ignored_rows:ignored,source_updated:sourceUpdated,source_status:sourceStatus,source_fallback_used:sourceFallback,source_activity_verified:sourceActivityVerified,stores:results};
}


var SMC_WB_INNER_SUMMARY_V1 = Object.freeze({
  registryId:'1E7HSGJgamzKZkdNdDNyp0f1iI2mLhhbpfi04-sOytBs',
  registrySheet:'Части',
  stores:{
    air_wb:{name:'AIR',id:'1fc6fYdNojj02T597-T0pyCjpiWmhvAr45r1_jiyabIk'},
    hozyayushka_wb:{name:'Хозяюшка',id:'1H_UpoQa0ZsY3hnnS6R4cM2xXiqezFRYa0ZDLhVYfZbQ'},
    sanych_wb:{name:'Саныч',id:'1kdeQ-N80Ul0WvekuPxM1QS8FRz0I3g5eOpsgcK0g9Ls'}
  },
  tabs:{day:'90_SM_Факт_Дни',week:'91_SM_Факт_Недели',month:'92_SM_Факт_Месяцы',archive:'93_SM_Факт_Архив',log:'94_SM_Факт_Журнал'},
  chunk:400
});

function sellmonitorGithubWbInnerDateKey_(v,tz){
  if(v instanceof Date&&!isNaN(v.getTime()))return Utilities.formatDate(v,tz,'yyyy-MM-dd');
  if(typeof v==='number'&&isFinite(v))return Utilities.formatDate(new Date(Date.UTC(1899,11,30)+Math.floor(v)*86400000),tz,'yyyy-MM-dd');
  var s=String(v==null?'':v).trim();
  var m=s.match(/^(\d{4})-(\d{2})-(\d{2})/);
  if(m)return m[1]+'-'+m[2]+'-'+m[3];
  var d=new Date(s);
  return isNaN(d.getTime())?'':Utilities.formatDate(d,tz,'yyyy-MM-dd');
}

function sellmonitorGithubWbInnerAddDays_(key,n){
  var p=key.split('-').map(Number);
  return Utilities.formatDate(new Date(Date.UTC(p[0],p[1]-1,p[2]+n)),'UTC','yyyy-MM-dd');
}

function sellmonitorGithubWbInnerWeekStart_(key){
  var p=key.split('-').map(Number),d=new Date(Date.UTC(p[0],p[1]-1,p[2]));
  return sellmonitorGithubWbInnerAddDays_(key,-((d.getUTCDay()+6)%7));
}

function sellmonitorGithubWbInnerMonthStart_(key){return key.substring(0,7)+'-01';}

function sellmonitorGithubWbInnerRowKey_(grain,period,storeId,nmId){
  return grain+'|'+period+'|'+storeId+'|'+String(nmId||'');
}

function sellmonitorGithubWbInnerBuildRows_(sourceRows,headers,storeId,tz){
  var metricHeaders=headers.slice(8,38), items={}, datesByProduct={}, dateSeen={};
  var sourceMin='',sourceMax='',factualDays={},partialDays={},sourceRowsCount=0;
  var idIx=headers.indexOf('nmId'), articleIx=headers.indexOf('seller_article'), dayIx=headers.indexOf('period_from');
  var trustIx=headers.indexOf('trust_status'), sourceIx=headers.indexOf('source'), loadedIx=headers.indexOf('loaded_at');
  if(idIx<0||articleIx<0||dayIx<0||trustIx<0||sourceIx<0||loadedIx<0)throw new Error('WB_INNER_FACTS_SCHEMA_MISMATCH');
  sourceRows.forEach(function(r){
    var day=sellmonitorGithubWbInnerDateKey_(r[dayIx],tz),nm=String(r[idIx]||'').trim(),shop=String(r[0]||'').trim();
    if(!day||!nm||shop!==storeId)return;
    var trust=String(r[trustIx]||'').trim(), recordKey=String(r[headers.indexOf('record_key')]||'').trim();
    if(!recordKey)throw new Error('WB_INNER_RECORD_KEY_MISSING');
    if(!items[recordKey]){
      var m=r.slice(8,38).map(function(v){return v===''||v==null?'':(typeof v==='number'?v:(isFinite(Number(v))?Number(v):''));});
      items[recordKey]={day:day,nm:nm,article:String(r[articleIx]||''),productGroup:String(r[headers.indexOf('product_group')]||''),productSubgroup:String(r[headers.indexOf('product_subgroup')]||''),metrics:m,trust:trust,source:String(r[sourceIx]||''),loaded:r[loadedIx]||'',recordKey:recordKey};
      var dkey=day+'|'+nm;
      if(!datesByProduct[dkey])datesByProduct[dkey]=[];
      datesByProduct[dkey].push(items[recordKey]);
      sourceRowsCount++;
      if(!sourceMin||day<sourceMin)sourceMin=day;
      if(!sourceMax||day>sourceMax)sourceMax=day;
      if(/FACTUAL/.test(trust))factualDays[day]=true;else if(/PARTIAL|PRELIM/.test(trust))partialDays[day]=true;
    }
  });
  // A day with any partial source row is not counted as exact.
  Object.keys(partialDays).forEach(function(day){delete factualDays[day];});
  var dayRows=[],weekGroups={},monthGroups={};
  Object.keys(items).forEach(function(k){
    var x=items[k],periodEnd=sellmonitorGithubWbInnerAddDays_(x.day,0);
    dayRows.push(['ДЕНЬ',x.day,periodEnd,1,1,storeId,x.nm,x.article,x.productGroup,x.productSubgroup].concat(x.metrics,[x.trust,x.source,x.loaded,sellmonitorGithubWbInnerRowKey_('D',x.day,storeId,x.nm)]));
    var ws=sellmonitorGithubWbInnerWeekStart_(x.day),ms=sellmonitorGithubWbInnerMonthStart_(x.day),wk=ws+'|'+x.nm,mk=ms+'|'+x.nm;
    if(!weekGroups[wk])weekGroups[wk]={grain:'НЕДЕЛЯ',start:ws,end:sellmonitorGithubWbInnerAddDays_(ws,6),store:storeId,nm:x.nm,article:x.article,group:x.productGroup,subgroup:x.productSubgroup,items:[],dates:{}};
    if(!monthGroups[mk])monthGroups[mk]={grain:'МЕСЯЦ',start:ms,end:Utilities.formatDate(new Date(Date.UTC(Number(ms.slice(0,4)),Number(ms.slice(5,7)),0)),'UTC','yyyy-MM-dd'),store:storeId,nm:x.nm,article:x.article,group:x.productGroup,subgroup:x.productSubgroup,items:[],dates:{}};
    weekGroups[wk].items.push(x);weekGroups[wk].dates[x.day]=1;
    monthGroups[mk].items.push(x);monthGroups[mk].dates[x.day]=1;
  });
  function rollup_(g,grain){
    var days=Object.keys(g.dates).length, expected=(new Date(Date.UTC(Number(g.start.slice(0,4)),Number(g.start.slice(5,7))-1,Number(g.start.slice(8,10)))).getTime());
    var expectedDays=grain==='НЕДЕЛЯ'?7:(new Date(Date.UTC(Number(g.start.slice(0,4)),Number(g.start.slice(5,7)),0)).getUTCDate());
    var sums=new Array(metricHeaders.length).fill(0),seen=new Array(metricHeaders.length).fill(0),last=g.items.slice().sort(function(a,b){return a.day<b.day?-1:1;}).pop(),anyPartial=false,allExact=true,source='',loaded='';
    g.items.forEach(function(it){
      if(/PARTIAL|PRELIM/.test(it.trust))anyPartial=true;
      if(!/FACTUAL/.test(it.trust))allExact=false;
      if(it.source)source=it.source;
      if(it.loaded&&(!loaded||String(it.loaded)>String(loaded)))loaded=it.loaded;
      it.metrics.forEach(function(v,i){if(v!==''&&v!=null&&isFinite(Number(v))){sums[i]+=Number(v);seen[i]++;}});
    });
    var vals=metricHeaders.map(function(h,i){return seen[i]===g.items.length?sums[i]:'';});
    var ix={};metricHeaders.forEach(function(h,i){ix[h]=i;});
    var profit=ix.profit_rub!=null?vals[ix.profit_rub]:'',revenue=ix.revenue_rub!=null?vals[ix.revenue_rub]:'',expenses=ix.expenses_total_rub!=null?vals[ix.expenses_total_rub]:'',sales=ix.net_sales_qty!=null?vals[ix.net_sales_qty]:'';
    if(ix.margin!=null)vals[ix.margin]=typeof profit==='number'&&typeof revenue==='number'&&revenue!==0?profit/revenue:'';
    if(ix.roi!=null)vals[ix.roi]=typeof profit==='number'&&typeof expenses==='number'&&expenses!==0?profit/expenses:'';
    if(ix.buyout!=null)vals[ix.buyout]='';
    if(ix.avg_profit_per_unit_rub!=null)vals[ix.avg_profit_per_unit_rub]=typeof profit==='number'&&typeof sales==='number'&&sales!==0?profit/sales:'';
    if(ix.stock_qty_snapshot!=null)vals[ix.stock_qty_snapshot]=last?last.metrics[ix.stock_qty_snapshot]:'';
    if(ix.stock_prime_cost_snapshot_rub!=null)vals[ix.stock_prime_cost_snapshot_rub]=last?last.metrics[ix.stock_prime_cost_snapshot_rub]:'';
    var status=allExact&&days===expectedDays?'FACTUAL_COMPLETE':(anyPartial?'PRELIMINARY':'PARTIAL_COVERAGE');
    return [grain,g.start,g.end,days,expectedDays,g.store,g.nm,g.article,g.group,g.subgroup].concat(vals,[status,source,loaded,sellmonitorGithubWbInnerRowKey_(grain==='НЕДЕЛЯ'?'W':'M',g.start,g.store,g.nm)]);
  }
  var weekRows=Object.keys(weekGroups).map(function(k){return rollup_(weekGroups[k],'НЕДЕЛЯ');});
  var monthRows=Object.keys(monthGroups).map(function(k){return rollup_(monthGroups[k],'МЕСЯЦ');});
  dayRows.sort(function(a,b){return String(a[1])===String(b[1])?String(a[6]).localeCompare(String(b[6])):String(a[1]).localeCompare(String(b[1]));});
  weekRows.sort(function(a,b){return String(a[1])===String(b[1])?String(a[6]).localeCompare(String(b[6])):String(a[1]).localeCompare(String(b[1]));});
  monthRows.sort(function(a,b){return String(a[1])===String(b[1])?String(a[6]).localeCompare(String(b[6])):String(a[1]).localeCompare(String(b[1]));});
  return {day:dayRows,week:weekRows,month:monthRows,metricHeaders:metricHeaders,sourceRows:sourceRowsCount,min:sourceMin,max:sourceMax,factualDays:Object.keys(factualDays).length,partialDays:Object.keys(partialDays).length};
}

function sellmonitorGithubWbInnerEnsureSheet_(ss,name,headers){
  var sh=ss.getSheetByName(name);
  if(!sh)sh=ss.insertSheet(name);
  if(sh.getLastRow()===0)sh.getRange(1,1,1,headers.length).setValues([headers]);
  else{
    var existing=sh.getRange(1,1,1,headers.length).getDisplayValues()[0];
    if(JSON.stringify(existing)!==JSON.stringify(headers))throw new Error('WB_INNER_TARGET_SCHEMA_MISMATCH:'+name);
  }
  sh.setFrozenRows(1);
  sh.getRange(1,1,1,headers.length).setFontWeight('bold').setBackground('#234f6e').setFontColor('#ffffff');
  return sh;
}

function sellmonitorGithubWbInnerUpsert_(ss,sh,headers,rows,storeName){
  var keyCol=headers.length,last=sh.getLastRow(),old=last>1?sh.getRange(2,1,last-1,keyCol).getValues():[],map={},duplicates=0;
  old.forEach(function(r,i){var k=String(r[keyCol-1]||'');if(!k)return;if(map[k])duplicates++;map[k]=i+2;});
  if(duplicates)throw new Error('WB_INNER_TARGET_DUPLICATE_KEYS:'+sh.getName()+':'+duplicates);
  var archive=sellmonitorGithubWbInnerEnsureSheet_(ss,SMC_WB_INNER_SUMMARY_V1.tabs.archive,['Время архива','Лист','record_key','Старая строка JSON']);
  var newRows=[],changedRows=[],archiveRows=[],archived=0,skippedIncomplete=0,verifiedRows=[];
  rows.forEach(function(r){
    var key=String(r[keyCol-1]||''),at=map[key];
    if(!at){newRows.push(r);verifiedRows.push(r);return;}
    var prior=old[at-2],priorStatus=String(prior[keyCol-4]||''),nextStatus=String(r[keyCol-4]||'');
    if(/^FACTUAL/.test(priorStatus)&&!/^FACTUAL/.test(nextStatus)){skippedIncomplete++;verifiedRows.push(prior);return;}
    var sameData=prior.slice(0,keyCol-2).concat(prior.slice(keyCol-1)).join('\u001f')===r.slice(0,keyCol-2).concat(r.slice(keyCol-1)).join('\u001f');
    if(!sameData){archiveRows.push([new Date(),sh.getName(),key,JSON.stringify(prior)]);changedRows.push({row:at,values:r});verifiedRows.push(r);}
    else verifiedRows.push(prior);
  });
  if(archiveRows.length){var ar=archive.getLastRow()+1;if(ar+archiveRows.length-1>archive.getMaxRows())archive.insertRowsAfter(archive.getMaxRows(),Math.max(100,ar+archiveRows.length-1-archive.getMaxRows()));archive.getRange(ar,1,archiveRows.length,4).setValues(archiveRows);archived=archiveRows.length;}
  changedRows.sort(function(a,b){return a.row-b.row;});
  var i=0;
  while(i<changedRows.length){var j=i+1;while(j<changedRows.length&&changedRows[j].row===changedRows[j-1].row+1)j++;sh.getRange(changedRows[i].row,1,j-i,keyCol).setValues(changedRows.slice(i,j).map(function(x){return x.values;}));i=j;}
  if(newRows.length){
    var atNew=sh.getLastRow()+1;
    for(var n=0;n<newRows.length;n+=SMC_WB_INNER_SUMMARY_V1.chunk){
      var part=newRows.slice(n,n+SMC_WB_INNER_SUMMARY_V1.chunk),need=atNew+part.length-1;
      if(need>sh.getMaxRows())sh.insertRowsAfter(sh.getMaxRows(),Math.max(100,need-sh.getMaxRows()));
      sh.getRange(atNew,1,part.length,keyCol).setValues(part);atNew+=part.length;
    }
  }
  SpreadsheetApp.flush();
  var finalLast=sh.getLastRow(),final=finalLast>1?sh.getRange(2,1,finalLast-1,keyCol).getValues():[],seen={},finalMap={},min='',max='';
  final.forEach(function(r){var k=String(r[keyCol-1]||'');if(!k)throw new Error('WB_INNER_TARGET_EMPTY_KEY:'+sh.getName());if(seen[k])throw new Error('WB_INNER_READBACK_DUPLICATE:'+sh.getName());seen[k]=true;finalMap[k]=r;var d=String(r[1]||'');if(!min||d<min)min=d;if(!max||d>max)max=d;});
  var verified=0;
  verifiedRows.forEach(function(r){var k=String(r[keyCol-1]||''),rb=finalMap[k];if(!rb)throw new Error('WB_INNER_READBACK_MISSING:'+sh.getName()+':'+k);if(rb.join('|')!==r.join('|'))throw new Error('WB_INNER_READBACK_MISMATCH:'+sh.getName()+':'+k);verified++;});
  if(final.length!==Object.keys(finalMap).length)throw new Error('WB_INNER_READBACK_COUNT_MISMATCH:'+sh.getName());
  if(final.length>1){
    var fmts=new Array(keyCol).fill('');fmts[1]='dd.mm.yyyy';fmts[2]='dd.mm.yyyy';
    headers.forEach(function(h,i){if(/_rub$/.test(String(h)))fmts[i]='#,##0.00';else if(/^(margin|roi|buyout)$/.test(String(h)))fmts[i]='0.0%';else if(/(_qty|_count)$/.test(String(h)))fmts[i]='#,##0.##';});
    var fmtStart=-1,fmtCurrent='';for(var fi=0;fi<=fmts.length;fi++){var fmt=fi<fmts.length?fmts[fi]:'';if(fmt!==fmtCurrent){if(fmtCurrent)sh.getRange(2,fmtStart+1,final.length,fi-fmtStart).setNumberFormat(fmtCurrent);fmtCurrent=fmt;fmtStart=fi;}}
    if(!sh.getFilter())sh.getRange(1,1,finalLast,keyCol).createFilter();
  }
  var digest=Utilities.computeDigest(Utilities.DigestAlgorithm.SHA_256,JSON.stringify(final),Utilities.Charset.UTF_8).map(function(b){var v=(b<0?b+256:b).toString(16);return v.length===1?'0'+v:v;}).join('');
  return {sheet:sh.getName(),rows:final.length,new_rows:newRows.length,changed_rows:changedRows.length,archived_rows:archived,skipped_incomplete_rows:skippedIncomplete,min:min,max:max,verified:verified,sha256:digest};
}

function sellmonitorGithubPublishWbInnerPeriods_(onlyStore){
  var reg=SpreadsheetApp.openById(SMC_WB_INNER_SUMMARY_V1.registryId).getSheetByName(SMC_WB_INNER_SUMMARY_V1.registrySheet);
  if(!reg)throw new Error('WB_INNER_REGISTRY_MISSING');
  var regLast=reg.getLastRow(),registry=regLast>1?reg.getRange(2,1,regLast-1,27).getValues():[];
  var parts={},partHeaders=['part_id','platform','shop_id','dataset','layer','period_start','period_end','part_number','schema_version','file_id','file_url','source_endpoint','source_kind','run_id','fetched_at','row_count','distinct_key_count','min_date','max_date','checksum','status','qc_status','error','supersedes_part_id','source_spreadsheet_id','source_sheet','folder_id'];
  registry.forEach(function(r){
    if(String(r[1]||'')!=='WB'||String(r[3]||'')!=='inner_finance'||!/inner_daily_rows$/.test(String(r[4]||''))||String(r[20]||'')!=='OK'||!/PASS/.test(String(r[21]||''))||!r[9])return;
    var shop=String(r[2]||''),period=sellmonitorGithubWbInnerDateKey_(r[5],Session.getScriptTimeZone()),k=shop+'|'+period,stamp=r[14] instanceof Date?r[14].getTime():Date.parse(String(r[14]||'')),old=parts[k];
    if(!old||stamp>(old.stamp||0))parts[k]={shop:shop,fileId:String(r[9]),period:period,stamp:isFinite(stamp)?stamp:0,partId:String(r[0]),expectedRows:Number(r[15]),expectedDistinct:Number(r[16]),checksum:String(r[19]||'')};
  });
  var results=[];
  var storeKeys=Object.keys(SMC_WB_INNER_SUMMARY_V1.stores);
  if(onlyStore&&storeKeys.indexOf(onlyStore)<0)throw new Error('WB_INNER_UNKNOWN_STORE:'+onlyStore);
  storeKeys.filter(function(shop){return !onlyStore||shop===onlyStore;}).forEach(function(shop){
    var cfg=SMC_WB_INNER_SUMMARY_V1.stores[shop];
    try{
      var chosen=Object.keys(parts).map(function(k){return parts[k];}).filter(function(p){return p.shop===shop;}).sort(function(a,b){return a.period<b.period?-1:1;});
      if(!chosen.length)throw new Error('WB_INNER_NO_TRUSTED_PARTS:'+shop);
      var headers=null,allRows=[],sourceIds=[],sourceChecks=[];
      chosen.forEach(function(p){
        var part=SpreadsheetApp.openById(p.fileId),facts=part.getSheetByName('facts');
        if(!facts)throw new Error('WB_INNER_FACTS_TAB_MISSING:'+p.fileId);
        var last=facts.getLastRow();if(last<2)throw new Error('WB_INNER_SOURCE_FACTS_EMPTY_PART:'+p.partId);
        var h=facts.getRange(1,1,1,46).getValues()[0].map(String);
        if(headers&&JSON.stringify(headers)!==JSON.stringify(h))throw new Error('WB_INNER_SOURCE_SCHEMA_VARIANCE');
        headers=h;
        var partRows=0,partKeys={};
        for(var start=2;start<=last;start+=SMC_WB_INNER_SUMMARY_V1.chunk){
          var n=Math.min(SMC_WB_INNER_SUMMARY_V1.chunk,last-start+1);
          facts.getRange(start,1,n,46).getValues().forEach(function(r){if(r.some(function(v){return v!==''&&v!=null;})){allRows.push(r);partRows++;var rk=String(r[45]||'');if(!rk)throw new Error('WB_INNER_PART_KEY_MISSING:'+p.partId);if(partKeys[rk])throw new Error('WB_INNER_PART_DUPLICATE_KEY:'+p.partId+':'+rk);partKeys[rk]=1;}});
        }
        if(partRows!==p.expectedRows||Object.keys(partKeys).length!==p.expectedDistinct)throw new Error('WB_INNER_PART_COUNT_MISMATCH:'+p.partId+':expected='+p.expectedRows+'/'+p.expectedDistinct+':actual='+partRows+'/'+Object.keys(partKeys).length);
        sourceIds.push(p.fileId);sourceChecks.push({part_id:p.partId,rows:partRows,distinct:Object.keys(partKeys).length,checksum:p.checksum});
      });
      if(!headers||!allRows.length)throw new Error('WB_INNER_SOURCE_FACTS_EMPTY:'+shop);
      var built=sellmonitorGithubWbInnerBuildRows_(allRows,headers,shop,Session.getScriptTimeZone());
      var metricHeaders=built.metricHeaders,head=['Период','Начало','Конец','Дней с данными','Ожидаемо дней','Магазин ID','nmId','Артикул продавца','Группа','Подгруппа'].concat(metricHeaders,['Статус источника','Источник','Загружено','Ключ']);
      var ss=SpreadsheetApp.openById(cfg.id);
      var day=sellmonitorGithubWbInnerEnsureSheet_(ss,SMC_WB_INNER_SUMMARY_V1.tabs.day,head);
      var week=sellmonitorGithubWbInnerEnsureSheet_(ss,SMC_WB_INNER_SUMMARY_V1.tabs.week,head);
      var month=sellmonitorGithubWbInnerEnsureSheet_(ss,SMC_WB_INNER_SUMMARY_V1.tabs.month,head);
      var res={
        store:cfg.name,ok:true,source_parts:sourceIds.length,source_ids:sourceIds,
        source_rows:built.sourceRows,source_min:built.min,source_max:built.max,
        factual_days:built.factualDays,partial_days:built.partialDays,
        day:sellmonitorGithubWbInnerUpsert_(ss,day,head,built.day,cfg.name),
        week:sellmonitorGithubWbInnerUpsert_(ss,week,head,built.week,cfg.name),
        month:sellmonitorGithubWbInnerUpsert_(ss,month,head,built.month,cfg.name)
      };
      var periodChanges=res.day.new_rows+res.day.changed_rows+res.week.new_rows+res.week.changed_rows+res.month.new_rows+res.month.changed_rows;
      var dashboardView=ss.getSheetByName('00_Динамика');var dashboardHasCharts=!!(dashboardView&&dashboardView.getCharts().length);res.dashboard=(shop==='sanych_wb'&&periodChanges===0&&dashboardHasCharts)?{ok:true,skipped:true,reason:'WB_INNER_NO_PERIOD_CHANGES'}:sellmonitorGithubWbBuildDashboard_(ss,cfg.name);
      results.push(res);
      var log=sellmonitorGithubWbInnerEnsureSheet_(ss,SMC_WB_INNER_SUMMARY_V1.tabs.log,['Время запуска','Магазин','Источник частей','Строк фактов','Период с','Период по','Дней точных','Дней предварительных','Строк день/неделя/месяц','Новых','Изменённых','Архивировано','SHA-256']);
      var counts=[res.day.rows,res.week.rows,res.month.rows],newCount=res.day.new_rows+res.week.new_rows+res.month.new_rows,changeCount=res.day.changed_rows+res.week.changed_rows+res.month.changed_rows,archCount=res.day.archived_rows+res.week.archived_rows+res.month.archived_rows;
      var logRow=[new Date(),cfg.name,sourceIds.join(','),built.sourceRows,built.min,built.max,built.factualDays,built.partialDays,counts.join('/'),newCount,changeCount,archCount,[res.day.sha256,res.week.sha256,res.month.sha256].join('/')];
      var at=log.getLastRow()+1;if(at>log.getMaxRows())log.insertRowsAfter(log.getMaxRows(),100);
      log.getRange(at,1,1,13).setValues([logRow]);
      var check=log.getRange(at,1,1,13).getValues()[0];
      if(String(check[12])!==String(logRow[12]))throw new Error('WB_INNER_STORE_LOG_READBACK_FAILED:'+cfg.name);
      SpreadsheetApp.flush();
    }catch(e){results.push({store:cfg.name,ok:false,error:String(e&&e.message||e)});}
  });
  var expectedCount=onlyStore?1:3;
  var ok=results.length===expectedCount&&results.every(function(x){return x.ok===true;});
  return {ok:ok,action:'publish_wb_inner_periods',stores:results};
}


function sellmonitorGithubWbBuildDashboard_(ss,storeName){
  var specs=[{grain:'day',tab:SMC_WB_INNER_SUMMARY_V1.tabs.day,label:'Дни'},
    {grain:'week',tab:SMC_WB_INNER_SUMMARY_V1.tabs.week,label:'Недели'},
    {grain:'month',tab:SMC_WB_INNER_SUMMARY_V1.tabs.month,label:'Месяцы'}];
  var data=ss.getSheetByName('__Динамика_данные');if(!data)data=ss.insertSheet('__Динамика_данные');
  var archive=ss.getSheetByName('Архив_Динамика_данные');
  if(!archive){archive=ss.insertSheet('Архив_Динамика_данные');archive.getRange(1,1,1,4).setValues([['Архивировано','Магазин','Период','Старые строки JSON']]);}
  var starts=[1,8,15],tables=[],latest={},chartsMade=0;
  function add(target,value){if(value===''||value==null||typeof value==='boolean'||!isFinite(Number(value)))target.unknown=true;else target.sum+=Number(value);}
  specs.forEach(function(spec,si){
    var src=ss.getSheetByName(spec.tab);if(!src)throw new Error('WB_DASHBOARD_FACT_TAB_MISSING:'+spec.tab);
    var count=Math.max(0,src.getLastRow()-1),groups={};
    if(count){
      var base=src.getRange(2,1,count,5).getValues(),metrics=src.getRange(2,11,count,31).getValues();
      for(var i=0;i<count;i++){
        var m=metrics[i],days=Number(base[i][3]),expected=Number(base[i][4]);
        if(['FACTUAL_INNER_ANALYTICS','FACTUAL_COMPLETE'].indexOf(String(m[30]||''))<0||!base[i][1]||!base[i][2]||
          (isFinite(expected)&&expected>0&&days<expected))continue;
        var start=sellmonitorGithubWbInnerDateKey_(base[i][1],ss.getSpreadsheetTimeZone());
        var end=sellmonitorGithubWbInnerDateKey_(base[i][2],ss.getSpreadsheetTimeZone());
        if(!start||!end||(spec.grain==='day'&&start!==end))continue;
        var key=start+'|'+end,g=groups[key];
        if(!g)g=groups[key]={start:start,end:end,orders:{sum:0,unknown:false},revenue:{sum:0,unknown:false},profit:{sum:0,unknown:false},ads:{sum:0,unknown:false}};
        add(g.orders,m[0]);add(g.revenue,m[6]);add(g.ads,m[15]);add(g.profit,m[22]);
      }
    }
    var periods=Object.keys(groups).map(function(k){return groups[k];}).sort(function(a,b){return a.start.localeCompare(b.start);});
    var rows=[['Период','Заказы, шт','Выручка, ₽','Прибыль, ₽','Реклама, ₽','Маржа, %']];
    periods.forEach(function(g){
      var revenue=g.revenue.unknown?null:g.revenue.sum,profit=g.profit.unknown?null:g.profit.sum;
      rows.push([new Date(g.start+'T12:00:00+03:00'),g.orders.unknown?null:g.orders.sum,revenue,profit,
        g.ads.unknown?null:g.ads.sum,revenue==null||profit==null||revenue===0?null:profit/revenue]);
    });
    var col=starts[si],oldLast=Math.max(1,data.getLastRow()),old=data.getRange(1,col,oldLast,6).getValues().filter(function(r){return r.some(function(v){return v!==''&&v!=null;});});
    function norm(a){return a.map(function(r){return r.map(function(v){if(v==null||v==='')return '';if(v instanceof Date)return Utilities.formatDate(v,ss.getSpreadsheetTimeZone(),'yyyy-MM-dd');if(typeof v==='number')return Math.round(v*1000000)/1000000;return v;});});}
    var changed=JSON.stringify(norm(old))!==JSON.stringify(norm(rows));
    if(changed&&old.length>1){
      var save=old.slice(1).map(function(r){return [new Date(),storeName,spec.label,JSON.stringify(norm([r])[0])];});
      var at=archive.getLastRow()+1;if(archive.getMaxRows()<at+save.length-1)archive.insertRowsAfter(archive.getMaxRows(),at+save.length-1-archive.getMaxRows());
      archive.getRange(at,1,save.length,4).setValues(save);SpreadsheetApp.flush();
      if(archive.getRange(at,4,save.length,1).getValues().some(function(r,i){return String(r[0])!==save[i][3];}))throw new Error('WB_DASHBOARD_ARCHIVE_READBACK_FAILED:'+spec.grain);
    }
    if(changed){
      if(data.getMaxRows()<rows.length)data.insertRowsAfter(data.getMaxRows(),rows.length-data.getMaxRows());
      data.getRange(1,col,Math.max(1,old.length,rows.length),6).clearContent();
      data.getRange(1,col,rows.length,6).setValues(rows);
      data.getRange(1,col,1,6).setBackground('#17365D').setFontColor('#FFFFFF').setFontWeight('bold');
      if(rows.length>1){data.getRange(2,col+1,rows.length-1,1).setNumberFormat('#,##0');data.getRange(2,col+2,rows.length-1,3).setNumberFormat('#,##0.00');data.getRange(2,col+5,rows.length-1,1).setNumberFormat('0.0%');}
      SpreadsheetApp.flush();
    }
    if(JSON.stringify(norm(data.getRange(1,col,rows.length,6).getValues()))!==JSON.stringify(norm(rows)))throw new Error('WB_DASHBOARD_DATA_READBACK_FAILED:'+spec.grain);
    latest[spec.grain]=periods.length?periods[periods.length-1].end:'';
    tables.push({grain:spec.grain,label:spec.label,col:col,rows:rows,periods:periods.length});
  });
  data.hideSheet();
  var view=ss.getSheetByName('00_Динамика');if(!view)view=ss.insertSheet('00_Динамика',0);else view.showSheet();
  var rnpCharts=ss.getSheetByName('05_Графики_РНП');
  if(String(storeName).toLowerCase().indexOf('сан')>=0&&rnpCharts&&rnpCharts.getCharts().length>=9){
    view.setHiddenGridlines(true);view.setFrozenRows(2);view.setTabColor('#17365D');
    view.getRange('A1').setValue('Саныч · динамика по дням, неделям и месяцам').setBackground('#17365D').setFontColor('#FFFFFF').setFontFamily('Arial').setFontSize(16).setFontWeight('bold');
    view.getRange('A2').setValue('Графики и разрезы РНП находятся на листе 05_Графики_РНП. Витрина данных обновлена: дни '+latest.day+'; недели '+latest.week+'; месяцы '+latest.month+'.').setFontColor('#44546A').setWrap(true);
    SpreadsheetApp.flush();
    return {ok:true,chart_count:rnpCharts.getCharts().length,dashboard_sheet:'05_Графики_РНП',dashboard_skipped:true,reason:'SANYCH_DEDICATED_RNP_CHARTS',exact_periods:{day:tables[0].periods,week:tables[1].periods,month:tables[2].periods},latest_exact:latest,archived_rows:archive.getLastRow()-1};
  }
  view.getCharts().forEach(function(ch){view.removeChart(ch);});
  view.getRange(1,1,2,21).clearContent();view.setHiddenGridlines(true);view.setFrozenRows(2);view.setTabColor('#17365D');
  view.getRange('A1').setValue(storeName+' · Динамика по точным фактам').setBackground('#17365D').setFontColor('#FFFFFF').setFontFamily('Arial').setFontSize(16).setFontWeight('bold');
  view.getRange('A2').setValue('Sellmonitor Inner · только FACTUAL_INNER_ANALYTICS и полностью закрытые периоды; неполные и неизвестные значения пропущены.').setFontColor('#44546A').setWrap(true);
  view.setColumnWidths(1,21,110);
  tables.forEach(function(t,ti){
    if(t.rows.length<2)return;
    var baseRow=4+ti*20,domain=data.getRange(1,t.col,t.rows.length,1);
    function addChart(title,ranges,rowOff,colOff,vTitle){
      var b=view.newChart().setChartType(Charts.ChartType.LINE);ranges.forEach(function(r){b.addRange(r);});
      b.setNumHeaders(1).setPosition(baseRow+rowOff,1+colOff,0,0)
        .setOption('title',t.label+' · '+title).setOption('hAxis',{title:'Период',format:'dd.MM.yy'})
        .setOption('vAxis',{title:vTitle}).setOption('legend',{position:'bottom'})
        .setOption('interpolateNulls',false).setOption('width',620).setOption('height',320)
        .setOption('backgroundColor','#FFFFFF');view.insertChart(b.build());chartsMade++;
    }
    var orders=data.getRange(1,t.col+1,t.rows.length,1),margin=data.getRange(1,t.col+5,t.rows.length,1);
    if(t.rows.slice(1).some(function(r){return r[1]!=null;}))addChart('Заказы',[domain,orders],0,0,'Штук');
    var moneyRanges=[],moneyNames=[];
    [[2,'Выручка'],[3,'Прибыль'],[4,'Реклама']].forEach(function(x){if(t.rows.slice(1).some(function(r){return r[x[0]]!=null;})){moneyRanges.push(data.getRange(1,t.col+x[0],t.rows.length,1));moneyNames.push(x[1]);}});
    if(moneyRanges.length)addChart(moneyNames.join(' и '),[domain].concat(moneyRanges),0,7,'Рубли');
    if(t.rows.slice(1).some(function(r){return r[5]!=null;}))addChart('Маржа',[domain,margin],0,14,'Доля');
  });
  SpreadsheetApp.flush();
  if(view.getCharts().length!==chartsMade)throw new Error('WB_DASHBOARD_CHART_READBACK_MISMATCH:'+view.getCharts().length+':'+chartsMade);
  return {ok:true,chart_count:chartsMade,exact_periods:{day:tables[0].periods,week:tables[1].periods,month:tables[2].periods},latest_exact:latest,archived_rows:archive.getLastRow()-1};
}

function sellmonitorGithubResumeOzonExport_() {
  var triggers = ScriptApp.getProjectTriggers();
  var existingContinuation = triggers.some(function(trigger) {
    return trigger.getHandlerFunction() === 'exportDOM_continue';
  });
  if (existingContinuation) {
    return {ok:true,continued_existing_export:true,continuation_pending:true,reason:'existing_continuation_trigger'};
  }
  var existingStart = triggers.some(function(trigger) {
    return trigger.getHandlerFunction() === 'domExportOzonOrders';
  });
  if (existingStart) {
    return {ok:true,continued_existing_export:true,continuation_pending:true,reason:'existing_export_trigger'};
  }
  if (typeof domExportOzonOrders !== 'function') {
    throw new Error('OZON_EXPORT_WRAPPER_MISSING');
  }
  ScriptApp.newTrigger('domExportOzonOrders').timeBased().after(60 * 1000).create();
  return {ok:true,started_fresh_export:true,continuation_pending:true,reason:'scheduled_existing_ozon_export_wrapper'};
}

function doPost(e) {
  try {
    var body = JSON.parse((e && e.postData && e.postData.contents) || '{}');
    var action = String(body.action || 'tick');

    sellmonitorGithubWebAuth_(body.token || body.key);

    if (action === 'seed_wb_tokens') {
      var seeded = sellmonitorGithubSeedWbTokens_(body);
      return sellmonitorGithubWebJson_({
        ok: seeded && seeded.ok === true,
        action: action,
        result: seeded
      });
    }

    if (action === 'bridge_ping') {
      return sellmonitorGithubWebJson_({ok:true,action:action,version:SMC_GH.VERSION,checkedAt:new Date().toISOString()});
    }

    if (action === 'fbs_ingest_batch') {
      var fbsResult = sellmonitorGithubFbsIngestBatchV1_(body);
      return sellmonitorGithubWebJson_({ok:fbsResult.ok===true,action:action,result:fbsResult});
    }

    if (action === 'fbs_supply_sync') {
      var supplyResult = wbFbsSupplySyncAllStores_();
      return sellmonitorGithubWebJson_({ok:supplyResult.ok===true,action:action,result:supplyResult});
    }

    if (action === 'fbs_targets_health') {
      return sellmonitorGithubWebJson_({ok:true,action:action,result:sellmonitorGithubFbsTargetHealthV1_()});
    }

    if (action === 'health') {
      return sellmonitorGithubWebJson_({
        ok: true,
        action: action,
        result: sellmonitorGithubHealth()
      });
    }

    if (action === 'inner_finance_tick') {
      var financeTick = sellmonitorGithubInnerFinanceTick();
      return sellmonitorGithubWebJson_({
        ok:financeTick && financeTick.ok === true,
        action:action,
        tick:financeTick
      });
    }

    if (action === 'publish_dashboard') {
      return sellmonitorGithubWebJson_(sellmonitorGithubPublishDashboard_(body.dashboard || {}));
    }

    if (action === 'publish_search_positions') {
      return sellmonitorGithubWebJson_(sellmonitorGithubPublishRuntimeSearch_(body.cabinet || '', body.snapshot || {}));
    }

    if (action === 'publish_campaign_settings') {
      return sellmonitorGithubWebJson_(sellmonitorGithubPublishCampaignSettings_(body));
    }

    if (action === 'publish_ad_clusters') {
      return sellmonitorGithubWebJson_(sellmonitorGithubPublishAdClusters_(body));
    }

    if (action === 'resume_ozon_export') {
      var continuationResult = sellmonitorGithubResumeOzonExport_();
      return sellmonitorGithubWebJson_({
        ok:continuationResult.ok === true,
        action:action,
        called:true,
        result:{continuation_result:continuationResult}
      });
    }

    if (action === 'publish_ozon_api_orders') {
      return sellmonitorGithubWebJson_(sellmonitorGithubPublishOzonSummaryOrders_(body));
    }

    if (action === 'publish_ozon_summary_orders') {
      return sellmonitorGithubWebJson_(sellmonitorGithubPublishOzonSummaryOrders_());
    }

    if (action === 'publish_wb_inner_periods') {
      var requestedStore = String(body.store || '').trim();
      return sellmonitorGithubWebJson_(sellmonitorGithubPublishWbInnerPeriods_(requestedStore || null));
    }

    if (action === 'publish_wb_traffic') {
      return sellmonitorGithubWebJson_(sellmonitorGithubPublishWbTraffic_(body));
    }

    if (action === 'publish_wb_penalties_snapshot') {
      return sellmonitorGithubWebJson_(sellmonitorGithubPublishWbPenaltySnapshot_(body));
    }

    if (action === 'publish_wb_finance') {
      return sellmonitorGithubWebJson_(sellmonitorGithubPublishWbFinance_(body));
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


/** Read-only health check over real order IDs and the maximum order timestamp. */
function sellmonitorGithubFbsTargetHealthV1_() {
  var targets=[
    {client:'SANYCH',id:'1v0qt-thJDOw3tLFdZVGhdXJdBM8rDPRz9mHlcHmuuyM'},
    {client:'AIR',id:'1qJvhEOIku7sOMydfrUz0PR5dkcU0sLjv433pa1MFBcM'},
    {client:'HOZYAYUSHKA',id:'1f9dxXeZxkDth8h2C9GA7L7WqAr1-Ok-Oi5YtufLYOOk'}
  ];
  var results=[];
  targets.forEach(function(t) {
    try {
      var ss=SpreadsheetApp.openById(t.id);
      var orders=ss.getSheetByName('📦 FBS Заказы');
      if(!orders)throw new Error('ORDERS_TAB_MISSING');
      var last=orders.getLastRow();
      var head=orders.getRange('A1:F1').getDisplayValues()[0];
      if(String(head[0]||'').indexOf('ID')<0)throw new Error('ORDERS_SCHEMA_MISMATCH');
      var idCount=0,latestRow=0,latestMs=-Infinity;
      if(last>1){
        var rows=orders.getRange(2,1,last-1,2).getValues();
        for(var r=0;r<rows.length;r++){
          var id=String((rows[r]||[])[0]||'').trim();
          if(!/^\d{6,20}$/.test(id))continue;
          idCount++;
          var rawDate=rows[r][1],stamp=NaN;
          if(rawDate instanceof Date)stamp=rawDate.getTime();
          else if(typeof rawDate==='number')stamp=(rawDate-25569)*86400000;
          else if(rawDate)stamp=new Date(rawDate).getTime();
          if(isFinite(stamp)&&stamp>latestMs){latestMs=stamp;latestRow=r+2;}
        }
      }
      var latest=latestRow?orders.getRange(latestRow,1,1,6).getDisplayValues()[0]:[];
      results.push({client:t.client,accessible:true,ordersRows:idCount,
        latestOrderDate:String(latest[1]||''),hasLatestOrderId:!!latest[0],
        columns:Number(orders.getMaxColumns()),analyticsExists:!!ss.getSheetByName('📈 Аналитика')});
    }catch(e){
      results.push({client:t.client,accessible:false,reason:'SPREADSHEET_ACCESS_OR_SCHEMA_ERROR',
        errorType:String(e&&e.name||'Error')});
    }
  });
  return {ok:results.every(function(x){return x.accessible;}),checkedAt:new Date().toISOString(),results:results};
}

function sellmonitorGithubFbsStageSnapshot_() {
  var props = PropertiesService.getScriptProperties(), out = {};
  ['SANYCH','AIR','HOZYAYUSHKA'].forEach(function(client) {
    try { var raw = props.getProperty('GITHUB_FBS_STAGE_' + client); if (raw) out[client] = JSON.parse(raw); } catch (e) {}
  });
  return out;
}

/** Safe bounded FBS upsert; authenticated at the doPost entry point. No external service. */
function sellmonitorGithubFbsIngestBatchV1_(body) {
  // Different stores may recover concurrently. UserLock protects only the short
  // lease claim; long-running Sellmonitor and finance ticks use ScriptLock.
  var client = String(body && body.client || '').trim().toUpperCase();
  if (['SANYCH','AIR','HOZYAYUSHKA'].indexOf(client) < 0) {
    throw new Error('FBS_CLIENT_NOT_ALLOWED');
  }
  var props = PropertiesService.getScriptProperties();
  var traceId = Utilities.getUuid(), startedAt = Date.now();
  function mark(stage) {
    try { props.setProperty('GITHUB_FBS_STAGE_' + client, JSON.stringify({client:client,trace:traceId,stage:stage,elapsedMs:Date.now()-startedAt,at:new Date().toISOString()})); } catch (e) {}
  }
  mark('LEASE_CLAIM_START');
  var leaseKey = 'GITHUB_FBS_INGEST_LEASE_' + client;
  var leaseToken = Utilities.getUuid();
  var leaseMs = 7 * 60 * 1000;
  var lock = LockService.getUserLock();
  if (!lock.tryLock(10000)) throw new Error('GITHUB_FBS_LOCK_BUSY');
  try {
    var current = null;
    try { current = JSON.parse(props.getProperty(leaseKey) || 'null'); } catch (e) {}
    if (current && Number(current.expiresAt) > Date.now()) {
      throw new Error('GITHUB_FBS_LOCK_BUSY');
    }
    props.setProperty(leaseKey, JSON.stringify({
      token: leaseToken, expiresAt: Date.now() + leaseMs
    }));
    mark('LEASE_CLAIMED');
  } finally {
    lock.releaseLock();
  }

  try {
    mark('CORE_START');
    var result = sellmonitorGithubFbsIngestBatchV1Core_(body, mark);
    mark('CORE_DONE');
    return result;
  } catch (e) {
    mark('CORE_ERROR');
    throw e;
  } finally {
    mark('LEASE_RELEASE_START');
    if (lock.tryLock(10000)) {
      try {
        var active = null;
        try { active = JSON.parse(props.getProperty(leaseKey) || 'null'); } catch (e) {}
        if (active && active.token === leaseToken) props.deleteProperty(leaseKey);
        mark('LEASE_RELEASED');
      } finally {
        lock.releaseLock();
      }
    }
  }
}

function sellmonitorGithubFbsSaveIdIndex_(cache, prefix, client, lastRow, index) {
  var json=JSON.stringify(index),chunkSize=60000,parts=[];
  for(var i=0;i<json.length;i+=chunkSize)parts.push(json.slice(i,i+chunkSize));
  for(var p=0;p<parts.length;p++)cache.put(prefix+'part_'+p,parts[p],21600);
  cache.put(prefix+'meta',JSON.stringify({client:client,lastRow:lastRow,parts:parts.length}),21600);
}

function sellmonitorGithubFbsLoadIdIndex_(sheet, client, lastRow, mark, forceRebuild) {
  var cache=CacheService.getScriptCache(),prefix='SMC_FBS_ID_INDEX_V2_',index=null,indexLast=0;
  if(!forceRebuild){
    try{
      var meta=JSON.parse(cache.get(prefix+'meta')||'null');
      if(meta&&meta.client===client&&Number(meta.parts)>0&&Number(meta.parts)<20&&Number(meta.lastRow)<=lastRow){
        var chunks=[];
        for(var i=0;i<meta.parts;i++){
          var part=cache.get(prefix+'part_'+i);
          if(part===null){chunks=[];break;}
          chunks.push(part);
        }
        if(chunks.length===meta.parts){index=JSON.parse(chunks.join(''));indexLast=Number(meta.lastRow);}
      }
    }catch(ignoreIndexCache){index=null;}
  }
  var rebuilt=false;
  if(!index||indexLast>lastRow){
    mark('ID_INDEX_BUILD_START');
    index={};
    if(lastRow>1){
      var values=sheet.getRange(2,1,lastRow-1,1).getDisplayValues();
      for(var r=0;r<values.length;r++){
        var id=String((values[r]||[])[0]||'');
        if(id&&!Object.prototype.hasOwnProperty.call(index,id))index[id]=r+2;
      }
    }
    indexLast=lastRow;rebuilt=true;
    mark('ID_INDEX_BUILD_DONE_'+Object.keys(index).length);
  }else if(indexLast<lastRow){
    mark('ID_INDEX_TAIL_READ_START');
    var tail=sheet.getRange(indexLast+1,1,lastRow-indexLast,1).getDisplayValues();
    for(var t=0;t<tail.length;t++){
      var tailId=String((tail[t]||[])[0]||'');
      if(tailId&&!Object.prototype.hasOwnProperty.call(index,tailId))index[tailId]=indexLast+1+t;
    }
    indexLast=lastRow;rebuilt=true;
    mark('ID_INDEX_TAIL_READ_DONE');
  }
  if(rebuilt)sellmonitorGithubFbsSaveIdIndex_(cache,prefix,client,indexLast,index);
  return {map:index,lastRow:indexLast,cache:cache,prefix:prefix,client:client};
}


function sellmonitorGithubFbsIngestBatchV1Core_(body, mark) {
  mark = typeof mark === 'function' ? mark : function() {};
  var stores={
    SANYCH:'1v0qt-thJDOw3tLFdZVGhdXJdBM8rDPRz9mHlcHmuuyM',
    AIR:'1qJvhEOIku7sOMydfrUz0PR5dkcU0sLjv433pa1MFBcM',
    HOZYAYUSHKA:'1f9dxXeZxkDth8h2C9GA7L7WqAr1-Ok-Oi5YtufLYOOk'
  };
  var client=String(body.client||'');
  var spreadsheetId=stores[client];
  if(!spreadsheetId)throw new Error('FBS_CLIENT_NOT_ALLOWED');
  var batch=body.orders;
  if(!Array.isArray(batch)||batch.length<1||batch.length>1000)throw new Error('FBS_BATCH_SIZE_INVALID');
  function str(x,limit) {
    var s=String(x==null?'':x).slice(0,limit||300);
    if(/^[=+@]/.test(s))s="'"+s;
    return s;
  }
  function rub(x) {
    var v=Number(x);
    return isFinite(v)&&v>=0&&v<1000000000 ? Math.round(v)/100 : 0;
  }
  function human(a,b){
    var sa={new:'Новый',confirm:'На сборке',complete:'Передан в доставку',cancel:'Отменён продавцом'};
    var wb={waiting:'WB ожидает заказ',sorted:'Отсортирован WB',sold:'Продан',
      canceled:'Отменён WB',canceled_by_client:'Отказ покупателя',
      declined_by_client:'Отмена покупателем в первый час',ready_for_pickup:'Готов к получению',
      defect:'Брак',canceled_by_missed_call:'Отмена из-за недозвона'};
    return [a?(sa[a]||a):'',b?(wb[b]||b):''].filter(Boolean).join(' / ')||'Статус не получен';
  }
  var fbsStarted=Date.now(); console.log('FBS_STAGE_BEGIN client='+client+' orders='+batch.length);
  var parsed=[],dedupe={};
  batch.forEach(function(v){
    if(!v||typeof v!=='object')throw new Error('FBS_ROW_INVALID');
    var id=str(v.id,32);
    if(!/^\d{6,20}$/.test(id))throw new Error('FBS_ID_INVALID');
    if(dedupe[id])throw new Error('FBS_DUPLICATE_WITHIN_BATCH');
    dedupe[id]=true;
    var created=new Date(String(v.createdAt||''));
    if(isNaN(created.getTime()))throw new Error('FBS_DATE_INVALID');
    var ageDays=(Date.now()-created.getTime())/86400000;
    if(ageDays>120||ageDays< -1)throw new Error('FBS_DATE_OUTSIDE_RECOVERY_WINDOW');
    var seller=str(v.supplierStatus,90),wb=str(v.wbStatus,90);
    if(!seller||!wb)throw new Error('FBS_MISSING_CONFIRMED_STATUS');
    parsed.push({id:id,created:created,seller:seller,wb:wb,source:v});
  });
  // GitHub recovery workflow serializes all calls to this endpoint. Do not
  // share the adapter-wide UserLock with the independent finance sync.

    console.log('FBS_STAGE_PARSE ms='+(Date.now()-fbsStarted)+' client='+client+' orders='+parsed.length);
    var authToken=ScriptApp.getOAuthToken();
    var apiBase='https://sheets.googleapis.com/v4/spreadsheets/'+encodeURIComponent(spreadsheetId);
    var orderSheet="'📦 FBS Заказы'";
    function fetchSheetApi(url,options){
      var opt=options||{}; opt.headers=opt.headers||{};
      opt.headers.Authorization='Bearer '+authToken; opt.muteHttpExceptions=true; opt.timeoutSeconds=30;
      return UrlFetchApp.fetch(url,opt);
    }
    function sheetApiFailure(response){
      try {
        var body=JSON.parse(response.getContentText()).error||{};
        var reasons=(body.errors||[]).map(function(e){return e.reason;}).filter(Boolean);
        var msg=String(body.message||'').toLowerCase(), category='';
        if(/insufficient.*scope|authentication scope|scope.*insufficient/.test(msg)) category='OAUTH_SCOPE';
        else if(/service.*disabled|not enabled|accessnotconfigured/.test(msg)) category='SHEETS_API_DISABLED';
        else if(/does not have permission|permission denied|not authorized/.test(msg)) category='SHEET_ACCESS_PERMISSION';
        else if(/not found/.test(msg)) category='SHEET_OR_RANGE_NOT_FOUND';
        return [body.status,body.code,category].concat(reasons).filter(Boolean).join(':')||'NO_ERROR_DETAIL';
      } catch(e) { return 'NO_ERROR_DETAIL'; }
    }
    function asSheetValue(value){
      if(value instanceof Date)return value.getTime()/86400000+25569;
      return value==null?'':value;
    }
    function sheetRows(rows){return rows.map(function(row){return row.map(fbsLocalSerial_);});}
    mark('SHEET_READ_START');
    // Read the used-row boundary and ID column with SpreadsheetApp. The previous
    // REST request used A2:A (the entire grid); on large books Sheets batchGet
    // timed out before the recovery could reach its bounded status reads.
    var orderSheetNative=SpreadsheetApp.openById(spreadsheetId).getSheetByName('📦 FBS Заказы');
    if(!orderSheetNative)throw new Error('FBS_ORDER_SHEET_NOT_FOUND');
    var sheetTimeZone=orderSheetNative.getParent().getSpreadsheetTimeZone();
    function fbsLocalSerial_(value){
      if(value instanceof Date){
        var local=Utilities.formatDate(value,sheetTimeZone,'yyyy-MM-dd HH:mm:ss').match(/^(\d{4})-(\d{2})-(\d{2}) (\d{2}):(\d{2}):(\d{2})$/);
        if(!local)throw new Error('FBS_LOCAL_TIME_FORMAT_FAILED');
        return Date.UTC(Number(local[1]),Number(local[2])-1,Number(local[3]),Number(local[4]),Number(local[5]),Number(local[6]))/86400000+25569;
      }
      return value==null?'':value;
    }
    var last=orderSheetNative.getLastRow();
    if(last<1||last>100000)throw new Error('FBS_ROW_COUNT_UNEXPECTED');
    var head=orderSheetNative.getRange(1,1,1,7).getDisplayValues()[0]||[];
    mark('SHEET_READ_DONE_NATIVE');
    if(String(head[0]||'')!=='ID заказа'||String(head[4]||'')!=='Статус продавца'||String(head[5]||'')!=='Статус WB')
      throw new Error('FBS_COLUMN_SCHEMA_MISMATCH');
    mark('ID_INDEX_LOOKUP_START');
    var indexState=sellmonitorGithubFbsLoadIdIndex_(orderSheetNative,client,last,mark,false);
    var byId=indexState.map,idByRow={};
    Object.keys(byId).forEach(function(id){idByRow[byId[id]]=id;});
    var stateByRow={},syncTimesByRow={};
    function readExistingStates_(){
      var rows=[];
      parsed.forEach(function(r){var row=byId[r.id];if(row)rows.push({id:r.id,row:row});});
      var stale=false;
      rows.sort(function(a,b){return a.row-b.row;});
      if(!rows.length){
        mark('STATE_READ_DONE_NATIVE_BLOCK_0_ROWS');
        return false;
      }
      // Ask Sheets for only the rows belonging to this idempotent batch.
      // The previous native read fetched every row between the first and last
      // matching ID, which could be tens of thousands of unrelated rows.
      var groups=[];
      rows.forEach(function(item){
        var group=groups.length?groups[groups.length-1]:null;
        if(!group||item.row!==group.end+1){
          group={start:item.row,end:item.row,items:[]};
          groups.push(group);
        }else{
          group.end=item.row;
        }
        group.items.push(item);
      });
      // Use a POST body for the disjoint A1 ranges. The equivalent batchGet
      // GET encoded every range into the URL and exceeded UrlFetch's URL limit
      // on large books when a 100-order batch touched many separated rows.
      var groupsByA1={};
      var dataFilters=groups.map(function(group){
        var a1Range=orderSheet+'!A'+group.start+':G'+group.end;
        groupsByA1[a1Range]=group;
        return {a1Range:a1Range};
      });
      mark('STATE_BATCH_READ_START_'+rows.length+'_IDS_'+groups.length+'_RANGES');
      var response=fetchSheetApi(apiBase+'/values:batchGetByDataFilter',{
        method:'post',contentType:'application/json',
        payload:JSON.stringify({dataFilters:dataFilters,majorDimension:'ROWS',valueRenderOption:'FORMATTED_VALUE'})
      });
      var code=response.getResponseCode();
      if(code<200||code>=300)throw new Error('FBS_STATE_BATCH_READ_HTTP_'+code+':'+sheetApiFailure(response));
      var payload;
      try{payload=JSON.parse(response.getContentText()||'{}');}
      catch(parseError){throw new Error('FBS_STATE_BATCH_READ_INVALID_JSON');}
      var valueRanges=payload.valueRanges||{};
      if(valueRanges.length!==groups.length)throw new Error('FBS_STATE_BATCH_READ_RANGE_COUNT_MISMATCH');
      var matchedGroups={};
      valueRanges.forEach(function(match){
        var group=null;
        (match.dataFilters||[]).some(function(filter){
          group=groupsByA1[String(filter.a1Range||'')]||null;
          return !!group;
        });
        if(!group)throw new Error('FBS_STATE_BATCH_READ_RANGE_MAPPING_INVALID');
        var groupKey=group.start+':'+group.end;
        if(matchedGroups[groupKey])throw new Error('FBS_STATE_BATCH_READ_RANGE_DUPLICATE');
        matchedGroups[groupKey]=true;
        var values=((match.valueRange||{}).values)||[];
        group.items.forEach(function(item){
          var rowValues=values[item.row-group.start]||[];
          if(String(rowValues[0]||'')!==item.id){stale=true;return;}
          stateByRow[item.row]=[rowValues[4]||'',rowValues[5]||'',rowValues[6]||''];
        });
      });
      if(Object.keys(matchedGroups).length!==groups.length)throw new Error('FBS_STATE_BATCH_READ_RANGE_MISSING');
      mark('STATE_BATCH_READ_DONE_'+rows.length+'_IDS_'+groups.length+'_RANGES');
      console.log('FBS_STAGE_STATE_BATCH_READ rows='+rows.length+' ranges='+groups.length+' ms='+(Date.now()-fbsStarted));
      return stale;
    }
    if(readExistingStates_()){
      mark('ID_INDEX_STALE_REBUILD');
      indexState=sellmonitorGithubFbsLoadIdIndex_(orderSheetNative,client,last,mark,true);
      byId=indexState.map;idByRow={};stateByRow={};
      Object.keys(byId).forEach(function(id){idByRow[byId[id]]=id;});
      if(readExistingStates_())throw new Error('FBS_ID_INDEX_STALE_AFTER_REBUILD');
    }
    console.log('FBS_STAGE_ID_INDEX ms='+(Date.now()-fbsStarted)+' entries='+Object.keys(byId).length);
    var n=new Date(),insert=[],patch=[],events=[],already=0,updated=0,confirmed=0;
    parsed.forEach(function(r){
      var o=r.source,id=r.id,rowNumber=byId[id];
      if(rowNumber) {
        already++;
        var old=stateByRow[rowNumber]||[],os=str(old[0],90),ow=str(old[1],90);
        if(os!==r.seller||ow!==r.wb){
          old[0]=r.seller;old[1]=r.wb;old[2]=human(r.seller,r.wb);stateByRow[rowNumber]=old;
          syncTimesByRow[rowNumber]=[n,n];patch.push(rowNumber);updated++;
          events.push([n,id,str(o.article,300),r.created,os,r.seller,ow,r.wb,'',
            'СМЕНА СТАТУСА',human(r.seller,r.wb),'GITHUB_WB_FBS']);
        } else confirmed++;
        return;
      }
      var row=Array(45).fill('');
      var age=Math.max(0,Math.floor((n.getTime()-r.created.getTime())/60000));
      row[0]=id;row[1]=r.created;row[2]=age;row[3]=age+' мин';
      row[4]=r.seller;row[5]=r.wb;row[6]=human(r.seller,r.wb);
      row[7]=str(o.article,300);row[8]=str(o.nmId,50);row[9]=rub(o.priceKopecks);
      row[10]=str(o.supplyId,90);row[11]=str(o.warehouseId,50);row[12]=str(o.officeId,50);
      row[13]=str(o.deliveryType,40);row[14]=str(o.cargoType,40);
      row[15]=str(o.orderUid,150);row[16]=str(o.rid,150);row[17]=n;row[21]=n;row[22]=n;
      row[26]=['confirm','complete'].indexOf(r.seller)!==-1?'ДА':'НЕТ';
      row[27]='НЕТ';row[31]='НЕТ';row[32]='GITHUB_WB_FBS';
      row[34]=str(o.comment,500);row[36]=str(o.barcode,100);row[37]=str(o.chrtId,100);
      row[43]='❔ Не проверено';
      insert.push(row);byId[id]=last+insert.length;idByRow[last+insert.length]=id;
      events.push([n,id,str(o.article,300),r.created,'',r.seller,'',r.wb,age+' мин',
        'СНИМОК СТАТУСА','Статус WB подтверждён при импорте из GitHub','GITHUB_WB_FBS']);
    });
    if(patch.length&&last>1){
      patch.sort(function(a,b){return a-b;});
      var segments=[],segmentStart=patch[0],segmentEnd=patch[0];
      for(var pi=1;pi<patch.length;pi++){
        if(patch[pi]===segmentEnd+1){segmentEnd=patch[pi];continue;}
        segments.push([segmentStart,segmentEnd]);segmentStart=segmentEnd=patch[pi];
      }
      segments.push([segmentStart,segmentEnd]);
      var patchData=[],verifyFilters=[];
      segments.forEach(function(seg){
        var startRow=seg[0],endRow=seg[1],offset=startRow-2,count=endRow-startRow+1;
        patchData.push({range:"'📦 FBS Заказы'!E"+startRow+":G"+endRow,
          majorDimension:'ROWS',values:Array.from({length:count},function(_,i){return (stateByRow[startRow+i]||[]).slice(0,3).map(asSheetValue);})});
        patchData.push({range:"'📦 FBS Заказы'!V"+startRow+":W"+endRow,
          majorDimension:'ROWS',values:Array.from({length:count},function(_,i){return (syncTimesByRow[startRow+i]||[]).map(fbsLocalSerial_);})});
        verifyFilters.push({a1Range:"'📦 FBS Заказы'!A"+startRow+":G"+endRow});
        verifyFilters.push({a1Range:"'📦 FBS Заказы'!V"+startRow+":W"+endRow});
      });
      mark('STATE_WRITE_START'); var writeResponse=fetchSheetApi(apiBase+'/values:batchUpdate',{
        method:'post',contentType:'application/json',
        headers:{Authorization:'Bearer '+authToken},muteHttpExceptions:true,
        payload:JSON.stringify({valueInputOption:'RAW',data:patchData})
      });
      var writeCode=writeResponse.getResponseCode(); console.log('FBS_STAGE_STATE_WRITE ms='+(Date.now()-fbsStarted)+' http='+writeCode+' segments='+segments.length); mark('STATE_WRITE_DONE_'+writeCode);
      if(writeCode<200||writeCode>=300){
        var writeDiagnostic='UNKNOWN';
        try { var writeError=JSON.parse(writeResponse.getContentText()||'{}').error||{}; writeDiagnostic=String(writeError.status||writeError.message||'UNKNOWN').slice(0,180); } catch(writeParseError) {}
        throw new Error('FBS_PATCH_BATCH_HTTP_'+writeCode+':'+writeDiagnostic);
      }
      mark('STATE_VERIFY_START_BOUNDED');
      // Force pending Sheets writes to become visible before readback verification.
      SpreadsheetApp.flush();
      Utilities.sleep(500);
      // Read only the changed segments. A single min-to-max range can span tens
      // of thousands of unrelated rows when a small batch contains scattered IDs.
      segments.forEach(function(seg){
        var startRow=seg[0],endRow=seg[1],count=endRow-startRow+1;
        var verifyStateMatrix=orderSheetNative.getRange(startRow,1,count,7).getValues();
        var verifyTimeMatrix=orderSheetNative.getRange(startRow,22,count,2).getDisplayValues();
        for(var rowNumber=startRow;rowNumber<=endRow;rowNumber++){
          var rowOffset=rowNumber-startRow;
          var orderRow=verifyStateMatrix[rowOffset]||[],timeRow=verifyTimeMatrix[rowOffset]||[];
          var expectedState=stateByRow[rowNumber]||[],expectedTimes=syncTimesByRow[rowNumber]||[];
          if(String(orderRow[0]||'')!==String(idByRow[rowNumber]||'')||
             String(orderRow[4]||'')!==String(expectedState[0]||'')||
             String(orderRow[5]||'')!==String(expectedState[1]||'')||
             String(orderRow[6]||'')!==String(expectedState[2]||''))
            throw new Error('FBS_PATCH_READBACK_FAILED');
          for(var t=0;t<2;t++){
            var expectedDisplay=expectedTimes[t] instanceof Date?Utilities.formatDate(expectedTimes[t],sheetTimeZone,'dd.MM.yyyy HH:mm:ss'):String(expectedTimes[t]||'');
            var actualDisplay=String(timeRow[t]||'');
            if(!expectedDisplay||actualDisplay!==expectedDisplay)
              throw new Error('FBS_PATCH_TIME_READBACK_FAILED:row='+rowNumber+':column='+(22+t)+':expected='+expectedDisplay+':actual='+actualDisplay);
          }
        }
      });
      mark('STATE_VERIFY_DONE_BOUNDED');
    }
    console.log('FBS_STAGE_PATCH ms='+(Date.now()-fbsStarted)+' changed='+updated+' events='+events.length);
    if(insert.length){
      mark('INSERT_START');
      var insertStart=last+1,insertEnd=last+insert.length;
      var currentGridRows=orderSheetNative.getMaxRows();
      if(insertEnd>currentGridRows){
        mark('GRID_EXTEND_START_'+currentGridRows+'_TO_'+insertEnd);
        orderSheetNative.insertRowsAfter(currentGridRows,insertEnd-currentGridRows);
        SpreadsheetApp.flush();
        mark('GRID_EXTEND_DONE_'+orderSheetNative.getMaxRows());
      }
      var insertRange=orderSheet+'!A'+insertStart+':AS'+insertEnd;
      var insertRequestBody=JSON.stringify({valueInputOption:'RAW',includeValuesInResponse:true,
        responseValueRenderOption:'UNFORMATTED_VALUE',data:[{
          range:insertRange,majorDimension:'ROWS',values:sheetRows(insert)
        }]});
      var insertResponse=null,insertCode=0,insertWriteError='';
      // A timeout may occur after Sheets has committed the fixed-range write.
      // Retrying this exact RAW range is idempotent and avoids losing the batch.
      for(var insertWriteAttempt=0;insertWriteAttempt<3;insertWriteAttempt++){
        mark('INSERT_WRITE_ATTEMPT_'+(insertWriteAttempt+1));
        try{
          insertResponse=fetchSheetApi(apiBase+'/values:batchUpdate',{
            method:'post',contentType:'application/json',payload:insertRequestBody
          });
          insertCode=insertResponse.getResponseCode();
          if(insertCode>=200&&insertCode<300)break;
          if(insertCode!==429&&insertCode<500)break;
          insertWriteError='HTTP_'+insertCode;
        }catch(insertWriteException){
          insertWriteError=/timeout|timed out/i.test(String(insertWriteException))?'TIMEOUT':'EXCEPTION';
          if(insertWriteError!=='TIMEOUT'||insertWriteAttempt>=2)throw new Error('FBS_INSERT_WRITE_'+insertWriteError);
        }
        if(insertWriteAttempt<2)Utilities.sleep([1000,3000][insertWriteAttempt]);
      }
      if(!insertResponse)throw new Error('FBS_INSERT_WRITE_'+(insertWriteError||'NO_RESPONSE'));
      mark('INSERT_RESPONSE_'+insertCode);
      if(insertCode<200||insertCode>=300)throw new Error('FBS_INSERT_HTTP_'+insertCode+':'+sheetApiFailure(insertResponse));
      // The Sheets API write response can include persisted values, avoiding
      // a second read request that may time out under API load. Fall back to a
      // bounded readback only if the response omits or mismatches those values.
      SpreadsheetApp.flush();
      var insertIds=[],insertReadbackMatched=false,insertReadbackAttempts=0,insertReadbackApiError='';
      try{
        var insertWritePayload=JSON.parse(insertResponse.getContentText()||'{}');
        var insertWriteRows=((((insertWritePayload.responses)||[])[0]||{}).updatedData||{}).values||[];
        insertIds=insertWriteRows.map(function(v){return String((v||[])[0]||'');});
        insertReadbackMatched=insertIds.length===insert.length;
        if(insertReadbackMatched){
          for(var writeVerifyIndex=0;writeVerifyIndex<insert.length;writeVerifyIndex++){
            if(insertIds[writeVerifyIndex]!==String(insert[writeVerifyIndex][0])){
              insertReadbackMatched=false;break;
            }
          }
        }
      }catch(insertWriteParseError){insertReadbackMatched=false;}
      if(!insertReadbackMatched)for(var insertAttempt=0;insertAttempt<4;insertAttempt++){
        insertReadbackAttempts=insertAttempt+1;
        try{
          var insertVerifyResponse=fetchSheetApi(apiBase+'/values/'+
            encodeURIComponent(orderSheet+'!A'+insertStart+':A'+insertEnd)+
            '?valueRenderOption=FORMATTED_VALUE',{method:'get'});
          var insertVerifyCode=insertVerifyResponse.getResponseCode();
          if(insertVerifyCode<200||insertVerifyCode>=300){
            if(insertVerifyCode!==429&&insertVerifyCode<500)
              throw new Error('FBS_INSERT_READBACK_HTTP_'+insertVerifyCode+':'+sheetApiFailure(insertVerifyResponse));
            insertReadbackApiError='API_HTTP_'+insertVerifyCode;
          }else{
            var insertVerifyPayload;
            try{insertVerifyPayload=JSON.parse(insertVerifyResponse.getContentText()||'{}');}
            catch(insertVerifyParseError){throw new Error('FBS_INSERT_READBACK_INVALID_JSON');}
            insertIds=((insertVerifyPayload.values)||[]).map(function(v){return String((v||[])[0]||'');});
            insertReadbackMatched=insertIds.length===insert.length;
            if(insertReadbackMatched){
              for(var verifyInsertIndex=0;verifyInsertIndex<insert.length;verifyInsertIndex++){
                if(insertIds[verifyInsertIndex]!==String(insert[verifyInsertIndex][0])){
                  insertReadbackMatched=false;break;
                }
              }
            }
            if(insertReadbackMatched)break;
            insertReadbackApiError='';
          }
        }catch(insertReadbackError){
          if(String(insertReadbackError).indexOf('FBS_INSERT_READBACK_HTTP_')===0||
             String(insertReadbackError).indexOf('FBS_INSERT_READBACK_INVALID_JSON')===0)throw insertReadbackError;
          insertReadbackApiError=/timeout|timed out/i.test(String(insertReadbackError))?'API_TIMEOUT':'API_EXCEPTION';
        }
        if(insertAttempt<3)Utilities.sleep([500,1500,3000][insertAttempt]);
      }
      mark('INSERT_READBACK_ATTEMPTS_'+insertReadbackAttempts+'_MATCH_'+insertReadbackMatched+
        (insertReadbackApiError?'_ERROR_'+insertReadbackApiError:''));
      if(!insertReadbackMatched&&insertReadbackApiError==='API_TIMEOUT')
        throw new Error('FBS_INSERT_READBACK_API_TIMEOUT');
      if(insertIds.length!==insert.length)throw new Error('FBS_INSERT_READBACK_COUNT_MISMATCH');
      for(var ii=0;ii<insert.length;ii++){
        if(insertIds[ii]!==String(insert[ii][0]))throw new Error('FBS_INSERT_READBACK_FAILED');
        byId[insertIds[ii]]=insertStart+ii;
        idByRow[insertStart+ii]=insertIds[ii];
      }
      indexState.lastRow=insertEnd;
      sellmonitorGithubFbsSaveIdIndex_(indexState.cache,indexState.prefix,indexState.client,indexState.lastRow,byId);
    }
    if(events.length){
      mark('HISTORY_WRITE_START');
      var historyResponse=fetchSheetApi(apiBase+'/values/'+encodeURIComponent("'🕘 История статусов'!A:L")+':append?valueInputOption=RAW&insertDataOption=INSERT_ROWS',{
        method:'post',contentType:'application/json',
        payload:JSON.stringify({majorDimension:'ROWS',values:sheetRows(events)})
      });
      var historyCode=historyResponse.getResponseCode(); mark('HISTORY_WRITE_DONE_'+historyCode);
      if(historyCode<200||historyCode>=300)throw new Error('FBS_HISTORY_WRITE_HTTP_'+historyCode);
    }
    console.log('FBS_STAGE_DONE ms='+(Date.now()-fbsStarted)+' inserted='+insert.length+' changed='+updated);
    return {ok:true,client:client,received:parsed.length,inserted:insert.length,
      existing:already,changed:updated,unchanged:confirmed,events:events.length,
      latestWorkbookOrderRows:last-1+insert.length,checkedAt:n.toISOString()};
}
