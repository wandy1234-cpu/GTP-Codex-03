#!/usr/bin/env python3
"""Resume incomplete pagination without mixing intraday or historical cutoffs."""
from __future__ import annotations
import datetime as dt
import json
import time
from pathlib import Path

ROOT=Path('data')
TZ=dt.timezone(dt.timedelta(hours=8))

def stable_window(captured,now):
    if captured.date()!=now.date(): return False
    c=captured.hour*60+captured.minute;n=now.hour*60+now.minute
    return (c>=900 and n>=900) or (690<=c<780 and 690<=n<780)

def main():
    import market_relay as core
    from market_audit_bridge import fetch_json,finite
    path=ROOT/'universe_latest.json'
    if not path.exists(): return
    obj=json.loads(path.read_text(encoding='utf8'))
    if obj.get('complete_pagination'): return
    now=dt.datetime.now(TZ)
    captured=dt.datetime.fromisoformat(obj['captured_at_beijing'])
    if not stable_window(captured,now):
        print('Pagination resume deferred: not the same stable frozen window.');return
    started=time.monotonic();records=obj.get('records',{});recovered=[]
    for node in ('sh_a','sz_a'):
        info=obj['nodes'][node]
        if info.get('terminal_page') and not info.get('missing_pages'): continue
        good=set(info.get('successful_pages',[]));missing=set(info.get('missing_pages',[]))
        first=min(missing) if missing else max(good,default=0)+1
        for page in range(first,101):
            if page in good: continue
            if time.monotonic()-started>100:
                missing.add(page);break
            url=('https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/'
                 f'Market_Center.getHQNodeData?page={page}&num=100&sort=symbol&asc=1&node={node}')
            rows,errors=fetch_json(core,url)
            obj.setdefault('source_requests',[]).append({'url':url,'errors':errors,'resumed':True})
            if not isinstance(rows,list):
                missing.add(page);break
            good.add(page);missing.discard(page)
            info['raw_row_count']=info.get('raw_row_count',0)+len(rows)
            recovered.append({'node':node,'page':page,'row_count':len(rows)})
            for row in rows:
                code=str(row.get('symbol',''))
                if code.startswith(('sh','sz')): records[code]=row
            if len(rows)<100:
                if info.get('raw_row_count'): info['terminal_page']=page
                else: missing.add(page)
                break
        info['successful_pages']=sorted(good);info['missing_pages']=sorted(missing)
        prefix='sh' if node=='sh_a' else 'sz'
        info['unique_count']=sum(s.startswith(prefix) for s in records)
    obj['records']=records;obj['unique_code_count']=len(records)
    obj['complete_pagination']=bool(all(n.get('terminal_page') and not n.get('missing_pages') for n in obj['nodes'].values())
        and sum(n.get('raw_row_count',0) for n in obj['nodes'].values())==len(records))
    keys=('symbol','name','trade','pricechange','changepercent','buy','sell','settlement','open','high','low','volume','amount','ticktime')
    obj['missing_fields']={k:sum(k not in r or r[k] is None for r in records.values()) for k in keys}
    # Re-stratify using the complete recovered universe; finalizer must verify all 20.
    samples=[]
    for prefix in ('sh','sz'):
        names=sorted([s for s,r in records.items() if s.startswith(prefix) and (finite(r.get('trade')) or 0)>0],
                     key=lambda s:finite(records[s].get('changepercent')) or 0)
        if len(names)>=10: samples.extend(names[round(i*(len(names)-1)/9)] for i in range(10))
    obj['stratified_checks']=[{'symbol':s,'refresh_required':True} for s in samples]
    obj['stock_pick_gate']='INCOMPLETE'
    obj['pagination_recovery']={'started_at_beijing':now.isoformat(),'completed_at_beijing':dt.datetime.now(TZ).isoformat(),
        'recovered_pages':recovered,'same_stable_window':True,'original_capture_time_preserved':True,
        'independent_frozen_checks_required':True}
    temp=path.with_suffix('.tmp');temp.write_text(json.dumps(obj,ensure_ascii=False,indent=2),encoding='utf8');temp.replace(path)
    print(json.dumps({'pagination_complete':obj['complete_pagination'],'unique_codes':len(records),'recovered_pages':recovered},ensure_ascii=False))

if __name__=='__main__': main()
