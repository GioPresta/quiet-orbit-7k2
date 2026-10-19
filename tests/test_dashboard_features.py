import json
from pathlib import Path
import tempfile
import threading
import unittest
from urllib.request import Request,urlopen
from urllib.error import HTTPError
from http.server import ThreadingHTTPServer
from arbitraggio.rules import assess_rules
from arbitraggio.exit import evaluate_exit,sell_fee,convergence_exit
from arbitraggio.engine import D,Fee
from arbitraggio.api import parse_book
from arbitraggio.dashboard import make_handler,update_settings
from arbitraggio.scanner import select_matches


class RuleTests(unittest.TestCase):
    def pair(self):
        rules='Resolves Yes if candidate wins. Source: Associated Press. Cancelled event refunds.'
        return [dict(venue=v,id='a',title='Will candidate win?',rules=rules,end='2028-11-08T00:00:00Z') for v in ('polymarket','predict')]
    def test_identical_linked_rules_are_evidence_not_guarantee(self):
        result=assess_rules(self.pair(),{'match_reason':'explicit_platform_link'})
        self.assertEqual(result['status'],'consistent');self.assertFalse(result['guarantee'])
        self.assertTrue(all(c['status']=='pass' for c in result['checks']))
    def test_different_oracle_is_conflict(self):
        pair=self.pair();pair[0]['rules']='Price according to Chainlink';pair[1]['rules']='Price according to Pyth'
        self.assertEqual(assess_rules(pair,{'match_reason':'explicit_platform_link'})['status'],'conflict')
    def test_different_threshold_is_conflict(self):
        pair=self.pair();pair[0]['rules']='FDV above 2 billion';pair[1]['rules']='FDV above 3 billion'
        self.assertEqual(assess_rules(pair)['status'],'conflict')
    def test_missing_rules_require_review(self):
        pair=self.pair();pair[1]['rules']=''
        self.assertEqual(assess_rules(pair)['status'],'review')


class ExitTests(unittest.TestCase):
    def entry(self):
        return dict(total_cost=D(97),allocations=[dict(venue=v,market='a',outcome=s,net_shares=D(100)) for v,s in [('predict','YES'),('polymarket','NO')]])
    def test_bid_depth_and_sell_fees_produce_loss_even_with_full_sale(self):
        fee=Fee('predict',D('.02'),'test')
        result=evaluate_exit(self.entry(),[{'bids':[(D('.39'),D(100))]},{'bids':[(D('.59'),D(100))]}],[fee,fee])
        self.assertTrue(result['fully_sellable']);self.assertFalse(result['profitable_now'])
        self.assertEqual(result['sell_fees'],D('1.60'));self.assertEqual(result['profit'],D('-.60'))
    def test_partial_bid_depth_does_not_claim_full_exit_profit(self):
        fee=Fee('cash_flat',D(0),'test')
        result=evaluate_exit(self.entry(),[{'bids':[(D('.4'),D(50))]},{'bids':[(D('.6'),D(100))]}],[fee,fee])
        self.assertFalse(result['fully_sellable']);self.assertIsNone(result['profit'])
        self.assertEqual(result['legs'][0]['unfilled_shares'],50)
    def test_limitless_sell_fee_uses_collateral_upper_bound(self):
        value,estimated=sell_fee(Fee('shares',D('.03'),'test'),'limitless',D('.5'),D(100))
        self.assertEqual(value,D('.75'));self.assertTrue(estimated)
    def test_no_bid_mirrors_yes_ask_with_correct_units(self):
        result=parse_book('limitless',{'asks':[{'price':'.7','size':'10000000'}],'bids':[]},True)
        self.assertEqual(result['bids'],[(D('.3'),D(10))])


class LiquidityRankingTests(unittest.TestCase):
    def test_rank_uses_weakest_side_volume_not_biggest_platform(self):
        rows=[]
        for ident,volume,profitprice in [('a',(1000000,1),'.01'),('b',(5000,5000),'.02')]:
            rows.extend([dict(venue='polymarket',id=ident,title='Event '+ident,condition_id=ident,fees_enabled=False,
                              volume24h=volume[0],indicative_asks={'YES':{'price':profitprice,'size':1000}}),
                         dict(venue='predict',id=ident,title='Event '+ident,polymarket_condition_ids=[ident],
                              volume24h=volume[1],indicative_asks={'NO':{'price':'.95','size':1000}})])
        matches,_=select_matches(rows,{'max_matches':1,'sort_policy':'liquidity'},D(25))
        self.assertIn('predict:b',matches[0]['markets'])


class LocalServerTests(unittest.TestCase):
    def setUp(self):
        self.directory=tempfile.TemporaryDirectory();self.root=Path(self.directory.name)
        (self.root/'output').mkdir();(self.root/'config.json').write_text(json.dumps({'fees':{'predict_discount':'0'},'budget':'25'}))
        self.server=ThreadingHTTPServer(('127.0.0.1',0),make_handler(self.root,'test-token'))
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.url=f'http://127.0.0.1:{self.server.server_port}'
    def tearDown(self):
        self.server.shutdown();self.server.server_close();self.thread.join();self.directory.cleanup()
    def request(self,payload,token='test-token',origin=None):
        headers={'Content-Type':'application/json','X-Arbitraggio-Token':token}
        if origin:headers['Origin']=origin
        return urlopen(Request(self.url+'/api/settings',data=json.dumps(payload).encode(),headers=headers),timeout=2)
    def test_budget_write_preserves_fee_settings(self):
        with self.request({'budget':'50','sort_policy':'liquidity'}) as response:self.assertEqual(response.status,202)
        config=json.loads((self.root/'config.json').read_text());self.assertEqual(config['budget'],'50');self.assertIn('fees',config)
    def test_token_required_and_foreign_origin_rejected(self):
        for kwargs in ({'token':'wrong'},{'origin':'http://evil.example'}):
            with self.assertRaises(HTTPError) as error:self.request({'budget':'50'},**kwargs)
            self.assertEqual(error.exception.code,403)
    def test_sensitive_and_traversal_paths_are_not_served(self):
        for path in ['/.env','/config.json','/../.env','/output/catalog.json']:
            with self.assertRaises(HTTPError) as error:urlopen(self.url+path,timeout=2)
            self.assertEqual(error.exception.code,404)
    def test_invalid_settings_rejected(self):
        for payload in [{'budget':'NaN'},{'budget':'-1'},{'api_key':'secret'},{'sort_policy':'anything'}]:
            with self.assertRaises(HTTPError) as error:self.request(payload)
            self.assertEqual(error.exception.code,400)

if __name__=='__main__':unittest.main()


class ConvergenceTests(unittest.TestCase):
    def entry(self):
        return dict(total_cost=D(96),profit=D(4),allocations=[
            dict(venue='polymarket',net_shares=D(100),gross_shares=D(100),notional=D(40)),
            dict(venue='predict',net_shares=D(100),gross_shares=D(100),notional=D(56))])

    def test_closure_is_conditional_and_deducts_exit_fees(self):
        r=convergence_exit(self.entry(),[Fee('shares_curve',D('.04'),'test'),Fee('predict',D('.02'),'test')],D('.1'))
        self.assertTrue(r['hypothetical']);self.assertFalse(r['depth_confirmed'])
        self.assertIsNone(r['time_estimate'])
        self.assertEqual(r['prices'],[D('.42'),D('.58')])
        self.assertEqual(r['sell_fees'],D('1.8144'))
        self.assertEqual(r['profit'],D('2.0856'))
        self.assertEqual(r['partial_target_net_proceeds'],98)
        for s in r['scenarios']:self.assertEqual(sum(s['prices']),1)

    def test_fees_can_erase_early_exit_profit(self):
        e=self.entry();e.update(total_cost=D('99.5'),profit=D('.5'))
        r=convergence_exit(e,[Fee('shares_curve',D('.07'),'test'),Fee('predict',D('.02'),'test')])
        self.assertLess(r['profit'],0)

    def test_without_selling_fees_matches_resolution_profit(self):
        r=convergence_exit(self.entry(),[Fee('cash_flat',D(0),'test')]*2)
        self.assertEqual(r['profit'],4)
