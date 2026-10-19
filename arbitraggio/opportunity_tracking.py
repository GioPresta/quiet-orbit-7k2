"""Persistent lifecycle and target-exit analysis for arbitrage opportunities."""
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
import statistics
import time
from concurrent.futures import ThreadPoolExecutor

from .api import ApiError, Client
from .engine import D, number
from .exit import liquidate
from .scanner import fee_for


def _episode_key(opportunity):
    legs=sorted((a['venue'],str(a['market']),a['outcome']) for a in opportunity['allocations'])
    return '|'.join(':'.join(leg) for leg in legs)


def _new_id(key,now):
    return hashlib.sha256(f'{key}|{now}'.encode()).hexdigest()[:16]


def _market_for(leg):
    market=dict(leg.get('market_data') or {})
    market.setdefault('venue',leg['venue']);market.setdefault('id',leg['market'])
    if leg['venue'] in ('polymarket','opinion'):
        market.setdefault('yes_token',leg.get('yes_token') or '0')
        market.setdefault('no_token',leg.get('no_token') or '0')
    return market


def _episode_legs(opportunity,catalog):
    legs=[]
    for allocation in opportunity.get('allocations') or []:
        ref=f"{allocation['venue']}:{allocation['market']}"
        market=catalog.get(ref) or {}
        legs.append(dict(
            venue=allocation['venue'],market=str(allocation['market']),outcome=allocation['outcome'],
            net_shares=str(allocation['net_shares']),url=allocation.get('url') or market.get('url'),
            market_data=market,yes_token=market.get('yes_token'),no_token=market.get('no_token')))
    return legs


def _entry(opportunity,tax_rate):
    gross=number(opportunity.get('roi_pct',0))
    net=gross*(1-tax_rate/100) if gross>0 else gross
    cost=sum((number(a['total']) for a in opportunity.get('allocations',[])),D(0))
    return cost,gross,net


def _exit_quote(episode,config,book_provider=None):
    entry_cost=number(episode['entry_cost'])
    total=D(0);fees=D(0);fully=True;errors=[];legs=[]
    for leg in episode['legs']:
        market=_market_for(leg)
        try:
            book=(book_provider or (lambda m,o: Client(m['venue']).book(m,o)))(market,leg['outcome'])
            fee=fee_for(market,config.get('fees',{}))
            allocation=dict(venue=leg['venue'],market=leg['market'],outcome=leg['outcome'],
                            net_shares=leg['net_shares'])
            result=liquidate(allocation,book,fee)
            total+=result['net_proceeds'];fees+=result['sell_fees'];fully=fully and result['fully_sellable']
            legs.append(dict(venue=leg['venue'],outcome=leg['outcome'],net_proceeds=str(result['net_proceeds']),
                             fully_sellable=result['fully_sellable'],best_bid=str(result['best_bid'])))
        except (ApiError,ValueError,KeyError,TypeError,OSError) as error:
            errors.append(f"{leg['venue']}:{leg['market']} {error}")
    if errors:
        return dict(error='; '.join(errors))
    profit=total-entry_cost
    tax=number(episode.get('tax_rate_pct','26'))/100
    net_profit=profit*(1-tax) if profit>0 else profit
    return dict(exit_value=total,sell_fees=fees,fully_sellable=fully,
                gross_roi=(profit/entry_cost*100 if entry_cost else D(0)),
                net_profit=net_profit,net_roi=(net_profit/entry_cost*100 if entry_cost else D(0)),
                legs=legs)


def _apply_exit(episode,quote,now):
    episode['last_checked']=now
    episode['current_exit_value']=str(quote['exit_value'])
    episode['current_exit_net_profit']=str(quote['net_profit'])
    episode['current_exit_gross_roi_pct']=str(quote['gross_roi'])
    episode['current_exit_net_roi_pct']=str(quote['net_roi'])
    episode['current_exit_fully_sellable']=quote['fully_sellable']
    episode['current_exit_fees']=str(quote['sell_fees'])
    episode['current_legs']=quote['legs']
    previous_max=episode.get('max_exit_net_roi_pct');previous_min=episode.get('min_exit_net_roi_pct')
    episode['max_exit_net_roi_pct']=str(number(quote['net_roi']) if previous_max is None
                                        else max(number(previous_max),number(quote['net_roi'])))
    episode['min_exit_net_roi_pct']=str(number(quote['net_roi']) if previous_min is None
                                        else min(number(previous_min),number(quote['net_roi'])))
    if episode['status']=='open' and quote['fully_sellable'] and number(quote['net_roi'])>=number(episode['target_yield_pct']):
        episode['status']='target'
        episode['closed_at']=now
        episode['duration_seconds']=max(0,now-episode['first_seen'])
        episode['final_exit_value']=str(quote['exit_value'])
        episode['final_exit_net_profit']=str(quote['net_profit'])
        episode['final_exit_net_roi_pct']=str(quote['net_roi'])
        episode['close_reason']='target'
    return episode


def _snapshot(episode,now):
    return dict(timestamp=now,episode_id=episode['id'],name=episode['name'],status=episode['status'],
                entry_net_roi_pct=episode['entry_net_roi_pct'],exit_net_roi_pct=episode.get('current_exit_net_roi_pct'),
                exit_value=episode.get('current_exit_value'),exit_net_profit=episode.get('current_exit_net_profit'),
                fully_sellable=episode.get('current_exit_fully_sellable'),closed_at=episode.get('closed_at'),
                duration_seconds=episode.get('duration_seconds'),target_yield_pct=episode.get('target_yield_pct'),
                tax_rate_pct=episode.get('tax_rate_pct'))


def _stats(episodes,now):
    open_rows=[e for e in episodes if e.get('status')=='open']
    target=[e for e in episodes if e.get('status')=='target']
    durations=[float(e.get('duration_seconds') or 0) for e in target]
    entry=[float(e.get('entry_net_roi_pct') or 0) for e in episodes]
    exit_roi=[float(e.get('final_exit_net_roi_pct') or 0) for e in target]
    profits=[float(e.get('final_exit_net_profit') or 0) for e in target]
    return dict(
        tracked=len(episodes),open=len(open_rows),target=len(target),
        median_target_seconds=statistics.median(durations) if durations else None,
        mean_target_seconds=statistics.mean(durations) if durations else None,
        mean_entry_net_roi_pct=statistics.mean(entry) if entry else None,
        mean_exit_net_roi_pct=statistics.mean(exit_roi) if exit_roi else None,
        total_net_profit_target=sum(profits),
        target_rate=(len(target)/len(episodes) if episodes else None),
        open_age_seconds=[max(0,now-e['first_seen']) for e in open_rows],
        updated_at=now)


def update_ledger(report,config,output_dir=Path('output'),catalog=None,book_provider=None,now=None):
    now=float(now or time.time())
    output_dir=Path(output_dir);output_dir.mkdir(parents=True,exist_ok=True)
    ledger_path=output_dir/'opportunity-ledger.json'
    history_path=output_dir/'opportunity-tracking.jsonl'
    try:
        ledger=json.loads(ledger_path.read_text())
        episodes=ledger.get('episodes',[])
    except (OSError,ValueError):
        episodes=[]
    episodes=[e for e in episodes if isinstance(e,dict)]
    normalized={}
    for episode in episodes:
        key=episode.get('key','')
        parts=key.split('|',1)
        if len(parts)==2 and parts[0].replace('.','',1).isdigit():
            key=parts[1];episode['key']=key
        rank=(episode.get('status')=='open',number(episode.get('budget',0)),episode.get('last_seen',0))
        previous=normalized.get(key)
        if previous is None or rank > (previous[0],number(previous[1].get('budget',0)),previous[1].get('last_seen',0)):
            normalized[key]=(episode.get('status')=='open',episode)
    episodes=[value[1] for value in normalized.values()]
    min_yield=number(config.get('tracking_min_yield_pct','1.5'))
    target_yield=number(config.get('tracking_target_yield_pct','1.5'))
    tax_rate=number(config.get('tax_rate_pct','0'))
    snapshot_seconds=number(config.get('tracking_snapshot_seconds','30'))
    reopen_cooldown=float(config.get('tracking_reopen_cooldown_seconds','300'))
    exclude_names=[str(x).lower() for x in config.get('tracking_exclude_names',[]) if x]
    catalog={f"{m['venue']}:{m['id']}":m for m in (catalog or [])}
    current={}
    for opportunity in report.get('opportunities',[]):
        if any(name in str(opportunity.get('name','')).lower() for name in exclude_names):
            continue
        cost,gross,net=_entry(opportunity,tax_rate)
        if net>=min_yield:
            key=_episode_key(opportunity)
            previous=current.get(key)
            if previous is None or number(opportunity.get('budget',0))>number(previous.get('budget',0)):
                current[key]=opportunity
    seen=set();snapshots=[]
    for key,opportunity in current.items():
        cost,gross,net=_entry(opportunity,tax_rate)
        episode=next((e for e in episodes if e.get('status')=='open' and e.get('key')==key),None)
        if episode is None:
            if any(e.get('key')==key and e.get('status')!='open'
                   and now-float(e.get('closed_at') or e.get('first_seen') or 0)<reopen_cooldown
                   for e in episodes):
                continue
            episode=dict(
                id=_new_id(key,now),key=key,name=opportunity.get('name') or 'Opportunità',
                first_seen=now,last_seen=now,last_checked=now,status='open',
                entry_cost=str(cost),entry_gross_roi_pct=str(gross),entry_net_roi_pct=str(net),
                target_yield_pct=str(target_yield),tax_rate_pct=str(tax_rate),
                max_exit_net_roi_pct=None,min_exit_net_roi_pct=None,
                budget=str(opportunity.get('budget')),legs=_episode_legs(opportunity,catalog),
                last_snapshot_at=0,urls=[a.get('url') for a in opportunity.get('allocations',[]) if a.get('url')])
            episodes.append(episode)
        else:
            episode['last_seen']=now
        seen.add(episode['id'])
    def quote(episode):
        return episode,_exit_quote(episode,config,book_provider)
    open_episodes=[e for e in episodes if e.get('status')=='open']
    with ThreadPoolExecutor(max_workers=4) as pool:
        for episode,result in pool.map(quote,open_episodes):
            if result.get('error'):
                episode['last_checked']=now;episode['last_error']=result['error']
                continue
            episode.pop('last_error',None)
            _apply_exit(episode,result,now)
            if now-float(number(episode.get('last_snapshot_at',0)))>=float(snapshot_seconds) or episode['status']=='target':
                episode['last_snapshot_at']=now
                snapshots.append(_snapshot(episode,now))
    stats=_stats(episodes,now)
    ledger=dict(updated_at=now,min_yield_pct=str(min_yield),target_yield_pct=str(target_yield),
                tax_rate_pct=str(tax_rate),episodes=episodes,stats=stats)
    temp=ledger_path.with_suffix('.tmp');temp.write_text(json.dumps(ledger,ensure_ascii=False,default=str));temp.replace(ledger_path)
    if snapshots:
        with history_path.open('a') as handle:
            for row in snapshots:
                handle.write(json.dumps(row,ensure_ascii=False,default=str)+'\n')
    return dict(stats=stats,episodes=len(episodes),min_yield_pct=str(min_yield),
                target_yield_pct=str(target_yield),tax_rate_pct=str(tax_rate))


def ledger_csv(ledger):
    fields=['id','name','platforms','status','first_seen','closed_at','duration_seconds','entry_gross_roi_pct',
            'entry_net_roi_pct','max_exit_net_roi_pct','exit_net_roi_pct','exit_net_profit','target_yield_pct',
            'tax_rate_pct','budget','close_reason','last_error']
    rows=[]
    for episode in ledger.get('episodes',[]):
        venues=' + '.join(sorted({leg.get('venue','') for leg in episode.get('legs',[])}))
        rows.append(dict(
            id=episode.get('id'),name=episode.get('name'),platforms=venues,status=episode.get('status'),
            first_seen=datetime.fromtimestamp(episode['first_seen'],timezone.utc).isoformat()
            if episode.get('first_seen') else '',
            closed_at=datetime.fromtimestamp(episode['closed_at'],timezone.utc).isoformat()
            if episode.get('closed_at') else '',
            duration_seconds=episode.get('duration_seconds'),
            entry_gross_roi_pct=episode.get('entry_gross_roi_pct'),
            entry_net_roi_pct=episode.get('entry_net_roi_pct'),
            max_exit_net_roi_pct=episode.get('max_exit_net_roi_pct'),
            exit_net_roi_pct=episode.get('final_exit_net_roi_pct') or episode.get('current_exit_net_roi_pct'),
            exit_net_profit=episode.get('final_exit_net_profit') or episode.get('current_exit_net_profit'),
            target_yield_pct=episode.get('target_yield_pct'),tax_rate_pct=episode.get('tax_rate_pct'),
            budget=episode.get('budget'),close_reason=episode.get('close_reason'),
            last_error=episode.get('last_error')))
    return fields,rows


def backfill_from_snapshots(snapshot_path,config,output_dir=Path('output'),catalog=None,
                            include_names=None,exclude_names=('trump',)):
    snapshot_path=Path(snapshot_path);output_dir=Path(output_dir)
    ledger_path=output_dir/'opportunity-ledger.json'
    try:
        ledger=json.loads(ledger_path.read_text());episodes=ledger.get('episodes',[])
    except (OSError,ValueError):
        episodes=[]
    existing={(e.get('key'),e.get('status')) for e in episodes}
    catalog={f"{m['venue']}:{m['id']}":m for m in (catalog or [])}
    min_yield=number(config.get('tracking_min_yield_pct','1.5'))
    tax_rate=number(config.get('tax_rate_pct','0'))
    target_yield=number(config.get('tracking_target_yield_pct','1.5'))
    include=[x.lower() for x in (include_names or [])]
    exclude=[x.lower() for x in exclude_names]
    first={}
    with snapshot_path.open() as handle:
        for line in handle:
            try:report=json.loads(line)
            except ValueError:continue
            for opportunity in report.get('opportunities',[]):
                name=str(opportunity.get('name',''))
                if include and not any(x in name.lower() for x in include):continue
                if any(x in name.lower() for x in exclude):continue
                if (opportunity.get('rules') or {}).get('status')!='consistent':continue
                if not (opportunity.get('match') or {}).get('rules_identical'):continue
                cost,gross,net=_entry(opportunity,tax_rate)
                if net<min_yield:continue
                key=_episode_key(opportunity)
                if key not in first:
                    first[key]=(float(report.get('timestamp') or time.time()),opportunity,cost,gross,net)
    added=0
    for key,(timestamp,opportunity,cost,gross,net) in first.items():
        if (key,'open') in existing:continue
        episode=dict(
            id=_new_id(key,timestamp),key=key,name=opportunity.get('name') or 'Opportunità',
            first_seen=timestamp,last_seen=timestamp,last_checked=timestamp,status='open',
            entry_cost=str(cost),entry_gross_roi_pct=str(gross),entry_net_roi_pct=str(net),
            target_yield_pct=str(target_yield),tax_rate_pct=str(tax_rate),
            max_exit_net_roi_pct=None,min_exit_net_roi_pct=None,
            budget=str(opportunity.get('budget')),legs=_episode_legs(opportunity,catalog),
            last_snapshot_at=0,urls=[a.get('url') for a in opportunity.get('allocations',[]) if a.get('url')],
            backfilled=True)
        episodes.append(episode);added+=1
    ledger=dict(updated_at=time.time(),min_yield_pct=str(min_yield),target_yield_pct=str(target_yield),
                tax_rate_pct=str(tax_rate),episodes=episodes,
                stats=_stats(episodes,time.time()))
    temp=ledger_path.with_suffix('.tmp');temp.write_text(json.dumps(ledger,ensure_ascii=False,default=str));temp.replace(ledger_path)
    return added
