#!/usr/bin/env python3
"""Dated independent sources; no synthetic prices, amounts or exclusions."""
from __future__ import annotations
import datetime as dt
import html
import json
import math
import re
import subprocess
import threading
import urllib.parse as up
from pathlib import Path
import alpha5d_pipeline as p
import alpha5d_source_fixes as fixes
import alpha5d_classification as classification

VERSION='alpha5d-sources-1.1.1'
ROOT=Path('data/alpha5d')
PRIMARY_DAILY=None
LEGACY_INDUSTRIES=fixes.industries
INDUSTRY_AUDIT={}

class ProviderCircuits:
    def __init__(self,limit=8):
        self.limit=limit;self.lock=threading.Lock();self.stats={}
    def call(self,name,fn):
        with self.lock:
            row=self.stats.setdefault(name,dict(attempted=0,succeeded=0,failed=0,data_errors=0,consecutive_failures=0,circuit_open=False))
            if row['circuit_open']:raise RuntimeError(name+': route circuit open')
            row['attempted']+=1
        try:result=fn()
        except Exception as error:
            with self.lock:
                row['failed']+=1
                specific=isinstance(error,(ValueError,KeyError,TypeError,subprocess.CalledProcessError)) or bool(re.search(r'HTTP Error (?:400|404|410)',str(error)))
                if specific:row['data_errors']+=1;row['consecutive_failures']=0
                else:row['consecutive_failures']+=1
                row['circuit_open']=row['consecutive_failures']>=self.limit
            raise
        with self.lock:row['succeeded']+=1;row['consecutive_failures']=0
        return result
    def snapshot(self):
        with self.lock:return json.loads(json.dumps(self.stats))

CIRCUITS=ProviderCircuits()

def validate_history(history,asof):
    bars=history.get('bars') or []
    dates=[b.get('date') for b in bars]
    if not bars or dates!=sorted(set(dates)) or dates[-1]>asof:raise ValueError('Missing, unordered, duplicate or future history')
    if history.get('amount_unit')!='CNY' or history.get('volume_unit')!='lots_100_shares':raise ValueError('Unknown units')
    for b in bars:
        if any(not isinstance(b.get(k),(int,float)) or isinstance(b.get(k),bool) or not math.isfinite(b[k]) for k in ('open','close','high','low','volume','amount')):raise ValueError('Incomplete OHLCVA')
        if not 0<b['low']<=min(b['open'],b['close'])<=max(b['open'],b['close'])<=b['high']:raise ValueError('Invalid OHLC')
        if b['volume']<0 or b['amount']<0:raise ValueError('Negative activity')
    return history

def normalize_sina(rows,asof,factors=None):
    entries=sorted(factors or [],key=lambda r:r['d'],reverse=True)
    def factor(date):
        for entry in entries:
            if entry['d']<=date:
                value=float(entry['f'])
                if not math.isfinite(value) or value<=0:raise ValueError('Invalid factor')
                return value
        raise ValueError('Factor history does not cover date')
    anchor=factor(asof) if factors is not None else 1.
    result=[]
    for row in [x for x in rows if str(x.get('date',''))<=asof][-240:]:
        date=str(row['date']);prices=[float(row[k]) for k in ('open','close','high','low')]
        shares=float(row['volume']);amount=float(row['amount'])
        if not all(math.isfinite(v) for v in prices+[shares,amount]):raise ValueError('Invalid daily values')
        if shares>0 and not prices[3]*.98<=amount/shares<=prices[2]*1.02:raise ValueError('Amount/share unit mismatch')
        scale=anchor/factor(date) if factors is not None else 1.
        bar={'date':date,'volume':shares/100.,'amount':amount}
        bar.update({k:v*scale for k,v in zip(('open','close','high','low'),prices)})
        result.append(bar)
    return result

def sina_history(symbol,asof,adjust=1):
    if not re.fullmatch(r'(?:sh|sz)\d{6}',symbol):raise ValueError('Invalid identity')
    url='https://finance.sina.com.cn/realstock/company/'+symbol+'/hisdata_klc2/klc_kl.js'
    text=p.http(url)
    encoded=re.search(r'var\s+KLC_K2_'+re.escape(symbol)+r'\s*=\s*"([A-Za-z0-9+/=]+)"',text)
    if not encoded:raise ValueError('KLC2 identity missing')
    decoder=Path(__file__).parent/'vendor'/'sina_klc_decode.cjs'
    output=subprocess.run(['node',str(decoder)],input=json.dumps({'encoded':encoded.group(1),'asof':asof,'limit':240}),text=True,capture_output=True,check=True,timeout=12)
    rows=json.loads(output.stdout);factors=None
    factor_url='https://finance.sina.com.cn/realstock/company/'+symbol+'/qfq.js'
    if adjust==1:
        factors=p.json_body(p.http(factor_url)).get('data')
        if not isinstance(factors,list) or not factors:raise ValueError('Published factors missing')
    elif adjust!=0:raise ValueError('Unsupported adjustment')
    out={'bars':normalize_sina(rows,asof,factors),'source_url':url,'source_urls':[url]+([factor_url] if adjust else []),
         'retrieved_at':p.now().isoformat(),'adjustment_basis':('Sina_qfq' if adjust else 'Sina_raw')+'_anchor='+asof,
         'amount_unit':'CNY','volume_unit':'lots_100_shares','independent_amount':True,'decoder':'AKShare_hk_js_decode_MIT_KLC2'}
    return validate_history(out,asof)

def daily_history(symbol,asof,adjust=1,primary=None,sample=False):
    errors=[];fetch=primary or PRIMARY_DAILY;prefix='sample_' if sample else ''
    for name,fn in [(prefix+'eastmoney_daily',lambda:validate_history(fetch(symbol,asof,adjust),asof)),(prefix+'sina_klc2',lambda:sina_history(symbol,asof,adjust))]:
        try:
            out=CIRCUITS.call(name,fn);out['provider_route']=name;out['failed_routes']=errors;return out
        except Exception as error:errors.append({'route':name,'error':type(error).__name__+': '+str(error)})
    raise RuntimeError(symbol+': all independent daily routes failed: '+json.dumps(errors,ensure_ascii=False))

def independent_sample_bars(symbol,asof):return daily_history(symbol,asof,0,sample=True)

def sina_listing_info(symbol,asof):
    path=ROOT/'history'/'_listing_fallback'/p.now().date().isoformat()/(symbol+'.json')
    if path.exists():
        saved=json.loads(path.read_text())
        if saved.get('symbol')==symbol and saved.get('asof')==asof:return saved
    url='https://vip.stock.finance.sina.com.cn/corp/go.php/vCI_CorpInfo/stockid/'+symbol[2:]+'.phtml'
    text=p.http(url)
    if not re.search(r'<title>[^<]*'+re.escape(symbol[2:])+r'[^<]*</title>',text,re.I):raise ValueError('Listing identity mismatch')
    found=re.search(r'上市日期\s*[：:]?\s*(\d{4}-\d{2}-\d{2})',html.unescape(re.sub(r'<[^>]*>',' ',text)))
    if not found:raise ValueError('Published listing date missing')
    date=dt.date.fromisoformat(found.group(1));end=dt.date.fromisoformat(asof)
    sessions=999 if date.year<2026 else sum(p.trading(date+dt.timedelta(days=i)) for i in range(max(0,(end-date).days+1)))
    out={'symbol':symbol,'asof':asof,'date':date.isoformat(),'sessions':sessions,'source_url':url,'retrieved_at':p.now().isoformat()}
    p.atomic(path,out);return out

def listing_info(symbol,asof):
    try:return sina_listing_info(symbol,asof)
    except Exception as first:
        try:return classification.listing_info(symbol,asof)
        except Exception as second:raise RuntimeError('Both listing sources failed: '+str(first)+'; '+str(second))

def parse_session_halts(items,asof,rows,source_url):
    by_code={r['symbol'][2:]:r for r in rows};out={}
    for item in items:
        row=by_code.get(str(item.get('SECURITY_CODE','')).zfill(6))
        if row is None:continue
        start=str(item.get('SUSPEND_START_DATE') or '')[:10];end=str(item.get('SUSPEND_END_TIME') or '')[:10]
        if not start or start>asof or (end and end<asof):continue
        if float(row.get('volume') or 0)>0 or float(row.get('amount') or 0)>0:continue
        if str(item.get('DATETIME') or asof)[:10]!=asof:raise ValueError('Suspension date mismatch')
        out[row['symbol']]={'session':asof,'halt_from':start,'reason':item.get('SUSPEND_REASON'),'source_url':source_url,'provider_row':item,'retrieved_at':p.now().isoformat()}
    return out

def session_halts(asof,rows):
    params={'reportName':'RPT_CUSTOM_SUSPEND_DATA_INTERFACE','columns':'ALL','pageNumber':1,'pageSize':500,'source':'WEB','client':'WEB','filter':'(MARKET="全部")'+"(DATETIME='"+asof+"')"}
    items=[];urls=[];expected=None
    for page in range(1,21):
        params['pageNumber']=page;url='https://datacenter-web.eastmoney.com/api/data/v1/get?'+up.urlencode(params)
        obj=p.json_body(p.http(url));data=obj.get('result')
        if obj.get('success') is not True or not isinstance(data,dict):raise ValueError('Dated halt report unavailable: '+str(obj.get('message')))
        batch=data.get('data')
        if not isinstance(batch,list):raise ValueError('Halt rows missing')
        count=int(data.get('count',len(batch)));pages=int(data.get('pages') or 1)
        if expected is None:expected=count
        if count!=expected:raise ValueError('Halt count changed')
        items.extend(batch);urls.append(url)
        if page>=pages:break
    else:raise ValueError('Halt pagination over budget')
    if len(items)!=expected:raise ValueError('Halt report incomplete')
    out=parse_session_halts(items,asof,rows,urls[0]);audit={'security_date':asof,'source_urls':urls,'rows':len(items),'provider_rows':items,'whole_session_nontrading_codes':sorted(out),'complete':True}
    p.atomic(ROOT/'history'/'_halts'/(asof+'.json'),{'audit':audit,'records':out});return out,audit

def frozen_universe(asof):
    file=Path('data/universe_latest.json')
    if not file.is_file():return None
    data=json.loads(file.read_text());verified=data.get('frozen_verification') or {}
    if str(verified.get('security_date'))!=asof.replace('-',''):return None
    if data.get('stock_pick_gate')!='VERIFIED_CLOSED_DATASET' or not data.get('complete_pagination') or int(verified.get('passed_count') or 0)<20:raise ValueError('Frozen source not independently verified')
    captured=dt.datetime.fromisoformat(data['captured_at_beijing'])
    if captured>p.now():raise ValueError('Frozen source availability is in the future')
    raw_hash=p.sha(data)
    records=data['records'];records=list(records.values()) if isinstance(records,dict) else records
    if len(records)!=len({r['symbol'] for r in records}) or len(records)!=data['unique_code_count']:raise ValueError('Frozen raw counts disagree')
    lookup={r['symbol']:r for r in records}
    checks=verified.get('checks') or []
    if len({x['symbol'] for x in checks})<20:raise ValueError('Frozen checks are not inspectable')
    for check in checks:
        values=[float(check[k]) for k in ('sina_price','tencent_snapshot_price','daily_close')]
        if not check.get('passed') or check['security_date']!=asof.replace('-','') or min(values)<=0 or max(values)/min(values)-1>.005:raise ValueError('Frozen sample invalid')
        if abs(float(lookup[check['symbol']]['trade'])/values[0]-1)>.005:raise ValueError('Frozen sample/raw close disagree')
    cover={}
    for node,prefix in [('sh_a','sh'),('sz_a','sz')]:
        meta=data['nodes'][node];group=[r for r in records if r['symbol'].startswith(prefix)];last=meta['terminal_page']
        urls=[item['url'] for item in data.get('source_requests',[]) if up.parse_qs(up.urlparse(item['url']).query).get('node')==[node] and not item.get('errors')]
        if len(group)!=meta['raw_row_count'] or meta.get('missing_pages') or meta['successful_pages']!=list(range(1,last+1)) or len(urls)!=last:raise ValueError('Frozen pagination evidence incomplete')
        for row in group:row['_security_date']=asof
        cover[node]={'successful_pages':meta['successful_pages'],'terminal_page':last,'terminal_verified':True,'missing_pages':[],
                     'raw_rows':len(group),'page_counts':[100]*(last-1)+[len(group)-100*(last-1)],'source_urls':urls}
    audit={'security_date':asof,'captured_at':data['captured_at_beijing'],'sha256':raw_hash,'frozen_samples_verified':len(checks),'source':'data/universe_latest.json'}
    return records,cover,audit

def industries(workers):
    global INDUSTRY_AUDIT
    try:
        result,url=classification.industries(workers);INDUSTRY_AUDIT={'classification':'Eastmoney first-level industry','fallback':False,'source_url':url};return result,url
    except Exception as error:
        result,url=LEGACY_INDUSTRIES(workers)
        for row in result.values():row['classification']='Sina industry'
        INDUSTRY_AUDIT={'classification':'Sina industry; coherent whole-source fallback, not Shenwan','fallback':True,'primary_error':str(error),'source_url':url}
        return result,url

def install(primary_daily,root):
    global ROOT,PRIMARY_DAILY,CIRCUITS
    ROOT=Path(root);PRIMARY_DAILY=primary_daily;CIRCUITS=ProviderCircuits()
    p.independent_sample_bars=independent_sample_bars;p.listing_info=listing_info;p.session_halts=session_halts;p.frozen_universe=frozen_universe
    fixes.industries=industries;original=fixes.assemble
    def assemble(*args,**kwargs):
        payload,errors=original(*args,**kwargs);payload['industry_classification']=INDUSTRY_AUDIT.get('classification','UNVERIFIED')
        payload['industry_source_audit']=dict(INDUSTRY_AUDIT);payload['provider_route_health']=CIRCUITS.snapshot();return payload,errors
    fixes.assemble=assemble
