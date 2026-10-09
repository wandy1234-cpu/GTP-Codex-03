#!/usr/bin/env python3
"""Preserve all observed industry memberships and prove Top5 invariance.
A conflicting source label must not be silently dropped or selected to boost a
stock. Enumerate every observed assignment (maximum16) using the unchanged
score model. Differing Top5 ordering fails acceptance. This is an input
uncertainty audit, not a new predictive signal or a trained model.
"""
import copy
import itertools
import math
import concurrent.futures as cf
import alpha5d_pipeline as p

BASE_ASSEMBLE=p.assemble
BASE_RANK=p.run_rank

def merge_membership(result, symbol, name, urls):
    item=result.setdefault(symbol,{'sector_options':[],'source_urls':[]})
    item['sector_options']=sorted(set(item['sector_options']+[name]))
    item['source_urls']=sorted(set(item['source_urls']+urls))
    item['sector']=item['sector_options'][0]
    item['assignment_state']='UNIQUE' if len(item['sector_options'])==1 else 'REQUIRES_RANK_INVARIANCE'

def industries(workers):
    url='https://vip.stock.finance.sina.com.cn/q/view/newSinaHy.php'
    obj=p.json_body(p.http(url))
    if not isinstance(obj,dict) or len(obj)<10:raise ValueError('Industry list incomplete')
    result={};errors=[]
    def one(item):
        parts=str(item[1]).split(',');rows,manifest=p.paged(parts[0])
        return parts[1],rows,manifest
    with cf.ThreadPoolExecutor(max_workers=workers) as pool:
        for f in cf.as_completed([pool.submit(one,item) for item in obj.items()]):
            try:
                name,rows,manifest=f.result()
                for r in rows:merge_membership(result,r['symbol'],name,manifest['source_urls'])
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
    eligible=set(baseline.get('eligible_codes',[]))
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
    p.VERSION='alpha5d-pipeline-1.0.1'
    p.CALENDAR_SOURCE='https://www.sse.com.cn/disclosure/announcement/general/c/c_20260915_10832273.shtml'
    p.industries=industries;p.assemble=assemble;p.run_rank=robust_rank
    p.main()

if __name__=='__main__':main()
