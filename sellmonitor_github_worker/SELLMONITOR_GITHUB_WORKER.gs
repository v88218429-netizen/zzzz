/**
 * Sellmonitor GitHub Central Worker v1.0.0
 * GitHub is source-of-truth/scheduler. This Apps Script project is only
 * the authorized Google adapter. Make is not part of the execution path.
 */
const SMC_GH = Object.freeze({
  VERSION: 'github-worker-1.0.0',
  CONTROL_CENTER_ID: '1sW51KKwQIvB7GZKyUhukqHXAL_CxJZKL-mjKWGbZLE0',
  CLIENTS_SHEET: 'Clients',
  LOG_SHEET: 'Log',
  MAX_CLIENTS_PER_TICK: 4,
  MAX_COMMANDS_PER_CLIENT: 1,
  MAX_RESULT_CHARS: 45000,
  WEBHOOK_SECRET: '__SELLMONITOR_GITHUB_WEBHOOK_SECRET__'
});


function sellmonitorGithubEnsureTrigger_() {
  var fn = 'sellmonitorGithubTick';
  var triggers = ScriptApp.getProjectTriggers();
  var found = null;
  for (var i = 0; i < triggers.length; i++) {
    if (triggers[i].getHandlerFunction() !== fn) continue;
    if (!found) found = triggers[i];
    else ScriptApp.deleteTrigger(triggers[i]);
  }
  if (!found) {
    ScriptApp.newTrigger(fn).timeBased().everyMinutes(5).create();
    return {ok: true, created: true, intervalMinutes: 5};
  }
  return {ok: true, created: false, intervalMinutes: 5};
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
          state: result.ready ? 'READY' : (String(r[map.state] || '') || 'ACTIVE'),
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
    ready = allOk && values.length > 0;
  }

  return {ok: true, spreadsheetId: spreadsheetId, processedCommands: processed, last: last, ready: ready};
}

function sellmonitorGithubNextPendingRow_(q) {
  var lr = q.getLastRow();
  if (lr < 2) return 0;
  var from = Math.max(2, lr - 2500);
  var vals = q.getRange(from, 1, lr - from + 1, 8).getValues();
  for (var i = 0; i < vals.length; i++) {
    var cmd = String(vals[i][2] || '');
    var st = String(vals[i][4] || '');
    if (cmd === 'RUN_REMOTE' && (st === 'PENDING' || st === 'NEW' || st === 'SCHEDULED')) return from + i;
  }
  return 0;
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
  var lr = codeSheet.getLastRow();
  if (lr < 2) throw new Error('97_Код is empty');
  var vals = codeSheet.getRange(2, 1, lr - 1, 6).getValues();
  var parts = [];
  vals.forEach(function(r, i) {
    var name = String(r[0] || '').trim();
    var enabled = r[4] === true || String(r[4]).toUpperCase() === 'TRUE';
    if (name !== file || !enabled) return;
    var part = Number(r[2]);
    if (!isFinite(part) || part <= 0) part = i + 1;
    parts.push({part: part, code: String(r[3] || '')});
  });
  if (!parts.length) throw new Error('Active code not found: ' + file);
  parts.sort(function(a, b) { return a.part - b.part; });
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
  var base = PropertiesService.getScriptProperties();
  var prefix = 'SMC__' + String(spreadsheetId) + '__';
  function key(k) { return prefix + String(k); }
  return {
    getProperty: function(k) { return base.getProperty(key(k)); },
    setProperty: function(k, v) { base.setProperty(key(k), String(v)); return this; },
    deleteProperty: function(k) { base.deleteProperty(key(k)); return this; },
    getProperties: function() {
      var all = base.getProperties(), out = {};
      Object.keys(all).forEach(function(k) {
        if (k.indexOf(prefix) === 0) out[k.slice(prefix.length)] = all[k];
      });
      return out;
    },
    setProperties: function(obj, deleteOthers) {
      if (deleteOthers) this.deleteAllProperties();
      var out = {};
      Object.keys(obj || {}).forEach(function(k) { out[key(k)] = String(obj[k]); });
      base.setProperties(out, false);
      return this;
    },
    deleteAllProperties: function() {
      var all = base.getProperties();
      Object.keys(all).forEach(function(k) { if (k.indexOf(prefix) === 0) base.deleteProperty(k); });
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

function sellmonitorGithubWebAuth_(token) {
  token = String(token || '');
  var expected = String(SMC_GH.WEBHOOK_SECRET || '');
  if (!expected || expected === '__SELLMONITOR_GITHUB_WEBHOOK_SECRET__') {
    throw new Error('SELLMONITOR_WEBHOOK_SECRET_NOT_COMPILED');
  }
  if (token !== expected) {
    throw new Error('UNAUTHORIZED');
  }
}

function doGet(e) {
  try {
    var token = e && e.parameter ? e.parameter.token : '';
    var action = e && e.parameter ? String(e.parameter.action || 'health') : 'health';

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
    sellmonitorGithubWebAuth_(body.token);
    var action = String(body.action || 'tick');

    if (action === 'health') {
      return sellmonitorGithubWebJson_({
        ok: true,
        action: action,
        result: sellmonitorGithubHealth()
      });
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
