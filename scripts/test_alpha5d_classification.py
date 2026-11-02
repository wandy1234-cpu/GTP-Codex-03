import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import alpha5d_classification as c
class IndustryClassificationTests(unittest.TestCase):
    def boards(self):return [{'f12':'BK'+str(i),'f14':name} for i,name in enumerate(sorted(c.FIRST_LEVEL))]
    def test_one_coherent_level(self):
        boards=self.boards()+[{'f12':'leaf','f14':'半导体'},{'f12':'leaf2','f14':'银行Ⅱ'}]
        self.assertEqual(len(c.select_first_level(boards)),31)
        self.assertNotIn('半导体',{b['f14'] for b in c.select_first_level(boards)})
    def test_missing_industry_rejected(self):
        with self.assertRaises(ValueError):c.select_first_level(self.boards()[:-1])
    def test_duplicate_root_rejected(self):
        boards=self.boards();boards.append(dict(boards[0]))
        with self.assertRaises(ValueError):c.select_first_level(boards)

class SameDayCacheTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.file=Path(self.temp.name)/'catalog.json'
        self.current=dt.datetime(2026,11,2,8,20,tzinfo=c.p.TZ)
        self.rows=[{'f12':'BK1','f13':90,'f14':'电子'}]
        self.audit={'total':1,'pages':1,'page_counts':[1],
                    'source_urls':[c.HOSTS[0]+'/api/qt/clist/get?pn=1'],
                    'observed_at':self.current.isoformat()}
    def tearDown(self):self.temp.cleanup()
    def save(self,**kw):
        data={'query':'catalog','name':None,'members':self.rows,'audit':self.audit}
        data.update(kw);self.file.write_text(json.dumps(data))
    def test_complete_same_day_cache_survives_provider_outage(self):
        self.save()
        with patch.object(c.p,'now',return_value=self.current),patch.object(c,'page_list',side_effect=RuntimeError('502')) as live:
            self.assertEqual(c.cached_pages(self.file,'catalog')[0],self.rows)
            live.assert_not_called()
    def test_stale_future_wrong_query_and_missing_page_never_pass(self):
        variants=[{'observed_at':'2026-11-01T08:00:00+08:00'},
                  {'observed_at':'2026-11-02T09:00:00+08:00'},
                  {'total':2},{'page_counts':[]},{'source_urls':[]}]
        for change in variants:
            self.save(audit={**self.audit,**change})
            with self.subTest(change=change),patch.object(c.p,'now',return_value=self.current),patch.object(c,'page_list',side_effect=RuntimeError('502')):
                with self.assertRaises(RuntimeError):c.cached_pages(self.file,'catalog')
        self.save(query='other-board')
        with patch.object(c.p,'now',return_value=self.current),patch.object(c,'page_list',side_effect=RuntimeError('502')):
            with self.assertRaises(RuntimeError):c.cached_pages(self.file,'catalog')
    def test_live_response_validated_and_reused(self):
        with patch.object(c.p,'now',return_value=self.current),patch.object(c,'page_list',return_value=(self.rows,self.audit)) as live:
            c.cached_pages(self.file,'catalog');c.cached_pages(self.file,'catalog')
            self.assertEqual(live.call_count,1)

if __name__=='__main__':unittest.main()
