/**
 * WB OS freshness guard.
 *
 * Goals:
 * - never let stale RUNNING queue rows block the worker indefinitely;
 * - detect when the trusted daily layer is behind D-1;
 * - re-enqueue the store autopilot when freshness is behind and no active autopilot exists;
 * - make detached downstream reports visibly fail-loud instead of silently looking current.
 *
 * Safe by design: only whitelisted idempotent/orchestration jobs may be auto-cancelled.
 */

var WB_FRESHNESS_GUARD_CFG = {
  timezone: 'Europe/Moscow',
  staleRunningMinutes: 90,
  sourceSpreadsheetId: '1-aBDZ7c5xfmVwwiNmUi9-DyfIANXmfiM5-Ti2_zg4zI',
  monthlySummaryId: '1K2ocoaGBTVajwUw-HCWwsdyULNjSOSXcZX5a15gWl-Y',
  airSpreadsheetId: '1qJvhEOIku7sOMydfrUz0PR5dkcU0sLjv433pa1MFBcM',
  hozyushkaSpreadsheetId: '1f9dxXeZxkDth8h2C9GA7L7WqAr1-Ok-Oi5YtufLYOOk',
  storeId: 'sanych_wb',
  safeFiles: {
    wb_ads_bulk_ingest_v167: true,
    store_autopilot_v207: true,
    traffic_refresh_gate_v257: true,
    daily_prevday_gate_v269: true,
    search_traffic_intelligence_v271: true,
    search_intelligence_qc_v242: true,
    orders_live_fast_v261: true,
    store_full_sync_gate_v248: true
  }
};

function wbOsFreshnessGuardParseArgs_(text) {
  try { return JSON.parse(String(text || '')); } catch (e) { return {}; }
}

function wbOsFreshnessGuardFile_(text) {
  var j = wbOsFreshnessGuardParseArgs_(text);
  return String(j.file || '');
}

function wbOsFreshnessGuardTime_(v) {
  if (v instanceof Date) return v.getTime();
  var t = Date.parse(String(v || '').replace(' ', 'T'));
  return isFinite(t) ? t : 0;
}

function wbOsFreshnessGuardLatestTrustedDay_(ss) {
  var sh = ss.getSheetByName('01_RNP');
  if (!sh) return '';
  var lastCol = Math.max(15, sh.getLastColumn());
  var values = sh.getRange(2, 15, 1, lastCol - 14).getDisplayValues()[0];
  var best = '';
  values.forEach(function(v) {
    var s = String(v || '');
    if (!s || /ПРЕДВ/i.test(s)) return;
    var m = s.match(/(\d{2})\.(\d{2})\.(\d{4})/);
    if (!m) return;
    var iso = m[3] + '-' + m[2] + '-' + m[1];
    if (!best || iso > best) best = iso;
  });
  return best;
}

function wbOsFreshnessGuardYesterday_() {
  var now = new Date();
  now.setDate(now.getDate() - 1);
  return Utilities.formatDate(now, WB_FRESHNESS_GUARD_CFG.timezone, 'yyyy-MM-dd');
}

function wbOsFreshnessGuardCancelStale_(ss) {
  var sh = ss.getSheetByName('97_Управление');
  if (!sh || sh.getLastRow() < 2) return [];
  var n = sh.getLastRow() - 1;
  var rows = sh.getRange(2, 1, n, 8).getValues();
  var now = Date.now();
  var cancelled = [];

  rows.forEach(function(r, i) {
    if (String(r[4] || '') !== 'RUNNING') return;
    var file = wbOsFreshnessGuardFile_(r[3]);
    if (!WB_FRESHNESS_GUARD_CFG.safeFiles[file]) return;
    var started = wbOsFreshnessGuardTime_(r[5]) || wbOsFreshnessGuardTime_(r[1]);
    var age = started ? (now - started) / 60000 : 999999;
    if (age < WB_FRESHNESS_GUARD_CFG.staleRunningMinutes) return;

    var row = i + 2;
    sh.getRange(row, 5).setValue('CANCELLED_STALE_RUNNING');
    sh.getRange(row, 7).setValue(new Date());
    sh.getRange(row, 8).setValue(
      'Freshness guard: stale RUNNING ' + Math.round(age) +
      'm; safe file=' + file + '; released for idempotent retry.'
    );
    cancelled.push({row: row, file: file, ageMinutes: Math.round(age)});
  });
  return cancelled;
}

function wbOsFreshnessGuardHasActiveAutopilot_(sh) {
  if (!sh || sh.getLastRow() < 2) return false;
  var rows = sh.getRange(2, 1, sh.getLastRow() - 1, 8).getValues();
  for (var i = 0; i < rows.length; i++) {
    var st = String(rows[i][4] || '');
    var file = wbOsFreshnessGuardFile_(rows[i][3]);
    if (file === 'store_autopilot_v207' &&
        (st === 'PENDING' || st === 'RUNNING' || st === 'SCHEDULED' || st === 'NEW')) {
      return true;
    }
  }
  return false;
}

function wbOsFreshnessGuardEnqueueAutopilot_(ss, reason) {
  var sh = ss.getSheetByName('97_Управление');
  if (!sh) throw new Error('Нет 97_Управление');
  if (wbOsFreshnessGuardHasActiveAutopilot_(sh)) return {enqueued: false, reason: 'active autopilot exists'};

  var start = 1200;
  var end = Math.min(2023, sh.getMaxRows());
  var rows = sh.getRange(start, 1, end - start + 1, 8).getValues();
  var row = 0;
  for (var i = 0; i < rows.length; i++) {
    var id = String(rows[i][0] || '');
    var st = String(rows[i][4] || '');
    if (!id || (!['PENDING','RUNNING','SCHEDULED','NEW'].includes(st) && /^CANCELLED/.test(st))) {
      row = start + i;
      break;
    }
  }
  if (!row) throw new Error('FRESHNESS_QUEUE_FULL 1200:2023');

  var tz = WB_FRESHNESS_GUARD_CFG.timezone;
  var id = 'FRESH-AUTOPILOT-' + Utilities.formatDate(new Date(), tz, 'yyyyMMdd-HHmmss');
  sh.getRange(row, 1, 1, 8).clearContent();
  sh.getRange(row, 1, 1, 8).setValues([[
    id,
    Utilities.formatDate(new Date(), tz, 'yyyy-MM-dd HH:mm:ss'),
    'RUN_REMOTE',
    JSON.stringify({file:'store_autopilot_v207',entrypoint:'REMOTE_MAIN',payload:{storeId:WB_FRESHNESS_GUARD_CFG.storeId,reason:reason}}),
    'PENDING','','',''
  ]]);
  return {enqueued: true, row: row, id: id};
}

function wbOsFreshnessGuardAirHealth_() {
  try {
    var ss = SpreadsheetApp.openById(WB_FRESHNESS_GUARD_CFG.airSpreadsheetId);
    var log = ss.getSheetByName('Технический лог');
    var lastUpdated = DriveApp.getFileById(WB_FRESHNESS_GUARD_CFG.airSpreadsheetId).getLastUpdated();
    var tokenWithdrawn = false;
    var lastError = '';
    if (log && log.getLastRow() > 1) {
      var start = Math.max(2, log.getLastRow() - 250);
      var vals = log.getRange(start, 1, log.getLastRow() - start + 1, Math.min(5, log.getLastColumn())).getDisplayValues();
      for (var i = vals.length - 1; i >= 0; i--) {
        var msg = vals[i].join(' | ');
        if (!lastError && String(vals[i][1] || '').toUpperCase() === 'ERROR') lastError = msg.slice(0, 500);
        if (/Access token withdrawn|HTTP 401/i.test(msg)) { tokenWithdrawn = true; break; }
      }
    }
    return {
      ok: !tokenWithdrawn,
      state: tokenWithdrawn ? 'AUTH_REVOKED' : 'OK',
      lastUpdated: Utilities.formatDate(lastUpdated, WB_FRESHNESS_GUARD_CFG.timezone, 'yyyy-MM-dd HH:mm:ss'),
      detail: tokenWithdrawn ? 'WB API token withdrawn / HTTP 401' : lastError
    };
  } catch (e) {
    return {ok:false, state:'CHECK_ERROR', detail:String(e && e.message ? e.message : e)};
  }
}

function wbOsFreshnessGuardHozyushkaHealth_() {
  try {
    var ss = SpreadsheetApp.openById(WB_FRESHNESS_GUARD_CFG.hozyushkaSpreadsheetId);
    var launch = ss.getSheetByName('🚀 Запуск');
    var settings = ss.getSheetByName('⚙️ Настройки');
    var state = launch ? String(launch.getRange('C36').getDisplayValue() || '') : '';
    var wbApi = settings ? String(settings.getRange('G4').getDisplayValue() || '') : '';
    var lastUpdated = DriveApp.getFileById(WB_FRESHNESS_GUARD_CFG.hozyushkaSpreadsheetId).getLastUpdated();
    var connected = state && state !== 'WAITING_API' && !/Не подключ/i.test(wbApi);
    return {
      ok: connected,
      state: connected ? 'OK' : (state || 'NOT_CONNECTED'),
      wbApi: wbApi,
      lastUpdated: Utilities.formatDate(lastUpdated, WB_FRESHNESS_GUARD_CFG.timezone, 'yyyy-MM-dd HH:mm:ss'),
      detail: connected ? '' : 'WB API/client registration is not complete'
    };
  } catch (e) {
    return {ok:false, state:'CHECK_ERROR', detail:String(e && e.message ? e.message : e)};
  }
}

function wbOsFreshnessGuardWriteHealthSheet_(monthlySs, rows) {
  var name = '99_Контроль_свежести';
  var sh = monthlySs.getSheetByName(name);
  if (!sh) sh = monthlySs.insertSheet(name);
  sh.clearContents();
  sh.getRange(1,1,1,6).setValues([['Контур','Статус','Свежесть / дата','Ожидание','Деталь','Проверено']]);
  sh.getRange(2,1,rows.length,6).setValues(rows);
  sh.setFrozenRows(1);
  return name;
}

function wbOsFreshnessGuardMarkSummary_(latestTrusted, yesterday) {
  var ss = SpreadsheetApp.openById(WB_FRESHNESS_GUARD_CFG.monthlySummaryId);
  var sh = ss.getSheetByName('00_Сводка');
  if (!sh) return {ok:false, error:'00_Сводка missing'};
  var period = String(sh.getRange('A2').getDisplayValue() || '');
  var m = period.match(/—\s*(\d{2})\.(\d{2})\.(\d{4})/);
  var end = m ? (m[3] + '-' + m[2] + '-' + m[1]) : '';
  var air = wbOsFreshnessGuardAirHealth_();
  var hoz = wbOsFreshnessGuardHozyushkaHealth_();
  var sanychOk = !!latestTrusted && latestTrusted >= yesterday;
  var reportOk = !!end && end >= latestTrusted && sanychOk;
  var stale = !reportOk || !air.ok || !hoz.ok;
  var checked = Utilities.formatDate(new Date(), WB_FRESHNESS_GUARD_CFG.timezone, 'yyyy-MM-dd HH:mm:ss');
  var blockers = [];
  if (!sanychOk) blockers.push('Саныч trusted=' + (latestTrusted || 'нет') + ', D-1=' + yesterday);
  if (!reportOk) blockers.push('сводка до=' + (end || 'не распознано'));
  if (!air.ok) blockers.push('AIR=' + air.state);
  if (!hoz.ok) blockers.push('Хозяюшка=' + hoz.state);

  if (stale) {
    sh.getRange('A3').setValue('⚠ АВТОКОНТРОЛЬ СВЕЖЕСТИ: ' + blockers.join(' · '));
  } else if (/АВТОКОНТРОЛЬ СВЕЖЕСТИ/.test(String(sh.getRange('A3').getDisplayValue() || ''))) {
    sh.getRange('A3').clearContent();
  }

  wbOsFreshnessGuardWriteHealthSheet_(ss, [
    ['Саныч Sellmonitor', sanychOk ? 'OK' : 'STALE', latestTrusted || '', yesterday, sanychOk ? '' : 'trusted D-1 отстаёт', checked],
    ['AIR FBS', air.ok ? 'OK' : air.state, air.lastUpdated || '', 'WB API авторизован', air.detail || '', checked],
    ['Хозяюшка FBS', hoz.ok ? 'OK' : hoz.state, hoz.lastUpdated || '', 'клиент зарегистрирован + WB API подключён', hoz.detail || hoz.wbApi || '', checked],
    ['Месячная сводка', reportOk ? 'OK' : 'STALE', end || '', latestTrusted || yesterday, reportOk ? '' : 'период/данные отстают от источника', checked]
  ]);

  return {ok:true, reportEnd:end, stale:stale, air:air, hozyushka:hoz, blockers:blockers};
}

function wbOsFreshnessGuardTick() {
  var ss = SpreadsheetApp.openById(WB_FRESHNESS_GUARD_CFG.sourceSpreadsheetId);
  var cancelled = wbOsFreshnessGuardCancelStale_(ss);
  var latest = wbOsFreshnessGuardLatestTrustedDay_(ss);
  var yesterday = wbOsFreshnessGuardYesterday_();
  var autopilot = {enqueued:false};

  if (!latest || latest < yesterday) {
    autopilot = wbOsFreshnessGuardEnqueueAutopilot_(ss, 'trusted daily freshness behind D-1: ' + latest + ' < ' + yesterday);
  }

  var summary = wbOsFreshnessGuardMarkSummary_(latest, yesterday);
  SpreadsheetApp.flush();

  var props = PropertiesService.getScriptProperties();
  props.setProperty('WB_FRESHNESS_LAST_RUN_AT', new Date().toISOString());
  props.setProperty('WB_FRESHNESS_LAST_TRUSTED_DAY', latest || '');
  props.setProperty('WB_FRESHNESS_EXPECTED_DAY', yesterday);
  props.setProperty('WB_FRESHNESS_LAST_STATUS', (latest && latest >= yesterday) ? 'OK' : 'STALE');

  return {
    ok:true,
    latestTrustedDay:latest,
    expectedDay:yesterday,
    stale:(!latest || latest < yesterday),
    cancelledStaleRunning:cancelled,
    autopilot:autopilot,
    monthlySummary:summary
  };
}

function wbOsEnsureFreshnessGuardTrigger() {
  var handler = 'wbOsFreshnessGuardTick';
  var triggers = ScriptApp.getProjectTriggers();
  var keep = null;
  triggers.forEach(function(t) {
    if (t.getHandlerFunction() !== handler) return;
    if (!keep) keep = t;
    else ScriptApp.deleteTrigger(t);
  });
  if (!keep) {
    ScriptApp.newTrigger(handler).timeBased().everyMinutes(15).create();
  }
  return 'WB_FRESHNESS_GUARD_TRIGGER_OK';
}
