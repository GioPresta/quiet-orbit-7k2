"""Market-level activity enrichment. Unknown volumes stay unknown, never invented."""
import json
import time
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor,as_completed
from .api import Client,ApiError
from .engine import D,number
from .matching import match_markets,deadline

CACHE=Path('output/liquidity-cache.json')


def read_cache():
    try:return json.loads(CACHE.read_text())
    except (OSError,ValueError):return {}


def apply_cache(catalog):
    cached=read_cache()
    for m in catalog:
        row=cached.get(f"{m['venue']}:{m['id']}")
        if row:
            m.update(volume=row.get('volume'),volume24h=row.get('volume24h'),volume_updated_at=row.get('updated_at'))
            for key in ('url','category_slug'):
                if row.get(key):m[key]=row[key]
    return catalog


def activity(markets):
    values=[number(m['volume24h']) if m.get('volume24h') is not None else None for m in markets]
    known=all(v is not None for v in values)
    return dict(volume24h_min=min(values) if known else None,volume_complete=known,
                volume_by_venue=[dict(venue=m['venue'],market=m['id'],volume24h=v,volume=m.get('volume'),
                                      updated_at=m.get('volume_updated_at')) for m,v in zip(markets,values)])


def enrich(catalog,config,max_markets=260,progress=None):
    from .scanner import indicative_profit
    catalog=apply_cache(catalog)
    active=[m for m in catalog if deadline(m.get('end')) is None or deadline(m['end'])>time.time()]
    mapping={f"{m['venue']}:{m['id']}":m for m in active}
    pairs=match_markets(active,limit=int(config.get('candidate_limit',20000)))
    positive=set();potential=[]
    for pair in pairs:
        profit=indicative_profit(pair,mapping,config,number(config.get('_budget',config.get('budget',25))))
        for ref in pair['markets']:
            m=mapping[ref]
            if m['venue']!='predict':continue
            counterpart=mapping[next(r for r in pair['markets'] if r!=ref)]
            vol=number(counterpart.get('volume24h') or 0)
            potential.append((vol,ref))
            if profit is not None and profit>0:positive.add(ref)
    ordered=sorted(positive)+[r for _,r in sorted(potential,reverse=True) if r not in positive]
    ordered=list(dict.fromkeys(ordered))[:max_markets]
    cache=read_cache();now=time.time()
    targets=[r for r in ordered if now-cache.get(r,{}).get('updated_at',0)>300]
    def fetch(ref):
        m=mapping[ref]
        data=Client('predict').request(f"/v1/markets/{m['id']}/stats")
        row=dict(volume=str(data['volumeTotalUsd']),volume24h=str(data['volume24hUsd']),updated_at=time.time())
        if ref in positive and not m.get('category_slug'):
            try:
                detail=Client('predict').request(f"/v1/markets/{m['id']}")
                detail=detail.get('market',detail)
                if detail.get('categorySlug'):
                    row['category_slug']=detail['categorySlug']
                    row['url']='https://predict.fun/market/'+detail['categorySlug']+'?market='+str(m['id'])
            except (ApiError,KeyError,TypeError):pass
        return ref,row
    done=0
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures=[pool.submit(fetch,r) for r in targets]
        for future in as_completed(futures):
            try:
                ref,row=future.result();cache[ref]=row;done+=1
            except (ApiError,ValueError,KeyError,TypeError):continue
            if progress and (done%50==0 or done==len(targets)):progress('predict-statistiche',done,len(targets))
    CACHE.parent.mkdir(parents=True,exist_ok=True)
    temp=CACHE.with_suffix('.tmp');temp.write_text(json.dumps(cache));temp.replace(CACHE)
    return apply_cache(catalog)
