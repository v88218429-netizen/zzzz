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
        var state=!fullyCovered?'Неполный диапазон источника':(!completed?'Предварительно · незавершённый период':'Полный календарный период · снимок API');
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
      sh.getRange(1,1,rows.length,width).setValues(rows);
      sh.setFrozenRows(1);sh.setHiddenGridlines(true);
      sh.getRange(1,1,1,width).setBackground('#17365D').setFontColor('#FFFFFF').setFontWeight('bold').setWrap(true);
      if(rows.length>1){
        sh.getRange(2,2,rows.length-1,Math.min(5,width-1)).setNumberFormat('#,##0.00;[Red]-#,##0.00;–');
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
    var at=journal.getLastRow()+1;sellmonitorGithubOzonEnsureRows_(journal,at);journal.getRange(at,1,1,row.length).setValues([row]);
    var check=journal.getRange(at,1,1,row.length).getValues()[0];
    if(JSON.stringify(check)!==JSON.stringify(row))throw new Error('OZON_FINANCE_JOURNAL_READBACK_FAILED:'+cfg.name);
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

    # Keep period-matrix freshness labels tied to the actual verified source and protect the prior cell value.
    freshness_marker = "WB_RNP_FRESHNESS_AND_AXIS_READBACK_V1"
    freshness_anchor = "    stats.push({sheet:spec.name,target_periods:targets.length,updated_columns:updates.length,archived_segments:archives,written_values:written,duplicates_removed:dupDates.length,min:targets[0]||'',max:targets.length?targets[targets.length-1]:''});"
    if freshness_marker not in worker_text:
        if worker_text.count(freshness_anchor) != 1:
            raise SystemExit("WB RNP matrix stats anchor mismatch")
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
