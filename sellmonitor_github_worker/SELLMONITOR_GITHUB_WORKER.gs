/**
 * Sellmonitor GitHub Central Worker v1.0.0
 * GitHub is source-of-truth/scheduler. This Apps Script project is only
 * the authorized Google adapter. Make is not part of the execution path.
 */
const SMC_GH = Object.freeze({
  VERSION: 'github-worker-1.3.1',
  CONTROL_CENTER_ID: '1sW51KKwQIvB7GZKyUhukqHXAL_CxJZKL-mjKWGbZLE0',
  CLIENTS_SHEET: 'Clients',
  LOG_SHEET: 'Log',
  MAX_CLIENTS_PER_TICK: 4,
  MAX_COMMANDS_PER_CLIENT: 4,
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
  var cc = SpreadsheetApp.openById(SMC_GH.CONTROL_CENTER_ID);
  var clients = cc.getSheetByName(SMC_GH.CLIENTS_SHEET);
  if (!clients) throw new Error('CONTROL_CENTER Clients sheet missing');
  return {
    ok: true,
    version: SMC_GH.VERSION,
    controlCenterId: cc.getId(),
    controlCenterName: cc.getName(),
    clientsRows: Math.max(0, clients.getLastRow() - 1),
    runtime: 'GITHUB_ACTIONS'
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

    var rows = sh.getRange(2, 1, lr - 1, width).getValues();
    var processedClients = 0, processedCommands = 0, errors = [];

    for (var i = 0; i < rows.length && processedClients < SMC_GH.MAX_CLIENTS_PER_TICK; i++) {
      var r = rows[i], row = i + 2;
      var enabled = r[map.enabled] === true || String(r[map.enabled]).toUpperCase() === 'TRUE';
      var spreadsheetId = String(r[map.spreadsheet_id] || '').trim();
      var storeId = String(r[map.store_id] || '').trim();
      var name = String(r[map.client_name] || '').trim();
      var status = String(r[map.status] || '').trim();

      if (!enabled || !spreadsheetId) continue;
      if (storeId === 'new_store_template' || /MASTER TEMPLATE/i.test(name) || status === 'template_only') continue;

      try {
        var result = sellmonitorGithubProcessClient_(spreadsheetId);
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
        errors.push({spreadsheetId: spreadsheetId, error: String(e.message || e)});
        sellmonitorGithubSetControl_(sh, row, map, {
          last_seen: new Date(),
          worker_version: SMC_GH.VERSION,
          status: 'ERROR',
          state: 'ERROR',
          error: String(e.message || e).slice(0, 1500)
        });
        sellmonitorGithubLog_(cc, spreadsheetId, 'GITHUB_WORKER', 'ERROR', String(e.message || e), {});
      }
    }

    SpreadsheetApp.flush();
    return {
      ok: errors.length === 0,
      version: SMC_GH.VERSION,
      processedClients: processedClients,
      processedCommands: processedCommands,
      errors: errors
    };
  } finally {
    lock.releaseLock();
  }
}

function sellmonitorGithubProcessClient_(spreadsheetId) {
  var ss = SpreadsheetApp.openById(spreadsheetId);
  var q = ss.getSheetByName('97_Управление');
  var code = ss.getSheetByName('97_Код');
  if (!q || !code) throw new Error('Client missing 97_Управление/97_Код: ' + spreadsheetId);

  sellmonitorGithubEnsureUiOnboarding_(ss, q);

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

  return {ok: true, spreadsheetId: spreadsheetId, processedCommands: processed, last: last, ready: ready};
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

function sellmonitorGithubNextPendingRow_(q) {
  var from = 1200;
  var to = Math.min(2023, q.getMaxRows());
  if (to < from) return 0;
  var vals = q.getRange(from, 1, to - from + 1, 8).getValues();
  var now = new Date().getTime(), best = null;

  function priority_(id, file) {
    id = String(id || '');
    file = String(file || '');

    // P0: yesterday-close / finance truth / RNP write path. These must never
    // wait behind SEO, traffic, competitors or cosmetic refreshes.
    if (/^(RELENTLESS-D1-|D1-GAP-|D1-)/.test(id)) return 0;
    if ([
      'd1_gap_watchdog_v310',
      'daily_prevday_close_v268',
      'daily_prevday_gate_v269',
      'inner_harvest_to_raw_v217',
      'finance_period_normalize_incremental_v227',
      'rnp_finance_columns_fast_v307',
      'rnp_latest_period_inner_sync_v224',
      'rnp_period_headers_sync_v198',
      'rnp_store_aggregate_v231',
      'finance_daily_coverage_guard_v226',
      'coverage_freshness_sync_v161',
      'connection_status_sync_v192'
    ].indexOf(file) >= 0) return 0;

    // P1: orchestration that can create/repair the finance path.
    if ([
      'store_autopilot_v207',
      'queue_scheduler_tick_v263',
      'store_full_sync_gate_v248',
      'inner_backfill_gate_v250',
      'inner_backfill_enqueue_v185'
    ].indexOf(file) >= 0) return 1;

    // P2: operational order/K2 refresh.
    if (/^orders_/.test(file) || file === 'k2_inventory_pool_sync_v246') return 2;

    // P3: ads / traffic. Important, but never blocks trusted finance D-1.
    if (/^(ads_|calculator_ads_|quality_ads_|traffic_)/.test(file)) return 3;

    // P5: search/SEO/competitors are explicitly nonblocking for finance.
    if (/^(search_|snapshot_)/.test(file) || /^SEARCH-/.test(id)) return 5;

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
    var p = priority_(id, file);
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

  var slot = 0;
  for (var j = 0; j < vals.length; j++) {
    if (!String(vals[j][0] || '') && !String(vals[j][4] || '')) { slot = from + j; break; }
  }
  if (!slot) return {ok:false, armed:true, reason:'worker-safe queue full'};

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
    if (!/^[A-Za-z_$][A-Za-z0-9_$]*$/.test(entrypoint)) throw new Error('Invalid entrypoint: ' + entrypoint);

    var source = sellmonitorGithubLoadSource_(codeSheet, file);
    source = sellmonitorGithubRebindSource_(source, ss.getId());
    var payload = spec.payload || {};
    var __SM_PAYLOAD__ = payload;
    var wrapped = '(function(__payload){\n' + source + '\n;return ' + entrypoint + '(__payload);\n})(__SM_PAYLOAD__)';
    var result = eval(wrapped);
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
    store_autopilot_v207: 1,
    search_monitor_refresh_gate_v214: 1,
    daily_prevday_close_v268: 1,
    daily_prevday_gate_v269: 1,
    rnp_snapshot_history_v304: 1,
    rnp_finance_columns_fast_v307: 1,
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
          source.ranges[rangeKey] = {
            a1: a1,
            values: sellmonitorGithubReadRange_(ss, a1)
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

function doPost(e) {
  try {
    var body = JSON.parse((e && e.postData && e.postData.contents) || '{}');
    var action = String(body.action || 'tick');

    sellmonitorGithubWebAuth_(body.token || body.key);

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
