const PRICE_SYNC = Object.freeze({ spreadsheetId: '12IkC57a5_Jkop5aiE2kRNUkdJjRMoXAKpwuJEAoJu5k', rawSheet: 'RAW_API_prices_live', shops: ['AP','AA','YV'], maxBodyBytes: 200000, maxRows: 1200, minRows: {AP:95,AA:83,YV:62} });

function doGet() {
  return priceJson_({ok:true, service:'WB unit price snapshot writer', mode:'KEY_PROTECTED_FIXED_DESTINATION'});
}

function doPost(e) {
  try {
    if (!e || !e.postData || !e.postData.contents) throw new Error('Empty request');
    if (e.postData.contents.length > PRICE_SYNC.maxBodyBytes) throw new Error('Request too large');
    const p = JSON.parse(e.postData.contents);
    const expected = PropertiesService.getScriptProperties().getProperty('WB_UNIT_PRICE_BRIDGE_KEY');
    if (!expected || !p || !p.key || p.key !== expected) return priceJson_({ok:false,error:'unauthorized'});
    if (p.action === 'verify_snapshot') return verifyPriceSnapshot_();
    if (p.action !== 'replace_snapshot') throw new Error('Unsupported action');
    const rows = p.rows;
    if (!Array.isArray(rows) || rows.length < 240 || rows.length > PRICE_SYNC.maxRows) throw new Error('Unexpected total row count');
    if (typeof p.snapshot_at !== 'string' || !/^\\d{4}-\\d{2}-\\d{2} \\d{2}:\\d{2}:\\d{2} UTC$/.test(p.snapshot_at)) throw new Error('Invalid snapshot timestamp');
    const timeMs = Date.parse(p.snapshot_at.replace(' UTC','Z').replace(' ','T'));
    if (!Number.isFinite(timeMs) || Math.abs(Date.now()-timeMs) > 30*60*1000) throw new Error('Snapshot timestamp outside accepted window');
    const counts = {AP:0,AA:0,YV:0};
    const seen = Object.create(null);
    rows.forEach(function(row) {
      if (!Array.isArray(row) || row.length !== 14) throw new Error('Each row must contain exactly 14 fields');
      if (row[0] !== p.snapshot_at) throw new Error('Mixed snapshot timestamps');
      const shop = String(row[1] || '');
      if (!Object.prototype.hasOwnProperty.call(counts, shop)) throw new Error('Unexpected shop');
      const nm = String(row[3] || '').trim();
      if (!/^\\d+$/.test(nm)) throw new Error('Invalid nmID');
      if (seen[shop+':'+nm]) throw new Error('Duplicate shop/nmID');
      seen[shop+':'+nm] = true;
      counts[shop]++;
    });
    PRICE_SYNC.shops.forEach(function(shop) {
      if (counts[shop] < PRICE_SYNC.minRows[shop]) throw new Error('Incomplete cabinet snapshot: '+shop+'='+counts[shop]);
    });
    const lock = LockService.getScriptLock();
    if (!lock.tryLock(20000)) throw new Error('LOCK_BUSY');
    try {
      const ss = SpreadsheetApp.openById(PRICE_SYNC.spreadsheetId);
      const sh = ss.getSheetByName(PRICE_SYNC.rawSheet);
      if (!sh) throw new Error('Destination sheet missing');
      const oldCount = Math.max(0, sh.getLastRow()-1);
      const height = Math.max(oldCount, rows.length);
      const backup = height ? sh.getRange(2,1,height,14).getValues() : [];
      try {
        if (height) sh.getRange(2,1,height,14).clearContent();
        sh.getRange(2,1,rows.length,14).setValues(rows.map(function(r){return r.map(priceCleanCell_);}));
        SpreadsheetApp.flush();
        const readback = sh.getRange(2,1,rows.length,14).getValues();
        if (readback.length !== rows.length || readback[0][0] !== p.snapshot_at || String(readback[readback.length-1][0]) !== p.snapshot_at) throw new Error('Snapshot readback mismatch');
      } catch (writeError) {
        if (height && backup.length) sh.getRange(2,1,height,14).setValues(backup);
        SpreadsheetApp.flush();
        throw writeError;
      }
      return priceJson_({ok:true,action:'replace_snapshot',snapshot_at:p.snapshot_at,total_rows:rows.length,shop_counts:counts,updated_cells:rows.length*14});
    } finally { lock.releaseLock(); }
  } catch (err) {
    return priceJson_({ok:false,error:String(err && err.message ? err.message : err).slice(0,220)});
  }
}

function verifyPriceSnapshot_() {
  const ss = SpreadsheetApp.openById(PRICE_SYNC.spreadsheetId);
  const sh = ss.getSheetByName(PRICE_SYNC.rawSheet);
  if (!sh || sh.getLastRow() < 2) return priceJson_({ok:false,error:'No snapshot rows'});
  const n = sh.getLastRow()-1;
  if (n > PRICE_SYNC.maxRows) throw new Error('Unexpected sheet size');
  const vals = sh.getRange(2,1,n,14).getValues();
  const counts = {AP:0,AA:0,YV:0};
  let stamp = '';
  vals.forEach(function(r) {
    if (!r[3]) return;
    stamp = stamp || String(r[0]);
    if (String(r[0]) !== stamp) throw new Error('Mixed timestamps on readback');
    if (Object.prototype.hasOwnProperty.call(counts,String(r[1]))) counts[String(r[1])]++;
  });
  return priceJson_({ok:true,action:'verify_snapshot',snapshot_at:stamp,total_rows:vals.filter(function(r){return !!r[3];}).length,shop_counts:counts});
}

function priceCleanCell_(v) {
  if (typeof v === 'string' && /^[=+@-]/.test(v)) return "'"+v;
  return v;
}
function priceJson_(o) {
  return ContentService.createTextOutput(JSON.stringify(o)).setMimeType(ContentService.MimeType.JSON);
}

function authorizePriceBridge() {
  const ss = SpreadsheetApp.openById(PRICE_SYNC.spreadsheetId);
  const sh = ss.getSheetByName(PRICE_SYNC.rawSheet);
  if (!sh) throw new Error('Destination not found');
  return {ok:true,title:ss.getName(),sheet:sh.getName(),columns:sh.getLastColumn()};
}