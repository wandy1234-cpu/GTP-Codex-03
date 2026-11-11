"""Captured public provider/Actions evidence plus explicit failure injection.

The two fixture files contain real observed bars, not manufactured IPO history.
No tests here certify a production ranking or investment performance.
"""
import copy
import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import alpha5d_pipeline as p
import alpha5d_provider_resilience as r

FIXTURES=Path(__file__).parent/'fixtures'/'alpha5d_history_20261111'

def captured(symbol='sh688828'):
    return json.loads((FIXTURES/(symbol+'.json')).read_text())

def sina_from_capture(data,asof='2026-11-10'):
    with patch.object(p,'http',side_effect=lambda url:data[
            'raw_sina_qfq' if url.endswith('qfq.js') else 'raw_sina_klc2']['body']):
        return r.sina_history(data['symbol'],asof)


class CapturedIpoEvidence(unittest.TestCase):
    def test_two_archived_histories_cover_every_exchange_session(self):
        for symbol in ('sh688828','sz301717'):
            data=captured(symbol)
            self.assertEqual(data['archived_bars'],data['live_primary']['bars'])
            dates=[b['date'] for b in data['archived_bars']]
            start=dt.date(2026,8,11);end=dt.date(2026,11,10)
            expected=[(start+dt.timedelta(days=i)).isoformat()
                      for i in range((end-start).days+1) if p.trading(start+dt.timedelta(days=i))]
            self.assertEqual(dates,expected);self.assertEqual(len(dates),60)

    def test_real_sina_factors_with_comment_decode_both_histories(self):
        for symbol in ('sh688828','sz301717'):
            data=captured(symbol);h=sina_from_capture(data)
            self.assertEqual(len(h['bars']),60)
            self.assertEqual([b['date'] for b in h['bars']],[b['date'] for b in data['archived_bars']])
            for actual,expected in zip(h['bars'],data['archived_bars']):
                for key in ('open','close','high','low'):
                    self.assertAlmostEqual(actual[key],expected[key],places=2)
                # Providers publish volume/amount at different precisions.
                self.assertLess(abs(actual['amount']/expected['amount']-1),.001)
                self.assertLess(abs(actual['volume']/expected['volume']-1),.001)

    def test_59_60_61_actual_bars_keep_original_eligibility_and_gate(self):
        for symbol in ('sh688828','sz301717'):
            data=captured(symbol)
            for asof,count in [('2026-11-09',59),('2026-11-10',60),('2026-11-11',61)]:
                h=sina_from_capture(data,asof);self.assertEqual(len(h['bars']),count)
                forecast=p.step(dt.date.fromisoformat(asof),1).isoformat()
                row=dict(symbol=symbol,name=data['name'],trade=h['bars'][-1]['close'],_security_date=asof)
                cover={n:{'source_urls':['https://example.invalid/test-only']} for n in ('sh_a','sz_a')}
                with patch.object(p,'sample_codes',return_value=[]),patch.object(p,'listing_info',return_value={
                        'date':'2026-08-11','sessions':count,'source_url':'https://example.invalid/test-only'}):
                    payload,errors=p.assemble([row],cover,{symbol:{'sector':'test-only'}},{symbol:h},
                        {'test':{'stamp':asof.replace('-','')+'160000'}},asof,forecast,
                        forecast+'T08:50:00+08:00',forecast+'T08:40:00+08:00')
                record=payload['universe'][0]
                self.assertEqual(record['eligible'],count>=60)
                self.assertEqual(bool(errors),count==60)
                self.assertEqual(len(record['bars']),count)
                if count==60:
                    self.assertEqual(record['history_diagnostic']['status'],'INSUFFICIENT_OBSERVED_SESSIONS')
                    self.assertFalse(record['history_diagnostic']['formal_top5_allowed'])
                    self.assertNotIn('exclude_reason',record)


class FactorEnvelopeIntegrity(unittest.TestCase):
    def test_wrong_security_truncated_payload_and_executable_suffix_rejected(self):
        data=captured();text=data['raw_sina_qfq']['body']
        for bad in (text.replace('sh688828qfq','sz301717qfq'),text[:50],
                    text+'\nalert(1);',text+'\n/* comment */ alert(1); /* trailing */'):
            with self.subTest(payload=bad[:45]),self.assertRaises(ValueError):
                r.parse_sina_factors(bad,'sh688828')
    def test_duplicate_or_missing_factor_rows_rejected(self):
        for obj in ({'total':3,'data':[{'d':'2026-08-11','f':'1'}]},
                    {'total':2,'data':[{'d':'2026-08-11','f':'1'}]*2},
                    {'total':1,'data':[{'d':'2026-08-11','f':'nan'}]}):
            with self.assertRaises(ValueError):r.parse_sina_factors('var sh688828qfq='+json.dumps(obj),'sh688828')


class ShortSourceFailover(unittest.TestCase):
    def setUp(self):
        self.old=r.CIRCUITS;r.CIRCUITS=r.ProviderCircuits()
        self.data=captured();self.h=self.data['live_primary']
    def tearDown(self):r.CIRCUITS=self.old
    def test_successful_but_truncated_primary_tries_whole_backup(self):
        short=copy.deepcopy(self.h);short['bars']=short['bars'][-20:]
        backup=sina_from_capture(self.data)
        with patch.object(r,'sina_history',return_value=backup) as fetch:
            out=r.daily_history('sh688828','2026-11-10',primary=lambda *args:short)
        fetch.assert_called_once()
        self.assertEqual(len(out['bars']),60)
        self.assertEqual(out['provider_route'],'sina_klc2')
        self.assertEqual([x['bar_count'] for x in out['route_attempts']],[20,60])
    def test_both_sources_have_only_60_still_returns_short_without_padding(self):
        backup=sina_from_capture(self.data)
        with patch.object(r,'sina_history',return_value=backup):
            out=r.daily_history('sh688828','2026-11-10',primary=lambda *args:copy.deepcopy(self.h))
        self.assertEqual(out['bars'],self.h['bars'])
        self.assertEqual(len(out['route_attempts']),2)
        self.assertFalse(r.CIRCUITS.snapshot()['eastmoney_daily']['circuit_open'])
    def test_61_actual_bars_can_recover_from_short_primary(self):
        complete=sina_from_capture(self.data,'2026-11-11')
        with patch.object(r,'sina_history',return_value=complete):
            out=r.daily_history('sh688828','2026-11-11',primary=lambda *args:copy.deepcopy(self.h))
        self.assertEqual(len(out['bars']),61);self.assertEqual(out['provider_route'],'sina_klc2')
        self.assertEqual(out['route_attempts'][-1]['status'],'COMPLETE')
    def test_backup_outage_is_visible_without_inventing_missing_bar(self):
        with patch.object(r,'sina_history',side_effect=RuntimeError('503')):
            out=r.daily_history('sh688828','2026-11-10',primary=lambda *args:copy.deepcopy(self.h))
        self.assertEqual(len(out['bars']),60)
        self.assertEqual(out['route_attempts'][-1]['status'],'ERROR')


class CacheIntegrity(unittest.TestCase):
    def complete(self):return sina_from_capture(captured(),'2026-11-11')
    def test_valid_same_asof_complete_cache_reused_without_network(self):
        h=self.complete()
        with tempfile.TemporaryDirectory() as directory:
            p.atomic(Path(directory)/'history/2026-11-11/sh688828.json',h)
            with patch.object(p,'east_bars',side_effect=AssertionError('network should not run')):
                _,out=p.load_daily_history(directory,'2026-11-11',{'symbol':'sh688828'})
            self.assertEqual(out['cache_audit']['state'],'VALIDATED_REUSE')
    def test_corrupt_partial_wrong_identity_and_future_caches_refetch(self):
        h=self.complete()
        variants=['{broken json', [], {**h,'bars':'invalid'}, {**h,'bars':h['bars'][-20:]},
                  {**h,'bars':[h['bars'][0]]+h['bars']},
                  {**h,'source_url':h['source_url'].replace('sh688828','sz301717')},
                  {**h,'retrieved_at':'2099-01-01T00:00:00+08:00'},
                  {**h,'retrieved_at':'2026-11-11T10:00:00+08:00'},
                  {**h,'amount_unit':'unknown'},{**h,'asof':'2026-11-10'}]
        damaged=copy.deepcopy(h);del damaged['bars'][0]['amount'];variants.append(damaged)
        for bad in variants:
            with self.subTest(kind=type(bad)),tempfile.TemporaryDirectory() as directory:
                file=Path(directory)/'history/2026-11-11/sh688828.json';file.parent.mkdir(parents=True)
                file.write_text(bad if isinstance(bad,str) else json.dumps(bad))
                with patch.object(p,'east_bars',return_value=copy.deepcopy(h)) as fetch:
                    _,out=p.load_daily_history(directory,'2026-11-11',{'symbol':'sh688828'})
                fetch.assert_called_once();self.assertEqual(len(out['bars']),61)
                self.assertTrue(out['cache_audit']['rejected_cache'])
    def test_refetch_failure_cannot_fall_back_to_poisoned_cache(self):
        with tempfile.TemporaryDirectory() as directory:
            p.atomic(Path(directory)/'history/2026-11-11/sh688828.json',{'bars':[]})
            with patch.object(p,'east_bars',side_effect=RuntimeError('both sources failed')):
                with self.assertRaises(RuntimeError):p.load_daily_history(directory,'2026-11-11',{'symbol':'sh688828'})


class FailureStageReceipt(unittest.TestCase):
    def test_industry_outage_records_actual_stage_after_history_collection(self):
        with tempfile.TemporaryDirectory() as directory:
            current=dt.datetime(2026,11,11,13,40,tzinfo=p.TZ)
            with patch('sys.argv',['pipeline','--output',directory,'--forecast','2026-11-11']),\
                 patch.object(p,'now',return_value=current),patch.object(p,'verified_market_minima',return_value={'sh_a':0,'sz_a':0}),\
                 patch.object(p,'frozen_universe',return_value=None),patch.object(p,'paged',return_value=([],{})),\
                 patch.object(p,'industries',side_effect=RuntimeError('502')):
                with self.assertRaises(SystemExit):p.main()
            receipt=json.loads((Path(directory)/'pipeline_status.json').read_text())
            self.assertEqual(receipt['failed_stage'],'industry_catalog_and_memberships')
            self.assertEqual(receipt['history_success'],0)
            self.assertFalse(receipt['formal_top5_allowed'])
            self.assertEqual(receipt,json.loads(next((Path(directory)/'runs').glob('*/receipt.json')).read_text()))

if __name__=='__main__':unittest.main()
