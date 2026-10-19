'use strict';
const $=s=>document.querySelector(s);
const staticMode=!['127.0.0.1','localhost'].includes(location.hostname);
const apiPath=name=>staticMode?'data/'+name:'/api/'+name;
if(staticMode){
 const nativeFetch=window.fetch.bind(window);
 const staticData={'/api/state':'data/state.json','/api/tracking':'data/tracking.json',
  '/api/tracking/history':'data/tracking-history.json','/api/opportunity-tracking':'data/opportunity-tracking.json'};
 window.fetch=(input,init)=>nativeFetch(staticData[input]||input,init);
}
let state=null,tab='consistent',selectedId=null,lastFingerprint='',pendingSettings=null,lastFocus=null;
const n=v=>v===null||v===undefined||v===''?null:Number(v);
const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const num=(v,d=2)=>n(v)===null||!Number.isFinite(n(v))?'—':new Intl.NumberFormat('it-IT',{maximumFractionDigits:d,minimumFractionDigits:d===2?2:0}).format(n(v));
const money=(v,d=2)=>n(v)===null?'—':`${n(v)<0?'−':''}$${num(Math.abs(n(v)),d===2&&Math.abs(n(v))>0&&Math.abs(n(v))<0.01?4:d)}`;
const compact=v=>n(v)===null?'Non disponibile':new Intl.NumberFormat('it-IT',{notation:'compact',maximumFractionDigits:1}).format(n(v))+' USD';
const dt=v=>v?new Date(Number(v)*1000).toLocaleString('it-IT',{timeZone:'Europe/Rome',dateStyle:'medium',timeStyle:'medium'}):'Non disponibile';
const venue=v=>({'polymarket':'Polymarket','predict':'Predict.fun','limitless':'Limitless','opinion':'Opinion'}[v]||v);
const day=v=>n(v)===null?'Non disponibile':new Date(Number(v)*1000).toLocaleDateString('it-IT',{timeZone:'Europe/Rome',dateStyle:'medium'});
const expiryCell=o=>o.allocations.map(a=>`<small class="cell-sub">${esc(venue(a.venue))} · ${a.expiry_at?esc(day(a.expiry_at)):'n.d.'}</small>`).join('');
const id=o=>o.allocations.map(a=>`${a.venue}:${a.market}:${a.outcome}`).join('|')+'|budget:'+o.budget;
const ruleStatus=o=>o.rules?.status||'review';
const ruleName=s=>({consistent:'Coerenza testuale',review:'Da verificare',conflict:'Conflitto rilevato'}[s]||'Da verificare');
const stale=()=>staticMode?false:(!state||!state.scanner_alive||Date.now()/1000-Number(state.timestamp)>120);
function safeUrl(value){try{const u=new URL(value);return u.protocol==='https:'&&['polymarket.com','predict.fun','limitless.exchange','app.opinion.trade','opinion.trade'].includes(u.hostname)?u.href:null}catch{return null}}
function link(url,label){const safe=safeUrl(url);return safe?`<a class="market-link" href="${esc(safe)}" target="_blank" rel="noopener noreferrer">${esc(label)} ↗</a>`:`<span class="value-neutral">Link in aggiornamento</span>`}
function category(title){const t=title.toLowerCase();if(/fdv|bitcoin|ethereum|btc |token|protocol|crypto/.test(t))return ['crypto','◈'];if(/nba|liga|league| fc |finals|champion|match|win the 2027/.test(t))return ['sport','◉'];if(/fed|rate|valuation|ipo|merger|stock|price/.test(t))return ['economy','↗'];return ['','▧']}
function uniqueByMarket(items){const unique=new Map();for(const o of items){const key=o.allocations.map(a=>`${a.venue}:${a.market}:${a.outcome}`).sort().join('|');const previous=unique.get(key);if(!previous||n(o.budget)>n(previous.budget))unique.set(key,o)}return [...unique.values()]}
function rows(){
 let data=(state?.opportunities||[]).filter(o=>o.full_budget_executable&&(tab==='all'||ruleStatus(o)===tab));
 const query=$('#search').value.toLowerCase().trim();
 if(query)data=data.filter(o=>(o.name+' '+o.allocations.map(a=>venue(a.venue)).join(' ')).toLowerCase().includes(query));
 const budget=$('#budget-filter').value;
 if(budget!=='all')data=data.filter(o=>n(o.budget)===n(budget));
 data=uniqueByMarket(data);
 const sort=$('#view-sort').value;
 return data.sort((a,b)=>(Number(ruleStatus(a)==='conflict')-Number(ruleStatus(b)==='conflict'))||(sort==='profit'?n(b.profit)-n(a.profit):sort==='exit'?(n(b.convergence_exit?.profit)??-1e20)-(n(a.convergence_exit?.profit)??-1e20):(n(b.liquidity?.volume24h_min)??-1)-(n(a.liquidity?.volume24h_min)??-1)||n(b.profit)-n(a.profit)))
}
function renderRows(){if(!state)return;const data=rows();const uniqueTotal=uniqueByMarket((state.opportunities||[]).filter(o=>o.full_budget_executable)).length;$('#loading-state').hidden=true;$('#empty-state').hidden=data.length>0;$('#list-count').textContent=uniqueTotal;$('#showing-count').textContent=`${data.length} di ${uniqueTotal} coppie uniche nel ciclo`;$('#opportunity-rows').innerHTML=data.map(o=>{const [cat,icon]=category(o.name);const scenario=o.convergence_exit;let exitHtml='<span class="value-neutral">Scenario non disponibile</span>';let exitRoiHtml='<span class="value-neutral">—</span>';if(scenario){exitHtml=`<span class="${n(scenario.profit)>0?'value-positive':'value-negative'}">${n(scenario.profit)>0?'+':''}${money(scenario.profit)}</span><small class="cell-sub">bid YES + NO = $1 · simulazione</small>`;const netRoi=n(scenario.roi_pct);exitRoiHtml=`<span class="${netRoi>0?'value-positive':'value-negative'}">${num(netRoi,2)}%</span><small class="cell-sub">netto fee piattaforma</small>`}const r=ruleStatus(o);return `<tr data-budget="${esc(o.budget)}"><td><div class="event-cell"><span class="event-icon ${cat}">${icon}</span><div><span class="event-name">${esc(o.name)}</span><div class="venue-pair">${o.allocations.map(a=>`<span class="venue-dot ${esc(a.venue)}"></span>${esc(venue(a.venue))}`).join('<span>·</span>')}</div></div></div></td><td>${esc(compact(o.liquidity?.volume24h_min))}<small class="cell-sub">${o.liquidity?.volume_complete?'Lato meno attivo':'Dato mancante su un lato'}</small></td><td>${expiryCell(o)}</td><td><span class="value-positive">+${money(o.profit)}</span><small class="cell-sub">budget ${money(o.budget)} · ${num(o.roi_pct,2)}% netto fee</small></td><td>${exitHtml}</td><td>${exitRoiHtml}</td><td><span class="badge ${r}">${ruleName(r)}</span></td><td><button class="details-button" data-detail="${esc(id(o))}" aria-label="Dettagli: ${esc(o.name)}">Dettagli ↗</button></td></tr>`}).join('')}
function renderSummary(){const opportunities=(state.opportunities||[]).filter(o=>o.full_budget_executable&&n(o.budget)===n(state.ui_settings?.budget??state.budget));const healthy=!stale();$('#metric-opps').textContent=opportunities.length;$('#nav-count').textContent=opportunities.length;$('#metric-rules').textContent=opportunities.filter(o=>ruleStatus(o)==='consistent').length;$('#metric-pairs').textContent=new Intl.NumberFormat('it-IT').format(state.coverage?.candidate_pairs||0);$('#metric-coverage').textContent=`${state.coverage?.selected_pairs||0} coppie seguite · ${state.coverage?.successful_books||0} book letti`;$('#metric-time').textContent=state.timestamp?new Date(state.timestamp*1000).toLocaleTimeString('it-IT',{timeZone:'Europe/Rome'}):'—';$('#metric-age').textContent=healthy?'Dati locali aggiornati':'Dati non aggiornati';$('#live-status').classList.toggle('offline',!healthy);$('#live-status').innerHTML=`<i></i> ${healthy?'Scanner live':'Scanner da controllare'}`;$('#footer-status').textContent=`${state.coverage?.catalog_markets||0} mercati nel catalogo attivo`;
$('#feed-list').innerHTML=['polymarket','predict','limitless','opinion'].map(v=>{const active=(state.enabled_venues||[]).includes(v);const status=state.streams?.[v];const label=!active?'Disattivata':status==='connected'?'Connesso':status==='rest_fallback_missing_api_key'?'REST pubblico':status?'In riconnessione':'In attesa';return `<div class="feed"><span><i class="${active&&status==='connected'?'ok':''}"></i>${venue(v)}</span><small>${label}</small></div>`}).join('');const banner=$('#stale-banner');banner.hidden=healthy;if(!healthy)banner.textContent='Dati scaduti o scanner fermo. I piani sono consultabili, ma non usare quantità e prezzi senza un nuovo ciclo live.';
if(pendingSettings!==null){const sameVenues=[...(state.settings?.enabled_venues||[])].sort().join(',')===[...(pendingSettings.enabled_venues||[])].sort().join(',');const sameTracking=n(state.settings?.tracking_min_yield_pct)===n(pendingSettings.tracking_min_yield_pct)&&n(state.settings?.tracking_target_yield_pct)===n(pendingSettings.tracking_target_yield_pct)&&n(state.settings?.tax_rate_pct)===n(pendingSettings.tax_rate_pct);if(n(state.budget)===n(pendingSettings.budget)&&state.settings?.sort_policy===pendingSettings.sort_policy&&n(state.settings?.min_volume24h)===n(pendingSettings.min_volume24h)&&sameVenues&&sameTracking){$('#settings-status').textContent='Impostazioni applicate. Piattaforme, budget e parametri di tracking sono aggiornati.';pendingSettings=null}else{$('#settings-status').textContent='Ricalcolo in corso… attendo il prossimo ciclo dello scanner.'}}
}
function planText(o){return `${o.name}\nSnapshot: ${dt(state.timestamp)}\nSTIMA — ${ruleName(ruleStatus(o))}; nessuna garanzia del 100%\n${o.allocations.map(a=>`Compra ${a.outcome} su ${venue(a.venue)}: ${num(a.gross_shares,6)} quote lorde, ${num(a.net_shares,6)} nette; prezzo medio $${num(a.price_avg??n(a.notional)/n(a.gross_shares),6)}, limite $${num(a.limit_price,6)}; costo ${money(a.total,6)}; fee cash ${money(a.cash_fee,6)}, fee quote ${num(a.withheld_shares,6)}; scadenza ${a.expiry_at?day(a.expiry_at):'n.d.'}.\n${safeUrl(a.url)||'Link non disponibile'}`).join('\n')}\nScenario bid YES + NO = $1 (ipotetico): ${money(o.convergence_exit?.profit,6)} netti (${num(o.convergence_exit?.roi_pct,2)}% sul capitale), profondità futura non verificata.\nSpesa totale ${money(o.total_cost,6)}; costi/riserva ${money(o.fixed_cost,6)}; payout nominale ${money(o.payout,6)}; profitto condizionato a risoluzione ${money(o.profit,6)}.\nControllo bid attuali: ${o.early_exit?.fully_sellable?money(o.early_exit.profit,6):'liquidità insufficiente per tutte le quote'}.`}
function drawer(o){const r=ruleStatus(o),exit=o.early_exit;$('#drawer-title').textContent=o.name;const plan=o.allocations.map(a=>`<article class="plan-card"><div class="plan-top"><span class="plan-side ${a.outcome==='NO'?'no':''}">COMPRA ${esc(a.outcome)} · ${esc(venue(a.venue))}</span>${link(a.url,'Apri mercato')}</div><div class="plan-shares">${num(a.gross_shares,6)} <small>quote lorde da acquistare</small></div><div class="definition"><span>Scadenza mercato</span><b>${a.expiry_at?esc(day(a.expiry_at)):'Non disponibile'}</b><span>Prezzo medio / limite massimo</span><b>$${num(a.price_avg??n(a.notional)/n(a.gross_shares),6)} / $${num(a.limit_price,6)}</b><span>Controvalore</span><b>${money(a.notional,6)}</b><span>Fee addebitata in saldo ${a.cash_fee_pct!==undefined?'('+num(a.cash_fee_pct,3)+'%)':''}</span><b>${money(a.cash_fee,6)}</b><span>Fee trattenuta in quote</span><b>${num(a.withheld_shares,6)} quote (${num(a.withheld_fee_pct??0,3)}%)</b><span>Valore della fee in quote, già incluso nel controvalore</span><b>${money(a.withheld_fee_value,6)}</b><span>Quote nette ricevute</span><b>${num(a.net_shares,6)}</b><span class="total">Importo da destinare a questo lato</span><b class="total">${money(a.total,6)}</b></div><p class="subtle-note">${esc(a.fee_source||'Fonte fee in aggiornamento')}${a.conservative_fee?' · Stima prudenziale':''}${a.estimated_fee?' · Fee stimata':''}</p></article>`).join('');
const vol=(o.liquidity?.volume_by_venue||[]).map(m=>`<span>${esc(venue(m.venue))} · volume 24h</span><b>${money(m.volume24h)}</b>`).join('');const checks=(o.rules?.checks||[]).map(c=>`<div class="check"><span class="check-icon ${c.status}">${c.status==='pass'?'✓':c.status==='conflict'?'×':'?'}</span><div><b>${esc(c.label)}</b><small>${esc(c.detail)}</small></div></div>`).join('');const docs=(o.rules?.documents||[]).map(d=>`<details><summary>${esc(venue(d.venue))} — leggi regole originali</summary><div class="rule-doc">${esc(d.rules||'Testo non disponibile')}</div><p class="subtle-note">Scadenza dichiarata: ${esc(d.end||'Non disponibile')}<br>${link(d.url,'Apri fonte')}</p></details>`).join('');
const scenario=o.convergence_exit;let exitBlock='<p>Scenario di convergenza non disponibile.</p>';if(scenario){exitBlock=`<p>Ipotesi: i bid futuri dei due lati sommano a <b>$1</b> e consentono di vendere tutte le quote nette. La chiusura dello spread agli ask da sola non basta.</p><div class="definition"><span>Ricavo lordo ipotetico</span><b>${money(scenario.gross_proceeds,6)}</b><span>Fee di vendita sui prezzi simulati</span><b>${money(scenario.sell_fees,6)}</b><span>Costi fissi di uscita</span><b>${money(scenario.fixed_exit_cost,6)}</b><span>Ricavo netto ipotetico</span><b>${money(scenario.net_proceeds,6)}</b><span>Guadagno anticipato nello scenario centrale</span><b class="${n(scenario.profit)>0?'value-positive':'value-negative'}">${money(scenario.profit,6)}</b><span>Rendimento netto fee</span><b class="${n(scenario.roi_pct)>0?'value-positive':'value-negative'}">${num(scenario.roi_pct,2)}%</b></div><h4>Prezzi e percorsi di convergenza</h4>${scenario.scenarios.map(x=>`<div class="check"><div><b>${esc(x.label)}</b><small>${o.allocations.map((a,i)=>`${esc(venue(a.venue))} ${esc(a.outcome)}: $${num(x.prices[i],6)}`).join(' · ')}<br>Fee vendita ${money(x.sell_fees,6)} · guadagno netto ${money(x.profit,6)} (${num(x.roi_pct,2)}%)</small></div></div>`).join('')}<h4>Obiettivo più rapido: metà del profitto a risoluzione</h4><div class="definition"><span>Guadagno netto obiettivo (50%)</span><b>${money(scenario.partial_profit_target,6)}</b><span>Rendimento obiettivo sul capitale</span><b>${num(n(o.total_cost)?n(scenario.partial_profit_target)/n(o.total_cost)*100:null,2)}%</b><span>Incasso necessario dopo tutte le fee di vendita</span><b>${money(scenario.partial_target_net_proceeds,6)}</b></div><p class="subtle-note">La soglia del 50% è un obiettivo, non un guadagno già disponibile. Richiede bid con profondità sufficiente su entrambi i lati. Nessuna previsione dei tempi; non viene seguita una posizione acquistata. ${scenario.fee_estimated?'Fee di vendita stimate prudenzialmente.':''}</p>`}if(exit){exitBlock+=`<details><summary>Controllo di realtà: vendere ai bid attuali</summary><div class="definition"><span>Quantità integralmente rivendibili ora</span><b>${exit.fully_sellable?'Sì, nello snapshot':'No, profondità insufficiente'}</b><span>Ricavo netto ${exit.fully_sellable?'':'parziale'}</span><b>${money(exit.net_proceeds,6)}</b><span>Risultato immediato</span><b>${money(exit.profit,6)}</b></div></details>`}

$('#drawer-content').innerHTML=`${stale()?'<div class="notice">Dati non aggiornati: attendi un nuovo ciclo prima di usare questo piano.</div>':''}<div class="drawer-summary"><div class="mini-card"><small>Spesa totale</small><strong>${money(o.total_cost)}</strong></div><div class="mini-card"><small>Netto a risoluzione</small><strong class="value-positive">+${money(o.profit)}</strong></div><div class="mini-card"><small>Quote nette per lato</small><strong>${num(o.payout,4)}</strong></div></div><div class="panel"><h3>01 · Distribuzione degli acquisti</h3>${r==='conflict'?'<div class="notice">Conflitto nelle regole: questo piano è solo un calcolo ipotetico, non una copertura validata.</div>':''}${plan}<div class="definition"><span>Costi fissi e riserva arrotondamenti</span><b>${money(o.fixed_cost,6)}</b><span>Budget non impiegato</span><b>${money(o.unused_budget,6)}</b><span>Capitale conveniente sul book letto</span><b>${money(o.max_profitable_cost)}</b></div><p class="subtle-note">Intero budget coperto dagli ask letti, fee incluse; nessun ordine maker ipotizzato. Le quote nette sono bilanciate. Rivalida precisione, minimi, prezzi e disponibilità prima di inviare i due ordini: non sono atomici.</p></div><button class="button primary copy-plan" id="copy-plan" ${stale()||r==='conflict'?'disabled':''}>Copia il piano d’acquisto</button><div class="panel"><h3>02 · Uscita prima della scadenza</h3>${exitBlock}</div><div class="panel"><h3>03 · Attività e liquidità</h3><div class="definition">${vol}<span>Volume 24h del lato meno attivo</span><b>${money(o.liquidity?.volume24h_min)}</b><span>Copertura delle quote ai bid</span><b>${num(n(o.liquidity?.bid_coverage_ratio)*100,1)}%</b></div><p class="subtle-note">Il volume è storico. La profondità dei bid indica solo quanto è rivendibile nello snapshot corrente. ${o.holding_until?'Scadenza massima dichiarata: '+esc(dt(o.holding_until))+'.':''}</p></div><div class="panel"><h3>04 · Controllo delle regole <span class="badge ${r}">${ruleName(r)}</span></h3>${checks}<p class="subtle-note">${esc(o.rules?.unresolved||'Le regole devono essere verificate. Nessuna garanzia di copertura al 100%.')}</p>${docs}</div><p class="drawer-foot">Snapshot: ${esc(dt(state.timestamp))} · ${esc((o.transports||[]).join(' / '))}<br>Valori in USD nominali, prima di eventuali costi esterni non configurati. ${o.italy_restriction?'Polymarket: trading limitato dall’Italia.':''}</p>`;
}
function openDetail(key){const o=state.opportunities.find(o=>id(o)===key);if(!o)return;selectedId=key;lastFocus=document.activeElement;drawer(o);$('#drawer').hidden=false;$('#drawer-backdrop').hidden=false;document.body.style.overflow='hidden';$('#close-drawer').focus()}
function closeDetail(){selectedId=null;$('#drawer').hidden=true;$('#drawer-backdrop').hidden=true;document.body.style.overflow='';lastFocus?.focus()}
function toast(text){$('#toast').textContent=text;$('#toast').hidden=false;setTimeout(()=>$('#toast').hidden=true,3500)}
async function refresh(){try{const response=await fetch('/api/state',{cache:'no-store'});if(!response.ok)throw Error('HTTP '+response.status);const next=await response.json();next.opportunities??=[];const first=!state;state=next;renderSummary();if(first){$('#budget').value=state.ui_settings?.budget||25;$('#min-volume').value=state.ui_settings?.min_volume24h||0;$('#sort-policy').value=state.ui_settings?.sort_policy||'liquidity'}const fingerprint=JSON.stringify([state.ui_settings?.budget,state.opportunities.map(o=>[id(o),o.profit,o.payout,o.allocations,o.rules,o.liquidity,o.early_exit,o.convergence_exit])]);if(fingerprint!==lastFingerprint){lastFingerprint=fingerprint;renderRows();if(selectedId){const o=state.opportunities.find(o=>id(o)===selectedId);if(o){const opened=[...$('#drawer-content').querySelectorAll('details')].map(d=>d.open);drawer(o);$('#drawer-content').querySelectorAll('details').forEach((d,i)=>d.open=opened[i]||false)}else{$('#drawer-content').innerHTML='<div class="notice">Questa opportunità non è più positiva o verificabile nel ciclo corrente. Il piano precedente è scaduto.</div>'}}}}catch{$('#live-status').classList.add('offline');$('#live-status').innerHTML='<i></i> Connessione interrotta';$('#stale-banner').hidden=false;$('#stale-banner').textContent='Impossibile aggiornare i dati locali. Non usare i prezzi visualizzati finché la connessione non torna disponibile.';const copy=$('#copy-plan');if(copy)copy.disabled=true}}
$('#opportunity-rows').addEventListener('click',e=>{const button=e.target.closest('[data-detail]');if(button)openDetail(button.dataset.detail)});$('#close-drawer').addEventListener('click',closeDetail);$('#drawer-backdrop').addEventListener('click',closeDetail);document.addEventListener('keydown',e=>{if(e.key==='Escape'&&selectedId)closeDetail();if(e.key==='Tab'&&!$('#drawer').hidden){const items=[...$('#drawer').querySelectorAll('button:not(:disabled),a[href],summary')];if(e.shiftKey&&document.activeElement===items[0]){e.preventDefault();items.at(-1)?.focus()}else if(!e.shiftKey&&document.activeElement===items.at(-1)){e.preventDefault();items[0]?.focus()}}});$('#drawer-content').addEventListener('click',async e=>{if(e.target.id==='copy-plan'){const o=state.opportunities.find(o=>id(o)===selectedId);if(!o||stale()||ruleStatus(o)==='conflict')return toast('Piano non utilizzabile: aggiorna i dati e controlla le regole.');try{await navigator.clipboard.writeText(planText(o));toast('Piano copiato con quantità, fee e link.')}catch{toast('Il browser non consente la copia negli appunti.')}}});$('.tabs').addEventListener('click',e=>{const b=e.target.closest('[data-tab]');if(!b)return;tab=b.dataset.tab;$('.tabs').querySelectorAll('button').forEach(x=>x.classList.toggle('selected',x===b));renderRows()});$('#search').addEventListener('input',renderRows);$('#view-sort').addEventListener('change',renderRows);$('#refresh').addEventListener('click',refresh);$('#nav-opps').addEventListener('click',()=>$('.opportunities').scrollIntoView({behavior:'smooth'}));for(const selector of ['#learn-more','#nav-method'])$(selector).addEventListener('click',()=>$('#method-dialog').showModal());$('#close-method').addEventListener('click',()=>$('#method-dialog').close());$('#settings-form').addEventListener('submit',async e=>{e.preventDefault();if(!state)return;const button=$('#apply');button.disabled=true;try{const enabled=[...$('#settings-form').querySelectorAll('.venue-toggles input:checked')].map(x=>x.value);if(!enabled.length)throw Error('Seleziona almeno una piattaforma');const response=await fetch('/api/settings',{method:'POST',headers:{'Content-Type':'application/json','X-Arbitraggio-Token':state.csrf_token},body:JSON.stringify({budget:$('#budget').value,sort_policy:$('#sort-policy').value,min_volume24h:$('#min-volume').value,enabled_venues:enabled,tracking_min_yield_pct:$('#tracking-min-yield').value,tracking_target_yield_pct:$('#tracking-target-yield').value,tax_rate_pct:$('#tax-rate').value})});const result=await response.json();if(!response.ok)throw Error(result.error||'Impostazioni non valide');pendingSettings=result.settings;$('#settings-status').textContent='Richiesta salvata. Ricalcolo al prossimo ciclo dello scanner…';$('#view-sort').value=result.settings.sort_policy==='profit'?'profit':'liquidity';await refresh()}catch(err){$('#settings-status').textContent=err.message}finally{button.disabled=false}});
let venueInitDone=false;
const venueInitTimer=setInterval(()=>{if(!state)return;const enabled=state.ui_settings?.enabled_venues||['polymarket','predict','limitless','opinion'];for(const v of ['polymarket','predict','limitless','opinion']){const box=$('#venue-'+v);if(box)box.checked=enabled.includes(v)}const fields={'#tracking-min-yield':state.ui_settings?.tracking_min_yield_pct??'1.5','#tracking-target-yield':state.ui_settings?.tracking_target_yield_pct??'1.5','#tax-rate':state.ui_settings?.tax_rate_pct??'0','#budgets':(state.ui_settings?.budgets||['50','100']).join(',')};for(const [selector,value] of Object.entries(fields)){const input=$(selector);if(input)input.value=value}venueInitDone=true;clearInterval(venueInitTimer)},300);
refresh();setInterval(refresh,2500);
if(staticMode){
 const downloads={'/download/latest.csv':'data/opportunities-latest.csv',
  '/download/legs.csv':'data/opportunities-legs.csv',
  '/download/sizes.csv':'data/opportunities-sizes.csv',
  '/download/catalog.csv':'data/catalog.csv',
  '/download/matches.csv':'data/matches.csv',
  '/download/tracking.csv':'data/tracking.csv',
  '/download/tracking-history.csv':'data/tracking-history.csv',
  '/download/opportunity-tracking.csv':'data/opportunity-tracking.csv'};
 document.querySelectorAll('a[href^="/download/"]').forEach(anchor=>{
  const target=downloads[anchor.getAttribute('href')];
  if(target)anchor.setAttribute('href',target);
 });
 for(const selector of ['#settings-form','#wallet-form']){
  document.querySelector(selector)?.addEventListener('submit',event=>{
   event.preventDefault();event.stopImmediatePropagation();
   toast('Dashboard remota in sola lettura.');
  },true);
 }
 for(const selector of ['#apply','#wallet-add','#opinion-connect']){
  const button=document.querySelector(selector);if(button)button.disabled=true;
 }
 const status=document.querySelector('#settings-status');
 if(status)status.textContent='Snapshot GitHub Actions · sola lettura';
}
setInterval(()=>{if(!staticMode||!state)return;const banner=$('#stale-banner');if(state.recent_fallback){banner.hidden=false;banner.textContent=`Ultimo snapshot con opportunità: ${dt(state.timestamp)}. Il ciclo pubblico più recente non ne aveva.`}},1000);
let tracking=null,historyEntries=[],currentView='opportunities';
function shortAddress(value){const text=String(value||'');return text.length>12?text.slice(0,6)+'…'+text.slice(-4):text}
function showView(name){
 currentView=name;
 $('#opportunities-main').hidden=name!=='opportunities';
 $('#portfolio-main').hidden=name!=='portfolio';
 $('#tracking-main').hidden=name!=='tracking';
 $('#nav-opps').classList.toggle('active',name==='opportunities');
 $('#nav-portfolio').classList.toggle('active',name==='portfolio');
 $('#nav-tracking').classList.toggle('active',name==='tracking');
 $('#breadcrumb-current').textContent=name==='portfolio'?'Portafoglio':name==='tracking'?'Seguite':'Opportunità';
 const hash=name==='portfolio'?'#portafoglio':name==='tracking'?'#seguite':'#opportunita';
 if(location.hash!==hash)history.replaceState(null,'',hash);
 if(name==='portfolio'){refreshTracking();refreshHistory()}
 if(name==='tracking')refreshLedger();
}
function pnlClass(value){return n(value)>0?'value-positive':n(value)<0?'value-negative':'value-neutral'}
function renderWalletList(){const wallets=tracking?.wallet_settings||[];$('#wallet-list').innerHTML=wallets.length?wallets.map(w=>`<span class="wallet-chip"><b>${esc(venue(w.venue))}</b> ${esc(w.label||'Wallet')} <small>${esc(shortAddress(w.address))}</small><button type="button" data-wallet-remove data-venue="${esc(w.venue)}" data-address="${esc(w.address)}" aria-label="Rimuovi wallet">Rimuovi</button></span>`).join(''):'<span class="wallet-chip">Nessun wallet collegato</span>'}
function renderTracking(){
 if(!tracking)return;
 const totals=tracking.totals||{};
 $('#track-net').textContent=money(totals.net_now);
 $('#track-pnl').textContent=(n(totals.pnl_now)>0?'+':'')+money(totals.pnl_now);
 $('#track-pnl').className=pnlClass(totals.pnl_now);
 $('#track-cost').textContent=money(totals.cost);
 $('#track-realized').textContent=money(totals.realized);
 const alive=tracking.tracker_alive;
 const age=tracking.timestamp?Math.max(0,Date.now()/1000-Number(tracking.timestamp)):null;
 $('#track-status').textContent=alive?`Tracker live · ${num(age,0)}s fa`:'Tracker da avviare';
 const banner=$('#tracking-banner');banner.hidden=Boolean(alive);
 if(!alive)banner.textContent='Tracker non attivo: avvia il processo di tracking per aggiornare prezzi e storico.';
 renderWalletList();
 const all=tracking.positions||[];
 const filter=$('#position-filter').value||'open';
 const rows=all.filter(p=>filter==='all'||(filter==='redeem'?Boolean(p.sell_now?.resolved&&n(p.sell_now?.net_proceeds)>0):!p.sell_now?.resolved));
 $('#tracking-count').textContent=rows.length;
 const empty=$('#positions-empty');empty.hidden=rows.length>0;
 if(!rows.length){empty.innerHTML=(tracking.wallet_settings||[]).length
  ?`<div>◫</div><h3>Nessuna posizione in questo filtro</h3><p>Cambia filtro per vedere le posizioni risolte o riscattabili.</p>`
  :'<div>◫</div><h3>Nessun wallet collegato</h3><p>Aggiungi un indirizzo pubblico per iniziare il tracking in sola lettura.</p>'}
 const errors=(tracking.errors||[]).map(e=>`<div class="notice">${esc(e)}</div>`).join('');
 const status=sale=>sale?.resolved?(n(sale.net_proceeds)>0?'riscattabile':'risolta'):sale?.fully_sellable?'profondità sufficiente':'solo parziale';
 $('#position-rows').innerHTML=errors+rows.map(p=>{
  const sale=p.sell_now||{};const outcome=String(p.outcome||'').toLowerCase();
  return `<tr><td><span class="position-title">${esc(p.title)}</span><span class="position-meta">${esc(venue(p.venue))} · ${esc(p.wallet_label||shortAddress(p.wallet))}</span></td><td><span class="outcome-pill ${outcome==='yes'?'yes':'no'}">${esc(p.outcome)}</span></td><td>${num(p.shares,4)}<small class="cell-sub">quote</small></td><td>${money(p.cost)}<small class="cell-sub">media $${num(p.avg_price,4)}</small></td><td>${sale.best_bid===null||sale.best_bid===undefined?'—':'$'+num(sale.best_bid,4)}<small class="cell-sub">${status(sale)}</small></td><td>${money(sale.net_proceeds)}<small class="cell-sub">fee ${money(sale.sell_fees)}</small></td><td class="${pnlClass(sale.pnl_now)}">${n(sale.pnl_now)>0?'+':''}${money(sale.pnl_now)}<small class="cell-sub">${sale.roi_pct===null?'—':num(sale.roi_pct,2)+'%'}</small></td><td>${p.expiry_at?esc(day(p.expiry_at)):'—'}</td><td>${link(p.url,'Apri')}</td></tr>`
 }).join('')
}
async function refreshTracking(){try{const response=await fetch('/api/tracking',{cache:'no-store'});if(!response.ok)throw Error('HTTP '+response.status);tracking=await response.json();renderTracking()}catch{$('#tracking-banner').hidden=false;$('#tracking-banner').textContent='Impossibile leggere lo stato del tracker locale.'}}
async function refreshHistory(){try{const response=await fetch('/api/tracking/history',{cache:'no-store'});if(!response.ok)throw Error('HTTP '+response.status);const data=await response.json();historyEntries=data.entries||[];renderHistory()}catch{historyEntries=[];renderHistory()}}
function renderHistory(){const entries=historyEntries.slice(-120);const values=entries.map(e=>n(e.totals?.net_now)).filter(v=>v!==null&&Number.isFinite(v));const chart=$('#history-chart');if(values.length<2){chart.innerHTML='<text x="360" y="95" text-anchor="middle" fill="#9aa5aa" font-size="11">Servono almeno due snapshot per disegnare lo storico</text>';$('#history-range').textContent='—'}else{const min=Math.min(...values),max=Math.max(...values),span=max-min||1,width=700,height=140,pad=20;const points=values.map((v,i)=>`${pad+i*(width/(values.length-1))},${pad+(max-v)/span*height}`).join(' ');chart.innerHTML=`<line x1="${pad}" y1="${pad+height}" x2="${pad+width}" y2="${pad+height}" stroke="#e7ecf0" stroke-width="1"/><line x1="${pad}" y1="${pad}" x2="${pad+width}" y2="${pad}" stroke="#f1f4f6" stroke-width="1"/><polyline points="${points}" fill="none" stroke="#167b61" stroke-width="2.2" stroke-linejoin="round" stroke-linecap="round"/><text x="${pad}" y="14" fill="#8d9a99" font-size="10">${esc(money(max))}</text><text x="${pad}" y="${pad+height+18}" fill="#8d9a99" font-size="10">${esc(money(min))}</text>`;$('#history-range').textContent=`${entries.length} snapshot`}$('#history-rows').innerHTML=entries.slice(-10).reverse().map(e=>`<tr><td>${esc(dt(e.timestamp))}</td><td>${money(e.totals?.net_now)}</td><td class="${pnlClass(e.totals?.pnl_now)}">${money(e.totals?.pnl_now)}</td><td>${num(e.totals?.positions,0)}</td></tr>`).join('')||'<tr><td colspan="4">Nessuno snapshot salvato.</td></tr>'}
$('#nav-portfolio').addEventListener('click',()=>showView('portfolio'));
$('#nav-opps').addEventListener('click',()=>showView('opportunities'));
$('#tracking-refresh').addEventListener('click',()=>{refreshTracking();refreshHistory()});
$('#position-filter').addEventListener('change',renderTracking);
$('#wallet-venue').addEventListener('change',()=>{const hints={polymarket:'Polymarket: usa il proxy wallet del profilo.',predict:'Predict.fun: usa l’indirizzo del conto.',limitless:'Limitless: lettura pubblica, nessuna chiave necessaria.',opinion:'Opinion: richiede OPINION_API_KEY nel file .env.'};$('#wallet-status').textContent=hints[$('#wallet-venue').value]||''});
$('#wallet-form').addEventListener('submit',async event=>{event.preventDefault();const button=$('#wallet-add');button.disabled=true;try{const response=await fetch('/api/wallets',{method:'POST',headers:{'Content-Type':'application/json','X-Arbitraggio-Token':tracking?.csrf_token||state?.csrf_token},body:JSON.stringify({action:'add',venue:$('#wallet-venue').value,address:$('#wallet-address').value.trim(),label:$('#wallet-label').value.trim()})});const result=await response.json();if(!response.ok)throw Error(result.error||'Wallet non valido');$('#wallet-address').value='';$('#wallet-label').value='';$('#wallet-status').textContent='Wallet salvato. Il tracker lo leggerà al prossimo ciclo.';toast('Wallet collegato in sola lettura.');await refreshTracking()}catch(error){$('#wallet-status').textContent=error.message}finally{button.disabled=false}});
$('#wallet-list').addEventListener('click',async event=>{const button=event.target.closest('[data-wallet-remove]');if(!button)return;button.disabled=true;try{const response=await fetch('/api/wallets',{method:'POST',headers:{'Content-Type':'application/json','X-Arbitraggio-Token':tracking?.csrf_token||state?.csrf_token},body:JSON.stringify({action:'remove',venue:button.dataset.venue,address:button.dataset.address})});const result=await response.json();if(!response.ok)throw Error(result.error||'Rimozione non riuscita');$('#wallet-status').textContent='Wallet rimosso.';await refreshTracking()}catch(error){$('#wallet-status').textContent=error.message;button.disabled=false}});
async function opinionRequest(address,action){
 const timestamp=Math.floor(Date.now()/1000).toString();
 const data={types:{EIP712Domain:[{name:'name',type:'string'},{name:'version',type:'string'},{name:'chainId',type:'uint256'}],OpinionApiKeyAuth:[{name:'walletAddress',type:'address'},{name:'action',type:'string'},{name:'timestamp',type:'string'}]},primaryType:'OpinionApiKeyAuth',domain:{name:'Opinion OpenAPI',version:'1',chainId:56},message:{walletAddress:address,action,timestamp}};
 const signature=await window.ethereum.request({method:'eth_signTypedData_v4',params:[address,JSON.stringify(data)]});
 const response=await fetch('/api/opinion-key',{method:'POST',headers:{'Content-Type':'application/json','X-Arbitraggio-Token':tracking?.csrf_token||state?.csrf_token},body:JSON.stringify({action,address,signature,timestamp})});
 const result=await response.json();if(!response.ok)throw Error(result.error||'Opinion non ha accettato la firma');return result
}
$('#opinion-connect').addEventListener('click',async()=>{
 const button=$('#opinion-connect');const status=$('#opinion-status');button.disabled=true;
 try{
  if(!window.ethereum)throw Error('Nessun wallet nel browser: apri la dashboard in un browser con MetaMask o un wallet compatibile.');
  const accounts=await window.ethereum.request({method:'eth_requestAccounts'});const address=accounts[0];
  status.textContent='Wallet: '+shortAddress(address)+' · richiesta firma…';
  try{await window.ethereum.request({method:'wallet_switchEthereumChain',params:[{chainId:'0x38'}]})}catch{}
  let result=await opinionRequest(address,'create');
  if(result.errno===11009)result=await opinionRequest(address,'get');
  if(result.errno!==0)throw Error(result.errmsg||('Opinion errno '+result.errno));
  status.textContent='Chiave Opinion salvata in .env. Scanner e tracker la useranno al prossimo ciclo.';
  toast('Opinion collegato in sola lettura.');
  await refreshTracking()
 }catch(error){status.textContent=error.message}finally{button.disabled=false}
});
setInterval(()=>{if(currentView==='portfolio')refreshTracking()},5000);
setInterval(()=>{if(currentView==='portfolio')refreshHistory()},30000);
let ledger=null;
function durationText(seconds){const value=Math.max(0,Number(seconds)||0);if(value<60)return Math.round(value)+'s';if(value<3600)return Math.floor(value/60)+'m';if(value<86400)return Math.floor(value/3600)+'h '+Math.floor(value%3600/60)+'m';return Math.floor(value/86400)+'g '+Math.floor(value%86400/3600)+'h'}
function renderLedger(){
 if(!ledger)return;
 const stats=ledger.stats||{},episodes=ledger.episodes||[],now=Date.now()/1000;
 $('#ledger-open').textContent=num(stats.open,0);
 $('#ledger-target').textContent=num(stats.target,0);
 $('#ledger-median').textContent=stats.median_target_seconds===null||stats.median_target_seconds===undefined?'—':durationText(stats.median_target_seconds);
 $('#ledger-roi').textContent=stats.mean_exit_net_roi_pct===null||stats.mean_exit_net_roi_pct===undefined?'—':num(stats.mean_exit_net_roi_pct,2)+'%';
 $('#ledger-threshold').textContent=`Ingresso ≥ ${num(ledger.min_yield_pct,2)}% netto · aggiornato ${dt(ledger.updated_at)}`;
 $('#ledger-target-rate').textContent=`Target ${num(ledger.target_yield_pct,2)}% · ${stats.target_rate===null||stats.target_rate===undefined?'—':num(stats.target_rate*100,1)+'% raggiunto'}`;
 $('#ledger-profit').textContent=`Ingresso ${stats.mean_entry_net_roi_pct===null||stats.mean_entry_net_roi_pct===undefined?'—':num(stats.mean_entry_net_roi_pct,2)+'%'} · profitto target ${money(stats.total_net_profit_target)}`;
 $('#ledger-count').textContent=episodes.length;
 const alive=ledger.scanner_alive;$('#ledger-banner').hidden=Boolean(alive);
 if(!alive)$('#ledger-banner').textContent='Scanner non attivo: il registro non riceve nuovi aggiornamenti.';
 const status=$('#ledger-status-filter').value,venueFilter=$('#ledger-venue-filter').value,duration=$('#ledger-duration-filter').value;
 const minEntry=n($('#ledger-min-entry').value),minExit=n($('#ledger-min-exit').value),query=$('#ledger-search').value.toLowerCase().trim();
 const exitValue=e=>n(e.final_exit_net_roi_pct)??n(e.current_exit_net_roi_pct);
 const rows=episodes.filter(e=>{
  if(status!=='all'&&e.status!==status)return false;
  if(venueFilter!=='all'&&!(e.legs||[]).some(l=>l.venue===venueFilter))return false;
  if(minEntry!==null&&n(e.entry_net_roi_pct)<minEntry)return false;
  if(minExit!==null&&(exitValue(e)===null||exitValue(e)<minExit))return false;
  const age=e.status==='open'?Math.max(0,now-Number(e.first_seen)):Number(e.duration_seconds||0);
  if(duration==='short'&&age>=3600)return false;
  if(duration==='medium'&&(age<3600||age>=21600))return false;
  if(duration==='long'&&(age<21600||age>=86400))return false;
  if(duration==='verylong'&&age<86400)return false;
  if(query&&!(e.name||'').toLowerCase().includes(query))return false;
  return true
 });
 const sort=$('#ledger-sort').value;
 rows.sort((a,b)=>sort==='entry'?n(b.entry_net_roi_pct)-n(a.entry_net_roi_pct):sort==='exit'?(exitValue(b)??-1e9)-(exitValue(a)??-1e9):sort==='duration'?Number(b.duration_seconds||0)-Number(a.duration_seconds||0):Number(b.first_seen)-Number(a.first_seen));
 $('#ledger-showing').textContent=`${rows.length} di ${episodes.length} episodi`;
 $('#ledger-empty').hidden=rows.length>0;
 $('#ledger-rows').innerHTML=rows.map(e=>{
  const venues=[...new Set((e.legs||[]).map(l=>venue(l.venue)))].join(' · ');
  const open=e.status==='open',age=open?Math.max(0,now-Number(e.first_seen)):Number(e.duration_seconds||0);
  const roi=exitValue(e),linkUrl=(e.urls||[])[0];
  return `<tr><td><span class="position-title">${esc(e.name)}</span><span class="position-meta">${esc(venues)}</span></td><td>${esc(venues)}</td><td><span class="badge ${open?'review':'consistent'}">${open?'Aperta':'Target'}</span></td><td>${esc(dt(e.first_seen))}</td><td>${durationText(age)}<small class="cell-sub">${open?'finora':'al target'}</small></td><td>${num(e.entry_net_roi_pct,2)}%</td><td>${num(e.max_exit_net_roi_pct,2)}%</td><td class="${pnlClass(roi)}">${num(roi,2)}%</td><td>${linkUrl?link(linkUrl,'Apri'):'—'}</td></tr>`
 }).join('');
 const targets=episodes.filter(e=>e.status==='target'),chart=$('#ledger-chart');
 if(!targets.length){
  chart.innerHTML='<text x="360" y="95" text-anchor="middle" fill="#9aa5aa" font-size="11">Nessun target raggiunto: attendi che il rendimento netto arrivi al 1,5%</text>';
  $('#ledger-range').textContent='—'
 }else{
  const xs=targets.map(e=>Math.max(.01,Number(e.duration_seconds)/3600));
  const ys=targets.map(e=>Math.max(.01,Number(e.final_exit_net_roi_pct)));
  const maxX=Math.max(...xs),maxY=Math.max(...ys,Number(ledger.target_yield_pct||1.5)),width=660,height=120,pad=30,targetLine=pad+height-Number(ledger.target_yield_pct||1.5)/maxY*height;
  chart.innerHTML=`<line x1="${pad}" y1="${pad+height}" x2="${pad+width}" y2="${pad+height}" stroke="#e7ecf0"/><line x1="${pad}" y1="${targetLine}" x2="${pad+width}" y2="${targetLine}" stroke="#d3ad55" stroke-dasharray="4 4"/><text x="${pad+width-4}" y="${targetLine-5}" text-anchor="end" fill="#987423" font-size="9">target ${num(ledger.target_yield_pct,2)}%</text>${targets.map((e,i)=>`<circle cx="${pad+xs[i]/maxX*width}" cy="${pad+height-ys[i]/maxY*height}" r="4" fill="#167b61"><title>${esc(e.name)} · ${num(e.final_exit_net_roi_pct,2)}% netto · ${esc(durationText(e.duration_seconds))}</title></circle>`).join('')}<text x="${pad}" y="${pad+height+18}" fill="#8d9a99" font-size="10">tempo → ${esc(num(maxX,1))}h</text><text x="${pad}" y="${pad-8}" fill="#8d9a99" font-size="10">ROI netto max ${esc(num(maxY,2))}%</text>`;
  $('#ledger-range').textContent=`${targets.length} target · mediana ${durationText(ledger.stats?.median_target_seconds||0)}`
 }
}
async function refreshLedger(){try{const response=await fetch('/api/opportunity-tracking',{cache:'no-store'});if(!response.ok)throw Error('HTTP '+response.status);ledger=await response.json();renderLedger()}catch{$('#ledger-banner').hidden=false;$('#ledger-banner').textContent='Impossibile leggere il registro delle opportunità.'}}
$('#nav-tracking').addEventListener('click',()=>showView('tracking'));
$('#ledger-refresh').addEventListener('click',refreshLedger);
for(const selector of ['#ledger-status-filter','#ledger-venue-filter','#ledger-duration-filter','#ledger-sort'])$(selector).addEventListener('change',renderLedger);
$('#ledger-search').addEventListener('input',renderLedger);
$('#ledger-min-entry').addEventListener('input',renderLedger);
$('#ledger-min-exit').addEventListener('input',renderLedger);
setInterval(()=>{if(currentView==='tracking')refreshLedger()},10000);
const baseRenderSummary=renderSummary;
renderSummary=function(){baseRenderSummary();if(!state)return;const opportunities=uniqueByMarket((state.opportunities||[]).filter(o=>o.full_budget_executable));$('#metric-opps').textContent=opportunities.length;$('#nav-count').textContent=opportunities.length;$('#metric-rules').textContent=opportunities.filter(o=>ruleStatus(o)==='consistent').length};
function syncBudgetFilter(){if(!state)return;const select=$('#budget-filter');const current=select.value;
 const budgets=[...new Set((state.opportunities||[]).map(o=>String(o.budget)).filter(Boolean))].sort((a,b)=>n(a)-n(b));
 const signature=budgets.join(',');
 if(select.dataset.signature!==signature){select.dataset.signature=signature;select.innerHTML='<option value="all">Tutti i budget</option>'+budgets.map(b=>`<option value="${esc(b)}">$${esc(b)}</option>`).join('');select.value=budgets.includes(current)?current:'all'}
}
setInterval(syncBudgetFilter,1000);
$('#budget-filter').addEventListener('change',renderRows);
$('#budgets').addEventListener('change',async()=>{
 if(!state||staticMode)return;
 try{
  const response=await fetch('/api/settings',{method:'POST',headers:{'Content-Type':'application/json','X-Arbitraggio-Token':state.csrf_token},body:JSON.stringify({budgets:$('#budgets').value})});
  const result=await response.json();if(!response.ok)throw Error(result.error||'Budget non valido');
  $('#settings-status').textContent='Budget multipli salvati: '+result.settings.budgets.join(', ');await refresh();
 }catch(error){$('#settings-status').textContent=error.message}
});
if(location.hash==='#portafoglio')showView('portfolio');
else if(location.hash==='#seguite')showView('tracking');
