import unittest
from unittest.mock import patch
from collections import Counter
from arbitraggio.matching import match_markets
from arbitraggio.scanner import discover


class LinkedDiscoveryTests(unittest.TestCase):
    @patch.dict('os.environ',{'PREDICT_API_KEY':'test-key'})
    def test_predict_pagination_reads_cursor_until_exhausted(self):
        from arbitraggio.api import Client
        calls=[]
        def request(client,path,params=None,base=None):
            calls.append(dict(params))
            client.cursor='next' if len(calls)==1 else None
            return [dict(id=len(calls),question='Question',tradingStatus='OPEN')]
        with patch.object(Client,'request',new=request):
            client=Client('predict');rows=client.discover(200)
        self.assertEqual(len(rows),2)
        self.assertEqual(calls[0]['first'],100)
        self.assertEqual(calls[1]['after'],'next')
        self.assertTrue(client.discovery_info['complete'])
        self.assertEqual(client.discovery_info['pages_read'],2)

    @patch('arbitraggio.scanner.Client')
    def test_predict_condition_fetches_missing_polymarket_market(self, client):
        predict=dict(venue='predict',id='p',title='Event?',polymarket_condition_ids=['0xabc'])
        poly=dict(venue='polymarket',id='a',title='Event?',condition_id='0xabc',slug='event')
        def create(venue):
            from unittest.mock import MagicMock
            obj=MagicMock()
            obj.discover.return_value=[predict] if venue=='predict' else []
            obj.request.return_value=[{'raw':'record'}]
            obj.normalize.return_value=poly
            return obj
        client.side_effect=create
        catalog,status=discover(['predict','polymarket'],1)
        self.assertIn(poly,catalog)
        self.assertEqual(status['polymarket']['markets'],1)
        self.assertEqual(len(match_markets(catalog)),1)

    @patch('arbitraggio.scanner.Client')
    def test_targeted_search_is_added_before_linked_market_discovery(self,client):
        from unittest.mock import MagicMock
        pred=dict(venue='predict',id='932143',title='Will Pete Hegseth win the 2028 US Presidential Election?',polymarket_condition_ids=['0xhegseth'])
        poly=dict(venue='polymarket',id='561264',title=pred['title'],condition_id='0xhegseth')
        def create(venue):
            obj=MagicMock();obj.discover.return_value=[]
            if venue=='predict':
                obj.request.return_value={'markets':[{'id':'932143'}]}
                obj.normalize.return_value=pred
            else:
                obj.request.return_value=[{'id':'561264'}]
                obj.normalize.return_value=poly
            return obj
        client.side_effect=create
        catalog,status=discover(['predict','polymarket'],1,queries=['Pete Hegseth'])
        self.assertEqual({m['id'] for m in catalog},{'932143','561264'})
        self.assertEqual(status['predict']['markets'],1)

    def test_priority_pair_is_monitored_without_marking_rules_verified(self):
        catalog=[]
        for i in range(4):
            for venue in ('polymarket','predict'):
                catalog.append(dict(venue=venue,id=str(i),title=f'Event {i}?'))
        priority=['polymarket:3','predict:3']
        matches=match_markets(catalog,limit=1,priority_pairs=[priority])
        self.assertEqual(matches[0]['markets'],priority)
        self.assertFalse(matches[0]['resolution_verified'])

    def test_unchanged_catalog_reuses_matching_but_changes_invalidate(self):
        from arbitraggio import matching
        catalog=[dict(venue='predict',id='cache-p',title='Cache event above 2 billion?'),
                 dict(venue='polymarket',id='cache-a',title='Cache event above 2 billion?')]
        matching._MATCH_CACHE.clear()
        with patch('arbitraggio.matching._match_markets',wraps=matching._match_markets) as compute:
            self.assertEqual(len(match_markets(catalog)),1)
            self.assertEqual(len(match_markets(catalog)),1)
            self.assertEqual(compute.call_count,1)
            catalog[0]['title']='Cache event above 3 billion?'
            self.assertEqual(match_markets(catalog),[])
            self.assertEqual(compute.call_count,2)

    def test_slug_condition_alias_links_predict_to_limitless(self):
        catalog=[dict(venue='polymarket',id='a',title='Event A',slug='a',condition_id='0xabc'),
                 dict(venue='predict',id='p',title='Alternate event',polymarket_condition_ids=['0xabc']),
                 dict(venue='limitless',id='l',title='Other displayed wording',external_slug='a')]
        pairs=match_markets(catalog)
        venues={tuple(sorted(r.split(':')[0] for r in p['markets'])) for p in pairs}
        self.assertIn(('limitless','predict'),venues)
        self.assertEqual(len(pairs),3)
        self.assertTrue(all(not p['resolution_verified'] for p in pairs))

    def test_match_cap_fair_between_equal_confidence_venue_pairs(self):
        catalog=[]
        for i in range(6):
            for v in ('polymarket','predict','limitless'):
                catalog.append(dict(venue=v,id=str(i),title=f'Event {i} above 2 billion?'))
        pairs=match_markets(catalog,limit=6)
        counts=Counter(tuple(sorted(r.split(':')[0] for r in p['markets'])) for p in pairs)
        self.assertEqual(len(counts),3)
        self.assertEqual(set(counts.values()),{2})

if __name__=='__main__':unittest.main()
