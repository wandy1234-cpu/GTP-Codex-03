import datetime as dt
import json, tempfile, unittest
from pathlib import Path
from unittest.mock import patch
import alpha5d_pipeline as p
class CacheAndSampleAcceptance(unittest.TestCase):
    def preopen_market(self):
        rows=[];histories={}
        for prefix in ['sh600','sh688','sz000','sz300']:
            for i in range(6):
                symbol=prefix+str(i).zfill(3)
                rows.append(dict(symbol=symbol,name='normal',trade=10.,volume=0,amount=0))
                histories[symbol]={'bars':[dict(date='2026-11-10',close=10.),
                    dict(date='2026-11-11',close=10.+i/10,volume=100.,amount=100000.)]}
        return rows,histories
    def test_zero_auction_volume_cannot_remove_dated_close_samples(self):
        rows,histories=self.preopen_market()
        codes=p.sample_codes(rows,'2026-11-11',histories)
        self.assertEqual(len(set(codes)),20)
        self.assertEqual({p.board(code) for code in codes},{'sh_main','star','sz_main','chinext'})
    def test_stale_or_nontrading_previous_close_cannot_enter_samples(self):
        rows,histories=self.preopen_market()
        histories['sh600000']['bars'][-1]['date']='2026-11-10'
        histories['sz300005']['bars'][-1]['amount']=0
        codes=p.sample_codes(rows,'2026-11-11',histories)
        self.assertEqual(len(codes),20);self.assertNotIn('sh600000',codes);self.assertNotIn('sz300005',codes)
    def cached(self):
        dates=[(dt.date(2026,7,1)+dt.timedelta(days=i)).isoformat() for i in range(61)]
        return {'bars':[dict(date=d,open=10.,high=11.,low=9.,close=10.,volume=100.,amount=100000.) for d in dates],
                'source_url':'https://example.org/real-bars','adjustment_basis':'verified',
                'volume_unit':'lots_100_shares','amount_unit':'CNY'}
    def test_short_or_stale_cache_requires_real_refetch(self):
        h=self.cached();asof=h['bars'][-1]['date']
        self.assertTrue(p.usable_cached_history(h,asof))
        self.assertFalse(p.usable_cached_history({**h,'bars':h['bars'][1:]},asof))
        self.assertFalse(p.usable_cached_history(h,'2026-11-11'))
    def test_cache_cannot_hide_missing_amount_or_duplicate_dates(self):
        h=self.cached();asof=h['bars'][-1]['date'];h['bars'][0].pop('amount')
        self.assertFalse(p.usable_cached_history(h,asof))
        h=self.cached();h['bars'][0]['date']=h['bars'][1]['date']
        self.assertFalse(p.usable_cached_history(h,asof))
    def test_all_twenty_crosschecks_and_one_failure_remain_accounted_for(self):
        codes=['sh'+str(600000+i) for i in range(20)]
        def fetch(code,asof):
            if code==codes[7]:raise ValueError('missing raw sample')
            return {'symbol':code,'asof':asof}
        with patch.object(p,'independent_sample_bars',side_effect=fetch):
            good,errors=p.sample_histories(codes,'2026-11-11')
        self.assertEqual(set(good)|set(errors),set(codes))
        self.assertEqual(len(good),19);self.assertEqual(errors,{codes[7]:'missing raw sample'})

class CalendarTest(unittest.TestCase):
    def test_holiday(self):
        for s in ('2026-10-01','2026-10-02','2026-10-07','2026-10-10','2026-09-25'):
            self.assertFalse(p.trading(dt.date.fromisoformat(s)))
    def test_open(self): self.assertTrue(p.trading(dt.date(2026,10,8)))
    def test_previous(self):self.assertEqual(p.step(dt.date(2026,10,8),-1).isoformat(),'2026-09-30')
    def test_five(self):self.assertEqual(p.forecast_sessions(dt.date(2026,10,9)),['2026-10-09','2026-10-12','2026-10-13','2026-10-14','2026-10-15'])
    def test_unknown_year(self):
        with self.assertRaises(ValueError):p.trading(dt.date(2027,1,4))
    def test_morning_close(self):self.assertEqual(p.previous_close_day(dt.datetime(2026,10,9,9,40,tzinfo=p.TZ)).isoformat(),'2026-10-08')
    def test_after_close(self):self.assertEqual(p.previous_close_day(dt.datetime(2026,10,8,16,0,tzinfo=p.TZ)).isoformat(),'2026-10-08')
class TransportTest(unittest.TestCase):
    def test_pagination(self):
        with patch.object(p,'http',side_effect=[json.dumps([{'symbol':str(i)} for i in range(100)]),json.dumps([{'symbol':'x'}])]):
            rows,c=p.paged('sh_a');self.assertEqual(len(rows),101);self.assertEqual(c['successful_pages'],[1,2]);self.assertTrue(c['terminal_verified'])
    def test_empty_first(self):
        with patch.object(p,'http',return_value='[]'):
            with self.assertRaises(ValueError):p.paged('sh_a')
    def test_page_failure(self):
        with patch.object(p,'http',side_effect=[json.dumps([{'symbol':str(i)} for i in range(100)]),RuntimeError('access')]):
            with self.assertRaises(RuntimeError):p.paged('sh_a')
    def test_duplicate(self):
        with patch.object(p,'http',return_value=json.dumps([{'symbol':'x'},{'symbol':'x'}])):
            with self.assertRaises(ValueError):p.paged('sh_a')
    def test_json_wrapper(self):self.assertEqual(p.json_body('var x={"a":1};'),{'a':1})
    def test_east_fields(self):
        obj={'data':{'code':'600026','klines':['2026-10-08,20,22,23,19,100,200000']}}
        with patch.object(p,'http',return_value=json.dumps(obj)):
            h=p.east_bars('sh600026','2026-10-08');self.assertEqual(h['bars'][0]['amount'],200000);self.assertEqual(h['bars'][0]['close'],22)
    def test_future_bar(self):
        obj={'data':{'code':'600026','klines':['2026-10-09,20,22,23,19,100,200000']}}
        with patch.object(p,'http',return_value=json.dumps(obj)):
            with self.assertRaises(ValueError):p.east_bars('sh600026','2026-10-08')
    def test_identity(self):
        with patch.object(p,'http',return_value=json.dumps({'data':{'code':'600000'}})):
            with self.assertRaises(ValueError):p.east_bars('sh600026','2026-10-08')
    def test_reference_after_refresh(self):
        self.assertEqual(p.reference({'trade':23,'settlement':22},{'stamp':'20261009103000','price':23,'previous':22},'2026-10-08'),(22,22))
    def test_reference_stale(self):
        with self.assertRaises(ValueError):p.reference({'trade':22},{'stamp':'20260930160000','price':22},'2026-10-08')
    def test_reference_close_refresh(self):
        self.assertEqual(p.reference({'trade':22},{'stamp':'20261008160000','price':22},'2026-10-08'),(22,22))
    def test_atomic(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'x.json';p.atomic(path,{'status':'INCOMPLETE'});self.assertEqual(json.loads(path.read_text())['status'],'INCOMPLETE')
class LateClockTest(unittest.TestCase):
    def make_engine(self, errors):
        from types import SimpleNamespace
        e=SimpleNamespace()
        e.validate=lambda data:(errors.copy(),{}, {})
        e.rank=lambda data:{'errors':e.validate(data)[0]}
        return e
    def payload(self):
        return {'run_kind':'late_research','forecast_date':'2026-10-09','cutoff':'2026-10-09T09:00:00+08:00','generated_at':'2026-10-09T09:00:00+08:00'}
    def test_late_retains_other_errors(self):
        e=self.make_engine(['cutoff must be forecast-date 08:50:00 Asia/Shanghai','sample failed'])
        with patch.object(p,'now',return_value=dt.datetime(2026,10,9,10,tzinfo=p.TZ)):
            r=p.run_rank(e,self.payload())
        self.assertEqual(r['errors'],['sample failed']);self.assertFalse(r['is_0850_live_prediction'])
    def test_future_generation_rejected(self):
        e=self.make_engine(['cutoff must be forecast-date 08:50:00 Asia/Shanghai'])
        x=self.payload();x['generated_at']='2026-10-09T12:00:00+08:00'
        with patch.object(p,'now',return_value=dt.datetime(2026,10,9,10,tzinfo=p.TZ)):
            self.assertIn('Invalid late-research actual timestamp',p.run_rank(e,x)['errors'])
    def test_validator_restored(self):
        e=self.make_engine([]);original=e.validate
        with patch.object(p,'now',return_value=dt.datetime(2026,10,9,10,tzinfo=p.TZ)):p.run_rank(e,self.payload())
        self.assertIs(e.validate,original)

class VerifiedSessionHaltTest(unittest.TestCase):
    def test_halt_is_only_excluded_on_verified_previous_session(self):
        for code in ('sh601326','sz301314','sz301468'):
            self.assertIsNotNone(p.verified_halt(code,'2026-10-23'))
            self.assertIsNone(p.verified_halt(code,'2026-10-26'))
        self.assertIsNone(p.verified_halt('sh600000','2026-10-23'))

    def test_halted_symbol_not_selected_for_twenty_daily_crosschecks(self):
        rows = []
        for prefix,begin in (('sh600',100),('sh688',100),('sz000',100),('sz301',310)):
            for i in range(7):
                rows.append({'symbol': prefix+str(begin+i).zfill(3),
                             'name':'exampleco','changepercent':str(i),'trade':10,'volume':100})
        rows[24]['symbol']='sz301399'
        selected=p.sample_codes(rows,'2026-10-23')
        self.assertEqual(len(selected),20)
        self.assertNotIn('sz301314',selected)
        self.assertIn('sz301314',p.sample_codes(rows,'2026-10-26'))

class WarmupBoundaryTest(unittest.TestCase):
    def dates_bars(self):
        dates=[];day=dt.date(2026,11,10)
        while len(dates)<61:
            if p.trading(day):dates.insert(0,day.isoformat())
            day-=dt.timedelta(days=1)
        return dates,[dict(date=d,open=10,high=11,low=9,close=10,volume=100,amount=1000000) for d in dates]
    def test_exact_60_sessions_allowed_only_as_explicit_warmup(self):
        dates,bars=self.dates_bars()
        ipo={'date':dates[1],'sessions':60,'source_url':'https://example.org/ipo'}
        ev=p.verified_60_session_warmup(ipo,bars[1:],'2026-11-10')
        self.assertEqual(ev['observed_bars'],60);self.assertTrue(ev['calendar_reconciled'])
        self.assertEqual(ev['status'],'TECHNICAL_WARMUP_NOT_LISTING_LT_60')
    def test_gap_59_bars_old_stock_or_wrong_listing_date_still_fails(self):
        dates,bars=self.dates_bars();ipo={'date':dates[1],'sessions':60,'source_url':'https://example.org/ipo'}
        self.assertIsNone(p.verified_60_session_warmup(ipo,bars[2:],'2026-11-10'))
        self.assertIsNone(p.verified_60_session_warmup({**ipo,'sessions':61},bars[1:],'2026-11-10'))
        self.assertIsNone(p.verified_60_session_warmup({**ipo,'date':dates[0]},bars[1:],'2026-11-10'))
    def test_bad_amount_is_not_warmup(self):
        dates,bars=self.dates_bars();ipo={'date':dates[1],'sessions':60,'source_url':'https://example.org/ipo'}
        bars[-1]['amount']=-1
        self.assertIsNone(p.verified_60_session_warmup(ipo,bars[1:],'2026-11-10'))

if __name__=='__main__':unittest.main()
