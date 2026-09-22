#!/usr/bin/env node
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const assert = require('assert');
const crypto = require('crypto');

const root = path.resolve(__dirname, '..');

const ctx = vm.createContext({
  console,
  Utilities: {
    DigestAlgorithm: { SHA_256: 'SHA_256' },
    Charset: { UTF_8: 'UTF_8' },
    computeDigest: (_algorithm, value) => {
      return Array.from(
        crypto.createHash('sha256').update(String(value), 'utf8').digest()
      );
    },
    base64EncodeWebSafe: (bytes) => {
      return Buffer.from(bytes).toString('base64url');
    }
  }
});

function load(rel) {
  const code = fs.readFileSync(path.join(root, rel), 'utf8');
  vm.runInContext(code, ctx, { filename: rel });
}

load('apps_script/WB_PUBLIC_PRICE_V4.gs');
load('apps_script/K2_EVOLUTION_ENGINE.gs');

// WB price: choose the cheapest AVAILABLE size, not simply sizes[0].
{
  const p = ctx.wbPriceV4ExtractPrice_({
    totalQuantity: 5,
    sizes: [
      {
        price: { product: 9000, basic: 12000, logistics: 300 },
        stocks: [{ qty: 0 }]
      },
      {
        price: { product: 11000, basic: 14000, logistics: 400 },
        stocks: [{ qty: 2 }]
      },
      {
        price: { product: 12500, basic: 15000, logistics: 500 },
        stocks: [{ qty: 3 }]
      }
    ]
  });

  assert.strictEqual(p.product, 110);
  assert.strictEqual(p.productPlusLogistics, 114);
}

// Each calibration mode must get its own minimum across available sizes.
// The size with the cheapest product is not always the cheapest
// product+logistics size.
{
  const p = ctx.wbPriceV4ExtractPrice_({
    totalQuantity: 4,
    sizes: [
      {
        price: { product: 9000, logistics: 3000, total: 12000 },
        stocks: [{ qty: 2 }]
      },
      {
        price: { product: 10000, logistics: 100, total: 10100 },
        stocks: [{ qty: 2 }]
      }
    ]
  });

  assert.strictEqual(p.product, 90);
  assert.strictEqual(p.productPlusLogistics, 101);
  assert.strictEqual(p.totalField, 101);
}

// If nothing is in stock, preserve the old parser behavior: cheapest size.
{
  const p = ctx.wbPriceV4ExtractPrice_({
    totalQuantity: 0,
    sizes: [
      { price: { product: 13000 }, stocks: [] },
      { price: { product: 10500 }, stocks: [] }
    ]
  });
  assert.strictEqual(p.product, 105);
}

// Calibration must pick a mode that actually passes the quality gates.
{
  const rows = [];
  const fetched = {};

  for (let i = 0; i < 12; i++) {
    const id = String(100000 + i);
    const row = Array(11).fill('');
    row[2] = id;
    row[10] = 105;
    rows.push(row);

    fetched[id] = {
      product: 100,
      productPlusLogistics: 105,
      logistics: 5,
      totalField: 125
    };
  }

  const result = ctx.wbPriceV4Calibrate_(rows, fetched);
  assert.strictEqual(result.ok, true);
  assert.strictEqual(result.mode, 'product_plus_logistics');
  assert.strictEqual(result.rows, 12);
}

// V4 must mirror the known-working legacy endpoint rather than forcing an
// arbitrary spp query parameter that could change buyer-price semantics.
{
  const priceSource = fs.readFileSync(
    path.join(root, 'apps_script/WB_PUBLIC_PRICE_V4.gs'),
    'utf8'
  );
  assert.ok(!priceSource.includes("'&spp=30'"));
}

// K2 duplicate snapshots fail closed.
{
  const props = { getProperty: () => '0' };
  const normalized = [
    { sku: 'A', name: 'one', stock: 1, reserved: 0, minStock: 0 },
    { sku: 'A', name: 'two', stock: 2, reserved: 0, minStock: 0 }
  ];
  const result = ctx.k2EvolutionValidateSnapshot_(normalized, props);
  assert.strictEqual(result.ok, false);
  assert.ok(result.message.includes('K2_EV_DUPLICATES'));
}

// K2 large sudden SKU loss is quarantined.
{
  const props = {
    getProperty: (key) => key === ctx.K2_EV.LAST_COUNT_KEY ? '100' : ''
  };
  const normalized = Array.from({ length: 70 }, (_, i) => ({
    sku: String(i),
    name: 'x' + i,
    stock: 1,
    reserved: 0,
    minStock: 0
  }));
  const result = ctx.k2EvolutionValidateSnapshot_(normalized, props);
  assert.strictEqual(result.ok, false);
  assert.ok(result.message.includes('K2_EV_QUARANTINE'));
}

// Hash encoding must not collide merely because a SKU/name contains "|".
{
  const a = [{
    sku: 'A|B',
    name: 'C',
    stock: 1,
    reserved: 2,
    minStock: 3
  }];
  const b = [{
    sku: 'A',
    name: 'B|C',
    stock: 1,
    reserved: 2,
    minStock: 3
  }];

  assert.notStrictEqual(
    ctx.k2EvolutionSnapshotHash_(a),
    ctx.k2EvolutionSnapshotHash_(b)
  );
}

// The current trusted K2 sheet gets its own business-state hash. This allows
// the engine to repair a manually/stale-corrupted sheet even when the source
// snapshot itself did not change.
{
  ctx.SpreadsheetApp = {
    getActiveSpreadsheet: () => ({
      getSheetByName: () => ({
        getLastRow: () => 2,
        getRange: () => ({
          getValues: () => [['A', 'one', 1, 0, 0]]
        })
      })
    })
  };

  const expected = ctx.k2EvolutionSnapshotHash_([
    { sku: 'A', name: 'one', stock: 1, reserved: 0, minStock: 0 }
  ]);

  assert.strictEqual(ctx.k2EvolutionCurrentSheetHash_(), expected);
}

// Legacy trigger cleanup must happen only after the new engine has succeeded.
{
  const k2Source = fs.readFileSync(
    path.join(root, 'apps_script/K2_EVOLUTION_ENGINE.gs'),
    'utf8'
  );
  const k2FnStart = k2Source.indexOf('function k2EvolutionFetchAndApply_');
  const k2FnEnd = k2Source.indexOf(
    '\nfunction k2EvolutionRecordFailure_',
    k2FnStart
  );
  const k2Fn = k2Source.slice(k2FnStart, k2FnEnd);

  assert.ok(k2Fn.indexOf('saveK2SyncSuccess_') >= 0);
  assert.ok(k2Fn.indexOf('k2EvolutionCleanupLegacyTriggers_') >= 0);
  assert.ok(
    k2Fn.indexOf('k2EvolutionCleanupLegacyTriggers_') >
    k2Fn.indexOf('saveK2SyncSuccess_')
  );

  const priceSource = fs.readFileSync(
    path.join(root, 'apps_script/WB_PUBLIC_PRICE_V4.gs'),
    'utf8'
  );
  const priceFnStart = priceSource.indexOf(
    'function syncWbPublicCustomerPricesV4_'
  );
  const priceFnEnd = priceSource.indexOf(
    '\nfunction forceSyncWbPublicCustomerPricesV4',
    priceFnStart
  );
  const priceFn = priceSource.slice(priceFnStart, priceFnEnd);

  assert.ok(priceFn.indexOf('.setValues(output)') >= 0);
  assert.ok(priceFn.indexOf('wbPriceV4CleanupLegacyTriggers_') >= 0);
  assert.ok(
    priceFn.indexOf('wbPriceV4CleanupLegacyTriggers_') >
    priceFn.indexOf('.setValues(output)')
  );

  assert.ok(k2Source.includes('ensureFinalAutomationTrigger_'));
  assert.ok(k2Source.includes('master trigger helper отсутствует'));
  assert.ok(priceSource.includes('ensureFinalAutomationTrigger_'));
  assert.ok(priceSource.includes('старый SPP trigger сохранён'));
  assert.ok(priceSource.includes('hasLegacyAlertLayer'));
}

console.log('WB OS Apps Script logic tests: OK');
