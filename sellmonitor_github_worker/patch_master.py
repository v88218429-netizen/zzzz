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
    throw new Error((innerMessage + '|AVAILABLE_TOOL_SCHEMAS=' + (cachedTools || 'unavailable')).slice(0,1600));
  }"""
        worker_text = worker_text.replace(mcp_catalog_anchor, mcp_catalog_replacement, 1)
        print("INNER_MCP_TOOL_SCHEMA=patched")
    elif mcp_catalog_marker not in worker_text and "function sellmonitorGithubInnerMcpCallWithToken_" in worker_text:
        raise SystemExit("Inner MCP tool schema anchor changed")
    # Refresh the cached Sellmonitor catalog schema in place when the live MCP
    # renamed legacy tools. Keep diagnostics bounded and never log API rows.
    schema_patch_marker = "INNER_MCP_TOOL_SCHEMA_DIAGNOSTIC_V4"
    schema_patch_anchor = "cachedTools = 'TOTAL=' + allTools.length + ';NAMES=' + names.join(',').slice(0,460) + ';RAW_ROWS_SCHEMA=' + JSON.stringify(rawTool.inputSchema || {}).slice(0,310);"
    if schema_patch_marker not in worker_text and schema_patch_anchor in worker_text:
        v4_schema_code = """/* INNER_MCP_TOOL_SCHEMA_DIAGNOSTIC_V4 */
          var compactSchema = function(name) {
            var item = allTools.filter(function(t){return String(t && t.name || '') === name;})[0] || {};
            var schema = item.inputSchema || {};
            var props = schema.properties || {};
            var compact = {required:schema.required || [],properties:{}};
            Object.keys(props).forEach(function(key){
              var p = props[key] || {};
              compact.properties[key] = {type:p.type || '',description:String(p.description || '').slice(0,55)};
              if (Array.isArray(p.enum)) compact.properties[key].enum = p.enum.slice(0,8);
            });
            return JSON.stringify(compact);
          };
          cachedTools = 'TOTAL=' + allTools.length + ';NAMES=' + names.filter(function(n){return /analytics.*(product|finance|order|sales|expense|wb)/i.test(n);}).join(',').slice(0,210) +
            ';PRODUCT_LIST_SCHEMA=' + compactSchema('analytics_get_product_list').slice(0,850) +
            ';WB_EXPENSES_SCHEMA=' + compactSchema('analytics_get_marketplace_expenses').slice(0,500);"""
        if schema_patch_anchor not in worker_text:
            raise SystemExit("Sellmonitor catalog cache anchor changed")
        worker_text = worker_text.replace("SMC_INNER_MCP_TOOL_SCHEMA_V3", "SMC_INNER_MCP_TOOL_SCHEMA_V4")
        worker_text = worker_text.replace(schema_patch_anchor, v4_schema_code, 1)
        print("INNER_MCP_TOOL_SCHEMA_DIAGNOSTIC_V4=patched")
    elif schema_patch_marker not in worker_text and "function sellmonitorGithubInnerMcpCallWithToken_" in worker_text:
        raise SystemExit("Sellmonitor catalog V4 patch anchor changed")

    # Upgrade the live diagnostic to compact required/property keys so the
    # replacement calls can be built from the actual MCP contract.
    schema_keys_marker = "INNER_MCP_TOOL_SCHEMA_DIAGNOSTIC_V5"
    schema_keys_anchor = "          if (cachedTools) innerProps.setProperty('SMC_INNER_MCP_TOOL_SCHEMA_V4',cachedTools);"
    if schema_keys_marker not in worker_text and schema_keys_anchor in worker_text:
        key_schema_code = """/* INNER_MCP_TOOL_SCHEMA_DIAGNOSTIC_V5 */
          var keySchema = function(name) {
            var item = allTools.filter(function(t){return String(t && t.name || '') === name;})[0] || {};
            var schema = item.inputSchema || {};
            var props = schema.properties || {};
            var out = {required:schema.required || [],propertyTypes:{}};
            Object.keys(props).forEach(function(key){
              var p = props[key] || {};
              out.propertyTypes[key] = p.type || '';
              if (Array.isArray(p.enum)) out.propertyTypes[key] += ':' + p.enum.slice(0,6).join(',');
            });
            return JSON.stringify(out);
          };
          cachedTools = 'TOTAL=' + allTools.length + ';PRODUCT_KEYS=' + keySchema('analytics_get_product_list').slice(0,380) +
            ';WB_EXPENSES_KEYS=' + keySchema('analytics_get_marketplace_expenses').slice(0,380);
          if (cachedTools) innerProps.setProperty('SMC_INNER_MCP_TOOL_SCHEMA_V5',cachedTools);"""
        if schema_keys_anchor not in worker_text:
            raise SystemExit("Sellmonitor V5 schema cache anchor changed")
        worker_text = worker_text.replace("SMC_INNER_MCP_TOOL_SCHEMA_V4", "SMC_INNER_MCP_TOOL_SCHEMA_V5")
        worker_text = worker_text.replace(schema_keys_anchor, key_schema_code, 1)
        print("INNER_MCP_TOOL_SCHEMA_DIAGNOSTIC_V5=patched")
    elif schema_keys_marker not in worker_text and "function sellmonitorGithubInnerMcpCallWithToken_" in worker_text:
        raise SystemExit("Sellmonitor V5 schema patch anchor changed")

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
    # Include the exporter state in the publisher's fail-closed response.
    # The GitHub schedule can then diagnose the trigger without direct Sheets API access.
    ozon_diag_marker = "SELLMONITOR_OZON_EXPORT_DIAGNOSTIC_RESPONSE_V1"
    ozon_diag_anchor = "source_status:sourceStatus,source_last_success:sourceLastSuccess||null,"
    if ozon_diag_marker not in worker_text and ozon_diag_anchor in worker_text:
        replacement = ozon_diag_anchor + "\n      /* " + ozon_diag_marker + " */\n      export_diagnostic: (typeof sellmonitorGithubReadOzonExportDiagnostic === 'function' ? sellmonitorGithubReadOzonExportDiagnostic() : null),"
        worker_text = worker_text.replace(ozon_diag_anchor, replacement, 1)
        print("OZON_EXPORT_DIAGNOSTIC_RESPONSE=patched")
    elif ozon_diag_marker not in worker_text and "function sellmonitorGithubPublishOzonSummaryOrders_" in worker_text:
        raise SystemExit("Ozon diagnostic response anchor changed")

    resume_marker = "SELLMONITOR_OZON_CONTINUATION_GITHUB_ACTION_V1"
    resume_anchor = "    if (action === 'seed_wb_tokens') {"
    if resume_marker not in worker_text and resume_anchor in worker_text:
        resume_route = """    /* SELLMONITOR_OZON_CONTINUATION_GITHUB_ACTION_V1 */
    if (action === 'resume_ozon_export') {
      var before = typeof sellmonitorGithubReadOzonExportDiagnostic === 'function' ? sellmonitorGithubReadOzonExportDiagnostic() : null;
      try {
        var continuationResult;
        if (typeof exportDOM_continue === 'function') continuationResult = exportDOM_continue();
        else if (typeof DOM !== 'undefined' && DOM && typeof DOM.exportDOM_continue === 'function') continuationResult = DOM.exportDOM_continue();
        else throw new Error('OZON_CONTINUATION_FUNCTION_MISSING');
        var after = typeof sellmonitorGithubReadOzonExportDiagnostic === 'function' ? sellmonitorGithubReadOzonExportDiagnostic() : null;
        return sellmonitorGithubWebJson_({ok:true,action:action,result:{called:true,return_type:typeof continuationResult,diagnostic_before:before,diagnostic_after:after}});
      } catch (continuationError) {
        var message = String(continuationError && continuationError.message || continuationError)
          .replace(/Bearer\\s+\\S+/ig,'Bearer [redacted]')
          .replace(/\\b(token|secret|api[_ -]?key)\\b[=: ]+\\S+/ig,'$1=[redacted]')
          .replace(/[A-Za-z0-9_-]{32,}/g,'[ID_REDACTED]')
          .slice(0,300);
        return sellmonitorGithubWebJson_({ok:false,action:action,error:'OZON_CONTINUATION_FAILED',message:message,
          result:{diagnostic_before:before,diagnostic_after:typeof sellmonitorGithubReadOzonExportDiagnostic === 'function' ? sellmonitorGithubReadOzonExportDiagnostic() : null}});
      }
    }

"""
        worker_text = worker_text.replace(resume_anchor, resume_route + resume_anchor, 1)
        print("OZON_CONTINUATION_GITHUB_ACTION=patched")
    elif resume_marker not in worker_text and "function doPost(e)" in worker_text:
        raise SystemExit("Ozon continuation action anchor changed")

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


# Report lock contention, and recover only when diagnostics show an orphaned
# continuation with no active job or source progress.
continuation_marker = "OZON_CONTINUATION_RECOVERY_V2"
continuation_start = text.find("function exportDOM_continue() {")
if continuation_start < 0:
    raise SystemExit("DOM continuation wrapper missing")
continuation_end = text.find("\nfunction ", continuation_start + 10)
if continuation_end < 0:
    continuation_end = len(text)
continuation_block = text[continuation_start:continuation_end]
if continuation_marker not in continuation_block:
    lock_anchor = "var lockAcquired = lock.tryLock(1000);"
    busy_anchor = "Logger.log('DOM continuation skipped: script lock is busy.');\n      return;"
    if lock_anchor not in continuation_block or busy_anchor not in continuation_block:
        raise SystemExit("DOM continuation lock anchors changed")
    continuation_block = continuation_block.replace(lock_anchor, "var lockAcquired = lock.tryLock(30000);", 1)
    continuation_block = continuation_block.replace(
        busy_anchor,
        "Logger.log('DOM continuation skipped: script lock is busy.');\n      return {ok:false,skipped:'script_lock_busy'};",
        1,
    )
    active_anchor = re.compile(r"DOM\.init\(\s*SpreadsheetApp\.getActiveSpreadsheet\(\)\s*\);\s*DOM\.exportDOM_continue\(\);")
    active_replacement = """var continuationBook = SpreadsheetApp.getActiveSpreadsheet();
    if (!continuationBook) throw new Error('DOM_CONTINUATION_ACTIVE_BOOK_UNAVAILABLE');
    DOM.init(continuationBook);
    var continuationProperties = PropertiesService.getScriptProperties();
    var continuationJob = String(continuationProperties.getProperty('dom.job') || '').trim();
    var continuationInProgress = String(continuationProperties.getProperty('OZON_FBS_IN_PROGRESS') || '').trim();
    var continuationAutomation = continuationBook.getSheetByName('Автоматизация');
    var continuationHealth = '';
    if (continuationAutomation) {
      var continuationHealthRows = continuationAutomation.getRange(1,1,Math.max(1,continuationAutomation.getLastRow()),4).getDisplayValues();
      for (var healthIndex=0;healthIndex<continuationHealthRows.length;healthIndex++) {
        if (String(continuationHealthRows[healthIndex][0]||'').trim().toLowerCase()==='ozon заказы') {
          continuationHealth=String(continuationHealthRows[healthIndex][3]||'').trim();
          break;
        }
      }
    }
    var continuationHasLiveStatus = /выгрузка продолжается/i.test(continuationHealth);
    var continuationNeedsRecovery = /ошибка|нет успешного запуска|не работает|не запущ/i.test(continuationHealth);
    if (!continuationJob && !continuationInProgress && !continuationHasLiveStatus && continuationNeedsRecovery) {
      ScriptApp.getProjectTriggers().filter(function(trigger){return trigger.getHandlerFunction()==='exportDOM_continue';}).forEach(function(trigger){ScriptApp.deleteTrigger(trigger);});
      if (typeof DOM.exportOzonOrdersToSheet !== 'function') throw new Error('DOM_OZON_ORDERS_METHOD_MISSING');
      var freshExportResult = DOM.exportOzonOrdersToSheet();
      var continuationCreated = ScriptApp.getProjectTriggers().some(function(trigger){return trigger.getHandlerFunction()==='exportDOM_continue';});
      if (freshExportResult === false && !continuationCreated) throw new Error('DOM_OZON_EXPORT_CONTINUATION_MISSING');
      return {ok:true,started_fresh_export:true,continuation_pending:continuationCreated,result_type:typeof freshExportResult};
    }
    var continuationResult = DOM.exportDOM_continue();
    return {ok:true,continued_existing_export:true,result_type:typeof continuationResult,health:continuationHealth};"""
    continuation_block, active_count = active_anchor.subn(active_replacement, continuation_block, count=1)
    if active_count != 1:
        raise SystemExit("DOM continuation invocation anchor changed")
    continuation_block = continuation_block.replace(
        "function exportDOM_continue() {",
        "function exportDOM_continue() {\n  /* " + continuation_marker + " */",
        1,
    )
    text = text[:continuation_start] + continuation_block + text[continuation_end:]
    print("OZON_CONTINUATION_RECOVERY=patched")

diagnostic_marker = "SELLMONITOR_OZON_EXPORT_DIAGNOSTIC_V1"
if diagnostic_marker not in text:
    text += """

    /* SELLMONITOR_OZON_EXPORT_DIAGNOSTIC_V1 */
    function sellmonitorGithubReadOzonExportDiagnostic() {
      var props = PropertiesService.getScriptProperties();
      var handlers = ScriptApp.getProjectTriggers().map(function(t) {
        return String(t.getHandlerFunction() || '');
      });
      var lastError = String(props.getProperty('OZON_FBS_LAST_ERROR') || '');
      try {
        if (typeof sanitizeAutomationError_ === 'function') lastError = sanitizeAutomationError_(lastError);
      } catch (ignored) {}
      lastError = lastError
        .replace(/(token|secret|authorization)\\s*[:=]\\s*\\S+/ig, '$1=[REDACTED]')
        .replace(/[A-Za-z0-9_-]{32,}/g, '[ID_REDACTED]')
        .slice(0, 400);
      return {
        ok: true,
        job: String(props.getProperty('dom.job') || ''),
        in_progress: String(props.getProperty('OZON_FBS_IN_PROGRESS') || ''),
        last_error: lastError,
        last_attempt_at: String(props.getProperty('OZON_FBS_LAST_ATTEMPT_AT') || ''),
        continuation_pending: handlers.indexOf('exportDOM_continue') >= 0
      };
    }
    """
    print("OZON_EXPORT_DIAGNOSTIC_WRAPPER=patched")

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
