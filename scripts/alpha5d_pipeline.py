#!/usr/bin/env python3
"""Public-market input adapter for alpha5d_engine; never reads personal portfolios.
Sina complete universe + Sina industry membership + Eastmoney completed OHLCVA.
Missing market data fails closed. Data errors are not eligibility exclusions.
"""
from __future__ import annotations
import argparse, concurrent.futures as cf, datetime as dt, hashlib, json, math
import os, re, statistics, time, urllib.parse as up, urllib.request as ur
from pathlib import Path
from zoneinfo import ZoneInfo

TZ = ZoneInfo('Asia/Shanghai')
VERSION = 'alpha5d-pipeline-1.0.0'
CALENDAR_SOURCE = 'https://www.sse.com.cn/disclosure/announcement/general/c/c_20251222_10802507.shtml'
HOLIDAYS_2026 = [('2026-01-01','2026-01-03'),('2026-02-15','2026-02-23'),
 ('2026-04-04','2026-04-06'),('2026-05-01','2026-05-05'),('2026-06-19','2026-06-21'),
 ('2026-09-25','2026-09-27'),('2026-10-01','2026-10-07')]
SINA = 'https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/Market_Center.getHQNodeData'
HEADERS = {'User-Agent':'Mozilla/5.0','Referer':'https://finance.sina.com.cn/'}

def now(): return dt.datetime.now(TZ)
def sha(obj): return hashlib.sha256(json.dumps(obj,ensure_ascii=False,sort_keys=True,allow_nan=False).encode()).hexdigest()
def trading(d):
    if d.year != 2026: raise ValueError('Calendar year not verified; refresh official calendar')
    return d.weekday()<5 and not any(a<=d.isoformat()<=b for a,b in HOLIDAYS_2026)
def step(d, direction):
    d += dt.timedelta(days=direction)
    while not trading(d): d += dt.timedelta(days=direction)
    return d

def forecast_sessions(d):
    if not trading(d): raise ValueError('Forecast date is not a verified trading day')
    result=[d.isoformat()]
    for _ in range(4): d=step(d,1);result.append(d.isoformat())
    return result

def previous_close_day(t):
    d=t.date()
    return d if trading(d) and t.time()>=dt.time(15,15) else step(d,-1)

def atomic(path, obj):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_suffix(path.suffix+'.tmp')
    tmp.write_text(json.dumps(obj,ensure_ascii=False,indent=2,allow_nan=False)+'\n',encoding='utf8')
    tmp.replace(path)

def http(url):
    error=None
    for attempt in range(2):
        try:
            req=ur.Request(url,headers=HEADERS)
            with ur.urlopen(req,timeout=12) as r: body=r.read(12_000_000)
            text=body.decode('utf-8-sig') if b'charset=gb' not in body[:100].lower() else body.decode('gb18030')
            if '\ufffd' in text: raise ValueError('decoding failure')
            return text
        except UnicodeDecodeError:
            return body.decode('gb18030')
        except Exception as e:
            error=e
            if attempt==0: time.sleep(.4)
    raise RuntimeError(f'{type(error).__name__}: {error}')

def json_body(text):
    text=text.strip()
    if text.startswith(('var ','let ','const ')):text=text.split('=',1)[1].strip().rstrip(';')
    return json.loads(text)

def paged(node):
    records=[];pages=[];urls=[];counts=[]
    for page in range(1,201):
        url=SINA+'?'+up.urlencode({'node':node,'page':page,'num':100,'sort':'symbol','asc':1})
        batch=json_body(http(url))
        if not isinstance(batch,list) or (page==1 and not batch): raise ValueError(node+': invalid first page')
        pages.append(page);urls.append(url);counts.append(len(batch));records.extend(batch)
        if len(batch)<100: break
    else: raise ValueError(node+': no terminal page')
    symbols=[r['symbol'] for r in records]
    if len(set(symbols)) != len(symbols):raise ValueError(node+': duplicate pagination')
    return records,{'successful_pages':pages,'terminal_page':page,'terminal_verified':True,
        'missing_pages':[],'raw_rows':len(records),'page_counts':counts,'source_urls':urls}

def industries(workers):
    url='https://vip.stock.finance.sina.com.cn/q/view/newSinaHy.php'
    text=http(url); obj=json_body(text)
    if not isinstance(obj,dict) or len(obj)<10:raise ValueError('Industry list incomplete')
    result={}; errors=[]
    def one(item):
        key,value=item; parts=str(value).split(','); node=parts[0];name=parts[1]
        rows,manifest=paged(node)
        return name,rows,manifest
    with cf.ThreadPoolExecutor(max_workers=workers) as pool:
        for f in cf.as_completed([pool.submit(one,item) for item in obj.items()]):
            try:
                name,rows,manifest=f.result()
                for r in rows:
                    sym=r['symbol']
                    if sym in result and result[sym]['sector']!=name:raise ValueError('Conflicting industry: '+sym)
                    result[sym]={'sector':name,'source_urls':manifest['source_urls']}
            except Exception as e:errors.append(str(e))
    if errors:raise ValueError('Industry pages failed: '+repr(errors[:5]))
    return result,url

def east_bars(symbol, asof, adjust=1):
    params={'secid':('1' if symbol.startswith('sh') else '0')+'.'+symbol[2:],
      'klt':101,'fqt':adjust,'beg':'0','end':asof.replace('-',''),'lmt':240,
      'fields1':'f1,f2,f3,f4,f5,f6','fields2':'f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61'}
    url='https://push2his.eastmoney.com/api/qt/stock/kline/get?'+up.urlencode(params)
    obj=json_body(http(url)); data=obj.get('data') or {}
    if str(data.get('code',''))!=symbol[2:]:raise ValueError(symbol+': kline identity mismatch')
    bars=[]
    for text in data.get('klines') or []:
        v=text.split(',')
        if len(v)<7:raise ValueError('OHLCVA fields missing')
        if v[0]>asof:raise ValueError('Future bar from provider')
        bars.append({'date':v[0],'open':float(v[1]),'close':float(v[2]),'high':float(v[3]),
                     'low':float(v[4]),'volume':float(v[5]),'amount':float(v[6])})
    if not bars:raise ValueError(symbol+': empty daily history')
    return {'bars':bars,'source_url':url,'retrieved_at':now().isoformat(),
            'adjustment_basis':f'Eastmoney_fqt={adjust}_end={asof}',
            'amount_unit':'CNY','volume_unit':'lots_100_shares'}

def listing_info(symbol, asof):
    url='https://push2.eastmoney.com/api/qt/stock/get?'+up.urlencode({
        'secid':('1' if symbol.startswith('sh') else '0')+'.'+symbol[2:],
        'fields':'f26,f57,f58'})
    data=json_body(http(url)).get('data') or {}
    if str(data.get('f57'))!=symbol[2:]: raise ValueError('Listing profile identity mismatch')
    date=dt.datetime.strptime(str(data['f26']),'%Y%m%d').date()
    if date.year<2026:return {'date':date.isoformat(),'sessions':999,'source_url':url}
    end=dt.date.fromisoformat(asof)
    sessions=sum(trading(date+dt.timedelta(days=i)) for i in range(max(0,(end-date).days+1)))
    return {'date':date.isoformat(),'sessions':sessions,'source_url':url}

def tencent(symbols):
    url='https://qt.gtimg.cn/q='+','.join(symbols)
    with ur.urlopen(ur.Request(url,headers=HEADERS),timeout=12) as r:text=r.read().decode('gb18030')
    result={}
    for sym,body in re.findall(r'v_(\w+)="([^"]*)"',text):
        f=body.split('~')
        if len(f)>34:result[sym]={'code':f[2],'name':f[1],'price':float(f[3]),
                                'previous':float(f[4]),'stamp':f[30],'source_url':url}
    return result

def board(sym):
    return 'star' if sym.startswith('sh688') else 'sh_main' if sym.startswith('sh') else 'chinext' if sym.startswith('sz30') else 'sz_main'

def reference(sina, qq, asof):
    d=dt.datetime.strptime(qq['stamp'][:8],'%Y%m%d').date()
    target=dt.date.fromisoformat(asof)
    if d==target:return float(sina['trade']),qq['price']
    if d==step(target,1):return float(sina['settlement']),qq['previous']
    raise ValueError('Quote security date incompatible with required close')

def sample_codes(rows):
    result=[]
    for label in ('sh_main','star','sz_main','chinext'):
        group=sorted((r for r in rows if board(r['symbol'])==label and 'ST' not in r['name'].upper()),
                     key=lambda r:float(r.get('changepercent') or 0))
        if len(group)<5:raise ValueError('Missing sample board '+label)
        result += [group[round(i*(len(group)-1)/4)]['symbol'] for i in range(5)]
    return result

def assemble(rows, cover, sector_map, histories, quotes, asof, forecast, cutoff, generated_at):
    errors=[]; normalized=[]; codes=sample_codes(rows); by_code={r['symbol']:r for r in rows}
    samples=[]
    for symbol in codes:
        try:
            q=quotes[symbol];p,t=reference(by_code[symbol],q,asof)
            h=east_bars(symbol,asof,0)
            daily=h['bars'][-1]
            if daily['date']!=asof:raise ValueError('Missing final sample bar')
            if max(p,t,daily['close'])/min(p,t,daily['close'])-1>.005:raise ValueError('Close mismatch')
            samples.append({'symbol':symbol,'board':board(symbol),'security_date':asof,
              'sina_close':p,'tencent_close':t,'daily_close':daily['close'],
              'identity_matches':q['code']==symbol[2:],'source_urls':[q['source_url'],h['source_url']]})
        except Exception as e:errors.append('sample '+symbol+': '+str(e))
    dates={q['stamp'][:8] for q in quotes.values()}
    target=dt.date.fromisoformat(asof)
    allowed={target.strftime('%Y%m%d'),step(target,1).strftime('%Y%m%d')}
    if not dates or not dates<=allowed:errors.append('Quote date regime unverified')
    for r in rows:
        symbol=r['symbol']; h=histories.get(symbol)
        record={'symbol':symbol,'name':r['name'],'eligible':True,'sector':sector_map.get(symbol,{}).get('sector'),
            'asof_date':asof,'available_at':generated_at,'retrieved_at':generated_at,
            'source_urls':cover['sh_a' if symbol.startswith('sh') else 'sz_a']['source_urls'],
            'amount_unit':'CNY','raw_close':None,'adjustment_basis':None,'bars':[]}
        if 'ST' in r['name'].upper():
            record.update(eligible=False,exclude_reason='st',exclusion_evidence='Sina current name: '+r['name'])
        elif h is None:errors.append(symbol+': daily history not available')
        else:
            record.update(bars=h['bars'],adjustment_basis=h['adjustment_basis'])
            record['source_urls'] = list(record['source_urls']) + [h['source_url']]
            if h['bars'][-1]['date']!=asof:errors.append(symbol+': last daily bar stale (suspension not yet independently verified)')
            if len(h['bars'])<61:
                try:
                    ipo=listing_info(symbol,asof)
                    if ipo['sessions']<60:
                        record.update(eligible=False,exclude_reason='listing_lt_60_sessions',exclusion_evidence=ipo)
                    else:errors.append(symbol+': fewer than61 bars for established listing')
                except Exception as e:errors.append(symbol+': listing status unverified: '+str(e))
            if len(dates)==1:
                record['raw_close']=float(r['trade'] if target.strftime('%Y%m%d') in dates else r['settlement'])
            else:errors.append(symbol+': mixed quote dates require per-symbol snapshot')
            if record['eligible'] and not record['sector']:errors.append(symbol+': missing industry membership')
        normalized.append(record)
    coverage={**cover,'unique_count':len(rows),'samples':samples}
    return {'forecast_date':forecast,'previous_session':asof,'cutoff':cutoff,
      'generated_at':generated_at,'run_kind':'late_research' if generated_at>forecast+'T08:50:00+08:00' else 'scheduled',
      'data_commit':os.getenv('GITHUB_SHA','local-research'),'source_urls':[SINA,CALENDAR_SOURCE],
      'coverage':coverage,'universe':normalized,'adapter_errors':errors},errors

def run_rank(engine, payload):
    """Permit an explicitly labelled late re-run without weakening data gates.
    The historical 08:50 batch is never overwritten or presented as on-time.
    Scoring formulas are unchanged. Only the exact clock-policy error can clear.
    """
    original=engine.validate
    def check(data):
        errors,good,excluded=original(data)
        if data.get('run_kind')=='late_research':
            c=dt.datetime.fromisoformat(data['cutoff']).astimezone(TZ)
            g=dt.datetime.fromisoformat(data['generated_at']).astimezone(TZ)
            allowed=(c.date().isoformat()==data['forecast_date'] and c.time()>=dt.time(8,50)
                     and g>=c and g<=now()+dt.timedelta(seconds=30))
            if allowed:
                errors=[e for e in errors if e!='cutoff must be forecast-date 08:50:00 Asia/Shanghai']
            else:errors.append('Invalid late-research actual timestamp')
        return errors,good,excluded
    engine.validate=check
    try:result=engine.rank(payload)
    finally:engine.validate=original
    result['adapter_version']=VERSION
    result['generated_at']=payload['generated_at']
    result['run_kind']=payload.get('run_kind')
    result['is_0850_live_prediction']=False if payload.get('run_kind')=='late_research' else None
    return result

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--forecast');ap.add_argument('--workers',type=int,default=8)
    ap.add_argument('--output',default='data/alpha5d')
    args=ap.parse_args();started=now();root=Path(args.output)
    fday=dt.date.fromisoformat(args.forecast) if args.forecast else (step(started.date(),1) if started.time()>=dt.time(15,15) else started.date())
    if not trading(fday):
        atomic(root/'pipeline_status.json',{'status':'NOT_TRADING_DAY','date':fday.isoformat(),'generated_at':started.isoformat()});return
    asof=step(fday,-1).isoformat();forecast=fday.isoformat();run_id=started.strftime('%Y%m%dT%H%M%S')
    run_dir=root/'runs'/run_id; manifest={'adapter_version':VERSION,'started_at':started.isoformat(),
       'forecast_date':forecast,'previous_session':asof,'sessions':forecast_sessions(fday),
       'calendar_source':CALENDAR_SOURCE,'status':'RUNNING','run_kind':'late_research' if started.time()>dt.time(8,50) else 'prefetch'}
    atomic(root/'pipeline_status.json',manifest)
    try:
        rows=[];cover={}
        for node in ('sh_a','sz_a'):
            batch,g=paged(node);rows+=batch;cover[node]=g;atomic(run_dir/(node+'.json'),{'records':batch,'manifest':g})
        if len(rows)!=len({r['symbol'] for r in rows}):raise ValueError('Cross-exchange duplicate')
        manifest.update(raw_count=len(rows),coverage=cover)
        industry,url=industries(args.workers);atomic(run_dir/'industry.json',{'source':url,'memberships':industry})
        histories={};history_errors={}
        targets=[r for r in rows if 'ST' not in r['name'].upper()]
        def load(r):
            sym=r['symbol'];path=root/'history'/asof/(sym+'.json')
            if path.exists():
                h=json.loads(path.read_text());
                if h.get('bars') and h['bars'][-1]['date']==asof:return sym,h
            h=east_bars(sym,asof);atomic(path,h);return sym,h
        with cf.ThreadPoolExecutor(max_workers=args.workers) as pool:
            futures={pool.submit(load,r):r['symbol'] for r in targets}
            for f in cf.as_completed(futures):
                sym=futures[f]
                try:_,histories[sym]=f.result()
                except Exception as e:history_errors[sym]=str(e)
        manifest.update(history_success=len(histories),history_missing=history_errors)
        quotes=tencent(sample_codes(rows))
        actual=now().isoformat();cutoff=max(forecast+'T08:50:00+08:00',actual) if started.date()==fday else forecast+'T08:50:00+08:00'
        payload,errors=assemble(rows,cover,industry,histories,quotes,asof,forecast,cutoff,actual)
        atomic(run_dir/'input.json',payload)
        if errors:raise ValueError('INPUT_INCOMPLETE: '+repr(errors[:20]))
        import alpha5d_engine as engine
        result=run_rank(engine,payload);atomic(run_dir/'ranking.json',result)
        manifest.update(status=result['ranking_health'],input_sha256=sha(payload),
                        ranking_path=str(run_dir/'ranking.json'),ranker_version=engine.VERSION,
                        top5=result.get('top5',[]),errors=result.get('errors',[]))
        manifest['publication_state']='AWAITING_EVIDENCE_REVIEW'
        manifest['formal_top5_allowed']=False
    except Exception as e:
        manifest.update(status='INCOMPLETE',error=f'{type(e).__name__}: {e}',formal_top5_allowed=False)
    finally:
        manifest['ended_at']=now().isoformat();atomic(run_dir/'receipt.json',manifest)
        atomic(root/'pipeline_status.json',manifest)
        print(json.dumps({k:manifest.get(k) for k in ('status','raw_count','history_success','error','publication_state')},ensure_ascii=False))
    if manifest['status']!='COMPLETE':raise SystemExit(2)

if __name__=='__main__':main()
