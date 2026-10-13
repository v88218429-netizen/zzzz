import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

const source = readFileSync(new URL('./SELLMONITOR_GITHUB_WORKER.gs', import.meta.url), 'utf8');
const headers = [
  'spreadsheet_id','enabled','client_name','store_id','state','status',
  'last_seen','last_sync','error','qc_status','worker_version',
  ...Array.from({length: 9}, (_, i) => 'unused_' + i)
];
function client(id, name, storeId, status) {
  const row = Array(headers.length).fill('');
  row[headers.indexOf('spreadsheet_id')] = id;
  row[headers.indexOf('enabled')] = true;
  row[headers.indexOf('client_name')] = name;
  row[headers.indexOf('store_id')] = storeId;
  row[headers.indexOf('state')] = 'ACTIVE';
  row[headers.indexOf('status')] = status;
  return row;
}
const rows = [
  client('air-id', 'AIR', 'air_wb', 'worker_ok'),
  client('hoz-id', 'Хозяюшка', 'hozyayushka_wb', 'worker_ok'),
  client('sanych-id', 'Дом Саныча', 'sanych_wb', 'worker_ok')
];
const writes = new Map();
const sheet = {
  getLastRow: () => rows.length + 1,
  getLastColumn: () => headers.length,
  getRange(row, col, numRows, numCols) {
    return {
      getDisplayValues() {
        if (row === 1) return [headers];
        return rows.slice(row - 2, row - 2 + numRows).map(x => x.slice(col - 1, col - 1 + numCols).map(String));
      },
      getValues() {
        if (row === 1) return [headers];
        return rows.slice(row - 2, row - 2 + numRows).map(x => x.slice(col - 1, col - 1 + numCols));
      },
      setValue(value) { writes.set(row + ':' + col, value); }
    };
  }
};
const cc = { getSheetByName: name => name === 'Clients' ? sheet : null };
const properties = new Map([['SMC_GH_CLIENT_CURSOR', '2']]);
const scriptProperties = {
  getProperty: key => properties.has(key) ? properties.get(key) : null,
  setProperty: (key, value) => properties.set(key, String(value))
};
const executed = [];
const context = {
  SpreadsheetApp: { openById: () => cc, flush: () => {} },
  PropertiesService: { getScriptProperties: () => scriptProperties },
  LockService: { getScriptLock: () => ({ tryLock: () => true, releaseLock: () => {} }) }
};
vm.runInNewContext(source, context, { timeout: 2000 });
context.sellmonitorGithubProcessClient_ = id => {
  executed.push(id);
  return { processedCommands: 1, ready: false };
};

const result = context.sellmonitorGithubTick();
assert.equal(result.startClient, 'sanych_wb');
assert.equal(result.processedClients, 1);
assert.equal(result.processedCommands, 1);
assert.deepEqual(executed, ['air-id']);
assert.equal(writes.get('4:6'), 'EXTERNAL_PARTITIONS_PARTIAL');
assert.equal(writes.get('4:9'), 'Legacy workbook exceeds Sheets write capacity; client-queue writes are paused. Traffic and finance are partitioned; remaining daily datasets still require migration.');
console.log('Paused-client worker slot regression: PASS');
