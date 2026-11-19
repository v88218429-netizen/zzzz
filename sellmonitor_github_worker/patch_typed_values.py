#!/usr/bin/env python3
"""Narrow, idempotent typed-value normalization for the existing Apps Script worker."""
from pathlib import Path
import re
import sys
if len(sys.argv) != 2:
    raise SystemExit("usage: patch_typed_values.py <apps-script-source-root>")
root = Path(sys.argv[1]).resolve()
path = root / "SELLMONITOR_GITHUB_WORKER.gs"
if not path.is_file():
    raise SystemExit("typed-values worker file missing")
source = path.read_text(encoding="utf-8")
def replace_exact(text, old, new, expected=1, label="anchor"):
    count = text.count(old)
    if count != expected:
        raise SystemExit(label + " changed: " + str(count))
    return text.replace(old, new, expected)
helper = r"""/* SELLMONITOR_TYPED_VALUE_CANONICALIZER_V1 */
function sellmonitorGithubCanonicalValue_(value, kind, timezone) {
  if (value === null || value === undefined) return '';
  if (value instanceof Date) {
    if (!isFinite(value.getTime())) return '';
    return Utilities.formatDate(value, timezone || 'UTC', 'yyyy-MM-dd');
  }
  if (kind === 'date') {
    if (typeof value === 'number' && isFinite(value)) {
      var serial = new Date(Date.UTC(1899,11,30) + Math.floor(value) * 86400000);
      return isFinite(serial.getTime()) ? Utilities.formatDate(serial, 'UTC', 'yyyy-MM-dd') : value;
    }
    if (typeof value === 'string') {
      var dateText = value.trim();
      if (!dateText) return '';
      var isoDate = dateText.match(/^\d{4}-\d{2}-\d{2}(?:$|[ T])/);
      if (isoDate) return isoDate[0].slice(0,10);
      var dmyDate = dateText.match(/^(\d{1,2})\.(\d{1,2})\.(\d{4})(?:[ T].*)?$/);
      if (dmyDate) return dmyDate[3]+'-'+('0'+dmyDate[2]).slice(-2)+'-'+('0'+dmyDate[1]).slice(-2);
    }
    return value;
  }
  if (kind === 'id') {
    if (typeof value === 'number' && !isFinite(value)) return '';
    return String(value).trim();
  }
  if (kind === 'number') {
    if (typeof value === 'number') return isFinite(value) ? (value === 0 ? 0 : value) : '';
    if (typeof value !== 'string') return value;
    var numericText = value.trim();
    if (!numericText) return '';
    var negative = /^\(.*\)$/.test(numericText);
    if (negative) numericText = numericText.slice(1,-1);
    var compact = numericText.replace(/[\s\u00a0\u202f]/g,'').replace(/(?:₽|руб\.?|RUB)/ig,'').replace(/%$/,'');
    var commaCount=(compact.match(/,/g)||[]).length,dotCount=(compact.match(/\./g)||[]).length;
    if (commaCount && dotCount) {
      if (compact.lastIndexOf(',') > compact.lastIndexOf('.')) compact=compact.replace(/\./g,'').replace(',','.');
      else compact=compact.replace(/,/g,'');
    } else if (commaCount > 1) {
      if (/^[+-]?\d{1,3}(?:,\d{3})+$/.test(compact)) compact=compact.replace(/,/g,'');
      else return value;
    } else if (commaCount === 1) {
      compact=compact.replace(',','.');
    } else if (dotCount > 1) {
      if (/^[+-]?\d{1,3}(?:\.\d{3})+$/.test(compact)) compact=compact.replace(/\./g,'');
      else return value;
    }
    if (!/^[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?$/.test(compact)) return value;
    var parsed = Number(compact);
    if (negative) parsed = -parsed;
    return isFinite(parsed) ? (parsed === 0 ? 0 : parsed) : value;
  }
  return value;
}
function sellmonitorGithubCanonicalRow_(row, schema, timezone) {
  var fields = schema || {};
  return (row || []).map(function(value,index) {
    var kind = Array.isArray(fields) ? fields[index] : fields[index];
    return sellmonitorGithubCanonicalValue_(value,kind,timezone);
  });
}
function sellmonitorGithubCanonicalRows_(rows, schema, timezone) {
  return (rows || []).map(function(row) {
    return sellmonitorGithubCanonicalRow_(row,schema,timezone);
  });
}"""
if "SELLMONITOR_TYPED_VALUE_CANONICALIZER_V1" not in source:
    anchor = "function sellmonitorGithubOzonNormalizeRows_(rows, idColumnsAsText) {"
    source = replace_exact(source, anchor, helper + "\n" + anchor, label="canonicalizer insertion")
if "OZON_SUMMARY_TYPED_CANONICALIZER_V1" not in source:
    anchor = "function sellmonitorGithubOzonNormalizeRows_(rows, idColumnsAsText) {"
    start = source.find(anchor)
    end = source.find("\nfunction sellmonitorGithubOzonLastValueRow_", start)
    if start < 0 or end < 0:
        raise SystemExit("Ozon summary normalizer boundary changed")
    normalized = """function sellmonitorGithubOzonNormalizeRows_(rows, idColumnsAsText, schema) {
  /* OZON_SUMMARY_TYPED_CANONICALIZER_V1 */
  var fields = schema || (idColumnsAsText === true ? {3:'id',4:'id'} :
    (idColumnsAsText && typeof idColumnsAsText === 'object' ? idColumnsAsText : {}));
  return sellmonitorGithubCanonicalRows_(rows,fields,SMC_OZON_SUMMARY.timezone);
}
"""
    source = source[:start] + normalized + source[end:]
if "WB_RNP_TYPED_COMPARISON_V1" not in source:
    old = "function norm(a){return a.map(function(r){return r.map(function(v){if(v==null||v==='')return '';if(v instanceof Date)return Utilities.formatDate(v,ss.getSpreadsheetTimeZone(),'yyyy-MM-dd');if(typeof v==='number')return Math.round(v*1000000)/1000000;return v;});});}"
    new = "function norm(a){/* WB_RNP_TYPED_COMPARISON_V1 */return sellmonitorGithubCanonicalRows_(a,['date','number','number','number','number','number'],ss.getSpreadsheetTimeZone());}"
    source = replace_exact(source, old, new, label="WB RNP normalizer")
if "OZON_PERF_SHARED_NUMBER_CANONICALIZER_V1" not in source:
    source = replace_exact(source, "    raw=raw.replace(/,/g,'.');", "    // Numeric separators are handled by the shared typed parser.", label="Performance comma parser")
    number_code = "  /* OZON_PERF_SHARED_NUMBER_CANONICALIZER_V1 */\n  var canonical=sellmonitorGithubCanonicalValue_(raw,'number',SMC_OZON_SUMMARY.timezone);\n  var n=typeof canonical==='number'?canonical:NaN;"
    source = replace_exact(source, "  var n=Number(raw);", number_code, label="Performance numeric conversion")
if "OZON_SELLER_QTY_SHARED_CANONICALIZER_V1" not in source:
    source = replace_exact(source,
        "var offer=String(r.offer||'').trim(),sku=String(r.sku||'').trim(),title=String(r.title||'').trim(),qty=Number(r.qty);",
        "var offer=String(r.offer||'').trim(),sku=String(r.sku||'').trim(),title=String(r.title||'').trim(),qtyValue=sellmonitorGithubCanonicalValue_(r.qty,'number',SMC_OZON_SUMMARY.timezone),qty=typeof qtyValue==='number'?qtyValue:NaN; /* OZON_SELLER_QTY_SHARED_CANONICALIZER_V1 */",
        label="Seller API quantity")
    source = replace_exact(source,
        "var offer=String(row[16]||'').trim(),sku=String(row[15]||'').trim(),title=String(row[17]||'').trim(),qty=Number(row[18]);",
        "var offer=String(row[16]||'').trim(),sku=String(row[15]||'').trim(),title=String(row[17]||'').trim(),qtyValue=sellmonitorGithubCanonicalValue_(row[18],'number',SMC_OZON_SUMMARY.timezone),qty=typeof qtyValue==='number'?qtyValue:NaN; /* OZON_SELLER_QTY_SHARED_CANONICALIZER_V1 */",
        label="Seller export quantity")
if "OZON_TYPED_NORMALIZER_CALL_SITES_V1" not in source:
    anchor = "    var sameDaily=dailyDatesTyped&&JSON.stringify(sellmonitorGithubOzonNormalizeRows_(oldDaily))"
    schemas = """    /* OZON_TYPED_NORMALIZER_CALL_SITES_V1 */
    var ozonDailySchema={1:'date',2:'date',3:'number',4:'number',5:'number',6:'number',7:'number',8:'number'};
    var ozonSkuSchema={1:'date',2:'date',3:'id',4:'id',6:'number',7:'number',8:'number',9:'number'};
    var ozonHelperSchema={0:'date',1:'number',2:'number',3:'number'};
"""
    source = replace_exact(source, anchor, schemas + anchor, label="Ozon typed call-site")
    calls = [
        ("sellmonitorGithubOzonNormalizeRows_(oldDaily)", "sellmonitorGithubOzonNormalizeRows_(oldDaily,null,ozonDailySchema)", 2),
        ("sellmonitorGithubOzonNormalizeRows_(dailyRows)", "sellmonitorGithubOzonNormalizeRows_(dailyRows,null,ozonDailySchema)", 3),
        ("sellmonitorGithubOzonNormalizeRows_(oldSku,true)", "sellmonitorGithubOzonNormalizeRows_(oldSku,null,ozonSkuSchema)", 1),
        ("sellmonitorGithubOzonNormalizeRows_(agg.products,true)", "sellmonitorGithubOzonNormalizeRows_(agg.products,null,ozonSkuSchema)", 2),
        ("sellmonitorGithubOzonNormalizeRows_(oldSkuWithoutTotals,true)", "sellmonitorGithubOzonNormalizeRows_(oldSkuWithoutTotals,null,ozonSkuSchema)", 1),
        ("sellmonitorGithubOzonNormalizeRows_(agg.products.filter(function(row){return String(row[0]||'').indexOf('ИТОГО МАГАЗИНА ·')!==0;}),true)", "sellmonitorGithubOzonNormalizeRows_(agg.products.filter(function(row){return String(row[0]||'').indexOf('ИТОГО МАГАЗИНА ·')!==0;}),null,ozonSkuSchema)", 1),
        ("sellmonitorGithubOzonNormalizeRows_(oldHelper)", "sellmonitorGithubOzonNormalizeRows_(oldHelper,null,ozonHelperSchema)", 2),
        ("sellmonitorGithubOzonNormalizeRows_(helper.slice(1))", "sellmonitorGithubOzonNormalizeRows_(helper.slice(1),null,ozonHelperSchema)", 2),
        ("sellmonitorGithubOzonNormalizeRows_(newDaily)", "sellmonitorGithubOzonNormalizeRows_(newDaily,null,ozonDailySchema)", 1),
        ("sellmonitorGithubOzonNormalizeRows_(newHelper)", "sellmonitorGithubOzonNormalizeRows_(newHelper,null,ozonHelperSchema)", 1),
        ("sellmonitorGithubOzonNormalizeRows_(oldBody,true)", "sellmonitorGithubOzonNormalizeRows_(oldBody,null,ozonSkuSchema)", 1),
        ("sellmonitorGithubOzonNormalizeRows_(viewRows,true)", "sellmonitorGithubOzonNormalizeRows_(viewRows,null,ozonSkuSchema)", 2),
        ("sellmonitorGithubOzonNormalizeRows_(checkedView,true)", "sellmonitorGithubOzonNormalizeRows_(checkedView,null,ozonSkuSchema)", 1),
    ]
    for old, new, count in calls:
        source = replace_exact(source, old, new, count, "Ozon typed comparison call")
if "SELLMONITOR_INNER_TYPED_METRIC_V1" not in source:
    start = source.find("function sellmonitorGithubInnerMetric_(")
    end = source.find("\\nfunction ", start + 1) if start >= 0 else -1
    if start < 0 or end < 0:
        raise SystemExit("Sellmonitor Inner numeric metric function missing")
    body = source[start:end]
    pattern = re.compile(r"(?m)^([ \\t]*)var\\s+number\\s*=\\s*Number\\([^;\\n]*\\)\\s*;\\s*\\n[ \\t]*return\\s+isFinite\\(number\\)\\s*\\?\\s*number\\s*/\\s*\\(divisor\\s*\\|\\|\\s*1\\)\\s*:\\s*''\\s*;")
    replacement = "  /* SELLMONITOR_INNER_TYPED_METRIC_V1 */\\n  var number = sellmonitorGithubCanonicalValue_(value,'number','UTC');\\n  return typeof number === 'number' ? number / (divisor || 1) : '';"
    body, changed = pattern.subn(lambda match: replacement, body, count=1)
    if changed != 1:
        safe_lines = [line.strip() for line in body.splitlines() if "Number(" in line or ("number" in line.lower() and "return" in line.lower())]
        raise SystemExit("Sellmonitor Inner numeric metric shape mismatch:" + "|".join(safe_lines[:4]))
    source = source[:start] + body + source[end:]

if "SELLMONITOR_INNER_TYPED_ID_WRITE_V1" not in source:
    old = "  if (merged.length) sheet.getRange(2,1,merged.length,width).setValues(merged);"
    new = """  /* SELLMONITOR_INNER_TYPED_ID_WRITE_V1 */
  if (merged.length) {
    merged.forEach(function(row) {
      [0,1,6,7,44,45].forEach(function(index) {
        row[index] = row[index] == null ? '' : String(row[index]);
      });
    });
    sheet.getRange(2,1,merged.length,2).setNumberFormat('@');
    sheet.getRange(2,7,merged.length,2).setNumberFormat('@');
    sheet.getRange(2,45,merged.length,2).setNumberFormat('@');
    sheet.getRange(2,1,merged.length,width).setValues(merged);
  }"""
    source = replace_exact(source, old, new, label="Sellmonitor Inner text ID write")

if "OZON_PERF_TYPED_IDEMPOTENCY_V7" not in source:
    new_daily = r"""  /* OZON_PERF_TYPED_IDEMPOTENCY_V7 */
  var dailyCompareSchema={1:'date',3:'id',8:'number',9:'number',10:'number',11:'number',12:'number',13:'number',14:'number',15:'number',16:'number'};
  var periodCompareSchema={1:'date',2:'date',4:'id',9:'number',10:'number',11:'number',12:'number',13:'number',14:'number',15:'number',16:'number',17:'number',18:'number',19:'number',20:'number',21:'number',22:'number'};
  var dailyChanges=[],dailyAdds=[],periodChanges=[],periodAdds=[];
  var dailyMismatchSample=[],periodMismatchSample=[];
  function mismatchSample_(oldValues,newValues,schema,key) {
    var oldRow=sellmonitorGithubCanonicalRow_(oldValues,schema,SMC_OZON_SUMMARY.timezone);
    var newRow=sellmonitorGithubCanonicalRow_(newValues,schema,SMC_OZON_SUMMARY.timezone),differences=[];
    for(var i=0;i<Math.min(oldRow.length,newRow.length);i++){
      if(JSON.stringify(oldRow[i])!==JSON.stringify(newRow[i])){
        differences.push({column:i+1,old_type:oldRow[i]===null?'null':typeof oldRow[i],new_type:newRow[i]===null?'null':typeof newRow[i],old_value:oldRow[i],new_value:newRow[i]});
        if(differences.length>=6)break;
      }
    }
    return {key:key,differences:differences};
  }
  dailyKeys.sort().forEach(function(k){
    var old=oldDaily.map[k],row=dailyMap[k];
    if(!old)dailyAdds.push(row);
    else if(JSON.stringify(sellmonitorGithubCanonicalRow_(old.values,dailyCompareSchema,SMC_OZON_SUMMARY.timezone))!==JSON.stringify(sellmonitorGithubCanonicalRow_(row,dailyCompareSchema,SMC_OZON_SUMMARY.timezone))){
      dailyChanges.push({row:old.row,old:old.values,next:row});
      if(dailyMismatchSample.length<3)dailyMismatchSample.push(mismatchSample_(old.values,row,dailyCompareSchema,k));
    }
  });
  periodKeys.sort().forEach(function(k){
    var old=oldPeriods.map[k],row=periodMap[k];
    if(!old)periodAdds.push(row);
    else if(JSON.stringify(sellmonitorGithubCanonicalRow_(old.values,periodCompareSchema,SMC_OZON_SUMMARY.timezone))!==JSON.stringify(sellmonitorGithubCanonicalRow_(row,periodCompareSchema,SMC_OZON_SUMMARY.timezone))){
      periodChanges.push({row:old.row,old:old.values,next:row});
      if(periodMismatchSample.length<3)periodMismatchSample.push(mismatchSample_(old.values,row,periodCompareSchema,k));
    }
  });"""
    helper_anchor = "\n\n  var helperSpecs=["
    start = source.find("  /* OZON_PERF_DATE_VALUE_IDEMPOTENCY_V6 */")
    if start < 0:
        start = source.find("  /* OZON_PERF_DATE_VALUE_IDEMPOTENCY_V5 */")
    end = source.find(helper_anchor, start) if start >= 0 else -1
    if start < 0 or end < 0:
        raise SystemExit("Ozon Performance V5/V6 comparison block missing")
    source = source[:start] + new_daily + source[end:]
    version = re.search(r"VERSION:\s*'github-worker-(\d+)\.(\d+)\.(\d+)'", source)
    if not version:
        raise SystemExit("worker version missing for typed release")
    major, minor, patch = map(int, version.groups())
    replacement = "VERSION: 'github-worker-" + str(major) + "." + str(minor) + "." + str(patch + 1) + "'"
    source = source[:version.start()] + replacement + source[version.end():]
    old_return = """    daily_sha256:sellmonitorGithubOzonPerfDigest_(dailyKeys.map(function(k){return dailyMap[k];})),
    period_sha256:sellmonitorGithubOzonPerfDigest_(periodKeys.map(function(k){return periodMap[k];}))};"""
    new_return = """    daily_sha256:sellmonitorGithubOzonPerfDigest_(dailyKeys.map(function(k){return dailyMap[k];})),
    period_sha256:sellmonitorGithubOzonPerfDigest_(periodKeys.map(function(k){return periodMap[k];})),
    comparison_sample:{daily:dailyMismatchSample,period:periodMismatchSample}};"""
    if "comparison_sample:{daily:dailyMismatchSample,period:periodMismatchSample}" not in source:
        source = replace_exact(source, old_return, new_return, label="Ozon comparison result")
required = [
    "SELLMONITOR_TYPED_VALUE_CANONICALIZER_V1", "OZON_SUMMARY_TYPED_CANONICALIZER_V1",
    "OZON_TYPED_NORMALIZER_CALL_SITES_V1", "OZON_PERF_SHARED_NUMBER_CANONICALIZER_V1",
    "OZON_SELLER_QTY_SHARED_CANONICALIZER_V1", "WB_RNP_TYPED_COMPARISON_V1",
    "OZON_PERF_TYPED_IDEMPOTENCY_V7", "comparison_sample:{daily:dailyMismatchSample,period:periodMismatchSample}",
    "SELLMONITOR_INNER_TYPED_METRIC_V1", "SELLMONITOR_INNER_TYPED_ID_WRITE_V1",
]
missing = [x for x in required if x not in source]
if missing:
    raise SystemExit("typed worker verification missing: " + ",".join(missing))
path.write_text(source, encoding="utf-8")
print("TYPED_VALUE_NORMALIZATION=patched")
print("TYPED_WORKER_VERSION=" + re.search(r"VERSION:\s*'([^']+)'", source).group(1))
print("TYPED_VALUE_READBACK=source_markers_verified")
print("SELLMONITOR_INNER_TYPED_METRIC=verified")
print("SELLMONITOR_INNER_TYPED_IDS=verified")
