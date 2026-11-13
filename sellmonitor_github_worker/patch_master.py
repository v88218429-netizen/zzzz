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


    # Read-only FBS production acceptance audit. Exposes counts and metadata only.
    audit_marker = "SELLMONITOR_GITHUB_FBS_ACCEPTANCE_AUDIT_V1"
    audit_anchor = "function sellmonitorGithubFbsTargetHealthV1_()"
    if audit_marker not in worker_text:
        audit_code = """/* SELLMONITOR_GITHUB_FBS_ACCEPTANCE_AUDIT_V1 */
function sellmonitorGithubFbsAcceptanceAuditV1_(body) {
  var targets=[
    {client:'SANYCH',id:'1v0qt-thJDOw3tLFdZVGhdXJdBM8rDPRz9mHlcHmuuyM'},
    {client:'AIR',id:'1qJvhEOIku7sOMydfrUz0PR5dkcU0sLjv433pa1MFBcM'},
    {client:'HOZYAYUSHKA',id:'1f9dxXeZxkDth8h2C9GA7L7WqAr1-Ok-Oi5YtufLYOOk'}
  ];
  var archiveTargets=[
    {name:'SANYCH_ARCHIVE_LEGACY',id:'1y70iV3KdiN7K_hsQpzv7HCv-lLNgX2hFY6mS-sYAGwI',owner:'1_rMW6w4a7ZKhsWAyVo4B4Hn2x1cXwJWVW7K4JAHpjI8'},
    {name:'AIR_ARCHIVE_OLD',id:'15xXOXkk31QShCzbVGjbtUEkjq0t6Mi1Q1jCuxjGlV4Y',owner:'17G_xCr'},
    {name:'AIR_ARCHIVE_CURRENT',id:'1huZN_N9cx26NX1rB4zacRp2jVlZbwg-5S7CaW8INxfY',owner:'1qJvhEOIku7sOMydfrUz0PR5dkcU0sLjv433pa1MFBcM'},
    {name:'HOZY_ARCHIVE_CURRENT',id:'1ujOWZNzAVny7agS0qJfK-VBLexEgtxbMiA-TsUDN_bE',owner:'1f9dxXeZxkDth8h2C9GA7L7WqAr1-Ok-Oi5YtufLYOOk'},
    {name:'HOZY_ARCHIVE_MASTER',id:'1EpL2t3y9xU0v_oowhAxkYM2YPUUd01hN_Q6bvHJOKSg',owner:'14-_i5xGBA9NiGRHxBOx9rfCe5tjuMoCeBARB60wnVj0'}
  ];
  var requested=String(body&&body.client||'ALL').trim().toUpperCase();
  var allowed=['ALL','ARCHIVES','SANYCH','AIR','HOZYAYUSHKA'];
  if(allowed.indexOf(requested)<0)throw new Error('FBS_AUDIT_CLIENT_NOT_ALLOWED');
  var orderTab='📦 FBS Заказы';
  function dateMs_(v) {
    if(v instanceof Date)return v.getTime();
    if(typeof v==='number')return (v-25569)*86400000;
    if(v){var d=new Date(v);if(!isNaN(d.getTime()))return d.getTime();}
    return NaN;
  }
  function scanOrders_(ss,tabName,withStatuses) {
    var sh=ss.getSheetByName(tabName||orderTab);
    if(!sh)return {exists:false};
    var last=sh.getLastRow();
    if(last>300000||(withStatuses&&last<1))throw new Error('FBS_AUDIT_ROW_COUNT_UNEXPECTED');
    var result={exists:true,lastRow:last,rowsWithId:0,uniqueIds:0,duplicateRows:0,
      invalidIds:0,missingSupplierStatus:0,missingWbStatus:0,missingBothStatuses:0,
      minOrderAt:'',maxOrderAt:'',duplicateRowExamples:[],idSet:Object.create(null)};
    var min=Infinity,max=-Infinity,chunk=4000;
    for(var start=2;start<=last;start+=chunk){
      var n=Math.min(chunk,last-start+1);
      var idDate=sh.getRange(start,1,n,2).getValues();
      var status=withStatuses?sh.getRange(start,5,n,2).getDisplayValues():null;
      for(var i=0;i<n;i++){
        var id=String((idDate[i]||[])[0]||'').trim();
        if(!id)continue;
        if(!/^[0-9]{6,20}$/.test(id)){result.invalidIds++;continue;}
        result.rowsWithId++;
        if(Object.prototype.hasOwnProperty.call(result.idSet,id)){
          result.duplicateRows++;
          if(result.duplicateRowExamples.length<20)result.duplicateRowExamples.push([result.idSet[id],start+i]);
        }else{
          result.idSet[id]=start+i;result.uniqueIds++;
        }
        if(withStatuses){
          var pair=status[i]||[],seller=String(pair[0]||'').trim(),wb=String(pair[1]||'').trim();
          if(!seller)result.missingSupplierStatus++;
          if(!wb)result.missingWbStatus++;
          if(!seller&&!wb)result.missingBothStatuses++;
        }
        var stamp=dateMs_((idDate[i]||[])[1]);
        if(isFinite(stamp)){if(stamp<min)min=stamp;if(stamp>max)max=stamp;}
      }
    }
    if(isFinite(min))result.minOrderAt=Utilities.formatDate(new Date(min),'Europe/Moscow','yyyy-MM-dd HH:mm:ss');
    if(isFinite(max))result.maxOrderAt=Utilities.formatDate(new Date(max),'Europe/Moscow','yyyy-MM-dd HH:mm:ss');
    return result;
  }
  function auditAnalytics_(ss) {
    var names=['📊 Дашборд','💼 Собственник','📋 Отчёт FBS','📋 FBS Report',
      '📈 Аналитика','📈 Аналитика 360','🧩 Товарный портфель','💰 Скорость = деньги',
      '🧠 Центр решений','🧮 Хронология','🛠 Charts Data'];
    var out=[];
    names.forEach(function(name){
      var sh=ss.getSheetByName(name);if(!sh)return;
      var last=sh.getLastRow(),cols=sh.getLastColumn(),scanRows=Math.min(last,3000),scanCols=Math.min(cols,80);
      var formulas=0,errors=0,examples=[];
      for(var start=1;start<=scanRows;start+=300){
        var n=Math.min(300,scanRows-start+1);
        var fs=sh.getRange(start,1,n,Math.max(1,scanCols)).getFormulas();
        var values=sh.getRange(start,1,n,Math.max(1,scanCols)).getDisplayValues();
        for(var r=0;r<n;r++)for(var c=0;c<scanCols;c++){
          if(fs[r][c])formulas++;
          var v=String(values[r][c]||'');
          if(/^#(?:REF!|N\/A|VALUE!|DIV\/0!|NAME\?|ERROR!|NUM!|NULL!)/i.test(v)){
            errors++;if(examples.length<12)examples.push(sh.getName()+'!'+sh.getRange(start+r,c+1).getA1Notation()+':'+v);
          }
        }
      }
      var charts=sh.getCharts(),chartRows=[];
      charts.forEach(function(ch){
        var title='',ranges=[];
        try{title=String(ch.getOptions().get('title')||'');}catch(ignoreTitle){}
        try{ranges=ch.getRanges().map(function(r){return r.getA1Notation();});}catch(ignoreRanges){}
        var chartType='UNKNOWN';
        try{
          if(ch&&typeof ch.getChartType==='function')chartType=String(ch.getChartType());
        }catch(chartTypeError){
          chartType='ERROR:'+String(chartTypeError&&chartTypeError.message||chartTypeError).slice(0,120);
        }
        if(chartType==='UNKNOWN'){
          try{
            var chartBuilder=ch&&typeof ch.modify==='function'?ch.modify():null;
            if(chartBuilder&&typeof chartBuilder.getChartType==='function')chartType=String(chartBuilder.getChartType());
          }catch(chartBuilderError){
            chartType='ERROR:'+String(chartBuilderError&&chartBuilderError.message||chartBuilderError).slice(0,120);
          }
        }
        chartRows.push({title:title,type:chartType,ranges:ranges});
      });
      out.push({sheet:name,lastRow:last,lastColumn:cols,formulaCellsScanned:formulas,
        scanRows:scanRows,scanColumns:scanCols,scanTruncated:last>scanRows||cols>scanCols,
        formulaErrorCount:errors,formulaErrorExamples:examples,chartCount:charts.length,charts:chartRows});
    });
    return out;
  }
  function auditSupplies_(ss) {
    var sh=ss.getSheetByName('🚚 Поставки FBS');if(!sh)return {exists:false};
    var last=sh.getLastRow(),rows=0,scanned=0,done=0;
    if(last>200000)throw new Error('FBS_AUDIT_SUPPLY_ROW_COUNT_UNEXPECTED');
    for(var start=2;start<=last;start+=4000){
      var n=Math.min(4000,last-start+1),v=sh.getRange(start,1,n,5).getValues();
      for(var i=0;i<n;i++){
        var id=String(v[i][0]||'').trim();if(!id)continue;
        rows++;if(v[i][3])scanned++;if(v[i][4]===true||String(v[i][4]).toLowerCase()==='true')done++;
      }
    }
    return {exists:true,lastRow:last,supplyRows:rows,scanDtPresent:scanned,scanDtMissing:Math.max(0,rows-scanned),done:done};
  }
  function auditCharts_(ss) {
    var out={total:0,sheets:{}};
    ss.getSheets().forEach(function(sh){var charts=sh.getCharts();if(charts.length){out.total+=charts.length;out.sheets[sh.getName()]=charts.length;}});
    return out;
  }
  var result={ok:true,checkedAt:new Date().toISOString(),timezone:'Europe/Moscow',clients:{},archives:[]};
  var mainByClient={};
  targets.forEach(function(t){
    if(requested!=='ALL'&&requested!==t.client)return;
    try{
      var ss=SpreadsheetApp.openById(t.id),orders=scanOrders_(ss,orderTab,true);
      mainByClient[t.client]=orders.idSet||{};
      result.clients[t.client]={spreadsheetId:t.id,accessible:true,orders:orders,
        supply:auditSupplies_(ss),analytics:auditAnalytics_(ss),charts:auditCharts_(ss)};
      delete result.clients[t.client].orders.idSet;
    }catch(e){result.ok=false;result.clients[t.client]={spreadsheetId:t.id,accessible:false,errorType:String(e&&e.name||'Error'),reason:String(e&&e.message||e).slice(0,180)};}
  });
  if(requested==='ALL'||requested==='SANYCH'){
    try{
      var alt=SpreadsheetApp.openById('1_rMW6w4a7ZKhsWAyVo4B4Hn2x1cXwJWVW7K4JAHpjI8');
      var altOrders=scanOrders_(alt,orderTab,true),main=mainByClient.SANYCH||{};
      var onlyMain=0,onlyAlt=0;
      Object.keys(main).forEach(function(id){if(!Object.prototype.hasOwnProperty.call(altOrders.idSet,id))onlyMain++;});
      Object.keys(altOrders.idSet).forEach(function(id){if(!Object.prototype.hasOwnProperty.call(main,id))onlyAlt++;});
      result.clients.SANYCH.alternate={spreadsheetId:'1_rMW6w4a7ZKhsWAyVo4B4Hn2x1cXwJWVW7K4JAHpjI8',
        accessible:true,orders:{rowsWithId:altOrders.rowsWithId,uniqueIds:altOrders.uniqueIds,
          duplicateRows:altOrders.duplicateRows,invalidIds:altOrders.invalidIds,
          missingSupplierStatus:altOrders.missingSupplierStatus,missingWbStatus:altOrders.missingWbStatus,
          missingBothStatuses:altOrders.missingBothStatuses,minOrderAt:altOrders.minOrderAt,maxOrderAt:altOrders.maxOrderAt},
        onlyInMain:onlyMain,onlyInAlternate:onlyAlt};
    }catch(e){if(result.clients.SANYCH)result.clients.SANYCH.alternate={accessible:false,errorType:String(e&&e.name||'Error'),reason:String(e&&e.message||e).slice(0,160)};}
  }
  if(requested==='ALL'||requested==='ARCHIVES'){
    archiveTargets.forEach(function(a){
      var item={name:a.name,spreadsheetId:a.id,expectedOwnerTarget:a.owner,accessible:false};
      try{
        var ss=SpreadsheetApp.openById(a.id),meta=ss.getSheetByName('SYS_ARCHIVE_META'),metaRows=meta?meta.getDataRange().getDisplayValues():[];
        var owner='';
        metaRows.forEach(function(row){if(String(row[0]||'').trim()==='OWNER_TARGET_ID')owner=String(row[1]||'').trim();});
        var orders=scanOrders_(ss,'📦 Архив заказов',false),history=ss.getSheetByName('🕘 Архив истории статусов');
        item.accessible=true;item.ownerTargetId=owner;item.ownerTargetMatches=owner.indexOf(a.owner)===0;
        item.archiveOrders={rowsWithId:orders.rowsWithId,uniqueIds:orders.uniqueIds,duplicateRows:orders.duplicateRows};
        item.historyRows=history?Math.max(0,history.getLastRow()-1):0;
      }catch(e){item.errorType=String(e&&e.name||'Error');item.reason=String(e&&e.message||e).slice(0,150);}
      result.archives.push(item);
    });
  }
  return result;
}
"""
        if audit_anchor not in worker_text:
            raise SystemExit("FBS audit insertion anchor missing")
        worker_text = worker_text.replace(audit_anchor, audit_code + "\n" + audit_anchor, 1)
        print("FBS_ACCEPTANCE_AUDIT_FUNCTION=patched")
    old_chart_audit = "        chartRows.push({title:title,type:String(ch.getChartType()),ranges:ranges});"
    if old_chart_audit in worker_text:
        chart_audit_patch = """        var chartType='UNKNOWN';
        try{
          if(ch&&typeof ch.getChartType==='function')chartType=String(ch.getChartType());
        }catch(chartTypeError){
          chartType='ERROR:'+String(chartTypeError&&chartTypeError.message||chartTypeError).slice(0,120);
        }
        if(chartType==='UNKNOWN'){
          try{
            var chartBuilder=ch&&typeof ch.modify==='function'?ch.modify():null;
            if(chartBuilder&&typeof chartBuilder.getChartType==='function')chartType=String(chartBuilder.getChartType());
          }catch(chartBuilderError){
            chartType='ERROR:'+String(chartBuilderError&&chartBuilderError.message||chartBuilderError).slice(0,120);
          }
        }
        chartRows.push({title:title,type:chartType,ranges:ranges});"""
        worker_text = worker_text.replace(old_chart_audit, chart_audit_patch, 1)
        print("FBS_CHART_AUDIT_FALLBACK=patched")
    elif "chartRows.push({title:title,type:chartType,ranges:ranges});" in worker_text:
        print("FBS_CHART_AUDIT_FALLBACK=already_present")
    else:
        raise SystemExit("FBS chart audit patch anchor missing")
    audit_route_marker = "FBS_ACCEPTANCE_AUDIT_ROUTE_V1"
    post_anchor = "    sellmonitorGithubWebAuth_(body.token || body.key);"
    if audit_route_marker not in worker_text:
        route = """    /* FBS_ACCEPTANCE_AUDIT_ROUTE_V1 */
    if (action === 'fbs_acceptance_audit') {
      var auditResult = sellmonitorGithubFbsAcceptanceAuditV1_(body);
      return sellmonitorGithubWebJson_({ok:auditResult.ok===true,action:action,result:auditResult});
    }
"""
        if post_anchor not in worker_text:
            raise SystemExit("FBS audit auth route anchor missing")
        worker_text = worker_text.replace(post_anchor, post_anchor + "\n" + route, 1)
        print("FBS_ACCEPTANCE_AUDIT_ROUTE=patched")

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

    # Build store-level RNP periods from the same exact daily Sellmonitor facts used by 01_Дни.
    # This keeps weekly/monthly shop totals available when SKU-level period coverage is partial.
    wb_store_aggregate_marker = "WB_RNP_STORE_EXACT_DAILY_AGG_V1"
    if wb_store_aggregate_marker not in worker_text:
        helper_anchor = "  var exactMax='';"
        exact_old = """  var exactMax='';
  built.day.forEach(function(r){var dayKey=String(r[1]||'');if(onlyPeriodStart&&(dayKey<onlyPeriodStart||dayKey>onlyPeriodEnd))return;if(onlyPeriodPrefix&&dayKey.indexOf(onlyPeriodPrefix)!==0)return;if(String(r[10+built.metricHeaders.length]||'')==='FACTUAL_INNER_ANALYTICS'&&dayKey>exactMax)exactMax=dayKey;});"""
        exact_new = """  /* WB_RNP_EXACT_DAY_REQUIRES_ALL_STORE_ROWS_V1 */
  var exactMax='';
  Object.keys(storeDayGroups_).forEach(function(dayKey){
    if(onlyPeriodStart&&(dayKey<onlyPeriodStart||dayKey>onlyPeriodEnd))return;
    if(onlyPeriodPrefix&&dayKey.indexOf(onlyPeriodPrefix)!==0)return;
    if(storeDayGroups_[dayKey].exact&&dayKey>exactMax)exactMax=dayKey;
  });"""
        status_old = """      next[0]=pstat.preliminary?(pstat.exact?'🟡 ЧАСТИЧНО':'🟡 ПРЕДВ.'):pstat.partialCoverage?(pstat.exact?'🟡 ЧАСТИЧНО':'🟠 НЕПОЛНЫЙ'):(pstat.other?'🟠 НЕПОЛНЫЙ':'🟢 ФАКТ');"""
        status_new = """      next[0]=pstat.preliminary?(pstat.exact?'🟡 ЧАСТИЧНО':'🟡 ПРЕДВ.'):pstat.partialCoverage?(pstat.exact?'🟡 ЧАСТИЧНО':'🟠 НЕПОЛНЫЙ'):(pstat.other?'🟠 НЕПОЛНЫЙ':'🟢 ФАКТ');
      /* WB_RNP_STORE_STATUS_FROM_EXACT_DAYS_V1 */
      var storePeriodFacts=storePeriodFacts_(period,spec.grain);
      next[0]=storePeriodFacts.status;"""
        store_branch_old = """        if(level==='МАГАЗИН'){
          if(!Object.prototype.hasOwnProperty.call(storeValueCache,code))storeValueCache[code]=rnpValue(all,code);
          v=storeValueCache[code];"""
        store_branch_new = """        if(level==='МАГАЗИН'){
          /* WB_RNP_STORE_VALUE_FROM_EXACT_DAYS_V1 */
          if(!Object.prototype.hasOwnProperty.call(storeValueCache,code)){
            storeValueCache[code]=storeAggregateCode_(code)?storeAggregateValue_(storePeriodFacts.rows,code):rnpValue(all,code);
          }
          v=storeValueCache[code];"""
        for label, anchor in (
            ("helper", helper_anchor),
            ("exact-day", exact_old),
            ("status", status_old),
            ("store-value", store_branch_old),
        ):
            if worker_text.count(anchor) != 1:
                raise SystemExit("WB exact-day store aggregate anchor mismatch: " + label)
        worker_text = worker_text.replace(helper_anchor, """  /* WB_RNP_STORE_EXACT_DAILY_AGG_V1 */
  var storeAggregateDirectFields_={
    fin_Profit:'profit_rub',pcs_ordersCount:'orders_qty',pcs_ordersSumRub:'orders_revenue_rub',
    fin_sales_total_sum:'revenue_rub',fin_sales_total_qnt:'net_sales_qty',newfin_sales_qnt:'net_sales_qty',
    newfin_retail_price_withdisc:'revenue_rub',newfin_return_qnt:'returns_qty',
    newfin_retail_price_withdisc_return:'returns_revenue_rub',expAdCosts:'ad_rub',
    fin_Costs:'expenses_total_rub',fin_purchase:'prime_cost_rub',fin_comis:'commission_rub',
    fin_logistics:'logistic_rub',storage_warehousePrice:'storage_rub',acceptance_total:'paid_acceptance_rub',
    newfin_penalty:'fines_rub',fin_taxpay:'tax_rub',newfin_deductions:'deduction_rub',
    newfin_delivery_sum:'delivery_logistic_rub',newfin_delivery_return_sum:'return_logistic_rub'
  };
  var storeAggregateRatioFields_={
    fin_margin:['profit_rub','revenue_rub'],fin_roi:['profit_rub','expenses_total_rub'],
    reductOrdToSale:['orders_qty','net_sales_qty'],fin_avgBillSPP:['customer_revenue_rub','net_sales_qty'],
    fin_avgBillnoSPP:['revenue_rub','net_sales_qty'],fin_SPP_Rate:['marketplace_discount_rub','revenue_rub'],
    drr_orders:['ad_rub','orders_revenue_rub'],fin_DRR:['ad_rub','revenue_rub'],
    expCostsByUnit:['expenses_total_rub','net_sales_qty'],ext_purchase_unit:['prime_cost_rub','net_sales_qty'],
    fin_logisticsByUnit:['logistic_rub','net_sales_qty'],fin_storagesByUnit:['storage_rub','net_sales_qty'],
    cr_sale_order:['paid_order_qty','orders_qty']
  };
  var storeDayGroups_=Object.create(null),storeSourceMin_='',storePeriodCache_=Object.create(null);
  var storeTrustIx_=10+built.metricHeaders.length;
  built.day.forEach(function(r){
    var day=key(r[1]);
    if(!day||String(r[5]||'')!==String(storeId))return;
    var g=storeDayGroups_[day]||(storeDayGroups_[day]={rows:[],exact:true});
    g.rows.push(r);
    if(String(r[storeTrustIx_]||'')!=='FACTUAL_INNER_ANALYTICS')g.exact=false;
    if(!storeSourceMin_||day<storeSourceMin_)storeSourceMin_=day;
  });
  function storeAddDay_(s,n){
    var p=s.split('-').map(Number),d=new Date(Date.UTC(p[0],p[1]-1,p[2]+n,12,0,0));
    return Utilities.formatDate(d,'UTC','yyyy-MM-dd');
  }
  function storePeriodFacts_(period,grain){
    var cacheKey=grain+'|'+period;
    if(storePeriodCache_[cacheKey])return storePeriodCache_[cacheKey];
    var start=period,end=period;
    if(grain==='week')end=storeAddDay_(period,6);
    else if(grain==='month'){
      var p=period.split('-').map(Number);
      end=Utilities.formatDate(new Date(Date.UTC(p[0],p[1],0,12,0,0)),'UTC','yyyy-MM-dd');
    }
    var today=Utilities.formatDate(new Date(),ss.getSpreadsheetTimeZone(),'yyyy-MM-dd');
    var closedThrough=storeAddDay_(today,-1);
    var first=storeSourceMin_&&storeSourceMin_>start?storeSourceMin_:start;
    var last=end<closedThrough?end:closedThrough;
    var expectedDays=0,exactDays=[],rows=[];
    if(first<=last){
      for(var d=first;d<=last;d=storeAddDay_(d,1)){
        expectedDays++;
        var daily=storeDayGroups_[d];
        if(daily&&daily.exact){exactDays.push(d);rows=rows.concat(daily.rows);}
        if(expectedDays>400)throw new Error('WB_RNP_STORE_PERIOD_GUARD:'+grain+':'+period);
      }
    }
    var periodOpen=end>closedThrough;
    var status=expectedDays===0?'🟡 ПРЕДВ.':(exactDays.length===expectedDays?(periodOpen?'🟡 ЧАСТИЧНО':'🟢 ФАКТ'):(exactDays.length?'🟡 ЧАСТИЧНО':'🟠 НЕПОЛНЫЙ'));
    var result={rows:rows,exact_days:exactDays.length,expected_days:expectedDays,status:status};
    storePeriodCache_[cacheKey]=result;
    return result;
  }
  function storeStrictSum_(rows,field){
    var idx=metricIx[field];
    if(idx==null||!rows||!rows.length)return null;
    var salesIx=metricIx.net_sales_qty,returnsIx=metricIx.returns_qty,total=0;
    for(var i=0;i<rows.length;i++){
      var raw=rows[i][idx];
      if(raw===''||raw==null||typeof raw==='boolean'||!isFinite(Number(raw))){
        var sold=salesIx==null?null:Number(rows[i][salesIx]);
        var returned=returnsIx==null?null:Number(rows[i][returnsIx]);
        if((field==='revenue_rub'||field==='customer_revenue_rub'||field==='marketplace_discount_rub')&&sold===0&&returned===0)raw=0;
        else if(field==='returns_revenue_rub'&&returned===0)raw=0;
        else return null;
      }
      total+=Number(raw);
    }
    return total;
  }
  function storeAggregateCode_(code){
    return Object.prototype.hasOwnProperty.call(storeAggregateDirectFields_,String(code||''))||
      Object.prototype.hasOwnProperty.call(storeAggregateRatioFields_,String(code||''));
  }
  function storeAggregateValue_(rows,code){
    code=String(code||'');
    var direct=storeAggregateDirectFields_[code];
    if(direct){var total=storeStrictSum_(rows,direct);return total==null?'':total;}
    var ratio=storeAggregateRatioFields_[code];
    if(!ratio)return '';
    var n=storeStrictSum_(rows,ratio[0]),d=storeStrictSum_(rows,ratio[1]);
    return n==null||d==null||d===0?'':n/d;
  }""" + "\n" + helper_anchor, 1)
        worker_text = worker_text.replace(exact_old, exact_new, 1)
        worker_text = worker_text.replace(status_old, status_new, 1)
        # Leave the store branch for the later coverage patch to wrap.
        print("WB_RNP_STORE_EXACT_DAILY_AGG=patched")
    elif not all(marker in worker_text for marker in (
        "WB_RNP_EXACT_DAY_REQUIRES_ALL_STORE_ROWS_V1",
        "WB_RNP_STORE_STATUS_FROM_EXACT_DAYS_V1",
        "WB_RNP_STORE_VALUE_FROM_EXACT_DAYS_V1",
    )):
        raise SystemExit("WB exact-day store aggregate patch incomplete")

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

    # Single authorized continuation invocation requested after bounded source diagnostics.
    result_marker = "SELLMONITOR_OZON_CONTINUATION_RESULT_V1"
    result_anchor = "result:{called:true,return_type:typeof continuationResult,diagnostic_before:before,diagnostic_after:after}"
    if result_marker not in worker_text and result_anchor in worker_text:
        result_replacement = "result:{called:true,return_type:typeof continuationResult,continuation_result:(continuationResult && typeof continuationResult === 'object' ? continuationResult : null),diagnostic_before:before,diagnostic_after:after}"
        worker_text = worker_text.replace(result_anchor, result_replacement, 1)
        worker_text = worker_text.replace("/* SELLMONITOR_OZON_CONTINUATION_GITHUB_ACTION_V1 */", "/* SELLMONITOR_OZON_CONTINUATION_GITHUB_ACTION_V1 */\n    /* " + result_marker + " */", 1)
        print("OZON_CONTINUATION_RESULT=patched")
    elif result_marker not in worker_text and "SELLMONITOR_OZON_CONTINUATION_GITHUB_ACTION_V1" in worker_text:
        raise SystemExit("Ozon continuation result anchor changed")
    elif resume_marker not in worker_text and "function doPost(e)" in worker_text:
        raise SystemExit("Ozon continuation action anchor changed")

  
    finance_marker = "SELLMONITOR_OZON_FINANCE_DAY_LAYER_V1"
    if finance_marker not in worker_text:
        route_anchor = "    if (action === 'publish_ozon_api_orders') {"
        route = """    /* SELLMONITOR_OZON_FINANCE_DAY_LAYER_V1 */
    if (action === 'publish_ozon_api_finance') {
      return sellmonitorGithubWebJson_(sellmonitorGithubPublishOzonFinance_(body));
    }

"""
        if worker_text.count(route_anchor) != 1:
            raise SystemExit("OZON_FINANCE_ROUTE_ANCHOR_MISMATCH")
        worker_text = worker_text.replace(route_anchor, route + route_anchor, 1)
        function_anchor = "function sellmonitorGithubPublishOzonSummaryOrders_(payload) {"
        finance_function = r"""/* SELLMONITOR_OZON_FINANCE_DAY_LAYER_V1 */
function sellmonitorGithubPublishOzonFinance_(payload) {
  /* OZON_FINANCE_INDEPENDENT_ACCOUNT_OTHER_CATEGORY_V1 */
  if (!payload || payload.source !== 'ozon_seller_api_accrual' || payload.complete !== true) throw new Error('OZON_FINANCE_EXPORT_INCOMPLETE_PRESERVE');
  var accounts=(payload.completed_accounts||[]).map(String).sort();
  if (!accounts.length || accounts.some(function(c,i){return ['1','2'].indexOf(c)<0 || accounts.indexOf(c)!==i;})) throw new Error('OZON_FINANCE_ACCOUNT_COVERAGE_INCOMPLETE_PRESERVE');
  var from=String(payload.coverage_from||''),to=String(payload.coverage_to||''),exported=String(payload.exported_at||'');
  if (!/^\d{4}-\d{2}-\d{2}$/.test(from)||!/^\d{4}-\d{2}-\d{2}$/.test(to)||from>'2026-09-01'||to<from||!isFinite(new Date(exported).getTime())) throw new Error('OZON_FINANCE_COVERAGE_INVALID');
  var dailyIn=payload.daily;
  if (!Array.isArray(dailyIn)||dailyIn.length<2) throw new Error('OZON_FINANCE_DAILY_ROWS_MISSING');
  function addDay_(s,n){var p=s.split('-').map(Number),d=new Date(Date.UTC(p[0],p[1]-1,p[2]+n,12));return Utilities.formatDate(d,'UTC','yyyy-MM-dd');}
  var dates=[],date=from;
  while(date<=to){dates.push(date);date=addDay_(date,1);if(dates.length>400)throw new Error('OZON_FINANCE_PERIOD_GUARD');}
  var today=Utilities.formatDate(new Date(),SMC_OZON_SUMMARY.timezone,'yyyy-MM-dd');
  var byKey=Object.create(null),allowedCats={ITEM:1,POSTING:1,NON_ITEM:1,OTHER:1};
  dailyIn.forEach(function(r,i){
    var cabinet=String(r.cabinet||''),day=String(r.day||'');
    if(!SMC_OZON_SUMMARY.stores[cabinet]||dates.indexOf(day)<0)throw new Error('OZON_FINANCE_INVALID_KEY:'+String(i+1));
    var key=cabinet+'|'+day;if(byKey[key])throw new Error('OZON_FINANCE_DUPLICATE_DAY:'+cabinet+':'+day);
    var count=Number(r.rows);
    if(!isFinite(count)||count<0||Math.floor(count)!==count)throw new Error('OZON_FINANCE_INVALID_ROW_COUNT:'+cabinet+':'+day);
    function money_(v,name){
      var n=Number(v);
      if(v===null||v===''||!isFinite(n))throw new Error('OZON_FINANCE_INVALID_AMOUNT:'+name+':'+cabinet+':'+day);
      return Math.round(n*100)/100;
    }
    byKey[key]={cabinet:cabinet,day:day,rows:count,total:money_(r.total,'total'),item:money_(r.item,'item'),posting:money_(r.posting,'posting'),nonItem:money_(r.non_item,'non_item'),other:money_(r.other,'other'),unknownCategories:Number(r.unknown_categories||0)};
    if(!isFinite(byKey[key].unknownCategories)||byKey[key].unknownCategories<0)throw new Error('OZON_FINANCE_UNKNOWN_CATEGORY_COUNT_INVALID:'+cabinet+':'+day);
  });
  if(dailyIn.length!==dates.length*accounts.length)throw new Error('OZON_FINANCE_DAY_COUNT_MISMATCH:'+dailyIn.length+':'+(dates.length*accounts.length));
  dates.forEach(function(d){accounts.forEach(function(c){if(!byKey[c+'|'+d])throw new Error('OZON_FINANCE_DAY_MISSING:'+c+':'+d);});});
  function round_(n){return Math.round(n*100)/100;}
  function buildStore_(cabinet,cfg){
    var dayRows=[['Дата','Начислено всего, ₽','Товар (ITEM), ₽','Отправления (POSTING), ₽','Нетоварное (NON_ITEM), ₽','Прочие категории, ₽','Начислений, шт.','Источник','Состояние','Время выгрузки']];
    var storeDays=dates.map(function(d){
      var r=byKey[cabinet+'|'+d],state=d===today?'Предварительно · текущий день':(r.rows?'Факт API · начисления получены':'Факт API · начислений нет');
      return [d,r.total,r.item,r.posting,r.nonItem,r.other,r.rows,'Ozon Seller API · accrual/by-day',state,exported];
    });
    dayRows=dayRows.concat(storeDays);
    var periodRows=[['Период','Начало','Конец','Начислено всего, ₽','Товар (ITEM), ₽','Отправления (POSTING), ₽','Нетоварное (NON_ITEM), ₽','Прочие категории, ₽','Дней покрыто','Дней в календарном периоде','Состояние']];
    function aggregate_(grain){
      var map=Object.create(null);
      dates.forEach(function(d){
        var p=d.split('-').map(Number),dt=new Date(Date.UTC(p[0],p[1]-1,p[2],12)),start,end,label;
        if(grain==='Неделя'){
          var shift=(dt.getUTCDay()+6)%7;start=addDay_(d,-shift);end=addDay_(start,6);label='Неделя';
        }else{
          start=d.slice(0,7)+'-01';var next=new Date(Date.UTC(p[0],p[1],1,12));end=Utilities.formatDate(new Date(next.getTime()-86400000),'UTC','yyyy-MM-dd');label='Месяц';
        }
        var k=label+'|'+start;
        if(!map[k])map[k]={type:label,start:start,end:end,total:0,item:0,posting:0,nonItem:0,other:0,days:0};
        var a=map[k],r=byKey[cabinet+'|'+d];a.total+=r.total;a.item+=r.item;a.posting+=r.posting;a.nonItem+=r.nonItem;a.other+=r.other;a.days++;
      });
      return Object.keys(map).sort().map(function(k){
        var a=map[k],calDays=datesBetween_(a.start,a.end),fullyCovered=a.start>=from&&a.end<=to,completed=a.end<today;
        var currentPartial=a.start<=today&&a.end>=today;var state=!fullyCovered?(currentPartial?'Предварительно · текущий период; данные по '+to:'Неполный диапазон источника'):(!completed?'Предварительно · незавершённый период':'Полный календарный период · снимок API');
        return [a.type,a.start,a.end,round_(a.total),round_(a.item),round_(a.posting),round_(a.nonItem),round_(a.other),a.days,calDays,state];
      });
    }
    function datesBetween_(a,b){var n=0,d=a;while(d<=b&&n<400){n++;d=addDay_(d,1);}return n;}
    periodRows=periodRows.concat(aggregate_('Неделя'),aggregate_('Месяц'));
    var ss=SpreadsheetApp.openById(cfg.id),archive=ss.getSheetByName('13_API_Заказы_Архив');
    if(!archive){archive=ss.insertSheet('13_API_Заказы_Архив');archive.getRange(1,1,1,7).setValues([['Архив ID','Время','Лист','Диапазон','Строка в диапазоне','Время источника','Старое значение JSON']]);archive.setFrozenRows(1);}
    var archiveId='FIN_'+cabinet+'_'+exported,archiveRows=0;
    function replace_(name,rows,width){
      var sh=ss.getSheetByName(name);if(!sh)sh=ss.insertSheet(name);
      var oldLast=sh.getLastRow(),oldWidth=Math.max(width,sh.getLastColumn()),unchanged=false;
      if(oldLast===rows.length&&oldWidth>=width){
        var existing=sh.getRange(1,1,oldLast,width).getValues(),compareWidth=String(rows[0][width-1]||'')==='Время выгрузки'?width-1:width;
        unchanged=true;
        for(var er=0;er<rows.length&&unchanged;er++)for(var ec=0;ec<compareWidth;ec++)if(String(existing[er][ec]||'')!==String(rows[er][ec]||'')){unchanged=false;break;}
        if(unchanged){
          var stableCheck=sh.getRange(1,1,rows.length,width).getValues();
          return {rows:rows.length-1,sha256:sellmonitorGithubOzonHash_(stableCheck),readback:true,unchanged:true};
        }
      }
      if(oldLast>0){
        var old=sh.getRange(1,1,oldLast,oldWidth).getValues();
        archiveRows+=sellmonitorGithubOzonArchive_(archive,name,'A1:'+oldWidth+':'+oldLast,old,exported,archiveId);
      }
      sh.clearContents();
      if(rows.length>1){
        if(name==='18_API_Финансы_дни')sh.getRange(2,1,rows.length-1,1).setNumberFormat('@');
        else if(name==='19_API_Финансы_периоды')sh.getRange(2,2,rows.length-1,2).setNumberFormat('@');
      }
      sh.getRange(1,1,rows.length,width).setValues(rows);
      sh.setFrozenRows(1);sh.setHiddenGridlines(true);
      sh.getRange(1,1,1,width).setBackground('#17365D').setFontColor('#FFFFFF').setFontWeight('bold').setWrap(true);
      if(rows.length>1){
        var moneyStart=name==='19_API_Финансы_периоды'?4:2;
        var moneyWidth=name==='19_API_Финансы_периоды'?5:5;
        sh.getRange(2,moneyStart,rows.length-1,moneyWidth).setNumberFormat('#,##0.00;[Red]-#,##0.00;–');
        if(name==='19_API_Финансы_периоды')sh.getRange(2,2,rows.length-1,2).setNumberFormat('@');
        sh.getRange(2,1,rows.length-1,1).setNumberFormat('@');
        sh.getRange(2,width,rows.length-1,1).setNumberFormat('@');
      }
      var check=sh.getRange(1,1,rows.length,width).getValues();
      if(JSON.stringify(check)!==JSON.stringify(rows))throw new Error('OZON_FINANCE_READBACK_MISMATCH:'+name);
      return {rows:rows.length-1,sha256:sellmonitorGithubOzonHash_(check),readback:true};
    }
    var dayResult=replace_('18_API_Финансы_дни',dayRows,10);
    var periodResult=replace_('19_API_Финансы_периоды',periodRows,11);
    var journal=ss.getSheetByName('20_API_Финансы_журнал');if(!journal)journal=ss.insertSheet('20_API_Финансы_журнал');
    if(journal.getLastRow()===0)journal.getRange(1,1,1,11).setValues([['Время выгрузки','Магазин','Источник','Период с','Период по','Дней получено','Начислений получено','Дневных строк','Периодов','SHA-256','Итог чтения']]).setFontWeight('bold').setBackground('#17365D').setFontColor('#FFFFFF');
    var row=[exported,cfg.name,'Ozon Seller API · accrual/by-day',from,to,dates.length,storeDays.reduce(function(n,r){return n+Number(r[6]||0);},0),dayResult.rows,periodResult.rows,dayResult.sha256+' / '+periodResult.sha256,'OK: запись перечитана; пустой день отделён от сбоя'];
    var at=journal.getLastRow()+1;sellmonitorGithubOzonEnsureRows_(journal,at);
    journal.getRange(at,4,1,2).setNumberFormat('yyyy-mm-dd');
    journal.getRange(at,1,1,row.length).setValues([row]);
    var check=journal.getRange(at,1,1,row.length).getValues()[0],normalizedCheck=check.slice();
    [3,4].forEach(function(ci){if(normalizedCheck[ci] instanceof Date)normalizedCheck[ci]=Utilities.formatDate(normalizedCheck[ci],'Etc/GMT','yyyy-MM-dd');});
    if(JSON.stringify(normalizedCheck)!==JSON.stringify(row))throw new Error('OZON_FINANCE_JOURNAL_READBACK_FAILED:'+cfg.name);
    return {store:cfg.name,days:dayResult.rows,periods:periodResult.rows,accrual_rows:row[6],archived_rows:archiveRows,daily_sha256:dayResult.sha256,period_sha256:periodResult.sha256,readback:true};
  }
  var results=[];
  accounts.forEach(function(c){try{results.push(buildStore_(c,SMC_OZON_SUMMARY.stores[c]));}catch(e){results.push({store:SMC_OZON_SUMMARY.stores[c].name,ok:false,error:String(e&&e.message||e).slice(0,800)});}});
  var failed=results.filter(function(x){return x.ok===false;});
  if(failed.length)return {ok:false,action:'publish_ozon_api_finance',stores:results};
  return {ok:true,action:'publish_ozon_api_finance',coverage_from:from,coverage_to:to,stores:results,source:'Ozon Seller API · accrual/by-day'};
}

"""
        if function_anchor not in worker_text:
            raise SystemExit("OZON_FINANCE_FUNCTION_ANCHOR_MISSING")
        worker_text = worker_text.replace(function_anchor, finance_function + "\n" + function_anchor, 1)
        worker_text = worker_text.replace("VERSION: 'github-worker-1.3.84'", "VERSION: 'github-worker-1.3.86'", 1)
        print("OZON_FINANCE_DAY_LAYER=patched")
    elif "function sellmonitorGithubPublishOzonFinance_(" not in worker_text or "action === 'publish_ozon_api_finance'" not in worker_text:
        raise SystemExit("OZON_FINANCE_PATCH_PARTIAL")

    # Add verified store-level finance facts to the existing shop RNP, without assigning them to SKUs.
    store_rnp_marker = "OZON_STORE_RNP_FINANCE_V1"
    if store_rnp_marker not in worker_text:
        store_rnp_anchor = "    var periodResult=replace_('19_API_Финансы_периоды',periodRows,11);\n"
        store_rnp_code = r"""    /* OZON_STORE_RNP_FINANCE_V1 */
    var rnpSheet=ss.getSheetByName('00_РНП_магазин');
    if(!rnpSheet)throw new Error('OZON_FINANCE_RNP_SHEET_MISSING:'+cfg.name);
    if(rnpSheet.getMaxColumns()<17)rnpSheet.insertColumnsAfter(rnpSheet.getMaxColumns(),17-rnpSheet.getMaxColumns());
    var rnpLast=Math.max(1,rnpSheet.getLastRow());
    var rnpLabelRows=rnpSheet.getRange(1,1,rnpLast,3).getDisplayValues();
    var rnpHeaders=['Начислено всего, ₽','Товар (ITEM), ₽','Отправления (POSTING), ₽','Нетоварное (NON_ITEM), ₽','Прочие категории, ₽','Строк начислений / дней с данными','Состояние финансового источника'];
    var rnpHeaderRows=[];
    var rnpRange=rnpSheet.getRange(1,11,rnpLast,7);
    var oldRnpFormulas=rnpRange.getFormulas();
    if(oldRnpFormulas.some(function(row){return row.some(function(value){return String(value||'')!=='';});}))
      throw new Error('OZON_FINANCE_RNP_FORMULA_PROTECTED:'+cfg.name);
    var oldRnp=rnpRange.getValues();
    var nextRnp=oldRnp.map(function(row){return row.slice();});
    var rnpByDay=Object.create(null),rnpByPeriod=Object.create(null);
    storeDays.forEach(function(row){rnpByDay[String(row[0])]=row;});
    periodRows.slice(1).forEach(function(row){rnpByPeriod[String(row[0])+'|'+String(row[1])+'|'+String(row[2])]=row;});
    function rnpIso_(value){
      var text=String(value||'').trim(),match=text.match(/^(\d{1,2})\.(\d{1,2})\.(\d{4})$/);
      if(match)return match[3]+'-'+('0'+match[2]).slice(-2)+'-'+('0'+match[1]).slice(-2);
      return /^\d{4}-\d{2}-\d{2}$/.test(text)?text:'';
    }
    function rnpFinanceMissing_(){return ['','','','','','','Нет покрытия источника'];}
    for(var ri=0;ri<rnpLabelRows.length;ri++){
      var label=String(rnpLabelRows[ri][0]||'').trim();
      if(label==='Дневные итоги'||label==='Недельные итоги'||label==='Месячные итоги'){
        nextRnp[ri]=rnpHeaders.slice();rnpHeaderRows.push(ri+1);continue;
      }
      var fromDay=rnpIso_(rnpLabelRows[ri][1]),toDay=rnpIso_(rnpLabelRows[ri][2]||rnpLabelRows[ri][1]);
      if(label==='День'&&fromDay){
        var dayFact=rnpByDay[fromDay];
        nextRnp[ri]=dayFact?[dayFact[1],dayFact[2],dayFact[3],dayFact[4],dayFact[5],dayFact[6],dayFact[8]]:rnpFinanceMissing_();
      }else if((label==='Неделя'||label==='Месяц')&&fromDay&&toDay){
        var periodFact=rnpByPeriod[label+'|'+fromDay+'|'+toDay];
        nextRnp[ri]=periodFact?[periodFact[3],periodFact[4],periodFact[5],periodFact[6],periodFact[7],periodFact[8],periodFact[10]]:rnpFinanceMissing_();
      }
    }
    var rnpChanged=JSON.stringify(oldRnp)!==JSON.stringify(nextRnp),rnpArchived=0;
    if(rnpChanged){
      var rnpHasOld=oldRnp.some(function(row){return row.some(function(value){return value!==''&&value!==null;});});
      if(rnpHasOld)rnpArchived+=sellmonitorGithubOzonArchive_(archive,'00_РНП_магазин','K1:Q'+rnpLast,oldRnp,exported,archiveId);
      rnpRange.setValues(nextRnp);SpreadsheetApp.flush();
    }
    rnpHeaderRows.forEach(function(row){
      rnpSheet.getRange(row,11,1,7).setBackground('#17365D').setFontColor('#FFFFFF').setFontWeight('bold').setWrap(true);
    });
    if(rnpLast>1){
      rnpSheet.getRange(1,11,rnpLast,5).setNumberFormat('#,##0.00;[Red]-#,##0.00;0.00');
      rnpSheet.getRange(1,16,rnpLast,1).setNumberFormat('#,##0');
      rnpSheet.getRange(1,17,rnpLast,1).setWrap(true);
    }
    var oldRnpDescription=String(rnpSheet.getRange('A3').getValue()||'');
    var financeNotice='Начисления Ozon: только уровень магазина в колонках K:Q; это не прибыль и не разбивка по артикулам.';
    var newRnpDescription=oldRnpDescription.indexOf(financeNotice)>=0?oldRnpDescription:
      (oldRnpDescription?oldRnpDescription+' '+financeNotice:financeNotice);
    if(oldRnpDescription!==newRnpDescription){
      rnpArchived+=sellmonitorGithubOzonArchive_(archive,'00_РНП_магазин','A3',[[oldRnpDescription]],exported,archiveId);
      rnpSheet.getRange('A3').setValue(newRnpDescription);
    }
    var rnpReadback=rnpRange.getValues();
    if(JSON.stringify(rnpReadback)!==JSON.stringify(nextRnp)||String(rnpSheet.getRange('A3').getValue()||'')!==newRnpDescription)
      throw new Error('OZON_FINANCE_RNP_READBACK_FAILED:'+cfg.name);
    var rnpResult={rows:rnpHeaderRows.length? rnpLast:0,sha256:sellmonitorGithubOzonHash_(rnpReadback),readback:true,archived_rows:rnpArchived};
    archiveRows+=rnpArchived;
    var journal=ss.getSheetByName('20_API_Финансы_журнал');""";
        if worker_text.count(store_rnp_anchor) != 1:
            raise SystemExit("OZON_STORE_RNP_FINANCE_ANCHOR_MISMATCH")
        worker_text = worker_text.replace(store_rnp_anchor, store_rnp_anchor + store_rnp_code, 1)
        worker_text = worker_text.replace("VERSION: 'github-worker-1.3.88'", "VERSION: 'github-worker-1.3.89'", 1)
        print("OZON_STORE_RNP_FINANCE=patched")
    elif "var rnpResult={rows:rnpHeaderRows.length?" not in worker_text:
        raise SystemExit("OZON_STORE_RNP_FINANCE_PATCH_PARTIAL")

    # Keep period-matrix freshness labels tied to the actual verified source and protect the prior cell value.
    freshness_marker = "WB_RNP_FRESHNESS_AND_AXIS_READBACK_V1"
    freshness_anchors = [
        "    stats.push({sheet:spec.name,target_periods:targets.length,updated_columns:updates.length,archived_segments:archives,written_values:written,duplicates_removed:dupDates.length,min:targets[0]||'',max:targets.length?targets[targets.length-1]:''});",
        "    stats.push({sheet:spec.name,target_periods:targets.length,source_periods:targets,readback_periods:readbackPeriods,coverage_verified:coverageVerified,axis_rebuilt:axisRebuilt,axis_archive_segments:axisArchiveRows,updated_columns:updates.length,archived_segments:archives,written_values:written,duplicates_removed:dupDates.length,min:targets[0]||'',max:targets.length?targets[targets.length-1]:''});",
        "    stats.push({sheet:spec.name,target_periods:targets.length,source_periods:targets,readback_periods:readbackPeriods,coverage_verified:coverageVerified,axis_rebuilt:axisRebuilt,axis_archive_segments:axisArchiveRows,updated_columns:updates.length,store_summary_columns:storeSummaryWrites.length,store_summary_rows_verified:storeSummaryReadbackCells,store_summary_readback:true,archived_segments:archives,written_values:written,duplicates_removed:dupDates.length,min:targets[0]||'',max:targets.length?targets[targets.length-1]:''});",
    ]
    if freshness_marker not in worker_text:
        freshness_matches = [anchor for anchor in freshness_anchors if worker_text.count(anchor) == 1]
        if len(freshness_matches) != 1:
            raise SystemExit("WB RNP matrix stats anchor mismatch")
        freshness_anchor = freshness_matches[0]
        freshness_code = """/* WB_RNP_FRESHNESS_AND_AXIS_READBACK_V1 */
    if(spec.grain==='week'||spec.grain==='month'){
      var freshnessCell=sh.getRange('M1');
      var freshnessText='Finance Inner exact periods · daily до '+(exactMax||'нет точного факта')+' · '+built.sourceRows+' фактов источника · предварительных дней: '+built.partialDays+' · FBS отдельно: операционный контроль';
      var oldFreshness=freshnessCell.getDisplayValue();
      if(oldFreshness!==freshnessText){
        archiveSnapshot(sh,13,'SOURCE_FRESHNESS_NOTE','Обновление метки свежести после проверки источника',1,[oldFreshness]);
        freshnessCell.setValue(freshnessText);
        SpreadsheetApp.flush();
        if(freshnessCell.getDisplayValue()!==freshnessText)throw new Error('WB_RNP_FRESHNESS_NOTE_READBACK_FAILED:'+spec.name);
      }
    }
"""
        worker_text = worker_text.replace(freshness_anchor, freshness_code + "\n" + freshness_anchor, 1)
        print("WB_RNP_FRESHNESS_AND_AXIS_READBACK=patched")
    elif freshness_marker not in worker_text and "function sellmonitorGithubWbInnerSyncRnpMatrices_" in worker_text:
        raise SystemExit("WB RNP freshness patch anchor changed")

    # Keep the order freshness note in the existing store daily sheet tied to the verified Seller API export.
    ozon_freshness_marker = "OZON_ORDER_FRESHNESS_NOTE_V1"
    ozon_freshness_anchor = "    var sameSummaryNote=oldSummaryNote===newSummaryNote;"
    if ozon_freshness_marker not in worker_text:
        if worker_text.count(ozon_freshness_anchor) != 1:
            raise SystemExit("OZON order freshness note anchor mismatch")
        ozon_freshness_code = """/* OZON_ORDER_FRESHNESS_NOTE_V1 */
    var orderFreshnessSheet=ss.getSheetByName('06_Магазин_дни');
    if(!orderFreshnessSheet)throw new Error('OZON_ORDER_FRESHNESS_SHEET_MISSING:'+cfg.name);
    var oldOrderFreshnessNote=String(orderFreshnessSheet.getRange('A1').getValue()||'');
    var orderCoverageNote=adMin&&adMax?'покрытие '+adMin+'–'+adMax:'покрытие не подтверждено';
    var newOrderFreshnessNote='Заказы: '+(apiMode?'Ozon Seller API через GitHub, все страницы получены':'последняя подтверждённая выгрузка')+
      '; обновлено '+sourceUpdated+'; период '+agg.minDay+'–'+agg.maxDay+'. '+fullDayNote+' '+currentDayNote+
      ' Реклама: Ozon Performance API по кампаниям, '+orderCoverageNote+
      '. Рекламных показателей по SKU нет; пустое значение не считается нулём.';
    var sameOrderFreshnessNote=oldOrderFreshnessNote===newOrderFreshnessNote;
    if(!sameOrderFreshnessNote)archived+=sellmonitorGithubOzonArchive_(archive,'06_Магазин_дни','A1',[[oldOrderFreshnessNote]],sourceUpdated,archiveId);
"""
        worker_text = worker_text.replace(ozon_freshness_anchor, ozon_freshness_code + ozon_freshness_anchor, 1)
        print("OZON_ORDER_FRESHNESS_NOTE=patched")
    elif "var sameOrderFreshnessNote=oldOrderFreshnessNote===newOrderFreshnessNote;" not in worker_text:
        raise SystemExit("OZON order freshness patch partial")
    order_write_anchor = "    if(!sameGraphNote)graphSheet.getRange('A2').setValue(newGraphNote);"
    order_write_marker = "OZON_ORDER_FRESHNESS_NOTE_WRITE_V1"
    if order_write_marker not in worker_text:
        if worker_text.count(order_write_anchor) != 1:
            raise SystemExit("OZON order freshness write anchor mismatch")
        worker_text = worker_text.replace(order_write_anchor,
            order_write_anchor + "\n    /* OZON_ORDER_FRESHNESS_NOTE_WRITE_V1 */\n    if(!sameOrderFreshnessNote)orderFreshnessSheet.getRange('A1').setValue(newOrderFreshnessNote);", 1)
        print("OZON_ORDER_FRESHNESS_NOTE_WRITE=patched")
    readback_anchor = "    if(String(summarySheet.getRange('A2').getValue()||'')!==newSummaryNote)throw new Error('OZON_SUMMARY_NOTE_READBACK_MISMATCH:'+cfg.name);"
    readback_marker = "OZON_ORDER_FRESHNESS_NOTE_READBACK_V1"
    if readback_marker not in worker_text:
        if worker_text.count(readback_anchor) != 1:
            raise SystemExit("OZON order freshness readback anchor mismatch")
        worker_text = worker_text.replace(readback_anchor,
            readback_anchor + "\n    /* OZON_ORDER_FRESHNESS_NOTE_READBACK_V1 */\n    if(String(orderFreshnessSheet.getRange('A1').getValue()||'')!==newOrderFreshnessNote)throw new Error('OZON_ORDER_FRESHNESS_NOTE_READBACK_FAILED:'+cfg.name);", 1)
        print("OZON_ORDER_FRESHNESS_NOTE_READBACK=patched")
    elif "orderFreshnessSheet.getRange('A1').getValue()||'')!==newOrderFreshnessNote" not in worker_text:
        raise SystemExit("OZON order freshness readback patch partial")
    # Keep Ozon notes aligned with the connected store-level finance source.
    finance_note_marker = "OZON_FINANCE_COVERAGE_NOTE_V1"
    if finance_note_marker not in worker_text:
        finance_note_replacements = (
            ("adCoverageNote+' Финансы, остатки и товарная воронка не передаются подключёнными источниками.';",
             "adCoverageNote+' Финансовые начисления Ozon Seller API доступны на уровне магазина на листах 18/19; свежесть — в листе 20_API_Финансы_журнал. Это не прибыль; маржа и разбивка начислений по товару не подтверждены. Остатки и товарная воронка не подключены.';"),
            ("Атрибутированная сумма рекламных заказов не является начисленной выручкой. Финансы, маржа, выкуп, остатки и органическая воронка не получены.",
             "Атрибутированная сумма рекламных заказов не является начисленной выручкой. Начисления Ozon Seller API доступны по магазину на листах 18/19; это не прибыль. Маржа, выкуп, остатки и органическая воронка не подключены."),
            ("Финансы и реклама на уровне артикула не предоставляются этим источником.",
             "Финансовые начисления доступны только по магазину на листах 18/19; разреза по артикулам нет. Реклама показывается по кампаниям, без SKU/offer_id.")
        )
        for old_note, new_note in finance_note_replacements:
            if worker_text.count(old_note) != 1:
                raise SystemExit("OZON_FINANCE_COVERAGE_NOTE_ANCHOR_MISMATCH")
            worker_text = worker_text.replace(old_note, new_note, 1)
        summary_anchor = "    var newSummaryNote="
        if worker_text.count(summary_anchor) != 1:
            raise SystemExit("OZON_FINANCE_COVERAGE_NOTE_MARKER_ANCHOR_MISMATCH")
        worker_text = worker_text.replace(summary_anchor, "    /* " + finance_note_marker + " */\n" + summary_anchor, 1)
        print("OZON_FINANCE_COVERAGE_NOTE=patched")

    version_matches = re.findall(r"VERSION:\s*'github-worker-(\d+)\.(\d+)\.(\d+)'", worker_text)
    if len(version_matches) != 1:
        raise SystemExit("OZON_FINANCE_WORKER_VERSION_PATTERN_MISMATCH")
    if tuple(int(value) for value in version_matches[0]) < (1, 3, 88):
        raise SystemExit("OZON_FINANCE_WORKER_VERSION_TOO_OLD")

    # Distinguish a partial Sellmonitor source response from a complete finance refresh.
    partial_status_marker = "INNER_FINANCE_PARTIAL_STATUS_V1"
    if partial_status_marker not in worker_text:
        log_status_anchor = "sync.ok ? 'DONE' : 'ERROR',sync.ok ? sync.date : sync.error,sync"
        if worker_text.count(log_status_anchor) != 1:
            raise SystemExit("INNER_FINANCE_PARTIAL_LOG_STATUS_ANCHOR_MISMATCH")
        worker_text = worker_text.replace(log_status_anchor, """/* INNER_FINANCE_PARTIAL_STATUS_V1 */
        sync.partial === true ? 'PARTIAL' : (sync.ok ? 'DONE' : 'ERROR'),sync.ok ? sync.date : sync.error,sync""", 1)
        registry_anchor = "Object.keys(byKey).length,dates[0] || '',dates[dates.length - 1] || '',digest,'OK','PASS','',''"
        if worker_text.count(registry_anchor) != 1:
            raise SystemExit("INNER_FINANCE_PARTIAL_REGISTRY_ANCHOR_MISMATCH")
        worker_text = worker_text.replace(registry_anchor, "Object.keys(byKey).length,dates[0] || '',dates[dates.length - 1] || '',digest,'OK',partialRows>0?'PASS_PARTIAL':'PASS','',''", 1)
        result_anchor = "checksum:digest,qc_status:'PASS',trusted_rows:merged.length - partialRows,partial_rows:partialRows,"
        if worker_text.count(result_anchor) != 1:
            raise SystemExit("INNER_FINANCE_PARTIAL_RESULT_ANCHOR_MISMATCH")
        worker_text = worker_text.replace(result_anchor, "checksum:digest,qc_status:partialRows>0?'PASS_PARTIAL':'PASS',coverage_status:partialRows>0?'PARTIAL':'COMPLETE',trusted_rows:merged.length - partialRows,partial_rows:partialRows,", 1)
        tick_anchor = "return Object.assign({ok:sync.ok !== false,version:SMC_GH.VERSION,store_id:storeId},sync);"
        if worker_text.count(tick_anchor) != 1:
            raise SystemExit("INNER_FINANCE_PARTIAL_TICK_RESULT_ANCHOR_MISMATCH")
        tick_replacement = """return Object.assign({ok:sync.ok !== false,version:SMC_GH.VERSION,store_id:storeId},sync,{
      complete:sync.attempted===true && sync.ok===true && sync.partial!==true,
      source_status:sync.partial===true?'PARTIAL':(sync.ok===false?'ERROR':(sync.attempted===true?'COMPLETE':'SKIPPED'))
    });"""
        worker_text = worker_text.replace(tick_anchor, tick_replacement, 1)
        version_matches = re.findall(r"VERSION:\s*'github-worker-(\d+)\.(\d+)\.(\d+)'", worker_text)
        if len(version_matches) != 1:
            raise SystemExit("INNER_FINANCE_PARTIAL_VERSION_PATTERN_MISMATCH")
        major, minor, patch = [int(value) for value in version_matches[0]]
        current_version = ".".join(str(value) for value in (major, minor, patch))
        next_version = ".".join(str(value) for value in (major, minor, patch + 1))
        version_anchor = "VERSION: 'github-worker-" + current_version + "'"
        if worker_text.count(version_anchor) != 1:
            raise SystemExit("INNER_FINANCE_PARTIAL_VERSION_ANCHOR_MISMATCH")
        worker_text = worker_text.replace(version_anchor, "VERSION: 'github-worker-" + next_version + "'", 1)
        print("INNER_FINANCE_WORKER_VERSION=" + current_version + "->" + next_version)
        print("INNER_FINANCE_PARTIAL_STATUS=patched")

    # Inspect the exact schemas and safe error details for the exposed finance routes.
    probe_marker = "INNER_FINANCE_RAW_REPORT_PROBE_V4"
    if probe_marker not in worker_text:
        mcp_call_anchor = "  var call = post({jsonrpc:'2.0',id:2,method:'tools/call',params:{name:tool,arguments:args || {}}},init.session);"
        if worker_text.count(mcp_call_anchor) != 1:
            raise SystemExit("INNER_FINANCE_MCP_DISPATCH_ANCHOR_MISMATCH")
        mcp_call_replacement = """  var call = tool === '__SELLMONITOR_TOOLS_LIST__'
    ? post({jsonrpc:'2.0',id:2,method:'tools/list',params:args || {}},init.session)
    : post({jsonrpc:'2.0',id:2,method:'tools/call',params:{name:tool,arguments:args || {}}},init.session);"""
        worker_text = worker_text.replace(mcp_call_anchor, mcp_call_replacement, 1)
        probe_anchor = "    var rows = sellmonitorGithubInnerFinanceRows_(cfg,dateKey,nextDay,products,new Date(),{partial:partial,freshness:freshness});"
        if worker_text.count(probe_anchor) != 1:
            raise SystemExit("INNER_FINANCE_RAW_REPORT_PROBE_ANCHOR_MISMATCH")
        probe_code = """    /* INNER_FINANCE_RAW_REPORT_PROBE_V4 */
    var financeToolsProbe = null;
    var financeRawProbe = null;
    if (partial && (!sellmonitorGithubInnerCategoryFresh_(freshness,'financial_operations') ||
                    !sellmonitorGithubInnerCategoryFresh_(freshness,'financial_report_rows'))) {
      var toolProbeProps = sellmonitorClientProperties_(cfg.spreadsheetId);
      var toolProbeKey = 'SMC_INNER_MCP_TOOLS_PROBE_V2_LAST_MS';
      var toolProbeLast = Number(toolProbeProps.getProperty(toolProbeKey) || 0);
      if (!toolProbeLast || Date.now() - toolProbeLast >= 6 * 60 * 60 * 1000) {
        toolProbeProps.setProperty(toolProbeKey,String(Date.now()));
        try {
          var toolCatalog = sellmonitorGithubInnerMcpCall_(cfg.spreadsheetId,'__SELLMONITOR_TOOLS_LIST__',{});
          var exposedTools = toolCatalog && Array.isArray(toolCatalog.tools) ? toolCatalog.tools : [];
          var focusNames = ['analytics_get_marketplace_raw_report_rows','analytics_get_marketplace_expenses','analytics_get_product_list'];
          var focusTools = exposedTools.filter(function(t) {
            return focusNames.indexOf(String(t && t.name || '')) >= 0;
          }).map(function(t) {
            var schema = t && (t.inputSchema || t.input_schema) || {};
            return {
              name:String(t && t.name || ''),
              description:String(t && t.description || '').slice(0,500),
              input_schema:{
                required:Array.isArray(schema.required)?schema.required:[],
                properties:schema.properties || {}
              }
            };
          });
          financeToolsProbe = {ok:true,total:exposedTools.length,focus_tools:focusTools};
        } catch (toolProbeError) {
          var toolProbeMessage = String(toolProbeError && toolProbeError.message ? toolProbeError.message : toolProbeError)
            .replace(/[A-Za-z0-9_-]{32,}/g,'[redacted]').slice(0,300);
          financeToolsProbe = {ok:false,error:toolProbeMessage};
        }
      }
      var rawProbeKey = 'SMC_INNER_MCP_RAW_REPORT_PROBE_V2_LAST_MS';
      var rawProbeLast = Number(toolProbeProps.getProperty(rawProbeKey) || 0);
      if (!rawProbeLast || Date.now() - rawProbeLast >= 30 * 60 * 1000) {
        toolProbeProps.setProperty(rawProbeKey,String(Date.now()));
        try {
          var rawReportData = sellmonitorGithubInnerMcpCall_(cfg.spreadsheetId,'analytics_get_marketplace_raw_report_rows',{
            marketplaceCode:'wb',marketplaceAccountId:cfg.accountId,
            from:dateKey,to:nextDay,dateField:'rrDt',limit:1000
          });
          var rawReportCandidates = [
            rawReportData,rawReportData && rawReportData.data,rawReportData && rawReportData.rows,
            rawReportData && rawReportData.items,rawReportData && rawReportData.data && rawReportData.data.rows,
            rawReportData && rawReportData.data && rawReportData.data.items
          ];
          var rawReportRows = [];
          for (var rawIndex=0;rawIndex<rawReportCandidates.length;rawIndex++) {
            if (Array.isArray(rawReportCandidates[rawIndex])) { rawReportRows=rawReportCandidates[rawIndex]; break; }
          }
          var rawFirst = rawReportRows.length ? rawReportRows[0] : null;
          financeRawProbe = {
            ok:true,response_type:Array.isArray(rawReportData)?'array':typeof rawReportData,
            response_keys:rawReportData && typeof rawReportData==='object' ? Object.keys(rawReportData).slice(0,30) : [],
            code:String(rawReportData && (rawReportData.code || rawReportData.errorCode) || ''),
            message:String(rawReportData && (rawReportData.message || rawReportData.error) || '')
              .replace(/[A-Za-z0-9_-]{32,}/g,'[redacted]').slice(0,400),
            data_keys:rawReportData && rawReportData.data && typeof rawReportData.data==='object' && !Array.isArray(rawReportData.data)
              ? Object.keys(rawReportData.data).slice(0,30) : [],
            rows:rawReportRows.length,
            first_row_keys:rawFirst && typeof rawFirst==='object' ? Object.keys(rawFirst).slice(0,40) : []
          };
        } catch (rawProbeError) {
          var rawProbeMessage = String(rawProbeError && rawProbeError.message ? rawProbeError.message : rawProbeError)
            .replace(/[A-Za-z0-9_-]{32,}/g,'[redacted]').slice(0,300);
          financeRawProbe = {ok:false,error:rawProbeMessage};
        }
      }
    }
"""
        worker_text = worker_text.replace(probe_anchor, probe_code + "\n" + probe_anchor, 1)
        finance_return_anchor = "fresh_categories:freshCategories,"
        if worker_text.count(finance_return_anchor) != 1:
            raise SystemExit("INNER_FINANCE_RAW_REPORT_PROBE_RETURN_ANCHOR_MISMATCH")
        worker_text = worker_text.replace(finance_return_anchor, "fresh_categories:freshCategories,finance_tools_probe:financeToolsProbe,finance_raw_probe:financeRawProbe,", 1)
        probe_versions = re.findall(r"VERSION:\s*'github-worker-(\d+)\.(\d+)\.(\d+)'", worker_text)
        if len(probe_versions) != 1:
            raise SystemExit("INNER_FINANCE_RAW_REPORT_PROBE_VERSION_PATTERN_MISMATCH")
        probe_major, probe_minor, probe_patch = [int(value) for value in probe_versions[0]]
        probe_current = ".".join(str(value) for value in (probe_major, probe_minor, probe_patch))
        probe_next = ".".join(str(value) for value in (probe_major, probe_minor, probe_patch + 2))
        probe_version_anchor = "VERSION: 'github-worker-" + probe_current + "'"
        if worker_text.count(probe_version_anchor) != 1:
            raise SystemExit("INNER_FINANCE_RAW_REPORT_PROBE_VERSION_ANCHOR_MISMATCH")
        worker_text = worker_text.replace(probe_version_anchor, "VERSION: 'github-worker-" + probe_next + "'", 1)
        print("INNER_FINANCE_WORKER_VERSION=" + probe_current + "->" + probe_next)
        print("INNER_FINANCE_RAW_REPORT_PROBE=patched")

    # The WB raw-report endpoint requires an explicit subject. Keep the
    # probe bounded to one day and request only the supported finance rows.
    subject_patch_marker = "INNER_FINANCE_RAW_REPORT_SUBJECT_V5"
    subject_patch_anchor = "            marketplaceCode:'wb',marketplaceAccountId:cfg.accountId,\n            from:dateKey,to:nextDay,dateField:'rrDt',limit:1000"
    subject_patch_replacement = "            marketplaceCode:'wb',marketplaceAccountId:cfg.accountId,\n            subject:'financial_report_rows',from:dateKey,to:nextDay,limit:1000"
    if subject_patch_marker not in worker_text:
        if worker_text.count(subject_patch_anchor) != 1:
            raise SystemExit("INNER_FINANCE_RAW_REPORT_SUBJECT_ANCHOR_MISMATCH")
        worker_text = worker_text.replace(subject_patch_anchor, subject_patch_replacement, 1)
        subject_versions = re.findall(r"VERSION:\s*'github-worker-(\d+)\.(\d+)\.(\d+)'", worker_text)
        if len(subject_versions) != 1:
            raise SystemExit("INNER_FINANCE_RAW_REPORT_SUBJECT_VERSION_MISMATCH")
        subject_major, subject_minor, subject_patch = [int(value) for value in subject_versions[0]]
        subject_current = ".".join(str(value) for value in (subject_major, subject_minor, subject_patch))
        subject_next = ".".join(str(value) for value in (subject_major, subject_minor, subject_patch + 2))
        subject_version_anchor = "VERSION: 'github-worker-" + subject_current + "'"
        if worker_text.count(subject_version_anchor) != 1:
            raise SystemExit("INNER_FINANCE_RAW_REPORT_SUBJECT_VERSION_ANCHOR_MISMATCH")
        worker_text = worker_text.replace(subject_version_anchor, "VERSION: 'github-worker-" + subject_next + "'", 1)
        print("INNER_FINANCE_WORKER_VERSION=" + subject_current + "->" + subject_next)
        print("INNER_FINANCE_RAW_REPORT_SUBJECT=patched")
    # Capture bounded pagination metadata and the canonical expense-row shape before mapping facts.
    snapshot_marker = "INNER_FINANCE_RAW_REPORT_SNAPSHOT_V6"
    if snapshot_marker not in worker_text:
        request_anchor = "            subject:'financial_report_rows',from:dateKey,to:nextDay,limit:1000"
        request_replacement = "            subject:'financial_report_rows',from:dateKey,to:nextDay,limit:2000"
        if worker_text.count(request_anchor) != 1:
            raise SystemExit("INNER_FINANCE_RAW_REPORT_SNAPSHOT_REQUEST_ANCHOR_MISMATCH")
        worker_text = worker_text.replace(request_anchor, request_replacement, 1)
        pagination_anchor = """            rows:rawReportRows.length,
            first_row_keys:rawFirst && typeof rawFirst==='object' ? Object.keys(rawFirst).slice(0,40) : []"""
        pagination_replacement = """            rows:rawReportRows.length,
            has_more:!!(rawReportData && rawReportData.data && rawReportData.data.hasMore),
            pagination:(function() {
              var p=rawReportData && rawReportData.data && rawReportData.data.pagination;
              if (!p || typeof p!=='object' || Array.isArray(p)) return {};
              var safe={};
              Object.keys(p).slice(0,16).forEach(function(k) {
                var v=p[k];
                if (v==null || ['string','number','boolean'].indexOf(typeof v)>=0) safe[k]=v;
              });
              return safe;
            })(),
            first_row_keys:rawFirst && typeof rawFirst==='object' ? Object.keys(rawFirst).slice(0,40) : []"""
        if worker_text.count(pagination_anchor) != 1:
            raise SystemExit("INNER_FINANCE_RAW_REPORT_SNAPSHOT_PAGINATION_ANCHOR_MISMATCH")
        worker_text = worker_text.replace(pagination_anchor, pagination_replacement, 1)
        probe_variable_anchor = "    var financeRawProbe = null;"
        if worker_text.count(probe_variable_anchor) != 1:
            raise SystemExit("INNER_FINANCE_RAW_REPORT_SNAPSHOT_VARIABLE_ANCHOR_MISMATCH")
        worker_text = worker_text.replace(probe_variable_anchor, probe_variable_anchor + "\n    var financeExpensesProbe = null;", 1)
        expense_anchor = "    var rows = sellmonitorGithubInnerFinanceRows_(cfg,dateKey,nextDay,products,new Date(),{partial:partial,freshness:freshness});"
        if worker_text.count(expense_anchor) != 1:
            raise SystemExit("INNER_FINANCE_EXPENSE_PROBE_ANCHOR_MISMATCH")
        expense_probe = """    /* INNER_FINANCE_RAW_REPORT_SNAPSHOT_V6 */
    if (partial && (!sellmonitorGithubInnerCategoryFresh_(freshness,'financial_operations') ||
                    !sellmonitorGithubInnerCategoryFresh_(freshness,'financial_report_rows'))) {
      var expenseProbeKey = 'SMC_INNER_MCP_EXPENSES_PROBE_V6_LAST_MS';
      var expenseProbeLast = Number(toolProbeProps.getProperty(expenseProbeKey) || 0);
      if (!expenseProbeLast || Date.now() - expenseProbeLast >= 30 * 60 * 1000) {
        toolProbeProps.setProperty(expenseProbeKey,String(Date.now()));
        try {
          var expenseData = sellmonitorGithubInnerMcpCall_(cfg.spreadsheetId,'analytics_get_marketplace_expenses',{
            marketplaceCode:'wb',marketplaceAccountId:cfg.accountId,
            from:dateKey,to:nextDay,groupByDate:true,limit:2000
          });
          var expenseCandidates = [
            expenseData,expenseData && expenseData.data,expenseData && expenseData.rows,
            expenseData && expenseData.items,expenseData && expenseData.data && expenseData.data.rows,
            expenseData && expenseData.data && expenseData.data.items
          ];
          var expenseRows = [];
          for (var expenseIndex=0;expenseIndex<expenseCandidates.length;expenseIndex++) {
            if (Array.isArray(expenseCandidates[expenseIndex])) { expenseRows=expenseCandidates[expenseIndex]; break; }
          }
          var expenseEnvelope = expenseData && expenseData.data && typeof expenseData.data==='object' && !Array.isArray(expenseData.data)
            ? expenseData.data : expenseData;
          financeExpensesProbe = {
            ok:true,
            response_type:Array.isArray(expenseData)?'array':typeof expenseData,
            response_keys:expenseData && typeof expenseData==='object' ? Object.keys(expenseData).slice(0,24) : [],
            data_keys:expenseEnvelope && typeof expenseEnvelope==='object' && !Array.isArray(expenseEnvelope)
              ? Object.keys(expenseEnvelope).slice(0,24) : [],
            rows:expenseRows.length,
            has_more:!!(expenseEnvelope && expenseEnvelope.hasMore),
            first_row_keys:expenseRows.length && expenseRows[0] && typeof expenseRows[0]==='object'
              ? Object.keys(expenseRows[0]).slice(0,32) : []
          };
        } catch (expenseProbeError) {
          var expenseProbeMessage = String(expenseProbeError && expenseProbeError.message ? expenseProbeError.message : expenseProbeError)
            .replace(/[A-Za-z0-9_-]{32,}/g,'[redacted]').slice(0,300);
          financeExpensesProbe = {ok:false,error:expenseProbeMessage};
        }
      }
    }
"""
        worker_text = worker_text.replace(expense_anchor, expense_probe + expense_anchor, 1)
        return_anchor = "finance_raw_probe:financeRawProbe,"
        if worker_text.count(return_anchor) != 1:
            raise SystemExit("INNER_FINANCE_RAW_REPORT_SNAPSHOT_RETURN_ANCHOR_MISMATCH")
        worker_text = worker_text.replace(return_anchor, return_anchor + "finance_expenses_probe:financeExpensesProbe,", 1)
        snapshot_versions = re.findall(r"VERSION:\s*'github-worker-(\d+)\.(\d+)\.(\d+)'", worker_text)
        if len(snapshot_versions) != 1:
            raise SystemExit("INNER_FINANCE_RAW_REPORT_SNAPSHOT_VERSION_MISMATCH")
        snapshot_major, snapshot_minor, snapshot_patch = [int(value) for value in snapshot_versions[0]]
        snapshot_current = ".".join(str(value) for value in (snapshot_major,snapshot_minor,snapshot_patch))
        snapshot_next = ".".join(str(value) for value in (snapshot_major,snapshot_minor,snapshot_patch + 2))
        snapshot_version_anchor = "VERSION: 'github-worker-" + snapshot_current + "'"
        if worker_text.count(snapshot_version_anchor) != 1:
            raise SystemExit("INNER_FINANCE_RAW_REPORT_SNAPSHOT_VERSION_ANCHOR_MISMATCH")
        worker_text = worker_text.replace(snapshot_version_anchor, "VERSION: 'github-worker-" + snapshot_next + "'", 1)
        print("INNER_FINANCE_WORKER_VERSION=" + snapshot_current + "->" + snapshot_next)
        print("INNER_FINANCE_RAW_REPORT_SNAPSHOT=patched")


    # Summarize the bounded Sellmonitor expense response by its own metric labels.
    # Keep this diagnostic free of product IDs, raw rows, and credentials.
    expense_v7_marker = "INNER_FINANCE_EXPENSE_METRIC_SUMMARY_V7"
    if expense_v7_marker not in worker_text:
        expense_anchor = """            rows:expenseRows.length,
            has_more:!!(expenseEnvelope && expenseEnvelope.hasMore),
            first_row_keys:expenseRows.length && expenseRows[0] && typeof expenseRows[0]==='object'
              ? Object.keys(expenseRows[0]).slice(0,32) : []"""
        expense_replacement = """            rows:expenseRows.length,
            has_more:!!(expenseEnvelope && expenseEnvelope.hasMore),
            metric_summary:(function() {
              /* INNER_FINANCE_EXPENSE_METRIC_SUMMARY_V7 */
              var buckets={};
              function number_(v) {
                if (typeof v==='number') return isFinite(v)?v:null;
                if (typeof v!=='string' || !v.trim()) return null;
                var n=Number(v.trim().replace(/\\s/g,'').replace(',','.'));
                return isFinite(n)?n:null;
              }
              expenseRows.forEach(function(row) {
                row=row && typeof row==='object'?row:{};
                var metric=String(row.dashboardMetric || '');
                var role=String(row.dashboardFormulaRole || '');
                var type=String(row.type || '');
                var group=String(row.group || '');
                var key=[metric,role,type,group].join('|');
                var b=buckets[key] || (buckets[key]={
                  metric:metric,role:role,type:type,group:group,rows:0,source_rows:0,
                  products:{},reliable_rows:0,dashboard_rows:0,numeric_cost_rows:0,
                  missing_cost_rows:0,object_cost_rows:0,sum_all:0,sum_reliable:0,
                  sum_dashboard:0,cost_object_keys:[],note:''
                });
                b.rows++;
                b.source_rows+=number_(row.rowsCount) || 0;
                if (row.nmId!=null && String(row.nmId)) b.products[String(row.nmId)]=1;
                var cost=row.cost;
                if (cost===null || cost===undefined || cost==='') {
                  b.missing_cost_rows++;
                } else if (typeof cost==='object') {
                  b.object_cost_rows++;
                  b.cost_object_keys=Object.keys(cost).slice(0,12);
                } else {
                  var n=number_(cost);
                  if (n===null) b.missing_cost_rows++;
                  else {
                    b.numeric_cost_rows++;
                    b.sum_all+=n;
                    if (row.reliableForAnalysis===true) {
                      b.reliable_rows++;
                      b.sum_reliable+=n;
                    }
                    if (row.contributesToDashboardTotal===true) {
                      b.dashboard_rows++;
                      b.sum_dashboard+=n;
                    }
                  }
                }
                if (!b.note && row.dashboardFormulaNote) b.note=String(row.dashboardFormulaNote).slice(0,100);
              });
              return Object.keys(buckets).sort().slice(0,40).map(function(k) {
                var b=buckets[k];
                return {
                  metric:b.metric,role:b.role,type:b.type,group:b.group,rows:b.rows,
                  source_rows:b.source_rows,products:Object.keys(b.products).length,
                  reliable_rows:b.reliable_rows,dashboard_rows:b.dashboard_rows,
                  numeric_cost_rows:b.numeric_cost_rows,missing_cost_rows:b.missing_cost_rows,
                  object_cost_rows:b.object_cost_rows,
                  sum_all:Math.round(b.sum_all*100)/100,
                  sum_reliable:Math.round(b.sum_reliable*100)/100,
                  sum_dashboard:Math.round(b.sum_dashboard*100)/100,
                  cost_object_keys:b.cost_object_keys,note:b.note
                };
              });
            })(),
            first_row_keys:expenseRows.length && expenseRows[0] && typeof expenseRows[0]==='object'
              ? Object.keys(expenseRows[0]).slice(0,32) : []"""
        if worker_text.count(expense_anchor) != 1:
            raise SystemExit("INNER_FINANCE_EXPENSE_METRIC_V7_ANCHOR_MISMATCH")
        worker_text=worker_text.replace(expense_anchor,expense_replacement,1)
        versions=re.findall(r"VERSION:\s*'github-worker-(\d+)\.(\d+)\.(\d+)'",worker_text)
        if len(versions)!=1:
            raise SystemExit("INNER_FINANCE_EXPENSE_METRIC_V7_VERSION_MISMATCH")
        major,minor,patch_no=[int(v) for v in versions[0]]
        current=".".join(str(v) for v in (major,minor,patch_no))
        next_version=".".join(str(v) for v in (major,minor,patch_no+1))
        version_anchor="VERSION: 'github-worker-"+current+"'"
        if worker_text.count(version_anchor)!=1:
            raise SystemExit("INNER_FINANCE_EXPENSE_METRIC_V7_VERSION_ANCHOR_MISMATCH")
        worker_text=worker_text.replace(version_anchor,"VERSION: 'github-worker-"+next_version+"'",1)
        print("INNER_FINANCE_WORKER_VERSION="+current+"->"+next_version)
        print("INNER_FINANCE_EXPENSE_METRIC_SUMMARY_V7=patched")

    # Do not publish a store total as complete when a sold SKU has a missing metric.
    coverage_patch_marker = "WB_RNP_METRIC_COVERAGE_V1"
    if coverage_patch_marker not in worker_text:
        coverage_anchor = "  function rnpValue(list,code){"
        coverage_replacement = """  /* WB_RNP_METRIC_COVERAGE_V1 */
  function metricCoverageMissing_(list,code){
    var dependencies={
      fin_Profit:['profit_rub'],
      fin_margin:['profit_rub','revenue_rub'],
      fin_roi:['profit_rub','expenses_total_rub'],
      fin_sales_total_sum:['revenue_rub'],
      newfin_retail_price_withdisc:['revenue_rub'],
      fin_avgBillSPP:['customer_revenue_rub'],
      fin_avgBillnoSPP:['revenue_rub'],
      fin_SPP_Rate:['marketplace_discount_rub','revenue_rub'],
      fin_DRR:['ad_rub','revenue_rub'],
      drr_orders:['ad_rub','orders_revenue_rub'],
      expAdCosts:['ad_rub'],
      fin_Costs:['expenses_total_rub'],
      fin_purchase:['prime_cost_rub'],
      fin_comis:['commission_rub'],
      fin_logistics:['logistic_rub'],
      storage_warehousePrice:['storage_rub'],
      acceptance_total:['paid_acceptance_rub'],
      newfin_penalty:['fines_rub'],
      fin_taxpay:['tax_rub'],
      newfin_deductions:['deduction_rub'],
      newfin_delivery_sum:['delivery_logistic_rub'],
      newfin_delivery_return_sum:['return_logistic_rub']
    };
    var required=dependencies[code];
    if(!required||!list||!list.length)return null;
    var q=metricIx.net_sales_qty,expected=0,missing=0;
    if(q==null)return {missing:list.length,expected:list.length,fields:required.slice()};
    for(var i=0;i<list.length;i++){
      var qv=list[i][q];
      if(qv===''||qv==null||typeof qv==='boolean'||!isFinite(Number(qv))||Number(qv)<=0)continue;
      expected++;
      for(var j=0;j<required.length;j++){
        var fi=metricIx[required[j]],v=fi==null?null:list[i][fi];
        if(fi==null||v===''||v==null||typeof v==='boolean'||!isFinite(Number(v))){missing++;break;}
      }
    }
    return missing?{missing:missing,expected:expected,fields:required.slice()}:null;
  }
  function rnpValue(list,code){"""
        if worker_text.count(coverage_anchor)!=1:
            raise SystemExit("WB_RNP_METRIC_COVERAGE_HELPER_ANCHOR_MISMATCH")
        worker_text=worker_text.replace(coverage_anchor,coverage_replacement,1)

        status_anchor = "      var all=productKeys.map(function(n){return productMap[n][0];});"
        status_replacement = """      var all=productKeys.map(function(n){return productMap[n][0];});
      var coverageChecks=['fin_sales_total_sum','fin_Profit','fin_avgBillSPP','fin_SPP_Rate','fin_DRR'];
      var coverageGaps=[];
      coverageChecks.forEach(function(coverageCode){
        var coverage=metricCoverageMissing_(all,coverageCode);
        if(coverage)coverageGaps.push(coverageCode+':'+coverage.missing+'/'+coverage.expected);
      });
      if(coverageGaps.length){
        pstat.partial=true;pstat.partialCoverage=true;
        next[0]=storePeriodFacts.status==='🟢 ФАКТ'?'🟡 ЧАСТИЧНО':storePeriodFacts.status;
      }"""
        if worker_text.count(status_anchor)!=1:
            raise SystemExit("WB_RNP_METRIC_COVERAGE_STATUS_ANCHOR_MISMATCH")
        worker_text=worker_text.replace(status_anchor,status_replacement,1)

        store_anchor = """        if(level==='МАГАЗИН'){
          if(!Object.prototype.hasOwnProperty.call(storeValueCache,code))storeValueCache[code]=rnpValue(all,code);
          v=storeValueCache[code];
        }"""
        store_replacement = """        if(level==='МАГАЗИН'){
          var aggregateStoreMetric=storeAggregateCode_(code);
          if(aggregateStoreMetric&&(spec.grain!=='day'||storePeriodFacts.rows.length)){
            if(!Object.prototype.hasOwnProperty.call(storeValueCache,code))storeValueCache[code]=storeAggregateValue_(storePeriodFacts.rows,code);
            v=storeValueCache[code];
          }else{
            var metricCoverage=metricCoverageMissing_(all,code);
            if(metricCoverage){v='';}
            else{
              if(!Object.prototype.hasOwnProperty.call(storeValueCache,code))storeValueCache[code]=rnpValue(all,code);
              v=storeValueCache[code];
            }
          }
        }"""
        if worker_text.count(store_anchor)!=1:
            raise SystemExit("WB_RNP_METRIC_COVERAGE_STORE_ANCHOR_MISMATCH")
        worker_text=worker_text.replace(store_anchor,store_replacement,1)

        header_anchor = "    var baseStatus=restoreBase_(spec,sh);"
        header_replacement = """    var baseStatus=restoreBase_(spec,sh);
    var oldCoverageHeader=String(sh.getRange(1,11).getDisplayValue()||'').toLowerCase();
    if(storeId==='sanych_wb'&&oldCoverageHeader.indexOf('фин. отчет полный')>=0){
      var headerLast=sh.getLastRow();
      archiveSnapshot(sh,11,'HEADER_K1','Сохранение старой подписи до уточнения полноты метрик',headerLast);
      sh.getRange(1,11).setValue('Фин. покрытие по метрикам');
      SpreadsheetApp.flush();
      if(String(sh.getRange(1,11).getDisplayValue())!=='Фин. покрытие по метрикам')throw new Error('WB_RNP_METRIC_COVERAGE_HEADER_READBACK_FAILED:'+spec.name);
    }"""
        if worker_text.count(header_anchor)!=1:
            raise SystemExit("WB_RNP_METRIC_COVERAGE_HEADER_ANCHOR_MISMATCH")
        worker_text=worker_text.replace(header_anchor,header_replacement,1)

        coverage_versions=re.findall(r"VERSION:\s*'github-worker-(\d+)\.(\d+)\.(\d+)'",worker_text)
        if len(coverage_versions)!=1:
            raise SystemExit("WB_RNP_METRIC_COVERAGE_VERSION_PATTERN_MISMATCH")
        coverage_major,coverage_minor,coverage_patch=[int(value) for value in coverage_versions[0]]
        coverage_current=".".join(str(value) for value in (coverage_major,coverage_minor,coverage_patch))
        coverage_next=".".join(str(value) for value in (coverage_major,coverage_minor,coverage_patch+1))
        coverage_version_anchor="VERSION: 'github-worker-"+coverage_current+"'"
        if worker_text.count(coverage_version_anchor)!=1:
            raise SystemExit("WB_RNP_METRIC_COVERAGE_VERSION_ANCHOR_MISMATCH")
        worker_text=worker_text.replace(coverage_version_anchor,"VERSION: 'github-worker-"+coverage_next+"'",1)
        print("INNER_FINANCE_WORKER_VERSION="+coverage_current+"->"+coverage_next)
        print("WB_RNP_METRIC_COVERAGE=patched")

    # Keep Ozon graph captions tied to the source window, archive old labels, and verify them after write.
    graph_label_marker = "OZON_GRAPH_LABEL_FRESHNESS_V1"
    if graph_label_marker not in worker_text:
        performance_anchor = "  if(chartSheet){\n    var oldChartNote=chartSheet.getRange('A2').getValue();"
        performance_replacement = """  var graphCaptionWrites=[];
  if(chartSheet){
    /* OZON_GRAPH_LABEL_FRESHNESS_V1 */
    [
      {cell:'A21',value:'ДНЕВНАЯ РЕКЛАМА · только кампании; покрытие '+coverageFrom+'–'+coverageTo},
      {cell:'A57',value:'НЕДЕЛЬНАЯ РЕКЛАМА · только кампании; источник '+coverageFrom+'–'+coverageTo+'; крайние недели могут быть неполными'},
      {cell:'A93',value:'МЕСЯЧНАЯ РЕКЛАМА · только кампании; источник '+coverageFrom+'–'+coverageTo+'; крайние месяцы могут быть неполными'}
    ].forEach(function(spec){
      var oldValue=String(chartSheet.getRange(spec.cell).getValue()||'');
      if(oldValue!==spec.value)helperArchive.push({sheet:'10_Графики_магазина',range:spec.cell,values:[oldValue]});
      graphCaptionWrites.push({cell:spec.cell,value:spec.value});
    });
    var oldChartNote=chartSheet.getRange('A2').getValue();"""
        if worker_text.count(performance_anchor) != 1:
            raise SystemExit("OZON_GRAPH_LABEL_PERFORMANCE_ANCHOR_MISMATCH")
        worker_text = worker_text.replace(performance_anchor, performance_replacement, 1)
        performance_write_anchor = "  dailySheet.getRange('A1').setValue(dailySheetTitle);"
        if worker_text.count(performance_write_anchor) != 1:
            raise SystemExit("OZON_GRAPH_LABEL_PERFORMANCE_WRITE_ANCHOR_MISMATCH")
        worker_text = worker_text.replace(performance_write_anchor,
            "  graphCaptionWrites.forEach(function(x){chartSheet.getRange(x.cell).setValue(x.value);});\n" + performance_write_anchor, 1)
        performance_readback_anchor = "  if(!dailyOk||!periodOk||!aggregatesOk||!axesOk||!rnpNoteReadback||!chartRangesOk)throw new Error('OZON_PERF_READBACK_FAILED:'+cfg.name+':'+dailyOk+':'+periodOk+':'+aggregatesOk+':'+axesOk+':'+rnpNoteReadback+':'+chartRangesOk);"
        if worker_text.count(performance_readback_anchor) != 1:
            raise SystemExit("OZON_GRAPH_LABEL_PERFORMANCE_READBACK_ANCHOR_MISMATCH")
        performance_readback = """  var graphCaptionsOk=graphCaptionWrites.every(function(x){return String(chartSheet.getRange(x.cell).getValue()||'')===x.value;});
  if(!dailyOk||!periodOk||!aggregatesOk||!axesOk||!rnpNoteReadback||!chartRangesOk||!graphCaptionsOk)throw new Error('OZON_PERF_READBACK_FAILED:'+cfg.name+':'+dailyOk+':'+periodOk+':'+aggregatesOk+':'+axesOk+':'+rnpNoteReadback+':'+chartRangesOk+':captions='+graphCaptionsOk);"""
        worker_text = worker_text.replace(performance_readback_anchor, performance_readback, 1)
        performance_result_anchor = "chart_ranges_readback:chartRangesOk,charts:charts,"
        if worker_text.count(performance_result_anchor) != 1:
            raise SystemExit("OZON_GRAPH_LABEL_PERFORMANCE_RESULT_ANCHOR_MISMATCH")
        worker_text = worker_text.replace(performance_result_anchor,
            "chart_ranges_readback:chartRangesOk,graph_captions_readback:graphCaptionsOk,charts:charts,", 1)

        order_anchor = "    var dailyOrderChartMatches=0,dailyOrderChartUpdated=false;"
        order_code = """    /* OZON_GRAPH_LABEL_FRESHNESS_V1 */
    var sellerGraphCaptionSpecs=[
      {cell:'A3',value:'ДНЕВНАЯ ДИНАМИКА · SELLER API · '+(firstDayLabel||'нет начала')+'–'+(lastFullDayLabel||'нет полной даты')+' · только полные дни'},
      {cell:'A39',value:'НЕДЕЛЬНАЯ ДИНАМИКА · Seller API · источник '+agg.minDay+'–'+agg.maxDay+'; крайние недели отмечены в заголовке графика'},
      {cell:'A75',value:'МЕСЯЧНАЯ ДИНАМИКА · Seller API · источник '+agg.minDay+'–'+agg.maxDay+'; крайние месяцы отмечены в заголовке графика'}
    ];
    var sellerGraphCaptionChanges=sellerGraphCaptionSpecs.map(function(spec){
      return {cell:spec.cell,old:String(graphSheet.getRange(spec.cell).getValue()||''),value:spec.value};
    });
"""
        if worker_text.count(order_anchor) != 1:
            raise SystemExit("OZON_GRAPH_LABEL_ORDER_ANCHOR_MISMATCH")
        worker_text = worker_text.replace(order_anchor, order_code + order_anchor, 1)
        order_archive_anchor = "    if(!sameGraphNote)archived+=sellmonitorGithubOzonArchive_(archive,'10_Графики_магазина','A2',[[oldGraphNote]],sourceUpdated,archiveId);"
        if worker_text.count(order_archive_anchor) != 1:
            raise SystemExit("OZON_GRAPH_LABEL_ORDER_ARCHIVE_ANCHOR_MISMATCH")
        worker_text = worker_text.replace(order_archive_anchor,
            order_archive_anchor + "\n    sellerGraphCaptionChanges.forEach(function(x){if(x.old!==x.value)archived+=sellmonitorGithubOzonArchive_(archive,'10_Графики_магазина',x.cell,[[x.old]],sourceUpdated,archiveId);});", 1)
        order_write_anchor = "    if(!sameGraphNote)graphSheet.getRange('A2').setValue(newGraphNote);"
        if worker_text.count(order_write_anchor) != 1:
            raise SystemExit("OZON_GRAPH_LABEL_ORDER_WRITE_ANCHOR_MISMATCH")
        worker_text = worker_text.replace(order_write_anchor,
            order_write_anchor + "\n    sellerGraphCaptionChanges.forEach(function(x){graphSheet.getRange(x.cell).setValue(x.value);});", 1)
        order_readback_anchor = "    var graphNoteReadback=String(graphSheet.getRange('A2').getValue()||'')===newGraphNote;"
        if worker_text.count(order_readback_anchor) != 1:
            raise SystemExit("OZON_GRAPH_LABEL_ORDER_READBACK_ANCHOR_MISMATCH")
        worker_text = worker_text.replace(order_readback_anchor,
            order_readback_anchor + "\n    var graphCaptionReadback=sellerGraphCaptionChanges.every(function(x){return String(graphSheet.getRange(x.cell).getValue()||'')===x.value;});", 1)
        order_gate_anchor = "    if(!graphNoteReadback||!dailyOrderChartReadback||(lastFullDay&&dailyOrderChartMatches!==1))"
        if worker_text.count(order_gate_anchor) != 1:
            raise SystemExit("OZON_GRAPH_LABEL_ORDER_GATE_ANCHOR_MISMATCH")
        worker_text = worker_text.replace(order_gate_anchor,
            "    if(!graphNoteReadback||!graphCaptionReadback||!dailyOrderChartReadback||(lastFullDay&&dailyOrderChartMatches!==1))", 1)
        order_result_anchor = "graph_note_readback:graphNoteReadback,"
        if worker_text.count(order_result_anchor) != 1:
            raise SystemExit("OZON_GRAPH_LABEL_ORDER_RESULT_ANCHOR_MISMATCH")
        worker_text = worker_text.replace(order_result_anchor,
            "graph_note_readback:graphNoteReadback,graph_caption_readback:graphCaptionReadback,", 1)

        graph_versions = re.findall(r"VERSION:\s*'github-worker-(\d+)\.(\d+)\.(\d+)'", worker_text)
        if len(graph_versions) != 1:
            raise SystemExit("OZON_GRAPH_LABEL_VERSION_PATTERN_MISMATCH")
        graph_major,graph_minor,graph_patch = [int(value) for value in graph_versions[0]]
        graph_current = ".".join(str(value) for value in (graph_major,graph_minor,graph_patch))
        graph_next = ".".join(str(value) for value in (graph_major,graph_minor,graph_patch+1))
        graph_version_anchor = "VERSION: 'github-worker-" + graph_current + "'"
        if worker_text.count(graph_version_anchor) != 1:
            raise SystemExit("OZON_GRAPH_LABEL_VERSION_ANCHOR_MISMATCH")
        worker_text = worker_text.replace(graph_version_anchor,"VERSION: 'github-worker-" + graph_next + "'",1)
        print("OZON_GRAPH_LABEL_WORKER_VERSION="+graph_current+"->"+graph_next)
        print("OZON_GRAPH_LABEL_FRESHNESS=patched")
    elif "graphCaptionWrites" not in worker_text or "graphCaptionReadback" not in worker_text or "sellerGraphCaptionChanges" not in worker_text:
        raise SystemExit("OZON_GRAPH_LABEL_FRESHNESS_PATCH_PARTIAL")


    # Read-only shape check for the existing WB raw report and expense APIs.
    # It records units and per-operation totals without item identifiers or raw rows.
    finance_shape_marker = "INNER_FINANCE_RAW_REPORT_FIELD_SHAPE_V8"
    if finance_shape_marker not in worker_text:
        raw_anchor = "            first_row_keys:rawFirst && typeof rawFirst==='object' ? Object.keys(rawFirst).slice(0,40) : []"
        raw_replacement = """            first_row_keys:rawFirst && typeof rawFirst==='object' ? Object.keys(rawFirst).slice(0,40) : [],
            response_meta:(function() {
              /* INNER_FINANCE_RAW_REPORT_FIELD_SHAPE_V8 */
              var envelope=rawReportData && rawReportData.data && typeof rawReportData.data==='object' ? rawReportData.data : {};
              return {
                source:String(rawReportData && rawReportData.source || ''),
                marketplace:String(rawReportData && rawReportData.marketplace || ''),
                partial:rawReportData && rawReportData.partial===true,
                live_fallback_used:rawReportData && rawReportData.liveFallbackUsed===true,
                persisted:rawReportData && rawReportData.persisted===true,
                money_unit:envelope.moneyUnit || '',
                monetary_fields:envelope.monetaryFields || [],
                date_field:envelope.dateField || '',
                time_window:envelope.timeWindow || {},
                deduplication:envelope.deduplication || {},
                warnings_count:Array.isArray(rawReportData && rawReportData.warnings) ? rawReportData.warnings.length : 0
              };
            })(),
            rows_by_doc_type:(function() {
              var sums={}, fields=['quantity','retailAmount','returnAmount','forPay','ppvzForPay','ppvzSalesCommission','deliveryAmount','deliveryRub','retailPriceWithDiscRub'];
              function number_(v) {
                if (typeof v==='number') return isFinite(v)?v:null;
                if (typeof v!=='string' || !v.trim()) return null;
                var n=Number(v.trim().replace(/[\\s\\u00A0]/g,'').replace(',','.'));
                return isFinite(n)?n:null;
              }
              rawReportRows.forEach(function(row) {
                row=row && typeof row==='object'?row:{};
                var kind=String(row.docTypeName || row.supplierOperName || 'UNKNOWN').slice(0,80);
                var b=sums[kind] || (sums[kind]={rows:0,fields:{}});
                b.rows++;
                fields.forEach(function(field) {
                  var item=b.fields[field] || (b.fields[field]={sum:0,numeric_rows:0,missing_rows:0});
                  var value=number_(row[field]);
                  if(value===null)item.missing_rows++;
                  else {item.sum+=value;item.numeric_rows++;}
                });
              });
              return Object.keys(sums).sort().slice(0,24).map(function(kind) {
                var b=sums[kind], fieldsOut={};
                Object.keys(b.fields).forEach(function(field) {
                  var item=b.fields[field];
                  fieldsOut[field]={
                    sum:Math.round(item.sum*10000)/10000,
                    numeric_rows:item.numeric_rows,
                    missing_rows:item.missing_rows
                  };
                });
                return {doc_type:kind,rows:b.rows,fields:fieldsOut};
              });
            })()"""
        if worker_text.count(raw_anchor) != 1:
            raise SystemExit("INNER_FINANCE_RAW_REPORT_FIELD_SHAPE_RAW_ANCHOR_MISMATCH")
        worker_text = worker_text.replace(raw_anchor, raw_replacement, 1)

        expense_anchor = """            first_row_keys:expenseRows.length && expenseRows[0] && typeof expenseRows[0]==='object'
              ? Object.keys(expenseRows[0]).slice(0,32) : []"""
        expense_replacement = """            response_meta:(function() {
              /* INNER_FINANCE_RAW_REPORT_FIELD_SHAPE_V8 */
              return {
                source:String(expenseData && expenseData.source || ''),
                marketplace:String(expenseData && expenseData.marketplace || ''),
                partial:expenseData && expenseData.partial===true,
                live_fallback_used:expenseData && expenseData.liveFallbackUsed===true,
                persisted:expenseData && expenseData.persisted===true,
                money_unit:expenseEnvelope && expenseEnvelope.moneyUnit || '',
                monetary_fields:expenseEnvelope && expenseEnvelope.monetaryFields || [],
                date_field:expenseEnvelope && expenseEnvelope.dateField || '',
                time_window:expenseEnvelope && expenseEnvelope.timeWindow || {},
                warnings_count:Array.isArray(expenseData && expenseData.warnings) ? expenseData.warnings.length : 0
              };
            })(),
            safe_sample:expenseRows.length ? (function() {
              var row=expenseRows[0] && typeof expenseRows[0]==='object'?expenseRows[0]:{};
              return {
                type:String(row.type || ''),group:String(row.group || ''),
                metric:String(row.dashboardMetric || ''),
                role:String(row.dashboardFormulaRole || ''),
                cost:row.cost==null?'':row.cost,
                rows_count:row.rowsCount==null?'':row.rowsCount,
                reliable:row.reliableForAnalysis===true,
                used_in_dashboard:row.usedInDashboard===true,
                contributes_to_dashboard_total:row.contributesToDashboardTotal===true
              };
            })() : null,
            first_row_keys:expenseRows.length && expenseRows[0] && typeof expenseRows[0]==='object'
              ? Object.keys(expenseRows[0]).slice(0,32) : []"""
        if worker_text.count(expense_anchor) != 1:
            raise SystemExit("INNER_FINANCE_RAW_REPORT_FIELD_SHAPE_EXPENSE_ANCHOR_MISMATCH")
        worker_text = worker_text.replace(expense_anchor, expense_replacement, 1)

        raw_key = "SMC_INNER_MCP_RAW_REPORT_PROBE_V2_LAST_MS"
        expense_key = "SMC_INNER_MCP_EXPENSES_PROBE_V6_LAST_MS"
        if worker_text.count(raw_key) != 1 or worker_text.count(expense_key) != 1:
            raise SystemExit("INNER_FINANCE_RAW_REPORT_FIELD_SHAPE_COOLDOWN_KEY_MISMATCH")
        worker_text = worker_text.replace(raw_key, "SMC_INNER_MCP_RAW_REPORT_PROBE_V8_LAST_MS", 1)
        worker_text = worker_text.replace(expense_key, "SMC_INNER_MCP_EXPENSES_PROBE_V8_LAST_MS", 1)

        versions = re.findall(r"VERSION:\s*'github-worker-(\d+)\.(\d+)\.(\d+)'", worker_text)
        if len(versions) != 1:
            raise SystemExit("INNER_FINANCE_RAW_REPORT_FIELD_SHAPE_VERSION_PATTERN_MISMATCH")
        major, minor, patch_no = [int(value) for value in versions[0]]
        current = ".".join(str(value) for value in (major, minor, patch_no))
        next_version = ".".join(str(value) for value in (major, minor, patch_no + 1))
        version_anchor = "VERSION: 'github-worker-" + current + "'"
        if worker_text.count(version_anchor) != 1:
            raise SystemExit("INNER_FINANCE_RAW_REPORT_FIELD_SHAPE_VERSION_ANCHOR_MISMATCH")
        worker_text = worker_text.replace(version_anchor, "VERSION: 'github-worker-" + next_version + "'", 1)
        print("INNER_FINANCE_WORKER_VERSION=" + current + "->" + next_version)
        print("INNER_FINANCE_RAW_REPORT_FIELD_SHAPE_V8=patched")

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
    if lock_anchor in continuation_block:
        continuation_block = continuation_block.replace(lock_anchor, "var lockAcquired = lock.tryLock(30000);", 1)
    elif "lock.tryLock(30000)" not in continuation_block:
        raise SystemExit("DOM continuation lock anchor changed")
    try_start = continuation_block.find("  try {")
    finally_start = continuation_block.find("  } finally {", try_start + 6)
    if try_start < 0 or finally_start < 0:
        raise SystemExit("DOM continuation try/finally boundaries changed")
    recovery_body = """if (!lockAcquired) {
      Logger.log('DOM continuation skipped: script lock is busy.');
      return {ok:false,skipped:'script_lock_busy'};
    }
    var continuationBook = SpreadsheetApp.getActiveSpreadsheet();
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
    continuation_block = continuation_block[:try_start] + "  try {\n    " + recovery_body + "\n" + continuation_block[finally_start:]
    continuation_block = continuation_block.replace(
        "function exportDOM_continue() {",
        "function exportDOM_continue() {\n  /* " + continuation_marker + " */",
        1,
    )
    text = text[:continuation_start] + continuation_block + text[continuation_end:]
    print("OZON_CONTINUATION_RECOVERY=patched")


continuation_lock_marker = "OZON_CONTINUATION_USER_LOCK_V3"
continuation_start = text.find("function exportDOM_continue() {")
continuation_end = text.find("\nfunction ", continuation_start + 10)
if continuation_end < 0:
    continuation_end = len(text)
if continuation_start < 0:
    raise SystemExit("DOM continuation wrapper missing for lock-scope update")
continuation_block = text[continuation_start:continuation_end]
if continuation_lock_marker not in continuation_block:
    lock_call = "LockService.getScriptLock()"
    if continuation_block.count(lock_call) != 1:
        raise SystemExit("Ozon continuation lock-scope anchor changed")
    continuation_block = continuation_block.replace(lock_call, "LockService.getUserLock()", 1)
    continuation_block = continuation_block.replace(
        "Logger.log('DOM continuation skipped: script lock is busy.');",
        "Logger.log('DOM continuation skipped: user lock is busy.');",
        1,
    )
    continuation_block = continuation_block.replace(
        "function exportDOM_continue() {",
        "function exportDOM_continue() {\n  /* " + continuation_lock_marker + " */",
        1,
    )
    text = text[:continuation_start] + continuation_block + text[continuation_end:]
    print("OZON_CONTINUATION_USER_LOCK=patched")

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