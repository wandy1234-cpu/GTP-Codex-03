#!/usr/bin/env python3
"""Bound failed public-data retrieval and preserve actual checkpoints.
No new market inputs, scoring features, exclusions or trading operations.
"""
import concurrent.futures as cf
import json
import sys
import threading
from pathlib import Path
import alpha5d_pipeline as p
import alpha5d_source_fixes as fixes

ORIGINAL_POOL=cf.ThreadPoolExecutor
ORIGINAL_DAILY=p.east_bars

class CancelPendingPool(ORIGINAL_POOL):
    def __exit__(self,exc_type,exc_val,exc_tb):
        self.shutdown(wait=exc_type is None,cancel_futures=exc_type is not None)
        return False

class RetrievalGuard:
    def __init__(self,function,checkpoint):
        self.function,self.checkpoint=function,checkpoint
        self.lock=threading.Lock()
        self.stats={'attempted':0,'succeeded':0,'failed':0,'consecutive_failures':0,
                    'circuit_open':False,'last_error':None}
    def __call__(self,*args,**kwargs):
        with self.lock:
            if self.stats['circuit_open']:
                raise RuntimeError('Local daily-data circuit open after repeated retrieval failures; not an eligibility exclusion')
            self.stats['attempted']+=1
        try:
            result=self.function(*args,**kwargs)
        except Exception as error:
            with self.lock:
                self.stats['failed']+=1
                self.stats['consecutive_failures']+=1
                self.stats['last_error']=str(error)
                if ((self.stats['failed']>=30 and self.stats['succeeded']==0)
                    or self.stats['consecutive_failures']>=50):
                    self.stats['circuit_open']=True
                self.save()
            raise
        with self.lock:
            self.stats['succeeded']+=1
            self.stats['consecutive_failures']=0
            if self.stats['succeeded']%50==0:self.save()
        return result
    def save(self):
        p.atomic(self.checkpoint,{**self.stats,'recorded_at':p.now().isoformat()})

def output_root(argv):
    if '--output' in argv:return Path(argv[argv.index('--output')+1])
    return Path('data/alpha5d')

def main():
    root=output_root(sys.argv)
    guard=RetrievalGuard(ORIGINAL_DAILY,root/'retrieval_progress.json')
    cf.ThreadPoolExecutor=CancelPendingPool
    p.east_bars=guard
    try:
        fixes.main()
    except KeyboardInterrupt:
        file=root/'pipeline_status.json'
        data=json.loads(file.read_text()) if file.exists() else {}
        previous=data.get('previous_session')
        cached=list((root/'history'/str(previous)).glob('*.json')) if previous else []
        data.update(status='INCOMPLETE',error='Collection interrupted; pending requests cancelled; cached histories retained',
                    cached_history_files=len(cached),formal_top5_allowed=False,
                    runtime_version='alpha5d-runtime-1.0.0',ended_at=p.now().isoformat())
        p.atomic(file,data)
        raise SystemExit(2)
    finally:
        with guard.lock:guard.save()
        cf.ThreadPoolExecutor=ORIGINAL_POOL
        p.east_bars=ORIGINAL_DAILY

if __name__=='__main__':main()
