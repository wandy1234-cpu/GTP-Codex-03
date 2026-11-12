#!/usr/bin/env python3
"""Promote immutable public run evidence without rebasing a shared status file."""
import argparse
import datetime as dt
import json
import os
from pathlib import Path
import alpha5d_pipeline as p

def stage_result(source,stage,run_id):
    source,stage=Path(source),Path(stage)
    file=source/'pipeline_status.json'
    payload=json.loads(file.read_text()) if file.exists() else {'status':'FAILED_BEFORE_COLLECTION'}
    if payload.get('status')=='RUNNING':
        payload.update(status='INTERRUPTED',failed_stage=payload.get('stage','UNKNOWN'),
            error='Collector interrupted before completing acceptance',formal_top5_allowed=False,
            ended_at=dt.datetime.now(dt.timezone.utc).isoformat())
    progress=source/'retrieval_progress.json'
    if progress.exists():payload['retrieval_progress']=json.loads(progress.read_text())
    previous=payload.get('previous_session')
    if previous:payload['cached_history_files']=len(list((source/'history'/previous).glob('*.json')))
    attempt=os.environ.get('GITHUB_RUN_ATTEMPT','1')
    payload.update(workflow_run_id=str(run_id),workflow_run_attempt=attempt,
                   evidence_artifact_name='alpha5d-public-evidence-'+str(run_id)+'-'+attempt,
                   persisted_at=dt.datetime.now(dt.timezone.utc).isoformat())
    p.atomic(stage/'pipeline_status.json',payload)
    for folder in (source/'runs').glob('*'):
        for name in ('receipt.json','ranking.json','review.json'):
            if (folder/name).is_file():p.atomic(stage/'runs'/folder.name/name,json.loads((folder/name).read_text()))
    # Interrupted processes may never reach the collector's finally block.
    # Preserve that attempt's terminal receipt instead of losing it behind latest status.
    record_id=payload.get('run_id')
    if record_id:
        receipt=stage/'runs'/record_id/'receipt.json'
        current=json.loads(receipt.read_text()) if receipt.exists() else {}
        if not current or current.get('status')=='RUNNING':
            if payload.get('status') not in ('COMPLETE','NOT_TRADING_DAY'):
                payload.setdefault('failed_stage',payload.get('stage','UNKNOWN'))
                p.atomic(stage/'pipeline_status.json',payload)
            p.atomic(receipt,payload)
    for dirname,target in [('industry_snapshots','industry'),('market','market')]:
        for file in (source/dirname).glob('*.json'):
            p.atomic(stage/target/file.name,json.loads(file.read_text()))
    return payload

def status_time(payload):
    value=payload.get('started_at') or payload.get('generated_at') or payload.get('persisted_at')
    return dt.datetime.fromisoformat(value).astimezone(dt.timezone.utc) if value else dt.datetime.min.replace(tzinfo=dt.timezone.utc)

def apply_result(stage,destination):
    stage,destination=Path(stage),Path(destination)
    incoming=json.loads((stage/'pipeline_status.json').read_text())
    for file in stage.rglob('*.json'):
        relative=file.relative_to(stage)
        if str(relative)=='pipeline_status.json':continue
        if relative.parts[0] not in ('runs','industry','market'):raise ValueError('Unapproved result path')
        target=destination/relative;data=json.loads(file.read_text())
        if target.exists():
            if p.sha(json.loads(target.read_text()))!=p.sha(data):raise ValueError('Immutable evidence collision: '+str(relative))
        else:p.atomic(target,data)
    file=destination/'pipeline_status.json'
    current=json.loads(file.read_text()) if file.exists() else {}
    if status_time(incoming)>=status_time(current):p.atomic(file,incoming)
    return incoming

def main():
    parser=argparse.ArgumentParser();parser.add_argument('mode',choices=['stage','apply'])
    parser.add_argument('--source',default='.cache/alpha5d');parser.add_argument('--stage',required=True)
    parser.add_argument('--destination',default='data/alpha5d');parser.add_argument('--run-id')
    args=parser.parse_args()
    data=stage_result(args.source,args.stage,args.run_id) if args.mode=='stage' else apply_result(args.stage,args.destination)
    print(json.dumps({'status':data.get('status'),'started_at':data.get('started_at'),'workflow_run_id':data.get('workflow_run_id')}))

if __name__=='__main__':main()
