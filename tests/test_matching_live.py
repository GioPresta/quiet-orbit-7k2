import unittest
import time
from unittest.mock import patch
from arbitraggio.engine import D,Fee,Leg,optimize
from arbitraggio.matching import match_markets
from arbitraggio.scanner import scan,fee_for
from arbitraggio.streams import LiveBooks


class MatchingTests(unittest.TestCase):
    def market(self,venue='predict',title='StandX FDV above $2B one day after launch?',**kw):
        return dict(venue=venue,id='1',title=title,**kw)

    def test_identical_title_automatic_unverified(self):
        r=match_markets([self.market(),self.market('opinion')])
        self.assertEqual(len(r),1)
        self.assertFalse(r[0]['resolution_verified'])
        self.assertEqual(r[0]['score'],1)

    def test_explicit_platform_link_survives_different_title(self):
        a=self.market('polymarket',slug='btc-hourly',condition_id='0xabc')
        b=self.market('limitless',title='BTC next hour',external_slug='btc-hourly')
        pairs=match_markets([a,b])
        self.assertEqual(pairs[0]['match_reason'],'explicit_platform_link')
        self.assertFalse(pairs[0]['resolution_verified'])

    def test_predict_condition_link(self):
        a=self.market('polymarket',condition_id='0xabc')
        b=self.market('predict',title='Different displayed wording',polymarket_condition_ids=['0xabc'])
        self.assertEqual(len(match_markets([a,b])),1)

    def test_amount_unit_not_confused(self):
        a=self.market();b=self.market('opinion',a['title'].replace('2B','2M'))
        self.assertEqual(match_markets([a,b]),[])

    def test_threshold_not_confused(self):
        a=self.market();b=self.market('opinion',a['title'].replace('2B','3B'))
        self.assertEqual(match_markets([a,b]),[])

    def test_opposite_direction_not_confused(self):
        a=self.market();b=self.market('opinion',a['title'].replace('above','below'))
        self.assertEqual(match_markets([a,b]),[])

    def test_different_expiry_not_confused(self):
        self.assertEqual(match_markets([self.market(end=1800000000),self.market('opinion',end=1800010000)]),[])

    def test_same_venue_not_matched(self):
        self.assertEqual(match_markets([self.market(),self.market(id2='a')]),[])

    def test_auto_match_sized_without_manual_groups(self):
        catalog=[self.market('polymarket',fees_enabled=False),self.market('limitless')]
        def book(m,side):
            price='.012' if m['venue']=='polymarket' and side=='YES' else '.948' if m['venue']=='limitless' and side=='NO' else '.999'
            return dict(asks=[(D(price),D(1000))],source_time=time.time(),received_at=time.time(),min_order=D(0))
        result=scan(catalog,{},D(25),book_provider=book)
        cross=[r for r in result['opportunities'] if r['kind']=='auto_cross_market']
        self.assertEqual(len(cross),1)
        self.assertEqual(cross[0]['status'],'candidate_unverified_rules')
        self.assertLessEqual(cross[0]['total_cost'],25)
        self.assertEqual({a['venue'] for a in cross[0]['allocations']},{'polymarket','limitless'})


class FeeTests(unittest.TestCase):
    def test_predict_documented_table(self):
        fee=Fee('predict',D('.02'),'docs')
        for p,expected in [('.01','.02'),('.2','.4'),('.5','1'),('.8','.4'),('.99','.02')]:
            self.assertEqual((fee.unit(D(p))[0]-D(p))*100,D(expected))

    def test_discount_not_implicitly_enabled(self):
        market={'venue':'predict','id':'1'}
        self.assertEqual(fee_for(market,{}).rate,D('.02'))
        self.assertEqual(fee_for(market,{'predict_discount':'.10'}).rate,D('.018'))

    def test_opinion_per_level_floor(self):
        fee=Fee('opinion',D('.04'),'docs',False,D('.25'),True)
        a=Leg('opinion','a','YES',[(D('.4'),D(20)),(D('.41'),D(100))],fee,min_notional=D(5))
        b=Leg('predict','b','NO',[(D('.5'),D(120))],Fee('cash_flat',D(0),'test'))
        r=optimize([a,b],D(30))
        self.assertEqual(r['allocations'][0]['cash_fee'],D('.50'))
        self.assertLessEqual(r['total_cost'],30)

    def test_opinion_notional_minimum(self):
        fee=Fee('opinion',D('.04'),'docs',False,D('.25'),True)
        a=Leg('opinion','a','YES',[(D('.012'),D(1000))],fee,min_notional=D(5))
        b=Leg('b','b','NO',[(D('.948'),D(1000))],Fee('cash_flat',D(0),'test'))
        self.assertIsNone(optimize([a,b],25))

    def test_pasted_25_example_violates_predict_minimum(self):
        a=Leg('predict','a','YES',[(D('.012'),D(1000))],Fee('predict',D('.018'),'docs'),min_notional=D(1))
        b=Leg('limitless','b','NO',[(D('.948'),D(1000))],Fee('cash_flat',D('.0003'),'example'))
        self.assertIsNone(optimize([a,b],25))

    def test_floor_crossing_still_can_be_profitable(self):
        a=Leg('opinion','a','YES',[(D('.4'),D(100))],Fee('opinion',D('.04'),'docs',False,D('.25'),True))
        b=Leg('b','b','NO',[(D('.5'),D(100))],Fee('cash_flat',D(0),'test'))
        self.assertGreater(optimize([a,b],10)['profit'],0)


class StreamTests(unittest.TestCase):
    def setUp(self):
        self.m={'venue':'polymarket','id':'1','yes_token':'a','no_token':'b'}
        self.live=LiveBooks([self.m,{'venue':'limitless','id':'slug'}])

    def test_snapshot_delta_and_remove(self):
        self.live.on_poly(dict(event_type='book',asset_id='a',asks=[{'price':'.4','size':'10'}],bids=[],timestamp=str(time.time()*1000)))
        self.live.on_poly(dict(event_type='price_change',price_changes=[dict(asset_id='a',price='.4',size='20',side='SELL')],timestamp=str(time.time()*1000)))
        self.assertEqual(self.live.book(self.m,'YES')['asks'],[(D('.4'),D(20))])
        self.live.on_poly(dict(event_type='price_change',price_changes=[dict(asset_id='a',price='.4',size='0',side='SELL')],timestamp=str(time.time()*1000)))
        self.assertEqual(self.live.book(self.m,'YES')['asks'],[])

    def test_limitless_versions_and_complement(self):
        self.live.put('limitless','slug',{'asks':[],'bids':[{'price':'.6','size':10000000}]},version=10)
        self.live.put('limitless','slug',{'asks':[],'bids':[{'price':'.7','size':10000000}]},version=9)
        self.assertEqual(self.live.cache[('limitless','slug','NO')]['asks'],[(D('.4'),D(10))])
        self.live.invalidate('limitless')
        self.assertNotIn(('limitless','slug','NO'),self.live.cache)

    @patch('arbitraggio.streams.Client')
    def test_rest_fallback_cached_and_labelled(self,client):
        client.return_value.book.return_value={'asks':[],'received_at':time.time()}
        self.assertEqual(self.live.book(self.m,'YES')['transport'],'rest_fallback')
        self.live.book(self.m,'YES')
        self.assertEqual(client.return_value.book.call_count,1)

    @patch('arbitraggio.streams.Client')
    def test_healthy_stream_keeps_unchanged_book_without_rest_storm(self,client):
        self.live.put('polymarket','1',{'asks':[{'price':'.4','size':'20'}],'bids':[],
                                     'timestamp':str((time.time()-100)*1000)},'YES')
        self.live.cache[('polymarket','1','YES')]['received_at']=time.time()-100
        self.live.status['polymarket']='connected'
        self.live.last_message['polymarket']=time.time()
        book=self.live.book(self.m,'YES')
        self.assertEqual(book['freshness_basis'],'synchronized_stream')
        self.assertLess(time.time()-book['checked_at'],1)
        self.assertGreater(time.time()-book['source_time'],90)
        client.assert_not_called()

    @patch('arbitraggio.streams.Client')
    def test_expired_heartbeat_falls_back_to_rest(self,client):
        self.live.put('polymarket','1',{'asks':[],'bids':[]},'YES')
        self.live.cache[('polymarket','1','YES')]['received_at']=time.time()-100
        self.live.status['polymarket']='connected'
        self.live.last_message['polymarket']=time.time()-100
        client.return_value.book.return_value={'asks':[],'received_at':time.time()}
        self.assertEqual(self.live.book(self.m,'YES')['transport'],'rest_fallback')
        client.return_value.book.assert_called_once()

    def test_no_delta_without_snapshot(self):
        self.live.on_poly(dict(event_type='price_change',price_changes=[dict(asset_id='a',price='.4',size='20',side='SELL')]))
        self.assertFalse(self.live.cache)

if __name__=='__main__': unittest.main()
