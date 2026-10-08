import fs from 'node:fs';
import vm from 'node:vm';
import assert from 'node:assert/strict';

const source = fs.readFileSync(new URL('./SELLMONITOR_GITHUB_WORKER.gs', import.meta.url), 'utf8');
const sandbox = { console, Date, Math, JSON, Number, String, Boolean, Array, Object, RegExp, isFinite, parseFloat, parseInt };
vm.createContext(sandbox);
vm.runInContext(source, sandbox);

const analyze = sandbox.sellmonitorGithubAnalyzeAdsClusters_;
assert.equal(typeof analyze, 'function');

const rows = [
  {normQuery:'лотки для метизов', views:4200, clicks:100, atbs:18, orders:3, shks:3, spend:400, clusterBidKopecks:18000, clusterState:'active'},
  {normQuery:'контейнер для хранения', views:5100, clicks:80, atbs:4, orders:0, shks:0, spend:1000, clusterBidKopecks:36000, clusterState:'active'},
  {normQuery:'органайзер пластиковый', views:2600, clicks:50, atbs:5, orders:1, shks:1, spend:700, clusterBidKopecks:30000, clusterState:'active'}
];

const out = analyze(rows, 400);
const by = Object.fromEntries(out.map(x => [x.normQuery, x]));

assert.equal(by['контейнер для хранения'].action, 'ИСКЛЮЧИТЬ / МИНУСОВАТЬ');
assert.equal(by['органайзер пластиковый'].action, 'СНИЗИТЬ СТАВКУ');
assert.equal(by['лотки для метизов'].action, 'ОСТАВИТЬ / МАСШТАБИРОВАТЬ');
assert.ok(by['органайзер пластиковый'].targetBidKopecks > 0);
assert.ok(by['органайзер пластиковый'].targetBidKopecks < by['органайзер пластиковый'].currentBidKopecks);
assert.ok(new Set(out.map(x => x.action)).size > 1, 'Нельзя давать blanket-решение всей кампании');

const sumSpend = out.reduce((a,x)=>a+x.spend,0);
assert.equal(sumSpend, 2100);
console.log('ADS_CLUSTER_FIXTURE_OK', JSON.stringify({
  sku:1194660182,
  campaign:40360852,
  actions:out.map(x=>({q:x.normQuery,action:x.action,spend:x.spend,cpa:x.cpa,targetBid:x.targetBidKopecks}))
}));
