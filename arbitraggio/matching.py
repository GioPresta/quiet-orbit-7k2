"""Deterministic cross-venue discovery; confidence is distinct from rule verification."""
import hashlib
import html
import re
import unicodedata
from collections import defaultdict
from datetime import datetime
from difflib import SequenceMatcher


def normalize(text):
    text = html.unescape(re.sub(r'<[^>]*>', ' ', text or ''))
    text = unicodedata.normalize('NFKC', text).lower().replace('’', "'")
    text=text.replace('%',' percent ').replace('>=',' at least ').replace('<=',' at most ').replace('>',' above ').replace('<',' below ').replace("won't",'will not')
    text = re.sub(r'\b([0-9]+(?:\.[0-9]+)?)\s*billion\b', r'\1b', text)
    text = re.sub(r'\b([0-9]+(?:\.[0-9]+)?)\s*million\b', r'\1m', text)
    return ' '.join(re.findall(r'[a-z0-9]+(?:\.[0-9]+)?', text))


def deadline(value):
    if value is None:
        return None
    try:
        if isinstance(value, str) and not re.fullmatch(r'[0-9.]+',value):
            return datetime.fromisoformat(value.replace('Z','+00:00')).timestamp()
        value=float(value)
        return value/1000 if value>1e11 else value
    except (ValueError,TypeError,OverflowError):
        return None


def links(m):
    if m['venue']=='polymarket':
        return {key for key in ('slug:'+str(m.get('slug')) if m.get('slug') else None,
                                'condition:'+str(m.get('condition_id')) if m.get('condition_id') else None) if key}
    keys=set()
    if m.get('external_slug'):keys.add('slug:'+m['external_slug'])
    for condition in m.get('polymarket_condition_ids') or []:
        keys.add('condition:'+str(condition))
    return keys


def _match_markets(catalog, threshold=.88, limit=24, priority_pairs=()):
    pairs, seen = [], set()
    # Resolve slug and condition-ID aliases through the shared Polymarket record,
    # including a direct Predict/Limitless pair when both reference that record.
    aliases={m['slug']:'condition:'+m['condition_id'] for m in catalog
             if m['venue']=='polymarket' and m.get('slug') and m.get('condition_id')}
    def market_links(m):
        keys=links(m)
        return keys | {aliases[key[5:]] for key in keys if key.startswith('slug:') and key[5:] in aliases}

    # Inverted index avoids O(N^2) across unrelated questions.
    content_index = defaultdict(list)
    external_index=defaultdict(list)
    prepared={}
    for m in catalog:
        title=normalize(m['title'])
        prepared[(m['venue'],str(m['id']))]=(title, re.findall(r'\d+(?:\.\d+)?(?:b|m|k)?',title),
                                            set(title.split()), market_links(m), deadline(m.get('end')),
                                            normalize(m.get('rules')))
    generic = {'will','the','a','an','be','by','in','on','at','of','to','or','and','above','below','before','after','price','up','down'}
    for m in catalog:
        title,numbers,words,m_links,e1,rules1=prepared[(m['venue'],str(m['id']))]
        content_key=(tuple(numbers),frozenset(words-generic))
        candidates={(other['venue'],str(other['id'])):other for other in content_index[content_key]}
        for link in market_links(m):
            for other in external_index[link]:
                candidates[(other['venue'],str(other['id']))]=other
        for other in candidates.values():
            if m['venue']==other['venue']:
                continue
            refs = sorted((f"{m['venue']}:{m['id']}", f"{other['venue']}:{other['id']}"))
            key=tuple(refs)
            if key in seen:
                continue
            seen.add(key)
            t2,numbers2,words2,other_links,e2,rules2=prepared[(other['venue'],str(other['id']))]
            linked=bool(m_links&other_links)
            # Order matters (e.g. Fed Hike-Pause-Hike vs Hike-Hike-Pause).
            # Without an explicit platform link, only an identical normalized
            # question is a usable automatic match.
            if not linked and title!=t2:
                continue
            # Numeric thresholds, dates and percentages must agree, even for fuzzy titles.
            if not linked and numbers!=numbers2:
                continue
            if not linked and any((word in words) != (word in words2) for word in
                   ('not','above','below','before','after','over','under','up','down','percent','least','most')):
                continue
            # A fuzzy template must not confuse two named candidates/entities.
            if not linked and (words-generic)!=(words2-generic):
                continue
            score=1.0 if linked else SequenceMatcher(None,title,t2).ratio()
            if score<threshold:
                continue
            if e1 is not None and e2 is not None and abs(e1-e2)>3600:
                continue
            same_rules=bool(rules1 and rules2 and rules1==rules2)
            # Generic sports questions repeat across fixtures; matching titles
            # cannot override different dates/opponents in the resolution rules.
            if not linked and rules1 and rules2 and not same_rules:
                continue
            pairs.append(dict(id=hashlib.sha256('|'.join(refs).encode()).hexdigest()[:16],
                              name=m['title'], markets=refs, score=round(score,4),
                              match_reason='explicit_platform_link' if linked else 'identical_title' if title==t2 else 'similar_title_same_numbers',
                              rules_identical=same_rules, resolution_verified=False,
                              verification_notes='',
                              review_reason='Verificare oracolo, scadenze, annullamenti e significato YES su entrambe le piattaforme'))
        content_index[content_key].append(m)
        for link in market_links(m):
            external_index[link].append(m)
    # Equal-confidence matches must not let one venue pair consume every slot.
    ranked=sorted(pairs,key=lambda p:(-p['score'],p['markets']))
    priority={tuple(sorted(pair)) for pair in priority_pairs}
    selected=[p for p in ranked if tuple(p['markets']) in priority][:limit]
    if len(selected)>=limit:return selected
    ranked=[p for p in ranked if tuple(p['markets']) not in priority]
    for score in sorted({p['score'] for p in ranked},reverse=True):
        buckets=defaultdict(list)
        for pair in ranked:
            if pair['score']==score:
                venues=tuple(sorted(ref.split(':')[0] for ref in pair['markets']))
                buckets[venues].append(pair)
        while any(buckets.values()):
            for venues in sorted(buckets):
                if buckets[venues]:
                    selected.append(buckets[venues].pop(0))
                    if len(selected)>=limit:
                        return selected
    return selected


_MATCH_CACHE = {}


def match_markets(catalog, threshold=.88, limit=24, priority_pairs=()):
    """Reuse matches between ticks; rebuild when catalog or matching settings change."""
    if not .5 <= threshold <= 1 or not 1 <= limit <= 20000:
        raise ValueError('match_threshold deve essere 0.5..1 e max_matches 1..20000')
    signature=tuple((m['venue'],str(m['id']),m['title'],m.get('slug'),m.get('condition_id'),
                     m.get('external_slug'),tuple(m.get('polymarket_condition_ids') or []),
                     str(m.get('end')),m.get('rules')) for m in catalog)
    key=(threshold,limit,signature,tuple(tuple(sorted(pair)) for pair in priority_pairs))
    if key not in _MATCH_CACHE:
        value=_match_markets(catalog,threshold,limit,priority_pairs)
        if len(_MATCH_CACHE)>=4:
            _MATCH_CACHE.pop(next(iter(_MATCH_CACHE)))
        _MATCH_CACHE[key]=value
    return [dict(m) for m in _MATCH_CACHE[key]]
