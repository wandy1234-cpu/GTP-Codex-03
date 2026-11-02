"""Offline regression tests: no market collection, ranking or published data."""
import contextlib
import datetime as dt
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch
import urllib.error
import urllib.parse as up

import alpha5d_classification as c
import alpha5d_pipeline as p


class ProviderHeadersTests(unittest.TestCase):
    def test_eastmoney_hosts_use_matching_referer(self):
        for host in (*c.HOSTS, 'https://push2his.eastmoney.com', 'https://eastmoney.com'):
            with self.subTest(host=host):
                headers=p.request_headers(host+'/api/qt/clist/get')
                self.assertEqual(headers['Referer'],'https://quote.eastmoney.com/')
                self.assertEqual(headers['User-Agent'],p.HEADERS['User-Agent'])

    def test_other_providers_and_lookalike_hosts_are_unchanged(self):
        for url in (p.SINA,'https://qt.gtimg.cn/q=sh600026',
                    'https://eastmoney.com.example.org/',
                    'https://noteastmoney.com/',
                    'https://example.org/?url=https://eastmoney.com/'):
            with self.subTest(url=url):
                self.assertEqual(p.request_headers(url),p.HEADERS)

    def test_header_selection_does_not_mutate_shared_defaults(self):
        original=p.HEADERS.copy()
        p.request_headers(c.HOSTS[0])['User-Agent']='test-only'
        self.assertEqual(p.HEADERS,original)

    def test_http_sends_selected_headers_with_existing_timeout(self):
        response=MagicMock()
        response.__enter__.return_value.read.return_value=b'{"data":{"total":1}}'
        with patch.object(p.ur,'urlopen',return_value=response) as opened:
            self.assertEqual(p.http(c.HOSTS[0]),'{"data":{"total":1}}')
        request=opened.call_args.args[0]
        self.assertEqual(request.get_header('Referer'),'https://quote.eastmoney.com/')
        self.assertEqual(opened.call_args.kwargs,{'timeout':12})

    def test_http_keeps_two_attempts_and_identifies_failed_url(self):
        url=c.HOSTS[0]+'/api/qt/clist/get?pn=2'
        error=urllib.error.HTTPError(url,502,'Bad Gateway',{},None)
        with patch.object(p.ur,'urlopen',side_effect=error) as opened, \
             patch.object(p.time,'sleep') as sleep:
            with self.assertRaises(RuntimeError) as caught:p.http(url)
        self.assertEqual(opened.call_count,2)
        sleep.assert_called_once_with(.4)
        self.assertIn('HTTPError',str(caught.exception))
        self.assertIn(url,str(caught.exception))
        self.assertIn('attempts=2',str(caught.exception))
        self.assertIs(caught.exception.__cause__,error)


class IndustryRequestTests(unittest.TestCase):
    def test_fallback_preserves_success_tuple_and_host_order(self):
        params={'fs':'m:90 t:2 f:!50','pn':1}
        data={'total':1,'diff':[{'f12':'BK1','f14':'银行'}]}
        with patch.object(p,'http',side_effect=[RuntimeError('502'),json.dumps({'data':data})]) as http:
            result,url=c.request('/api/qt/clist/get',params)
        self.assertEqual(result,data)
        self.assertEqual(url,c.HOSTS[1]+'/api/qt/clist/get?'+up.urlencode(params))
        self.assertEqual([call.args for call in http.call_args_list],
                         [(host+'/api/qt/clist/get?'+up.urlencode(params),) for host in c.HOSTS[:2]])

    def test_catalog_failure_records_each_attempted_host_and_page(self):
        params={'fs':'m:90 t:2 f:!50','pn':2}
        with patch.object(p,'http',side_effect=RuntimeError('RemoteDisconnected')):
            with self.assertRaises(c.IndustryRequestError) as caught:
                c.request('/api/qt/clist/get',params)
        errors=caught.exception.source_errors
        self.assertEqual(len(errors),len(c.HOSTS))
        for host,error in zip(c.HOSTS,errors):
            self.assertEqual(error['stage'],'industry_catalog')
            self.assertEqual(error['host'],host)
            self.assertEqual(error['url'],host+'/api/qt/clist/get?'+up.urlencode(params))
            self.assertEqual(error['page'],2)
            self.assertEqual(error['filter'],params['fs'])
            self.assertEqual(error['referer'],'https://quote.eastmoney.com/')
            self.assertEqual(error['error_type'],'RuntimeError')
            self.assertIn('RemoteDisconnected',error['error'])
            dt.datetime.fromisoformat(error['observed_at'])

    def test_membership_and_profile_stages_are_distinct(self):
        for path,params,stage in (
            ('/api/qt/clist/get',{'fs':'b:BK1 f:!50','pn':3},'industry_membership'),
            ('/api/qt/stock/get',{'secid':'1.600026'},'profile')):
            with self.subTest(stage=stage), patch.object(p,'http',side_effect=RuntimeError('502')):
                with self.assertRaises(c.IndustryRequestError) as caught:c.request(path,params)
                error=caught.exception.source_errors[0]
                self.assertEqual(error['stage'],stage)
                self.assertEqual(error['secid'],params.get('secid'))

    def test_invalid_provider_payloads_still_fail_closed(self):
        for payload in ('{"data":null}','{"data":{}}','not json'):
            with self.subTest(payload=payload),patch.object(p,'http',return_value=payload):
                with self.assertRaises(c.IndustryRequestError) as caught:
                    c.request('/api/qt/clist/get',{'fs':'m:90 t:2 f:!50','pn':1})
                self.assertEqual(len(caught.exception.source_errors),3)


class IndustryPaginationTests(unittest.TestCase):
    def rows(self,count,start=0):
        return [{'f12':str(i),'f13':1,'f14':'test'} for i in range(start,start+count)]

    def test_complete_catalog_keeps_page_audit(self):
        with patch.object(c,'request',side_effect=[
            ({'total':101,'diff':self.rows(100)},'https://example.org/page1'),
            ({'total':101,'diff':{'0':self.rows(1,100)[0]}},'https://example.org/page2')]):
            rows,audit=c.page_list('m:90 t:2 f:!50')
        self.assertEqual(len(rows),101)
        self.assertEqual(audit['total'],101)
        self.assertEqual(audit['page_counts'],[100,1])
        self.assertEqual(audit['pages'],2)
        self.assertEqual(len(audit['source_urls']),2)

    def test_incomplete_page_rejected(self):
        with patch.object(c,'request',return_value=({'total':101,'diff':self.rows(99)},'url')):
            with self.assertRaisesRegex(ValueError,'page count incomplete'):c.page_list('catalog')

    def test_changing_total_rejected(self):
        with patch.object(c,'request',side_effect=[
            ({'total':101,'diff':self.rows(100)},'url1'),
            ({'total':102,'diff':self.rows(2,100)},'url2')]):
            with self.assertRaisesRegex(ValueError,'total changed'):c.page_list('catalog')

    def test_duplicate_rejected(self):
        with patch.object(c,'request',return_value=({'total':2,'diff':self.rows(1)*2},'url')):
            with self.assertRaisesRegex(ValueError,'pagination duplicate'):c.page_list('catalog')

    def test_empty_first_page_rejected(self):
        with patch.object(c,'request',return_value=({'total':0,'diff':[]},'url')):
            with self.assertRaisesRegex(ValueError,'Empty industry first page'):c.page_list('catalog')

    def test_missing_total_rejected(self):
        with patch.object(c,'request',return_value=({'diff':self.rows(1)},'url')):
            with self.assertRaisesRegex(ValueError,'Missing membership total'):c.page_list('catalog')


class FailureReceiptTests(unittest.TestCase):
    def test_catalog_failure_is_persisted_without_rank_or_history_collection(self):
        # Exercise the real catalog request wrapper, while every network read is mocked.
        stamp=dt.datetime(2026,10,9,9,30,tzinfo=p.TZ)
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(p,'now',return_value=stamp), \
             patch.object(p,'paged',side_effect=[([{'symbol':'sh600026'}],{}),([{'symbol':'sz000001'}],{})]), \
             patch.object(p,'industries',side_effect=c.industries), \
             patch.object(c,'root',return_value=Path(directory)), \
             patch.object(p,'http',side_effect=RuntimeError('RemoteDisconnected')), \
             patch.object(p,'east_bars') as daily, \
             patch.object(p,'run_rank') as rank, \
             patch('sys.argv',['pipeline','--output',directory]), \
             contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(SystemExit) as caught:p.main()
            self.assertEqual(caught.exception.code,2)
            receipt=json.loads((Path(directory)/'runs'/'20261009T093000'/'receipt.json').read_text())
            status=json.loads((Path(directory)/'pipeline_status.json').read_text())
            self.assertEqual(receipt,status)
            self.assertEqual(status['status'],'INCOMPLETE')
            self.assertFalse(status['formal_top5_allowed'])
            self.assertEqual(len(status['source_errors']),3)
            self.assertTrue(all(e['stage']=='industry_catalog' for e in status['source_errors']))
            self.assertTrue(all(e['page']==1 for e in status['source_errors']))
            self.assertEqual(list(Path(directory).rglob('ranking.json')),[])
            daily.assert_not_called()
            rank.assert_not_called()

    def test_classification_edits_trigger_data_workflow(self):
        workflow=Path(__file__).resolve().parents[1]/'.github'/'workflows'/'alpha5d-data.yml'
        text=workflow.read_text()
        paths=text.split('    paths:',1)[1].split('  schedule:',1)[0]
        self.assertIn("      - 'scripts/alpha5d_classification.py'",paths)


if __name__=='__main__':unittest.main()
