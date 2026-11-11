import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import vm from 'node:vm';
import { fileURLToPath } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
const source = fs.readFileSync(path.join(here, 'SELLMONITOR_GITHUB_WORKER.gs'), 'utf8');

const rows = [
  ['ID заказа','Дата заказа','','','Статус продавца','Статус WB','Статус по-русски'],
  ['11111111','11.11.2026','','','new','waiting','Новый / WB ожидает заказ'],
  ['22222222','11.11.2026','','','confirm','sorted','На сборке / Отсортирован WB']
];
let gridRows = 12;
const cacheData = new Map();
const rangesRead = [];
function colNumber(letters) {
  return letters.split('').reduce((value, letter) => value * 26 + letter.charCodeAt(0) - 64, 0);
}
function parseRange(a1) {
  const match = String(a1).match(/^'((?:[^']|'')*)'!([A-Z]+)(\\d+):([A-Z]+)(\\d+)$/);
  assert.ok(match, 'unexpected Sheets API range: ' + a1);
  return {
    sheet: match[1].replace(/''/g, "'"),
    startColumn: colNumber(match[2]) - 1,
    startRow: Number(match[3]) - 1,
    endColumn: colNumber(match[4]),
    endRow: Number(match[5])
  };
}
function readValues(a1) {
  rangesRead.push(a1);
  const r = parseRange(a1);
  assert.equal(r.sheet, '📦 FBS Заказы');
  const out = [];
  for (let row = r.startRow; row < Math.min(r.endRow, gridRows); row++) {
    const raw = rows[row] || [];
    out.push(raw.slice(r.startColumn, r.endColumn).map(value => value == null ? '' : String(value)));
  }
  while (out.length && out[out.length - 1].every(value => value === '')) out.pop();
  return out;
}
const sandbox = {
  console,
  CacheService: { getScriptCache: () => ({
    get: key => cacheData.has(key) ? cacheData.get(key) : null,
    put: (key, value) => cacheData.set(key, value)
  }) },
  Sheets: { Spreadsheets: {
    get: () => ({ sheets: [{ properties: { title: '📦 FBS Заказы', gridProperties: { rowCount: gridRows } } }] }),
    Values: { get: (_spreadsheetId, a1) => ({ values: readValues(a1) }) }
  } }
};
Object.defineProperty(sandbox, 'SpreadsheetApp', {
  get() { throw new Error('LEGACY_SPREADSHEET_SERVICE_USED'); }
});
vm.createContext(sandbox);
function topLevelFunction(name) {
  const marker = 'function ' + name + '(';
  const at = source.indexOf(marker);
  assert.notEqual(at, -1, 'function not found: ' + name);
  const start = source.lastIndexOf('\\n', at) + 1;
  const next = source.indexOf('\\nfunction ', at + 1);
  return source.slice(start, next < 0 ? source.length : next + 1);
}
vm.runInContext([
  topLevelFunction('sellmonitorGithubFbsSaveIdIndex_'),
  topLevelFunction('sellmonitorGithubFbsLoadIdIndex_'),
  topLevelFunction('sellmonitorGithubFbsStatusReadV1_')
].join('\\n'), sandbox);

const first = sandbox.sellmonitorGithubFbsStatusReadV1_({
  client: 'AIR', orderIds: ['11111111', '22222222']
});
assert.equal(first.ok, true);
assert.equal(first.sheetLastRow, 3);
assert.equal(first.found, 2);
assert.equal(first.rowMap['11111111'], 2);
assert.equal(first.statuses['22222222'].wbStatus, 'sorted');
assert.ok(rangesRead.includes("'📦 FBS Заказы'!A2:A12"), 'initial index must use Sheets API');

rows.push(['33333333','11.11.2026','','','complete','sold','Передан в доставку / Продан']);
const second = sandbox.sellmonitorGithubFbsStatusReadV1_({
  client: 'AIR', orderIds: ['11111111', '33333333']
});
assert.equal(second.sheetLastRow, 4);
assert.equal(second.found, 2);
assert.equal(second.rowMap['33333333'], 4);
assert.equal(second.statuses['33333333'].wbStatus, 'sold');
assert.ok(rangesRead.includes("'📦 FBS Заказы'!A4:A12"), 'cache must extend only the new ID tail');

console.log('FBS_STATUS_READ_SHEETS_API=passed');
console.log('FBS_STATUS_READ_CACHE_TAIL=passed');
console.log('FBS_STATUS_READ_LEGACY_SERVICE=not_used');
