/**
 * OZON RADAR -> TELEGRAM relay
 *
 * Reads pending alert events from the Ozon positions spreadsheet and sends
 * them through the existing WB OS Telegram bot. Telegram credentials remain
 * in this Apps Script project's Script Properties.
 */
var OZON_RADAR_RELAY = {
  VERSION: '0.1.0',
  SHEET_ID: '1SHY1rz7XZeqOGkJSkitfSPlKs4cshS_5U63NSv0TO4c',
  QUEUE_SHEET: '06_Радар_Очередь',
  TRIGGER_FN: 'ozonRadarTelegramTick',
  TRIGGER_KEY: 'OZON_RADAR_TG_TRIGGER_READY',
  MAX_PER_TICK: 20
};

function ensureOzonRadarTelegramTrigger_() {
  var props = PropertiesService.getScriptProperties();
  var triggers = ScriptApp.getProjectTriggers();
  var found = false;

  for (var i = 0; i < triggers.length; i++) {
    if (triggers[i].getHandlerFunction() === OZON_RADAR_RELAY.TRIGGER_FN) {
      found = true;
      break;
    }
  }

  if (!found) {
    ScriptApp
      .newTrigger(OZON_RADAR_RELAY.TRIGGER_FN)
      .timeBased()
      .everyMinutes(1)
      .create();
  }

  props.setProperty(OZON_RADAR_RELAY.TRIGGER_KEY, '1');
}

function ozonRadarTelegramTick() {
  var lock = LockService.getScriptLock();
  if (!lock.tryLock(5000)) return;

  try {
    if (typeof sendTelegramMessage_ !== 'function') {
      throw new Error('sendTelegramMessage_ is unavailable in WB OS project');
    }

    var ss = SpreadsheetApp.openById(OZON_RADAR_RELAY.SHEET_ID);
    var sh = ss.getSheetByName(OZON_RADAR_RELAY.QUEUE_SHEET);
    if (!sh || sh.getLastRow() < 2) return;

    var lastRow = sh.getLastRow();
    var values = sh.getRange(2, 1, lastRow - 1, 14).getValues();
    var sent = 0;

    for (var i = 0; i < values.length && sent < OZON_RADAR_RELAY.MAX_PER_TICK; i++) {
      var row = values[i];
      var status = String(row[10] || '').trim().toUpperCase();

      if (status !== 'PENDING') continue;

      var attempts = Number(row[12] || 0);
      var message = String(row[9] || '').trim();

      try {
        if (!message) {
          throw new Error('Empty Telegram message');
        }

        sendTelegramMessage_(message);

        sh.getRange(i + 2, 11, 1, 4).setValues([[
          'SENT',
          new Date(),
          attempts + 1,
          ''
        ]]);

        sent++;
      } catch (error) {
        sh.getRange(i + 2, 11, 1, 4).setValues([[
          attempts >= 4 ? 'ERROR' : 'PENDING',
          '',
          attempts + 1,
          String(error && error.message ? error.message : error).substring(0, 500)
        ]]);
      }
    }
  } finally {
    lock.releaseLock();
  }
}

function setupOzonRadarTelegramRelay() {
  ensureOzonRadarTelegramTrigger_();
  ozonRadarTelegramTick();
  return {
    ok: true,
    version: OZON_RADAR_RELAY.VERSION
  };
}
