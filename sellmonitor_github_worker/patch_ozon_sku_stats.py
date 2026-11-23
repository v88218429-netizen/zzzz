#!/usr/bin/env python3
from pathlib import Path
import sys

if len(sys.argv) != 2:
    raise SystemExit("usage: patch_ozon_sku_stats.py <apps-script-source-root>")

root = Path(sys.argv[1]).resolve()
path = root / "SELLMONITOR_GITHUB_WORKER.gs"
if not path.is_file():
    raise SystemExit("OZON_SKU_PATCH_TARGET_MISSING")

source = path.read_text(encoding="utf-8")
marker = "OZON_SKU_ITEM_STATS_V1"
fn_anchor = "function sellmonitorGithubPublishOzonPerformanceDaily_(body) {"
route_anchor = "    if (action === 'publish_ozon_performance_daily') {"
route_marker = "    if (action === 'publish_ozon_performance_sku_daily') {"

function_text = r'''
/* OZON_SKU_ITEM_STATS_V1 */
function sellmonitorGithubPublishOzonPerformanceSkuDaily_(body) {
  body=body||{};
  var store=String(body.store||'').trim().toUpperCase();
  var cfg=store==='DOM'?{key:'DOM',name:'ДОМ',id:'1g57et2zqlVfbs0_BoRobb5G-4Z2MTXZd028V7u4Kd-Q'}:
    store==='HOZYAIKA'?{key:'HOZYAIKA',name:'Хозяюшка',id:'1Rl-IVihrZ56wKwDsf76wspZ53a8ntN3hCVN1gd4Tguo'}:null;
  if(!cfg)throw new Error('OZON_SKU_STORE_INVALID');
  var date=String(body.date||'');
  if(!/^\d{4}-\d{2}-\d{2}$/.test(date))throw new Error('OZON_SKU_DATE_INVALID');
  var state=String(body.source_status||'RETRY_REQUIRED');
  var requested=(body.requested_campaign_ids||[]).map(function(x){return String(x);});
  var confirmed=(body.confirmed_campaign_ids||[]).map(function(x){return String(x);});
  var names=body.campaign_names||{};
  var input=Array.isArray(body.sku_rows)?body.sku_rows:[];
  var successSet={};
  confirmed.forEach(function(id){if(!id||successSet[id])throw new Error('OZON_SKU_CAMPAIGN_DUPLICATE');successSet[id]=true;});
  confirmed.forEach(function(id){if(requested.indexOf(id)<0)throw new Error('OZON_SKU_CAMPAIGN_NOT_REQUESTED');});
  if(body.complete===true&&state==='COMPLETE'&&confirmed.length!==requested.length)throw new Error('OZON_SKU_COMPLETION_MISMATCH');
  var ss=SpreadsheetApp.openById(cfg.id);

  var dailyHeader=['Кабинет ERP','Дата','Кабинет','ID кампании','Название кампании','offer_id','SKU Ozon','Наименование','Показы','Переходы','Добавления в корзину','Рекламные заказы, шт.','Рекламные продажи, ₽','Расход, ₽','CTR','CPC, ₽','ДРР','Модельные заказы, шт.','Модельные продажи, ₽','Источник','Состояние данных','Ключ записи'];
  var periodHeader=['Кабинет ERP','Начало периода','Конец периода','Кабинет','ID кампании','Название кампании','offer_id','SKU Ozon','Наименование','Дней с данными','Дней источника','Дней в периоде','Показы','Переходы','Добавления в корзину','Рекламные заказы, шт.','Рекламные продажи, ₽','Расход, ₽','CTR','CPC, ₽','ДРР','Модельные заказы, шт.','Модельные продажи, ₽','Источник','Состояние периода','Ключ записи'];
  var coverageHeader=['Кабинет ERP','Дата','Кабинет','Статус источника','Кампаний запрошено','Кампаний подтверждено','Строк SKU','Коды ошибок','Время обновления','Ключ'];
  var archiveHeader=['Время архива','Лист','Ключ записи','Причина','Предыдущая строка JSON'];

  function ensureRows_(sheet,needed){
    if(sheet.getMaxRows()<needed)sheet.insertRowsAfter(sheet.getMaxRows(),needed-sheet.getMaxRows());
  }
  function table_(name,title,headers){
    var sh=ss.getSheetByName(name);
    if(!sh)sh=ss.insertSheet(name);
    ensureRows_(sh,4);
    if(sh.getLastRow()===0){
      sh.getRange(1,1).setValue(title);
      sh.getRange(2,1).setValue('Источник: официальный Ozon Performance API; отсутствующие значения остаются пустыми.');
      sh.getRange(4,1,1,headers.length).setValues([headers]);
      sh.getRange(1,1,1,headers.length).merge();
      sh.getRange(2,1,1,headers.length).merge();
      sh.getRange(1,1,1,headers.length).setBackground('#17365D').setFontColor('#FFFFFF').setFontWeight('bold').setFontSize(12);
      sh.getRange(2,1,1,headers.length).setBackground('#FFF2CC').setWrap(true);
      sh.getRange(4,1,1,headers.length).setBackground('#1F4E78').setFontColor('#FFFFFF').setFontWeight('bold').setWrap(true);
    }else{
      var actual=sh.getRange(4,1,1,headers.length).getDisplayValues()[0];
      if(JSON.stringify(actual)!==JSON.stringify(headers)){
        if(actual.every(function(v){return v===''||v===null;})&&sh.getLastRow()<=4){sh.getRange(4,1,1,headers.length).setValues([headers]);}
        else throw new Error('OZON_SKU_HEADER_MISMATCH:'+name);
      }
    }
    sh.setFrozenRows(4);
    sh.setHiddenGridlines(true);
    return sh;
  }
  var daily=table_('20_Реклама_SKU_Дни','Ozon: товарная рекламная статистика по дням',dailyHeader);
  var weekly=table_('21_Реклама_SKU_Недели','Ozon: товарная рекламная статистика по неделям',periodHeader);
  var monthly=table_('22_Реклама_SKU_Месяцы','Ozon: товарная рекламная статистика по месяцам',periodHeader);
  var coverage=table_('23_Реклама_SKU_Сбор','Ozon: журнал покрытия статистики по товарам',coverageHeader);
  var archive=ss.getSheetByName('Архив_Реклама_SKU_до_обновления');
  if(!archive)archive=ss.insertSheet('Архив_Реклама_SKU_до_обновления');
  ensureRows_(archive,1);
  if(archive.getLastRow()===0)archive.getRange(1,1,1,archiveHeader.length).setValues([archiveHeader]);
  else if(JSON.stringify(archive.getRange(1,1,1,archiveHeader.length).getDisplayValues()[0])!==JSON.stringify(archiveHeader)){
    var ah=archive.getRange(1,1,1,archiveHeader.length).getDisplayValues()[0];
    if(ah.every(function(v){return v===''||v===null;})&&archive.getLastRow()<=1)archive.getRange(1,1,1,archiveHeader.length).setValues([archiveHeader]);
    else throw new Error('OZON_SKU_ARCHIVE_HEADER_MISMATCH');
  }

  var skuMap={};
  var mapping=ss.getSheetByName('12_API_Заказы_SKU');
  if(mapping&&mapping.getLastRow()>=5){
    mapping.getRange(5,4,mapping.getLastRow()-4,3).getDisplayValues().forEach(function(r){
      var offer=String(r[0]||'').trim(),sku=String(r[1]||'').trim(),product=String(r[2]||'').trim();
      if(!sku)return;
      if(!skuMap[sku])skuMap[sku]={offers:{},names:{}};
      if(offer)skuMap[sku].offers[offer]=true;
      if(product)skuMap[sku].names[product]=true;
    });
  }
  function uniqueMap_(map){
    var keys=Object.keys(map||{});
    return keys.length===1?keys[0]:'';
  }
  function n_(v){
    if(v===null||v===undefined||v==='')return '';
    if(typeof v==='number')return isFinite(v)?v:'';
    var s=String(v).trim().replace(/\s/g,'').replace(',','.');
    if(!s)return '';
    var n=Number(s);
    return isFinite(n)?n:'';
  }
  function ratio_(a,b){
    return a!==''&&b!==''&&Number(b)>0?Number(a)/Number(b):'';
  }
  function now_(){return new Date().toISOString();}
  function keyOf_(row,col){return String(row[col-1]||'').trim();}
  function load_(sheet,width,keyCol){
    var last=sheet.getLastRow(),out={map:{},rows:[]};
    if(last>=5)sheet.getRange(5,1,last-4,width).getValues().forEach(function(row,i){
      var key=keyOf_(row,keyCol);
      if(key){if(out.map[key])throw new Error('OZON_SKU_EXISTING_DUPLICATE:'+sheet.getName()+':'+key);out.map[key]={row:5+i,values:row};out.rows.push({key:key,values:row,row:5+i});}
    });
    return out;
  }
  function archiveRows_(entries){
    if(!entries.length)return;
    var at=archive.getLastRow()+1;ensureRows_(archive,at+entries.length-1);
    archive.getRange(at,1,entries.length,5).setValues(entries);
  }
  function sync_(sheet,width,keyCol,newRows){
    var old=load_(sheet,width,keyCol),next={};
    newRows.forEach(function(row){
      var k=keyOf_(row,keyCol);
      if(!k||next[k])throw new Error('OZON_SKU_NEW_DUPLICATE:'+sheet.getName()+':'+k);
      next[k]=row;
    });
    var archives=[],remove=[];
    old.rows.forEach(function(item){
      var replacement=next[item.key];
      if(!replacement){archives.push([now_(),sheet.getName(),item.key,'Источник подтвердил отсутствие в покрываемой части',JSON.stringify(item.values)]);remove.push(item.row);}
      else if(JSON.stringify(item.values)!==JSON.stringify(replacement))archives.push([now_(),sheet.getName(),item.key,'Заменено подтверждённым повторным ответом API',JSON.stringify(item.values)]);
    });
    archiveRows_(archives);
    remove.sort(function(a,b){return b-a;}).forEach(function(r){sheet.deleteRow(r);});
    var current=load_(sheet,width,keyCol),updates=[],adds=[];
    newRows.forEach(function(row){
      var k=keyOf_(row,keyCol),prior=current.map[k];
      if(!prior)adds.push(row);
      else if(JSON.stringify(prior.values)!==JSON.stringify(row))updates.push({row:prior.row,values:row});
    });
    updates.forEach(function(x){sheet.getRange(x.row,1,1,width).setValues([x.values]);});
    if(adds.length){
      var at=sheet.getLastRow()+1;ensureRows_(sheet,at+adds.length-1);
      sheet.getRange(at,1,adds.length,width).setValues(adds);
    }
    if(sheet.getLastRow()>=5)sheet.getRange(5,1,sheet.getLastRow()-4,width).sort([{column:2,ascending:false},{column:keyCol,ascending:true}]);
    var filter=sheet.getFilter();if(filter)filter.remove();
    var last=sheet.getLastRow();sheet.getRange(4,1,Math.max(1,last-3),width).createFilter();
    return {added:adds.length,changed:updates.length,removed:remove.length};
  }

  var incomingMap={};
  input.forEach(function(x,i){
    if(!x||typeof x!=='object')throw new Error('OZON_SKU_ROW_INVALID:'+String(i+1));
    var d=String(x.date||'').slice(0,10),campaign=String(x.campaignId||'').trim(),sku=String(x.sku||'').trim();
    if(d!==date||!campaign||!sku||!successSet[campaign])throw new Error('OZON_SKU_ROW_KEY_INVALID:'+String(i+1));
    var key=d+'|'+cfg.key+'|'+campaign+'|'+sku;
    if(incomingMap[key])throw new Error('OZON_SKU_ROW_DUPLICATE:'+key);
    var views=n_(x.views),clicks=n_(x.clicks),expense=n_(x.expense),sales=n_(x.sales);
    var mappingItem=skuMap[sku]||{offers:{},names:{}},offer=uniqueMap_(mappingItem.offers),product=uniqueMap_(mappingItem.names);
    var campaignName=String(names[campaign]||'').trim();
    incomingMap[key]=[
      cfg.key,date,cfg.name,campaign,campaignName,offer,sku,product,
      views,clicks,n_(x.toCart),n_(x.orders),sales,expense,
      ratio_(clicks,views),ratio_(expense,clicks),ratio_(expense,sales),
      n_(x.modelOrders),n_(x.modelSales),
      'Ozon Performance API /api/client/statistics/products/sku',
      state==='COMPLETE'?'Финальные данные за предыдущий день по Ozon API':
      state==='COMPLETE_EMPTY'?'Ответ полный, товаров для кампаний за этот день нет':
      state==='NO_CPC_CAMPAIGNS'?'На эту дату CPC-кампаний нет':'Частичная выгрузка: только подтверждённые кампании; будет повторена',
      key
    ];
  });
  var targetRows=Object.keys(incomingMap).sort().map(function(k){return incomingMap[k];});
  var allOld=load_(daily,22,22);
  var toDelete=[];
  if(confirmed.length){
    allOld.rows.forEach(function(item){
      var row=item.values, rowDate=String(row[1]||'').slice(0,10), camp=String(row[3]||'');
      if(rowDate===date&&successSet[camp]&&!incomingMap[item.key])toDelete.push(item.key);
    });
  }
  var retained=allOld.rows.filter(function(item){return toDelete.indexOf(item.key)<0;}).map(function(item){return item.values;});
  // sync_ receives the complete desired daily table so confirmed missing products are removed only for confirmed campaigns/date.
  var keepKeys={};retained.forEach(function(r){keepKeys[String(r[21]||'')]=true;});
  var desired=retained.filter(function(r){return !(String(r[1]||'').slice(0,10)===date&&successSet[String(r[3]||'')]&&!incomingMap[String(r[21]||'')]);});
  targetRows.forEach(function(r){desired.push(r);});
  var daySync=sync_(daily,22,22,desired);

  var errors=(body.sku_errors||[]).map(function(e){return String((e&&e.code)||e||'').slice(0,80);}).filter(String).join(';').slice(0,450);
  var statusNote='Состояние источника '+state+'; дата '+date+'; подтверждено кампаний '+confirmed.length+'/'+requested.length+'; строк товаров '+targetRows.length+(errors?'; коды ошибок: '+errors:'')+'.';
  daily.getRange(2,1).setValue(statusNote);
  weekly.getRange(2,1).setValue('Суммы по товару и кампании из дневных строк; полнота определяется журналом 23_Реклама_SKU_Сбор. '+statusNote);
  monthly.getRange(2,1).setValue('Суммы по товару и кампании из дневных строк; неполный месяц помечается. '+statusNote);
  coverage.getRange(2,1).setValue('Одна строка на магазин и дату; частичный ответ не считается полным покрытием.');
  archive.getRange(1,1,1,archiveHeader.length).setFontWeight('bold').setBackground('#666666').setFontColor('#FFFFFF');

  var covKey=date+'|'+cfg.key;
  var priorCov=load_(coverage,10,10).map[covKey];
  var covRow=[cfg.key,date,cfg.name,state,requested.length,confirmed.length,targetRows.length,errors,now_(),covKey];
  var covRows=load_(coverage,10,10).rows.map(function(x){return x.values;}).filter(function(r){return String(r[9]||'')!==covKey;});
  covRows.push(covRow);
  sync_(coverage,10,10,covRows);

  var dailyRows=load_(daily,22,22).rows.map(function(x){return x.values;});
  var coverageRows=load_(coverage,10,10).rows.map(function(x){return x.values;});
  var goodDates={};
  coverageRows.forEach(function(r){
    if(String(r[0])===cfg.key&&['COMPLETE','COMPLETE_EMPTY','NO_CPC_CAMPAIGNS'].indexOf(String(r[3]))>=0)goodDates[String(r[1]).slice(0,10)]=true;
  });
  function datePlus_(key,n){var p=key.split('-').map(Number);return Utilities.formatDate(new Date(Date.UTC(p[0],p[1]-1,p[2]+n)),'UTC','yyyy-MM-dd');}
  function weekStart_(key){var p=key.split('-').map(Number),d=new Date(Date.UTC(p[0],p[1]-1,p[2]));return datePlus_(key,-((d.getUTCDay()+6)%7));}
  function monthEnd_(key){var p=key.split('-').map(Number);return Utilities.formatDate(new Date(Date.UTC(p[0],p[1],0)),'UTC','yyyy-MM-dd');}
  function buildPeriods_(kind){
    var groups={};
    dailyRows.forEach(function(r){
      if(String(r[0])!==cfg.key)return;
      var d=String(r[1]).slice(0,10),start=kind==='week'?weekStart_(d):d.slice(0,7)+'-01',end=kind==='week'?datePlus_(start,6):monthEnd_(d);
      var k=cfg.key+'|'+start+'|'+String(r[3])+'|'+String(r[6]);
      if(!groups[k])groups[k]={key:k,start:start,end:end,campaign:String(r[3]),title:String(r[4]),offer:String(r[5]),sku:String(r[6]),product:String(r[7]),dates:{},sum:[0,0,0,0,0,0,0,0],unknown:[false,false,false,false,false,false,false,false]};
      var g=groups[k];g.dates[d]=true;
      [8,9,10,11,12,13,17,18].forEach(function(ix,j){var v=r[ix];if(v===''||v===null||v===undefined)g.unknown[j]=true;else g.sum[j]+=Number(v);});
    });
    var out=[];
    Object.keys(groups).forEach(function(k){
      var g=groups[k],daysData=Object.keys(g.dates).length,daysCovered=0,calendar=kind==='week'?7:Math.round((new Date(g.end+'T00:00:00Z')-new Date(g.start+'T00:00:00Z'))/86400000)+1;
      Object.keys(goodDates).forEach(function(d){if(d>=g.start&&d<=g.end)daysCovered++;});
      var full=daysCovered===calendar,views=g.unknown[0]?'':g.sum[0],clicks=g.unknown[1]?'':g.sum[1],carts=g.unknown[2]?'':g.sum[2],orders=g.unknown[3]?'':g.sum[3],sales=g.unknown[4]?'':g.sum[4],spend=g.unknown[5]?'':g.sum[5],modelOrders=g.unknown[6]?'':g.sum[6],modelSales=g.unknown[7]?'':g.sum[7];
      var row=[cfg.key,g.start,g.end,cfg.name,g.campaign,g.title,g.offer,g.sku,g.product,daysData,daysCovered,calendar,
        views,clicks,carts,orders,sales,spend,ratio_(clicks,views),ratio_(spend,clicks),ratio_(spend,sales),
        modelOrders,modelSales,'Ozon Performance API / SKU, дневная история',
        (full?'Покрытие источника полное':'Покрытие источника '+daysCovered+'/'+calendar+' дней')+'; суммы только по фактически полученным строкам',k];
      out.push(row);
    });
    return out.sort(function(a,b){return String(b[1]).localeCompare(String(a[1]))||String(a[7]).localeCompare(String(b[7]))||String(a[4]).localeCompare(String(b[4]));});
  }
  var weekRows=buildPeriods_('week'),monthRows=buildPeriods_('month');
  var weekSync=sync_(weekly,26,26,weekRows),monthSync=sync_(monthly,26,26,monthRows);

  function format_(sh,width,percentCols,moneyCols,countCols){
    var last=sh.getLastRow();
    if(last>=5){
      var n=last-4;
      moneyCols.forEach(function(c){sh.getRange(5,c,n,1).setNumberFormat('#,##0.00');});
      countCols.forEach(function(c){sh.getRange(5,c,n,1).setNumberFormat('#,##0');});
      percentCols.forEach(function(c){sh.getRange(5,c,n,1).setNumberFormat('0.00%');});
    }
    sh.getRange(4,1,1,width).setWrap(true).setVerticalAlignment('middle');
    sh.setRowHeight(4,42);
  }
  format_(daily,22,[15,17],[13,14,16,19],[9,10,11,12,18]);
  format_(weekly,26,[19,21],[17,18,20,23],[10,11,12,13,14,15,16,22]);
  format_(monthly,26,[19,21],[17,18,20,23],[10,11,12,13,14,15,16,22]);
  format_(coverage,10,[],[],[5,6,7]);
  SpreadsheetApp.flush();

  var dailyCheck=load_(daily,22,22),weekCheck=load_(weekly,26,26),monthCheck=load_(monthly,26,26),covCheck=load_(coverage,10,10);
  function exactRows_(loaded,expected){
    if(loaded.rows.length!==expected.length)return false;
    var map={};
    expected.forEach(function(r){var k=String(r[r.length-1]||'');if(!k||map[k])map.__invalid__=true;map[k]=r;});
    if(map.__invalid__)return false;
    return loaded.rows.every(function(x){return map[x.key]&&JSON.stringify(x.values)===JSON.stringify(map[x.key]);});
  }
  var coverageRead=!!covCheck.map[covKey]&&JSON.stringify(covCheck.map[covKey].values)===JSON.stringify(covRow);
  var rangeRead=targetRows.every(function(r){var k=String(r[21]);return dailyCheck.map[k]&&JSON.stringify(dailyCheck.map[k].values)===JSON.stringify(r);});
  var weekRead=exactRows_(weekCheck,weekRows),monthRead=exactRows_(monthCheck,monthRows);
  if(!rangeRead||!coverageRead||!weekRead||!monthRead)throw new Error('OZON_SKU_READBACK_FAILED:'+cfg.key);
  return {ok:true,action:'publish_ozon_performance_sku_daily',store:cfg.name,date:date,source_status:state,
    source_rows:input.length,daily_added:daySync.added,daily_changed:daySync.changed,daily_removed:daySync.removed,
    weekly_rows:weekRows.length,weekly_added:weekSync.added,weekly_changed:weekSync.changed,
    monthly_rows:monthRows.length,monthly_added:monthSync.added,monthly_changed:monthSync.changed,
    daily_readback:true,weekly_readback:weekRead,
    monthly_readback:monthRead,coverage_readback:coverageRead,
    archive_rows:archive.getLastRow()-1,sku_readback:rangeRead};
}
'''

if marker not in source:
    if source.count(fn_anchor) != 1:
        raise SystemExit("OZON_SKU_FUNCTION_ANCHOR_MISMATCH")
    source = source.replace(fn_anchor, function_text + "\n\n" + fn_anchor, 1)
if route_marker not in source:
    if source.count(route_anchor) != 1:
        raise SystemExit("OZON_SKU_ROUTE_ANCHOR_MISMATCH")
    route = """    if (action === 'publish_ozon_performance_sku_daily') {
      return sellmonitorGithubWebJson_(sellmonitorGithubPublishOzonPerformanceSkuDaily_(body));
    }

"""
    source = source.replace(route_anchor, route + route_anchor, 1)
if marker not in source or route_marker not in source:
    raise SystemExit("OZON_SKU_PATCH_NOT_APPLIED")
path.write_text(source, encoding="utf-8")
print("OZON_SKU_PATCH_OK: function and route ready")
