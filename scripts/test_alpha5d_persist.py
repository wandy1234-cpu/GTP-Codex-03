import json
import tempfile
import unittest
from pathlib import Path
import alpha5d_persist as x
import alpha5d_pipeline as p

class ResultPersistence(unittest.TestCase):
    def test_old_checkout_cannot_replace_newer_status_but_keeps_its_run(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);stage=root/'stage';dest=root/'destination'
            p.atomic(dest/'pipeline_status.json',{'status':'COMPLETE','started_at':'2026-11-03T09:30:00+08:00'})
            p.atomic(stage/'pipeline_status.json',{'status':'INCOMPLETE','started_at':'2026-11-03T09:20:00+08:00'})
            p.atomic(stage/'runs'/'older-run'/'receipt.json',{'status':'INCOMPLETE'})
            x.apply_result(stage,dest)
            self.assertEqual(json.loads((dest/'pipeline_status.json').read_text())['status'],'COMPLETE')
            self.assertTrue((dest/'runs'/'older-run'/'receipt.json').exists())
    def test_later_failure_is_visible_and_does_not_erase_prior_success(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);stage=root/'stage';dest=root/'destination'
            p.atomic(dest/'pipeline_status.json',{'status':'COMPLETE','started_at':'2026-11-03T09:20:00+08:00'})
            p.atomic(dest/'runs'/'successful'/'ranking.json',{'top5':['preserved']})
            p.atomic(stage/'pipeline_status.json',{'status':'INCOMPLETE','started_at':'2026-11-03T09:30:00+08:00'})
            x.apply_result(stage,dest)
            self.assertEqual(json.loads((dest/'pipeline_status.json').read_text())['status'],'INCOMPLETE')
            self.assertEqual(json.loads((dest/'runs'/'successful'/'ranking.json').read_text())['top5'],['preserved'])
    def test_frozen_evidence_is_create_only(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);stage=root/'stage';dest=root/'destination'
            p.atomic(stage/'pipeline_status.json',{'status':'COMPLETE'})
            p.atomic(stage/'runs'/'frozen'/'ranking.json',{'top5':['changed']})
            p.atomic(dest/'runs'/'frozen'/'ranking.json',{'top5':['original']})
            with self.assertRaisesRegex(ValueError,'Immutable evidence collision'):x.apply_result(stage,dest)
    def test_interrupted_run_cannot_be_staged_as_running_or_complete(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);p.atomic(root/'source'/'pipeline_status.json',{'status':'RUNNING'})
            result=x.stage_result(root/'source',root/'stage','123')
            self.assertEqual(result['status'],'INTERRUPTED');self.assertEqual(result['workflow_run_id'],'123')

if __name__=='__main__':unittest.main()
