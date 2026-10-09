import unittest
from unittest.mock import patch
import alpha5d_source_fixes as f
class SourceFixTests(unittest.TestCase):
    def test_membership_preserved(self):
        result={};f.merge_membership(result,'sh600714','B',['url2']);f.merge_membership(result,'sh600714','A',['url1'])
        self.assertEqual(result['sh600714']['sector_options'],['A','B'])
        self.assertEqual(result['sh600714']['assignment_state'],'REQUIRES_RANK_INVARIANCE')
    def test_stable_assignment_passes(self):
        payload={'universe':[{'symbol':'a','sector_options':['X','Y']} ]}
        def rank(e,p):return {'ranking_health':'COMPLETE','eligible_codes':['a'],'top5':[{'symbol':'a','score':1}],'errors':[]}
        with patch.object(f,'BASE_RANK',side_effect=rank):out=f.robust_rank(None,payload)
        self.assertTrue(out['industry_assignment_audit']['top5_order_invariant'])
        self.assertEqual(out['industry_assignment_audit']['scenarios_verified'],2)
    def test_unstable_assignment_fails(self):
        payload={'universe':[{'symbol':'a','sector_options':['X','Y'],'sector':'X'}]}
        def rank(e,p):
            code='b' if p['universe'][0]['sector']=='Y' else 'a'
            return {'ranking_health':'COMPLETE','eligible_codes':['a'],'top5':[{'symbol':code,'score':1}],'errors':[]}
        with patch.object(f,'BASE_RANK',side_effect=rank):out=f.robust_rank(None,payload)
        self.assertEqual(out['ranking_health'],'INCOMPLETE');self.assertEqual(out['top5'],[])
if __name__=='__main__':unittest.main()
