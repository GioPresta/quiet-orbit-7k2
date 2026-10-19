import unittest
from arbitraggio.engine import D, Fee, Leg, optimize, number


class EngineTests(unittest.TestCase):
    def leg(self, side, levels, fee=None, **kw):
        return Leg(side, 'm', side, [(D(p), D(q)) for p, q in levels],
                   fee or Fee('cash_flat', D(0), 'test'), **kw)

    def test_depth_and_budget(self):
        legs = [self.leg('YES', [('.4','10'),('.45','100')]), self.leg('NO', [('.5','100')])]
        r = optimize(legs, D('18.5'))
        self.assertEqual(r['payout'], D(20))
        self.assertEqual(r['total_cost'], D('18.5'))
        self.assertEqual(r['profit'], D('1.5'))

    def test_stop_at_negative_marginal(self):
        r = optimize([self.leg('YES', [('.4','10'),('.6','100')]), self.leg('NO', [('.5','100')])], 100)
        self.assertEqual(r['payout'], 10)
        self.assertEqual(r['total_cost'], 9)

    def test_withheld_shares_need_more_gross(self):
        a = self.leg('YES', [('.4','100')], Fee('shares', D('.03'), 'test'))
        b = self.leg('NO', [('.5','100')])
        r = optimize([a,b],100)
        self.assertEqual(r['payout'], 97)
        self.assertEqual(r['allocations'][0]['gross_shares'], 100)
        self.assertEqual(r['allocations'][0]['withheld_shares'], 3)
        self.assertEqual(r['allocations'][1]['gross_shares'],97)
        self.assertEqual(r['profit'], D('8.5'))

    def test_fees_erase_edge(self):
        fee = Fee('cash_flat',D('.1'),'test')
        self.assertIsNone(optimize([self.leg('YES',[('.45','100')],fee),self.leg('NO',[('.5','100')],fee)],100))

    def test_curve_fee(self):
        fee = Fee('cash_curve',D('.04'),'test')
        self.assertEqual(fee.unit(D('.5'))[0],D('.51'))

    def test_balances(self):
        r = optimize([self.leg('YES',[('.4','100')], balance=D(4)),self.leg('NO',[('.5','100')])],100)
        self.assertEqual(r['payout'],10)

    def test_fixed_cost_kills_small_trade(self):
        self.assertIsNone(optimize([self.leg('YES',[('.4','1')]),self.leg('NO',[('.5','1')])],10,D('.11')))

    def test_minimum_order(self):
        self.assertIsNone(optimize([self.leg('YES',[('.4','10')],min_order=D(5)),self.leg('NO',[('.5','10')])],1))

    def test_no_depth(self):
        self.assertIsNone(optimize([self.leg('YES',[]),self.leg('NO',[('.5','10')])],100))

    def test_invalid_numbers(self):
        for v in ['NaN','Infinity','-Infinity']:
            with self.assertRaises(ValueError): number(v)

    def test_rounding_never_exceeds_budget(self):
        r=optimize([self.leg('YES',[('.4111','1000')]),self.leg('NO',[('.5222','1000')])],D('100'),D('.02'))
        self.assertLessEqual(r['total_cost'],D(100))
        self.assertEqual(r['payout'] % D('.000001'),0)

if __name__=='__main__': unittest.main()


class FullBudgetTests(unittest.TestCase):
    leg = EngineTests.leg
    def test_rejects_partial_depth_even_if_partial_is_profitable(self):
        legs=[self.leg('YES',[('.4','10')]),self.leg('NO',[('.5','100')])]
        self.assertIsNotNone(optimize(legs,25))
        self.assertIsNone(optimize(legs,25,require_full_budget=True))

    def test_consumes_multiple_ask_levels_with_fees(self):
        legs=[self.leg('YES',[('.4','10'),('.45','100')],Fee('cash_flat',D('.02'),'test')),self.leg('NO',[('.5','100')])]
        r=optimize(legs,D(25),require_full_budget=True)
        self.assertIsNotNone(r)
        self.assertLess(D(25)-r['total_cost'],D('.000002'))
        self.assertEqual(len(r['allocations'][0]['fills']),2)
        self.assertGreater(r['allocations'][0]['cash_fee'],0)

    def test_rejects_balance_limited_budget(self):
        legs=[self.leg('YES',[('.4','100')],balance=D(2)),self.leg('NO',[('.5','100')])]
        self.assertIsNone(optimize(legs,25,require_full_budget=True))

    def test_full_budget_can_differ_from_maximum_profit_partial(self):
        legs=[self.leg('YES',[('.2','10'),('.55','100')]),self.leg('NO',[('.5','100')])]
        r=optimize(legs,D(20),require_full_budget=True)
        self.assertIsNotNone(r)
        self.assertGreater(r['total_cost'],D('19.99999'))
        self.assertGreater(r['profit'],0)


class ShareCurveTests(unittest.TestCase):
    def test_polymarket_curve_fee_is_shares_not_extra_wallet_spend(self):
        fee=Fee('shares_curve',D('.04'),'official test')
        legs=[Leg('polymarket','a','YES',[(D('.5'),D(100))],fee),
              Leg('predict','b','NO',[(D('.4'),D(200))],Fee('cash_flat',D(0),'test'))]
        r=optimize(legs,100)
        self.assertEqual(r['payout'],98)
        a=r['allocations'][0]
        self.assertEqual(a['gross_shares'],100)
        self.assertEqual(a['withheld_shares'],2)
        self.assertEqual(a['withheld_fee_value'],1)
        self.assertEqual(a['cash_fee'],0)
        self.assertEqual(a['total'],50)
        self.assertEqual(r['total_cost'],D('89.2'))

    def test_fee_reduces_depth_and_full_budget_capacity(self):
        legs=[Leg('polymarket','a','YES',[(D('.5'),D(100))],Fee('shares_curve',D('.04'),'test')),
              Leg('predict','b','NO',[(D('.4'),D(200))],Fee('cash_flat',D(0),'test'))]
        self.assertIsNone(optimize(legs,D(90),require_full_budget=True))
        r=optimize(legs,D('89.2'),require_full_budget=True)
        self.assertEqual(r['payout'],98)

    def test_price_levels_have_different_retained_percentages(self):
        fee=Fee('shares_curve',D('.04'),'test')
        leg=Leg('polymarket','a','YES',[(D('.2'),D(100)),(D('.5'),D(100))],fee)
        levels=leg.levels()
        self.assertEqual(levels[0][0],D('96.8'))
        self.assertEqual(levels[1][0],D(98))
        r=optimize([leg,Leg('predict','b','NO',[(D('.1'),D(500))],Fee('cash_flat',D(0),'test'))],100)
        a=r['allocations'][0]
        self.assertEqual(a['withheld_shares'],D('5.2'))
        self.assertEqual(a['withheld_fee_value'],D('1.64'))
        self.assertEqual(a['total'],70)
