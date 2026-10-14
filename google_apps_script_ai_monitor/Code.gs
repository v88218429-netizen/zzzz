/** AI Video Monitor <> GitHub Actions bridge.
 * Runs inside the OWNER'S spreadsheet. Never send credentials in responses.
 * The authenticated caller uses a derived token from GitHub's existing clasp
 * secret. Only a SHA-256 of this token is stored in Apps Script source.
 */
const SHEET_ID = '1ejXreSQYBDDlTDI9ZtRs7hW0vgdssVRm__hNtxJmP64';
const TOKEN_SHA256 = '__TOKEN_HASH__';
const PERMITTED = Object.freeze([
  'Задания', 'История', 'Видео', 'База знаний', 'Справочники',
  'Мои цели', 'Правила анализа', 'Варианты', 'Идеи для владельца'
]);
const PERMITTED_SET = new Set(PERMITTED);

function respond(payload) {
  return ContentService.createTextOutput(JSON.stringify(payload))
    .setMimeType(ContentService.MimeType.JSON);
}
function checksum(value) {
  return Utilities.computeDigest(Utilities.DigestAlgorithm.SHA_256, String(value))
    .map(function(b){return ('0' + (b < 0 ? b + 256 : b).toString(16)).slice(-2);})
    .join('');
}
function parseA1(name) {
  const match = String(name || '').match(/^'([^']+)'!([A-Z]{1,2})([1-9]\d*)(?::([A-Z]{1,2})([1-9]\d*))?$/);
  if (!match || !PERMITTED_SET.has(match[1])) {
    throw new Error('Invalid or unauthorized A1 range');
  }
  return match;
}
function cleanCell(value) {
  if (typeof value === 'number' || typeof value === 'boolean') return value;
  const text = String(value == null ? '' : value).slice(0, 15000);
  return /^[=+@]/.test(text) ? "'" + text : text;
}
function doPost(e) {
  try {
    if (!e || !e.postData || !e.postData.contents) throw new Error('No request');
    if (e.postData.contents.length > 150000) throw new Error('Oversized request');
    const payload = JSON.parse(e.postData.contents);
    if (!payload || !payload.token || checksum(payload.token) !== TOKEN_SHA256) {
      return respond({ok:false, error:'Unauthorized'});
    }
    const book = SpreadsheetApp.openById(SHEET_ID);
    if (payload.op === 'read') {
      const ranges = payload.ranges;
      if (!Array.isArray(ranges) || ranges.length > 12) throw new Error('Invalid ranges');
      const values = ranges.map(function(ref){
        const m = parseA1(ref);
        const tab = book.getSheetByName(m[1]);
        if (!tab) throw new Error('Missing tab');
        return tab.getRange(ref.split('!')[1]).getDisplayValues();
      });
      return respond({ok:true, values:values, timestamp:new Date().toISOString()});
    }
    if (payload.op === 'write') {
      const batches = payload.data;
      if (!Array.isArray(batches) || batches.length > 250) throw new Error('Invalid batch');
      const lock = LockService.getScriptLock();
      if (!lock.tryLock(20000)) throw new Error('LOCK_BUSY');
      try {
        let cells = 0;
        batches.forEach(function(b) {
          const m = parseA1(b.range);
          if (!Array.isArray(b.values) || b.values.length === 0) throw new Error('Empty values');
          if (!Array.isArray(b.values[0])) throw new Error('Expected row arrays');
          const tab = book.getSheetByName(m[1]);
          const rectangle = tab.getRange(b.range.split('!')[1]);
          if (rectangle.getNumRows() !== b.values.length ||
              rectangle.getNumColumns() !== b.values[0].length) throw new Error('Range shape mismatch');
          cells += rectangle.getNumRows() * rectangle.getNumColumns();
          if (cells > 2500) throw new Error('Too many cells');
        });
        batches.forEach(function(b) {
          book.getSheetByName(parseA1(b.range)[1])
            .getRange(b.range.split('!')[1])
            .setValues(b.values.map(function(row){return row.map(cleanCell);}));
        });
        SpreadsheetApp.flush();
        return respond({ok:true, updatedCells:cells});
      } finally {
        lock.releaseLock();
      }
    }
    if (payload.op === 'ping') return respond({ok:true, service:'ai-video-monitor-github-bridge'});
    return respond({ok:false, error:'Unknown operation'});
  } catch (error) {
    return respond({ok:false, error:String(error.message || 'Bridge error').slice(0,180)});
  }
}

/** Run once from Apps Script editor to grant the script owner permission.
 * No modifications to sheet content; only reads the spreadsheet title.
 */
function authorizeBridge() {
  const name = SpreadsheetApp.openById(SHEET_ID).getName();
  console.log('AI Video Monitor bridge authorized for ' + name);
  return name;
}
