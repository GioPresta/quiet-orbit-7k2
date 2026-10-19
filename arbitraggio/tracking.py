"""Read-only wallet position tracking with executable-bid liquidation estimates."""
import argparse
import json
import os
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import time
from urllib.parse import urlencode

from .api import ApiError, Client, get
from .engine import D, Fee, number
from .exit import liquidate
from .matching import deadline


DATA_API='https://data-api.polymarket.com'
LIMITLESS_API='https://api.limitless.exchange'
GAMMA_API='https://gamma-api.polymarket.com'
VENUES={'polymarket':'Polymarket','predict':'Predict.fun','limitless':'Limitless','opinion':'Opinion'}


def load_env():
    path=Path('.env')
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        if '=' in line and not line.lstrip().startswith('#'):
            key,value=line.split('=',1)
            if key.strip() in ('OPINION_API_KEY','PREDICT_API_KEY','LIMITLESS_API_KEY'):
                name=key.strip();secret=value.strip().strip('"').strip("'")
                if not os.environ.get(name):
                    os.environ[name]=secret


def public_get(url,headers=None):
    return get(url,headers)


def parse_polymarket(rows,wallet):
    positions=[]
    for row in rows or []:
        shares=number(row.get('size'))
        if shares is None or shares<=0:
            continue
        avg=number(row.get('avgPrice')) or D(0)
        cost=number(row.get('initialValue'))
        if cost is None:
            cost=shares*avg
        mark_price=number(row.get('curPrice')) or D(0)
        mark=number(row.get('currentValue'))
        if mark is None:
            mark=shares*mark_price
        outcome='YES' if str(row.get('outcome','')).lower() in ('yes','up') else 'NO'
        slug=row.get('eventSlug') or row.get('slug') or ''
        positions.append(dict(
            venue='polymarket',wallet=wallet['address'],wallet_label=wallet.get('label') or 'Polymarket',
            market_id=str(row.get('conditionId') or ''),token_id=str(row.get('asset') or ''),
            title=row.get('title') or row.get('question') or 'Mercato Polymarket',outcome=outcome,
            shares=shares,avg_price=avg,cost=cost,mark_price=mark_price,mark_value=mark,
            platform_pnl=number(row.get('cashPnl')),realized_pnl=number(row.get('realizedPnl')) or D(0),
            redeemable=bool(row.get('redeemable')),resolved=bool(row.get('redeemable')),
            url=f'https://polymarket.com/event/{slug}' if slug else None,
            expiry_at=deadline(row.get('endDate')),fee_rate_bps=0))
    return positions


def parse_predict(rows,wallet):
    positions=[]
    for row in rows or []:
        market=row.get('market') or {}
        outcome=row.get('outcome') or {}
        shares=(number(row.get('amount')) or D(0))/D('1e18')
        if shares<=0:
            continue
        avg=number(row.get('averageBuyPriceUsd')) or D(0)
        cost=shares*avg
        value=number(row.get('valueUsd'))
        if value is None:
            value=shares*avg
        side='YES' if number(outcome.get('indexSet'))==1 else 'NO'
        slug=market.get('categorySlug') or market.get('slug') or ''
        url=(f"https://predict.fun/market/{slug}?market={market.get('id')}" if slug else
             f"https://predict.fun/market/{market.get('id')}")
        positions.append(dict(
            venue='predict',wallet=wallet['address'],wallet_label=wallet.get('label') or 'Predict.fun',
            market_id=str(market.get('id') or ''),token_id=str(outcome.get('onChainId') or ''),
            title=market.get('question') or market.get('title') or 'Mercato Predict.fun',outcome=side,
            shares=shares,avg_price=avg,cost=cost,mark_price=(value/shares if shares else D(0)),
            mark_value=value,platform_pnl=number(row.get('pnlUsd')),realized_pnl=D(0),
            redeemable=str(outcome.get('status') or '').upper()=='WON',
            resolved=bool(outcome.get('status')),url=url,expiry_at=deadline(market.get('endsAt')),
            category_slug=market.get('categorySlug'),
            fee_rate_bps=number(market.get('feeRateBps',200))))
    return positions


def parse_limitless(data,wallet):
    positions=[]
    for row in (data or {}).get('clob',[]) or []:
        market=row.get('market') or {}
        balances=row.get('tokensBalance') or {}
        sides=row.get('positions') or {}
        slug=market.get('slug') or str(market.get('id') or '')
        for side in ('yes','no'):
            info=sides.get(side) or {}
            shares=(number(balances.get(side)) or D(0))/D('1e6')
            cost=(number(info.get('cost')) or D(0))/D('1e6')
            value=(number(info.get('marketValue')) or D(0))/D('1e6')
            avg=(number(info.get('fillPrice')) or D(0))/D('1e6')
            if shares<=0 and cost<=0:
                continue
            positions.append(dict(
                venue='limitless',wallet=wallet['address'],wallet_label=wallet.get('label') or 'Limitless',
                market_id=slug,token_id=str(market.get(f'{side}PositionId') or ''),
                title=market.get('title') or 'Mercato Limitless',outcome=side.upper(),
                shares=shares,avg_price=avg,cost=cost,mark_price=(value/shares if shares else avg),
                mark_value=value,platform_pnl=(number(info.get('unrealizedPnl')) or D(0))/D('1e6'),
                realized_pnl=(number(info.get('realisedPnl')) or D(0))/D('1e6'),
                redeemable=(market.get('winningOutcomeIndex') is not None and
                            str(market.get('winningOutcomeIndex'))==str(0 if side=='yes' else 1)),
                resolved=bool(market.get('status')=='RESOLVED' or market.get('winningOutcomeIndex') is not None),
                url=f'https://limitless.exchange/markets/{market.get("slug") or slug}',
                expiry_at=deadline(market.get('deadline') or market.get('expirationDate')),
                fee_rate_bps=0))
    return positions


def parse_opinion(rows,wallet):
    positions=[]
    for row in rows or []:
        owned=number(row.get('sharesOwned')) or D(0)
        frozen=number(row.get('sharesFrozen')) or D(0)
        shares=max(D(0),owned-frozen)
        if shares<=0:
            continue
        avg=number(row.get('avgEntryPrice')) or D(0)
        value=number(row.get('currentValueInQuoteToken'))
        if value is None:
            value=shares*avg
        elif owned:
            value=value*shares/owned
        claim=str(row.get('claimStatusEnum') or '')
        status=str(row.get('marketStatusEnum') or '')
        outcome='YES' if str(row.get('outcomeSideEnum') or '').lower()=='yes' else 'NO'
        positions.append(dict(
            venue='opinion',wallet=wallet['address'],wallet_label=wallet.get('label') or 'Opinion',
            market_id=str(row.get('marketId') or ''),token_id=str(row.get('tokenId') or ''),
            condition_id=str(row.get('conditionId') or ''),
            title=row.get('marketTitle') or 'Mercato Opinion',outcome=outcome,
            shares=shares,avg_price=avg,cost=shares*avg,
            mark_price=(value/shares if shares else avg),mark_value=value,
            platform_pnl=number(row.get('unrealizedPnl')),
            realized_pnl=D(0),redeemable=claim=='CanClaim',
            resolved=claim in ('CanClaim','Claimed') or status.lower() in ('resolved','closed'),
            url='https://app.opinion.trade/',expiry_at=deadline(row.get('marketCutoffAt')),
            fee_rate_bps=0))
    return positions


def fetch_positions(wallet):
    venue=wallet['venue']
    address=wallet['address']
    if venue=='polymarket':
        query=urlencode({'user':address,'limit':500,'sizeThreshold':0})
        return parse_polymarket(public_get(f'{DATA_API}/positions?{query}'),wallet)
    if venue=='predict':
        client=Client('predict')
        rows=[];after=None;pages=0
        while pages<20:
            params=dict(first=100)
            if after:
                params['after']=after
            batch=client.request(f'/v1/positions/{address}',params)
            rows.extend(batch or [])
            after=getattr(client,'cursor',None)
            pages+=1
            if not after or not batch:
                break
        return parse_predict(rows,wallet)
    if venue=='limitless':
        try:
            data=public_get(f'{LIMITLESS_API}/portfolio/{address}/positions')
        except ApiError as error:
            if 'HTTP 404' in str(error):
                return []
            raise
        return parse_limitless(data,wallet)
    if venue=='opinion':
        client=Client('opinion')
        rows=[];page=1
        while page<=50:
            data=client.request(f'/positions/user/{address}',dict(page=page,limit=20))
            batch=data.get('list',[]) if isinstance(data,dict) else []
            rows.extend(batch)
            total=number(data.get('total')) if isinstance(data,dict) else None
            if not batch or (total is not None and len(rows)>=total):
                break
            page+=1
        return parse_opinion(rows,wallet)
    raise ValueError(f'Piattaforma di tracking non supportata: {venue}')


def polymarket_fee(token_id,cache_path):
    cache={}
    try:
        cache=json.loads(cache_path.read_text())
        if not isinstance(cache,dict):
            cache={}
    except (OSError,ValueError):
        pass
    if token_id not in cache:
        try:
            rows=public_get(f'{GAMMA_API}/markets?'+urlencode({'clob_token_ids':token_id}))
            row=rows[0] if rows else None
        except (ApiError,IndexError,TypeError):
            row=None
        if row:
            enabled=row.get('feesEnabled')
            schedule=row.get('feeSchedule') or {}
            rate=number(schedule.get('rate')) if enabled is not False else D(0)
            exact=enabled is not False and bool(schedule) and number(schedule.get('exponent',1))==1
            cache[token_id]=dict(rate=str(rate or 0),exact=exact)
            try:
                cache_path.parent.mkdir(parents=True,exist_ok=True)
                temp=cache_path.with_suffix('.tmp')
                temp.write_text(json.dumps(cache))
                temp.replace(cache_path)
            except OSError:
                pass
    entry=cache.get(token_id)
    if entry is None:
        return Fee('shares_curve',D(0),'Polymarket: fee non disponibile',False,D(0),True)
    return Fee('shares_curve',number(entry.get('rate')) or D(0),
               'Polymarket: feeSchedule Gamma' if entry.get('exact') else 'Polymarket: fee stimata',
               False,D(0),not entry.get('exact'))


def position_fee(position,config,fee_cache):
    if position['venue']=='polymarket':
        return polymarket_fee(position.get('token_id',''),fee_cache)
    if position['venue']=='predict':
        discount=number(config.get('fees',{}).get('predict_discount','0'))
        rate=number(position.get('fee_rate_bps',200))/10000*(1-discount)
        return Fee('predict',rate,'Predict: feeRateBps del mercato',False,D(0),False)
    if position['venue']=='opinion':
        return Fee('opinion',D('.04'),'Opinion: curva massima 1%, minimo $0.25/fill (stima)',False,D('.25'),True)
    return Fee('shares',D('.015'),'Limitless: stima prudenziale vendita 1.5%',True,D(0),True)


def position_market(position):
    if position['venue'] in ('polymarket','opinion'):
        token=position.get('token_id') or '0'
        return dict(venue=position['venue'],id=position['market_id'],
                    yes_token=token if position['outcome']=='YES' else '0',
                    no_token=token if position['outcome']=='NO' else '0')
    return dict(venue=position['venue'],id=position['market_id'])


def enrich_expiry(position,cache_path):
    if position['venue']!='predict' or position.get('expiry_at'):
        return
    slug=position.get('category_slug')
    if not slug:
        return
    try:
        cache=json.loads(cache_path.read_text())
        end=cache.get(slug)
    except (OSError,ValueError):
        end=None
    if end:
        position['expiry_at']=deadline(end)


def sell_now(position,fee,book):
    result=liquidate(dict(position,market=position['market_id'],net_shares=position['shares']),book,fee)
    net=result['net_proceeds']
    redeemed=False
    if not result['fully_sellable'] and position.get('redeemable') and number(position.get('mark_price'))>=D('.99'):
        net=position['shares']
        redeemed=True
    pnl=net-position['cost']
    return dict(
        best_bid=result['best_bid'],sell_fees=result['sell_fees'],net_proceeds=net,
        fully_sellable=result['fully_sellable'] or redeemed,redeemed=redeemed,
        sellable_shares=position['shares'] if redeemed else result['sellable_shares'],
        unfilled_shares=result['unfilled_shares'],fee_estimated=result['fee_estimated'],
        pnl_now=pnl,roi_pct=(pnl/position['cost']*100 if position['cost'] else None))


def resolved_sale(position):
    mark=number(position.get('mark_price')) or D(0)
    net=position['shares'] if position.get('redeemable') and mark>=D('.5') else D(0)
    pnl=net-position['cost']
    return dict(best_bid=None,sell_fees=D(0),net_proceeds=net,fully_sellable=False,redeemed=True,
                resolved=True,sellable_shares=position['shares'] if net else D(0),
                unfilled_shares=D(0),fee_estimated=False,pnl_now=pnl,
                roi_pct=(pnl/position['cost']*100 if position['cost'] else None))


def run_once(config,output_dir=Path('output'),book_fetcher=None):
    output_dir=Path(output_dir)
    wallets=[w for w in config.get('tracking_wallets',[]) if isinstance(w,dict)]
    venue_status=[]
    positions=[]
    errors=[]
    fee_cache=output_dir/'tracking-fees.json'
    category_cache=output_dir/'predict-categories.json'
    for wallet in wallets:
        try:
            rows=fetch_positions(wallet)
            positions.extend(rows)
            venue_status.append(dict(address=wallet['address'],venue=wallet['venue'],
                                     label=wallet.get('label') or VENUES.get(wallet['venue'],wallet['venue']),
                                     positions=len(rows),status='ok'))
        except (ApiError,ValueError,KeyError,TypeError,OSError) as error:
            venue_status.append(dict(address=wallet.get('address'),venue=wallet.get('venue'),
                                     label=wallet.get('label') or wallet.get('venue'),
                                     positions=0,status='error',error=str(error)))
            errors.append(f"{wallet.get('label') or wallet.get('address')}: {error}")
    pending=[]
    for position in positions:
        enrich_expiry(position,category_cache)
        if position.get('resolved'):
            position['sell_now']=resolved_sale(position)
            position['book_source']='resolved'
            position['book_age']=0
        else:
            pending.append(position)

    def quote(position):
        try:
            fee=position_fee(position,config,fee_cache)
            book=(book_fetcher or (lambda p: Client(p['venue']).book(position_market(p),p['outcome'])))(position)
            return position,sell_now(position,fee,book),book,None
        except (ApiError,ValueError,KeyError,TypeError,OSError) as error:
            return position,None,None,error

    with ThreadPoolExecutor(max_workers=4) as pool:
        for position,sale,book,error in pool.map(quote,pending):
            if error:
                position['sell_now']=None
                errors.append(f"{position['title']} {position['outcome']}: {error}")
            else:
                position['sell_now']=sale
                position['book_source']=book.get('transport','rest')
                position['book_age']=max(0,time.time()-book.get('received_at',time.time()))
    total_cost=sum((p['cost'] for p in positions),D(0))
    total_net=sum((p['sell_now']['net_proceeds'] for p in positions if p.get('sell_now')),D(0))
    total_pnl=sum((p['sell_now']['pnl_now'] for p in positions if p.get('sell_now')),D(0))
    realized=sum((p.get('realized_pnl') or D(0) for p in positions),D(0))
    report=dict(
        timestamp=time.time(),mode='read_only_wallet_tracking',currency='USD_stablecoin_nominal',
        wallets=venue_status,positions=positions,errors=errors,
        totals=dict(cost=total_cost,net_now=total_net,pnl_now=total_pnl,realized=realized,
                    positions=len(positions),
                    liquid=sum(1 for p in positions if (p.get('sell_now') or {}).get('fully_sellable')),
                    unsupported_venues=sorted({s['venue'] for s in venue_status if s.get('status')=='error'})))
    temp=output_dir/'tracking.tmp'
    temp.write_text(json.dumps(report,default=str,ensure_ascii=False))
    temp.replace(output_dir/'tracking.json')
    history=output_dir/'tracking-history.jsonl'
    interval=number(config.get('tracking_history_seconds','60')) or D(60)
    last=0
    try:
        if history.exists() and history.stat().st_size:
            with history.open('rb') as handle:
                handle.seek(max(0,history.stat().st_size-4096))
                lines=handle.read().decode('utf-8','replace').strip().splitlines()
            if lines:
                last=json.loads(lines[-1]).get('timestamp',0)
    except (OSError,ValueError):
        last=0
    if positions and time.time()-float(last)>=float(interval):
        entry=dict(timestamp=report['timestamp'],totals={k:str(v) if isinstance(v,D) else v
                                                         for k,v in report['totals'].items()},
                   wallets=[dict(venue=w.get('venue'),label=w.get('label')) for w in wallets])
        with history.open('a') as handle:
            handle.write(json.dumps(entry,ensure_ascii=False)+'\n')
    return report


def csv_rows(report):
    fields=['wallet_label','venue','wallet','title','outcome','shares','avg_price','cost','best_bid',
            'sell_fees','net_now','pnl_now','roi_pct','fully_sellable','realized_pnl','expiry_at','url']
    rows=[]
    for position in report.get('positions',[]):
        sale=position.get('sell_now') or {}
        rows.append(dict(
            wallet_label=position.get('wallet_label'),venue=position.get('venue'),
            wallet=position.get('wallet'),title=position.get('title'),outcome=position.get('outcome'),
            shares=str(position.get('shares')),avg_price=str(position.get('avg_price')),
            cost=str(position.get('cost')),best_bid=str(sale.get('best_bid')),
            sell_fees=str(sale.get('sell_fees')),net_now=str(sale.get('net_proceeds')),
            pnl_now=str(sale.get('pnl_now')),roi_pct=str(sale.get('roi_pct')),
            fully_sellable=sale.get('fully_sellable'),realized_pnl=str(position.get('realized_pnl')),
            expiry_at=(datetime.fromtimestamp(position['expiry_at'],timezone.utc).date().isoformat()
                       if position.get('expiry_at') else ''),
            url=position.get('url')))
    return fields,rows


def main():
    parser=argparse.ArgumentParser(description='Tracking read-only dei wallet su prediction market')
    parser.add_argument('--config',type=Path,default=Path('config.json'))
    parser.add_argument('--output',type=Path,default=Path('output'))
    parser.add_argument('--interval',type=float,default=8)
    parser.add_argument('--once',action='store_true')
    args=parser.parse_args()
    load_env()
    while True:
        load_env()
        config=json.loads(args.config.read_text()) if args.config.exists() else {}
        try:
            report=run_once(config,args.output)
            errors=len(report.get('errors',[]))
            print(f"Tracking · {time.strftime('%H:%M:%S')} · {len(report['positions'])} posizioni · "
                  f"netto ${report['totals']['net_now']:.2f} · errori {errors}",flush=True)
        except (OSError,ValueError,KeyError,TypeError) as error:
            print(f'Tracking error: {error}',flush=True)
        if args.once:
            break
        time.sleep(max(2,args.interval))


if __name__=='__main__':
    main()
