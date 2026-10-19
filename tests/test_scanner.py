import time
import unittest
from unittest.mock import patch
from arbitraggio.api import parse_book, ApiError, Client
from arbitraggio.engine import D
from arbitraggio.scanner import fee_for, scan, suggestions


class AdapterTests(unittest.TestCase):
    def test_limitless_scale_and_complement(self):
        r=parse_book('limitless',{'bids':[{'price':'.7','size':'10000000'}]}, True)
        self.assertEqual(r['asks'],[(D('.3'),D(10))])

    def test_predict_tuples(self):
        r=parse_book('predict',{'asks':[[.4,20]], 'updateTimestampMs':10000})
        self.assertEqual(r['asks'],[(D('.4'),D(20))])
        self.assertEqual(r['source_time'],10)

    def test_invalid_book(self):
        with self.assertRaises(ApiError): parse_book('opinion',{})
        with self.assertRaises(ApiError): parse_book('opinion',{'asks':[['1.2','2']]})

    def test_unknown_fee_never_zero(self):
        f=fee_for({'venue':'opinion','id':'1'}, {})
        self.assertEqual(f.model,'opinion')
        self.assertEqual(f.minimum,D('.25'))
        self.assertTrue(f.estimated)
        f=fee_for({'venue':'limitless','id':'1'}, {})
        self.assertEqual(f.model,'shares')
        self.assertTrue(f.conservative)

    def test_polymarket_metadata(self):
        f=fee_for({'venue':'polymarket','id':'1','fee_schedule':{'rate':'.04','exponent':1}}, {})
        self.assertEqual(f.rate,D('.04'))

    def test_similarity_is_unverified(self):
        rows=[dict(venue='opinion',id='1',title='BTC above 100000?'),dict(venue='predict',id='2',title='BTC above 100000?')]
        self.assertFalse(suggestions(rows)[0]['resolution_verified'])


class ScannerTests(unittest.TestCase):
    def setUp(self):
        self.markets=[dict(venue='polymarket',id='a',title='Example',fees_enabled=False,min_order=0)]

    def book(self, client_market, outcome):
        return dict(asks=[(D('.4') if outcome=='YES' else D('.5'),D(100))],
                    source_time=time.time(),received_at=time.time(),min_order=D(0))

    @patch.object(Client,'book')
    def test_full_scan(self,mock):
        mock.side_effect=self.book
        r=scan(self.markets,{},D('90.02'))
        self.assertEqual(len(r['opportunities']),1)
        o=r['opportunities'][0]
        self.assertTrue(o['italy_restriction'])
        self.assertEqual(o['status'],'simulation')
        self.assertEqual(o['profit'],D('9.98'))

    @patch.object(Client,'book')
    def test_insufficient_full_budget_depth_is_excluded(self,mock):
        mock.side_effect=self.book
        result=scan(self.markets,{},D(100))
        self.assertEqual(result['opportunities'],[])
        self.assertTrue(result['rejected'])

    @patch.object(Client,'book')
    def test_stale_dropped(self,mock):
        r=self.book(None,'YES');r['source_time']=time.time()-100
        mock.return_value=r
        result=scan(self.markets,{},D(100))
        self.assertEqual(result['opportunities'],[])
        self.assertTrue(result['warnings'])

    @patch.object(Client,'book')
    def test_recent_snapshot_of_unchanged_book_is_not_stale(self,mock):
        def snapshot(m,outcome):
            book=self.book(m,outcome)
            book['source_time']=time.time()-1000
            book['checked_at']=time.time()
            return book
        mock.side_effect=snapshot
        result=scan(self.markets,{},D('90.02'))
        self.assertEqual(len(result['opportunities']),1)
        self.assertEqual(result['opportunities'][0]['freshness'],'recent_snapshot_old_last_change')

    @patch.object(Client,'book')
    def test_old_confirmation_still_rejected(self,mock):
        book=self.book(None,'YES')
        book['checked_at']=time.time()-100
        mock.return_value=book
        self.assertEqual(scan(self.markets,{},D(100))['opportunities'],[])

    @patch.object(Client,'book')
    def test_shared_balance(self,mock):
        mock.side_effect=self.book
        r=scan(self.markets,{'balances':{'polymarket':'9.02'}},D(100))
        self.assertEqual(r['opportunities'],[])

    @patch.object(Client,'book')
    def test_unverified_cross_excluded(self,mock):
        mock.side_effect=self.book
        r=scan(self.markets,{'groups':[{'name':'x','markets':['a','b'],'resolution_verified':False}]},D(100))
        self.assertTrue(r['warnings'])
        self.assertTrue(all(o['kind']=='same_market' for o in r['opportunities']))

    @patch.object(Client,'book')
    def test_failure_not_replaced_with_demo(self,mock):
        mock.side_effect=ApiError('offline')
        r=scan(self.markets,{},D(100))
        self.assertEqual(r['opportunities'],[])
        self.assertTrue(r['errors'])

if __name__=='__main__': unittest.main()
