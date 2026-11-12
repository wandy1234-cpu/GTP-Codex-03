"""Regression checks for data integrity and failover, not market backtests."""
import copy
import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import alpha5d_pipeline as p
import alpha5d_engine as e
import alpha5d_provider_resilience as r
import alpha5d_runtime as runtime

def observed():
    return dict(date='2026-11-02',open=9.98,high=10.22,low=9.93,close=10.06,volume=116422276,amount=1177122567)
def history():
    return dict(bars=r.normalize_sina([observed()],'2026-11-02'),amount_unit='CNY',volume_unit='lots_100_shares',source_url='https://finance.sina.com.cn/realstock/company/sh600000/hisdata_klc2/klc_kl.js',adjustment_basis='Sina_raw_anchor=2026-11-02')
def unavailable(*args):raise RuntimeError('transport unavailable')

class HistoryIntegrity(unittest.TestCase):
    def test_live_factor_assignment_with_trailing_provider_signature(self):
        obj={'total':2,'data':[{'d':'2026-08-11','f':'1.0'},{'d':'1900-01-01','f':'1.0'}]}
        body='var sh688828qfq='+json.dumps(obj)+'\n/* Zq+XaqQOatiAAXE6hDMWwBSRiAwfeAdl */'
        self.assertEqual(r.parse_sina_factors(body,'sh688828'),obj['data'])
        self.assertEqual(r.parse_sina_factors(body.replace('\n/*',';\n/*'),'sh688828'),obj['data'])
    def test_factor_assignment_rejects_wrong_identity_and_executable_tail(self):
        body='var sh688828qfq={"total":1,"data":[{"d":"1900-01-01","f":"1"}]}'
        for text,symbol in [(body,'sz301717'),(body+';run_code()','sh688828'),
                            (body.replace('"total":1','"total":2'),'sh688828')]:
            with self.assertRaises(ValueError):r.parse_sina_factors(text,symbol)
    def test_executable_between_two_comments_is_not_swallowed_as_one_comment(self):
        body='var sh688828qfq={"total":1,"data":[{"d":"1900-01-01","f":"1"}]}'
        with self.assertRaises(ValueError):r.parse_sina_factors(body+'/* first */;run_code();/* last */','sh688828')
    def test_real_amount_and_shares_are_preserved(self):
        bar=r.normalize_sina([observed()],'2026-11-02')[0]
        self.assertEqual(bar['amount'],1177122567);self.assertEqual(bar['volume'],1164222.76)
    def test_ohlcv_without_actual_amount_rejected(self):
        row=observed();del row['amount']
        with self.assertRaises(KeyError):r.normalize_sina([row],'2026-11-02')
    def test_wrong_unit_rejected_instead_of_guessed(self):
        row=observed();row['amount']/=10000
        with self.assertRaises(ValueError):r.normalize_sina([row],'2026-11-02')
    def test_qfq_anchored_before_future_corporate_action(self):
        factors=[{'d':'2026-11-04','f':'0.5'},{'d':'2026-11-02','f':'1'},{'d':'1999-11-10','f':'2'}]
        bars=r.normalize_sina([dict(observed(),date='2026-10-30'),observed()],'2026-11-02',factors)
        self.assertEqual(bars[0]['close'],5.03);self.assertEqual(bars[1]['close'],10.06)
        self.assertEqual(bars[0]['amount'],observed()['amount'])
    def test_future_observation_cannot_enter_features(self):
        bars=r.normalize_sina([observed(),dict(observed(),date='2026-11-03')],'2026-11-02')
        self.assertEqual([b['date'] for b in bars],['2026-11-02'])
    def test_input_hash_matches_ranker_exactly(self):self.assertEqual(p.sha({'中文':[1,2.3]}),e.digest({'中文':[1,2.3]}))

class Failover(unittest.TestCase):
    def setUp(self):self.old=r.CIRCUITS;r.CIRCUITS=r.ProviderCircuits(limit=1)
    def tearDown(self):r.CIRCUITS=self.old
    def test_source_failure_uses_independent_complete_ohlcva(self):
        with patch.object(r,'sina_history',return_value=history()):out=r.daily_history('sh600000','2026-11-02',primary=unavailable)
        self.assertEqual(out['provider_route'],'sina_klc2')
        self.assertTrue(r.CIRCUITS.snapshot()['eastmoney_daily']['circuit_open'])
    def test_bulk_circuit_cannot_block_sample(self):
        with self.assertRaises(RuntimeError):r.CIRCUITS.call('eastmoney_daily',unavailable)
        out=r.daily_history('sh600000','2026-11-02',0,primary=lambda *a:history(),sample=True)
        self.assertEqual(out['provider_route'],'sample_eastmoney_daily')
    def test_all_failed_sources_still_block_data(self):
        with patch.object(r,'sina_history',side_effect=RuntimeError('second failed')):
            with self.assertRaisesRegex(RuntimeError,'all independent daily routes failed'):r.daily_history('sh600000','2026-11-02',primary=unavailable)
    def test_one_tickers_missing_data_does_not_disable_source(self):
        for _ in range(10):
            with self.assertRaises(ValueError):r.CIRCUITS.call('sina_klc2',lambda:(_ for _ in ()).throw(ValueError('ticker missing')))
        self.assertEqual(r.CIRCUITS.call('sina_klc2',lambda:'healthy'),'healthy')
    def test_global_failure_meter_does_not_poison_healthy_request(self):
        with tempfile.TemporaryDirectory() as directory:
            def call(symbol):
                if symbol=='bad':raise RuntimeError('ticker unavailable')
                return history()
            guard=runtime.RetrievalGuard(call,Path(directory)/'progress.json')
            for _ in range(35):
                with self.assertRaises(RuntimeError):guard('bad')
            self.assertTrue(guard('good')['bars']);self.assertFalse(guard.stats['circuit_open'])

class EligibilityEvidence(unittest.TestCase):
    def test_dated_suspension_request_includes_both_required_filters(self):
        import urllib.parse as up
        response={'success':True,'result':{'count':0,'pages':1,'data':[]}}
        with tempfile.TemporaryDirectory() as directory,patch.object(r,'ROOT',Path(directory)),patch.object(p,'http',return_value=json.dumps(response)) as request:
            out,audit=r.session_halts('2026-11-02',[])
        filters=up.parse_qs(up.urlparse(request.call_args.args[0]).query)['filter'][0]
        self.assertIn('(MARKET="全部")',filters);self.assertIn("(DATETIME='2026-11-02')",filters)
        self.assertTrue(audit['complete']);self.assertEqual(out,{})
    def test_zero_volume_is_not_a_suspension_notice(self):
        self.assertEqual(r.parse_session_halts([],'2026-11-02',[dict(symbol='sh601326',volume=0,amount=0)],'url'),{})
    def test_dated_notice_covers_nontrading_security(self):
        notice=dict(SECURITY_CODE='601326',DATETIME='2026-11-02',SUSPEND_START_DATE='2026-10-21')
        result=r.parse_session_halts([notice],'2026-11-02',[dict(symbol='sh601326',volume=0,amount=0)],'url')
        self.assertEqual(result['sh601326']['provider_row'],notice)
    def test_intraday_pause_does_not_remove_traded_security(self):
        notice=dict(SECURITY_CODE='601326',DATETIME='2026-11-02',SUSPEND_START_DATE='2026-11-02')
        self.assertEqual(r.parse_session_halts([notice],'2026-11-02',[dict(symbol='sh601326',volume=10,amount=100)],'url'),{})
    def forecast_notice(self,**kw):
        return dict(SECURITY_CODE='002623',SUSPEND_START_DATE='2026-11-03',SUSPEND_START_TIME='2026-11-03 09:30:00',SUSPEND_EXPIRE='连续停牌',**kw)
    def test_known_next_session_halt_excludes_even_if_previous_session_traded(self):
        notice=self.forecast_notice()
        out=r.parse_forecast_halts([notice],'2026-11-02','2026-11-03',[dict(symbol='sz002623',volume=100,amount=1000)],'url')
        self.assertEqual(out['sz002623']['session'],'2026-11-03')
    def test_intraday_forecast_pause_is_not_whole_session_exclusion(self):
        notice=self.forecast_notice(SUSPEND_END_TIME='2026-11-03 10:00:00')
        self.assertEqual(r.parse_forecast_halts([notice],'2026-11-02','2026-11-03',[dict(symbol='sz002623')],'url'),{})
    def test_already_resumed_stock_is_not_carryover_halt(self):
        notice=self.forecast_notice(PREDICT_RESUME_DATE='2026-11-03')
        self.assertEqual(r.parse_forecast_halts([notice],'2026-11-02','2026-11-03',[dict(symbol='sz002623')],'url'),{})
    def test_other_session_notice_cannot_be_carried_forward(self):
        notice=dict(SECURITY_CODE='601326',DATETIME='2026-10-30',SUSPEND_START_DATE='2026-10-21')
        with self.assertRaises(ValueError):r.parse_session_halts([notice],'2026-11-02',[dict(symbol='sh601326',volume=0,amount=0)],'url')
    def test_listing_date_is_independently_read(self):
        with tempfile.TemporaryDirectory() as directory,patch.object(r,'ROOT',Path(directory)),patch.object(p,'http',return_value='<title>公司(601326)公司资料</title><td>上市日期：</td><td>2017-08-16</td>'):
            result=r.sina_listing_info('sh601326','2026-11-02')
        self.assertEqual(result['date'],'2017-08-16');self.assertEqual(result['sessions'],999)
    def test_missing_history_excludes_only_verified_recent_ipo(self):
        rows=[dict(symbol='sz301999',name='SYNTHETIC')];cover={n:dict(source_urls=['https://sina']) for n in ['sh_a','sz_a']}
        for sessions,expected in [(2,False),(999,True)]:
            with patch.object(p,'sample_codes',return_value=[]),patch.object(p,'listing_info',return_value=dict(date='2026-10-30',sessions=sessions,source_url='https://listing')):
                payload,errors=p.assemble(rows,cover,{}, {}, {'sample':{'stamp':'20261102160000'}},'2026-11-02','2026-11-03','2026-11-03T08:50:00+08:00','2026-11-03T08:40:00+08:00')
            self.assertEqual(payload['universe'][0]['eligible'],expected);self.assertEqual(bool(errors),expected)

class RecoveredPageIntegrity(unittest.TestCase):
    def make(self,folder):
        rows=[dict(symbol='sz000001',trade='10.02',volume=100,amount=200000),dict(symbol='sz000002',trade='10.03',volume=120,amount=300000)]
        record={'captured_at_beijing':'2026-11-10T23:00:00+08:00','node':'sz_a','page':1,
                'stock_pick_gate':'VERIFIED_CLOSED_DATASET','record_count':2,
                'records':{x['symbol']:x for x in rows}}
        Path(folder,'sz_a_001.json').write_text(json.dumps(record))
        return rows
    def test_error_then_success_verified_by_original_raw_shard(self):
        with tempfile.TemporaryDirectory() as d:
            rows=self.make(d)
            self.assertTrue(r.verified_recovered_page('sz_a',1,rows,'2026-11-10T23:00:00+08:00',d))
    def test_missing_or_changed_page_never_accepted(self):
        with tempfile.TemporaryDirectory() as d:
            rows=self.make(d)
            self.assertFalse(r.verified_recovered_page('sz_a',1,[{**rows[0],'trade':'9.99'},rows[1]],'2026-11-10T23:00:00+08:00',d))
            self.assertFalse(r.verified_recovered_page('sz_a',1,rows,'2026-11-09T23:00:00+08:00',d))
            Path(d,'sz_a_001.json').unlink()
            self.assertFalse(r.verified_recovered_page('sz_a',1,rows,'2026-11-10T23:00:00+08:00',d))

class FrozenPreviousClose(unittest.TestCase):
    def dataset(self):
        records={};checks=[]
        for i in range(20):
            symbol=('sh600' if i<10 else 'sz000')+str(i).zfill(3)
            records[symbol]=dict(symbol=symbol,trade='10.06',settlement='10.02')
            checks.append(dict(symbol=symbol,passed=True,security_date='20261102',sina_price=10.06,tencent_snapshot_price=10.06,daily_close=10.06))
        return dict(records=records,unique_code_count=20,stock_pick_gate='VERIFIED_CLOSED_DATASET',complete_pagination=True,
                    captured_at_beijing='2026-11-02T21:00:00+08:00',
                    frozen_verification=dict(security_date='20261102',passed_count=20,checks=checks),
                    nodes={n:dict(raw_row_count=10,terminal_page=1,successful_pages=[1],missing_pages=[]) for n in ['sh_a','sz_a']},
                    source_requests=[dict(url='https://sina?node='+n+'&page=1',errors=[]) for n in ['sh_a','sz_a']])
    def call(self,data):
        with tempfile.TemporaryDirectory() as directory:
            file=Path(directory)/'frozen.json';file.write_text(json.dumps(data))
            with patch.object(r,'ROOT',Path(directory)),patch.object(r,'Path',return_value=file):return r.frozen_universe('2026-11-02')
    def test_verified_previous_close_used_even_during_next_session(self):
        rows,cover,audit=self.call(self.dataset())
        self.assertEqual(rows[0]['_security_date'],'2026-11-02');self.assertEqual(audit['frozen_samples_verified'],20)
        self.assertEqual(p.reference(rows[0],dict(stamp='20261103093500',price=10.50,previous=10.06),'2026-11-02'),(10.06,10.06))
    def test_raw_mismatch_cannot_pass_a_green_marker(self):
        data=self.dataset();next(iter(data['records'].values()))['trade']='9.00'
        with self.assertRaises(ValueError):self.call(data)
    def test_missing_frozen_page_url_manifest_requires_fresh_page_collection(self):
        data=self.dataset();data['source_requests']=[]
        # Absence of original page URL receipts never authenticates a frozen
        # snapshot. A None result routes the pipeline into full fresh paging.
        self.assertIsNone(self.call(data))
    def test_missing_actual_page_still_fails_closed(self):
        data=self.dataset();data['nodes']['sh_a']['successful_pages']=[]
        with self.assertRaises(ValueError):self.call(data)
    def test_wrong_security_date_not_reused(self):
        data=self.dataset();data['frozen_verification']['security_date']='20261030'
        self.assertIsNone(self.call(data))
    def test_future_snapshot_cannot_be_reused(self):
        data=self.dataset();data['captured_at_beijing']='2027-01-01T21:00:00+08:00'
        with self.assertRaises(ValueError):self.call(data)

class CompleteIndustryCoverage(unittest.TestCase):
    def test_successful_but_partial_map_uses_next_whole_source(self):
        complete={'sh600025':{'sector':'电力'},'sz000001':{'sector':'银行'}}
        with patch.object(r,'REQUIRED_INDUSTRIES',set(complete)),patch.object(r,'INDUSTRY_AUDIT',{}),\
             patch.object(r.classification,'industries',return_value=({'sz000001':{'sector':'银行'}},'primary')),\
             patch.object(r,'LEGACY_INDUSTRIES',return_value=(copy.deepcopy(complete),'backup')):
            result,url=r.industries(2)
            self.assertEqual(url,'backup');self.assertEqual(set(result),set(complete))
            self.assertEqual(r.INDUSTRY_AUDIT['covered_required_codes'],2)
    def test_sources_cannot_be_silently_mixed_to_fill_holes(self):
        with patch.object(r,'REQUIRED_INDUSTRIES',{'sh600025','sz000001'}),patch.object(r,'INDUSTRY_AUDIT',{}),\
             patch.object(r.classification,'industries',return_value=({'sz000001':{'sector':'银行'}},'primary')),\
             patch.object(r,'LEGACY_INDUSTRIES',return_value=({'sh600025':{'sector':'电力'}},'backup')):
            with self.assertRaisesRegex(ValueError,'All coherent industry sources incomplete'):r.industries(2)
    def test_required_memberships_exclude_only_existing_legal_exclusions(self):
        rows=[dict(symbol=s,name=n) for s,n in [('sh600025','华能水电'),('sz000001','平安银行'),('sz000002','ST测试'),('sz301999','N测试')]]
        h={x['symbol']:{'bars':[{}]*61} for x in rows};h['sz301999']['bars']=[{}]*3
        with patch.object(r,'REQUIRED_INDUSTRIES',set()),patch.object(r,'INDUSTRY_AUDIT',{}),patch.object(p,'verified_halt',side_effect=lambda s,d:s=='sz000001'):
            r.set_industry_context(rows,h,'2026-11-02');self.assertEqual(r.REQUIRED_INDUSTRIES,{'sh600025'})

class IndustrySnapshotIntegrity(unittest.TestCase):
    def snapshot(self):
        catalog=[];boards=[]
        for i,name in enumerate(sorted(r.classification.FIRST_LEVEL)):
            code='BK'+str(i).zfill(4);catalog.append(dict(f12=code,f13=90,f14=name))
            boards.append(dict(code=code,name=name,members=[dict(f12='6'+str(i).zfill(5),f13=1,f14='TEST')],
                audit=dict(source_urls=['https://82.push2.eastmoney.com/api/qt/clist/get?fs=b:'+code],
                           pages=1,page_counts=[1],total=1,observed_at=p.now().isoformat())))
        return dict(schema='eastmoney-level1-pages-v1',observed_at=p.now().isoformat(),catalog_records=catalog,
            catalog_audit=dict(source_urls=['https://82.push2.eastmoney.com/catalog']),boards=boards,boards_sha256=p.sha(boards))
    def test_complete_raw_pages_rebuild_original_memberships(self):
        result=r.classification.snapshot_memberships(self.snapshot())
        self.assertEqual(len(result),31);self.assertEqual({x['sector'] for x in result.values()},r.classification.FIRST_LEVEL)
    def test_tampered_source_pages_cannot_be_reused(self):
        data=self.snapshot();data['boards'][0]['members'][0]['f14']='ALTERED'
        with self.assertRaisesRegex(ValueError,'hash mismatch'):r.classification.snapshot_memberships(data)
    def test_future_snapshot_cannot_enter_current_rank(self):
        data=self.snapshot();data['observed_at']=(p.now()+dt.timedelta(seconds=5)).isoformat()
        with self.assertRaises(ValueError):r.classification.snapshot_memberships(data)
    def test_expired_snapshot_requires_refresh(self):
        data=self.snapshot();data['observed_at']=(p.now()-dt.timedelta(days=8)).isoformat()
        with self.assertRaises(ValueError):r.classification.snapshot_memberships(data)
    def test_partial_board_cannot_be_cached_as_complete(self):
        data=self.snapshot();data['boards'][0]['audit']['total']=2;data['boards_sha256']=p.sha(data['boards'])
        with self.assertRaisesRegex(ValueError,'pagination incomplete'):r.classification.snapshot_memberships(data)
    def test_missing_first_level_cannot_be_cached(self):
        data=self.snapshot();data['boards'].pop();data['boards_sha256']=p.sha(data['boards'])
        with self.assertRaisesRegex(ValueError,'board coverage incomplete'):r.classification.snapshot_memberships(data)
    def test_market_code_mismatch_is_rejected(self):
        data=self.snapshot();data['boards'][0]['members'][0]['f13']=0;data['boards_sha256']=p.sha(data['boards'])
        with self.assertRaisesRegex(ValueError,'market mismatch'):r.classification.snapshot_memberships(data)

if __name__=='__main__':unittest.main()
