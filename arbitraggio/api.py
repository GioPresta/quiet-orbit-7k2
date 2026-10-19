"""Read-only REST adapters. No private keys, signatures or trading endpoints."""
import json
import os
import time
import threading
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, quote
from .engine import number, D

VENUES = ('polymarket', 'opinion', 'predict', 'limitless')
KEYS = {'opinion': 'OPINION_API_KEY', 'predict': 'PREDICT_API_KEY', 'limitless': 'LIMITLESS_API_KEY'}
_RATE_LOCK = threading.Lock()
_NEXT_REQUEST = {}

BASE = {'polymarket': 'https://gamma-api.polymarket.com', 'opinion': 'https://openapi.opinion.trade/openapi',
        'predict': 'https://api.predict.fun', 'limitless': 'https://api.limitless.exchange'}


class ApiError(Exception):
    pass


def get(url, headers=None):
    try:
        req = Request(url, headers={'User-Agent': 'Arbitraggio/0.1', **(headers or {})})
        with urlopen(req, timeout=12) as response:
            return json.load(response, parse_float=D)
    except HTTPError as e:
        # Never print response bodies/headers, which can contain credentials.
        raise ApiError(f'HTTP {e.code}' + (': limite richieste' if e.code == 429 else '')) from None
    except (URLError, TimeoutError, ValueError, OSError):
        raise ApiError('Rete non disponibile o risposta JSON non valida') from None


class Client:
    def __init__(self, venue):
        if venue not in VENUES:
            raise ValueError('Piattaforma sconosciuta')
        self.venue = venue
        self.headers = {}
        key = os.getenv(KEYS.get(venue, 'ARBITRAGGIO_UNUSED_KEY'), '')
        if venue == 'predict' and not key:
            raise ApiError(f'Imposta {KEYS[venue]}')
        if key:
            self.headers['apikey' if venue == 'opinion' else 'X-API-Key'] = key

    def request(self, path, params=None, base=None):
        url = (base or BASE[self.venue]) + path
        if params:
            url += '?' + urlencode(params,doseq=True)
        # At most ~3 REST requests/second per venue, shared by discovery and books.
        with _RATE_LOCK:
            current=time.monotonic()
            scheduled=max(current,_NEXT_REQUEST.get(self.venue,0))
            _NEXT_REQUEST[self.venue]=scheduled+.34
        if scheduled>current:
            time.sleep(scheduled-current)
        data = get(url, self.headers)
        if self.venue == 'opinion':
            if data.get('code', data.get('errno', 0)) != 0:
                raise ApiError('Opinion: risposta applicativa non riuscita')
            return data['result']
        if self.venue == 'predict':
            if data.get('success') is not True:
                raise ApiError('Predict: risposta applicativa non riuscita')
            self.cursor=data.get('cursor')
            return data['data']
        return data

    def discover(self, pages=2, progress=None):
        markets = []
        cursor=None
        for page in range(pages):
            if self.venue == 'polymarket':
                rows = self.request('/markets', dict(closed='false', active='true', limit=50,
                                                     offset=page * 50, order='volume24hr', ascending='false'))
            elif self.venue == 'limitless':
                rows = self.request('/markets/active', dict(limit=25, page=page + 1, tradeType='clob'))['data']
            elif self.venue == 'opinion':
                rows = self.request('/market', dict(limit=20, page=page + 1, status='activated', marketType=2))['list']
            else:
                params=dict(first=100, status='OPEN',sort='VOLUME_24H_DESC')
                if cursor:params['after']=cursor
                payload=self.request('/v1/markets',params)
                if isinstance(payload,dict):
                    rows=payload.get('markets',payload.get('data',[]))
                    cursor=payload.get('cursor',payload.get('endCursor'))
                else:
                    rows=payload
                    cursor=getattr(self,'cursor',None)
            expanded=[]
            for row in rows:
                children=row.get('childMarkets',row.get('markets',[]))
                if children and self.venue in ('opinion','limitless'):
                    for child in children:
                        expanded.append({**child, 'parent_title':row.get('marketTitle',row.get('title',''))})
                else:
                    expanded.append(row)
            rows=expanded
            if not rows:
                break
            for row in rows:
                m = self.normalize(row)
                if m:
                    markets.append(m)
            if progress and (page==0 or (page+1)%10==0 or (self.venue=='predict' and not cursor)):
                progress(self.venue,page+1,len(markets))
            if self.venue=='predict' and not cursor:
                break
        self.discovery_info={'pages_read':page+1, 'complete':not bool(cursor)} if self.venue=='predict' else {'pages_read':page+1}
        return markets

    def normalize(self, row):
        v = self.venue
        def array(val):
            return json.loads(val) if isinstance(val, str) else val
        if v == 'polymarket':
            tokens = array(row.get('clobTokenIds', []))
            labels = array(row.get('outcomes', []))
            if len(tokens) != 2 or row.get('closed') or not row.get('acceptingOrders'):
                return None
            yes = next((i for i, l in enumerate(labels) if l.lower() in ('yes', 'up')), None)
            if yes is None:
                return None
            return dict(venue=v, id=str(row['id']), title=row['question'],
                        slug=row['slug'], condition_id=row.get('conditionId'),
                        yes_token=tokens[yes], no_token=tokens[1-yes], rules=row.get('description', ''),
                        end=row.get('endDate'), volume=row.get('volume'), volume24h=row.get('volume24hr'),
                        min_order=row.get('orderMinSize', 0), fee_schedule=row.get('feeSchedule'),
                        fees_enabled=row.get('feesEnabled'),
                        indicative_asks={'YES':{'price':row.get('bestAsk'),'size':None},
                                         'NO':{'price':1-number(row['bestBid']) if row.get('bestBid') is not None else None,'size':None}}, url='https://polymarket.com/event/' + row['slug'])
        if v == 'limitless':
            if row.get('tradeType') != 'clob' or row.get('expired'):
                return None
            return dict(venue=v, id=row['slug'], title=row['title'], rules=row.get('description', ''),
                        end=row.get('expirationTimestamp'), volume=row.get('volumeFormatted'),
                        external_slug=row.get('metadata',{}).get('externalSlug'),
                        volume24h=None, min_order=0, url='https://limitless.exchange/markets/' + row['slug'])
        if v == 'opinion':
            if not row.get('yesTokenId') or not row.get('noTokenId') or row.get('status') != 2:
                return None
            return dict(venue=v, id=str(row['marketId']), title=row['marketTitle'],
                        yes_token=row['yesTokenId'], no_token=row['noTokenId'],
                        rules=row.get('rules', ''), end=row.get('cutoffAt'), volume=row.get('volume'),
                        volume24h=row.get('volume24h'), min_order=0, min_notional=5,
                        yes_label=row.get('yesLabel'), no_label=row.get('noLabel'))
        if row.get('tradingStatus') != 'OPEN':
            return None
        return dict(venue=v, id=str(row['id']), title=row.get('question') or row.get('title') or str(row['id']),
                    rules=row.get('description', ''), end=row.get('endsAt'), volume=(row.get('stats') or {}).get('volumeTotalUsd'),
                    volume24h=(row.get('stats') or {}).get('volume24hUsd'), min_order=0, min_notional=1,
                    fee_rate_bps=row.get('feeRateBps',200),
                    polymarket_condition_ids=row.get('polymarketConditionIds',[]),
                    indicative_asks={('YES' if o.get('indexSet')==1 else 'NO'):o.get('bestAsk')
                                     for o in row.get('outcomes',[]) if o.get('indexSet') in (1,2)},
                    category_slug=row.get('categorySlug'),
                    url='https://predict.fun/market/'+str(row.get('categorySlug') or row.get('slug') or row['id'])+'?market='+str(row['id']))

    def book(self, market, outcome):
        v = self.venue
        invert = outcome == 'NO' and v in ('predict', 'limitless')
        if v == 'polymarket':
            data = self.request('/book', {'token_id': market[outcome.lower() + '_token']},
                                base='https://clob.polymarket.com')
        elif v == 'opinion':
            data = self.request('/token/orderbook', {'token_id': market[outcome.lower() + '_token']})
        else:
            ident = quote(str(market['id']), safe='')
            prefix = '/v1' if v == 'predict' else ''
            data = self.request(f'{prefix}/markets/{ident}/orderbook')
        book=parse_book(v,data,invert)
        book['checked_at']=book['received_at']
        book['freshness_basis']='rest_snapshot'
        return book


def parse_book(venue, data, invert=False):
    # Limitless raw token size is 1e6 units/share; other adapters use shares.
    scale = D('1000000') if venue == 'limitless' else D('1')
    levels = []
    key = 'bids' if invert else 'asks'
    if key not in data:
        raise ApiError('Schema order book inatteso')
    for item in data[key]:
        p, q = (item['price'], item['size']) if isinstance(item, dict) else item
        p, q = number(p), number(q) / scale
        if invert:
            p = 1-p
        if not 0 < p < 1 or q < 0:
            raise ApiError('Prezzo o quantità order book non valido')
        if q:
            levels.append((p, q))
    stamp = data.get('updateTimestampMs', data.get('timestamp'))
    # Keep source timestamp separate from local receipt; missing != fresh source.
    stamp = float(stamp) / 1000 if stamp is not None else None
    bid_levels=[]
    bid_key='asks' if invert else 'bids'
    for item in data.get(bid_key,[]):
        p,q=(item['price'],item['size']) if isinstance(item,dict) else item
        p,q=number(p),number(q)/scale
        if invert:p=1-p
        if not 0<p<1 or q<0:raise ApiError('Bid non valido')
        if q:bid_levels.append((p,q))
    return dict(asks=sorted(levels), bids=sorted(bid_levels,reverse=True), source_time=stamp, received_at=time.time(),
                min_order=number(data.get('min_order_size', 0)))
