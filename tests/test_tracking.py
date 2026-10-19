import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from arbitraggio.dashboard import store_env_key, update_settings, update_wallets
from arbitraggio.engine import D, Fee
from arbitraggio.tracking import parse_limitless, parse_opinion, parse_polymarket, parse_predict, sell_now


WALLET={'venue':'polymarket','address':'0x8a815b830d6ecfb203abd27334ef8d621e2558b0','label':'Test'}


class TrackingParsersTest(unittest.TestCase):
    def test_polymarket_cost_and_resolved_value(self):
        rows=[{'proxyWallet':WALLET['address'],'asset':'123','conditionId':'0xabc',
               'size':850967.2481,'avgPrice':0.001,'initialValue':850.9672,'currentValue':0,
               'cashPnl':-850.9672,'realizedPnl':865.8902,'curPrice':0,'redeemable':True,
               'title':'Test market','slug':'test-market','outcome':'Yes','endDate':'2026-10-05'}]
        position=parse_polymarket(rows,WALLET)[0]
        self.assertEqual(position['shares'],D('850967.2481'))
        self.assertEqual(position['cost'],D('850.9672'))
        self.assertEqual(position['mark_value'],D('0'))
        self.assertEqual(position['outcome'],'YES')
        self.assertEqual(position['realized_pnl'],D('865.8902'))

    def test_limitless_positions_use_six_decimals(self):
        data={'clob':[{'market':{'slug':'india-vs-west-indies','title':'India vs West Indies',
                                  'deadline':'2026-10-18T13:30:00.000Z','yesPositionId':'1','noPositionId':'2'},
                       'tokensBalance':{'yes':'7042760896','no':'0'},
                       'positions':{'yes':{'cost':'4748029184','fillPrice':'674171','marketValue':'4833731700',
                                           'realisedPnl':'0','unrealizedPnl':'5932443'},
                                    'no':{'cost':'0','fillPrice':'0','marketValue':'0','realisedPnl':'0','unrealizedPnl':'0'}}}]}
        position=parse_limitless(data,{'venue':'limitless','address':WALLET['address']})[0]
        self.assertEqual(position['shares'],D('7042.760896'))
        self.assertEqual(position['avg_price'],D('0.674171'))
        self.assertEqual(position['cost'],D('4748.029184'))
        self.assertEqual(position['realized_pnl'],D('0'))

    def test_predict_amount_is_eighteen_decimals(self):
        rows=[{'market':{'id':99,'question':'Test Predict','categorySlug':'cat','feeRateBps':200},
               'outcome':{'name':'Yes','indexSet':1,'onChainId':'42'},
               'amount':'10000000000000000000','valueUsd':'5','averageBuyPriceUsd':'0.4','pnlUsd':'1'}]
        position=parse_predict(rows,{'venue':'predict','address':WALLET['address']})[0]
        self.assertEqual(position['shares'],D('10'))
        self.assertEqual(position['cost'],D('4'))
        self.assertEqual(position['mark_value'],D('5'))
        self.assertEqual(position['url'],'https://predict.fun/market/cat?market=99')

    def test_opinion_position_excludes_frozen_shares(self):
        rows=[{'marketId':7,'marketTitle':'Test Opinion','outcomeSideEnum':'Yes',
               'sharesOwned':'10','sharesFrozen':'2','avgEntryPrice':'0.4',
               'currentValueInQuoteToken':'5','unrealizedPnl':'1',
               'claimStatusEnum':'CanNotClaim','marketStatusEnum':'activated',
               'tokenId':'42','conditionId':'0xabc','marketCutoffAt':1798779540000}]
        position=parse_opinion(rows,{'venue':'opinion','address':WALLET['address']})[0]
        self.assertEqual(position['shares'],D('8'))
        self.assertEqual(position['cost'],D('3.2'))
        self.assertEqual(position['mark_value'],D('4'))
        self.assertEqual(position['token_id'],'42')
        self.assertFalse(position['resolved'])

    def test_instant_sale_uses_bid_depth_and_fee(self):
        position=dict(venue='predict',market_id='1',outcome='YES',shares=D('10'),cost=D('5'),
                      mark_price=D('.6'),redeemable=False)
        book={'bids':[(D('.55'),D('10'))]}
        result=sell_now(position,Fee('cash_flat',D(0),'test'),book)
        self.assertTrue(result['fully_sellable'])
        self.assertEqual(result['net_proceeds'],D('5.5'))
        self.assertEqual(result['pnl_now'],D('.5'))
        self.assertEqual(result['roi_pct'],D('10'))


class WalletSettingsTest(unittest.TestCase):
    def test_add_and_remove_wallet(self):
        with TemporaryDirectory() as folder:
            root=Path(folder)
            (root/'config.json').write_text('{}')
            wallets=update_wallets(root,{'action':'add','venue':'predict',
                                         'address':WALLET['address'],'label':'Principale'})
            self.assertEqual(len(wallets),1)
            wallets=update_wallets(root,{'action':'add','venue':'polymarket',
                                         'address':WALLET['address'],'label':'Poly'})
            self.assertEqual(len(wallets),2)
            wallets=update_wallets(root,{'action':'remove','venue':'predict',
                                         'address':WALLET['address']})
            self.assertEqual(len(wallets),1)
            self.assertEqual(json.loads((root/'config.json').read_text())['tracking_wallets'][0]['venue'],'polymarket')

    def test_opinion_key_is_stored_without_dropping_other_keys(self):
        with TemporaryDirectory() as folder:
            root=Path(folder)
            (root/'.env').write_text('PREDICT_API_KEY=abc\nOPINION_API_KEY=\n')
            store_env_key(root,'opinion-secret')
            content=(root/'.env').read_text()
            self.assertIn('PREDICT_API_KEY=abc',content)
            self.assertIn('OPINION_API_KEY=opinion-secret',content)
            self.assertEqual(oct((root/'.env').stat().st_mode & 0o777),'0o600')

    def test_enabled_venues_are_persisted_and_validated(self):
        with TemporaryDirectory() as folder:
            root=Path(folder)
            (root/'config.json').write_text('{}')
            result=update_settings(root,{'enabled_venues':['predict','opinion']})
            self.assertEqual(result['enabled_venues'],['opinion','predict'])
            with self.assertRaises(ValueError):
                update_settings(root,{'enabled_venues':[]})
            with self.assertRaises(ValueError):
                update_settings(root,{'enabled_venues':['predict','kalshi']})


if __name__=='__main__':
    unittest.main()
