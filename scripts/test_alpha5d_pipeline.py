import datetime as dt
import json, tempfile, unittest
from pathlib import Path
from unittest.mock import patch
import alpha5d_pipeline as p
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
if __name__=='__main__':unittest.main()
