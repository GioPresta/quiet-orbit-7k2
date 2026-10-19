import csv
import json
import tempfile
from pathlib import Path
import unittest
from arbitraggio.__main__ import export_latest_csv
from arbitraggio.engine import D,Fee,Leg,optimize
from arbitraggio.scanner import dumps


class ExportTests(unittest.TestCase):
    def test_latest_csv_supports_saved_decimal_strings_and_replaces_previous_cycle(self):
        fee=Fee('cash_flat',D(0),'test')
        result=optimize([Leg('polymarket','a','YES',[(D('.4'),D(100))],fee),
                         Leg('predict','b','NO',[(D('.5'),D(100))],fee)],D(25))
        result.update(name='Test event',status='candidate_unverified_rules')
        report=json.loads(dumps(dict(timestamp=1,opportunities=[result])))
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'latest.csv'
            export_latest_csv(path,report);export_latest_csv(path,report)
            with path.open() as f:rows=list(csv.DictReader(f))
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]['yes_price_avg'],'0.4')
        self.assertEqual(rows[0]['no_price_avg'],'0.5')
        self.assertEqual(rows[0]['status'],'candidate_unverified_rules')

if __name__=='__main__':unittest.main()
