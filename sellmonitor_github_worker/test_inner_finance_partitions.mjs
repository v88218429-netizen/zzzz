import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

const source = readFileSync(new URL('./SELLMONITOR_GITHUB_WORKER.gs', import.meta.url), 'utf8');
const context = {};
vm.runInNewContext(source + `
  globalThis.__innerRows = function(storeId, products, from, to) {
    return sellmonitorGithubInnerFinanceRows_(
      SMC_INNER_FINANCE_STORES[storeId], from, to, products, new Date('2026-10-13T12:00:00Z')
    );
  };
  globalThis.__headers = SMC_INNER_FINANCE_HEADERS;
`, context, { timeout: 3000 });

const stores = ['sanych_wb', 'air_wb', 'hozyayushka_wb'];
for (const storeId of stores) {
  const rows = context.__innerRows(storeId, [{
    id: 123456,
    supplierArticle: 'SKU-1',
    orderedAmount: { count: 0, price: 0 },
    revenueAmount: { price: 12345 }
  }], '2026-10-06', '2026-10-07');
  assert.equal(rows.length, 1);
  assert.equal(rows[0].length, context.__headers.length);
  assert.equal(rows[0][0], storeId);
  assert.equal(rows[0][3], '2026-10-06');
  assert.equal(rows[0][4], '2026-10-07');
  assert.equal(rows[0][6], '123456');
  assert.equal(rows[0][8], 0, 'real source zero must remain zero');
  assert.equal(rows[0][14], 123.45, 'currency units are normalized from kopecks');
  assert.equal(rows[0][9], 0, 'real source zero amount must remain zero');
  assert.equal(rows[0][10], '', 'missing metric must remain blank, not become zero');
}

assert.throws(() => context.__innerRows('air_wb', [
  { id: 1 }, { id: 1 }
], '2026-10-06', '2026-10-07'), /INNER_FINANCE_DUPLICATE_PRODUCT/);
assert.throws(() => context.__innerRows('air_wb', [
  { id: 1 }
], '2026-10-06', '2026-10-08'), /INNER_FINANCE_DAILY_PERIOD_REQUIRED/);

console.log('INNER_FINANCE_PARTITION_FIXTURE_OK', JSON.stringify({stores:stores.length,columns:context.__headers.length}));
