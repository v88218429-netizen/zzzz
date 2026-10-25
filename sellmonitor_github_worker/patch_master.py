#!/usr/bin/env python3
from pathlib import Path
import re
import sys

if len(sys.argv) != 2:
    raise SystemExit("usage: patch_master.py <apps-script-source-root>")

root = Path(sys.argv[1]).resolve()
marker = "SELLMONITOR_GITHUB_MASTER_HOOK_V1"

# Refresh the Apps Script minute trigger and let the deployment workflow's
# existing tick_once_v3 endpoint run once again after this deployment.
worker = root / "SELLMONITOR_GITHUB_WORKER.gs"
if worker.exists():
    worker_text = worker.read_text(encoding="utf-8")
    worker_text = worker_text.replace(
        "var desiredVersion = 'worker-1m-v1';",
        "var desiredVersion = 'worker-1m-v2';",
    )
    worker_text = worker_text.replace(
        "var onceKey3 = 'SMC_TICK_ONCE_V3_USED';",
        "var onceKey3 = 'SMC_TICK_ONCE_V4_USED';",
    )

    # One-shot secret seeding endpoint. No JWT is stored in GitHub or Sheets.
    post_anchor = "    sellmonitorGithubWebAuth_(body.token || body.key);"
    if post_anchor in worker_text and "seed_wb_tokens_once_v1" not in worker_text:
        seed_block = """    if (String(body.action || '') === 'seed_wb_tokens_once_v1') {
      var seedProps = PropertiesService.getScriptProperties();
      if (String(body.nonce || '') !== 'fDbUQTm1QRENPzZkoPHXCUwOS5cnMWjd') {
        throw new Error('SEED_NONCE_INVALID');
      }
      if (seedProps.getProperty('SMC_WB_SEED_V1_USED') === '1') {
        return sellmonitorGithubWebJson_({ok:false,error:'WB_SEED_ALREADY_USED'});
      }
      var tokens = body.tokens || {};
      function seedPut_(key, value) {
        value = String(value || '').trim();
        if (!value || value.split('.').length !== 3 || value.length < 80) throw new Error('BAD_WB_TOKEN '+key);
        seedProps.setProperty(key, value);
      }
      seedPut_('WB_FBS_API_TOKEN__SANYCH', tokens.AP);
      seedPut_('FBS_CLIENT__SANYCH__WB_API_TOKEN', tokens.AP);
      seedPut_('FBS_CLIENT__AIR__WB_API_TOKEN', tokens.AA);
      seedPut_('FBS_CLIENT__FBS_14I5XGBA9NIG__WB_API_TOKEN', tokens.YV);
      seedProps.setProperty('SMC_WB_SEED_V1_USED','1');
      return sellmonitorGithubWebJson_({ok:true,stored:{AP:true,AA:true,YV:true},secretsReturned:false});
    }

"""
        worker_text = worker_text.replace(post_anchor, seed_block + post_anchor, 1)
        print("WB_SECRET_SEED_ENDPOINT=patched")

    # Dates written as ISO strings are read back by Apps Script as Date values
    # once the destination columns have a date number format. Compare their
    # calendar keys, not JavaScript Date.toString() output.
    date_readback_anchor = "if(rb.join('|')!==r.join('|'))throw new Error('WB_INNER_READBACK_MISMATCH:'+sh.getName()+':'+k);"
    date_readback_marker = "WB_INNER_DATE_READBACK_V2"
    if date_readback_anchor in worker_text:
        date_readback_block = """/* WB_INNER_DATE_READBACK_V2 */
      var readbackMatches = rb.length === r.length;
      var targetTimeZone = sh.getParent().getSpreadsheetTimeZone();
      for (var ci = 0; readbackMatches && ci < r.length; ci++) {
        var actualValue = rb[ci], expectedValue = r[ci];
        if ((ci === 1 || ci === 2) && actualValue instanceof Date) {
          actualValue = Utilities.formatDate(actualValue, targetTimeZone, 'yyyy-MM-dd');
        } else if (actualValue instanceof Date) actualValue = actualValue.toISOString();
        if ((ci === 1 || ci === 2) && expectedValue instanceof Date) {
          expectedValue = Utilities.formatDate(expectedValue, targetTimeZone, 'yyyy-MM-dd');
        } else if (expectedValue instanceof Date) expectedValue = expectedValue.toISOString();
        if ((actualValue == null ? '' : String(actualValue)) !== (expectedValue == null ? '' : String(expectedValue))) {
          readbackMatches = false;
        }
      }
      if (!readbackMatches) throw new Error('WB_INNER_READBACK_MISMATCH:'+sh.getName()+':'+k);"""
        worker_text = worker_text.replace(date_readback_anchor, date_readback_block, 1)
        print("WB_INNER_DATE_READBACK=patched")
    elif date_readback_marker not in worker_text and "WB_INNER_READBACK_MISMATCH" in worker_text:
        raise SystemExit("WB inner readback comparison anchor changed")
    worker.write_text(worker_text, encoding="utf-8")
    print("WORKER_TRIGGER_REFRESH_V2=patched")
candidates = []

for ext in ("*.gs", "*.js"):
    for path in root.rglob(ext):
        try:
            text = path.read_text(encoding="utf-8")
        except Exception:
            continue
        if "function runFinalAutomationCycle_" in text and "saveMasterCycleResult_(cycleErrors);" in text:
            candidates.append((path, text))

if len(candidates) != 1:
    raise SystemExit(f"expected exactly one master source, found={len(candidates)}")

path, text = candidates[0]
original_text = text

# Keep the parent export scheduler from cancelling the library's one-minute
# continuation trigger. The library remains unchanged; this wrapper simply
# yields after a chunk and does not start another export while a continuation
# is pending.
export_start = text.find("function runMarketplaceExports_() {")
if export_start < 0:
    raise SystemExit("marketplace export wrapper missing")
export_end = text.find("\nfunction ", export_start + 10)
if export_end < 0:
    export_end = len(text)
export_block = text[export_start:export_end]
guard_code = """  var continuationPending = ScriptApp.getProjectTriggers().some(function(t) {
    return t.getHandlerFunction() === 'exportDOM_continue';
  });
  if (continuationPending) {
    Logger.log('Master: выгрузка заказов пропущена; сохранён триггер продолжения exportDOM_continue.');
    return;
  }
"""
guard_marker = "var continuationPending = ScriptApp.getProjectTriggers()"
if guard_marker not in export_block:
    header_end = export_block.find("\n", export_block.find("function runMarketplaceExports_() {"))
    if header_end < 0:
        raise SystemExit("marketplace export wrapper header malformed")
    export_block = export_block[:header_end + 1] + guard_code + export_block[header_end + 1:]

result_anchor = "      DOM[job.method]();"
result_replacement = """      var exportResult = DOM[job.method]();
      if (exportResult === false) {
        var continuationCreated = ScriptApp.getProjectTriggers().some(function(t) {
          return t.getHandlerFunction() === 'exportDOM_continue';
        });
        if (!continuationCreated) {
          throw new Error(job.name + ': вернулся признак продолжения без триггера exportDOM_continue');
        }
        Logger.log(job.name + ': порция завершена; продолжение сохранено, остальные выгрузки отложены.');
        return;
      }"""
if result_anchor in export_block:
    export_block = export_block.replace(result_anchor, result_replacement, 1)
elif "var exportResult = DOM[job.method]();" not in export_block:
    export_block, replaced = re.subn(
        r"DOM\s*\[\s*job\.method\s*\]\s*\(\s*\)\s*;",
        lambda _: result_replacement,
        export_block,
        count=1,
    )
    if replaced != 1:
        raise SystemExit("marketplace export result anchor missing")

text = text[:export_start] + export_block + text[export_end:]

if marker in text:
    if text != original_text:
        path.write_text(text, encoding="utf-8")
        print(f"MARKETPLACE_CONTINUATION_PATCHED: {path.name}")
    else:
        print(f"PATCH_ALREADY_PRESENT: {path.name}")
    raise SystemExit(0)

needle = "    saveMasterCycleResult_(cycleErrors);"
if text.count(needle) != 1:
    raise SystemExit(f"expected exactly one master save point, found={text.count(needle)}")

block = """    /* SELLMONITOR_GITHUB_MASTER_HOOK_V1 */
    try {
      var smGithubTrigger = sellmonitorGithubEnsureTrigger_();
      if (smGithubTrigger && smGithubTrigger.created) {
        sellmonitorGithubPlatformReady();
        sellmonitorGithubTick();
      }
    } catch (sellmonitorGithubError) {
      cycleErrors.push(
        'Sellmonitor GitHub worker: ' + sellmonitorGithubError.message
      );
      Logger.log(
        'Sellmonitor GitHub worker: ' +
        (sellmonitorGithubError.stack || sellmonitorGithubError.message)
      );
    }

"""

text = text.replace(needle, block + needle, 1)
path.write_text(text, encoding="utf-8")
print(f"PATCH_OK: {path.name}")

# deploy-bump 2026-10-04
