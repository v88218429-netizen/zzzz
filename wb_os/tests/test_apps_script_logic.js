#!/usr/bin/env node
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const assert = require('assert');

const root = path.resolve(__dirname, '..');
const ctx = vm.createContext({ console });

function load(rel) {
  const code = fs.readFileSync(path.join(root, rel), 'utf8');
  vm.runInContext(code, ctx, { filename: rel });
}

load('apps_script/WB_PUBLIC_PRICE_V4.gs');
load('apps_script/K2_EVOLUTION_ENGINE.gs');

// WB price: choose the cheapest available size, not simply sizes[0].
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
  assert.strictEqual(p.logistics, 4);
}

// If nothing is in stock, preserve the previous parser behavior: cheapest size.
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
      logistics: 5,
      totalField: 125
    };
  }

  const result = ctx.wbPriceV4Calibrate_(rows, fetched);
  assert.strictEqual(result.ok, true);
  assert.strictEqual(result.mode, 'product_plus_logistics');
  assert.strictEqual(result.rows, 12);
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

// Legacy trigger cleanup must happen only after the new engine has succeeded.
{
  const k2Source = fs.readFileSync(
    path.join(root, 'apps_script/K2_EVOLUTION_ENGINE.gs'),
    'utf8'
  );
  const k2FnStart = k2Source.indexOf('function k2EvolutionFetchAndApply_');
  const k2FnEnd = k2Source.indexOf('\nfunction k2EvolutionRecordFailure_', k2FnStart);
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
  const priceFnStart = priceSource.indexOf('function syncWbPublicCustomerPricesV4_');
  const priceFnEnd = priceSource.indexOf('\nfunction forceSyncWbPublicCustomerPricesV4', priceFnStart);
  const priceFn = priceSource.slice(priceFnStart, priceFnEnd);
  assert.ok(priceFn.indexOf('.setValues(output)') >= 0);
  assert.ok(priceFn.indexOf('wbPriceV4CleanupLegacyTriggers_') >= 0);
  assert.ok(
    priceFn.indexOf('wbPriceV4CleanupLegacyTriggers_') >
    priceFn.indexOf('.setValues(output)')
  );
}

console.log('WB OS Apps Script logic tests: OK');
