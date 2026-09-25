const state = { data: null, eventFilter: 'important', page: location.hash.replace('#','') || 'overview', periodFrom: null, periodTo: null };
const $ = (id) => document.getElementById(id);
const qa = (sel, root=document) => [...root.querySelectorAll(sel)];

const agentNames = {
  api_health: 'Состояние API', cards: 'Карточки', advertising_monitor: 'Реклама · монитор', advertising_optimizer: 'Реклама · диагностика',
  search_positions: 'Поисковые позиции', inventory: 'Остатки', supply: 'Поставки', funnel: 'Воронка', price_margin: 'Цена и маржа',
  finance: 'Финансы', cost_guard: 'Контроль расходов', reviews_questions: 'Отзывы и вопросы', buyer_chats: 'Чаты покупателей',
  orders_fbs: 'FBS-заказы', returns_quality: 'Возвраты и качество', documents: 'Документы', competitors: 'Конкуренты', experiments: 'Эксперименты', supervisor: 'Главный управляющий'
};
const agentDescriptions = {
  api_health:'Проверяет токен, доступность WB API и деградации.', cards:'Ловит ошибки, блокировки и ценовой карантин.', advertising_monitor:'Следит за расходом, заказами, ДРР, CTR и CPC.', advertising_optimizer:'Формирует рекомендации по проблемной рекламе.', search_positions:'Контролирует позиции по поисковым запросам.', inventory:'Считает дни запаса и риск дефицита.', supply:'Ищет доступные и выгодные окна приёмки.', funnel:'Следит за конверсиями карточки и заказами.', price_margin:'Контролирует цены, акции и риск потери маржи.', finance:'Читает баланс и отчёт реализации.', cost_guard:'Ищет хранение, удержания, штрафы и дорогую приёмку.', reviews_questions:'Отслеживает неотвеченные отзывы и вопросы.', buyer_chats:'Следит за новыми сообщениями покупателей.', orders_fbs:'Контролирует новые FBS-заказы и повторную отгрузку.', returns_quality:'Ищет рост возвратов и системные причины.', documents:'Контролирует появление финансовых документов.', competitors:'Наблюдает за заданными конкурентами.', experiments:'Контролирует эксперименты и периоды наблюдения.', supervisor:'Сводит сигналы всех агентов в одну картину.'
};
const domainAgents = {
  'Реклама':['advertising_monitor','advertising_optimizer'], 'Остатки':['inventory','supply'], 'Поиск':['search_positions','funnel'],
  'Карточки':['cards','price_margin'], 'Финансы':['finance','cost_guard','documents'], 'Клиенты':['reviews_questions','buyer_chats','returns_quality']
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

function scopeName(v){ const m={campaign_sku:'Реклама · товар',campaign:'Реклама',sku:'Товар',store:'Магазин',system:'Система',operating_model:'Модель управления'}; return m[String(v||'')]||String(v||'—'); }
function changeTypeName(v){ const m={advert_bid:'Ставка рекламы',price:'Цена',card_media:'Медиа карточки'}; return m[String(v||'')]||String(v||'—'); }
function historyStatusName(v){ const m={recommended:'Рекомендовано',observed_applied:'Фактически применено'}; return m[String(v||'')]||String(v||'—'); }
function sourceHealthName(v){ const m={fresh:'Актуально',ok:'Актуально',stale:'Устарело',broken:'Ошибка',unknown:'Не проверено'}; return m[String(v||'').toLowerCase()]||String(v||'—'); }
function extractList(obj){
  if(Array.isArray(obj)) return obj;
  if(!obj || typeof obj!=='object') return [];
  for(const k of ['data','items','campaigns','adverts','products','feedbacks','questions','claims','orders','events']) if(Array.isArray(obj[k])) return obj[k];
  return [];
}
function showToast(msg, kind=''){ const t=$('toast'); t.textContent=msg; t.className=`toast show ${kind}`; clearTimeout(showToast.t); showToast.t=setTimeout(()=>t.className='toast',3200); }
function empty(text){ return `<div class="empty">${esc(text)}</div>`; }
function entityInfo(id){ return state.data?.entity_map?.[String(id)] || null; }
function entityName(id){ const x=entityInfo(id); return x?.seller_article || x?.display || (missing(id)?'—':String(id)); }
function entityMeta(id){ const x=entityInfo(id); return x ? `nmID ${esc(x.nm_id)}${x.weekly_group?` · ${esc(x.weekly_group)}`:''}` : (missing(id)?'':`nmID ${esc(id)}`); }
function currentPeriodLabel(){ return state.data?.period?.label || 'выбранный период'; }
function dateInSelectedRange(value){
  if(!value) return false;
  const raw=String(value).slice(0,10);
  const d=new Date(raw+'T00:00:00');
  if(Number.isNaN(d.getTime())) return false;
  const from=state.data?.period?.from ? new Date(state.data.period.from+'T00:00:00') : null;
  const to=state.data?.period?.to ? new Date(state.data.period.to+'T23:59:59') : null;
  return (!from || d>=from) && (!to || d<=to);
}
function flattenFacts(obj, prefix='', out=[]){
  if(out.length>=30 || obj==null) return out;
  if(Array.isArray(obj)){ obj.slice(0,12).forEach((v,i)=>flattenFacts(v,`${prefix}[${i}]`,out)); return out; }
  if(typeof obj==='object'){ Object.entries(obj).slice(0,30).forEach(([k,v])=>flattenFacts(v,prefix?`${prefix} · ${k}`:k,out)); return out; }
  if(String(obj).trim()) out.push([prefix,String(obj)]);
  return out;
}
function openDetail(kicker,title,body){
  $('detail-kicker').textContent=kicker||'Подробности';
  $('detail-title').textContent=title||'—';
  $('detail-body').innerHTML=body||empty('Нет дополнительных данных.');
  $('detail-modal').classList.add('open');
  $('detail-modal').setAttribute('aria-hidden','false');
}
function closeDetail(){ $('detail-modal').classList.remove('open'); $('detail-modal').setAttribute('aria-hidden','true'); }
function eventDetail(e){
  const payload=e?.payload||{};
  const rows=extractList(payload?.data ?? payload);
  let body=`<div class="detail-lead"><p>${esc(e.message||'')}</p><div class="detail-meta"><span>${esc(agentNames[e.agent]||e.agent)}</span><span>${time(e.created_at)}</span></div></div>`;
  if(e.event_key==='reshipment' && rows.length){
    body+=`<div class="detail-section"><h3>Конкретные заказы на повторную отгрузку</h3><div class="detail-table">${rows.slice(0,100).map((r,i)=>{
      const nm=r.nmId??r.nmID??r.nm_id; const art=r.vendorCode??r.supplierArticle??r.article??entityName(nm);
      const order=r.orderId??r.order_id??r.id??r.srid??'—'; const supply=r.supplyId??r.supply_id??r.supply??'—'; const qty=r.quantity??r.qty??1;
      return `<div class="detail-row"><b>${i+1}. ${esc(art||entityName(nm))}</b><span>Заказ ${esc(order)} · поставка ${esc(supply)} · ${esc(qty)} шт${nm?` · nmID ${esc(nm)}`:''}</span></div>`;
    }).join('')}</div></div>`;
  } else {
    const facts=flattenFacts(payload);
    if(facts.length) body+=`<div class="detail-section"><h3>Исходные факты</h3><div class="detail-facts">${facts.map(([k,v])=>`<div><span>${esc(k)}</span><strong>${esc(v)}</strong></div>`).join('')}</div></div>`;
  }
  const related=(state.data?.decisions||[]).filter(d=>{
    const hay=JSON.stringify(d); const needle=String(e.event_key||'');
    return (needle && hay.includes(needle)) || (e.message && hay.includes(String(e.message).slice(0,40)));
  }).slice(0,5);
  if(related.length) body+=`<div class="detail-section"><h3>Связанные решения</h3>${related.map(d=>`<button class="detail-link" data-decision-key="${esc(d.decision_key)}">${esc(d.title)}</button>`).join('')}</div>`;
  return body;
}
function decisionDetail(d){
  const acts=(d.recommended_actions||[]).map((a,i)=>`<div class="detail-step"><b>${i+1}</b><span>${esc(a.action)}</span></div>`).join('');
  const ev=(d.evidence||[]).map(x=>`<div><span>${esc(x.metric)} · ${esc(x.source)}</span><strong>${esc(x.value)}</strong>${x.note?`<small>${esc(x.note)}</small>`:''}</div>`).join('');
  const blockers=(d.blockers||[]).map(x=>`<li>${esc(x)}</li>`).join('');
  return `<div class="detail-lead"><div class="detail-entity"><strong>${esc(entityName(d.entity_id))}</strong><span>${entityMeta(d.entity_id)}</span></div><p>${esc(d.diagnosis||'')}</p></div>
    ${blockers?`<div class="detail-section danger-box"><h3>Почему решение ограничено</h3><ul>${blockers}</ul></div>`:''}
    <div class="detail-section"><h3>Что делать</h3><div class="detail-steps">${acts||empty('Конкретного действия пока нет.')}</div></div>
    <div class="detail-section"><h3>На каких фактах основано</h3><div class="detail-facts">${ev||'<div><span>Факты</span><strong>Недостаточно данных</strong></div>'}</div></div>
    <div class="detail-section"><h3>Когда проверить результат</h3><p>${esc(d.follow_up||'—')}</p></div>`;
}
function productDetail(id){
  const prod=(state.data?.portfolio?.own_27?.products||[]).find(x=>String(x.sku)===String(id))||{};
  const decisions=(state.data?.decisions||[]).filter(d=>String(d.entity_id)===String(id)).slice(0,10);
  const facts=[
    ['Артикул продавца',entityName(id)],['nmID',id],['Цена клиенту',rub(prod.price_client_rub)],['Прибыль на единицу',rub(prod.profit_rub)],
    ['Маржа',pct(prod.margin_pct)],['ДРР',pct(prod.drr_pct)],['Остаток',num(prod.safe_stock)],['Источник остатка',prod.safe_stock_source||'—'],
    ['Заказов в день',num(prod.orders_per_day,1)],['Заказы, ₽',rub(prod.orders_rub)]
  ];
  let body=`<div class="detail-section"><h3>Факты по товару</h3><div class="detail-facts">${facts.map(([k,v])=>`<div><span>${esc(k)}</span><strong>${esc(v)}</strong></div>`).join('')}</div></div>`;
  if(decisions.length) body+=`<div class="detail-section"><h3>Текущие решения</h3>${decisions.map(d=>`<button class="detail-link" data-decision-key="${esc(d.decision_key)}">${esc(d.title)}</button>`).join('')}</div>`;
  return body;
}

function initNav(){
  qa('.nav-item').forEach(btn=>btn.addEventListener('click',()=>setPage(btn.dataset.page)));
  $('mobile-menu').addEventListener('click',()=> $('sidebar').classList.toggle('open'));
  const filters=$('event-filter'); if(filters) filters.addEventListener('click',e=>{ if(!e.target.dataset.filter) return; state.eventFilter=e.target.dataset.filter; qa('button',filters).forEach(b=>b.classList.toggle('active',b===e.target)); renderEvents(); });
  setPage(state.page);
}
function setPage(page){
  if(!document.querySelector(`[data-page-panel="${page}"]`)) page='overview'; state.page=page; location.hash=page==='overview'?'':page;
  qa('.nav-item').forEach(b=>b.classList.toggle('active',b.dataset.page===page)); qa('.page').forEach(p=>p.classList.toggle('active',p.dataset.pagePanel===page));
  const titles={overview:['Операционный центр','Обзор магазина'],stores:['Портфель','Магазины и реальные факты'],decisions:['Решения','Что конкретно делать'],control:['Контроль решений','История, проверка и обучение'],advertising:['Реклама','Рекламная аналитика'],policy:['Редактор правил','Управление логикой агента'],inventory:['Логистика','Остатки и поставки'],search:['Видимость','Поиск и карточки'],finance:['Экономика','Финансы и расходы'],customers:['Качество','Клиенты и возвраты'],connections:['Интеграции','Подключения и источники'],knowledge:['Методология','База знаний и правила'],agents:['Система','Агенты и расписание']};
  const t=titles[page]||titles.overview; $('page-kicker').textContent=t[0]; $('page-title').textContent=t[1]; $('sidebar').classList.remove('open');
}

async function fetchData(showLoader=false){
  if(showLoader) $('loading-state').classList.remove('hidden');
  try{
    const params=new URLSearchParams(); if(state.periodFrom) params.set('from_date',state.periodFrom); if(state.periodTo) params.set('to_date',state.periodTo);
    const r=await fetch('/api/dashboard-data'+(params.size?`?${params.toString()}`:''),{cache:'no-store'}); if(!r.ok) throw new Error(`HTTP ${r.status}`); state.data=await r.json();
    if(!state.periodFrom && state.data?.period?.from) state.periodFrom=state.data.period.from;
    if(!state.periodTo && state.data?.period?.to) state.periodTo=state.data.period.to;
    if($('period-from')) $('period-from').value=state.periodFrom||''; if($('period-to')) $('period-to').value=state.periodTo||'';
    renderAll(); $('loading-state').classList.add('hidden');
  }catch(e){ $('loading-state').classList.remove('hidden'); $('loading-state').innerHTML=`<div class="event-severity critical">!</div><div><strong>Не удалось получить данные</strong><span>${esc(e.message)}. Проверь, что WB AI Manager запущен.</span></div>`; }
}

async function runAudit(){
  const b=$('run-audit'); if(b.disabled) return; b.disabled=true; const old=b.innerHTML; b.innerHTML='<span class="spinner" style="width:14px;height:14px;border-color:rgba(255,255,255,.35);border-top-color:white"></span> Проверяю…';
  showToast('Запущена полная безопасная проверка. Никакие изменения в WB не выполняются.');
  try{ const r=await fetch('/run-all',{method:'POST'}); if(!r.ok) throw new Error(`HTTP ${r.status}`); await r.json(); showToast('Проверка завершена. Данные обновлены.','success'); await fetchData(); }
  catch(e){ showToast(`Ошибка проверки: ${e.message}`,'error'); }
  finally{ b.disabled=false; b.innerHTML=old; }
}

function renderAll(){
  const d=state.data; if(!d) return;
  const health=d.health||{}; $('reasoning-mode').textContent=String(health.reasoning||'').startsWith('rules')?'Правила и расчёты без LLM':(health.reasoning||'—');
  $('store-name').textContent=(d.portfolio?.stores?.length>1?'Портфель WB':(d.store?.name || (health.wb_mode==='demo'?'DEMO WB cabinet':'Wildberries'))); $('connection-dot').classList.toggle('ok',!!health.wb_connected);
  $('last-sync').textContent=d.summary?.last_run_at ? `Синхронизация ${ago(d.summary.last_run_at)}` : 'Нет запусков';
  const critical=d.summary?.critical_24h||0, warning=d.summary?.warning_24h||0, actions=(d.decisions||[]).length || d.summary?.recommendations||0;
  $('metric-critical').textContent=num(critical); $('metric-warning').textContent=num(warning); $('metric-actions').textContent=num(actions); $('metric-agents').textContent=num(Object.keys(d.agents||{}).length);
  if($('metric-critical-period')) $('metric-critical-period').textContent=d.period?.label||'за выбранный период';
  if($('metric-warning-period')) $('metric-warning-period').textContent=d.period?.label||'за выбранный период';
  if($('finance-period-label')) $('finance-period-label').textContent=d.period?.label||'выбранный период';
  $('nav-alerts').textContent=critical+warning; if($('nav-decisions')) $('nav-decisions').textContent=(d.decisions||[]).filter(x=>['critical','high'].includes(x.priority)).length;
  const ad=adSummary(); $('metric-ad-spend').textContent=rub(ad.spend); const rating=snap('reviews_questions','seller_rating')?.data; const ratingVal=firstNumeric(rating,['rating']); $('metric-rating').textContent=ratingVal==null?'—':num(ratingVal,2);
  renderExecutive(); renderEvents(); renderRecommendations(); renderDecisions(); renderDecisionControl(); renderDomains(); renderPortfolio(); renderConnections(); renderAdvertising(); renderPolicyStudio(); renderInventory(); renderSearch(); renderFinance(); renderCustomers(); renderKnowledge(); renderAgents();
}
function snap(source,key){ return state.data?.snapshots?.[source]?.[key] || null; }
function firstNumeric(obj, keys){
  if(obj==null) return null; if(typeof obj==='number') return obj; if(Array.isArray(obj)){ for(const v of obj){ const n=firstNumeric(v,keys); if(n!=null) return n; } return null; }
  if(typeof obj==='object'){ for(const k of Object.keys(obj)){ if(keys.includes(k) && !missing(obj[k]) && Number.isFinite(Number(obj[k]))) return Number(obj[k]); } for(const v of Object.values(obj)){ const n=firstNumeric(v,keys); if(n!=null) return n; } }
  return null;
}
function latestSupervisor(){ return (state.data.events||[]).find(e=>e.agent==='supervisor' && e.event_key==='supervisor_digest'); }
function healthScore(){ const c=state.data.summary?.critical_24h||0,w=state.data.summary?.warning_24h||0,errs=state.data.summary?.agent_errors_24h||0; return Math.max(35,Math.round(100-c*14-w*4-errs*10)); }
function renderExecutive(){
  const score=healthScore(), c=state.data.summary?.critical_24h||0,w=state.data.summary?.warning_24h||0; const hs=$('health-score'); hs.style.setProperty('--health-angle',`${score*3.6}deg`); hs.querySelector('span').textContent=score;
  const s=latestSupervisor(); $('executive-title').textContent=c?`${c} критических сигнал${c===1?'':'а'} требуют внимания`:w?`${w} предупреждений, критичных проблем нет`:'Серьёзных отклонений не обнаружено';
  $('executive-summary').textContent=s?.message || (c||w ? 'Система обнаружила отклонения. Ниже показаны приоритеты, фактические показатели и рекомендации. Все действия остаются только рекомендациями.' : 'Агенты работают в режиме наблюдения. Существенных отклонений за последние 24 часа не найдено.');
  const imp=(state.data.events||[]).filter(e=>['critical','warning'].includes(e.severity)).slice(0,4); $('priority-strip').innerHTML=imp.length?imp.map(e=>`<span class="priority-chip ${e.severity}"><b></b>${esc(e.title)}</span>`).join(''):'<span class="priority-chip"><b></b>Система работает штатно</span>';
}
function renderEvents(){
  let events=state.data?.events||[]; if(state.eventFilter==='important') events=events.filter(e=>['critical','warning'].includes(e.severity)); events=events.slice(0,12);
  $('event-list').innerHTML=events.length?events.map(e=>`<button class="event-item interactive-card" type="button" data-event-id="${esc(e.id)}"><div class="event-severity ${esc(e.severity)}">${severityIcon(e.severity)}</div><div class="event-main"><strong>${esc(e.title)}</strong><p>${esc(e.message)}</p><div class="event-meta"><span>${esc(agentNames[e.agent]||e.agent)}</span><span>Открыть подробности</span></div></div><div class="event-time">${time(e.created_at)}</div></button>`).join(''):empty('Событий этого типа пока нет.');
}
function toolLabel(t){ const m={wb_advert_pause:'Разобрать и при необходимости поставить кампанию на паузу',wb_prices_set:'Проверить изменение цены',wb_advert_bids_set:'Проверить изменение ставки',wb_advert_cluster_bids:'Проверить ставку кластера'}; return m[t]||`Рекомендация: ${t}`; }
function renderRecommendations(){
  const a=state.data?.recommendations||[]; $('recommendation-list').innerHTML=a.length?a.slice(0,8).map(x=>`<div class="recommendation"><div class="recommendation-head"><strong>${esc(toolLabel(x.tool))}</strong><span class="pill neutral">НЕ ИСПОЛНЯЕТСЯ</span></div><p>${esc(x.reason)}</p><small>${esc(agentNames[x.agent]||x.agent)} · #${x.id} · режим только чтения</small></div>`).join(''):empty('Сейчас нет рекомендаций, требующих отдельного внимания.');
}
function eventsForAgents(list){ return (state.data.events||[]).filter(e=>list.includes(e.agent)&&['critical','warning'].includes(e.severity)); }
function renderDomains(){
  const symbols={'Реклама':'AD','Остатки':'ST','Поиск':'SR','Карточки':'CD','Финансы':'₽','Клиенты':'CX'};
  $('domain-grid').innerHTML=Object.entries(domainAgents).map(([name,agents])=>{ const es=eventsForAgents(agents), c=es.filter(e=>e.severity==='critical').length,w=es.filter(e=>e.severity==='warning').length, cls=c?'critical':w?'warn':''; const note=c?`${c} критич. · ${w} предупр.`:w?`${w} предупреждений`:'Штатно'; return `<div class="domain ${cls}"><div class="domain-top"><div class="domain-icon">${symbols[name]}</div><span class="dot"></span></div><strong>${name}</strong><span>${note}</span></div>`; }).join('');
}

function pf(){ return state.data?.portfolio || {}; }
function sourceHealthClass(status){ return status==='fresh'?'fresh':status==='broken'?'broken':'stale'; }
function renderPortfolio(){
  const p=pf(), all=p.portfolio||{};
  if($('portfolio-origin')) $('portfolio-origin').textContent=p.data_origin==='google_sheets_bridge'?'ЖИВЫЕ ТАБЛИЦЫ':'СОХРАНЁННЫЙ СРЕЗ';
  if($('portfolio-period')) $('portfolio-period').textContent=p.period||'—';
  if($('pf-orders')) $('pf-orders').textContent=rub(all.orders_rub);
  if($('pf-buyouts')) $('pf-buyouts').textContent=rub(all.buyouts_rub);
  if($('pf-profit')) $('pf-profit').textContent=rub(all.profit_rub);
  if($('pf-margin')) $('pf-margin').textContent=pct(all.margin_pct);
  if($('pf-roi')) $('pf-roi').textContent=`ROI ${pct(all.roi_pct)}`;
  if($('pf-ads')) $('pf-ads').textContent=rub(all.ad_spend_rub);
  if($('pf-drr')) $('pf-drr').textContent=`ДРР ${pct(all.drr_pct)}`;
  if($('pf-orders-change')) $('pf-orders-change').textContent=Number.isFinite(Number(all.orders_change_pct))?`${Number(all.orders_change_pct)>=0?'+':''}${pct(all.orders_change_pct)} к прошлому периоду`:'—';
  if($('pf-profit-change')) $('pf-profit-change').textContent=Number.isFinite(Number(all.profit_change_pct))?`${Number(all.profit_change_pct)>=0?'+':''}${pct(all.profit_change_pct)} к прошлому периоду`:'—';
  const stores=p.stores||[];
  if($('stores-grid')) $('stores-grid').innerHTML=stores.length?stores.map(x=>{
    const cls=x.status==='critical'?'critical':x.status==='watch'?'watch':'';
    const badge=x.status==='critical'?['bad','Требует внимания']:x.status==='watch'?['warn','Наблюдать']:['ok','Штатно'];
    return `<div class="store-portfolio-card ${cls}"><div class="store-card-head"><div><strong>${esc(x.name)}</strong><div class="store-source">${esc(x.source||'')}</div></div><span class="status-tag ${badge[0]}">${badge[1]}</span></div><div class="store-stat-grid"><div class="store-stat"><span>Заказы</span><b>${rub(x.orders_rub)}</b></div><div class="store-stat"><span>Выкупы</span><b>${rub(x.buyouts_rub)}</b></div><div class="store-stat"><span>Прибыль</span><b>${rub(x.profit_rub)}</b></div><div class="store-stat"><span>Маржа / ROI</span><b>${pct(x.margin_pct)} · ${pct(x.roi_pct)}</b></div><div class="store-stat"><span>Реклама</span><b>${rub(x.ad_spend_rub)}</b></div><div class="store-stat"><span>ДРР</span><b>${pct(x.drr_pct)}</b></div></div><p class="store-note">Срез: ${esc(x.freshness||'—')}. ${esc(x.note||'')}</p></div>`;
  }).join(''):empty('Нет портфельных фактов.');

  const o=p.own_27||{};
  const facts=[['Заказы, ₽',rub(o.orders_rub)],['Заказы, шт',num(o.orders_qty)],['Продажи, шт',num(o.sales_qty)],['Остатки ФФ',num(o.ff_stock)],['WB FBS',num(o.wb_fbs_stock)],['Ozon FBS',num(o.ozon_fbs_stock)],['В пути к клиенту',num(o.in_way_to_client)],['FBS долг заказов',num(o.fbs_debt_orders)],['FBW',num(o.fbw_stock)]];
  if($('own27-grid')) $('own27-grid').innerHTML=facts.map(([a,b])=>`<div class="fact-box"><span>${a}</span><strong>${b}</strong></div>`).join('');
  const econ=(o.economy_examples||[]).filter(x=>Number(x.margin_pct)<0 || Number(x.profit_rub)<0).slice(0,6);
  if($('unit-alerts')) $('unit-alerts').innerHTML=econ.length?econ.map(x=>`<div class="signal warning"><strong>${esc(x.name)} · ${esc(x.sku)}</strong><p>Прибыль ${rub(x.profit_rub)} · маржа ${pct(x.margin_pct)} · ROI ${pct(x.roi_pct)} · ДРР ${pct(x.drr_pct)}</p><span class="source">Источник: Юнитка / 27</span></div>`).join(''):empty('В загруженном срезе отрицательных примеров экономики не найдено.');
  if($('ff-snapshot-date')) $('ff-snapshot-date').textContent=o.ff_snapshot_date?`снимок ${o.ff_snapshot_date}`:'—';
  const ff=(o.ff_examples||[]).slice(0,12);
  if($('ff-grid')) $('ff-grid').innerHTML=ff.length?ff.map(x=>`<div class="mini-row"><div><b>${esc(x.name)}</b><br><span>${esc(x.sku)}</span></div><strong>${num(x.available)} шт</strong></div>`).join(''):empty('Нет фактов ФФ.');
}

function renderConnections(){
  const c=state.data?.connections||{}, wb=c.wb||{}, gs=c.google_sheets||{};
  if($('wb-connection-status')){ $('wb-connection-status').className=`status-tag ${wb.configured?'ok':'warn'}`; $('wb-connection-status').textContent=wb.configured?'ПОДКЛЮЧЕН':'НЕТ ТОКЕНА'; }
  if($('wb-connection-note')) $('wb-connection-note').textContent=wb.configured?'API продавца WB подключён. Любые изменяющие действия заблокированы жёстким режимом только чтения.':'API продавца WB не подключён — агент продолжает работать на таблицах Google и публичных данных WB. Токен можно добавить позже.';
  if($('sheets-connection-status')){ $('sheets-connection-status').className=`status-tag ${gs.configured?'ok':'warn'}`; $('sheets-connection-status').textContent=gs.configured?'ПОДКЛЮЧЕН':'СОХРАНЁННЫЙ СРЕЗ'; }
  if($('sheets-connection-note')) $('sheets-connection-note').textContent=gs.configured?`Автоисточник: ${gs.mode||'таблицы Google'}${gs.last_live_snapshot?' · '+time(gs.last_live_snapshot):''}.`:'Агент сам попробует получить разрешённые таблицы через текущий вход в Google в браузере. Ссылки и идентификаторы вводить не нужно.';
  const up=state.data?.health?.auto_update||{}, ver=state.data?.health?.app_version||up.current_version||'—';
  if($('app-version-banner')) $('app-version-banner').textContent=`v${ver}`;
  if($('update-version')) $('update-version').textContent=`Версия ${ver}`;
  if($('update-connection-status')){ const bad=!!up.last_error; const available=!!up.available; $('update-connection-status').className=`status-tag ${bad?'warn':available?'warn':'ok'}`; $('update-connection-status').textContent=bad?'ОШИБКА':available?'ЕСТЬ ОБНОВЛЕНИЕ':'АКТУАЛЬНО'; }
  if($('update-connection-note')) $('update-connection-note').textContent=up.last_error?`Последняя проверка не удалась: ${up.last_error}. Агент продолжает работать на текущей версии.`:up.last_result==='updated'?`Обновлено автоматически до версии ${up.installed_version||ver}. Перед установкой создана резервная копия.`:`Код проверяется автоматически примерно раз в минуту. Новая версия ставится только после проверки и с автоматическим откатом при ошибке.`;
  const health=(pf().source_health||[]), healthMap=Object.fromEntries(health.map(x=>[x.id,x]));
  const sources=c.sources||[];
  if($('sources-table')) $('sources-table').innerHTML=sources.length?sources.map(x=>{ const h=healthMap[x.id]||{}; const st=h.status||'stale'; return `<tr><td><strong>${esc(x.title)}</strong><br><span class="muted">${esc(x.id)}</span></td><td>${esc(x.role||'—')}</td><td><span class="source-health-badge ${sourceHealthClass(st)}">${esc(sourceHealthName(st))}</span><br><span class="muted">${esc(h.freshness||'не проверено')} · доверие ${esc(h.trust||'—')}</span></td><td>${esc(h.used_for||x.ranges?.join(', ')||'—')}</td></tr>`; }).join(''):`<tr><td colspan="4">${empty('Реестр источников пуст.')}</td></tr>`;
}

async function refreshSheets(){
  const b=$('refresh-sheets'); if(!b||b.disabled) return; b.disabled=true; const old=b.textContent; b.textContent='Обновляю…';
  try{ const r=await fetch('/api/sheets/refresh',{method:'POST'}); const j=await r.json(); if(!r.ok) throw new Error(j.detail||`HTTP ${r.status}`); showToast('Google Sheets обновлены.','success'); await fetchData(); }
  catch(e){ showToast(`Таблицы не обновлены: ${e.message}`,'error'); }
  finally{ b.disabled=false; b.textContent=old; }
}


function renderDecisions(){
  const rows=state.data?.decisions||[], counts={critical:0,high:0,medium:0,low:0}; rows.forEach(x=>counts[x.priority]=(counts[x.priority]||0)+1);
  if($('decision-summary')) $('decision-summary').innerHTML=`<div><span>Критично</span><strong>${counts.critical}</strong></div><div><span>Высокий приоритет</span><strong>${counts.high}</strong></div><div><span>Средний</span><strong>${counts.medium}</strong></div><div><span>Всего решений</span><strong>${rows.length}</strong></div>`;
  if(!$('decision-grid')) return;
  $('decision-grid').innerHTML=rows.length?rows.map(d=>{ const acts=(d.recommended_actions||[]).map((a,i)=>`<div class="decision-action"><b>${i+1}</b><span>${esc(a.action)}</span></div>`).join(''); const ev=(d.evidence||[]).map(x=>`<li><strong>${esc(x.metric)}</strong>: ${esc(x.value)} <span class="muted">${esc(x.source)} ${x.note?`· ${esc(x.note)}`:''}</span></li>`).join(''); const blockers=(d.blockers||[]).map(x=>`<li>${esc(x)}</li>`).join(''); const pri=d.priority==='critical'?'КРИТИЧНО':d.priority==='high'?'ВЫСОКИЙ':d.priority==='medium'?'СРЕДНИЙ':'НИЗКИЙ'; return `<article class="decision-card ${esc(d.priority)}"><div class="decision-head"><div><small>${esc(scopeName(d.scope))} · ${esc(d.entity_id)}</small><h3>${esc(d.title)}</h3></div><span class="confidence">${pri} · ${esc(d.confidence)}</span></div><p class="decision-diagnosis">${esc(d.diagnosis)}</p>${blockers?`<div class="decision-blockers"><strong>Чего не хватает для безопасного решения</strong><ul>${blockers}</ul></div>`:''}<div class="decision-actions">${acts}</div><details class="decision-evidence"><summary>Почему так · ${d.evidence?.length||0} фактов</summary><ul>${ev||'<li>Нет evidence</li>'}</ul></details><div class="decision-foot">Контроль: ${esc(d.follow_up||'—')}</div></article>`; }).join(''):empty('Решений пока нет. Запусти «Проверить сейчас».');
}
async function refreshDecisions(){ const b=$('refresh-decisions'); if(b)b.disabled=true; try{ const r=await fetch('/api/decisions/refresh',{method:'POST'}); if(!r.ok) throw new Error(`HTTP ${r.status}`); await fetchData(); showToast('Решения пересчитаны по всем доступным контурам.','success'); }catch(e){ showToast(`Не удалось пересчитать: ${e.message}`,'error'); }finally{ if(b)b.disabled=false; } }

function adSummary(){
  const s=snap('advertising_monitor','stats_7d')?.data; const rows=extractList(s); let spend=0,orders=0,revenue=0,views=0,clicks=0;
  for(const r of rows){ spend+=Number(r.sum??r.spend??r.cost??0)||0; orders+=Number(r.orders??r.ordersCount??0)||0; revenue+=Number(r.sum_price??r.revenue??r.sales??0)||0; views+=Number(r.views??r.impressions??0)||0; clicks+=Number(r.clicks??0)||0; }
  return {rows,spend,orders,revenue,drr:revenue>0?spend/revenue*100:null,ctr:views>0?clicks/views*100:null};
}
function campaignNames(){ const c=extractList(snap('advertising_monitor','active_campaigns')?.data); const m={}; c.forEach(x=>{ const id=x.advertId??x.advert_id??x.id; if(id!=null)m[String(id)]=x.name||`Кампания #${id}`; }); return m; }
function renderAdvertising(){
  const a=adSummary(), names=campaignNames(); $('ads-spend').textContent=rub(a.spend); $('ads-orders').textContent=num(a.orders); $('ads-drr').textContent=pct(a.drr); $('ads-ctr').textContent=pct(a.ctr); const snapObj=snap('advertising_monitor','stats_7d'); $('ads-updated').textContent=snapObj?`обновлено ${ago(snapObj.created_at)}`:'нет данных';
  $('ads-table').innerHTML=a.rows.length?a.rows.map(r=>{ const id=r.advertId??r.advert_id??r.id; const sp=Number(r.sum??r.spend??0)||0, or=Number(r.orders??r.ordersCount??0)||0, rev=Number(r.sum_price??r.revenue??0)||0, drr=rev>0?sp/rev*100:null, ctr=Number(r.ctr), cpc=Number(r.cpc); let st='ok',tx='Штатно'; if(or===0&&sp>=1500){st='bad';tx='Расход без заказов';} else if(drr!=null&&drr>=20){st='bad';tx='Критичный ДРР';} else if(drr!=null&&drr>=12){st='warn';tx='Повышенный ДРР';} return `<tr><td><strong>${esc(names[String(id)]||`Кампания #${id??'—'}`)}</strong><br><span class="muted">ID ${esc(id??'—')}</span></td><td>${rub(sp)}</td><td>${num(or)}</td><td>${rub(rev)}</td><td>${pct(drr)}</td><td>${Number.isFinite(ctr)?pct(ctr):'—'}</td><td>${Number.isFinite(cpc)?rub(cpc):'—'}</td><td><span class="status-tag ${st}">${tx}</span></td></tr>`; }).join(''):`<tr><td colspan="8">${empty('Нет рекламных данных. Запусти проверку.')}</td></tr>`;
  const ev=(state.data.events||[]).filter(e=>['advertising_monitor','advertising_optimizer'].includes(e.agent)&&['critical','warning'].includes(e.severity)).slice(0,8); $('ads-signals').innerHTML=ev.length?ev.map(signalHtml).join(''):empty('Проблемных рекламных сигналов сейчас нет.');
  const plans=(state.data.decisions||[]).filter(d=>String(d.decision_key||'').startsWith('advert:')&&(String(d.decision_key||'').includes('numeric_control')||String(d.decision_key||'').includes('zero_orders')));
  if($('ad-control-plans')) $('ad-control-plans').innerHTML=plans.length?plans.map(adPlanHtml).join(''):empty('Числовых планов пока нет. Запусти полный аудит.');
}

function adPlanHtml(d){
  const ev=Object.fromEntries((d.evidence||[]).map(x=>[x.metric,x.value]));
  const current=Number(ev.current_bid_rub), target=Number(ev.target_bid_rub), cap=Number(ev.max_spend_next_24h_rub), drr=Number(ev.observed_drr_pct), tdrr=Number(ev.target_drr_pct), trend=Number(ev.orders_trend_pct), stock=Number(ev.stock_days_forecast), maturity=Number(ev.cohort_maturity_pct), lag50=Number(ev.buyout_lag_p50_days), lag90=Number(ev.buyout_lag_p90_days);
  const bid=Number.isFinite(current)&&Number.isFinite(target)?`${rub(current)} → ${rub(target)}`:'нет подтверждённой ставки';
  return `<article class="ad-control-card ${esc(d.priority)}"><div class="ad-control-head"><div><small>${esc(d.entity_id)}</small><h3>${esc(d.title)}</h3></div><span class="confidence">${d.confidence==='high'?'высокая уверенность':d.confidence==='medium'?'средняя уверенность':'низкая уверенность'}</span></div><div class="ad-kpi-grid"><div><span>Ставка</span><strong>${bid}</strong></div><div><span>Лимит 24ч</span><strong>${Number.isFinite(cap)?rub(cap):'—'}</strong></div><div><span>ДРР / допустимый предел</span><strong>${Number.isFinite(drr)?pct(drr):'—'} / ${Number.isFinite(tdrr)?pct(tdrr):'—'}</strong></div><div><span>Изменение темпа заказов</span><strong>${Number.isFinite(trend)?pct(trend):'—'}</strong></div><div><span>Прогноз запаса</span><strong>${Number.isFinite(stock)?`${num(stock,1)} дн.`:'—'}</strong></div><div><span>Созревание заказов</span><strong>${Number.isFinite(maturity)?`${num(maturity,0)}%`:'—'}</strong></div><div><span>Типичный срок выкупа</span><strong>${Number.isFinite(lag50)&&Number.isFinite(lag90)?`${num(lag50,1)} / ${num(lag90,1)} дн. (50/90%)`:'—'}</strong></div></div><p>${esc(d.diagnosis)}</p>${(d.blockers||[]).length?`<div class="decision-blockers"><strong>Не хватает данных</strong><ul>${d.blockers.map(x=>`<li>${esc(x)}</li>`).join('')}</ul></div>`:''}<div class="decision-actions">${(d.recommended_actions||[]).map((a,i)=>`<div class="decision-action"><b>${i+1}</b><span>${esc(a.action)}</span></div>`).join('')}</div></article>`;
}

function renderDecisionControl(){
  const dc=state.data?.decision_control||{}, history=dc.history||[], changes=dc.changes||[], evals=dc.evaluations||[];
  const review=dc.latest_review?.data||dc.latest_review||{}, reviews=review.reviews||[], investigations=review.investigations||[];
  const validation=dc.demand_model_validation?.data||dc.demand_model_validation||{};
  if($('control-history-count')) $('control-history-count').textContent=num(history.length);
  if($('control-change-count')) $('control-change-count').textContent=num(changes.length);
  if($('control-eval-count')) $('control-eval-count').textContent=num(evals.length);
  const blocked=reviews.filter(x=>x.verdict==='заблокировано проверкой').length;
  if($('control-blocked-count')) $('control-blocked-count').textContent=num(blocked);
  if($('control-changes')) $('control-changes').innerHTML=changes.length?changes.slice(0,20).map(x=>`<div class="signal info"><strong>${esc(changeTypeName(x.change_type))} · ${esc(x.entity_id)}</strong><p>${esc(x.old?.value)} → ${esc(x.new?.value)}</p><span class="source">${esc(x.source)} · ${time(x.observed_at)}</span></div>`).join(''):empty('Фактических изменений пока не замечено. В режиме только чтения они появятся, когда ставка/цена/медиа изменятся снаружи.');
  if($('control-reviews')){
    const parts=[];
    if(validation&&validation.samples){parts.push(`<div class="signal info"><strong>Проверка прогноза спроса</strong><p>${esc(`Окон: ${validation.samples}; SKU: ${validation.skus}; WAPE: ${validation.wape_pct??'—'}%; средняя ошибка: ${validation.mae_orders??'—'} заказа`)}</p><span class="source">Историческая проверка без использования будущих данных</span></div>`);}
    parts.push(...investigations.slice(0,10).map(x=>`<div class="signal ${x.status==='частичное'?'warning':'info'}"><strong>Расследование · ${esc(x.entity_id)}</strong><p>${esc([...(x.hypotheses||[]),...(x.missing||[]).map(v=>'не хватает: '+v)].slice(0,4).join(' · ')||'Критичных гипотез не найдено.')}</p><span class="source">${esc(x.status)} расследование</span></div>`));
    parts.push(...reviews.slice(0,20).map(x=>`<div class="signal ${x.verdict==='заблокировано проверкой'?'warning':'info'}"><strong>${esc(x.verdict)} · ${esc(x.decision_key)}</strong><p>${esc([...(x.blockers_added||[]),...(x.critic||[]),...(x.risk_checks||[])].slice(0,4).join(' · ')||'Критичных возражений нет.')}</p><span class="source">Независимый критик и контроль риска</span></div>`));
    $('control-reviews').innerHTML=parts.length?parts.join(''):empty('Проверка ещё не запускалась.');
  }
  if($('control-evaluations')) $('control-evaluations').innerHTML=evals.length?evals.slice(0,30).map(x=>`<div class="signal info"><strong>${esc(x.horizon)} · ${esc(x.entity_id)}</strong><p>${esc(x.verdict||'наблюдение')} ${x.notes?`· ${esc(x.notes)}`:''}</p><span class="source">${time(x.evaluated_at)}</span></div>`).join(''):empty('Нет завершённых контрольных точек. Они появляются только после реально замеченного изменения.');
  if($('control-history')) $('control-history').innerHTML=history.length?history.slice(0,30).map(x=>`<div class="signal info"><strong>${esc(x.payload?.title||x.decision_key)}</strong><p>${esc(x.payload?.diagnosis||'')}</p><span class="source">${esc(historyStatusName(x.status))} · ${time(x.created_at)}</span></div>`).join(''):empty('История решений пока пуста.');
}

function renderPolicyStudio(){
  const p=state.data?.runtime_policy?.advertising||{};
  const set=(id,v)=>{const el=$(id); if(el&&document.activeElement!==el) el.value=v??'';};
  set('policy-strategy-mode',p.strategy_mode||'balanced'); set('policy-target-drr',p.target_drr_pct); set('policy-min-profit',p.min_profit_after_ads_rub);
  set('policy-bid-step',p.max_bid_step_pct); set('policy-spend-step',p.max_spend_step_pct); set('policy-min-clicks',p.min_clicks_for_numeric_ad_decision);
  set('policy-min-orders',p.min_orders_for_scale_up); set('policy-eval-hours',p.evaluation_window_hours); set('policy-stock-scale',p.min_stock_days_for_scale);
  set('policy-stock-hold',p.min_stock_days_for_hold); set('policy-stock-target',p.target_stock_days); set('policy-organic-growth',p.organic_growth_hold_threshold_pct); set('policy-trend-short',p.trend_short_days); set('policy-trend-long',p.trend_long_days); set('policy-max-trend',p.max_trend_pct_for_forecast); set('policy-max-spend24',p.max_internal_spend_24h_rub);
  const safe=state.data?.policy_safety||{}; if($('hard-bid-step')) $('hard-bid-step').textContent=`${safe.hard_max_bid_change_pct??10}%`; if($('hard-bid-cap')) $('hard-bid-cap').textContent=rub(safe.absolute_bid_cap_rub);
  const remote=state.data?.remote_policy||{}; if($('remote-policy-version')) $('remote-policy-version').textContent=remote.enabled?(remote.version?`версия ${remote.version}`:'подключены'):'отключены'; if($('remote-policy-note')) $('remote-policy-note').textContent=remote.last_error?`Удалённые правила временно недоступны: ${remote.last_error}. Используется последняя сохранённая версия.`:`Удалённые правила автоматически проверяются каждые ${remote.refresh_seconds||60} сек. Изменения бизнес-логики подхватываются без новой версии программы.`;
  const h=state.data?.runtime_policy_history||[]; if($('policy-history')) $('policy-history').innerHTML=h.length?h.map((x,i)=>`<div class="policy-history-row"><div><strong>${time(x.at)}</strong><span>${esc(x.by||'оператор')}</span></div><p>Доп. предел ДРР ${esc(x.before?.target_drr_pct??'—')} → ${esc(x.after?.target_drr_pct??'—')}% · макс. шаг ставки ${esc(x.before?.max_bid_step_pct??'—')} → ${esc(x.after?.max_bid_step_pct??'—')}% · окно ${esc(x.before?.evaluation_window_hours??'—')} → ${esc(x.after?.evaluation_window_hours??'—')} ч</p></div>`).join(''):empty('Локальных изменений ещё не было.');
}
function policyPayload(){ return {
  strategy_mode:$('policy-strategy-mode').value,
  target_drr_pct:Number($('policy-target-drr').value), min_profit_after_ads_rub:Number($('policy-min-profit').value), max_bid_step_pct:Number($('policy-bid-step').value), max_spend_step_pct:Number($('policy-spend-step').value),
  min_clicks_for_numeric_ad_decision:Number($('policy-min-clicks').value), min_orders_for_scale_up:Number($('policy-min-orders').value), evaluation_window_hours:Number($('policy-eval-hours').value),
  min_stock_days_for_scale:Number($('policy-stock-scale').value), min_stock_days_for_hold:Number($('policy-stock-hold').value), target_stock_days:Number($('policy-stock-target').value), organic_growth_hold_threshold_pct:Number($('policy-organic-growth').value), trend_short_days:Number($('policy-trend-short').value), trend_long_days:Number($('policy-trend-long').value), max_trend_pct_for_forecast:Number($('policy-max-trend').value), max_internal_spend_24h_rub:Number($('policy-max-spend24').value)
}; }
async function refreshRemotePolicy(){ const b=$('refresh-remote-policy'); if(!b)return; b.disabled=true; const old=b.textContent; b.textContent='Обновляю…'; try{const r=await fetch('/api/policy-studio/remote-refresh',{method:'POST'}); const j=await r.json(); if(!r.ok)throw new Error(j.detail||`HTTP ${r.status}`); showToast('Удалённые правила обновлены, решения пересчитаны.','success'); await fetchData();}catch(e){showToast(`Правила не обновлены: ${e.message}`,'error');}finally{b.disabled=false;b.textContent=old;}}
async function savePolicy(){ const b=$('save-policy'); if(!b)return; b.disabled=true; try{const r=await fetch('/api/policy-studio/advertising',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(policyPayload())}); const j=await r.json(); if(!r.ok)throw new Error(j.detail||`HTTP ${r.status}`); showToast('Правила применены без перезапуска. Решения пересчитаны.','success'); await fetchData();}catch(e){showToast(`Правила не сохранены: ${e.message}`,'error');}finally{b.disabled=false;}}
async function rollbackPolicy(){ const b=$('rollback-policy'); if(!b)return; b.disabled=true; try{const r=await fetch('/api/policy-studio/rollback/0',{method:'POST'}); const j=await r.json(); if(!r.ok)throw new Error(j.detail||`HTTP ${r.status}`); showToast('Последнее изменение откатилось.','success'); await fetchData();}catch(e){showToast(`Откат не выполнен: ${e.message}`,'error');}finally{b.disabled=false;}}

function renderInventory(){
  const s=snap('inventory','coverage'), cov=s?.data||{}; $('inventory-updated').textContent=s?`обновлено ${ago(s.created_at)}`:'нет данных'; const rows=Object.values(cov||{}).sort((a,b)=>(a.days_cover??9999)-(b.days_cover??9999));
  $('inventory-grid').innerHTML=rows.length?rows.map(r=>{ const days=Number(r.days_cover), cls=Number.isFinite(days)&&(days<=2?'critical':days<=5?'warn':''); const width=Number.isFinite(days)?Math.max(4,Math.min(100,days/30*100)):100; return `<div class="inventory-item ${cls}"><div class="inventory-top"><strong>nmID ${esc(r.nm_id)}</strong><span class="status-tag ${cls==='critical'?'bad':cls==='warn'?'warn':'ok'}">${cls==='critical'?'Дефицит':cls==='warn'?'Низкий запас':'Норма'}</span></div><div class="inventory-days">${Number.isFinite(days)?`${num(days,1)} дня`:'Нет продаж'}</div><div class="inventory-meta">Остаток ${num(r.stock)} · темп ${num(r.daily_sales,1)}/день</div><div class="cover-bar"><i style="width:${width}%"></i></div></div>`; }).join(''):empty('Нет данных по покрытию остатками.');
  const acc=extractList(snap('supply','acceptance')?.data); $('acceptance-list').innerHTML=acc.length?acc.slice(0,8).map(x=>{const coef=Number(x.coefficient); return `<div class="signal ${coef<=1?'info':'warning'}"><strong>${esc(x.warehouseName||x.warehouse_name||'Склад')}</strong><p>Коэффициент приёмки: ${Number.isFinite(coef)?num(coef,0):'—'} · разгрузка ${x.allowUnload===false?'недоступна':'доступна'}</p><span class="source">Источник: коэффициенты приёмки WB</span></div>`;}).join(''):empty('Нет данных по коэффициентам приёмки.');
}
function renderSearch(){
  const s=snap('search_positions','positions'); $('search-updated').textContent=s?`обновлено ${ago(s.created_at)}`:'нет данных'; const rows=Object.values(s?.data||{}).sort((a,b)=>Number(a.position)-Number(b.position)); $('search-table').innerHTML=rows.length?rows.map(r=>{const p=Number(r.position); const st=p<=10?['ok','Топ-10']:p<=30?['warn','11–30']:['bad','30+']; return `<tr><td><strong>${esc(r.nm_id)}</strong></td><td>${esc(r.query||'—')}</td><td>${num(p)}</td><td><span class="status-tag ${st[0]}">${st[1]}</span></td></tr>`;}).join(''):`<tr><td colspan="4">${empty('Поисковая аналитика недоступна или ещё не загружена.')}</td></tr>`;
  const ev=(state.data.events||[]).filter(e=>['cards','price_margin','search_positions'].includes(e.agent)&&['critical','warning'].includes(e.severity)).slice(0,10); $('cards-signals').innerHTML=ev.length?ev.map(signalHtml).join(''):empty('Карточки и позиции без заметных проблем.');
}
function renderFinance(){
  const bal=snap('finance','balance')?.data||{}; $('finance-withdraw').textContent=rub(firstNumeric(bal,['forWithdraw','for_withdraw','balance'])); $('finance-transit').textContent=rub(firstNumeric(bal,['inTransit','in_transit']));
  const ded=extractList(snap('cost_guard','deductions')?.data); const dsum=ded.reduce((s,x)=>s+(Number(x.amount??x.sum??0)||0),0); $('finance-deductions').textContent=ded.length?rub(dsum):'0 ₽';
  const stor=extractList(snap('cost_guard','paid_storage')?.data); const ssum=stor.reduce((s,x)=>s+(Number(x.warehousePrice??x.amount??x.price??0)||0),0); $('finance-storage').textContent=stor.length?rub(ssum):'0 ₽';
  const ev=(state.data.events||[]).filter(e=>['finance','cost_guard','documents'].includes(e.agent)&&['critical','warning'].includes(e.severity)).slice(0,10); $('finance-signals').innerHTML=ev.length?ev.map(signalHtml).join(''):empty('Финансовых предупреждений сейчас нет.');
}
function countObjList(s,k){ const x=snap(s,k)?.data; return extractList(x).length; }
function renderCustomers(){
  const rating=firstNumeric(snap('reviews_questions','seller_rating')?.data,['rating']); $('customer-rating').textContent=rating==null?'—':num(rating,2); const flags=snap('reviews_questions','flags')?.data||{}; const nf=!!flags.hasNewFeedbacks,nq=!!flags.hasNewQuestions; $('customer-flags').textContent=nf||nq?[nf?'Отзывы':null,nq?'Вопросы':null].filter(Boolean).join(' + '):'Нет';
  $('customer-returns').textContent=num(countObjList('returns_quality','open_claims')); $('customer-chats').textContent=num(countObjList('buyer_chats','chat_events'));
  const ev=(state.data.events||[]).filter(e=>['reviews_questions','buyer_chats','returns_quality','orders_fbs'].includes(e.agent)&&['critical','warning'].includes(e.severity)).slice(0,12); $('customer-signals').innerHTML=ev.length?ev.map(signalHtml).join(''):empty('Критичных сигналов от покупателей и возвратов нет.');
}
function signalHtml(e){ return `<div class="signal ${esc(e.severity)}"><strong>${esc(e.title)}</strong><p>${esc(e.message)}</p><span class="source">${esc(agentNames[e.agent]||e.agent)} · ${time(e.created_at)}</span></div>`; }

function renderKnowledge(){
  const v=state.data?.knowledge?.policy_version; if($('policy-version')) $('policy-version').textContent=v?`ПРАВИЛА v${v}`:'ПРАВИЛА';
}

function renderAgents(){
  const agents=state.data.agents||{}, runs=state.data.runs||{}; const ordered=Object.keys(agents); $('agents-grid').innerHTML=ordered.map((name,i)=>{const r=runs[name],ok=!r||r.status==='ok',interval=agents[name].interval_minutes; return `<div class="agent-card"><div class="agent-card-top"><div class="agent-symbol">${String(i+1).padStart(2,'0')}</div><span class="status-tag ${ok?'ok':'bad'}">${ok?'РАБОТАЕТ':'ОШИБКА'}</span></div><strong>${esc(agentNames[name]||name)}</strong><p>${esc(agentDescriptions[name]||'Специализированный модуль WB AI Manager.')}</p><small>${r?.finished_at?`последний запуск ${ago(r.finished_at)}`:`каждые ${interval||'—'} мин`}</small></div>`;}).join('');
  $('agents-table').innerHTML=ordered.map(name=>{const r=runs[name], int=agents[name].interval_minutes; return `<tr><td><strong>${esc(agentNames[name]||name)}</strong></td><td><span class="status-tag ${!r||r.status==='ok'?'ok':'bad'}">${esc(r?.status==='ok'?'работает':r?.status==='error'?'ошибка':'по расписанию')}</span></td><td>${r?.finished_at?time(r.finished_at):'ещё не запускался'}</td><td>${int?`${int} мин`:'ручной'}</td></tr>`;}).join('');
}

$('refresh-data').addEventListener('click',()=>fetchData(true)); if($('save-policy')) $('save-policy').addEventListener('click',savePolicy); if($('rollback-policy')) $('rollback-policy').addEventListener('click',rollbackPolicy); if($('refresh-decisions')) $('refresh-decisions').addEventListener('click',refreshDecisions); if($('refresh-sheets')) $('refresh-sheets').addEventListener('click',refreshSheets); if($('refresh-remote-policy')) $('refresh-remote-policy').addEventListener('click',refreshRemotePolicy); $('run-audit').addEventListener('click',runAudit); window.addEventListener('hashchange',()=>setPage(location.hash.replace('#','')||'overview'));
initNav(); fetchData(true); setInterval(()=>fetchData(false),15000);
