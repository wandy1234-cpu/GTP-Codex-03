import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path
import alpha5d_pipeline as p


class IncrementalCacheTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.asof = '2026-10-29'
        self.prior = p.step(dt.date.fromisoformat(self.asof), -1).isoformat()
        dates = [dt.date.fromisoformat(self.prior)]
        for _ in range(60):
            dates.append(p.step(dates[-1], -1))
        self.bars = [{'date': d.isoformat(), 'open': 10., 'close': 10.,
                      'high': 10.2, 'low': 9.8, 'volume': 1000.,
                      'amount': 1000000.} for d in sorted(dates)]
        self.row = {'symbol': 'sh600900', 'settlement': '10.00', 'open': '10.00',
                    'trade': '10.10', 'high': '10.15', 'low': '9.95',
                    'volume': 10000, 'amount': 100500}
        self.path = self.root/'history'/self.prior/'sh600900.json'
        self.path.parent.mkdir(parents=True)
        self.old = {'bars': self.bars, 'source_url': 'https://push2his.eastmoney.com/test',
                    'adjustment_basis': 'Eastmoney_fqt=1_end='+self.prior,
                    'volume_unit': 'lots_100_shares', 'amount_unit': 'CNY'}
        self.path.write_text(json.dumps(self.old), encoding='utf-8')

    def tearDown(self):
        self.temp.cleanup()

    def test_adjacent_session_reused_with_real_unit_conversion(self):
        out = p.incremental_cached_history(self.root, self.asof, self.row)
        self.assertIsNotNone(out)
        self.assertEqual(len(out['bars']), 62)
        self.assertEqual(out['bars'][-1]['date'], self.asof)
        self.assertEqual(out['bars'][-1]['volume'], 100.)
        self.assertEqual(out['bars'][-1]['amount'], 100500.)
        self.assertTrue(out['incremental_guard']['prior_close_matches_sina_settlement'])

    def test_ex_action_or_mismatch_must_fail_closed(self):
        row = dict(self.row, settlement='11.00')
        self.assertIsNone(p.incremental_cached_history(self.root,self.asof,row))

    def test_incorrect_units_or_stale_cache_must_fail_closed(self):
        row = dict(self.row, volume=1)
        self.assertIsNone(p.incremental_cached_history(self.root,self.asof,row))
        modified = dict(self.old)
        modified['bars'] = self.bars[:-1]
        self.path.write_text(json.dumps(modified),encoding='utf-8')
        self.assertIsNone(p.incremental_cached_history(self.root,self.asof,self.row))


if __name__ == '__main__':
    unittest.main()
