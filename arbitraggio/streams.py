"""Market-data-only subscriptions. Reconnect invalidates local venue state.

Poly/Predict/Limitless maintain snapshots. Opinion diff events invalidate REST
snapshots rather than applying unsequenced diffs to an unreliable local book.
"""
import json
import os
import threading
import time
from datetime import datetime
from urllib.parse import urlencode
from .api import Client, ApiError, parse_book
from .engine import D


def timestamp(value):
    if value is None:
        return None
    if isinstance(value,str) and 'T' in value:
        return datetime.fromisoformat(value.replace('Z','+00:00')).timestamp()
    return float(value)/1000


class LiveBooks:
    def __init__(self, markets, rest_ttl=10):
        self.markets = {(m['venue'],str(m['id'])):m for m in markets}
        self.cache = {}
        self.raw = {}
        self.lock = threading.RLock()
        self.changed = threading.Event()
        self.stop_event = threading.Event()
        self.rest_ttl = rest_ttl
        self.status = {}
        self.last_message = {}
        self.threads = []
        self.connections = []
        self.token_map = {}
        for m in markets:
            if m['venue']=='polymarket':
                for outcome in ('YES','NO'):
                    self.token_map[str(m[outcome.lower()+'_token'])]=(m['id'],outcome)

    def put(self, venue, market_id, data, outcome=None, event_time=None, version=None, snapshot=True):
        if (venue,str(market_id)) not in self.markets:
            return
        with self.lock:
            rawkey=(venue,str(market_id),outcome)
            old=self.raw.get(rawkey)
            newstamp=event_time if event_time is not None else data.get('updateTimestampMs',data.get('timestamp'))
            oldstamp=old.get('updateTimestampMs',old.get('timestamp')) if old else None
            if newstamp is not None and oldstamp is not None and timestamp(newstamp)<timestamp(oldstamp):
                return
            if version is not None and old and old.get('_version',-1)>version:
                return
            copy=dict(data)
            if event_time is not None:
                copy['timestamp']=str(timestamp(event_time)*1000)
            copy['_version']=version if version is not None else -1
            self.raw[rawkey]=copy
            self.last_message[venue]=time.time()
            for side in ((outcome,) if outcome else ('YES','NO')):
                book=parse_book(venue,copy,side=='NO' and venue in ('predict','limitless'))
                book['transport']='websocket'
                book['checked_at']=book['received_at'] if snapshot else book['source_time']
                book['freshness_basis']='stream_snapshot' if snapshot else 'stream_delta'
                book['stream_lagging']=not snapshot and book['source_time'] is not None and time.time()-book['source_time']>20
                self.cache[(venue,str(market_id),side)]=book
            self.changed.set()

    def invalidate(self, venue, market_id=None):
        with self.lock:
            if market_id is None:self.last_message.pop(venue,None)
            for store in (self.cache,self.raw):
                for key in list(store):
                    if key[0]==venue and (market_id is None or key[1]==str(market_id)):
                        del store[key]
            self.changed.set()

    def book(self, market, outcome):
        key=(market['venue'],str(market['id']),outcome)
        with self.lock:
            book=self.cache.get(key)
            # An ordered live stream + recent server heartbeat confirms unchanged
            # books too. Do not refetch every idle book over REST every 10 seconds.
            if (book and book.get('transport')=='websocket' and not book.get('stream_lagging')
                    and self.status.get(market['venue'])=='connected'
                    and time.time()-self.last_message.get(market['venue'],0)<30):
                confirmed=dict(book)
                confirmed['checked_at']=time.time()
                confirmed['freshness_basis']='synchronized_stream'
                return confirmed
            if book and time.time()-book['received_at']<self.rest_ttl:
                return dict(book)
            # Don't let a late REST response overwrite a newer websocket update.
            request_start=time.time()
        result=Client(market['venue']).book(market,outcome)
        result['transport']='rest_fallback'
        with self.lock:
            newer=self.cache.get(key)
            if newer and newer['received_at']>request_start:
                return dict(newer)
            self.cache[key]=result
        return dict(result)

    def on_poly(self, event):
        if isinstance(event,list):
            for item in event:
                self.on_poly(item)
            return
        kind=event.get('event_type',event.get('type'))
        event=event.get('payload',event)
        if kind=='book':
            token=str(event.get('asset_id',event.get('assetId')))
            if token in self.token_map:
                market_id,outcome=self.token_map[token]
                self.put('polymarket',market_id,event,outcome)
        elif kind=='price_change':
            for update in event.get('price_changes',event.get('priceChanges',[])):
                token=str(update.get('asset_id',update.get('assetId')))
                if token not in self.token_map:
                    continue
                market_id,outcome=self.token_map[token]
                rawkey=('polymarket',str(market_id),outcome)
                with self.lock:
                    old=self.raw.get(rawkey)
                    if old is None:
                        continue  # Cannot apply a delta without a snapshot.
                    side='bids' if update['side']=='BUY' else 'asks'
                    levels={D(str(l['price'])):D(str(l['size'])) for l in old[side]}
                    price,size=D(str(update['price'])),D(str(update['size']))
                    if size==0:
                        levels.pop(price,None)
                    else:
                        levels[price]=size
                    data={**old,side:[dict(price=str(p),size=str(q)) for p,q in levels.items()],
                          'timestamp':event.get('timestamp')}
                    self.put('polymarket',market_id,data,outcome,snapshot=False)
        elif kind in ('market_resolved','tick_size_change'):
            self.invalidate('polymarket')

    def start(self):
        try:
            import websocket
            import socketio
        except ImportError:
            raise ValueError('Installa i feed live: .venv/bin/python -m pip install -r requirements.txt') from None
        for venue in sorted({k[0] for k in self.markets}):
            target=self._limitless if venue=='limitless' else self._websocket
            thread=threading.Thread(target=target,args=(venue,),daemon=True)
            self.threads.append(thread)
            thread.start()
        # Give initial WS snapshots a short window before a large REST fanout.
        ready_deadline=time.monotonic()+5
        while time.monotonic()<ready_deadline and not self.stop_event.is_set():
            with self.lock:
                ready=len(self.cache)>=len(self.markets)*2
            if ready:break
            self.changed.wait(timeout=.1)
            self.changed.clear()
        return self

    def _websocket(self, venue):
        import websocket
        rows=[m for (v,_),m in self.markets.items() if v==venue]
        delay=1
        while not self.stop_event.is_set():
            ws=None
            try:
                headers={}
                if venue=='polymarket':
                    url='wss://ws-subscriptions-clob.polymarket.com/ws/market'
                    requests=[dict(type='market', assets_ids=list(self.token_map))]
                elif venue=='predict':
                    key=os.getenv('PREDICT_API_KEY')
                    if not key:
                        self.status[venue]='missing_api_key';return
                    url='wss://ws.predict.fun/ws'
                    headers={'x-api-key':key}
                    requests=[dict(requestId=i,method='subscribe',params=[f"predictOrderbook/{m['id']}"]) for i,m in enumerate(rows)]
                else:
                    key=os.getenv('OPINION_API_KEY')
                    if not key:
                        # Public REST books remain available; the private websocket needs a key.
                        self.status[venue]='rest_fallback_missing_api_key';return
                    url='wss://ws.opinion.trade?'+urlencode({'apikey':key})
                    requests=[dict(action='SUBSCRIBE',channel='market.depth.diff',marketId=int(m['id'])) for m in rows]
                self.status[venue]='connecting'
                ws=websocket.create_connection(url,header=headers,timeout=8)
                self.connections.append(ws)
                self.invalidate(venue)
                for request in requests:
                    ws.send(json.dumps(request))
                ws.settimeout(1)
                self.status[venue]='connected'
                last_ping=time.monotonic();last_message=time.monotonic();delay=1
                while not self.stop_event.is_set():
                    if time.monotonic()-last_ping>=10:
                        if venue=='polymarket':ws.send('PING')
                        elif venue=='opinion':ws.send(json.dumps({'action':'HEARTBEAT'}))
                        last_ping=time.monotonic()
                    try:
                        message=ws.recv()
                    except websocket.WebSocketTimeoutException:
                        if time.monotonic()-last_message>45:
                            raise ApiError('Stream senza heartbeat')
                        continue
                    if not message:
                        raise ApiError('Stream chiuso')
                    last_message=time.monotonic()
                    self.last_message[venue]=time.time()
                    if message in ('PONG','PING'):
                        if message=='PING':ws.send('PONG')
                        continue
                    data=json.loads(message,parse_float=D)
                    if venue=='polymarket':
                        self.on_poly(data)
                    elif venue=='predict':
                        if data.get('topic')=='heartbeat':
                            ws.send(json.dumps({'method':'heartbeat','data':data['data']}))
                        elif data.get('topic','').startswith('predictOrderbook/'):
                            payload=data['data']
                            self.put(venue,payload['marketId'],payload)
                        elif data.get('type')=='R' and data.get('success') is False:
                            raise ApiError('Sottoscrizione rifiutata')
                    elif data.get('msgType')=='market.depth.diff':
                        self.invalidate(venue,data['marketId'])
            except Exception:
                # Do not expose exception strings: Opinion URL contains API key.
                self.status[venue]='disconnected_retrying_rest_fallback'
                self.invalidate(venue)
            finally:
                if ws:
                    ws.close()
                    if ws in self.connections:self.connections.remove(ws)
            if self.stop_event.wait(delay):break
            delay=min(30,delay*2)

    def _limitless(self, venue):
        import socketio
        slugs=[ident for (v,ident) in self.markets if v==venue]
        delay=1
        while not self.stop_event.is_set():
            sio=socketio.Client(reconnection=False,logger=False,engineio_logger=False)
            @sio.on('connect',namespace='/markets')
            def connected():
                self.invalidate(venue)
                self.status[venue]='connected'
                sio.emit('subscribe_market_prices',{'marketSlugs':slugs},namespace='/markets')
            @sio.on('orderbookUpdate',namespace='/markets')
            def update(data):
                try:
                    self.put(venue,data['marketSlug'],data['orderbook'],event_time=data.get('timestamp'),version=data.get('version'))
                except (ValueError,TypeError,KeyError,ApiError):
                    self.invalidate(venue,data.get('marketSlug'))
            @sio.on('disconnect',namespace='/markets')
            def disconnected(*args):
                self.status[venue]='disconnected_retrying_rest_fallback'
                self.invalidate(venue)
            try:
                self.status[venue]='connecting'
                sio.connect('https://ws.limitless.exchange',transports=['websocket'],namespaces=['/markets'],wait_timeout=10)
                self.connections.append(sio)
                delay=1
                while sio.connected and not self.stop_event.wait(.5):
                    pass
            except Exception:
                self.status[venue]='disconnected_retrying_rest_fallback'
                self.invalidate(venue)
            finally:
                if sio.connected:sio.disconnect()
                if sio in self.connections:self.connections.remove(sio)
            if self.stop_event.wait(delay):break
            delay=min(30,delay*2)

    def close(self):
        self.stop_event.set()
        for conn in list(self.connections):
            try:
                if hasattr(conn,'disconnect'):conn.disconnect()
                else:conn.close()
            except Exception:
                pass
        for thread in self.threads:thread.join(timeout=2)
