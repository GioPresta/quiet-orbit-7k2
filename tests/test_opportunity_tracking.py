import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from arbitraggio.engine import D
from arbitraggio.opportunity_tracking import ledger_csv, update_ledger


def opportunity(roi='2'):
    return dict(name='Test pair',roi_pct=roi,budget='100',allocations=[
        dict(venue='predict',market='p1',outcome='YES',total='40',net_shares='100',url='https://predict.fun/market/x'),
        dict(venue='opinion',market='o1',outcome='NO',total='58',net_shares='100',url='https://app.opinion.trade/')])


class OpportunityTrackingTest(unittest.TestCase):
    def test_tracks_above_threshold_and_closes_when_target_exit_is_reached(self):
        config={'tracking_min_yield_pct':'1.5','tracking_target_yield_pct':'1.5',
                'tax_rate_pct':'0','tracking_snapshot_seconds':'0'}
        with TemporaryDirectory() as folder:
            root=Path(folder)
            low={'bids':[(D('.45'),D('100'))]}
            first=update_ledger({'opportunities':[opportunity()]},config,root,
                                book_provider=lambda market,outcome:low,now=1000)
            self.assertEqual(first['stats']['open'],1)
            high={'bids':[(D('.55'),D('100'))]}
            second=update_ledger({'opportunities':[]},config,root,
                                 book_provider=lambda market,outcome:high,now=4600)
            self.assertEqual(second['stats']['open'],0)
            self.assertEqual(second['stats']['target'],1)
            episode=json.loads((root/'opportunity-ledger.json').read_text())['episodes'][0]
            self.assertEqual(episode['status'],'target')
            self.assertEqual(episode['duration_seconds'],3600)
            self.assertEqual(len((root/'opportunity-tracking.jsonl').read_text().splitlines()),2)

    def test_below_threshold_is_not_registered(self):
        config={'tracking_min_yield_pct':'1.5','tracking_target_yield_pct':'1.5','tracking_snapshot_seconds':'0'}
        with TemporaryDirectory() as folder:
            result=update_ledger({'opportunities':[opportunity('1.0')]},config,Path(folder),now=1000)
            self.assertEqual(result['stats']['tracked'],0)

    def test_csv_contains_duration_and_yield(self):
        config={'tracking_min_yield_pct':'0','tracking_target_yield_pct':'1.5',
                'tax_rate_pct':'0','tracking_snapshot_seconds':'0'}
        with TemporaryDirectory() as folder:
            update_ledger({'opportunities':[opportunity()]},config,Path(folder),
                          book_provider=lambda market,outcome:{'bids':[(D('.45'),D('100'))]},now=1000)
            ledger=json.loads((Path(folder)/'opportunity-ledger.json').read_text())
            fields,rows=ledger_csv(ledger)
            self.assertIn('duration_seconds',fields)
            self.assertEqual(rows[0]['entry_net_roi_pct'],'2')


if __name__=='__main__':
    unittest.main()
