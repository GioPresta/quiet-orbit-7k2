import itertools
import json
import time
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import quote
from .api import Client, ApiError, VENUES
from .matching import match_markets, deadline
from .engine import D, Fee, Leg, number, optimize
from .rules import assess_rules
from .exit import evaluate_exit, convergence_exit
from .liquidity import activity


def json_default(value):
    if isinstance(value, D):
        return str(value)
    raise TypeError(type(value).__name__)


def dumps(value):
    return json.dumps(value, default=json_default, ensure_ascii=False)


def enrich_predict_expiries(results, config):
    """Predict exposes category endsAt; the market payload does not include it."""
    slugs={a.get('category_slug') for r in results for a in r['allocations']
           if a['venue']=='predict' and a.get('expiry_at') is None and a.get('category_slug')}
    if not slugs:
        return
    path=Path(config.get('_output_dir','output'))/'predict-categories.json'
    try:
        cache=json.loads(path.read_text())
        if not isinstance(cache,dict):
            cache={}
    except (OSError,ValueError):
        cache={}
    missing=[slug for slug in slugs if not cache.get(slug)]
    if missing:
        def fetch(slug):
            try:
                data=Client('predict').request('/v1/categories/'+quote(str(slug),safe=''))
                return slug,data.get('endsAt')
            except (ApiError,ValueError,KeyError,TypeError):
                return slug,None
        with ThreadPoolExecutor(max_workers=3) as pool:
            for slug,end in pool.map(fetch,missing):
                if end:
                    cache[slug]=end
        try:
            path.parent.mkdir(parents=True,exist_ok=True)
            temp=path.with_suffix('.tmp')
            temp.write_text(dumps(cache))
            temp.replace(path)
        except OSError:
            pass
    for result in results:
        for allocation in result['allocations']:
            if allocation['venue']=='predict' and allocation.get('expiry_at') is None:
                end=cache.get(allocation.get('category_slug'))
                if end:
                    allocation['expiry_at']=deadline(end)
        known=[a.get('expiry_at') for a in result['allocations'] if a.get('expiry_at')]
        if known:
            result['holding_until']=max(known)


def fee_for(market, overrides):
    ident = f"{market['venue']}:{market['id']}"
    cfg = overrides.get(ident, overrides.get(market['venue']))
    if cfg:
        return Fee(cfg['model'], number(cfg['rate']), cfg['source'], cfg.get('conservative', False),
                   number(cfg.get('minimum', '.25' if cfg['model']=='opinion' else '0')), cfg.get('estimated', cfg['model']=='opinion'))
    if market['venue'] == 'polymarket':
        if market.get('fees_enabled') is False:
            return Fee('shares_curve', D('0'), 'Gamma feesEnabled=false')
        schedule = market.get('fee_schedule')
        if schedule and number(schedule.get('exponent', 1)) == 1:
            return Fee('shares_curve', number(schedule['rate']), 'Polymarket: fee in quote sugli acquisti, Gamma feeSchedule exponent=1')
    if market['venue'] == 'predict':
        discount = number(overrides.get('predict_discount', '0'))
        if discount not in (D('0'), D('.10')):
            raise ValueError('Sconto Predict ammesso: 0 oppure 0.10 verificato sul proprio account')
        rate = number(market.get('fee_rate_bps', 200))/10000
        return Fee('predict', rate*(1-discount), 'Predict: base * min(p,1-p), sconto account esplicito')
    if market['venue'] == 'opinion':
        # Documented maximum effective rate is 1% at p=.5 -> coefficient .04.
        # Per-level fill floor is estimated because aggregated books hide maker orders.
        return Fee('opinion', D('.04'), 'Opinion: curva massima 1%, minimo $0.25/fill (stima per livello)',
                   False, D('.25'), True)
    if market['venue'] == 'limitless':
        # Upper bound from official CLOB buy fee range; fees are withheld in shares.
        return Fee('shares', D('.03'), 'Limitless: limite superiore documentato 3% contratti', True)
    raise ValueError(f'{ident}: fee sconosciuta; configura fees con modello, rate e source')


def discover(venues, pages, queries=(), predict_pages=None, progress=None, liquidity_settings=None):
    catalog, statuses = [], {}
    def load(v):
        client=Client(v)
        rows=client.discover(predict_pages if v=='predict' and predict_pages is not None else pages, progress=progress)
        info=getattr(client,'discovery_info',{})
        return rows, info if isinstance(info,dict) else {}
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {pool.submit(load, v): v for v in venues}
        for future in as_completed(futures):
            v = futures[future]
            try:
                rows, info = future.result()
                catalog.extend(rows)
                statuses[v] = dict(status='ok', markets=len(rows), **info)
            except (ApiError, ValueError, KeyError, TypeError) as e:
                statuses[v] = dict(status='unavailable', reason=str(e))
    if 'predict' in venues:
        for query in queries:
            try:
                client=Client('predict')
                payload=client.request('/v1/search',{'query':query,'limit':25})
                for row in payload.get('markets',[]):
                    market=client.normalize(row)
                    if market:catalog.append(market)
            except (ApiError,ValueError,KeyError,TypeError):
                statuses.setdefault('predict',{})['search_discovery']='failed'
    # Platform-provided Polymarket links let us discover the matching market even
    # when it is absent from the volume-ranked first pages.
    if 'polymarket' in venues:
        known_slugs={m.get('slug') for m in catalog if m['venue']=='polymarket'}
        known_conditions={m.get('condition_id') for m in catalog if m['venue']=='polymarket'}
        slugs=sorted({m['external_slug'] for m in catalog if m.get('external_slug')} - known_slugs)
        conditions=sorted({c for m in catalog for c in (m.get('polymarket_condition_ids') or [])
                           if isinstance(c,str)} - known_conditions)
        client=Client('polymarket')
        for parameter, values in (('slug',slugs),('condition_ids',conditions)):
            try:
                for start in range(0,len(values),100):
                    rows=client.request('/markets',{parameter:values[start:start+100], 'limit':100, 'closed':'false'})
                    for row in rows:
                        m=client.normalize(row)
                        if m:catalog.append(m)
            except (ApiError,ValueError,KeyError,TypeError):
                statuses.setdefault('polymarket',{})['linked_discovery_'+parameter]='failed'
    catalog=list({(m['venue'],m['id']):m for m in catalog}.values())
    catalog.sort(key=lambda m: (m['venue'], m['id']))
    for venue in venues:
        if statuses.get(venue,{}).get('status')=='ok':
            statuses[venue]['markets']=sum(m['venue']==venue for m in catalog)
    if liquidity_settings is not None:
        from .liquidity import enrich
        catalog=enrich(catalog,liquidity_settings,progress=progress)
    return catalog, statuses


def suggestions(catalog):
    return match_markets(catalog)


def scan(catalog, config, budget, max_age=20, max_skew=5, min_profit=D('0'), markets_per_venue=8, book_provider=None):
    errors, warnings, results, rejected = [], [], [], []
    active = []
    for m in catalog:
        end = m.get('end')
        try:
            if isinstance(end, str) and not end.replace('.', '', 1).isdigit():
                expiry_at = datetime.fromisoformat(end.replace('Z', '+00:00')).timestamp()
            elif end is not None:
                expiry_at = float(end)
                if expiry_at > 1e11:
                    expiry_at /= 1000
            else:
                expiry_at = None
            if expiry_at is not None and expiry_at <= time.time():
                continue
        except (ValueError, TypeError, OverflowError):
            warnings.append(f"{m['venue']}:{m['id']}: scadenza non valida, escluso")
            continue
        active.append(m)
    catalog = active
    mapping = {f"{m['venue']}:{m['id']}": m for m in catalog}
    groups = []
    # Automatic same-market complete sets need no cross-venue matching.
    for venue in VENUES:
        subset = [m for m in catalog if m['venue'] == venue][:markets_per_venue]
        for m in subset:
            ident = f"{m['venue']}:{m['id']}"
            groups.append(dict(name=m['title'], verified=True,
                               legs=[(ident, 'YES'), (ident, 'NO')], kind='same_market'))
    for group in config.get('groups', []):
        if group.get('resolution_verified') is not True or not group.get('verification_notes'):
            warnings.append(f"{group.get('name', 'Gruppo')}: regole non verificate, escluso")
            continue
        refs = group.get('markets', [])
        if len(refs) != 2 or refs[0] == refs[1] or any(r not in mapping for r in refs):
            errors.append(f"{group.get('name')}: servono due mercati distinti presenti nel catalogo")
            continue
        # Both YES labels must describe the exact same proposition.
        for a, b in (refs, refs[::-1]):
            groups.append(dict(name=group['name'], verified=True,
                               legs=[(a, 'YES'), (b, 'NO')], kind='cross_market'))
    manual_pairs = {tuple(sorted(g['markets'])) for g in config.get('groups', [])
                    if g.get('resolution_verified') is True and g.get('verification_notes')}
    matches, screening = select_matches(catalog,config,budget)
    if config.get('auto_match', True):
        for match in matches:
            if tuple(match['markets']) in manual_pairs:
                continue
            for a,b in (match['markets'], match['markets'][::-1]):
                groups.append(dict(name=match['name'], verified=False,
                                   legs=[(a,'YES'),(b,'NO')], kind='auto_cross_market', match=match))
    # Queue both legs of a priority hedge together: sorting by venue fetched the
    # two venues far apart and could fail the snapshot-skew check artificially.
    priority={tuple(sorted(pair)) for pair in config.get('priority_pairs',[])}
    groups.sort(key=lambda g:0 if tuple(sorted(r[0] for r in g['legs'])) in priority else
                1 if g['kind'] in ('auto_cross_market','cross_market') else 2)
    needed_order=list(dict.fromkeys(ref for group in groups for ref in group['legs']))
    needed = set(needed_order)
    books = {}
    def fetch(ref):
        ident, outcome = ref
        m = mapping[ident]
        fee = fee_for(m, config.get('fees', {}))
        book = book_provider(m, outcome) if book_provider else Client(m['venue']).book(m, outcome)
        return fee, book
    # Bounded fanout. No stale cache fallback after failures.
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {pool.submit(fetch, ref): ref for ref in needed_order}
        for future in as_completed(futures):
            ref = futures[future]
            try:
                books[ref] = future.result()
            except (ApiError, ValueError, KeyError, TypeError) as e:
                errors.append(f'{ref[0]} {ref[1]}: {e}')
    for group in groups:
        if any(ref not in books for ref in group['legs']):
            continue
        source_times = [books[r][1]['source_time'] for r in group['legs']]
        now = time.time()
        # Snapshot timestamps can be the last book mutation, not snapshot creation.
        # A freshly requested complete snapshot revalidates an unchanged book;
        # unsequenced/unknown data and deltas still use the source event timestamp.
        checked_times=[books[r][1].get('checked_at',books[r][1]['source_time']) for r in group['legs']]
        stale = any(t is not None and now-t > max_age for t in checked_times)
        stale = stale or any(t is not None and t > now+5 for t in source_times)
        receipt_stale = any(now-books[r][1]['received_at'] > max_age and books[r][1].get('freshness_basis')!='synchronized_stream' for r in group['legs'])
        known = [t for t in checked_times if t is not None]
        if stale or receipt_stale or (len(known) > 1 and max(known)-min(known) > max_skew):
            warnings.append(f"{group['name']}: snapshot vecchi o non sincronizzati, escluso")
            continue
        legs = []
        for ident, outcome in group['legs']:
            m = mapping[ident]
            fee, book = books[(ident, outcome)]
            balance = config.get('balances', {}).get(m['venue'])
            legs.append(Leg(m['venue'], m['id'], outcome, book['asks'], fee,
                            max(number(m.get('min_order', 0)), book['min_order']),
                            number(balance) if balance is not None else None,
                            number(m.get('min_notional', '5' if m['venue']=='opinion' else '1' if m['venue']=='predict' else '0'))))
        # Balances are per venue; both legs on same venue share that balance.
        venues = {leg.venue for leg in legs}
        reserve = number(config.get('rounding_reserve', '.02'))
        fixed = number(config.get('fixed_cost_per_hedge', '0')) + reserve
        result = optimize(legs, budget, fixed, number(config.get('net_share_step', '.000001')), require_full_budget=True)
        if not result:
            if all(l.asks for l in legs) and sum((l.fee.unit(l.asks[0][0])[0] for l in legs),D(0))<1:
                rejected.append(dict(name=group['name'], kind=group['kind'],
                                     reason='Intero budget non eseguibile agli ask con profitto netto, fee, saldi e minimi ordine',
                                     min_notional={l.venue:l.min_notional for l in legs}))
            continue
        if result['profit'] < min_profit:
            continue
        # Compute depth cap independent of requested budget, respecting declared balances.
        cap_budget = D('1e30')
        if len(venues) == 1 and config.get('balances', {}).get(legs[0].venue) is not None:
            cap_budget = number(config['balances'][legs[0].venue])
        cap = optimize(legs, cap_budget, fixed, number(config.get('net_share_step', '.000001')))
        result.update(name=group['name'], kind=group['kind'],
                      status='simulation' if group['verified'] else 'candidate_unverified_rules',
                      resolution_verified=group['verified'], match=group.get('match'),
                      fee_estimated=any(l.fee.estimated for l in legs),
                      max_profitable_cost=cap['total_cost'] if cap else None,
                      max_profitable_payout=cap['payout'] if cap else None,
                      budget=budget, unused_budget=budget-result['total_cost'],
                      execution_mode='taker_full_budget', full_budget_executable=True,
                      source_timestamps=source_times,
                      snapshot_checked_at=checked_times,
                      transports=[books[r][1].get('transport', 'rest') for r in group['legs']],
                      freshness='source_timestamp_missing' if None in source_times else
                                'recent_snapshot_old_last_change' if any(now-t>max_age for t in source_times) else 'checked',
                      italy_restriction=any(l.venue == 'polymarket' for l in legs),
                      volume=[dict(venue=mapping[r[0]]['venue'], market=r[0],
                                   volume=mapping[r[0]].get('volume'),
                                   volume24h=mapping[r[0]].get('volume24h')) for r in group['legs']])
        markets=[mapping[r[0]] for r in group['legs']]
        for allocation,market,leg,ref in zip(result['allocations'],markets,legs,group['legs']):
            book=books[ref][1]
            asks=sorted((number(p),number(s)) for p,s in book.get('asks',[]) if number(s)>0)
            bids=sorted(((number(p),number(s)) for p,s in book.get('bids',[]) if number(s)>0),reverse=True)
            best_ask=asks[0][0] if asks else None
            best_bid=bids[0][0] if bids else None
            allocation.update(url=market.get('url'),price_avg=allocation['notional']/allocation['gross_shares'],
                              cash_fee_pct=allocation['cash_fee']/allocation['notional']*100,
                              fee_model=leg.fee.model,fee_rate=leg.fee.rate,fee_minimum=leg.fee.minimum,
                              fee_in_quotes=allocation['withheld_shares'],
                              fee_settlement='outcome_tokens' if leg.fee.model in ('shares','shares_curve') else 'collateral',
                              withheld_fee_pct=allocation['withheld_shares']/allocation['gross_shares']*100,volume24h=market.get('volume24h'),
                              volume_total=market.get('volume'),expiry_at=deadline(market.get('end')),
                              category_slug=market.get('category_slug'),
                              outcome_volume_total=market.get('outcome_volume_total'),
                              outcome_volume24h=market.get('outcome_volume24h'),
                              best_ask=str(best_ask) if best_ask is not None else None,
                              best_ask_size=str(asks[0][1]) if asks else None,
                              ask_depth_shares=str(sum((s for _,s in asks),D(0))),
                              ask_depth_notional=str(sum((p*s for p,s in asks),D(0))),
                              best_bid=str(best_bid) if best_bid is not None else None,
                              bid_depth_shares=str(sum((s for _,s in bids),D(0))),
                              bid_depth_notional=str(sum((p*s for p,s in bids),D(0)))
                              )
        result['rules']=assess_rules(markets,group.get('match'))
        result['early_exit']=evaluate_exit(result,[books[r][1] for r in group['legs']],
                                           [books[r][0] for r in group['legs']],config.get('fixed_exit_cost',0))
        result['liquidity']=activity(markets)
        result['liquidity']['bid_coverage_ratio']=min((l['sellable_shares']/l['requested_shares'] for l in result['early_exit']['legs']),default=D(0))
        result['convergence_exit']=convergence_exit(result,[books[r][0] for r in group['legs']],config.get('fixed_exit_cost',0))
        result['buy_fees_total']=sum((a['cash_fee'] for a in result['allocations']),D(0))
        result['buy_fee_quotes_total']=sum((a['withheld_shares'] for a in result['allocations']),D(0))
        ladder=size_ladder(legs,fixed,number(config.get('net_share_step','.000001')))
        result['size_ladder']=ladder
        feasible=[number(row['budget']) for row in ladder if row['feasible'] and number(row['profit'])>0]
        result['max_feasible_budget']=str(max(feasible)) if feasible else None
        result['holding_until']=max((deadline(m.get('end')) or 0 for m in markets),default=0) or None
        results.append(result)
    enrich_predict_expiries(results,config)
    if config.get('sort_policy')=='liquidity':
        results.sort(key=lambda r:(r['rules']['status']=='conflict',-(r['liquidity']['volume24h_min'] or 0),
                                   -r['liquidity']['bid_coverage_ratio'],-r['profit']))
    else:results.sort(key=lambda r:-r['profit'])
    return dict(timestamp=time.time(), mode='read_only_simulation', currency='USD_stablecoin_nominal',
                opportunities=results, matches=matches, rejected=rejected, errors=errors, warnings=warnings,
                coverage=dict(**screening, catalog_markets=len(catalog), requested_books=len(needed), successful_books=len(books),
                              automatic_markets_per_venue=markets_per_venue, cross_market_directions=sum(g['kind'] in ('cross_market','auto_cross_market') for g in groups),
                              matched_venue_pairs=sorted({tuple(sorted(r.split(':')[0] for r in m['markets'])) for m in matches}),
                              books_by_venue={v:sum(r[0].split(':')[0]==v for r in books) for v in VENUES if any(r[0].split(':')[0]==v for r in needed)}),
                note='Liquidità indicativa: nessuna riserva o esecuzione. Stablecoin considerate al valore nominale USD.')


def tracked_markets(catalog, config, markets_per_venue):
    catalog=[m for m in catalog if deadline(m.get('end')) is None or deadline(m['end'])>time.time()]
    refs=set()
    for venue in VENUES:
        refs.update(f"{m['venue']}:{m['id']}" for m in [m for m in catalog if m['venue']==venue][:markets_per_venue])
    matches,_=select_matches(catalog,config,number(config.get('_budget',200)))
    for match in matches:
        refs.update(match['markets'])
    for group in config.get('groups',[]):
        refs.update(group.get('markets',[]))
    return [m for m in catalog if f"{m['venue']}:{m['id']}" in refs]


def indicative_profit(match,mapping,config,budget):
    """Screening only. Never exported as an executable opportunity."""
    fixed=number(config.get('fixed_cost_per_hedge',0))+number(config.get('rounding_reserve','.02'))
    best=None
    for a,b in (match['markets'],match['markets'][::-1]):
        unit=D(0);capacity=D('1e30');known=True;terms=[]
        for ref,outcome in ((a,'YES'),(b,'NO')):
            market=mapping[ref]
            quote=market.get('indicative_asks',{}).get(outcome)
            if not quote or quote.get('price') is None:
                known=False;break
            price=number(quote['price'])
            if not 0<price<1:
                known=False;break
            try:fee=fee_for(market,config.get('fees',{}))
            except ValueError:known=False;break
            cost,gross=fee.unit(price)
            unit+=cost
            terms.append((market,price,gross))
            balance=config.get('balances',{}).get(market['venue'])
            if balance is not None:capacity=min(capacity,number(balance)/cost)
            if quote.get('size') is not None:
                capacity=min(capacity,number(quote['size'])/gross)
        if known:
            q=min(capacity,max(D(0),budget-fixed)/unit)
            valid=all(q*g>=number(m.get('min_order',0)) and q*g*p>=number(m.get('min_notional',1 if m['venue']=='predict' else 0)) for m,p,g in terms)
            profit=q*(1-unit)-fixed if valid else D(0)
            best=max(best,profit) if best is not None else profit
    return best


def size_ladder(legs,fixed,step):
    rows=[]
    for budget in (10,25,50,100,250,500,1000):
        result=optimize(legs,D(budget),fixed,step,require_full_budget=True)
        rows.append(dict(
            budget=str(budget),feasible=bool(result),
            total_cost=str(result['total_cost']) if result else None,
            payout=str(result['payout']) if result else None,
            profit=str(result['profit']) if result else None,
            roi_pct=str(result['roi_pct']) if result else None))
    return rows


def select_matches(catalog,config,budget):
    """Rank a broad universe, reserve rotating exploration slots, then verify books."""
    limit=int(config.get('max_matches',12))
    if not 1<=limit<=100:
        raise ValueError('max_matches deve essere 1..100')
    universe=match_markets(catalog,float(config.get('match_threshold',.88)),
                           int(config.get('candidate_limit',20000)),config.get('priority_pairs',[]))
    mapping={f"{m['venue']}:{m['id']}":m for m in catalog}
    priority={tuple(sorted(p)) for p in config.get('priority_pairs',[])}
    scored=[(m,indicative_profit(m,mapping,config,budget)) for m in universe]
    priority_rows=[(m,s) for m,s in scored if tuple(m['markets']) in priority]
    floor=number(config.get('min_volume24h',0))
    if floor>0:
        scored=[(m,s) for m,s in scored
                if (activity([mapping[r] for r in m['markets']])['volume24h_min'] is None
                    or activity([mapping[r] for r in m['markets']])['volume24h_min']>=floor)]
    selected=[dict(m,screening_profit=score) for m,score in priority_rows][:limit]
    chosen={m['id'] for m in selected}
    # Reserve a few slots for non-dominant venue pairs so Opinion/Limitless are
    # not hidden by the much larger Polymarket/Predict candidate pool.
    exploration=min(max(0,int(config.get('exploration_slots',0))),max(0,limit-len(selected)))
    pair_quota=int(config.get('venue_pair_slots',4)) if limit>=8 else 0
    if pair_quota:
        groups={}
        for match,score in scored:
            key=tuple(sorted(ref.split(':',1)[0] for ref in match['markets']))
            groups.setdefault(key,[]).append((match,score))
        dominant=max(groups,key=lambda key:len(groups[key])) if groups else None
        for key in sorted(groups):
            if key==dominant:
                continue
            rows=sorted(groups[key],key=lambda row:(
                -(activity([mapping[r] for r in row[0]['markets']])['volume24h_min'] or 0)
                if config.get('sort_policy')=='liquidity' else 0,
                -(row[1] if row[1] is not None else D('-1e30')),-row[0]['score'],row[0]['id']))
            for match,score in rows[:pair_quota]:
                if len(selected)>=limit-exploration:
                    break
                if match['id'] in chosen:
                    continue
                selected.append(dict(match,screening_profit=score))
                chosen.add(match['id'])
    profitable=sorted([(m,score) for m,score in scored if score is not None and score>0 and m['id'] not in chosen],
                      key=lambda row:(-(activity([mapping[r] for r in row[0]['markets']])['volume24h_min'] or 0) if config.get('sort_policy')=='liquidity' else 0,-row[1],-row[0]['score'],row[0]['id']))
    take=max(0,limit-len(selected)-exploration)
    selected.extend(dict(m,screening_profit=score) for m,score in profitable[:take])
    chosen={m['id'] for m in selected}
    other=[(m,score) for m,score in scored if m['id'] not in chosen]
    slots=limit-len(selected)
    rotation=max(10,int(config.get('rotation_seconds',60)))
    if other and slots:
        offset=(int(time.time())//rotation*max(1,slots))%len(other) if exploration else 0
        rotated=other[offset:]+other[:offset]
        selected.extend(dict(m,screening_profit=score) for m,score in rotated[:slots])
    return selected,dict(candidate_pairs=len(universe),screening_positive_pairs=sum(score is not None and score>0 for _,score in scored),
                         selected_pairs=len(selected),screening_unknown_pairs=sum(score is None for _,score in scored),
                         candidate_limit=int(config.get('candidate_limit',20000)))


def background_discovery(*args):
    """Metadata refresh cannot keep the scanner process alive during shutdown."""
    import threading
    from concurrent.futures import Future
    future=Future()
    def run():
        if not future.set_running_or_notify_cancel():return
        try:future.set_result(discover(*args))
        except Exception as exc:future.set_exception(exc)
    threading.Thread(target=run,name='catalog-discovery',daemon=True).start()
    return future
