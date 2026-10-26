#!/usr/bin/env python3
"""Coherent Eastmoney first-level industry classification, not mixed levels.
The provider upgraded to a three-level 496-board taxonomy in February 2026.
Never interpret all 496 boards as alternative peer industry assignments.
"""
import concurrent.futures as cf
import datetime as dt
import json
import sys
import urllib.parse as up
from pathlib import Path
import alpha5d_pipeline as p
import alpha5d_source_fixes as fixes

ORIGINAL_ASSEMBLE=fixes.assemble
# Actual cloud probe 37874129625: primary host TLS times out; 82/79 respond.
HOSTS=('https://82.push2.eastmoney.com','https://79.push2.eastmoney.com','https://push2.eastmoney.com')
FIRST_LEVEL=set('农林牧渔 基础化工 钢铁 有色金属 电子 家用电器 食品饮料 纺织服饰 轻工制造 医药生物 公用事业 交通运输 房地产 商贸零售 社会服务 综合 建筑材料 建筑装饰 电力设备 国防军工 计算机 传媒 通信 银行 非银金融 汽车 机械设备 煤炭 石油石化 环保 美容护理'.split())
TAXONOMY_SOURCE='https://caifuhao.eastmoney.com/news/20260212141558284153090'

class IndustryRequestError(RuntimeError):
    def __init__(self,errors):
        self.source_errors=errors
        super().__init__('Industry/profile access failed: '+repr(errors))

def root():
    return Path(sys.argv[sys.argv.index('--output')+1]) if '--output' in sys.argv else Path('data/alpha5d')

def request(path,params):
    errors=[]
    stage=('industry_catalog' if params.get('fs')=='m:90 t:2 f:!50' else
           'industry_membership' if path=='/api/qt/clist/get' else 'profile')
    for host in HOSTS:
        url=host+path+'?'+up.urlencode(params)
        try:
            data=p.json_body(p.http(url)).get('data')
            if not data:raise ValueError('Empty provider data')
            return data,url
        except Exception as e:
            errors.append({'stage':stage,'url':url,'host':host,
                           'referer':p.request_headers(url)['Referer'],
                           'page':params.get('pn'),'filter':params.get('fs'),
                           'secid':params.get('secid'),'error_type':type(e).__name__,
                           'error':str(e),'observed_at':p.now().isoformat()})
    raise IndustryRequestError(errors)

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

def select_first_level(boards):
    selected=[b for b in boards if b['f14'] in FIRST_LEVEL]
    names=[b['f14'] for b in selected]
    if len(selected)!=31 or set(names)!=FIRST_LEVEL:
        raise ValueError('First-level taxonomy not fully verified: missing='+repr(sorted(FIRST_LEVEL-set(names))))
    return selected

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
    cache=root()/'history'/'_em_industry_l1'/p.now().date().isoformat()
    all_boards,board_audit=page_list('m:90 t:2 f:!50')
    boards=select_first_level(all_boards)
    p.atomic(cache/'taxonomy_audit.json',{'all_catalog_count':len(all_boards),'selected':boards,
               'source':TAXONOMY_SOURCE,'catalog':board_audit})
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
                    result[sym]['classification']='Eastmoney level1'
            except Exception as e:errors.append(str(e))
    if errors:raise ValueError('Current level1 industry membership incomplete: '+repr(errors[:8]))
    # Missing labels stay missing; base input validation names each affected
    # company instead of mixing a leaf profile into a first-level taxonomy.
    return result,board_audit['source_urls'][0]

def assemble(*args,**kwargs):
    payload,errors=ORIGINAL_ASSEMBLE(*args,**kwargs)
    payload['industry_classification']='Eastmoney first-level industry; not Shenwan or legacy Sina'
    payload['industry_taxonomy_source']=TAXONOMY_SOURCE
    return payload,errors

def install():
    fixes.industries=industries
    fixes.assemble=assemble
    p.listing_info=listing_info
