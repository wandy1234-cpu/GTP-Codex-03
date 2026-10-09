#!/usr/bin/env python3
"""Audited industry memberships with retries, same-day cache and rank invariance.
Only industry memberships may be deduplicated; SH/SZ universe remains strict.
"""
import copy
import itertools
import json
import math
import sys
import time
import urllib.parse as up
from pathlib import Path
import concurrent.futures as cf
import alpha5d_pipeline as p

BASE_ASSEMBLE=p.assemble
BASE_RANK=p.run_rank
CACHE_DIR=None

def merge_membership(result,symbol,name,urls):
    item=result.setdefault(symbol,{'sector_options':[],'source_urls':[]})
    item['sector_options']=sorted(set(item['sector_options']+[name]))
    item['source_urls']=sorted(set(item['source_urls']+urls))
    item['sector']=item['sector_options'][0]
    item['assignment_state']='UNIQUE' if len(item['sector_options'])==1 else 'REQUIRES_RANK_INVARIANCE'

def industry_pages(node):
    if node in ('sh_a','sz_a'):raise ValueError('Use strict universe paginator')
    cache=(CACHE_DIR/(node+'.json')) if CACHE_DIR else None
    if cache and cache.exists():
        saved=json.loads(cache.read_text())
        manifest=saved.get('manifest',{});terminal=manifest.get('terminal_page',0)
        if (saved.get('observed_date')==p.now().date().isoformat() and saved.get('records')
            and terminal and manifest.get('terminal_verified') and not manifest.get('missing_pages')
            and manifest.get('successful_pages')==list(range(1,terminal+1))):
            manifest['reused_cache_at']=p.now().isoformat()
            return saved['records'],manifest
    rows=[];pages=[];urls=[];counts=[];retries={}
    for page in range(1,201):
        url=p.SINA+'?'+up.urlencode({'node':node,'page':page,'num':100,'sort':'symbol','asc':1})
        batch=None
        for attempt in range(3):
            batch=p.json_body(p.http(url))
            if isinstance(batch,list) and (page!=1 or bool(batch)):break
            if attempt<2:time.sleep(.5*(attempt+1))
        if not isinstance(batch,list) or (page==1 and not batch):
            raise ValueError(node+': invalid industry page after payload retries: '+str(page))
        retries[str(page)]=attempt
        pages.append(page);urls.append(url);counts.append(len(batch));rows.extend(batch)
        if len(batch)<100:break
    else:raise ValueError(node+': no verified terminal page')
    unique={};duplicates={}
    for r in rows:
        sym=r['symbol']
        if sym in unique:
            if unique[sym].get('code')!=r.get('code') or unique[sym]['name']!=r['name']:
                raise ValueError(node+': conflicting identity for '+sym)
            duplicates[sym]=duplicates.get(sym,1)+1
        unique[sym]=r
    manifest={'successful_pages':pages,'terminal_page':page,'terminal_verified':True,'missing_pages':[],
              'raw_rows':len(rows),'unique_memberships':len(unique),'duplicate_memberships':duplicates,
              'page_counts':counts,'source_urls':urls,'payload_retries':retries,
              'observed_at':p.now().isoformat()}
    records=list(unique.values())
    if cache:p.atomic(cache,{'observed_date':p.now().date().isoformat(),'records':records,'manifest':manifest})
    return records,manifest

def industries(workers):
    url='https://vip.stock.finance.sina.com.cn/q/view/newSinaHy.php'
    obj=p.json_body(p.http(url))
    if not isinstance(obj,dict) or len(obj)<10:raise ValueError('Industry list incomplete')
    result={};errors=[]
    def one(item):
        parts=str(item[1]).split(',');rows,manifest=industry_pages(parts[0])
        return parts[1],rows,manifest
    with cf.ThreadPoolExecutor(max_workers=workers) as pool:
        for f in cf.as_completed([pool.submit(one,item) for item in obj.items()]):
            try:
                name,rows,manifest=f.result()
                for r in rows:
                    merge_membership(result,r['symbol'],name,manifest['source_urls'])
                    result[r['symbol']].setdefault('membership_page_audit',[]).append({
                        'node':str(manifest['source_urls'][0]).split('node=',1)[-1].split('&',1)[0],
                        'raw_rows':manifest['raw_rows'],'unique_memberships':manifest['unique_memberships'],
                        'duplicate_memberships':manifest['duplicate_memberships'],
                        'observed_at':manifest.get('observed_at'),'reused_cache_at':manifest.get('reused_cache_at')})
            except Exception as e:errors.append(str(e))
    if errors:raise ValueError('Industry pages failed: '+repr(errors[:5]))
    return result,url

def assemble(rows,cover,sectors,histories,quotes,asof,forecast,cutoff,generated_at):
    payload,errors=BASE_ASSEMBLE(rows,cover,sectors,histories,quotes,asof,forecast,cutoff,generated_at)
    payload['industry_classification']='Sina industry memberships; ambiguity explicitly enumerated'
    for r in payload['universe']:
        industry=sectors.get(r['symbol'],{})
        r['sector_options']=industry.get('sector_options',[])
        r['sector_source_urls']=industry.get('source_urls',[])
        r['source_urls']=sorted(set(r['source_urls']+r['sector_source_urls']))
    return payload,errors

def robust_rank(engine,payload):
    baseline=BASE_RANK(engine,payload)
    if baseline.get('ranking_health')!='COMPLETE':return baseline
    if not baseline.get('eligible_codes'):raise ValueError('Missing eligible-code manifest')
    eligible=set(baseline['eligible_codes'])
    uncertain=[r for r in payload['universe'] if r['symbol'] in eligible and len(r.get('sector_options',[]))>1]
    count=math.prod(len(r['sector_options']) for r in uncertain)
    audit={'ambiguous_memberships':{r['symbol']:r['sector_options'] for r in uncertain},
           'scenarios_required':count,'scenarios_verified':0,'top5_order_invariant':False}
    baseline['industry_assignment_audit']=audit
    if count>16:
        baseline.update(ranking_health='INCOMPLETE',formal_top5_allowed=False,top5=[])
        baseline['errors'].append('Industry ambiguity exceeds exhaustive scenario budget')
        return baseline
    expected=[r['symbol'] for r in baseline['top5']]
    ranges={r['symbol']:[r['score'],r['score']] for r in baseline['top5']}
    for values in itertools.product(*(r['sector_options'] for r in uncertain)):
        variant=copy.deepcopy(payload);mapping=dict(zip((r['symbol'] for r in uncertain),values))
        for r in variant['universe']:
            if r['symbol'] in mapping:r['sector']=mapping[r['symbol']]
        result=BASE_RANK(engine,variant)
        actual=[r['symbol'] for r in result.get('top5',[])]
        if result.get('ranking_health')!='COMPLETE' or actual!=expected:
            baseline.update(ranking_health='INCOMPLETE',formal_top5_allowed=False,top5=[])
            baseline['errors'].append('Top5 changes under verified alternative industry assignment')
            audit['counterexample_assignment']=mapping
            return baseline
        audit['scenarios_verified']+=1
        for r in result['top5']:
            z=ranges[r['symbol']];z[0]=min(z[0],r['score']);z[1]=max(z[1],r['score'])
    audit['top5_order_invariant']=True
    audit['selected_score_ranges']=ranges
    return baseline

def main():
    global CACHE_DIR
    root=Path(sys.argv[sys.argv.index('--output')+1]) if '--output' in sys.argv else Path('data/alpha5d')
    CACHE_DIR=root/'history'/'_industry'/p.now().date().isoformat()
    p.VERSION='alpha5d-pipeline-1.0.3'
    p.CALENDAR_SOURCE='https://www.sse.com.cn/disclosure/announcement/general/c/c_20260915_10832273.shtml'
    p.industries=industries;p.assemble=assemble;p.run_rank=robust_rank
    p.main()

if __name__=='__main__':main()
