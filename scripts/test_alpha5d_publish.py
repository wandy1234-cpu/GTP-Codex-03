import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import alpha5d_publish as publisher


class EvidencePublishTest(unittest.TestCase):
    def test_failed_attempt_keeps_source_and_attempt_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            publisher.snapshot_results(root/'missing', root/'snapshot',
                {'GITHUB_RUN_ID': '42', 'GITHUB_RUN_ATTEMPT': '2', 'GITHUB_SHA': 'source-sha'})
            payload = json.loads((root/'snapshot/attempts/42/2/status.json').read_text())
            self.assertEqual(payload['status'], 'FAILED_BEFORE_COLLECTION')
            self.assertEqual(payload['source_commit'], 'source-sha')
            self.assertEqual(payload['workflow_run_attempt'], 2)

    def test_older_run_is_archived_without_replacing_newer_status(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            publisher.write_json(root/'snapshot/pipeline_status.json', {'started_at': '2026-11-02T13:30:00+08:00'})
            publisher.write_json(root/'snapshot/runs/old/receipt.json', {'status': 'INCOMPLETE'})
            publisher.write_json(root/'destination/pipeline_status.json', {'started_at': '2026-11-02T13:40:00+08:00'})
            publisher.overlay_results(root/'snapshot', root/'destination')
            self.assertTrue((root/'destination/runs/old/receipt.json').exists())
            self.assertIn('13:40', (root/'destination/pipeline_status.json').read_text())

    def test_different_existing_receipt_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            publisher.write_json(root/'snapshot/pipeline_status.json', {'started_at': '2026-11-02T13:30:00+08:00'})
            publisher.write_json(root/'snapshot/runs/same/receipt.json', {'status': 'COMPLETE'})
            publisher.write_json(root/'destination/runs/same/receipt.json', {'status': 'INCOMPLETE'})
            with self.assertRaises(ValueError):
                publisher.overlay_results(root/'snapshot', root/'destination')
            self.assertIn('INCOMPLETE', (root/'destination/runs/same/receipt.json').read_text())

    def test_concurrent_main_advance_keeps_both_publications(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            remote, runner, other, snapshot = [root/name for name in ('remote.git', 'runner', 'other', 'snapshot')]
            remote.mkdir()
            publisher.git(remote, 'init', '--bare', '--initial-branch=main')
            publisher.git(root, 'clone', str(remote), str(runner))
            for repo in (runner,):
                publisher.git(repo, 'config', 'user.name', 'test')
                publisher.git(repo, 'config', 'user.email', 'test@example.invalid')
            (runner/'README').write_text('initial')
            publisher.git(runner, 'add', 'README')
            publisher.git(runner, 'commit', '-m', 'initial')
            publisher.git(runner, 'push', 'origin', 'main')
            publisher.git(root, 'clone', str(remote), str(other))
            publisher.git(other, 'config', 'user.name', 'test')
            publisher.git(other, 'config', 'user.email', 'test@example.invalid')
            publisher.write_json(snapshot/'pipeline_status.json', {'started_at': '2026-11-02T13:40:00+08:00'})
            publisher.write_json(snapshot/'runs/new/receipt.json', {'status': 'INCOMPLETE'})
            original_git = publisher.git
            raced = False

            def concurrent_git(repo, *args, **kwargs):
                nonlocal raced
                if args[:1] == ('push',) and not raced:
                    raced = True
                    publisher.write_json(other/'data/alpha5d/pipeline_status.json', {'started_at': '2026-11-02T13:30:00+08:00'})
                    (other/'relay.txt').write_text('concurrent relay update')
                    original_git(other, 'add', '.')
                    original_git(other, 'commit', '-m', 'concurrent publication')
                    original_git(other, 'push', 'origin', 'main')
                return original_git(repo, *args, **kwargs)

            with patch.object(publisher, 'git', side_effect=concurrent_git):
                publisher.publish(snapshot, runner)
            self.assertTrue(raced)
            self.assertEqual((runner/'relay.txt').read_text(), 'concurrent relay update')
            self.assertTrue((runner/'data/alpha5d/runs/new/receipt.json').exists())
            self.assertEqual(publisher.git(runner, 'rev-parse', 'HEAD').stdout,
                             publisher.git(remote, 'rev-parse', 'main').stdout)


if __name__ == '__main__':
    unittest.main()
