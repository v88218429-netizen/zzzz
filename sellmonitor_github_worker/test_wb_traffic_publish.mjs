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
    insertColumnsAfter() {},
    setFrozenRows() {},
    insertRowsAfter(_after, count) { maxRows += count; },
    getRange(startRow, startColumn, rowCount, columnCount) {
      return {
        getValues() {
          return Array.from({length: rowCount}, (_, r) =>
            Array.from({length: columnCount}, (_, c) => values[startRow - 1 + r]?.[startColumn - 1 + c] ?? '')
          );
        },
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
        },
        setNumberFormat() {}
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
const workbook = {
  getSheetByName: name => sheets[name] || null,
  insertSheet(name) { sheets[name] = makeSheet([]); return sheets[name]; },
  getSpreadsheetTimeZone: () => 'Europe/Moscow'
};
let openedId = '';
const sandbox = {
  console, Date, Math, JSON, Number, String, Boolean, Array, Object, RegExp, isFinite,
  parseFloat, parseInt,
  SpreadsheetApp: {
    openById(id) { openedId = id; return workbook; },
    flush() {}
  },
  Utilities: {
    formatDate(value, _tz, pattern) {
      const shifted = new Date(value.getTime() + 3 * 60 * 60 * 1000);
      return pattern === 'yyyy-MM-dd' ? shifted.toISOString().slice(0,10) : shifted.toISOString();
    }
  }
};
vm.createContext(sandbox);
vm.runInContext(source, sandbox);

const publish = sandbox.sellmonitorGithubPublishWbTraffic_;
const status = {
  ok: true,
  complete: false,
  finishedAt: '2026-10-09T12:00:00+03:00',
  period: ['2026-09-10','2026-10-10'],
  funnel_period: ['2026-10-04','2026-10-10'],
  shops: {
    ap: {ok:true},
    aa: {ok:true,campaigns_with_null_payload:1,campaigns_with_null_payload_sample:[12345678]},
    yv: {ok:true}
  }
};
const observedAt = '2026-10-09T13:00:00+03:00';
const result = publish({
  sync_status: status,
  datasets: {
    ads: [['ap','2026-10-09','42','9','100','10','2','1','3.5','50','WB /adv/v3/fullstats','EXACT_CAMPAIGN_NM_DAY','=formula-like']],
    funnel: [['aa','2026-10-09','10','@formula-like','20','3','2','100','WB Analytics products/history']],
    ads_poll: [['ap',observedAt,'2026-10-09',1,1,100,10,2,1,3.5,50,0.1,0.35,0.07,14,'WB /adv/v3/fullstats','DAILY_CUMULATIVE_OBSERVED_AT_POLL']],
    funnel_poll: [['aa',observedAt,'2026-10-09',1,20,3,2,100,0.15,0.666667,0.1,'WB Analytics products/history','DAILY_CUMULATIVE_OBSERVED_AT_POLL']]
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
assert.equal(result.datasets.ads_poll.added, 1);
assert.equal(result.datasets.funnel_poll.added, 1);
assert.equal(result.quality.written, 3);
assert.equal(result.quality.complete, false);
assert.equal(sheets['17_TRAFFIC_QUALITY'].values[1][2], 'ЧАСТИЧНО');
assert.equal(sheets['17_TRAFFIC_QUALITY'].values[2][4], 'ЧАСТИЧНО');
assert.match(sheets['17_TRAFFIC_QUALITY'].values[2][5], /не дополнялись нулями/);
assert.equal(sheets['17_TRAFFIC_QUALITY'].values[2][10], '12345678');
assert.equal(sheets['15_ADS_POLL_SNAPSHOT'].values[1][16], 'DAILY_CUMULATIVE_OBSERVED_AT_POLL');
assert.equal(sheets['16_FUNNEL_POLL_SNAPSHOT'].values[1][14], 'SOURCE_OK');

const replay = publish({sync_status:status,datasets:{ads:[],funnel:[],ads_poll:[['ap',observedAt,'2026-10-09',1,1,100,10,2,1,3.5,50,0.1,0.35,0.07,14,'WB /adv/v3/fullstats','DAILY_CUMULATIVE_OBSERVED_AT_POLL']],funnel_poll:[]}});
assert.equal(replay.datasets.ads_poll.added, 0, 'replayed poll must not create duplicate history');
assert.equal(replay.datasets.ads_poll.updated, 1, 'replayed poll must update the existing observation');

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
assert.equal(sheets['15_ADS_POLL_SNAPSHOT'].values.length, 2, 'empty refresh must preserve poll history');

const queueRows = new Map();
function queued(file, createdAt) {
  return ['queue-' + file, createdAt, 'RUN_REMOTE', JSON.stringify({file}), 'PENDING', '', '', ''];
}
const nowMs = Date.now();
queueRows.set(1200, queued('traffic_refresh_enqueue_v256', new Date(nowMs)));
queueRows.set(1780, queued('wb_ads_cluster_intelligence_v1', new Date(nowMs - 20 * 60 * 60 * 1000)));
queueRows.set(1783, queued('search_position_monitor_sync_v238', new Date(nowMs - 15 * 60 * 60 * 1000)));
const queue = {
  getMaxRows: () => 2023,
  getRange(startRow, _startColumn, rowCount) {
    return {getValues: () => Array.from({length: rowCount}, (_, offset) => queueRows.get(startRow + offset) || new Array(8).fill(''))};
  }
};
assert.equal(sandbox.sellmonitorGithubNextPendingRow_(queue), 1783, 'factual search history materialization must not starve behind derivative jobs');


const clusterRow = ['yv','2026-10-09',42,123,'таз строительный 90 л',
                    150,12,2,1,1,50,7.9,8,4.16,333,
                    'WB /adv/v1/normquery/stats','FACT_AD_CLUSTER_DAY_NOT_SEARCH_PHRASE'];
const clusterPayload = {
  cabinet:'yv',observed_at:observedAt,rows:[clusterRow],
  source:'WB /adv/v1/normquery/stats',
  quality:'FACT_AD_CLUSTER_DAY_NOT_SEARCH_PHRASE',
};
const clusterPublish=sandbox.sellmonitorGithubPublishAdClusters_(clusterPayload);
assert.equal(clusterPublish.ok,true);
assert.equal(clusterPublish.written,1);
assert.equal(clusterPublish.added,1);
assert.equal(sheets['18_AD_CLUSTERS_DAY'].values[1][4],'таз строительный 90 л');
assert.equal(sheets['18_WB_Рекламные_кластеры'].values[1][3],'таз строительный 90 л');
assert.equal(sheets['18_WB_Рекламные_кластеры'].values[1][10],7.9,'ad position must remain a distinct advertising metric');
assert.equal(sheets['18_AD_CLUSTERS_DAY'].values[1][18],'SOURCE_OK');
const clusterReplay=sandbox.sellmonitorGithubPublishAdClusters_(clusterPayload);
assert.equal(clusterReplay.added,0,'cluster replay must not duplicate');
assert.equal(clusterReplay.updated,1,'cluster replay must update the same grain');
assert.equal(sheets['18_WB_Рекламные_кластеры'].values.length,2,'per-cabinet replay must not create duplicate history');
assert.equal(sheets['18_AD_CLUSTERS_DAY'].values.length,2);
assert.throws(
  () => sandbox.sellmonitorGithubPublishAdClusters_({...clusterPayload,cabinet:'aa'}),
  /WB_CLUSTER_ROW_INVALID/
);
assert.throws(
  () => sandbox.sellmonitorGithubPublishAdClusters_({...clusterPayload,rows:[clusterRow,clusterRow]}),
  /WB_CLUSTER_DUPLICATE_INPUT/
);
const cpcWithoutViews=[...clusterRow];cpcWithoutViews[1]='2026-10-10';cpcWithoutViews[5]='';
cpcWithoutViews[12]='';cpcWithoutViews[14]='';
const cpcResult=sandbox.sellmonitorGithubPublishAdClusters_({...clusterPayload,rows:[cpcWithoutViews]});
assert.equal(cpcResult.added,1);
assert.equal(sheets['18_AD_CLUSTERS_DAY'].values[2][5],'','unavailable impressions must stay blank');
assert.equal(sheets['18_AD_CLUSTERS_DAY'].values[2][12],'','unavailable CTR must stay blank');


const campaignRow=['yv',123,456,'=Campaign 1',9,'manual','cpc','RUB',
                   1100,0,true,false,'2026-10-10T18:00:00+03:00',
                   observedAt,'WB /api/advert/v2/adverts',
                   'FACT_CURRENT_CAMPAIGN_SKU_SETTINGS'];
const campaignPayload={shop:'yv', observed_at:observedAt,
  source:'WB /api/advert/v2/adverts',
  quality:'FACT_CURRENT_CAMPAIGN_SKU_SETTINGS',
  rows:[campaignRow]};
const campaignPublish=sandbox.sellmonitorGithubPublishCampaignSettings_(campaignPayload);
assert.equal(campaignPublish.ok,true);
assert.equal(campaignPublish.written,1);
assert.equal(campaignPublish.added,1);
assert.equal(sheets['19_AD_CAMPAIGN_SETTINGS'].values[1][3],"'=Campaign 1");
assert.equal(sheets['19_AD_CAMPAIGN_SETTINGS'].values[1][8],1100);
const campaignReplay=sandbox.sellmonitorGithubPublishCampaignSettings_(campaignPayload);
assert.equal(campaignReplay.added,0);
assert.equal(campaignReplay.updated,1);
assert.equal(sheets['19_AD_CAMPAIGN_SETTINGS'].values.length,2);
const campaignBad=[...campaignRow];campaignBad[8]=-5;
assert.throws(
  () => sandbox.sellmonitorGithubPublishCampaignSettings_({...campaignPayload,rows:[campaignBad]}),
  /WB_CAMPAIGN_SETTINGS_INVALID_BID/
);

console.log('WB_TRAFFIC_PUBLISH_FIXTURE_OK', JSON.stringify({
  ads_rows: result.datasets.ads.written,
  funnel_rows: result.datasets.funnel.written,
  poll_rows: {ads:result.datasets.ads_poll.written,funnel:result.datasets.funnel_poll.written},
  empty_refresh_preserved: empty.datasets.ads.skipped,
  search_history_priority: 1783
}));
