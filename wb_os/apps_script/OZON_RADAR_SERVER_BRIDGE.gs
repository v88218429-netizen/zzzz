/**
 * Ozon Radar Server Bridge
 *
 * Cloud-only bridge:
 * Google Apps Script (1 min) <-> Railway Ozon radar.
 *
 * Responsibilities:
 * - push enabled rows from 06_Радар_1мин to Railway /config
 * - pull CHECK/ERROR events from Railway /events
 * - update the visible radar sheet
 * - append every check to 06_Радар_История
 *
 * Immediate Telegram alerts are sent by Railway itself, so alert latency does
 * not depend on the next Apps Script tick.
 */
var OZON_RADAR_SERVER = {
  VERSION: '1.1.0',
  SHEET_ID: '1SHY1rz7XZeqOGkJSkitfSPlKs4cshS_5U63NSv0TO4c',
  RADAR_SHEET: '06_Радар_1мин',
  HISTORY_SHEET: '06_Радар_История',
  QUEUE_SHEET: '06_Радар_Очередь',
  SETTINGS_SHEET: '99_Настройки',
  TRIGGER_FN: 'ozonRadarServerTick',
  PROP_URL: 'OZON_RADAR_SERVER_URL',
  PROP_SECRET: 'OZON_RADAR_SERVER_SECRET',
  PROP_CURSOR: 'OZON_RADAR_SERVER_CURSOR',
  PROP_TRIGGER_READY: 'OZON_RADAR_SERVER_TRIGGER_READY'
};

var OZON_RADAR_SERVER_COMPILED = {
  URL: '__OZON_RADAR_SERVER_URL__',
  SECRET: '__OZON_RADAR_SERVER_SECRET__'
};

function radarBridgeCompiledValue_(value, placeholder) {
  value = String(value || '').trim();
  if (!value || value === placeholder) return '';
  return value;
}

function ensureOzonRadarServerTrigger_() {
  var props = PropertiesService.getScriptProperties();
  var triggers = ScriptApp.getProjectTriggers();
  var found = false;

  for (var i = 0; i < triggers.length; i++) {
    if (triggers[i].getHandlerFunction() === OZON_RADAR_SERVER.TRIGGER_FN) {
      found = true;
      break;
    }
  }

  if (!found) {
    ScriptApp.newTrigger(OZON_RADAR_SERVER.TRIGGER_FN)
      .timeBased()
      .everyMinutes(1)
      .create();
  }

  props.setProperty(OZON_RADAR_SERVER.PROP_TRIGGER_READY, '1');
}

function configureOzonRadarServerConnection(serverUrl, secret) {
  serverUrl = String(serverUrl || '').trim().replace(/\/+$/, '');
  secret = String(secret || '').trim();

  if (!/^https:\/\//i.test(serverUrl)) {
    throw new Error('Нужен HTTPS URL Railway-сервиса.');
  }
  if (!secret || secret.length < 16) {
    throw new Error('Слишком короткий RADAR secret.');
  }

  var props = PropertiesService.getScriptProperties();
  props.setProperty(OZON_RADAR_SERVER.PROP_URL, serverUrl);
  props.setProperty(OZON_RADAR_SERVER.PROP_SECRET, secret);
  props.deleteProperty(OZON_RADAR_SERVER.PROP_CURSOR);

  ensureOzonRadarServerTrigger_();
  ozonRadarServerTick();

  return {
    ok: true,
    version: OZON_RADAR_SERVER.VERSION,
    serverUrl: serverUrl
  };
}

function ozonRadarServerTick() {
  var lock = LockService.getScriptLock();
  if (!lock.tryLock(5000)) return;

  try {
    var props = PropertiesService.getScriptProperties();
    var serverUrl = String(
      props.getProperty(OZON_RADAR_SERVER.PROP_URL) ||
      radarBridgeCompiledValue_(
        OZON_RADAR_SERVER_COMPILED.URL,
        '__OZON_RADAR_SERVER_URL__'
      ) ||
      ''
    ).trim().replace(/\/+$/, '');
    var secret = String(
      props.getProperty(OZON_RADAR_SERVER.PROP_SECRET) ||
      radarBridgeCompiledValue_(
        OZON_RADAR_SERVER_COMPILED.SECRET,
        '__OZON_RADAR_SERVER_SECRET__'
      ) ||
      ''
    ).trim();

    if (!serverUrl || !secret) {
      return;
    }

    var ss = SpreadsheetApp.openById(OZON_RADAR_SERVER.SHEET_ID);
    var radar = ss.getSheetByName(OZON_RADAR_SERVER.RADAR_SHEET);
    var history = ss.getSheetByName(OZON_RADAR_SERVER.HISTORY_SHEET);
    var queue = ss.getSheetByName(OZON_RADAR_SERVER.QUEUE_SHEET);

    if (!radar || !history || !queue) {
      throw new Error('Не найдены листы Ozon Radar.');
    }

    var lastRow = radar.getLastRow();
    if (lastRow < 5) return;

    var values = radar.getRange(5, 1, lastRow - 4, 16).getValues();
    var tasks = [];
    var rowByKey = {};

    for (var i = 0; i < values.length; i++) {
      var row = values[i];
      if (row[0] !== true) continue;

      var article = String(row[1] || '').trim();
      var sku = String(row[2] || '').trim();
      var query = String(row[3] || '').trim();
      if (!article || !sku || !query) continue;

      var intervalMin = Math.max(1, Number(row[4] || 1));
      var dropThreshold = Math.max(1, Number(row[5] || 3));
      var topBoundary = Math.max(1, Number(row[6] || 10));
      var maxPosition = Math.max(10, Number(row[7] || 100));
      var baseline = Number(row[8]);
      if (!isFinite(baseline) || baseline <= 0) baseline = null;

      var key = radarBridgeKey_(article, sku, query);
      rowByKey[key] = i + 5;

      tasks.push({
        article: article,
        sku: sku,
        query: query,
        interval_min: intervalMin,
        drop_threshold: dropThreshold,
        top_boundary: topBoundary,
        max_position: maxPosition,
        baseline_position: baseline,
        enabled: true
      });
    }

    radarBridgeFetch_(serverUrl + '/config', secret, {
      method: 'post',
      contentType: 'application/json',
      payload: JSON.stringify({tasks: tasks}),
      muteHttpExceptions: true
    });

    var cursor = Number(
      props.getProperty(OZON_RADAR_SERVER.PROP_CURSOR) || 0
    ) || 0;

    var eventsResp = radarBridgeFetch_(
      serverUrl + '/events?after=' + encodeURIComponent(cursor) + '&limit=2000',
      secret,
      {
        method: 'get',
        muteHttpExceptions: true
      }
    );

    var events = Array.isArray(eventsResp.events) ? eventsResp.events : [];
    var historyRows = [];
    var queueRows = [];
    var sourceErrors = 0;
    var nextCursor = Number(eventsResp.next_cursor || cursor) || cursor;

    for (var e = 0; e < events.length; e++) {
      var event = events[e] || {};
      var eventKey = radarBridgeKey_(
        event.article,
        event.sku,
        event.query
      );
      var targetRow = rowByKey[eventKey];

      if (String(event.kind || '') === 'CHECK') {
        // The visible radar I:O contains protective formulas. Never overwrite
        // those cells from Apps Script. A valid LIVE measurement is appended
        // to history and the formulas derive current/previous/delta from it.
        historyRows.push([
          String(event.checked_at || ''),
          String(event.article || ''),
          String(event.sku || ''),
          String(event.query || ''),
          event.position === null || event.position === undefined ? '' : event.position,
          event.previous === null || event.previous === undefined ? '' : event.previous,
          event.delta === null || event.delta === undefined ? '' : event.delta,
          String(event.status || ''),
          String(event.source || 'LIVE SERP'),
          Number(event.http_status || 0) || '',
          Number(event.response_ms || 0) || '',
          'srv-' + String(event.seq || '')
        ]);

        if (
          String(event.alert_message || '').trim() &&
          event.telegram_sent !== true
        ) {
          queueRows.push([
            'srv-alert-' + String(event.seq || ''),
            String(event.checked_at || ''),
            String(event.article || ''),
            String(event.sku || ''),
            String(event.query || ''),
            event.previous === null || event.previous === undefined ? '' : event.previous,
            event.position === null || event.position === undefined ? '' : event.position,
            event.delta === null || event.delta === undefined ? '' : event.delta,
            String(event.alert_type || 'ALERT'),
            String(event.alert_message || ''),
            'PENDING',
            '',
            0,
            ''
          ]);
        }
      } else if (String(event.kind || '') === 'ERROR') {
        // A source failure is deliberately not written as a measurement and
        // does not touch the visible radar formulas. They will age naturally
        // into LIVE НЕТ / LIVE УСТАРЕЛ.
        sourceErrors++;
      }
    }

    if (historyRows.length) {
      history.getRange(
        history.getLastRow() + 1,
        1,
        historyRows.length,
        12
      ).setValues(historyRows);
    }

    if (queueRows.length) {
      queue.getRange(
        queue.getLastRow() + 1,
        1,
        queueRows.length,
        14
      ).setValues(queueRows);
    }

    props.setProperty(
      OZON_RADAR_SERVER.PROP_CURSOR,
      String(nextCursor)
    );

    radarBridgeWriteStatus_(ss, {
      status: sourceErrors > 0 && historyRows.length === 0
        ? 'SOURCE ERROR · LIVE НЕТ'
        : 'LIVE · SERVER BRIDGE',
      detail:
        'tasks=' + tasks.length +
        '; checks=' + historyRows.length +
        '; sourceErrors=' + sourceErrors +
        '; alertsQueued=' + queueRows.length +
        '; cursor=' + nextCursor +
        '; ' + Utilities.formatDate(
          new Date(),
          Session.getScriptTimeZone(),
          'dd.MM.yyyy HH:mm:ss'
        )
    });
  } catch (error) {
    try {
      var ss2 = SpreadsheetApp.openById(OZON_RADAR_SERVER.SHEET_ID);
      radarBridgeWriteStatus_(ss2, {
        status: 'ERROR · RAILWAY BRIDGE',
        detail: String(
          error && error.message ? error.message : error
        ).substring(0, 500)
      });
    } catch (statusError) {
      Logger.log(statusError);
    }
    throw error;
  } finally {
    lock.releaseLock();
  }
}

function radarBridgeFetch_(url, secret, options) {
  options = options || {};
  options.headers = options.headers || {};
  options.headers.Authorization = 'Bearer ' + secret;

  var response = UrlFetchApp.fetch(url, options);
  var code = response.getResponseCode();
  var body = response.getContentText() || '';

  if (code < 200 || code >= 300) {
    throw new Error(
      'Radar server HTTP ' + code + ': ' + body.substring(0, 500)
    );
  }

  var data = body ? JSON.parse(body) : {};
  if (!data || data.ok === false) {
    throw new Error('Radar server invalid response.');
  }

  return data;
}

function radarBridgeKey_(article, sku, query) {
  return [
    String(article || '').trim(),
    String(sku || '').trim(),
    String(query || '').trim()
  ].join('|');
}

function radarBridgeDisplayTime_(iso) {
  if (!iso) return '';
  var dt = new Date(iso);
  if (isNaN(dt.getTime())) return String(iso);
  return Utilities.formatDate(
    dt,
    Session.getScriptTimeZone(),
    'dd.MM.yyyy HH:mm:ss'
  );
}

function radarBridgeWriteStatus_(ss, info) {
  var settings = ss.getSheetByName(OZON_RADAR_SERVER.SETTINGS_SHEET);
  if (!settings) return;

  var values = settings.getRange(
    1,
    1,
    Math.max(1, settings.getLastRow()),
    3
  ).getValues();

  var row = 0;
  for (var i = 0; i < values.length; i++) {
    if (String(values[i][0] || '').trim() === 'RADAR_STATUS') {
      row = i + 1;
      break;
    }
  }

  if (!row) {
    row = settings.getLastRow() + 1;
    settings.getRange(row, 1, 1, 3).setValues([[
      'RADAR_STATUS',
      '',
      ''
    ]]);
  }

  settings.getRange(row, 2, 1, 2).setValues([[
    String(info.status || ''),
    String(info.detail || '')
  ]]);
}

function testOzonRadarServerBridge() {
  ozonRadarServerTick();
}
