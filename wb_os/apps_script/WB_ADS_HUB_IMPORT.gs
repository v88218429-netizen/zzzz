/**
 * WB ADS HUB importer. Bind to WB AI HUB spreadsheet.
 * Script Properties: WB_WORKER_URL and WB_EXPORT_TOKEN (never sheet cells).
 * setupAdsHubSync() installs one hourly trigger after properties are configured.
 */
const ADS_HUB_ID = '11ULokTx74QjziZjThJ0lfxFiMQW42-WV4315cG7eIuU';
const ADS_TARGETS = [
  {path: '/api/traffic/campaign_sku_day.csv', sheet: '12_ADS_CAMPAIGN_DAY', columns: 15},
  {path: '/api/traffic/funnel_sku_day.csv', sheet: '13_FUNNEL_DAY', columns: 11}
];

function adsHubFetch_(path) {
  const props = PropertiesService.getScriptProperties();
  const base = props.getProperty('WB_WORKER_URL');
  const token = props.getProperty('WB_EXPORT_TOKEN');
  if (!base || !token) throw new Error('WB_WORKER_URL or WB_EXPORT_TOKEN missing');
  const url = base.replace(/\/$/, '') + path + '?token=' + encodeURIComponent(token);
  const response = UrlFetchApp.fetch(url, {muteHttpExceptions: true});
  if (response.getResponseCode() !== 200) {
    throw new Error(path + ' HTTP ' + response.getResponseCode());
  }
  return response.getContentText('UTF-8').replace(/^\uFEFF/, '');
}

function syncAdsHub() {
  const lock = LockService.getScriptLock();
  if (!lock.tryLock(30000)) return;
  try {
    const state = JSON.parse(adsHubFetch_('/api/traffic/status'));
    if (!state.ok || !state.finishedAt) {
      throw new Error('Collector incomplete: ' + JSON.stringify(state.shops || state.error));
    }
    const prepared = ADS_TARGETS.map(target => {
      const rows = Utilities.parseCsv(adsHubFetch_(target.path));
      if (!rows.length || !rows[0][0]) throw new Error('Empty CSV: ' + target.path);
      return {target: target, rows: rows.slice(1).filter(row => row[0])};
    });
    const ss = SpreadsheetApp.openById(ADS_HUB_ID);
    prepared.forEach(item => {
      const sh = ss.getSheetByName(item.target.sheet);
      if (!sh) throw new Error('Missing tab ' + item.target.sheet);
      const last = Math.max(sh.getLastRow() - 1, 0);
      if (last) sh.getRange(2, 1, last, item.target.columns).clearContent();
      if (item.rows.length) {
        const values = item.rows.map(row => {
          const output = new Array(item.target.columns).fill('');
          row.forEach((value, i) => { if (i < output.length) output[i] = value; });
          if (item.target.sheet === '12_ADS_CAMPAIGN_DAY') {
            output[12] = row[12] || '';
            output[13] = state.finishedAt;
            output[14] = 'SOURCE_OK';
          } else {
            output[9] = state.finishedAt;
            output[10] = 'SOURCE_OK';
          }
          return output;
        });
        if (sh.getMaxRows() < values.length + 1) sh.insertRowsAfter(sh.getMaxRows(), values.length + 1 - sh.getMaxRows());
        sh.getRange(2, 1, values.length, item.target.columns).setValues(values);
      }
    });
    PropertiesService.getScriptProperties().setProperty('WB_ADS_LAST_SUCCESS', state.finishedAt);
  } finally {
    lock.releaseLock();
  }
}

function setupAdsHubSync() {
  ScriptApp.getProjectTriggers().filter(t => t.getHandlerFunction() === 'syncAdsHub')
    .forEach(t => ScriptApp.deleteTrigger(t));
  ScriptApp.newTrigger('syncAdsHub').timeBased().everyHours(1).create();
  syncAdsHub();
}
