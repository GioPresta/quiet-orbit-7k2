"""Deterministic comparison evidence, never a guarantee of settlement equivalence."""
import hashlib
import re
from .matching import normalize,deadline


ORACLES={'chainlink':'Chainlink','pyth':'Pyth','associated press':'Associated Press','fox news':'Fox News',
         'nbc':'NBC','uma':'UMA','flashscore':'Flashscore','sofascore':'Sofascore','coinmarketcap':'CoinMarketCap',
         'coingecko':'CoinGecko','binance':'Binance'}


def assess_rules(markets,match=None):
    checks=[];conflicts=[];unknown=[]
    titles=[normalize(m.get('title','')) for m in markets]
    rules=[normalize(m.get('rules','')) for m in markets]
    same_market=len({(m['venue'],m['id']) for m in markets})==1
    def add(key,label,status,detail):
        checks.append(dict(key=key,label=label,status=status,detail=detail))
        if status=='conflict':conflicts.append(label)
        elif status=='unknown':unknown.append(label)
    linked=same_market or (match or {}).get('match_reason')=='explicit_platform_link'
    add('mapping','Collegamento tra contratti','pass' if linked else 'unknown',
        'Stesso mercato o riferimento esplicito della piattaforma' if linked else 'Solo somiglianza della domanda')
    add('question','Proposizione e sequenza degli esiti','pass' if titles and all(t==titles[0] and t for t in titles) else 'unknown',
        'Domande normalizzate identiche' if len(set(titles))==1 else 'Domande non identiche: serve confronto umano')
    complete=all(rules)
    identical=complete and len(set(rules))==1
    add('text','Testo delle regole','pass' if identical else 'unknown',
        'Testi normalizzati identici' if identical else 'Testi diversi o mancanti: non certificati')
    if complete and not identical:
        # Dates and thresholds in otherwise similar rules are material, not punctuation.
        numbers=[re.findall(r'\d+(?:\.\d+)?',r) for r in rules]
        if len({tuple(n) for n in numbers})>1:
            add('thresholds','Date, importi e soglie nelle regole','conflict','Sequenze numeriche diverse nei testi')
    oracle_sets=[{label for token,label in ORACLES.items() if token in (m.get('rules','').lower())} for m in markets]
    if all(oracle_sets):
        equal=all(s==oracle_sets[0] for s in oracle_sets)
        add('oracles','Fonti di risoluzione','pass' if equal else 'conflict',
            ' / '.join(', '.join(sorted(s)) for s in oracle_sets))
    else:add('oracles','Fonti di risoluzione','unknown','Fonte non estratta in modo affidabile da tutti i testi')
    ends=[deadline(m.get('end')) for m in markets]
    if all(e is not None for e in ends):
        same=max(ends)-min(ends)<=3600
        add('deadline','Scadenze','pass' if same else 'conflict','Scarto massimo tra scadenze: '+str(round(max(ends)-min(ends)))+' secondi')
    else:add('deadline','Scadenze','unknown','Scadenza strutturata mancante su almeno una piattaforma')
    for token,label in [('cancel','Annullamenti e rimborsi'),('postpon','Rinvii'),('tie','Pareggi')]:
        texts=[m.get('rules','').lower() for m in markets]
        mentions=[any(t in r for t in ({'cancel':('cancel','refund','void'),'postpon':('postpon','reschedul'),'tie':(' tie','draw','tied')}[token])) for r in texts]
        add(token,label,'pass' if identical else 'unknown',
            'Stessa clausola nei testi' if identical and any(mentions) else 'Nessuna differenza testuale rilevata; non è prova che tutti i casi siano coperti' if identical else 'Confronto manuale necessario')
    status='conflict' if conflicts else 'consistent' if identical and linked and len(set(titles))==1 else 'review'
    return dict(status=status,checks=checks,conflicts=conflicts,unknown=unknown,
                guarantee=False,auto_consistent=status=='consistent',
                unresolved='Verifica umana delle clausole, del settlement e dei casi eccezionali sempre necessaria. Nessuna garanzia del 100%.',
                documents=[dict(venue=m['venue'],market=m['id'],title=m.get('title'),end=m.get('end'),url=m.get('url'),
                                rules=m.get('rules',''),sha256=hashlib.sha256(m.get('rules','').encode()).hexdigest()) for m in markets])
