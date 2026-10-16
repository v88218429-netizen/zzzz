import fs from 'node:fs';
import vm from 'node:vm';
import assert from 'node:assert/strict';

const source = fs.readFileSync(process.env.WBCC_WORKER_SOURCE || new URL('./SELLMONITOR_GITHUB_WORKER.gs', import.meta.url), 'utf8');
assert.doesNotMatch(source, /\bDriveApp\s*\./, 'worker collection and publication must not require Drive-wide OAuth');

function makeSheet(headers) {
  const values = [headers.slice()];
  let maxRows = 5;
  return {
    values,
    setName(name) { this.name = name; },
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
const partitionBooks = new Map();
const propertyStore = new Map();
let nextPartition = 0;
function makeWorkbook(id) {
  const localSheets = {};
  return {
    getSheetByName: name => localSheets[name] || null,
    insertSheet(name) {
      const sheet = makeSheet([]);
      localSheets[name] = sheet;
      sheet.setName = nextName => {
        delete localSheets[name];
        localSheets[nextName] = sheet;
        sheet.name = nextName;
      };
      return sheet;
    },
    getSheets: () => Object.values(localSheets),
    getId: () => id,
    getSpreadsheetTimeZone: () => 'Europe/Moscow'
  };
}
const registryHeaders = ['part_id','platform','shop_id','dataset','layer','period_start','period_end'];
const registrySheet = makeSheet(registryHeaders);
const registryViews = {};
const registryWorkbook = {
  getSheetByName(name) { return name === 'Части' ? registrySheet : registryViews[name] || null; },
  insertSheet(name) { const sheet = makeSheet([]); registryViews[name] = sheet; return sheet; },
  getSpreadsheetTimeZone: () => 'Etc/GMT'
};
let openedId = '';
const sandbox = {
  console, Date, Math, JSON, Number, String, Boolean, Array, Object, RegExp, isFinite,
  parseFloat, parseInt,
  SpreadsheetApp: {
    openById(id) {
      openedId = id;
      if (id === '1E7HSGJgamzKZkdNdDNyp0f1iI2mLhhbpfi04-sOytBs') return registryWorkbook;
      if (id === '11ULokTx74QjziZjThJ0lfxFiMQW42-WV4315cG7eIuU') return workbook;
      const part = partitionBooks.get(id);
      if (part) return part;
      if (String(id).startsWith('traffic-part-')) throw new Error('Spreadsheet not found: ' + id);
      return workbook;
    },
    create(_title) {
      const id = 'traffic-part-' + (++nextPartition);
      const book = makeWorkbook(id);
      book.insertSheet('Sheet1');
      partitionBooks.set(id, book);
      return book;
    },
    flush() {}
  },
  PropertiesService: {
    getScriptProperties() {
      return {
        getProperty: key => propertyStore.get(key) || '',
        setProperty: (key, value) => propertyStore.set(key, String(value))
      };
    }
  },
  Utilities: {
    DigestAlgorithm: {SHA_256:'SHA_256'},
    Charset: {UTF_8:'UTF_8'},
    computeDigest(_algo, value) { return Array.from({length:32}, (_,i) => value.charCodeAt(i % value.length) || i); },
    formatDate(value, _tz, pattern) {
      const shifted = new Date(value.getTime() + 3 * 60 * 60 * 1000);
      return pattern === 'yyyy-MM-dd' ? shifted.toISOString().slice(0,10) : shifted.toISOString();
    }
  }
};
vm.createContext(sandbox);
vm.runInContext(source, sandbox);
assert.equal(sandbox.sellmonitorGithubGridTargetRows_(6988, 1000, 500), 7488,
  'retain every populated traffic row plus a bounded reserve');
assert.equal(sandbox.sellmonitorGithubGridTargetRows_(0, 1000, 500), 1000,
  'empty traffic sheets retain a minimum writable grid');
const sanychSettings = {
  getLastRow: () => 1,
  getRange: () => ({getDisplayValues: () => [['ACTIVE_STORE_ID', 'sanych_wb']]})
};
const sanychCapacity = sandbox.sellmonitorGithubRepairTrafficCapacity_({
  getSheetByName(name) {
    assert.equal(name, '99_Настройки');
    return sanychSettings;
  }
});
assert.equal(sanychCapacity.skipped, true, 'oversized Sanych workbook must not block worker queues');
assert.equal(sanychCapacity.reason, 'external_monthly_partitions');

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
  partition_only: true,
  sync_status: status,
  datasets: {
    ads: [['ap','2026-10-09','42','9','100','10','2','1','3.5','50','WB /adv/v3/fullstats','EXACT_CAMPAIGN_NM_DAY','=formula-like']],
    funnel: [
      ['ap','2026-10-09','9','SKU9','10','2','1','50','WB Analytics products/history'],
      ['aa','2026-10-09','10','@formula-like','20','3','2','100','WB Analytics products/history']
    ],
    organic: [
      ['ap','2026-10-09','9','SKU9','10','10','0','WB Analytics products/history','WB /adv/v3/fullstats','total_open_count-paid_clicks','DERIVED_COMPLETE',3,0,0],
      ['aa','2026-10-09','10','SKU10','20','','','WB Analytics products/history','WB /adv/v3/fullstats','total_open_count-paid_clicks','ADVERTISING_COVERAGE_INCOMPLETE',4,0,1]
    ],
    ads_poll: [['ap',observedAt,'2026-10-09',1,1,100,10,2,1,3.5,50,0.1,0.35,0.07,14,'WB /adv/v3/fullstats','DAILY_CUMULATIVE_OBSERVED_AT_POLL']],
    funnel_poll: [['aa',observedAt,'2026-10-09',1,20,3,2,100,0.15,0.666667,0.1,'WB Analytics products/history','DAILY_CUMULATIVE_OBSERVED_AT_POLL']]
  }
});

assert.notEqual(openedId, '11ULokTx74QjziZjThJ0lfxFiMQW42-WV4315cG7eIuU',
  'partition-only publication must not open the crowded aggregate workbook');
assert.equal(result.ok, true);
assert.equal(result.action, 'publish_wb_traffic');
assert.equal(result.datasets, undefined, 'partition-only result has no duplicate aggregate write');
assert.equal(result.partitions.datasets.ads.written, 1, 'ads must be written to a monthly partition');
assert.equal(result.partitions.datasets.funnel.written, 2, 'funnel must be written to monthly partitions');
assert.equal(result.partitions.datasets.organic.written, 2, 'derived organic facts and explicit incomplete coverage must be partitioned');
assert.equal(result.partitions.datasets.organic.parts[0].qc_status, 'PASS');
assert.equal(result.partitions.datasets.ads.parts[0].qc_status, 'PASS');
assert.equal(result.partitions.datasets.daily_view.rows, 2, 'compact view must contain one daily row per store with source facts');
const dailyView = registryViews['Дневная_витрина'];
assert.ok(dailyView, 'compact daily view must be published in the shared registry');
assert.equal(dailyView.values.length, 3, 'daily view contains one header and two store/date rows');
const completeRow = dailyView.values.find(row => row[1] === 'Саныч');
const incompleteRow = dailyView.values.find(row => row[1] === 'AIR');
assert.equal(completeRow[12], 'ПОЛНЫЙ РАСЧЁТ', 'organic is visible only after clicks and funnel totals reconcile');
assert.equal(completeRow[11], 0, 'a valid zero organic result remains zero');
assert.equal(incompleteRow[10], '', 'incomplete paid-click source remains blank');
assert.equal(incompleteRow[11], '', 'incomplete organic result remains blank, not zero');
assert.equal(incompleteRow[12], 'НЕПОЛНО · не считать нулём');
assert.equal(partitionBooks.size, 3, 'organic and poll facts use separate bounded tabs in their existing store-month books');
assert.equal(sheets['12_ADS_CAMPAIGN_DAY'].values.length, 1, 'do not copy facts into the full aggregate book');
assert.equal(sheets['13_FUNNEL_DAY'].values.length, 1, 'do not copy facts into the full aggregate book');
assert.equal(sheets['15_ADS_POLL_SNAPSHOT'], undefined);
assert.equal(sheets['16_FUNNEL_POLL_SNAPSHOT'], undefined);
assert.equal(sheets['17_TRAFFIC_QUALITY'], undefined);

const replay = publish({partition_only:true,sync_status:status,datasets:{
  ads:[['ap','2026-10-09','42','9','100','10','2','1','3.5','50','WB /adv/v3/fullstats','EXACT_CAMPAIGN_NM_DAY','=formula-like']],
  funnel:[['ap','2026-10-09','9','SKU9','10','2','1','50','WB Analytics products/history']],
  organic:[['ap','2026-10-09','9','SKU9','10','10','0','WB Analytics products/history','WB /adv/v3/fullstats','total_open_count-paid_clicks','DERIVED_COMPLETE',3,0,0]],
  ads_poll:[['ap',observedAt,'2026-10-09',1,1,100,10,2,1,3.5,50,0.1,0.35,0.07,14,'WB /adv/v3/fullstats','DAILY_CUMULATIVE_OBSERVED_AT_POLL']],
  funnel_poll:[]
}});
assert.equal(replay.partitions.datasets.ads_poll.parts[0].added, 0, 'replayed poll must not create duplicate history');
assert.equal(replay.partitions.datasets.ads_poll.parts[0].updated, 1, 'replayed poll must update the existing observation');
assert.equal(replay.partitions.datasets.daily_view.rows, 2, 'replayed traffic updates the same bounded daily rows');
assert.equal(replay.partitions.datasets.daily_view.updated, 2, 'replayed daily facts must upsert without duplication');
assert.equal(registryViews['Дневная_витрина'].values.length, 3, 'daily replay must not append duplicate rows');

assert.throws(() => publish({
  sync_status: {...status, shops: {...status.shops, yv: {ok:false}}},
  datasets: {ads: [], funnel: []}
}), /WB_TRAFFIC_SHOP_NOT_VERIFIED/);
assert.throws(() => sandbox.sellmonitorGithubNormalizeWbTrafficRows_('ads', [
  ['ap','2026-10-09','42','9','100','10','2','1','3.5','50','WB /adv/v3/fullstats','EXACT_CAMPAIGN_NM_DAY','a'],
  ['ap','2026-10-09','42','9','100','10','2','1','3.5','50','WB /adv/v3/fullstats','EXACT_CAMPAIGN_NM_DAY','a']
], status.finishedAt), /WB_TRAFFIC_DUPLICATE_KEY/);

const financeRow = [
  'Саныч','rep-1','2026-10-11','2026-10-11','2026-10-12','2026-10-11','12345',
  'Отчет','Продажа','Начисление','12,50','','0','0','0','12,50','Причина',
  '42','SKU-42','Товар','Склад','FBS','order-1','uid-1','srid-1','sticker-1',
  '2026-10-11','2026-10-11','','120,00','Авто GitHub',''
];
const financeRun = '2026-10-12T20:00:00.000Z';
const financeResult = sandbox.sellmonitorGithubPublishWbFinance_({
  shop:'Саныч',month:'2026-10',sync_run_id:financeRun,rows:[financeRow]
});
assert.equal(financeResult.ok,true);
assert.equal(financeResult.written,1);
assert.equal(financeResult.distinct_keys,1);
const financeBook=partitionBooks.get(financeResult.file_id);
const financeFacts=financeBook.getSheetByName('facts');
assert.equal(financeFacts.values[1][10],12.5,'localized financial values must become numbers');
assert.equal(financeFacts.values[1][11],'','missing charges stay blank, never zero-filled');
const financeReplay=sandbox.sellmonitorGithubPublishWbFinance_({
  shop:'Саныч',month:'2026-10',sync_run_id:financeRun,rows:[financeRow]
});
assert.equal(financeReplay.added,0,'finance replay must not duplicate a stable RRD fact');
assert.equal(financeReplay.updated,1,'finance replay must update the same natural key');

const empty = publish({partition_only:true,sync_status:status, datasets:{ads:[], funnel:[]}});
assert.equal(empty.partitions.datasets.ads.skipped, true);
assert.equal(sheets['12_ADS_CAMPAIGN_DAY'].values.length, 1, 'empty refresh must leave the aggregate book untouched');

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
assert.equal(campaignPublish.client_catalog.written,1);
assert.equal(sheets['90_RAW_ads_campaigns'].values[1][0],123);
assert.equal(sheets['90_RAW_ads_campaigns'].values[1][1],456);
assert.equal(sheets['90_RAW_ads_campaigns'].values[1][3],"'=Campaign 1");
assert.equal(sheets['90_RAW_ads_campaigns'].values[1][9],1100);
assert.equal(sheets['90_RAW_ads_campaigns'].values[1][14],'LIVE_WB_V2_NO_JEM');
const campaignReplay=sandbox.sellmonitorGithubPublishCampaignSettings_(campaignPayload);
assert.equal(campaignReplay.added,0);
assert.equal(campaignReplay.updated,1);
assert.equal(sheets['19_AD_CAMPAIGN_SETTINGS'].values.length,2);
assert.equal(campaignReplay.client_catalog.added,0,'client replay cannot duplicate campaign records');
assert.equal(campaignReplay.client_catalog.updated,1);
assert.equal(sheets['90_RAW_ads_campaigns'].values.length,2);
const campaignBad=[...campaignRow];campaignBad[8]=-5;
assert.throws(
  () => sandbox.sellmonitorGithubPublishCampaignSettings_({...campaignPayload,rows:[campaignBad]}),
  /WB_CAMPAIGN_SETTINGS_INVALID_BID/
);

console.log('WB_TRAFFIC_PUBLISH_FIXTURE_OK', JSON.stringify({
  ads_rows: result.partitions.datasets.ads.written,
  funnel_rows: result.partitions.datasets.funnel.written,
  poll_rows: {ads:result.partitions.datasets.ads_poll.written,funnel:result.partitions.datasets.funnel_poll.written},
  empty_refresh_preserved: empty.partitions.datasets.ads.skipped,
  search_history_priority: 1783
}));
