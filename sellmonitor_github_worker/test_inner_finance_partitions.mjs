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
  globalThis.__checksum = sellmonitorGithubInnerFinanceChecksum_;
  globalThis.__dateChecksums = function(a, b) {
    return [
      sellmonitorGithubInnerFinanceChecksum_([[new Date(a)]]),
      sellmonitorGithubInnerFinanceChecksum_([[new Date(b)]])
    ];
  };
  globalThis.__loadedAtChecksums = function(a, b) {
    var rowA = Array(41).fill(''), rowB = Array(41).fill('');
    rowA[40] = a;
    rowB[40] = b;
    return [
      sellmonitorGithubInnerFinanceChecksum_([rowA]),
      sellmonitorGithubInnerFinanceChecksum_([rowB])
    ];
  };
  globalThis.__numberChecksums = function(a, b) {
    return [sellmonitorGithubInnerFinanceChecksum_([[a]]),sellmonitorGithubInnerFinanceChecksum_([[b]])];
  };
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

context.Utilities = {
  DigestAlgorithm: { SHA_256: 'test' },
  Charset: { UTF_8: 'test' },
  formatDate: (date) => date.toISOString().slice(0, 19) + 'Z',
  computeDigest: (_algorithm, value) => Array.from(String(value), x => x.charCodeAt(0) & 255)
};
const [olderDateChecksum, roundedDateChecksum] = context.__dateChecksums(
  '2026-10-13T17:18:52.100Z', '2026-10-13T17:18:52.900Z'
);
assert.equal(olderDateChecksum, roundedDateChecksum,
  'Sheets date precision must not make a correct read-back fail');
const [dateObjectChecksum, dateTextChecksum] = context.__loadedAtChecksums(
  '2026-10-13T17:18:52.100Z', '2026-10-13T17:18:52.900Z'
);
assert.equal(dateObjectChecksum, dateTextChecksum,
  'loaded_at may be read back as a date instead of its original text');
const [precisionA, precisionB] = context.__numberChecksums(1.2345670001, 1.2345670002);
assert.equal(precisionA, precisionB, 'Sheets numeric precision must not make a correct read-back fail');

console.log('INNER_FINANCE_PARTITION_FIXTURE_OK', JSON.stringify({stores:stores.length,columns:context.__headers.length}));
