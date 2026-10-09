#!/usr/bin/env python3
"""Current industry classification adapter, separate from market prices.
Provider field mapping is documented by upstream akshare stock_info_em.py:
f127=industry, f189=listing date. Do not mix this scheme with SW or Sina.
"""
import concurrent.futures as cf
import datetime as dt
import json
import sys
import time
import urllib.parse as up
from pathlib import Path
import alpha5d_pipeline as p
import alpha5d_source_fixes as fixes

ORIGINAL_ASSEMBLE=fixes.assemble
HOSTS=('https://push2.eastmoney.com','https://82.push2.eastmoney.com','https://79.push2.eastmoney.com')

def root():
    return Path(sys.argv[sys.argv.index('--output')+1]) if '--output' in sys.argv else Path('data/alpha5d')

def request(path,params):
    errors=[]
    for host in HOSTS:
        url=host+path+'?'+up.urlencode(params)
        try:
            data=p.json_body(p.http(url)).get('data')
            if not data:raise ValueError('Empty provider data')
            return data,url
        except Exception as e:errors.append(str(e))
    raise RuntimeError('Industry/profile access failed: '+repr(errors))

def page_list(fs):
    rows=[];urls=[];expected=None;counts=[]
    for page in range(1,201):
        data,url=request('/api/qt/clist/get',{'pn':page,'pz':100,'po':0,'np':1,'fltt':2,'invt':2,
            'fid':'f12','fs':fs,'fields':'f12,f13,f14'})
        batch=data.get('diff')
        if isinstance(batch,dict):batch=list(batch.values())
        if not isinstance(batch,list) or (page==1 and not batch):raise ValueError('Empty industry first page')
        total=int(data.get('total',-1))
        if total<0:raise ValueError('Missing membership total')
        if expected is None:expected=total
        if total!=expected:raise ValueError('Industry membership total changed during retrieval')
        rows.extend(batch);urls.append(url);counts.append(len(batch))
        if len(rows)>=expected or len(batch)<100:break
    if len(rows)!=expected:raise ValueError('Industry membership page count incomplete')
    keys=[(str(r.get('f13','')),str(r['f12'])) for r in rows]
    if len(keys)!=len(set(keys)):raise ValueError('Industry membership pagination duplicate')
    return rows,{'source_urls':urls,'total':expected,'pages':page,'page_counts':counts,'observed_at':p.now().isoformat()}

def profile(symbol):
    file=root()/'history'/'_profiles'/p.now().date().isoformat()/(symbol+'.json')
    if file.exists():return json.loads(file.read_text())
    data,url=request('/api/qt/stock/get',{'secid':('1' if symbol.startswith('sh') else '0')+'.'+symbol[2:],
                  'fltt':2,'invt':2,'fields':'f57,f58,f127,f128,f189,f116'})
    if str(data.get('f57'))!=symbol[2:]:raise ValueError('Profile identity mismatch')
    out={'symbol':symbol,'name':data.get('f58'),'sector':data.get('f127'),
         'listing_date':data.get('f189'),'market_cap':data.get('f116'),
         'source_url':url,'retrieved_at':p.now().isoformat()}
    p.atomic(file,out);return out

def listing_info(symbol,asof):
    data=profile(symbol)
    date=dt.datetime.strptime(str(data['listing_date']),'%Y%m%d').date()
    if date.year<2026:sessions=999
    else:
        end=dt.date.fromisoformat(asof)
        sessions=sum(p.trading(date+dt.timedelta(days=i)) for i in range(max(0,(end-date).days+1)))
    return {'date':date.isoformat(),'sessions':sessions,'source_url':data['source_url'],'retrieved_at':data['retrieved_at']}

def industries(workers):
    cache=root()/'history'/'_em_industry'/p.now().date().isoformat()
    boards,board_audit=page_list('m:90 t:2 f:!50')
    result={};errors=[]
    def one(board):
        code,name=str(board['f12']),str(board['f14'])
        file=cache/(code+'.json')
        if file.exists():saved=json.loads(file.read_text());members,audit=saved['members'],saved['audit']
        else:
            members,audit=page_list('b:'+code+' f:!50')
            p.atomic(file,{'members':members,'audit':audit,'name':name})
        return name,members,audit
    with cf.ThreadPoolExecutor(max_workers=workers) as pool:
        for fut in cf.as_completed([pool.submit(one,b) for b in boards]):
            try:
                name,members,audit=fut.result()
                for r in members:
                    code=str(r['f12'])
                    if len(code)!=6 or code[0] not in '036':continue
                    sym=('sh' if str(r.get('f13'))=='1' or code.startswith('6') else 'sz')+code
                    fixes.merge_membership(result,sym,name,audit['source_urls'])
                    result[sym]['classification']='Eastmoney industry'
            except Exception as e:errors.append(str(e))
    if errors:raise ValueError('Current industry membership incomplete: '+repr(errors[:8]))
    # Fill genuinely unassigned companies from the SAME provider classification,
    # not a guessed label or a second, incompatible industry taxonomy.
    run_dirs=sorted((root()/'runs').glob('*'))
    if not run_dirs:raise ValueError('No source universe available for classification audit')
    rows=[]
    for node in ('sh_a','sz_a'):rows+=json.loads((run_dirs[-1]/(node+'.json')).read_text())['records']
    missing=[r['symbol'] for r in rows if r['symbol'] not in result and 'ST' not in r['name'].upper()]
    with cf.ThreadPoolExecutor(max_workers=workers) as pool:
        futures={pool.submit(profile,s):s for s in missing}
        for fut in cf.as_completed(futures):
            sym=futures[fut]
            try:
                info=fut.result();sector=info.get('sector')
                if not isinstance(sector,str) or sector in ('','-'):raise ValueError('No verified industry label')
                fixes.merge_membership(result,sym,sector,[info['source_url']])
                result[sym]['classification']='Eastmoney industry profile'
            except Exception as e:errors.append(sym+': '+str(e))
    if errors:raise ValueError('Unmapped industry profiles: '+repr(errors[:12]))
    return result,board_audit['source_urls'][0]

def assemble(*args,**kwargs):
    payload,errors=ORIGINAL_ASSEMBLE(*args,**kwargs)
    payload['industry_classification']='Eastmoney industry; not Shenwan or legacy Sina'
    return payload,errors

def install():
    fixes.industries=industries
    fixes.assemble=assemble
    p.listing_info=listing_info
