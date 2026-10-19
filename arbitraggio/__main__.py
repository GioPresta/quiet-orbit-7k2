import argparse
import csv
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import time
import signal
from .engine import D, Fee, Leg, number, optimize
from .api import VENUES, Client
from .scanner import discover, scan, dumps, json_default, suggestions, tracked_markets, background_discovery


def load_env():
    path = Path('.env')
    if path.exists():
        for line in path.read_text().splitlines():
            if '=' in line and not line.lstrip().startswith('#'):
                k, v = line.split('=', 1)
                if k.strip() in ('OPINION_API_KEY', 'PREDICT_API_KEY', 'LIMITLESS_API_KEY'):
                    key = k.strip(); value = v.strip().strip('\"').strip("'")
                    if not os.environ.get(key):
                        os.environ[key] = value


def export_csv(path, report):
    fields = ['timestamp', 'name', 'budget', 'profit', 'roi_pct', 'total_cost', 'payout', 'max_profitable_cost',
              'venue', 'market', 'outcome', 'gross_shares', 'net_shares', 'notional', 'cash_fee',
              'withheld_shares', 'total', 'limit_price', 'status', 'italy_restriction', 'freshness',
              'resolution_verified', 'fee_estimated']
    if path.exists() and path.stat().st_size:
        with path.open(newline='') as previous:
            if next(csv.reader(previous),[]) != fields:
                path.rename(path.with_name(path.stem+f'.previous-{time.time_ns()}'+path.suffix))
    fresh = not path.exists() or path.stat().st_size == 0
    with path.open('a', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction='ignore')
        if fresh:
            writer.writeheader()
        for result in report['opportunities']:
            for leg in result['allocations']:
                writer.writerow({**report, **result, **leg})


def export_latest_csv(path, report):
    fields=['timestamp','name','profit_usd','roi_pct','total_cost_usd','payout_usd','max_profitable_cost_usd',
            'max_feasible_budget_usd','size_ladder','budget_usd',
            'yes_venue','yes_price_avg','yes_spend','yes_expiry','no_venue','no_price_avg','no_spend','no_expiry',
            'yes_url','no_url','yes_volume_total','yes_volume24h','no_volume_total','no_volume24h',
            'yes_best_ask','yes_best_ask_size','yes_ask_depth_shares','yes_ask_depth_notional',
            'yes_best_bid','yes_bid_depth_shares','yes_bid_depth_notional',
            'no_best_ask','no_best_ask_size','no_ask_depth_shares','no_ask_depth_notional',
            'no_best_bid','no_bid_depth_shares','no_bid_depth_notional',
            'net_shares','status','rules_identical','fee_estimated','italy_restriction',
            'exit_scenario_profit_usd','exit_scenario_roi_pct','exit_scenario_sell_fees_usd','exit_scenario_condition',
            'early_target_profit_usd','early_target_roi_pct','early_target_net_proceeds_usd']
    temp=path.with_suffix('.tmp')
    with temp.open('w',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=fields);writer.writeheader()
        for r in report['opportunities']:
            title=r['name']
            if title.startswith(('=','+','-','@')):title="'"+title
            row=dict(timestamp=report['timestamp'],name=title,budget_usd=r.get('budget'),profit_usd=r['profit'],roi_pct=r['roi_pct'],
                     total_cost_usd=r['total_cost'],payout_usd=r['payout'],max_profitable_cost_usd=r.get('max_profitable_cost'),
                     max_feasible_budget_usd=r.get('max_feasible_budget'),
                     size_ladder=json.dumps(r.get('size_ladder'),ensure_ascii=False,default=str),
                     net_shares=r['payout'],status=r['status'],rules_identical=(r.get('match') or {}).get('rules_identical'),
                     fee_estimated=r.get('fee_estimated'),italy_restriction=r.get('italy_restriction'))
            scenario=r.get('convergence_exit') or {}
            row.update(exit_scenario_profit_usd=scenario.get('profit'),exit_scenario_roi_pct=scenario.get('roi_pct'),
                       exit_scenario_sell_fees_usd=scenario.get('sell_fees'),
                       exit_scenario_condition=scenario.get('condition'),early_target_profit_usd=scenario.get('partial_profit_target'),
                       early_target_roi_pct=(number(scenario['partial_profit_target'])/number(r['total_cost'])*100
                                             if scenario.get('partial_profit_target') is not None and number(r['total_cost']) else None),
                       early_target_net_proceeds_usd=scenario.get('partial_target_net_proceeds'))
            for leg in r['allocations']:
                side=leg['outcome'].lower()
                row[side+'_venue']=leg['venue'];row[side+'_price_avg']=number(leg['notional'])/number(leg['gross_shares'])
                row[side+'_spend']=leg['total']
                expiry=leg.get('expiry_at')
                row[side+'_expiry']=(datetime.fromtimestamp(float(expiry),timezone.utc).date().isoformat()
                                     if expiry is not None else None)
                for key in ('url','volume_total','volume24h','best_ask','best_ask_size',
                            'ask_depth_shares','ask_depth_notional','best_bid',
                            'bid_depth_shares','bid_depth_notional'):
                    row[side+'_'+key]=leg.get(key)
            writer.writerow(row)
    temp.replace(path)


def append_csv_rows(path,fields,rows):
    if path.exists() and path.stat().st_size:
        with path.open(newline='') as previous:
            if next(csv.reader(previous),[])!=fields:
                path.rename(path.with_name(path.stem+f'.previous-{time.time_ns()}'+path.suffix))
    fresh=not path.exists() or path.stat().st_size==0
    with path.open('a',newline='') as handle:
        writer=csv.DictWriter(handle,fieldnames=fields,extrasaction='ignore')
        if fresh:writer.writeheader()
        writer.writerows(rows)


def export_legs_csv(path,report):
    fields=['timestamp','name','match_id','budget_usd','venue','market','outcome','url','price_avg','limit_price',
            'gross_shares','net_shares','total','cash_fee','withheld_shares','fee_model','fee_rate',
            'volume_total','volume24h','best_ask','best_ask_size','ask_depth_shares','ask_depth_notional',
            'best_bid','bid_depth_shares','bid_depth_notional','fills']
    rows=[]
    for result in report.get('opportunities',[]):
        for leg in result.get('allocations',[]):
            rows.append(dict(timestamp=report['timestamp'],name=result.get('name'),budget_usd=result.get('budget'),
                             match_id=(result.get('match') or {}).get('id'),venue=leg.get('venue'),
                             market=leg.get('market'),outcome=leg.get('outcome'),url=leg.get('url'),
                             price_avg=leg.get('price_avg'),limit_price=leg.get('limit_price'),
                             gross_shares=leg.get('gross_shares'),net_shares=leg.get('net_shares'),
                             total=leg.get('total'),cash_fee=leg.get('cash_fee'),
                             withheld_shares=leg.get('withheld_shares'),fee_model=leg.get('fee_model'),
                             fee_rate=leg.get('fee_rate'),volume_total=leg.get('volume_total'),
                             volume24h=leg.get('volume24h'),best_ask=leg.get('best_ask'),
                             best_ask_size=leg.get('best_ask_size'),
                             ask_depth_shares=leg.get('ask_depth_shares'),
                             ask_depth_notional=leg.get('ask_depth_notional'),
                             best_bid=leg.get('best_bid'),bid_depth_shares=leg.get('bid_depth_shares'),
                             bid_depth_notional=leg.get('bid_depth_notional'),
                             fills=json.dumps(leg.get('fills'),ensure_ascii=False,default=str)))
    append_csv_rows(path,fields,rows)


def export_sizes_csv(path,report):
    fields=['timestamp','name','match_id','budget_usd','feasible','total_cost_usd','payout_usd','profit_usd','roi_pct']
    rows=[]
    for result in report.get('opportunities',[]):
        for row in result.get('size_ladder',[]):
            rows.append(dict(timestamp=report['timestamp'],name=result.get('name'),
                             match_id=(result.get('match') or {}).get('id'),budget_usd=row.get('budget'),
                             feasible=row.get('feasible'),total_cost_usd=row.get('total_cost'),
                             payout_usd=row.get('payout'),profit_usd=row.get('profit'),roi_pct=row.get('roi_pct')))
    append_csv_rows(path,fields,rows)


_CATALOG_SIGNATURE=None


def export_catalog_csv(path,catalog):
    global _CATALOG_SIGNATURE
    signature=hash(tuple((m['venue'],str(m['id'])) for m in catalog))
    if path.exists() and signature==_CATALOG_SIGNATURE:return
    fields=['venue','market','title','end','volume_total','volume24h','url']
    temp=path.with_suffix('.tmp')
    with temp.open('w',newline='') as handle:
        writer=csv.DictWriter(handle,fieldnames=fields);writer.writeheader()
        for market in catalog:
            writer.writerow(dict(venue=market.get('venue'),market=market.get('id'),title=market.get('title'),
                                 end=market.get('end'),volume_total=market.get('volume'),
                                 volume24h=market.get('volume24h'),url=market.get('url')))
    temp.replace(path)
    _CATALOG_SIGNATURE=signature


def export_matches_csv(path,matches):
    fields=['match_id','name','markets','score','match_reason','rules_identical','screening_profit']
    temp=path.with_suffix('.tmp')
    with temp.open('w',newline='') as handle:
        writer=csv.DictWriter(handle,fieldnames=fields);writer.writeheader()
        for match in matches:
            writer.writerow(dict(match_id=match.get('id'),name=match.get('name'),
                                 markets=' | '.join(match.get('markets',[])),score=match.get('score'),
                                 match_reason=match.get('match_reason'),
                                 rules_identical=match.get('rules_identical'),
                                 screening_profit=match.get('screening_profit')))
    temp.replace(path)


def display(report):
    print(f"\nScanner · {time.strftime('%H:%M:%S')} · SIMULAZIONE · {len(report['opportunities'])} opportunità", flush=True)
    for v, s in report.get('platforms', {}).items():
        print(f"  {v}: {s.get('reason', str(s.get('markets', 0)) + ' mercati nel catalogo')}")
    print(f"  Coppie selezionate: {len(report.get('matches', []))} su {report.get('coverage',{}).get('candidate_pairs','?')} · feed: {report.get('streams', {})}")
    for r in report['opportunities']:
        print(f"\n{r['name']} [{r.get('status','demo')}] | netto stimato ${r['profit']:.4f} | ROI {r['roi_pct']:.2f}%")
        print(f"  Spesa ${r['total_cost']:.4f} · payout nominale ${r['payout']:.4f} · residuo ${r['unused_budget']:.4f}")
        print(f"  Capacità conveniente attuale ${r.get('max_profitable_cost', r['total_cost']):.4f}")
        if r.get('status')=='candidate_unverified_rules':
            print('  MATCH AUTOMATICO: equivalenza delle regole da verificare.')
        if r.get('fee_estimated'):
            print('  FEE STIMATA: il book aggregato non espone il numero di fill Opinion.')
        for a in r['allocations']:
            average=a['notional']/a['gross_shares']
            fee_pct=a['cash_fee']/a['notional']*100 if a['notional'] else D(0)
            print(f"  Buy {a['outcome']} on {a['venue']}: {a['gross_shares']:.6f} shares × ${average:.6f} "
                  f"(+{fee_pct:.4f}% cash fee) = ${a['total']:.4f}")
            print(f"  {a['venue']:12} {a['outcome']:3} ${a['total']:.4f} → {a['net_shares']:.6f} quote nette "
                  f"(lorde {a['gross_shares']:.6f}, fee cash ${a['cash_fee']:.6f}, trattenute {a['withheld_shares']:.6f})")
        if r.get('italy_restriction'):
            print('  Polymarket: trading limitato dall’Italia; opportunità solo informativa.')
        if r.get('freshness') == 'source_timestamp_missing':
            print('  Timestamp sorgente assente: freschezza non certificata.')
    for item in report.get('errors', []) + report.get('warnings', []):
        print('  ! ' + item)
    for r in report.get('rejected',[])[:5]:
        print(f"  Scartato: {r['name']} · {r['reason']} · minimi {r['min_notional']}")
    if not report['opportunities']:
        print('  Nessuna opportunità positiva nel sottoinsieme monitorato; non è una scansione esaustiva.')


def main():
    signal.signal(signal.SIGINT,signal.default_int_handler)
    parser = argparse.ArgumentParser(description='Arbitraggio: scanner read-only e sizing con fee e profondità')
    parser.add_argument('--budget', default='200', help='Budget totale per hedge, in USD nominali incluse fee')
    parser.add_argument('--budgets', nargs='+', default=None,
                        help='Budget multipli da calcolare, per esempio: 50 100')
    parser.add_argument('--venues', nargs='+', choices=VENUES, default=list(VENUES))
    parser.add_argument('--config', type=Path, default=Path('config.json'))
    parser.add_argument('--catalog', type=Path, default=Path('output/catalog.json'))
    parser.add_argument('--pages', type=int, default=50)
    parser.add_argument('--reuse-catalog', action='store_true', help='Avvia dai metadati salvati, verificando sempre i book live')
    parser.add_argument('--predict-pages', type=int, default=200, help='Pagine Predict da 100 mercati; termina al cursore finale')
    parser.add_argument('--markets', type=int, default=8, help='Mercati automatici per piattaforma (max 8)')
    parser.add_argument('--interval', type=float, default=None, help='Debounce dei feed live in secondi; polling minimo 10')
    parser.add_argument('--poll', action='store_true', help='Disabilita WebSocket e usa polling REST')
    parser.add_argument('--max-age', type=float, default=20)
    parser.add_argument('--max-skew', type=float, default=5)
    parser.add_argument('--min-profit', default='0')
    parser.add_argument('--output', type=Path, default=Path('output'))
    parser.add_argument('--once', action='store_true')
    parser.add_argument('--discover', action='store_true', help='Esporta catalogo e suggerimenti, poi termina')
    parser.add_argument('--demo', action='store_true', help='Esempio sintetico, senza richieste API')
    args = parser.parse_args()
    if args.interval is None:args.interval=10 if args.poll else 1
    load_env()
    live=None
    discovery_future=None
    try:
        budget = number(args.budget)
        minimum = number(args.min_profit)
        if not 1<=args.predict_pages<=500:
            raise ValueError('predict-pages deve essere 1..500')
        if budget <= 0 or minimum < 0 or not 1 <= args.markets <= 8 or not 1 <= args.pages <= 1000:
            raise ValueError('Budget positivo, profitto >=0, markets 1..8 e pages 1..1000 richiesti')
        if not (10 if args.poll else .25) <= args.interval < 86400 or not 0 < args.max_age <= 3600 or not 0 <= args.max_skew <= args.max_age:
            raise ValueError('Intervallo minimo live 0.25s, polling 10s; max-skew <= max-age')
        if args.demo:
            fee = Fee('cash_flat', D('.01'), 'ESEMPIO SINTETICO 1%')
            legs = [Leg('opinion', 'demo-A', 'YES', [(D('.40'), D('150')), (D('.44'), D('200'))], fee),
                    Leg('predict', 'demo-B', 'NO', [(D('.52'), D('100')), (D('.54'), D('250'))], fee)]
            r = optimize(legs, budget, D('.02'))
            report = dict(timestamp=time.time(), opportunities=[dict(r, name='DEMO — dati inventati', status='demo')] if r else [], errors=[], warnings=[])
            display(report)
            return
        config = json.loads(args.config.read_text()) if args.config.exists() else {}
        config['_budget']=budget
        config['_output_dir']=str(args.output)
        args.output.mkdir(parents=True, exist_ok=True)
        args.catalog.parent.mkdir(parents=True, exist_ok=True)
        print('Modalità lettura. Budget USD nominali; matching automatico e feed WebSocket con fallback REST.' if not args.poll and not args.once and not args.discover else 'Modalità REST con matching automatico.', flush=True)
        def progress(venue,pages,markets):
            print(f'Discovery {venue}: {pages} pagine, {markets} mercati',flush=True)
        if args.reuse_catalog and args.catalog.exists():
            saved=json.loads(args.catalog.read_text())
            catalog=[m for m in saved['markets'] if m['venue'] in args.venues]
            statuses={v:s for v,s in saved.get('platforms',{}).items() if v in args.venues}
        else:
            catalog, statuses = discover(args.venues, args.pages, config.get('discovery_queries',[]),args.predict_pages,progress,config)

        from .liquidity import apply_cache
        catalog=apply_cache(catalog)
        if not catalog:
            print(dumps(statuses), file=sys.stderr)
            raise ValueError('Nessun catalogo disponibile; controlla API key e rete')
        args.catalog.write_text(dumps(dict(markets=catalog, platforms=statuses, suggestions=[]))+'\n')
        args.catalog.write_text(dumps(dict(markets=catalog, platforms=statuses, suggestions=suggestions(catalog)))+'\n')
        if args.discover:
            print(f'Catalogo salvato: {args.catalog} ({len(catalog)} mercati). Suggerimenti da verificare prima del sizing cross-platform.')
            print(dumps(statuses))
            return
        if not args.poll and not args.once:
            from .streams import LiveBooks
            live=LiveBooks(tracked_markets(catalog,config,args.markets)).start()
        discovery_future=None
        refreshed = time.monotonic()
        while True:
            load_env()
            if discovery_future is not None and discovery_future.done():
                try:
                    updated, new_statuses=discovery_future.result()
                    if updated:
                        # A transient provider failure must not erase the last
                        # usable catalog for that venue.
                        for venue,state in list(new_statuses.items()):
                            if state.get('status')!='ok':
                                previous=[m for m in catalog if m['venue']==venue]
                                if previous:
                                    updated.extend(previous)
                                    new_statuses[venue]=dict(statuses.get(venue,{}),
                                                             status='ok',markets=len(previous),
                                                             stale_refresh=True,
                                                             refresh_error=state.get('reason'))
                        for venue in list(new_statuses):
                            previous=[m for m in catalog if m['venue']==venue]
                            incoming=[m for m in updated if m['venue']==venue]
                            if previous and len(incoming)<len(previous):
                                merged={ (m['venue'],str(m['id'])):m for m in previous }
                                merged.update({(m['venue'],str(m['id'])):m for m in incoming})
                                updated=[m for m in updated if m['venue']!=venue]+list(merged.values())
                                new_statuses[venue]=dict(new_statuses.get(venue,{}),
                                                         status='ok',markets=len(merged),
                                                         stale_refresh=True)
                        catalog,statuses=updated,new_statuses
                        args.catalog.write_text(dumps(dict(markets=catalog,platforms=statuses,suggestions=suggestions(catalog)))+'\n')
                except (ValueError,OSError,KeyError,TypeError):
                    statuses['discovery_refresh']={'status':'failed'}
                discovery_future=None
                refreshed=time.monotonic()
            if discovery_future is None and time.monotonic()-refreshed>=float(config.get('discovery_interval',300)):
                discovery_future=background_discovery(args.venues,args.pages,config.get('discovery_queries',[]),args.predict_pages,progress,config)
            # Reload settings/budget-independent mapping on every cycle.
            config = json.loads(args.config.read_text()) if args.config.exists() else {}
            configured_budgets=config.get('budgets')
            if isinstance(configured_budgets,list) and configured_budgets:
                budgets=sorted({number(value) for value in configured_budgets})
            elif args.budgets:
                budgets=sorted({number(value) for value in args.budgets})
            else:
                budgets=[number(config.get('budget',args.budget))]
            if not budgets or any(value<=0 for value in budgets):
                raise ValueError('Budget deve essere positivo')
            budget=max(budgets)
            config['_budget']=budget
            config['_output_dir']=str(args.output)
            enabled=config.get('enabled_venues')
            if enabled is None:
                enabled=list(args.venues)
            else:
                enabled=[v for v in args.venues if v in set(enabled)]
            if not enabled:
                raise ValueError('Seleziona almeno una piattaforma')
            active_catalog=[m for m in catalog if m['venue'] in enabled]
            if live:
                selected=tracked_markets(active_catalog,config,args.markets)
                if {(m['venue'],str(m['id'])) for m in selected} != set(live.markets):
                    live.close()
                    live=LiveBooks(selected).start()
                live.changed.clear()
            book_cache={}
            def cached_book(market,outcome):
                key=(market['venue'],str(market['id']),outcome)
                if key not in book_cache:
                    book_cache[key]=live.book(market,outcome) if live else Client(market['venue']).book(market,outcome)
                return book_cache[key]
            reports=[scan(active_catalog,config,value,args.max_age,args.max_skew,minimum,
                          args.markets,cached_book) for value in budgets]
            report=reports[0]
            report['opportunities']=[item for result in reports for item in result.get('opportunities',[])]
            report['rejected']=[item for result in reports for item in result.get('rejected',[])]
            report['budgets']=[str(value) for value in budgets]
            report['budget']=str(budget)
            if config.get('require_rule_consistency',True):
                before=len(report.get('opportunities',[]))
                report['opportunities']=[o for o in report.get('opportunities',[])
                                         if (o.get('rules') or {}).get('status')=='consistent']
                report['rule_filter']=dict(status='consistent',excluded=before-len(report['opportunities']))
            if config.get('require_identical_rules',True):
                before=len(report.get('opportunities',[]))
                report['opportunities']=[o for o in report.get('opportunities',[])
                                         if (o.get('match') or {}).get('rules_identical') is True
                                         or (not o.get('match') and o.get('resolution_verified'))]
                report.setdefault('rule_filter',{})['identical']=True
                report['rule_filter']['identical_excluded']=before-len(report['opportunities'])
            excluded_names=[str(x).lower() for x in config.get('exclude_opportunity_names',[]) if x]
            if excluded_names:
                before=len(report.get('opportunities',[]))
                report['opportunities']=[o for o in report.get('opportunities',[])
                                         if not any(x in str(o.get('name','')).lower() for x in excluded_names)]
                report.setdefault('rule_filter',{})['excluded_names']=excluded_names
                report['rule_filter']['name_excluded']=before-len(report['opportunities'])
            try:
                from .opportunity_tracking import update_ledger
                report['opportunity_tracking']=update_ledger(
                    report,config,args.output,active_catalog,live.book if live else None)
            except (OSError,ValueError,KeyError,TypeError) as error:
                report.setdefault('warnings',[]).append(f'Tracking opportunità: {error}')
            report['streams']=dict(live.status) if live else {'mode':'REST'}
            report['platforms'] = statuses
            report['enabled_venues']=enabled
            report['budget']=budget
            report['settings']={k:config.get(k) for k in
                                ('budget','sort_policy','min_volume24h','max_matches','enabled_venues',
                                 'tracking_min_yield_pct','tracking_target_yield_pct','tax_rate_pct','budgets')}
            report['settings']['enabled_venues']=enabled
            report['settings']['budget']=budget
            report['settings']['budgets']=[str(value) for value in budgets]
            display(report)
            line = dumps(report)+'\n'
            latest = args.output/'latest.json'
            temp = args.output/'latest.tmp'
            temp.write_text(line)
            temp.replace(latest)
            with (args.output/'snapshots.jsonl').open('a') as f:
                f.write(line)
            export_csv(args.output/'opportunities.csv', report)
            export_latest_csv(args.output/'opportunities-latest.csv',report)
            export_legs_csv(args.output/'opportunities-legs.csv',report)
            export_sizes_csv(args.output/'opportunities-sizes.csv',report)
            export_catalog_csv(args.output/'catalog.csv',active_catalog)
            export_matches_csv(args.output/'matches.csv',report.get('matches',[]))
            if args.once:
                break
            if live:
                # React to server events, coalescing bursts to avoid console/API churn.
                live.changed.wait(timeout=10)
                time.sleep(args.interval)
            else:
                time.sleep(max(10,args.interval))
    except (ValueError, OSError, KeyError, TypeError) as e:
        parser.exit(2, f'Errore: {e}\n')
    except KeyboardInterrupt:
        print('\nScanner arrestato.')
    finally:
        if live:live.close()
        if discovery_future:discovery_future.cancel()


if __name__ == '__main__':
    main()
