#!/usr/bin/env python3
"""Bound public-data retrieval, memory and actual diagnostic checkpoints.
No new scoring features, eligibility exclusions or trading operations.
"""
import concurrent.futures as cf
import datetime as dt
import json
import sys
import threading
import urllib.parse as up
from pathlib import Path
import alpha5d_pipeline as p
import alpha5d_source_fixes as fixes
import alpha5d_classification as classification

ORIGINAL_POOL=cf.ThreadPoolExecutor
ORIGINAL_DAILY=p.east_bars

def compact_history(result):
    symbol,history=result
    history['bars']=history['bars'][-240:]
    history['retained_bars']=len(history['bars'])
    return symbol,history

def bounded_daily(symbol,asof,adjust=1):
    first=(dt.date.fromisoformat(asof)-dt.timedelta(days=400)).strftime('%Y%m%d')
    params={'secid':('1' if symbol.startswith('sh') else '0')+'.'+symbol[2:],
      'klt':101,'fqt':adjust,'beg':first,'end':asof.replace('-',''),'lmt':240,
      'fields1':'f1,f2,f3,f4,f5,f6','fields2':'f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61'}
    url='https://push2his.eastmoney.com/api/qt/stock/kline/get?'+up.urlencode(params)
    data=p.json_body(p.http(url)).get('data') or {}
    if str(data.get('code',''))!=symbol[2:]:raise ValueError(symbol+': kline identity mismatch')
    bars=[]
    for text in data.get('klines') or []:
        v=text.split(',')
        if len(v)<7:raise ValueError('OHLCVA fields missing')
        if v[0]>asof:raise ValueError('Future bar from provider')
        bars.append({'date':v[0],'open':float(v[1]),'close':float(v[2]),'high':float(v[3]),
                     'low':float(v[4]),'volume':float(v[5]),'amount':float(v[6])})
    if len(bars)<61:
        return compact_history((symbol,ORIGINAL_DAILY(symbol,asof,adjust)))[1]
    return {'bars':bars[-240:],'source_url':url,'retrieved_at':p.now().isoformat(),
            'adjustment_basis':f'Eastmoney_fqt={adjust}_end={asof}',
            'amount_unit':'CNY','volume_unit':'lots_100_shares','retained_bars':min(240,len(bars))}

class CancelPendingPool(ORIGINAL_POOL):
    def submit(self,fn,*args,**kwargs):
        if getattr(fn,'__name__','')=='load':
            def loaded(*a,**kw):return compact_history(fn(*a,**kw))
            return super().submit(loaded,*args,**kwargs)
        return super().submit(fn,*args,**kwargs)
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
    guard=RetrievalGuard(bounded_daily,root/'retrieval_progress.json')
    cf.ThreadPoolExecutor=CancelPendingPool
    p.east_bars=guard
    classification.install()
    try:
        fixes.main()
    except KeyboardInterrupt:
        file=root/'pipeline_status.json'
        data=json.loads(file.read_text()) if file.exists() else {}
        previous=data.get('previous_session')
        cached=list((root/'history'/str(previous)).glob('*.json')) if previous else []
        data.update(status='INCOMPLETE',error='Collection interrupted; pending requests cancelled; cached histories retained',
                    cached_history_files=len(cached),formal_top5_allowed=False,
                    runtime_version='alpha5d-runtime-1.0.2',ended_at=p.now().isoformat())
        p.atomic(file,data)
        raise SystemExit(2)
    finally:
        with guard.lock:guard.save()
        cf.ThreadPoolExecutor=ORIGINAL_POOL
        p.east_bars=ORIGINAL_DAILY

if __name__=='__main__':main()
