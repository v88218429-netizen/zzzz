const state = { data: null, eventFilter: 'important', inventoryFilter: 'shortage', decisionFilter: 'priority', page: location.hash.replace('#','') || 'overview', periodFrom: null, periodTo: null, periodAuditRequestedKey: null, periodPollTimer: null };
const $ = (id) => document.getElementById(id);
const qa = (sel, root=document) => [...root.querySelectorAll(sel)];

const agentNames = {
  api_health: 'Состояние API', cards: 'Карточки', advertising_monitor: 'Реклама · монитор', advertising_optimizer: 'Реклама · диагностика',
  search_positions: 'Поисковые позиции', inventory: 'Остатки', supply: 'Поставки', funnel: 'Воронка', price_margin: 'Цена и маржа',
  finance: 'Финансы', cost_guard: 'Контроль расходов', reviews_questions: 'Отзывы и вопросы', buyer_chats: 'Чаты покупателей',
  orders_fbs: 'FBS-заказы', returns_quality: 'Возвраты и качество', documents: 'Документы', competitors: 'Конкуренты', experiments: 'Эксперименты', supervisor: 'Главный управляющий', portal_news: 'Новости WB', seller_services: 'Джем и опции'
};
const agentDescriptions = {
  api_health:'Проверяет токен, WB API и качество источников; отсутствие данных не превращает в ноль.',
  cards:'Ловит ошибки карточек, блокировки и ценовой карантин; передаёт факты центральной экономике.',
  advertising_monitor:'Даёт факт рекламы по campaign×nmID: расход, заказы, ДРР, CTR и CPC за сопоставимый период.',
  advertising_optimizer:'Предлагает рекламный тест только после сверки фактического ДРР с PRIMARY-юниткой и запасом.',
  search_positions:'Контролирует позиции и частотность как возможные причины изменения спроса.',
  inventory:'Считает риск дефицита/излишка по фактическому спросу; экономика SKU берётся только из PRIMARY.',
  supply:'Ищет окна приёмки и пополнение с учётом ценности товара и риска stockout.',
  funnel:'Даёт независимый факт трафика, конверсии, заказов и выкупов для объяснения отклонения экономики.',
  price_margin:'Контролирует фактическую цену и акции; плановую прибыль не считает фактом.',
  finance:'Проверяет плановую модель закрытой реализацией WB: комиссии, логистика, налоги, удержания и итоговый факт.',
  cost_guard:'Ищет фактические расходы, которые объясняют расхождение PRIMARY-плана и реализации.',
  reviews_questions:'Ищет клиентские причины просадки конверсии и выкупа.',
  buyer_chats:'Следит за сообщениями покупателей как источником причин возвратов/проблем товара.',
  orders_fbs:'Контролирует исполнение FBS, которое может искажать продажи, остатки и финансовый факт.',
  returns_quality:'Ищет возвраты и качество как причины расхождения планового выкупа с фактом.',
  documents:'Контролирует финансовые документы для окончательного подтверждения реализованной экономики.',
  competitors:'Наблюдает изменения конкурентов как внешнюю причину цены, спроса и рекламной эффективности.',
  experiments:'Проверяет гипотезы plan↔fact на следующих окнах и не принимает корреляцию за причинность.',
  supervisor:'Арбитр основной оси: PRIMARY-план → независимый факт → отклонение → причина → действие.',
  portal_news:'Читает новости портала продавца, сохраняет первоисточник и отмечает возможную связь с работой магазина.',
  seller_services:'Сверяет статус «Джем» и активные платные опции с API WB. Ничего не подключает и не отключает.'
};
const domainAgents = {
  'Реклама':['advertising_monitor','advertising_optimizer'], 'Остатки':['inventory','supply'], 'Поиск':['search_positions','funnel'],
  'Карточки':['cards','price_margin'], 'Финансы':['finance','cost_guard','documents'], 'Клиенты':['reviews_questions','buyer_chats','returns_quality'], 'Новости WB':['portal_news'], 'Тарифы и Джем':['seller_services']
};
const agentDomain = Object.fromEntries(Object.entries(domainAgents).flatMap(([domain,agents])=>agents.map(a=>[a,domain])));
const domainRequiredSnapshots = {
  'Реклама':[['advertising_monitor','active_campaigns']],
  'Остатки':[['inventory','coverage']],
  'Поиск':[['search_positions','positions']],
  'Карточки':[['cards','card_catalog']],
  'Финансы':[['finance','balance'],['finance','worker_finance']],
  'Клиенты':[['reviews_questions','seller_rating'],['buyer_chats','chat_events']],
  'Новости WB':[['portal_news','news']],
  'Тарифы и Джем':[['seller_services','jam_subscription'],['seller_services','tariff_options']]
};

function esc(v){ return String(v ?? '').replace(/[&<>"']/g, m => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#039;'}[m])); }
function missing(v){ return v===null || v===undefined || v==='' || (typeof v==='string' && !v.trim()); }
function num(v, digits=0){ if(missing(v)) return '—'; const n=Number(v); return Number.isFinite(n) ? n.toLocaleString('ru-RU',{maximumFractionDigits:digits,minimumFractionDigits:digits}) : '—'; }
function rub(v){ if(missing(v)) return '—'; const n=Number(v); return Number.isFinite(n) ? `${n.toLocaleString('ru-RU',{maximumFractionDigits:0})} ₽` : '—'; }
function pct(v, digits=1){ if(missing(v)) return '—'; const n=Number(v); return Number.isFinite(n) ? `${n.toLocaleString('ru-RU',{maximumFractionDigits:digits,minimumFractionDigits:digits})}%` : '—'; }
function ago(iso){
  if(!iso) return 'нет данных'; const d=new Date(iso); if(Number.isNaN(d.getTime())) return '—'; const s=Math.max(0,(Date.now()-d.getTime())/1000);
  if(s<60) return 'только что'; if(s<3600) return `${Math.floor(s/60)} мин назад`; if(s<86400) return `${Math.floor(s/3600)} ч назад`; return `${Math.floor(s/86400)} дн назад`;
}
function time(iso){ if(!iso) return '—'; const d=new Date(iso); return Number.isNaN(d.getTime()) ? '—' : d.toLocaleString('ru-RU',{day:'2-digit',month:'2-digit',hour:'2-digit',minute:'2-digit'}); }
function severityIcon(s){ return s==='critical'?'!':s==='warning'?'•':'i'; }
function statusClass(n, thresholds={warn:12,bad:20}){ if(!Number.isFinite(n)) return 'neutral'; if(n>=thresholds.bad) return 'bad'; if(n>=thresholds.warn) return 'warn'; return 'ok'; }

function scopeName(v){ const m={campaign_sku:'Реклама · товар',campaign:'Реклама',sku:'Товар',physical_product:'Физический товар',store:'Магазин',system:'Система',operating_model:'Модель управления'}; return m[String(v||'')]||String(v||'—'); }
function changeTypeName(v){ const m={advert_bid:'Ставка рекламы',price:'Цена',card_media:'Медиа карточки'}; return m[String(v||'')]||String(v||'—'); }
function historyStatusName(v){ const m={recommended:'Рекомендовано',observed_applied:'Фактически применено'}; return m[String(v||'')]||String(v||'—'); }
function sourceHealthName(v){ const m={fresh:'Актуально',ok:'Актуально',stale:'Устарело',broken:'Ошибка',unknown:'Не проверено'}; return m[String(v||'').toLowerCase()]||String(v||'—'); }
function extractList(obj, depth=0){
  if(Array.isArray(obj)) return obj;
  if(!obj || typeof obj!=='object' || depth>4) return [];
  for(const k of ['data','items','campaigns','adverts','products','feedbacks','questions','claims','orders','events']){
    if(Array.isArray(obj[k])) return obj[k];
  }
  for(const k of ['data','result','response']){
    if(obj[k] && typeof obj[k]==='object'){
      const nested=extractList(obj[k],depth+1);
      if(nested.length) return nested;
    }
  }
  return [];
}
function showToast(msg, kind=''){ const t=$('toast'); t.textContent=msg; t.className=`toast show ${kind}`; clearTimeout(showToast.t); showToast.t=setTimeout(()=>t.className='toast',3200); }
function empty(text){ return `<div class="empty">${esc(text)}</div>`; }
const specialEntityNames={reshipment:'Повторная отгрузка FBS'};
function entityNmId(id){
  if(missing(id)) return null;
  const raw=String(id).trim(), map=state.data?.entity_map||{};
  if(map[raw]) return raw;
  const nums=raw.match(/\d{8,12}/g)||[];
  for(let i=nums.length-1;i>=0;i--) if(map[nums[i]]) return nums[i];
  return null;
}
function entityInfo(id){ const nm=entityNmId(id); return nm ? state.data?.entity_map?.[nm]||null : null; }
function entityBySellerArticle(article){
  const a=String(article||'').trim(); if(!a) return null;
  return Object.values(state.data?.entity_map||{}).find(x=>String(x?.seller_article||'').trim()===a)||null;
}
function entityName(id){
  const raw=String(id??'').trim();
  if(specialEntityNames[raw]) return specialEntityNames[raw];
  const x=entityInfo(id);
  return x?.seller_article || x?.display || (missing(id)?'—':raw);
}
function entityMeta(id){
  const x=entityInfo(id);
  if(!x) return '';
  const group=x.group_name||x.weekly_group;
  return `${group?`группа: ${esc(group)}`:''}${x.ozon_seller_article?`${group?' · ':''}Ozon: ${esc(x.ozon_seller_article)}`:''}`;
}
function activeCampaignRows(){ return extractList(snap('advertising_monitor','active_campaigns')?.data); }
function campaignInfo(id){
  const target=String(id||'');
  return activeCampaignRows().find(r=>String(r?.id??r?.advertId??r?.advert_id??'')===target)||null;
}
function campaignLabel(id){
  const c=campaignInfo(id)||{}, name=String(c?.settings?.name||c?.name||'').trim();
  const nms=extractList(c?.nm_settings||c?.nmSettings||[]).map(x=>x?.nm_id??x?.nmId??x?.nmID).filter(x=>!missing(x));
  const articles=[...new Set(nms.map(entityName).filter(x=>x&&x!=='—'))];
  if(articles.length) return `${articles.slice(0,2).join(', ')}${articles.length>2?` +${articles.length-2}`:''}${name?` · ${name}`:''}`;
  return name || `кампания ${id}`;
}
function entityLabel(id, scope=''){
  const s=String(scope||'');
  if(s==='campaign') return campaignLabel(id);
  return entityName(id);
}
function entityMetaScoped(id, scope=''){
  const s=String(scope||''), base=entityMeta(id);
  if(s==='campaign') return missing(id)?'':`кампания ID ${id}`;
  if(s==='campaign_sku'){
    const m=String(id||'').match(/^(\d{4,12}):/);
    return `${base}${base&&m?' · ':''}${m?`кампания ID ${m[1]}`:''}`;
  }
  return base;
}
function humanizeText(value){
  let text=String(value??'');
  text=text.replace(/MCP tool not available:\s*[a-z0-9_]+/gi,'Источник WB временно недоступен');
  text=text.replace(/ReadTimeout:?/gi,'Источник WB не ответил вовремя');
  text=text.replace(/nmID\s*(\d{8,12})/gi,(_,id)=>{ const n=entityName(id); return n!==String(id)?n:`WB ID ${id}`; });
  text=text.replace(/NMID\s*(\d{8,12})/g,(_,id)=>{ const n=entityName(id); return n!==String(id)?n:`WB ID ${id}`; });
  text=text.replace(/\b(\d{7,10}):(\d{8,12})\b/g,(m,campaign,id)=>{ const n=entityName(id); return n!==String(id)?`кампания ${campaign} · ${n}`:m; });
  text=text.replace(/Реклама\s*#(\d+)/gi,(_,id)=>`Реклама · ${campaignLabel(id)}`);
  text=text.replace(/\bnmID\b/gi,'карточка WB');
  return text;
}
function eventEntity(e){
  const p=e?.payload||{};
  const campaignNm=Array.isArray(p.nm_ids)?p.nm_ids.find(x=>!missing(x)):null;
  const nm=p.nm_id??p.nmId??p.nmID??campaignNm;
  if(!missing(nm)) return {id:String(nm),name:entityName(nm),meta:entityMeta(nm)};
  const items=Array.isArray(p.items)?p.items:[];
  const article=p.vendor_code||p.vendorCode||items[0]?.vendor_code||items[0]?.vendorCode;
  const x=entityBySellerArticle(article);
  return article?{id:x?.nm_id||'',name:String(article),meta:x?entityMeta(x.nm_id):'артикул продавца'}:null;
}
function domainDataQuality(name,agents){
  if(name==='Остатки' && !state.data?.health?.sheets_connected) return {ok:false,text:'Не сверено с ФФ/K2 · Sheets не подключены'};
  if(name==='Тарифы и Джем'){
    const health=snap('seller_services','service_health')?.data||{};
    const missing=['jam_subscription','tariff_options'].filter(key=>health[key]?.status!=='fresh');
    if(missing.length) return {ok:false,text:`Нет доступа к источникам: ${missing.length}`};
  }
  const req=domainRequiredSnapshots[name]||[];
  if(req.length && !req.some(([a,k])=>!!snap(a,k))) return {ok:false,text:'Нет подтверждённого снимка данных'};
  const stale=[];
  for(const a of agents