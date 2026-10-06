"""Synthetic unit tests only; these are NOT a market backtest or live predictions."""
import datetime as dt
import tempfile
import unittest
from pathlib import Path
import alpha5d_engine as e


def fixture():
    dates=[(dt.date(2020,1,1)+dt.timedelta(days=i)).isoformat() for i in range(61)]
    # Synthetic dates are explicitly not exchange-calendar evidence.
    rows=[]
    for j in range(24):
        code=('sh' if j<12 else 'sz')+f'{600000+j:06d}'
        bars=[]
        for i,d in enumerate(dates):
            p=10*(1+.001*(j+1))**i
            bars.append(dict(date=d,open=p*.995,high=p*1.01,low=p*.99,close=p,volume=10_000_000,amount=100_000_000))
        rows.append(dict(symbol=code,name='SYNTHETIC',eligible=True,sector='sector'+str(j%3),
                         asof_date=dates[-1],available_at=dates[-1]+'T15:01:00+08:00',amount_unit='CNY',
                         adjustment_basis='synthetic',source_urls=['synthetic://not-live'],bars=bars,raw_close=bars[-1]['close']))
    groups=['sh_main','star','sz_main','chinext']
    samples=[dict(symbol=r['symbol'],board=groups[i%4],security_date=dates[-1],sina_close=r['raw_close'],
                  tencent_close=r['raw_close'],daily_close=r['raw_close'],identity_matches=True,
                  source_urls=['synthetic://not-live']) for i,r in enumerate(rows[:20])]
    d=dict(cutoff='2020-03-02T08:50:00+08:00',forecast_date='2020-03-02',previous_session=dates[-1],
           source_urls=['synthetic://not-live'],data_commit='SYNTHETIC_NOT_MARKET',universe=rows,
           coverage=dict(unique_count=24,samples=samples))
    for node in ['sh_a','sz_a']:
        d['coverage'][node]=dict(successful_pages=[1],missing_pages=[],terminal_page=1,terminal_verified=True,raw_rows=12)
    return d


def cohort_and_outcomes():
    r=e.rank(fixture()); r['forecast_date']='2020-03-02'
    r['sessions']=['2020-03-02','2020-03-03','2020-03-04','2020-03-05','2020-03-06']
    vals={s:dict(reference=100,final=80+i) for i,s in enumerate(r['eligible_codes'])}
    o=dict(forecast_date=r['forecast_date'],sessions=r['sessions'],prices=vals,
           observed_at='2020-03-06T15:01:00+08:00',source_urls=['synthetic://not-live'],
           basis='total_return_previous_close_to_fifth_close')
    return r,o

class Tests(unittest.TestCase):
    def test_full_rank_is_deterministic(self):
        a=e.rank(fixture()); self.assertTrue(a['formal_top5_allowed']); self.assertEqual(len(a['ranking']),24)
        self.assertEqual(a,e.rank(fixture())); self.assertEqual(len(a['top5']),5)
    def test_missing_history_blocks_top5(self):
        d=fixture();d['universe'][0]['bars']=d['universe'][0]['bars'][-20:]
        self.assertFalse(e.rank(d)['formal_top5_allowed']);self.assertEqual(e.rank(d)['top5'],[])
    def test_future_data_blocks_top5(self):
        d=fixture();d['universe'][0]['available_at']='2020-03-02T09:25:00+08:00'
        self.assertFalse(e.rank(d)['formal_top5_allowed'])
    def test_future_catalyst_blocks_top5(self):
        d=fixture();r=d['universe'][0];r.update(catalyst_points=5,catalyst_points_evidence='synthetic',catalyst_points_public_at='2020-03-02T09:00:00+08:00')
        self.assertFalse(e.rank(d)['formal_top5_allowed'])
    def test_missing_page_blocks_top5(self):
        d=fixture();d['coverage']['sh_a']['missing_pages']=[1]
        self.assertFalse(e.rank(d)['formal_top5_allowed'])
    def test_raw_count_mismatch(self):
        d=fixture();d['coverage']['unique_count']=5000
        self.assertFalse(e.rank(d)['formal_top5_allowed'])
    def test_sample_must_match_raw_record(self):
        d=fixture();d['coverage']['samples'][0]['sina_close']=1
        self.assertFalse(e.rank(d)['formal_top5_allowed'])
    def test_missing_outcome_never_drops_loser(self):
        c,o=cohort_and_outcomes();del o['prices'][c['top5'][0]['symbol']]
        r=e.evaluate(c,o);self.assertEqual(r['rank_metrics_state'],'INCOMPLETE')
        self.assertIsNone(r['mean_5d_return']);self.assertNotIn('precision5_top5pct',r)
    def test_no_trade_does_not_change_selection_metrics(self):
        c,o=cohort_and_outcomes();a=e.evaluate(c,o);c['execution_state']='NO TRADE';b=e.evaluate(c,o)
        self.assertEqual(a['selected_returns'],b['selected_returns']);self.assertEqual(a['mean_5d_return'],b['mean_5d_return'])
    def test_maturity_required(self):
        c,o=cohort_and_outcomes();o['observed_at']='2020-03-05T15:30:00+08:00'
        with self.assertRaises(ValueError):e.evaluate(c,o)
    def test_no_rewrite(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'cohort.json';e.freeze(p,{'a':1})
            with self.assertRaises(FileExistsError):e.freeze(p,{'a':2})
    def test_ties_not_probabilities(self):
        self.assertEqual(e.percentiles({'x':4,'y':4}),{'x':.5,'y':.5})
    def test_no_blind_penalty_for_strong_momentum(self):
        r=e.rank(fixture());self.assertEqual(r['top5'][0]['penalty'],0)

if __name__=='__main__':unittest.main()
