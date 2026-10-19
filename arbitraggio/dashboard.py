"""Loopback-only UI for the local scanner. Never serves workspace files or API keys."""
import argparse
import csv
import io
import json
import os
from pathlib import Path
import re
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from urllib.parse import urlparse
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
from .engine import number
from .tracking import csv_rows
from .opportunity_tracking import ledger_csv

ROOT=Path(__file__).resolve().parent.parent
PUBLIC_SETTINGS={'budget','sort_policy','min_volume24h','max_matches','enabled_venues',
                 'tracking_min_yield_pct','tracking_target_yield_pct','tax_rate_pct','budgets'}
SETTINGS_LOCK=threading.Lock()
ADDRESS_RE=re.compile(r'^0x[0-9a-fA-F]{40}$')
TRACKING_VENUES={'polymarket','predict','limitless','opinion'}
ARBITRAGE_VENUES={'polymarket','predict','limitless','opinion'}
OPINION_AUTH='https://openapi.opinion.trade/openapi/auth/api-key'


def settings(root):
    try:config=json.loads((root/'config.json').read_text())
    except (OSError,ValueError):config={}
    result={k:config.get(k,default) for k,default in
            dict(budget='25',sort_policy='liquidity',min_volume24h='0',max_matches=64,
                 tracking_min_yield_pct='1.5',tracking_target_yield_pct='1.5',
                 tax_rate_pct='0').items()}
    enabled=config.get('enabled_venues')
    result['enabled_venues']=(sorted(enabled) if isinstance(enabled,list) and enabled
                              else sorted(ARBITRAGE_VENUES))
    configured_budgets=config.get('budgets')
    result['budgets']=([str(x) for x in configured_budgets]
                       if isinstance(configured_budgets,list) and configured_budgets
                       else [str(config.get('budget','25'))])
    return result


def update_settings(root,payload):
    if not isinstance(payload,dict) or set(payload)-{
            'budget','sort_policy','min_volume24h','enabled_venues',
            'tracking_min_yield_pct','tracking_target_yield_pct','tax_rate_pct','budgets'}:
        raise ValueError('Impostazioni non consentite')
    values={}
    for key,value in payload.items():
        if key=='sort_policy':
            if value not in ('liquidity','profit'):raise ValueError('Ordinamento non valido')
            values[key]=value
        elif key=='enabled_venues':
            if not isinstance(value,list) or not value or set(value)-ARBITRAGE_VENUES:
                raise ValueError('Seleziona almeno una piattaforma valida')
            values[key]=sorted(set(value))
        elif key=='tracking_min_yield_pct':
            n=number(value)
            if not 0<=n<=1000:raise ValueError('Rendimento minimo fuori intervallo')
            values[key]=str(n)
        elif key=='budgets':
            raw=value if isinstance(value,list) else str(value).split(',')
            parsed=[]
            for item in raw:
                n=number(str(item).strip())
                if not 0<n<=1000000:raise ValueError('Budget multiplo fuori intervallo')
                parsed.append(str(n))
            if not parsed:raise ValueError('Inserisci almeno un budget')
            values[key]=list(dict.fromkeys(parsed))
        elif key in ('tracking_target_yield_pct','tax_rate_pct'):
            n=number(value)
            if not 0<=n<=1000:raise ValueError('Valore percentuale fuori intervallo')
            values[key]=str(n)
        else:
            n=number(value)
            if (key=='budget' and not 0<n<=1000000) or (key=='min_volume24h' and not 0<=n<=1000000000):
                raise ValueError('Importo fuori intervallo')
            values[key]=str(n)
    with SETTINGS_LOCK:
        path=root/'config.json'
        config=json.loads(path.read_text()) if path.exists() else {}
        config.update(values)
        temp=path.with_suffix('.ui.tmp');temp.write_text(json.dumps(config,indent=2)+'\n');temp.replace(path)
    return settings(root)


def wallet_entries(root):
    try:config=json.loads((root/'config.json').read_text())
    except (OSError,ValueError):config={}
    return config.get('tracking_wallets',[]) if isinstance(config.get('tracking_wallets',[]),list) else []


def update_wallets(root,payload):
    if not isinstance(payload,dict) or payload.get('action') not in ('add','remove'):
        raise ValueError('Azione wallet non valida')
    with SETTINGS_LOCK:
        path=root/'config.json'
        config=json.loads(path.read_text()) if path.exists() else {}
        wallets=config.get('tracking_wallets',[])
        if not isinstance(wallets,list):wallets=[]
        if payload['action']=='add':
            venue=str(payload.get('venue','')).lower()
            address=str(payload.get('address','')).strip()
            label=str(payload.get('label','')).strip()[:60]
            if venue not in TRACKING_VENUES or not ADDRESS_RE.fullmatch(address):
                raise ValueError('Piattaforma o indirizzo non valido')
            if len(wallets)>=20:
                raise ValueError('Massimo 20 wallet')
            if any(w.get('venue')==venue and str(w.get('address','')).lower()==address.lower() for w in wallets):
                raise ValueError('Wallet già collegato a questa piattaforma')
            wallets.append(dict(venue=venue,address=address,label=label or venue))
        else:
            venue=str(payload.get('venue','')).lower()
            address=str(payload.get('address','')).strip().lower()
            wallets=[w for w in wallets
                     if not (w.get('venue')==venue and str(w.get('address','')).lower()==address)]
        config['tracking_wallets']=wallets
        temp=path.with_suffix('.wallets.tmp');temp.write_text(json.dumps(config,indent=2)+'\n');temp.replace(path)
    return wallets


def store_env_key(root,key):
    path=root/'.env'
    lines=path.read_text().splitlines() if path.exists() else []
    output=[];found=False
    for line in lines:
        if line.startswith('OPINION_API_KEY='):
            output.append('OPINION_API_KEY='+key);found=True
        else:
            output.append(line)
    if not found:
        output.append('OPINION_API_KEY='+key)
    temp=path.with_suffix('.tmp')
    temp.write_text('\n'.join(output)+'\n')
    os.chmod(temp,0o600)
    temp.replace(path)


def opinion_auth(root,payload):
    if not isinstance(payload,dict) or payload.get('action') not in ('create','get','delete'):
        raise ValueError('Azione Opinion non valida')
    action=payload['action']
    address=str(payload.get('address','')).strip()
    signature=str(payload.get('signature','')).strip()
    timestamp=str(payload.get('timestamp','')).strip()
    if not ADDRESS_RE.fullmatch(address) or not signature or not timestamp.isdigit():
        raise ValueError('Firma Opinion non valida')
    if abs(time.time()-int(timestamp))>300:
        raise ValueError('Firma Opinion scaduta')
    request=Request(OPINION_AUTH,method={'create':'POST','get':'GET','delete':'DELETE'}[action],
                    headers={'OPINION_ADDRESS':address,'OPINION_SIGNATURE':signature,
                             'OPINION_TIMESTAMP':timestamp,'Accept':'application/json'})
    try:
        with urlopen(request,timeout=15) as response:
            data=json.load(response)
    except HTTPError as error:
        raise ValueError(f'Opinion HTTP {error.code}') from None
    except (URLError,TimeoutError,ValueError,OSError):
        raise ValueError('Opinion non raggiungibile') from None
    if data.get('errno')==0:
        key=(data.get('result') or {}).get('apiKey')
        if key:
            store_env_key(root,key)
            os.environ['OPINION_API_KEY']=key
    return {'errno':data.get('errno'),'errmsg':data.get('errmsg',''),'address':address}


def make_handler(root=ROOT,token=None):
    token=token or secrets.token_urlsafe(32)
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,fmt,*args):pass
        def send(self,status,body,content_type='application/json; charset=utf-8',download=None):
            if isinstance(body,(dict,list)):body=json.dumps(body,ensure_ascii=False).encode()
            elif isinstance(body,str):body=body.encode()
            self.send_response(status);self.send_header('Content-Type',content_type)
            self.send_header('Content-Length',str(len(body)));self.send_header('Cache-Control','no-store')
            self.send_header('X-Content-Type-Options','nosniff')
            self.send_header('Content-Security-Policy',"default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'")
            if download:self.send_header('Content-Disposition','attachment; filename="'+download+'"')
            self.end_headers();self.wfile.write(body)
        def allowed_host(self):
            host=self.headers.get('Host','')
            return host in (f'127.0.0.1:{self.server.server_port}',f'localhost:{self.server.server_port}')
        def do_GET(self):
            if not self.allowed_host():return self.send(403,{'error':'Host non consentito'})
            route=urlparse(self.path).path
            if route=='/favicon.ico':return self.send(204,b'','image/x-icon')
            if route=='/api/state':
                try:report=json.loads((root/'output/latest.json').read_text())
                except (OSError,ValueError):report=dict(opportunities=[],timestamp=0,coverage={},platforms={},streams={})
                alive=False
                try:os.kill(int((root/'output/scanner.pid').read_text()),0);alive=True
                except (OSError,ValueError):pass
                report.update(server_time=time.time(),scanner_alive=alive,ui_settings=settings(root),csrf_token=token)
                return self.send(200,report)
            if route=='/download/latest.csv':
                try:return self.send(200,(root/'output/opportunities-latest.csv').read_bytes(),'text/csv; charset=utf-8','opportunita.csv')
                except OSError:return self.send(404,{'error':'CSV non ancora disponibile'})
            if route=='/download/legs.csv':
                try:return self.send(200,(root/'output/opportunities-legs.csv').read_bytes(),'text/csv; charset=utf-8','opportunita-gambe.csv')
                except OSError:return self.send(404,{'error':'CSV gambe non ancora disponibile'})
            if route=='/download/sizes.csv':
                try:return self.send(200,(root/'output/opportunities-sizes.csv').read_bytes(),'text/csv; charset=utf-8','opportunita-dimensioni.csv')
                except OSError:return self.send(404,{'error':'CSV dimensioni non ancora disponibile'})
            if route=='/download/catalog.csv':
                try:return self.send(200,(root/'output/catalog.csv').read_bytes(),'text/csv; charset=utf-8','catalogo-mercati.csv')
                except OSError:return self.send(404,{'error':'CSV catalogo non ancora disponibile'})
            if route=='/download/matches.csv':
                try:return self.send(200,(root/'output/matches.csv').read_bytes(),'text/csv; charset=utf-8','coppie-candidate.csv')
                except OSError:return self.send(404,{'error':'CSV coppie non ancora disponibile'})
            if route=='/api/tracking':
                try:report=json.loads((root/'output/tracking.json').read_text())
                except (OSError,ValueError):
                    report=dict(timestamp=0,positions=[],wallets=[],errors=[],
                                totals=dict(cost=0,net_now=0,pnl_now=0,realized=0,positions=0,liquid=0))
                alive=False
                try:os.kill(int((root/'output/tracker.pid').read_text()),0);alive=True
                except (OSError,ValueError):pass
                report.update(server_time=time.time(),tracker_alive=alive,
                              wallet_settings=wallet_entries(root),csrf_token=token)
                return self.send(200,report)
            if route=='/api/tracking/history':
                entries=[]
                try:
                    lines=(root/'output/tracking-history.jsonl').read_text().splitlines()[-500:]
                    entries=[json.loads(line) for line in lines if line.strip()]
                except (OSError,ValueError):
                    entries=[]
                return self.send(200,{'entries':entries})
            if route=='/download/tracking.csv':
                try:report=json.loads((root/'output/tracking.json').read_text())
                except (OSError,ValueError):report={}
                fields,rows=csv_rows(report)
                buffer=io.StringIO();writer=csv.DictWriter(buffer,fieldnames=fields)
                writer.writeheader();writer.writerows(rows)
                return self.send(200,buffer.getvalue(),'text/csv; charset=utf-8','portafoglio.csv')
            if route=='/download/tracking-history.csv':
                fields=['timestamp','net_now','pnl_now','realized','positions','wallets']
                buffer=io.StringIO();writer=csv.DictWriter(buffer,fieldnames=fields);writer.writeheader()
                try:
                    for line in (root/'output/tracking-history.jsonl').read_text().splitlines():
                        if not line.strip():continue
                        entry=json.loads(line);totals=entry.get('totals') or {}
                        writer.writerow(dict(timestamp=entry.get('timestamp'),net_now=totals.get('net_now'),
                                             pnl_now=totals.get('pnl_now'),realized=totals.get('realized'),
                                             positions=totals.get('positions'),
                                             wallets=','.join(w.get('label','') for w in entry.get('wallets',[]))))
                except (OSError,ValueError):
                    pass
                return self.send(200,buffer.getvalue(),'text/csv; charset=utf-8','storico-portafoglio.csv')
            if route=='/api/opportunity-tracking':
                try:ledger=json.loads((root/'output/opportunity-ledger.json').read_text())
                except (OSError,ValueError):
                    ledger=dict(updated_at=0,episodes=[],stats={})
                alive=False
                try:os.kill(int((root/'output/scanner.pid').read_text()),0);alive=True
                except (OSError,ValueError):pass
                ledger['scanner_alive']=alive
                return self.send(200,ledger)
            if route=='/download/opportunity-tracking.csv':
                try:ledger=json.loads((root/'output/opportunity-ledger.json').read_text())
                except (OSError,ValueError):ledger={}
                fields,rows=ledger_csv(ledger)
                buffer=io.StringIO();writer=csv.DictWriter(buffer,fieldnames=fields)
                writer.writeheader();writer.writerows(rows)
                return self.send(200,buffer.getvalue(),'text/csv; charset=utf-8','opportunita-seguite.csv')
            static={'/':('index.html','text/html; charset=utf-8'),'/app.js':('app.js','text/javascript; charset=utf-8'),
                    '/style.css':('style.css','text/css; charset=utf-8'),
                    '/portfolio.css':('portfolio.css','text/css; charset=utf-8')}
            if route in static:
                name,mime=static[route]
                return self.send(200,(root/'web'/name).read_bytes(),mime)
            return self.send(404,{'error':'Non trovato'})
        def do_POST(self):
            if not self.allowed_host():return self.send(403,{'error':'Host non consentito'})
            origin=self.headers.get('Origin')
            allowed=(f'http://127.0.0.1:{self.server.server_port}',f'http://localhost:{self.server.server_port}')
            if (origin and origin not in allowed) or self.headers.get('X-Arbitraggio-Token')!=token:
                return self.send(403,{'error':'Richiesta non autorizzata'})
            route=urlparse(self.path).path
            if route not in ('/api/settings','/api/wallets','/api/opinion-key'):return self.send(404,{'error':'Non trovato'})
            if self.headers.get_content_type()!='application/json':return self.send(415,{'error':'Usa JSON'})
            try:
                size=int(self.headers.get('Content-Length','0'))
                if not 0<size<=8192:raise ValueError('Richiesta troppo grande')
                payload=json.loads(self.rfile.read(size))
                if route=='/api/settings':
                    result=update_settings(root,payload)
                    return self.send(202,{'settings':result,'message':'Il motore applicherà le impostazioni al prossimo ciclo'})
                if route=='/api/opinion-key':
                    result=opinion_auth(root,payload)
                    return self.send(200,result)
                result=update_wallets(root,payload)
                return self.send(202,{'wallets':result,'message':'Wallet aggiornati; il tracker li leggerà al prossimo ciclo'})
            except (ValueError,TypeError,OSError):return self.send(400,{'error':'Impostazioni non valide'})
    return Handler


def main():
    parser=argparse.ArgumentParser(description='Dashboard locale Arbitraggio')
    parser.add_argument('--port',type=int,default=8765)
    args=parser.parse_args()
    server=ThreadingHTTPServer(('127.0.0.1',args.port),make_handler())
    print(f'Dashboard: http://127.0.0.1:{args.port}',flush=True)
    try:server.serve_forever()
    except KeyboardInterrupt:pass
    finally:server.server_close()

if __name__=='__main__':main()
