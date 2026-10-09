import fs from 'node:fs';
import vm from 'node:vm';
import assert from 'node:assert/strict';

const source = fs.readFileSync(new URL('./SELLMONITOR_GITHUB_WORKER.gs', import.meta.url), 'utf8');

function makeSheet(headers) {
  const values = [headers.slice()];
  let maxRows = 5;
  return {
    values,
    getMaxRows: () => maxRows,
    getMaxColumns: () => 20,
    getLastRow() {
      for (let i = values.length - 1; i >= 0; i--) {
        if ((values[i] || []).some(value => value !== '' && value != null)) return i + 1;
      }
      return 0;
    },
    insertRowsAfter(_after, count) { maxRows += count; },
    getRange(startRow, startColumn, rowCount, columnCount) {
      return {
        getDisplayValues() {
          return Array.from({length: rowCount}, (_, r) =>
            Array.from({length: columnCount}, (_, c) => String(values[startRow - 1 + r]?.[startColumn - 1 + c] ?? ''))
          );
        },
        setValues(rows) {
          rows.forEach((row, r) => {
            const target = values[startRow - 1 + r] || (values[startRow - 1 + r] = []);
            row.forEach((value, c) => { target[startColumn - 1 + c] = value; });
          });
        },
        clearContent() {
          for (let r = 0; r < rowCount; r++) {
            const target = values[startRow - 1 + r] || [];
            for (let c = 0; c < columnCount; c++) target[startColumn - 1 + c] = '';
          }
        }
      };
    }
  };
}

const adsHeaders = ['store_id','date','advert_id','nmId','views','clicks','atbs','orders','spend_rub','order_sum_rub','source','quality','seller_article','load_timestamp','coverage_status'];
const funnelHeaders = ['store_id','date','nmId','vendorCode','open_count','cart_count','order_count','order_sum_rub','source','load_timestamp','coverage_status'];
const sheets = {
  '12_ADS_CAMPAIGN_DAY': makeSheet(adsHeaders),
  '13_FUNNEL_DAY': makeSheet(funnelHeaders)
};
const workbook = {getSheetByName: name => sheets[name] || null};
let openedId = '';
const sandbox = {
  console, Date, Math, JSON, Number, String, Boolean, Array, Object, RegExp, isFinite,
  parseFloat, parseInt,
  SpreadsheetApp: {
    openById(id) { openedId = id; return workbook; },
    flush() {}
  }
};
vm.createContext(sandbox);
vm.runInContext(source, sandbox);

const publish = sandbox.sellmonitorGithubPublishWbTraffic_;
const status = {
  ok: true,
  finishedAt: '2026-10-09T12:00:00+03:00',
  shops: {ap: {ok:true}, aa: {ok:true}, yv: {ok:true}}
};
const result = publish({
  sync_status: status,
  datasets: {
    ads: [['ap','2026-10-09','42','9','100','10','2','1','3.5','50','WB /adv/v3/fullstats','EXACT_CAMPAIGN_NM_DAY','=formula-like']],
    funnel: [['aa','2026-10-09','10','@formula-like','20','3','2','100','WB Analytics products/history']]
  }
});

assert.equal(openedId, '11ULokTx74QjziZjThJ0lfxFiMQW42-WV4315cG7eIuU');
assert.equal(result.ok, true);
assert.equal(result.datasets.ads.written, 1);
assert.equal(result.datasets.funnel.written, 1);
assert.equal(sheets['12_ADS_CAMPAIGN_DAY'].values[1].length, 15);
assert.equal(sheets['12_ADS_CAMPAIGN_DAY'].values[1][2], 42);
assert.equal(sheets['12_ADS_CAMPAIGN_DAY'].values[1][12], "'=formula-like");
assert.equal(sheets['12_ADS_CAMPAIGN_DAY'].values[1][14], 'SOURCE_OK');
assert.equal(sheets['13_FUNNEL_DAY'].values[1][3], "'@formula-like");

assert.throws(() => publish({
  sync_status: {...status, shops: {...status.shops, yv: {ok:false}}},
  datasets: {ads: [], funnel: []}
}), /WB_TRAFFIC_SHOP_NOT_VERIFIED/);
assert.throws(() => sandbox.sellmonitorGithubNormalizeWbTrafficRows_('ads', [
  ['ap','2026-10-09','42','9','100','10','2','1','3.5','50','WB /adv/v3/fullstats','EXACT_CAMPAIGN_NM_DAY','a'],
  ['ap','2026-10-09','42','9','100','10','2','1','3.5','50','WB /adv/v3/fullstats','EXACT_CAMPAIGN_NM_DAY','a']
], status.finishedAt), /WB_TRAFFIC_DUPLICATE_KEY/);

const empty = publish({sync_status:status, datasets:{ads:[], funnel:[]}});
assert.equal(empty.datasets.ads.skipped, true);
assert.equal(sheets['12_ADS_CAMPAIGN_DAY'].values.length, 2, 'empty refresh must preserve last-good data');

console.log('WB_TRAFFIC_PUBLISH_FIXTURE_OK', JSON.stringify({
  ads_rows: result.datasets.ads.written,
  funnel_rows: result.datasets.funnel.written,
  empty_refresh_preserved: empty.datasets.ads.skipped
}));
