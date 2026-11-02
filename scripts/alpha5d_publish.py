#!/usr/bin/env python3
"""Publish public receipts from a disposable Actions checkout, without rebasing data.

Snapshot collection results before refreshing main. Keep per-run evidence immutable
and never replace a newer shared status with an older collector's status.
"""
import datetime as dt
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile


def write_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2)+'\n')


def snapshot_results(output, snapshot, env):
    output, snapshot = Path(output), Path(snapshot)
    source = output/'pipeline_status.json'
    payload = json.loads(source.read_text()) if source.exists() else {'status': 'FAILED_BEFORE_COLLECTION'}
    if payload.get('status') == 'RUNNING':
        payload['status'] = 'INTERRUPTED'
    if (output/'retrieval_progress.json').exists():
        payload['retrieval_progress'] = json.loads((output/'retrieval_progress.json').read_text())
    if payload.get('previous_session'):
        payload['cached_history_files'] = len(list((output/'history'/payload['previous_session']).glob('*.json')))
    payload.update(workflow_run_id=env['GITHUB_RUN_ID'],
                   workflow_run_attempt=int(env['GITHUB_RUN_ATTEMPT']),
                   source_commit=env['GITHUB_SHA'],
                   evidence_artifact_name='alpha5d-public-evidence-'+env['GITHUB_RUN_ID']+'-'+env['GITHUB_RUN_ATTEMPT'],
                   persisted_at=dt.datetime.now(dt.timezone.utc).isoformat())
    write_json(snapshot/'pipeline_status.json', payload)
    for source in (output/'runs').glob('*'):
        for name in ('receipt.json', 'ranking.json'):
            if (source/name).exists():
                target = snapshot/'runs'/source.name/name
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source/name, target)
    # Also retain this attempt's enriched failure/status even if collection never started.
    write_json(snapshot/'attempts'/env['GITHUB_RUN_ID']/env['GITHUB_RUN_ATTEMPT']/'status.json', payload)


def status_time(payload):
    return dt.datetime.fromisoformat(payload.get('started_at') or payload['persisted_at'])


def overlay_results(snapshot, destination):
    snapshot, destination = Path(snapshot), Path(destination)
    status = snapshot/'pipeline_status.json'
    target_status = destination/'pipeline_status.json'
    incoming = json.loads(status.read_text())
    if not target_status.exists() or status_time(incoming) >= status_time(json.loads(target_status.read_text())):
        write_json(target_status, incoming)
    for folder in ('runs', 'attempts'):
        for source in (snapshot/folder).rglob('*.json'):
            target = destination/source.relative_to(snapshot)
            if target.exists() and target.read_bytes() != source.read_bytes():
                raise ValueError('Refuse to overwrite existing run evidence: '+str(target))
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)


def git(repo, *args, check=True):
    return subprocess.run(['git', '-C', str(repo), *args], check=check, capture_output=True, text=True)


def publish(snapshot, repo, attempts=3):
    """Only for a disposable checkout: refresh and overlay again on a push race."""
    for attempt in range(attempts):
        git(repo, 'fetch', 'origin', 'main')
        git(repo, 'reset', '--hard', 'origin/main')
        overlay_results(snapshot, Path(repo)/'data/alpha5d')
        git(repo, 'add', 'data/alpha5d')
        if git(repo, 'diff', '--cached', '--quiet', check=False).returncode == 0:
            return
        git(repo, 'commit', '-m', 'Record actual alpha5d public-data and ranking run')
        pushed = git(repo, 'push', 'origin', 'HEAD:main', check=False)
        if pushed.returncode == 0:
            return
        if attempt == attempts-1:
            raise RuntimeError('Public evidence push failed after bounded retries: '+pushed.stderr)


if __name__ == '__main__':
    if os.environ.get('GITHUB_ACTIONS') != 'true':
        raise SystemExit('Publisher requires a disposable GitHub Actions checkout')
    with tempfile.TemporaryDirectory(prefix='alpha5d-evidence-') as directory:
        snapshot_results('.cache/alpha5d', directory, os.environ)
        publish(directory, Path.cwd())
