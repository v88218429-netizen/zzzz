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
    # On an unknown tool, expose the live finance/product/order/report names
    # plus the bounded raw-report schema, never credentials or source rows.
    mcp_catalog_anchor = "  return sellmonitorGithubMcpData_(call.json);"
    mcp_catalog_marker = "INNER_MCP_TOOL_SCHEMA_DIAGNOSTIC_V3"
    if mcp_catalog_anchor in worker_text and mcp_catalog_marker not in worker_text:
        mcp_catalog_replacement = """  /* INNER_MCP_TOOL_SCHEMA_DIAGNOSTIC_V3 */
  try {
    return sellmonitorGithubMcpData_(call.json);
  } catch (innerError) {
    var innerMessage = String(innerError && innerError.message ? innerError.message : innerError);
    if (!/unknown tool|method not found/i.test(innerMessage)) throw innerError;
    var innerProps = PropertiesService.getScriptProperties();
    var cachedTools = String(innerProps.getProperty('SMC_INNER_MCP_TOOL_SCHEMA_V3') || '');
    if (!cachedTools) {
      try {
        var catalog = post({jsonrpc:'2.0',id:3,method:'tools/list',params:{}},call.session);
        if (catalog.http >= 200 && catalog.http < 300) {
          var catalogItem = catalog.json;
          if (Array.isArray(catalogItem)) catalogItem = catalogItem.filter(function(x){return x && x.id === 3;})[0] || catalogItem[0];
          var allTools = (((catalogItem || {}).result || {}).tools || []);
          var names = allTools.filter(function(t){return /finance|product|order|sales|report|wildberries|\\bwb\\b/i.test(String(t && t.name || ''));}).map(function(t){return String(t.name || '');});
          var rawTool = allTools.filter(function(t){return String(t && t.name || '') === 'analytics_get_marketplace_raw_report_rows';})[0] || {};
          cachedTools = 'TOTAL=' + allTools.length + ';NAMES=' + names.join(',').slice(0,460) + ';RAW_ROWS_SCHEMA=' + JSON.stringify(rawTool.inputSchema || {}).slice(0,310);
          if (cachedTools) innerProps.setProperty('SMC_INNER_MCP_TOOL_SCHEMA_V3',cachedTools);
        } else {
          cachedTools = 'TOOLS_LIST_HTTP_' + catalog.http;
        }
      } catch (catalogError) {
        cachedTools = 'TOOLS_LIST_ERROR_' + String(catalogError && catalogError.message ? catalogError.message : catalogError).slice(0,180);
      }
    }
    throw new Error((innerMessage + '|AVAILABLE_TOOL_SCHEMAS=' + (cachedTools || 'unavailable')).slice(0,1100));
  }"""
        worker_text = worker_text.replace(mcp_catalog_anchor, mcp_catalog_replacement, 1)
        print("INNER_MCP_TOOL_SCHEMA=patched")
    elif mcp_catalog_marker not in worker_text and "function sellmonitorGithubInnerMcpCallWithToken_" in worker_text:
        raise SystemExit("Inner MCP tool schema anchor changed")
    # Preserve the upstream MCP error code and a scrubbed, bounded message in
    # the existing per-store log. The previous generic error hid whether the
    # tool was unavailable or the OAuth session needed refresh.
    mcp_error_anchor = "if (item && item.error) throw new Error('INNER_MCP_RPC_ERROR');"
    mcp_error_marker = "INNER_MCP_ERROR_DIAGNOSTIC_V1"
    if mcp_error_anchor in worker_text and mcp_error_marker not in worker_text:
        mcp_error_replacement = """/* INNER_MCP_ERROR_DIAGNOSTIC_V1 */
  if (item && item.error) {
    var rpcError = item.error || {};
    var rpcCode = String(rpcError.code == null ? 'unknown' : rpcError.code).slice(0,40);
    var rpcMessage = String(rpcError.message || 'unspecified')
      .replace(/Bearer\\s+\\S+/ig,'Bearer [redacted]')
      .replace(/\\b(?:access_token|refresh_token|token|secret|api[_ -]?key)\\b[=: ]+[^\\s,;]{1,120}/ig,'[redacted]')
      .slice(0,180);
    throw new Error('INNER_MCP_RPC_ERROR:' + rpcCode + ':' + rpcMessage);
  }"""
        worker_text = worker_text.replace(mcp_error_anchor, mcp_error_replacement, 1)
        print("INNER_MCP_ERROR_DIAGNOSTIC=patched")
    elif mcp_error_marker not in worker_text and "function sellmonitorGithubMcpData_" in worker_text:
        raise SystemExit("Inner MCP error diagnostic anchor changed")
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
    # once the destination columns have a date number format. Normalize date
    # cells both for idempotence checks and for readback verification.
    date_normalize_anchor = "var newRows=[],changedRows=[],archiveRows=[],archived=0,skippedIncomplete=0,verifiedRows=[];"
    date_normalize_marker = "WB_INNER_DATE_READBACK_V2"
    if date_normalize_anchor in worker_text and date_normalize_marker not in worker_text:
        normalizer = """var newRows=[],changedRows=[],archiveRows=[],archived=0,skippedIncomplete=0,verifiedRows=[];
  /* WB_INNER_DATE_READBACK_V2 */
  var targetTimeZone=ss.getSpreadsheetTimeZone();
  var normalizeInnerRow=function(row){return row.map(function(v,i){
    if(v instanceof Date)return (i===1||i===2)?Utilities.formatDate(v,targetTimeZone,'yyyy-MM-dd'):v.toISOString();
    return v==null?'':String(v);
  });};"""
        worker_text = worker_text.replace(date_normalize_anchor, normalizer, 1)
        same_data_anchor = "var sameData=prior.slice(0,keyCol-2).concat(prior.slice(keyCol-1)).join('\\u001f')===r.slice(0,keyCol-2).concat(r.slice(keyCol-1)).join('\\u001f');"
        same_data_replacement = "var sameData=normalizeInnerRow(prior.slice(0,keyCol-2).concat(prior.slice(keyCol-1))).join('\\u001f')===normalizeInnerRow(r.slice(0,keyCol-2).concat(r.slice(keyCol-1))).join('\\u001f');"
        if same_data_anchor not in worker_text:
            raise SystemExit("WB inner idempotence comparison anchor changed")
        worker_text = worker_text.replace(same_data_anchor, same_data_replacement, 1)
        print("WB_INNER_DATE_NORMALIZER=patched")
    elif date_normalize_marker not in worker_text and "function sellmonitorGithubWbInnerUpsert_" in worker_text:
        raise SystemExit("WB inner date normalization anchor changed")

    date_readback_anchor = "if(rb.join('|')!==r.join('|'))throw new Error('WB_INNER_READBACK_MISMATCH:'+sh.getName()+':'+k);"
    if date_readback_anchor in worker_text:
        date_readback_block = "if(normalizeInnerRow(rb).join('|')!==normalizeInnerRow(r).join('|'))throw new Error('WB_INNER_READBACK_MISMATCH:'+sh.getName()+':'+k);"
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

result_replacement = """__INDENT__var result = DOM[job.method]();
__INDENT__if (result === false) {
        var continuationCreated = ScriptApp.getProjectTriggers().some(function(t) {
          return t.getHandlerFunction() === 'exportDOM_continue';
        });
        if (!continuationCreated) {
          throw new Error(job.name + ': вернулся признак продолжения без триггера exportDOM_continue');
        }
__INDENT__  Logger.log(job.name + ': порция завершена; продолжение сохранено, остальные выгрузки отложены.');
__INDENT__  return;
__INDENT__}"""
if "if (result === false)" not in export_block:
    export_block, replaced = re.subn(
        r"(?m)^(?P<indent>[ \t]*)(?:var\s+result\s*=\s*)?DOM\s*\[\s*job\.method\s*\]\s*\(\s*\)\s*;",
        lambda m: result_replacement.replace("__INDENT__", m.group("indent")),
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
