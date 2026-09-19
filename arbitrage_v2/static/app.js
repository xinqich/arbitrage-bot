'use strict';
let token='', current=null, pending=false, paperPreview=null, selectedRoute=null, selectedPrediction=null, renderedSearchId='', activeSignature='', resultSignature='', previewRequest=0;
let routeMode='confirmed';try{routeMode=localStorage.getItem('arbitrage-route-mode')==='paper'?'paper':'confirmed';}catch{}
const $=id=>document.getElementById(id);
const money=value=>value==null?'Not recorded':new Intl.NumberFormat(undefined,{style:'currency',currency:'USD'}).format(value/100);
const time=value=>value?new Date(value).toLocaleString():'Unknown';
const words=value=>String(value||'').replaceAll('_',' ');
const openRoute=r=>!['resolved','cancelled'].includes(r.state);
const saleLabel=value=>({current_bids:'Highest orders',listing_price:'Lowest ask',midpoint:'Midpoint'}[value]||'Not recorded');
const scenarioLabel=p=>`Steam: ${saleLabel(p.sale_scenarios?.steam)} / DMarket: ${saleLabel(p.sale_scenarios?.dmarket)}`;
const roi=p=>p.entry_cost_cents>0&&p.predicted_net_cents!=null?(100*p.predicted_net_cents/p.entry_cost_cents).toFixed(2)+'%':'Unknown';
function el(tag,text,className){const n=document.createElement(tag);if(text!=null)n.textContent=text;if(className)n.className=className;return n;}
function empty(node){node.replaceChildren();return node;}
function pair(dl,key,value){dl.append(el('dt',key),el('dd',value));}
function notice(text){$('message').textContent=text;$('message').hidden=false;}
function localDateTime(){return new Date(Date.now()-new Date().getTimezoneOffset()*60000).toISOString().slice(0,23);}
async function api(path,body){const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),45000);try{const r=await fetch(path,{cache:'no-store',signal:controller.signal,...(body?{method:'POST',headers:{'Content-Type':'application/json','X-Local-Token':token},body:JSON.stringify(body)}:{})});const data=await r.json();if(!r.ok)throw new Error(data.error||'Request failed');return data;}finally{clearTimeout(timer);}}
async function action(data){if(pending)return;pending=true;try{const result=await api('/api/action',data);notice(data.action==='backup'?`Backup checked and saved: ${result.backup}`:data.action==='search'?'Search complete.':data.action==='check_now'?'Price check requested. Resume first if collection is paused.':'Saved.');await refresh();return result;}catch(error){notice(error.message);}finally{pending=false;}}
function reportForMode(){return current?.searches?.[routeMode]||(current?.search?.mode===routeMode?current.search:null);}
function holdingCost(routeId){return current?.holding_costs?.routes.find(r=>r.route_id===routeId);}
function sumAccounts(accounts){return Object.values(accounts||{}).reduce((a,b)=>a+b,0);}
function metric(root,label,value,note){const box=el('article',null,'metric');box.append(el('span',label),el('strong',value));if(note)box.append(el('small',note));root.append(box);}
function navigationLink(text,mode){const a=el('a',text);a.href='#routes';a.addEventListener('click',()=>setMode(mode));return a;}
function render(s){
  if(!$('search-settings-form').dataset.loaded&&s.search_settings){$('search-minimum').value=(s.search_settings.minimum_purchase_cents/100).toFixed(2);$('search-spread').value=(s.search_settings.narrow_spread_bps/100).toFixed(2);$('search-settings-form').dataset.loaded='true';}
  current=s;
  const funds=s.real_funds,wallet=s.steam_wallet||{},costs=s.holding_costs||{},open=s.routes.filter(openRoute);
  $('worker-status').textContent=s.controls.paused?'Collection paused':s.busy?'Collecting prices…':s.worker_alive?'Collection running':'Worker stopped';
  $('next-check').textContent=s.controls.paused?'Resume when ready.':`Next check: ${time(s.next_action_at)}`;
  $('pause-button').dataset.action=s.controls.paused?'resume':'pause';$('pause-button').textContent=s.controls.paused?'Resume':'Pause';
  $('overview-dmarket').textContent=money(funds.configured?sumAccounts(funds.accounts):null);
  $('overview-available').textContent=funds.configured?`${money(funds.available_cents)} available · ${money(funds.reserved_cents)} reserved`:'Record your actual DMarket funds';
  $('overview-remainders').textContent=wallet.needs_reconciliation?'Check records':money(wallet.balance_cents);
  $('overview-invested').textContent=money(costs.confirmed?.cost_cents);
  const counts=empty($('overview-routes'));for(const [mode,label] of [['confirmed','real'],['paper','paper']])counts.append(navigationLink(`${open.filter(r=>r.mode===mode).length} ${label}`,mode));
  $('allowance').textContent=Math.max(0,s.request_allowance-Object.values(s.requests).reduce((a,b)=>a+b,0)).toLocaleString();
  const attention=$('attention'),sourceProblems=Object.keys(s.health.source_states||{}).length;
  attention.hidden=!(s.health.status==='blocked'||sourceProblems||wallet.needs_reconciliation||!s.worker_alive);
  if(!attention.hidden){empty(attention).append(document.createTextNode(wallet.needs_reconciliation?'Steam wallet records need reconciliation. ':!s.worker_alive?'The worker is stopped. Reopen the launcher. ':'Some price collection needs attention. '));const link=el('a',wallet.needs_reconciliation?'Open Funds':'Open Debug');link.href=wallet.needs_reconciliation?'#funds':'#controls';link.addEventListener('click',()=>{$('debug-panel').open=true;});attention.append(link);}
  $('route-budget').textContent=routeMode==='paper'?`Shared paper funds: ${money(s.unallocated_dmarket_paper_cents)} available · held-item cost ${money(costs.paper?.cost_cents)}`:funds.configured?`${money(funds.available_cents)} available for a new real route`:'Record your DMarket funds in Funds before a real search.';
  renderActive(open.filter(r=>r.mode===routeMode));renderSearch(reportForMode());renderFunds(s);renderDebug(s);renderResults(s);
  if(selectedRoute){const route=s.routes.find(r=>r.route_id===selectedRoute);if(route&&openRoute(route)&&route.mode===routeMode)renderActiveSummary(route);else closeActive();}
  $('updated').textContent=`Version ${s.version} · Updated ${time(s.at)}`;
}
function renderActive(rows){
  const signature=JSON.stringify([routeMode,rows.map(r=>[r.route_id,r.state,r.open_assets,r.next_eligible_at,holdingCost(r.route_id),r.paper_settings?.enabled]),selectedRoute]);
  if(signature===activeSignature)return;activeSignature=signature;
  const root=empty($('route-list'));if(!rows.length){root.append(el('p',`No active ${routeMode==='confirmed'?'real':'paper'} routes.`,'empty'));return;}
  const wrap=el('div',null,'table-scroll'),table=el('table'),head=el('thead'),hr=el('tr'),body=el('tbody');
  for(const title of ['Route','Items held','Current step','Next unlock','Holding cost'])hr.append(el('th',title));head.append(hr);table.append(head,body);
  for(const r of rows){const row=el('tr',null,'selectable'+(selectedRoute===r.route_id?' selected':'')),cell=el('td'),button=el('button',r.route_id,'row-select');button.addEventListener('click',()=>selectRoute(r.route_id));cell.append(button);row.append(cell);
    for(const value of [Object.entries(r.open_assets).filter(([,q])=>q).map(([key,q])=>`x${q} ${key.replace(/^\d+:/,'')}`).join(', ')||'None',words(r.state),time(r.next_eligible_at),money(holdingCost(r.route_id)?.cost_cents)])row.append(el('td',value));row.addEventListener('click',e=>{if(!e.target.closest('button'))selectRoute(r.route_id);});body.append(row);}
  wrap.append(table);root.append(wrap);
}
function closeActive(){selectedRoute=null;activeSignature='';$('active-detail').hidden=true;}
async function selectRoute(id){
  if(pending)return;if(selectedRoute===id&&!$('active-detail').hidden){$('active-detail').scrollIntoView({block:'nearest'});return;}selectedRoute=id;const route=current.routes.find(r=>r.route_id===id);if(!route)return;
  $('active-detail').hidden=false;$('active-title').textContent=id;
  choices('real-receipt-route',route.mode==='confirmed'?[{id}]:[],r=>r.id);choices('event-route',[{id}],r=>r.id);
  $('receipt-panel').hidden=route.mode!=='confirmed';
  for(const node of ['receipt-panel','manual-panel','correction-panel'])$(node).open=false;
  receiptFields();eventFields();renderActiveSummary(route);renderActive(current.routes.filter(r=>openRoute(r)&&r.mode===routeMode));
  empty($('active-original-content')).append(el('p','Loading saved prediction…','muted'));
  try{const data=await api('/api/route-detail?route_id='+encodeURIComponent(id));if(selectedRoute===id)renderDetail($('active-original-content'),data);}catch(error){if(selectedRoute===id)notice(error.message);}
  $('active-detail').scrollIntoView({block:'nearest'});
}
function renderActiveSummary(route){
  const root=$('active-summary'),signature=JSON.stringify([route.state,route.open_assets,route.steam_wallet_available_cents,route.next_eligible_at,route.paper_settings,holdingCost(route.route_id)]);
  if(root.dataset.signature!==signature){root.dataset.signature=signature;empty(root);const facts=el('dl');pair(facts,'Current step',words(route.state));pair(facts,'Next unlock',time(route.next_eligible_at));pair(facts,'Holding cost',money(holdingCost(route.route_id)?.cost_cents));pair(facts,'Route Steam money',money(route.steam_wallet_available_cents));root.append(facts);
    if(route.overdue)root.append(el('p','Past the original expected finish time. Review this route.','warning'));
    if(route.mode==='paper'){const button=el('button',route.paper_settings?.enabled?'Pause paper steps':'Enable paper steps');button.addEventListener('click',()=>action({action:'paper',route_id:route.route_id,enabled:!route.paper_settings?.enabled}));root.append(button);}
    root.append(el('p','Holding cost is the recorded purchase cost, including Steam credit spent on return items.','muted'));
  }
  const events=(current.events||[]).filter(e=>e.route_id===route.route_id&&['movement','asset'].includes(e.kind));
  choices('correction-target',events.map(e=>({...e,id:e.event_id})),e=>`${e.reference} · ${e.kind==='movement'?money(e.net_delta_cents):e.quantity_delta+' items'}`);
}
function setMode(mode){
  routeMode=mode==='paper'?'paper':'confirmed';try{localStorage.setItem('arbitrage-route-mode',routeMode);}catch{}
  $('route-mode').value=routeMode;renderedSearchId='';activeSignature='';previewRequest++;paperPreview=null;selectedPrediction=null;$('paper-preview').hidden=true;closeActive();if(current)render(current);
}
function renderSearch(report){
  const id=routeMode+':'+(report?.record_id||'none');if(id===renderedSearchId)return;renderedSearchId=id;
  const root=empty($('search-result'));if(!report){root.append(el('p','Search saved prices to compare routes.','empty'));return;}
  const rows=report.predictions||[],groups=new Map();for(const p of rows){const key=JSON.stringify([p.item_a,p.item_b,p.sale_scenarios,p.route_priority]);if(!groups.has(key))groups.set(key,p);}
  root.append(el('p',`${routeMode==='paper'?'Paper':'Real'} search · ${time(report.at)} · ${money(report.capital_cents)} budget · ${rows.length} quantity/scenario choices`,'muted'));
  const coverage=report.catalogue_coverage;if(coverage){root.append(el('p',`${coverage.catalogue_size} catalogue items · ${coverage.checked_items} freshly checked · ${coverage.pending_items} pending. ${coverage.note}`,'muted'));const browse=el('details'),items=el('div'),more=el('button','Load more items');let offset=0,loading=false;const load=async()=>{if(loading||offset==null)return;loading=true;more.disabled=true;try{const page=await api('/api/catalogue?offset='+offset);for(const item of page.items)items.append(el('p',`${item.title} · DMarket ask ${money(item.dmarket_ask_cents)} · ${words(item.detail_status)}${item.detail_issues?.length?' · '+item.detail_issues.join(', '):''}`,'muted'));offset=page.next_offset;more.hidden=offset==null;}catch(error){notice(error.message);}finally{loading=false;more.disabled=false;}};browse.append(el('summary','Browse catalogue and pending checks'),items,more);browse.addEventListener('toggle',()=>{if(browse.open&&!items.childNodes.length)load();});more.addEventListener('click',load);root.append(browse);if(coverage.catalogue_error)root.append(el('p','Catalogue needs attention: '+words(coverage.catalogue_error),'warning'));}
  if(rows.length){root.append(routeTable([...groups.values()],rows));root.append(el('p','Best quantity for each pair, selling method and priority. Select a row for assumptions, fees and other quantities.','muted'));}
  else root.append(el('p',report.status==='no_available_funds'?report.note:'No route could be calculated from these prices and available funds.','empty'));
  if(report.excluded?.length||report.scenario_limits?.length){const details=el('details');details.append(el('summary','Missing prices and calculation limits'));for(const x of report.excluded||[])details.append(el('p',`${x.title}: ${x.message||words(x.reason)}`,'muted'));const seen=new Set();for(const x of report.scenario_limits||[]){const text=`${x.item_a} → ${x.item_b||'—'}: ${words(x.reason)}`;if(!seen.has(text)){details.append(el('p',text,'muted'));seen.add(text);}}root.append(details);}
}
function routeTable(rows,rankRows=rows){
  const wrap=el('div',null,'table-scroll'),table=el('table'),head=el('thead'),tr=el('tr'),body=el('tbody');
  for(const title of ['Rank','Sell scenario','Game A → B','Item A','Buy total A','Steam sale A','Steam net A','Item B','Buy total B','Sell total B','Sell net B','Profit','ROI'])tr.append(el('th',title));head.append(tr);table.append(head,body);
  const rank=new Map(rankRows.map((p,i)=>[p.prediction_id,i+1]));
  for(const p of rows){const row=el('tr',null,'selectable'+(selectedPrediction===p.prediction_id?' selected':'')),cell=el('td'),button=el('button','#'+rank.get(p.prediction_id),'row-select');button.setAttribute('aria-label',`Review rank ${rank.get(p.prediction_id)}: ${p.quantity_a} ${p.item_a} to ${p.item_b}. ${scenarioLabel(p)}`);button.addEventListener('click',()=>reviewPaper(p.prediction_id));cell.append(button);row.append(cell);row.dataset.predictionId=p.prediction_id;
    const scenario=el('td',null,'scenario');scenario.append(el('div','Steam: '+saleLabel(p.sale_scenarios?.steam)),el('div','DM: '+saleLabel(p.sale_scenarios?.dmarket)));if(p.route_priority)scenario.append(el('div',`Priority ${p.route_priority} · weakest selling leg`,'muted'));row.append(scenario);
    const game=app=>app===730?'CS2':app==null?'Unknown':String(app);row.append(el('td',`${game(p.item_a_identity?.app_id||p.app_id)} → ${game(p.item_b_identity?.app_id||p.app_id)}`));
    row.append(el('td',`x${p.quantity_a} ${p.item_a}`,'item'));
    for(const value of [p.entry_cost_cents,p.quote_legs?.steam_sale?.gross_cents,p.steam_proceeds_cents])row.append(el('td',money(value),'money'));
    row.append(el('td',`x${p.quantity_b} ${p.item_b}`,'item'));
    for(const value of [p.return_purchase_cents,p.quote_legs?.exit?.gross_cents,p.predicted_dmarket_receipts_cents])row.append(el('td',money(value),'money'));
    row.append(el('td',money(p.predicted_net_cents),'money '+(p.predicted_net_cents>0?'positive':p.predicted_net_cents<0?'negative':'')),el('td',roi(p),'money'));
    row.addEventListener('click',e=>{if(!e.target.closest('button'))reviewPaper(p.prediction_id);});body.append(row);
  }
  wrap.append(table);return wrap;
}
function linkedPrice(dl,label,value,url){const dd=el('dd'),a=el('a',money(value));if(url){a.href=url;a.target='_blank';a.rel='noopener noreferrer';dd.append(a);}else dd.textContent=money(value);dl.append(el('dt',label),dd);}
function renderDetail(root,data){
  empty(root);const p=data.prediction,q=p.quote_legs||{},columns=el('div',null,'columns');
  root.append(el('p',scenarioLabel(p),'muted'));
  for(const side of ['a','b']){const item=data.items[side],card=el('div',null,'detail-item'),facts=el('dl');card.append(el('h3',`Item ${side.toUpperCase()} · ${item.title}`));
    if(side==='a'){linkedPrice(facts,'DMarket buy total',p.entry_cost_cents,item.links.dmarket);linkedPrice(facts,'Steam lowest listing / item',item.steam_lowest_ask_cents,item.links.steam);linkedPrice(facts,'Steam highest order / item',item.steam_highest_bid_cents,item.links.steam);pair(facts,'Steam buyer pays · total',money(q.steam_sale?.gross_cents));pair(facts,'You receive · after fees',money(p.steam_proceeds_cents));}
    else{linkedPrice(facts,'Steam buy total',p.return_purchase_cents,item.links.steam);pair(facts,'Steam remainder',money(p.steam_wallet_residual_cents));linkedPrice(facts,'DMarket sell total · before fees',q.exit?.gross_cents,item.links.dmarket);pair(facts,'DMarket received · after fees',money(p.predicted_dmarket_receipts_cents));pair(facts,'Predicted profit',money(p.predicted_net_cents));pair(facts,'ROI',roi(p));}
    card.append(facts);const h=item.history;card.append(el('p',h?`${h.reported_dates} dates with reported activity · ${h.provider} · ${time(h.retrieved_at)}. ${h.note}`:'Historical activity: Unknown.','muted'));columns.append(card);
  }
  root.append(columns);
  if(p.sale_quality){const ranks=el('dl');pair(ranks,'Route priority',String(p.route_priority));for(const [venue,quality] of Object.entries(p.sale_quality)){const label=venue==='steam'?'Steam':'DMarket';pair(ranks,label+' priority',`${quality.priority} · ${words(quality.reason)}`);pair(ranks,label+' best-price spread',quality.best_spread_pct==null?'Unknown':quality.best_spread_pct+'%');pair(ranks,label+' batch spread',quality.batch_spread_pct==null?'Not applicable / unknown':quality.batch_spread_pct+'%');pair(ranks,label+' order coverage',quality.covered_quantity==null?'Assumed sale':`${quality.covered_quantity} of ${quality.quantity} items`);}root.append(ranks,el('p',`Minimum purchase ${money(p.search_settings.minimum_purchase_cents)} per item; narrow spread below ${p.search_settings.narrow_spread_bps/100}%. Buy orders are current observations, not promises after the wait.`,'muted'));}
  const totals=el('dl');pair(totals,'Net at stated prices',money(p.base_net_cents));pair(totals,'Learned adjustment',money(p.predicted_net_cents==null||p.base_net_cents==null?null:p.predicted_net_cents-p.base_net_cents));pair(totals,'Other assumed costs',money(p.other_cost_cents));pair(totals,'Profit including Steam remainder',money(p.predicted_net_cents==null||p.steam_wallet_residual_cents==null?null:p.predicted_net_cents+p.steam_wallet_residual_cents));pair(totals,'Minimum known wait',p.minimum_known_delay_seconds==null?'Not recorded':`${(p.minimum_known_delay_seconds/86400).toFixed(1)} days`);pair(totals,'Predicted completion',p.predicted_duration_seconds==null?'Unknown':`${(p.predicted_duration_seconds/86400).toFixed(1)} days`);root.append(totals,el('p','Future prices and sale times are unknown. Profit including the remainder contains Steam credit, not just DMarket money.','muted'));
  const evidence=el('details');evidence.append(el('summary','Fees, price levels and saved evidence'));
  for(const [key,label] of [['entry','Buy A'],['steam_sale','Sell A on Steam'],['return_purchase','Buy B'],['exit','Sell B']]){const leg=q[key];if(!leg){evidence.append(el('p',label+': detailed breakdown not recorded.','muted'));continue;}evidence.append(el('h3',label),el('p',`${leg.quantity} items · total ${money(leg.gross_cents)} · fees ${money(leg.fee_cents)} · net ${money(leg.net_cents)}`));if(leg.quantity_is_assumed)evidence.append(el('p',`Assumed sale at ${money(leg.assumed_price_cents)} per item; no sale or completion time is confirmed.`,'muted'));for(const fill of leg.fills||[])evidence.append(el('p',`${fill.quantity} × ${money(fill.price_cents)} → ${money(fill.net_unit_cents)} each after fees`,'muted'));}
  evidence.append(el('p',`Model: ${p.model_version} · ${p.training_sample_count||0} completed outcomes`,'muted'));
  for(const [leg,source] of Object.entries(p.price_sources||{})){for(const r of source.sources||[source])evidence.append(el('p',`${words(leg)} · ${time(r.source_time)} · ${r.evidence_id||''}`,'muted'));}
  for(const id of p.evidence_ids||[])evidence.append(el('p',id,'muted'));root.append(evidence);
}
function comparisonTable(rows){const wrap=el('div',null,'table-scroll'),table=el('table'),head=el('tr');for(const name of ['Scenario','Status','Steam gross','Steam net','Remainder','DMarket received','Profit','Including Steam remainder'])head.append(el('th',name));table.append(head);for(const p of rows){const row=el('tr');for(const value of [scenarioLabel(p),'Conditional estimate',money(p.quote_legs?.steam_sale?.gross_cents),money(p.steam_proceeds_cents),money(p.steam_wallet_residual_cents),money(p.predicted_dmarket_receipts_cents),money(p.predicted_net_cents),money(p.predicted_net_cents==null?null:p.predicted_net_cents+p.steam_wallet_residual_cents)])row.append(el('td',value));table.append(row);}wrap.append(table);return wrap;}
async function reviewPaper(id){
  if(pending)return;const serial=++previewRequest,mode=routeMode;selectedPrediction=id;for(const row of document.querySelectorAll('[data-prediction-id]'))row.classList.toggle('selected',row.dataset.predictionId===id);paperPreview=null;$('paper-preview').hidden=false;$('paper-entry-button').disabled=true;$('entry-note').textContent='Checking entry…';
  empty($('preview-comparison'));empty($('quantity-content'));$('quantity-alternatives').hidden=true;
  empty($('paper-preview-content')).append(el('p','Loading saved estimate…','muted'));
  try{const [data,review]=await Promise.all([api('/api/route-detail?prediction_id='+encodeURIComponent(id)),api('/api/action',{action:mode==='confirmed'?'preview_real':'preview_paper',prediction_id:id})]);if(serial!==previewRequest||mode!==routeMode)return;
    paperPreview=review;renderDetail($('paper-preview-content'),data);for(const blocker of review.blockers)$('paper-preview-content').append(el('p',blocker,'warning'));
    const p=data.prediction,report=reportForMode(),all=(report?.predictions||[]).filter(r=>r.item_a===p.item_a&&r.item_b===p.item_b),comp=new Map();for(const r of all)if(!comp.has(JSON.stringify(r.sale_scenarios)))comp.set(JSON.stringify(r.sale_scenarios),r);
    empty($('preview-comparison')).append(el('h3','Selling scenarios'),comparisonTable([...comp.values()]));
    const alternatives=all.filter(r=>JSON.stringify(r.sale_scenarios)===JSON.stringify(p.sale_scenarios));empty($('quantity-content')).append(routeTable(alternatives,report?.predictions||alternatives));$('quantity-alternatives').open=false;$('quantity-alternatives').hidden=false;
    $('paper-entry-button').textContent=mode==='confirmed'?'Start real trial':'Start paper trial';$('paper-entry-button').disabled=!review.ready;
    $('entry-note').textContent=mode==='confirmed'?'Saves the prediction and reserves money. Make purchases manually, then record the receipts.':'Uses the shared paper budget. No real order is placed.';
    $('paper-route-name').value=`${mode==='confirmed'?'real':'paper'}-${new Date().toISOString().slice(0,10)}-${crypto.randomUUID().slice(0,8)}`;
    $('paper-preview').scrollIntoView({block:'nearest'});
  }catch(error){if(serial===previewRequest){notice(error.message);$('entry-note').textContent='Review unavailable. Search again when the data is ready.';}}
}
function renderFunds(s){const f=s.real_funds,w=s.steam_wallet||{},root=empty($('funds-cards'));metric(root,'DMarket regular',money(f.configured?(f.accounts.dmarket_regular||0):null));metric(root,'DMarket Tradable',money(f.configured?(f.accounts.dmarket_tradable||0):null),'Restrictions still apply');metric(root,'Steam wallet',money(w.balance_cents),w.configured?'Based on balance record at '+time(w.as_of):'Record your full wallet balance');metric(root,'CSFloat','Not connected','Excluded from wallet totals');metric(root,'Invested in active routes',money(s.holding_costs?.confirmed.cost_cents),'Purchase cost of items still held');metric(root,'Reserved for plans',money(f.reserved_cents),'Not yet spent; still part of DMarket wallet');$('funds-note').textContent=w.needs_reconciliation?'Steam records currently imply a negative balance. Check receipts or record a current wallet reconciliation.':'Real money only. Steam credit and item purchase costs are shown separately; these are not a cash valuation.';
  const history=empty($('funding-history'));for(const r of f.records)history.append(el('p',`${time(r.at)} · ${words(r.kind)} · regular ${money(r.regular_cents)} · Tradable ${money(r.tradable_cents)} · ${r.reference}`,'muted'));if(!f.records.length)history.append(el('p','No DMarket funds recorded.','muted'));
  choices('funding-correction-target',f.records.map(r=>({...r,id:r.funding_id})),r=>`${words(r.kind)} · ${r.reference}`);
  const wh=empty($('steam-wallet-history'));for(const r of w.records||[])wh.append(el('p',`${time(r.at)} · ${words(r.kind)} · ${money(r.amount_cents)} · ${r.reference}`,'muted'));if(!w.records?.length)wh.append(el('p','No Steam wallet records.','muted'));choices('steam-correction-target',(w.records||[]).map(r=>({...r,id:r.wallet_id})),r=>`${words(r.kind)} · ${r.reference}`);
}
function renderDebug(s){$('health-note').textContent=s.coverage;const errors=empty($('health-errors'));for(const [provider,r] of Object.entries(s.health.source_states||{}))errors.append(el('p',`${r.provider||provider}${r.scope==='item'?' · '+r.title:r.scope==='endpoint'?' · '+words(r.kind):''}: ${words(r.error||r.reason)}${r.next_retry_at?' · retry '+time(r.next_retry_at):''}`,'muted'));if(s.health.reason)errors.append(el('p',words(typeof s.health.reason==='string'?s.health.reason:JSON.stringify(s.health.reason)),'muted'));if(!errors.childNodes.length)errors.append(el('p','No current source blockers.','muted'));empty($('request-details'));for(const [source,count] of Object.entries(s.requests))$('request-details').append(el('p',`${source}: ${count} requests used`,'muted'));const gaps=empty($('gaps'));for(const gap of s.gaps)gaps.append(el('p',`${gap.hours} hours · ${time(gap.from)} → ${time(gap.to)}`,'muted'));if(!s.gaps.length)gaps.append(el('p','No recorded gaps over seven hours.','muted'));if(!$('report-links').childNodes.length)for(const name of s.reports){const link=el('a',words(name.replace('.md','').toLowerCase()));link.href='/api/report?name='+encodeURIComponent(name);link.target='_blank';link.rel='noopener';$('report-links').append(link);}}
function renderResults(s){const signature=JSON.stringify([s.outcomes,s.paper_decisions,s.routes.filter(r=>r.written_off_wallet_cents||Object.values(r.written_off_holdings||{}).some(Boolean))]);if(signature===resultSignature)return;resultSignature=signature;const root=empty($('result-list'));for(const mode of ['confirmed','paper']){const box=el('article',null,'panel');box.append(el('h2',`${mode==='confirmed'?'Real':'Paper'} results`));const rows=s.outcomes.filter(o=>o.mode===mode);if(!rows.length)box.append(el('p','No resolved routes yet.','muted'));for(const o of rows)box.append(outcomeCard(o));root.append(box);}empty($('decision-list'));for(const d of s.paper_decisions.slice().reverse())$('decision-list').append(el('p',`${time(d.at)} · ${d.route_id} · ${words(d.action)}`,'muted'));const recovery=s.routes.filter(r=>r.written_off_wallet_cents||Object.values(r.written_off_holdings||{}).some(Boolean));$('recovery-panel').hidden=!recovery.length;choices('recovery-parent',recovery.map(r=>({...r,id:r.route_id})),r=>r.route_id);empty($('recovery-holdings'));for(const r of recovery)$('recovery-holdings').append(el('p',`${r.route_id}: ${JSON.stringify(r.written_off_holdings)}; Steam ${money(r.written_off_wallet_cents)} originally written off.`,'muted'));}
async function refresh(){try{render(await api('/api/status'));$('connection').textContent='Connected locally';}catch(error){$('connection').textContent='Disconnected · displayed data may be old';$('worker-status').textContent='Connection lost';}}
function navigate(){const hash=location.hash.slice(1)||'overview',parts=hash.split('?'),name=parts[0],page=name==='search'?'routes':['overview','routes','funds','results','controls'].includes(name)?name:'overview';for(const id of ['overview','routes','funds','results','controls'])$(id).hidden=id!==page;for(const a of document.querySelectorAll('nav a'))a.classList.toggle('active',a.hash==='#'+page);$('page-title').textContent=page[0].toUpperCase()+page.slice(1);if(name==='search')$('route-search').scrollIntoView({block:'start'});}
function field(label,name,type='text',options){const l=el('label',label),input=el(options?'select':'input');input.name=name;if(options)for(const text of options){const o=el('option',words(text));o.value=text;input.append(o);}else input.type=type;if(type==='number'){input.step='1';input.required=true;}l.append(input);return l;}
function eventFields(){const actualTime=$("event-form").querySelector("input[name=at]");actualTime.step="0.001";actualTime.value=localDateTime();const target=empty($('event-fields')),kind=$('event-kind').value,grid=el('div',null,'form-grid');if(kind==='progress'){grid.append(field('Stage','stage','text',['entered','awaiting_transfer','steam_locked','awaiting_steam_sale','steam_wallet','return_item_locked','awaiting_dmarket_sale','awaiting_settlement','awaiting_csfloat_sale','csfloat_pending','awaiting_payout']),field('Next unlock (optional)','next_eligible_at','datetime-local'),field('Note','note'));}if(kind==='movement')grid.append(field('Account','account','text',['dmarket_regular','dmarket_tradable','steam_wallet','external_cost','csfloat_deposited','csfloat_spendable','csfloat_pending','csfloat_withdrawable','cash_received']),field('Net change in cents (negative = spent)','net_delta_cents','number'),field('Fee already included in net change, cents','fee_cents','number'));if(kind==='asset')grid.append(field('Exact item title or asset key','asset_key'),field('Item change (negative = sold)','quantity_delta','number'));if(kind==='resolve'){grid.append(field('Outcome','resolution','text',['completed','liquidated','write_off']),field('Leftover treatment','residual_disposition','text',['none','written_off']));const l=el('label','I confirm there are no pending operations and this outcome is final.','checkbox');const input=el('input');input.type='checkbox';input.name='confirmed';input.required=true;l.prepend(input);grid.append(l);}target.append(grid);}

const submissionKeys=new WeakMap();
function submissionId(form,kind,fields){
  const signature=JSON.stringify([kind,fields]),old=submissionKeys.get(form);
  if(old?.signature===signature)return old.id;
  const value={signature,id:crypto.randomUUID(),at:new Date().toISOString()};submissionKeys.set(form,value);return value.id;
}
function submissionTime(form){return submissionKeys.get(form).at;}
function dollars(value){
  const text=String(value).trim().replace(',','.');
  if(!/^\d+(\.\d{1,2})?$/.test(text))throw new Error('Enter a positive USD amount with at most two decimal places.');
  const [whole,decimal='']=text.split('.'),cents=Number(whole)*100+Number(decimal.padEnd(2,'0'));
  if(!Number.isSafeInteger(cents))throw new Error('Amount is too large.');return cents;
}
function choices(id,rows,label){
  const select=$(id),signature=JSON.stringify(rows.map(r=>[r.id,label(r)]));if(select.dataset.signature===signature)return;select.dataset.signature=signature;const old=select.value;empty(select);
  for(const row of rows){const option=el('option',label(row));option.value=row.id;select.append(option);}
  if(rows.some(row=>row.id===old))select.value=old;
}

function usdField(label,name){const control=field(label,name);const input=control.querySelector('input');input.inputMode='decimal';input.required=true;input.value='0.00';return control;}
function receiptFields(){
  const date=$('real-receipt-form').querySelector('input[name=at]');date.step='0.001';date.value=localDateTime();
  const root=empty($('real-receipt-fields')),grid=el('div',null,'form-grid'),kind=
$('real-receipt-kind').value;
  if(['buy_a','sell_a','buy_b','sell_b'].includes(kind))grid.append(field('Number of items','quantity','number'));
  if(kind==='buy_a'){
    grid.append(usdField('Paid from Regular, USD','regular'),usdField('Paid from Tradable, USD','tradable'),usdField('Fees already included, USD','fee'));
    const label=el('label','These are all the initial purchases for this route.','checkbox'),input=el('input');input.type='checkbox';input.name='entry_complete';input.checked=true;label.prepend(input);grid.append(label);
  }
  if(['buy_b','sell_b'].includes(kind)){
    const label=field('Actual return item title','item_title'),route=current?.routes.find(r=>r.route_id===$('real-receipt-route').value);
    label.querySelector('input').value=route?.prediction.item_b||'';label.querySelector('input').required=true;grid.append(label);
  }
  if(['sell_a','buy_b','sell_b','cost'].includes(kind))grid.append(usdField(kind==='buy_b'||kind==='cost'?'Total paid, USD':'Total received after fees, USD','net'));
  if(['sell_a','buy_b','sell_b'].includes(kind))grid.append(usdField('Fees already included in that total, USD','fee'));
  if(['transfer_a','buy_b'].includes(kind))grid.append(field('Actual unlock time (leave blank if unknown)','next_eligible_at','datetime-local'));
  if(kind==='sell_b')grid.append(field('DMarket account credited','account','text',['dmarket_tradable','dmarket_regular']));
  if(kind==='cancel')grid.append(field('Why this unused plan is being cancelled','reason'));
  root.append(grid);
}
$('real-receipt-kind').addEventListener('change',receiptFields);
$('real-receipt-route').addEventListener('change',receiptFields);
$('funding-form').addEventListener('submit',async event=>{
  event.preventDefault();if(pending)return;
  try{const f=Object.fromEntries(new FormData(event.target));const request={kind:f.kind,at:new Date(f.at).toISOString(),reference:f.reference.trim(),regular_cents:dollars(f.regular),tradable_cents:dollars(f.tradable)};
    request.funding_id=submissionId(event.target,'funding',request);await action({action:'funding',funding:request});
  }catch(error){notice(error.message);}
});
$('funding-correction-form').addEventListener('submit',async event=>{
  event.preventDefault();if(pending)return;
  try{const f=Object.fromEntries(new FormData(event.target));const request={funding_id:f.funding_id,reason:f.reason.trim(),regular_cents:dollars(f.regular),tradable_cents:dollars(f.tradable)};
    request.correction_id=submissionId(event.target,'funding-correction',request);request.at=submissionTime(event.target);await action({action:'correct_funding',correction:request});
  }catch(error){notice(error.message);}
});
$('real-receipt-form').addEventListener('submit',async event=>{
  event.preventDefault();if(pending)return;
  try{const f=Object.fromEntries(new FormData(event.target));const request={route_id:f.route_id,kind:f.kind,at:new Date(f.at).toISOString(),reference:f.reference.trim()};
    if('quantity' in f)request.quantity=Number(f.quantity);
    for(const [from,to] of [['regular','regular_cents'],['tradable','tradable_cents'],['net','net_cents'],['fee','fee_cents']])if(from in f)request[to]=dollars(f[from]);
    if(f.kind==='buy_a')request.entry_complete=f.entry_complete==='on';
    for(const key of ['item_title','account','reason'])if(key in f)request[key]=f[key].trim();
    if('next_eligible_at' in f)request.next_eligible_at=f.next_eligible_at?new Date(f.next_eligible_at).toISOString():null;
    request.action_id=submissionId(event.target,'receipt',request);await action({action:'real_step',receipt:request});
  }catch(error){notice(error.message);}
});

function outcomeCard(o){
  const box=el('details'),facts=el('dl');
  box.append(el('summary',`${o.route_id}: ${money(o.actual_net_cents)} · ${words(o.resolution)} · ${o.input_kind}`));
  pair(facts,'Actual net result',money(o.actual_net_cents));
  pair(facts,'Difference from original net estimate',money(o.net_error_cents));
  pair(facts,'Actual initial cost',money(o.actual_entry_cost_cents));
  pair(facts,'Initial cost difference',money(o.entry_cost_error_cents));
  pair(facts,'Steam sale receipt difference',money(o.steam_receipts_error_cents));
  pair(facts,'Steam purchase cost difference',money(o.return_purchase_error_cents));
  pair(facts,'Destination receipt difference',money(o.destination_receipts_error_cents));
  pair(facts,'Reported fees already included in receipts',money(o.fee_cents_reported));
  pair(facts,'Actual duration',`${(o.actual_duration_seconds/86400).toFixed(2)} days`);
  pair(facts,'Duration difference',o.duration_error_seconds==null?'Original completion time was unknown':`${(o.duration_error_seconds/86400).toFixed(2)} days`);
  box.append(facts);return box;
}

document.addEventListener('click',event=>{const button=event.target.closest('button[data-action]');if(button)action({action:button.dataset.action});});
$('route-mode').value=routeMode;$('route-mode').addEventListener('change',event=>setMode(event.target.value));
$('close-active').addEventListener('click',()=>{closeActive();if(current)render(current);});
$('close-preview').addEventListener('click',()=>{previewRequest++;paperPreview=null;selectedPrediction=null;$('paper-preview').hidden=true;});
$('search-settings-form').addEventListener('submit',event=>{event.preventDefault();try{const cents=value=>{if(!/^\d+(?:\.\d{1,2})?$/.test(value.trim()))throw new Error('Use a positive number with up to two decimal places.');const [whole,fraction='']=value.trim().split('.');return Number(whole)*100+Number(fraction.padEnd(2,'0'));};action({action:'search_settings',settings:{minimum_purchase_cents:cents($('search-minimum').value),narrow_spread_bps:cents($('search-spread').value)}});}catch(error){notice(error.message);}});
$('search-form').addEventListener('submit',event=>{event.preventDefault();action({action:'search',purpose:'grow',mode:routeMode});});
$('paper-entry-form').addEventListener('submit',async event=>{
  event.preventDefault();if(pending||!paperPreview?.ready)return;pending=true;$('paper-entry-button').disabled=true;
  const entry={route_id:$('paper-route-name').value.trim(),prediction_id:paperPreview.prediction_id},mode=routeMode;
  try{const result=await api('/api/action',{action:mode==='confirmed'?'enter_real':'enter_paper',entry});notice(`${result.mode==='confirmed'?'Real':'Paper'} trial ${result.route_id} started. No market order was placed.`);paperPreview=null;selectedPrediction=null;$('paper-preview').hidden=true;await refresh();pending=false;await selectRoute(result.route_id);}
  catch(error){notice(error.message);}finally{pending=false;$('paper-entry-button').disabled=!paperPreview?.ready;}
});
$('event-kind').addEventListener('change',eventFields);
$('event-form').addEventListener('submit',async event=>{
  event.preventDefault();if(pending)return;try{const f=Object.fromEntries(new FormData(event.target)),kind=f.kind,record={event_id:submissionId(event.target,'event',f),route_id:f.route_id,at:new Date(f.at).toISOString(),kind,reference:f.reference};
    if(kind==='progress')Object.assign(record,{stage:f.stage,next_eligible_at:f.next_eligible_at?new Date(f.next_eligible_at).toISOString():null,note:f.note});
    if(kind==='movement')Object.assign(record,{account:f.account,net_delta_cents:Number(f.net_delta_cents),fee_cents:Number(f.fee_cents)});
    if(kind==='asset')Object.assign(record,{asset_key:f.asset_key,quantity_delta:Number(f.quantity_delta)});
    if(kind==='resolve')Object.assign(record,{resolution:f.resolution,residual_disposition:f.residual_disposition,pending_operations:0,confirmed:f.confirmed==='on'});
    await action({action:'event',event:record});
  }catch(error){notice(error.message);}
});
$('correction-form').addEventListener('submit',event=>{event.preventDefault();const f=Object.fromEntries(new FormData(event.target));const target=current.events.find(e=>e.event_id===f.target_event_id);if(!target)return;action({action:'correction',correction:{correction_id:submissionId(event.target,'correction',f),route_id:target.route_id,target_event_id:target.event_id,at:submissionTime(event.target),reference:f.reference,reason:f.reason,value:Number(f.value),fee_cents:Number(f.fee_cents)}});});
$('recovery-form').addEventListener('submit',event=>{event.preventDefault();const f=Object.fromEntries(new FormData(event.target));const request={route_id:f.route_id,parent_route_id:f.parent_route_id,reference:f.reference,asset_key:f.asset_key,quantity:Number(f.quantity),steam_wallet_cents:Number(f.steam_wallet_cents)};submissionId(event.target,'recovery',request);request.at=submissionTime(event.target);action({action:'recovery',recovery:request});});
function signedDollars(value){const text=String(value).trim();return text.startsWith('-')?-dollars(text.slice(1)):dollars(text.replace(/^\+/,''));}
$('steam-wallet-form').addEventListener('submit',event=>{event.preventDefault();if(pending)return;try{const f=Object.fromEntries(new FormData(event.target)),request={kind:f.kind,at:new Date(f.at).toISOString(),reference:f.reference.trim(),amount_cents:signedDollars(f.amount)};request.wallet_id=submissionId(event.target,'steam-wallet',request);action({action:'steam_wallet',record:request});}catch(error){notice(error.message);}});
$('steam-correction-form').addEventListener('submit',event=>{event.preventDefault();if(pending)return;try{const f=Object.fromEntries(new FormData(event.target)),request={wallet_id:f.wallet_id,reason:f.reason.trim(),amount_cents:signedDollars(f.amount)};request.correction_id=submissionId(event.target,'steam-correction',request);request.at=submissionTime(event.target);action({action:'correct_steam_wallet',correction:request});}catch(error){notice(error.message);}});
$('log-button').addEventListener('click',async()=>{try{$('log').textContent=JSON.stringify(await api('/api/log'),null,2);$('log').hidden=false;}catch(error){notice(error.message);}});
window.addEventListener('hashchange',navigate);navigate();eventFields();receiptFields();
for(const input of document.querySelectorAll('input[name=at]')){input.step='0.001';input.value=localDateTime();}
(async()=>{try{token=(await api('/api/session')).token;await refresh();}catch(error){notice('Cannot reach the local server. Open the Local desk launcher.');}setInterval(refresh,5000);})();
// Preserve the optional local read-status and collection controls.
if(document.modelContext?.registerTool){
  const lifecycle=new AbortController();window.addEventListener('pagehide',()=>lifecycle.abort(),{once:true});
  const tools=[{name:'read_bot_status',title:'Read bot status',description:'Read local collection status and route holdings.',inputSchema:{type:'object',properties:{},additionalProperties:false},annotations:{readOnlyHint:true,untrustedContentHint:true},async execute(input){if(!input||Object.keys(input).length)throw new Error('No arguments expected');const s=await api('/api/status');render(s);return {paused:s.controls.paused,busy:s.busy,next_check:s.next_action_at,routes:s.routes.map(r=>({id:r.route_id,mode:r.mode,stage:r.state,holdings:r.open_assets}))};}},
  {name:'set_collection_state',title:'Pause or resume collection',description:'Pause or resume the local collector.',inputSchema:{type:'object',properties:{state:{type:'string',enum:['pause','resume']}},required:['state'],additionalProperties:false},annotations:{readOnlyHint:false,untrustedContentHint:false},async execute(input){if(!input||Object.keys(input).length!==1||!['pause','resume'].includes(input.state))throw new Error('Choose pause or resume');const result=await api('/api/action',{action:input.state});await refresh();return result;}}];
  for(const tool of tools){try{Promise.resolve(document.modelContext.registerTool(tool,{signal:lifecycle.signal})).catch(()=>{});}catch{}}
}
