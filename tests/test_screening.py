import unittest
from unittest.mock import patch
from arbitraggio.engine import D
from arbitraggio.scanner import select_matches,scan
from arbitraggio.matching import match_markets


def market_pair(ident,yes='.01',no='.95',size='1000'):
    condition='cond-'+ident
    return [dict(venue='polymarket',id=ident,title='Will candidate '+ident+' win?',condition_id=condition,
                 fees_enabled=False,indicative_asks={'YES':{'price':yes,'size':None}}),
            dict(venue='predict',id=ident,title='Will candidate '+ident+' win?',polymarket_condition_ids=[condition],
                 indicative_asks={'NO':{'price':no,'size':size}},min_notional=1)]


class ScreeningTests(unittest.TestCase):
    def test_economic_edge_beats_alphabetical_order(self):
        catalog=market_pair('aaa',yes='.1',no='.95')+market_pair('zzz')
        selected,stats=select_matches(catalog,{'max_matches':1},D(25))
        self.assertEqual(selected[0]['markets'],['polymarket:zzz','predict:zzz'])
        self.assertEqual(stats['screening_positive_pairs'],1)
        self.assertEqual(stats['candidate_pairs'],2)

    def test_screening_over_more_than_old_hundred_pair_cap(self):
        catalog=[m for i in range(150) for m in market_pair(str(i))]
        selected,stats=select_matches(catalog,{'max_matches':64},D(25))
        self.assertEqual(stats['candidate_pairs'],150)
        self.assertEqual(stats['screening_positive_pairs'],150)
        self.assertEqual(len(selected),64)
        self.assertTrue(all(not m['resolution_verified'] for m in selected))

    def test_available_size_affects_economic_rank(self):
        selected,_=select_matches(market_pair('aaa',size='1')+market_pair('zzz',size='1000'),{'max_matches':1},D(25))
        self.assertIn('predict:zzz',selected[0]['markets'])

    def test_indicative_edge_is_not_exported_as_confirmed_book_edge(self):
        import time
        def books(m,outcome):
            return dict(asks=[(D('.6'),D(1000))],received_at=time.time(),source_time=time.time(),min_order=D(0))
        report=scan(market_pair('x'),{'max_matches':1},D(25),book_provider=books)
        self.assertEqual(report['coverage']['screening_positive_pairs'],1)
        self.assertEqual(report['opportunities'],[])

    def test_exploration_rotates_without_evicting_best_profit(self):
        catalog=market_pair('profitable')+market_pair('a','.1','.95')+market_pair('b','.1','.95')+market_pair('c','.1','.95')
        config={'max_matches':2,'exploration_slots':1,'rotation_seconds':60}
        with patch('arbitraggio.scanner.time.time',return_value=120):a,_=select_matches(catalog,config,D(25))
        with patch('arbitraggio.scanner.time.time',return_value=180):b,_=select_matches(catalog,config,D(25))
        self.assertEqual(a[0]['markets'],b[0]['markets'])
        self.assertNotEqual(a[1]['markets'],b[1]['markets'])

    def test_identical_sports_title_does_not_cross_different_fixture_dates(self):
        rows=[dict(venue='polymarket',id='a',title='Aston Villa FC leading at halftime?',
                   rules='Game scheduled October 21, 2026. Wins within first 45 minutes.'),
              dict(venue='predict',id='b',title='Aston Villa FC leading at halftime?',
                   rules='Game scheduled October 17, 2026. Wins within first 45 minutes.')]
        self.assertEqual(match_markets(rows),[])

    def test_fed_sequence_order_not_confused_even_with_identical_rules(self):
        rules='Resolves based on the pattern specified in the question.'
        rows=[dict(venue='polymarket',id='a',title='Will the Fed Hike-Pause-Hike in Oct-Dec-Jan?',rules=rules),
              dict(venue='predict',id='b',title='Will the Fed Hike-Hike-Pause in Oct-Dec-Jan?',rules=rules)]
        self.assertEqual(match_markets(rows),[])

    def test_fuzzy_template_does_not_pair_different_political_candidates(self):
        rows=[dict(venue='polymarket',id='a',title='Will Pete Hegseth win the 2028 US Presidential Election?'),
              dict(venue='predict',id='b',title='Will Pete Buttigieg win the 2028 US Presidential Election?')]
        self.assertEqual(match_markets(rows),[])

if __name__=='__main__':unittest.main()
