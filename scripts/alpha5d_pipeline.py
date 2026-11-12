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
def sha(obj): return hashlib.sha256(json.dumps(obj,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()
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
            headers=dict(HEADERS);host=up.urlparse(url).hostname or ''
            if host.endswith('.eastmoney.com'):headers['Referer']='https://quote.eastmoney.com/'
            elif host.endswith('.sse.com.cn'):headers['Referer']='https://www.sse.com.cn/'
            elif host.endswith('.gtimg.cn') or host.endswith('.qq.com'):headers['Referer']='https://gu.qq.com/'
            req=ur.Request(url,headers=headers)
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

def verified_market_minima(previous_session):
    """Find a verified coverage floor; never certify the requested date from stale data.

    If the target close snapshot is incomplete, a recent independently verified
    earlier session is only a *minimum count floor* for fresh same-date paging,
    not a replacement market input. Downstream 20 samples and dated bars still
    must pass for the requested session.
    """
    requested=dt.date.fromisoformat(previous_session)
    candidates=[Path('data/alpha5d/market')/(previous_session+'.json'),
                Path('data/universe_summary.json')]
    # A relay can publish an incomplete target snapshot (e.g. 19/20 checks).
    # Recover with the nearest previous valid close from no more than 3
    # actual exchange sessions; an older floor must not silently pass.
    archive=Path('data/alpha5d/market')
    if archive.is_dir():
        for path in sorted(archive.glob('????-??-??.json'),reverse=True):
            if path not in candidates:candidates.append(path)
    for path in candidates:
        if not path.is_file():continue
        try:
            baseline=json.loads(path.read_text(encoding='utf8'))
            frozen=baseline.get('frozen_verification') or {}
            security=dt.date.fromisoformat(str(frozen.get('security_date',''))[:4]+'-'+str(frozen.get('security_date',''))[4:6]+'-'+str(frozen.get('security_date',''))[6:8])
            if security>requested:continue
            d=security;age=0
            while d<requested and age<=3:
                d=step(d,1);age+=1
            if age>3 or d!=requested:continue
            if (baseline.get('stock_pick_gate')!='VERIFIED_CLOSED_DATASET'
                    or not baseline.get('complete_pagination')
                    or int(frozen.get('passed_count') or 0)<20):
                continue
            minima={}
            for node in ('sh_a','sz_a'):
                meta=(baseline.get('nodes') or {}).get(node,{})
                ref=int(meta.get('unique_count') or 0)
                terminal=int(meta.get('terminal_page') or 0)
                if (ref<100 or terminal<1
                        or meta.get('successful_pages')!=list(range(1,terminal+1))
                        or meta.get('missing_pages')):
                    raise ValueError('Insufficient verified baseline pagination')
                minima[node]=math.ceil(ref*.998)
            # Older snapshot only supplies a conservative floor. Actual prices,
            # pages, source dates and 20 three-source checks are recollected.
            return minima
        except (ValueError,KeyError,TypeError,IndexError,json.JSONDecodeError):
            continue
    raise ValueError('No verified recent full-market coverage floor for '+previous_session)

def paged(node, minimum_rows=0):
    records=[];pages=[];urls=[];counts=[]
    for page in range(1,201):
        url=SINA+'?'+up.urlencode({'node':node,'page':page,'num':100,'sort':'symbol','asc':1})
        batch=None
        for attempt in range(4 if minimum_rows else 1):
            candidate=json_body(http(url))
            if not isinstance(candidate,list):
                raise ValueError(node+': invalid page response '+str(page))
            # Empty/short pages before independent expected coverage are transient,
            # not proof that a market has ended. Never save terminal_verified=True.
            premature=(page==1 and not candidate) or (
                minimum_rows and len(records)+len(candidate)<minimum_rows and len(candidate)<100)
            if premature:
                if attempt<3:
                    time.sleep(.5*(attempt+1))
                    continue
                raise ValueError(node+': false terminal/short page '+str(page)
                                 +' before minimum verified coverage '+str(minimum_rows))
            batch=candidate
            break
        if batch is None:raise ValueError(node+': missing page '+str(page))
        if minimum_rows and len(batch)<100:
            # Confirm the next page really has no records, rather than silently
            # terminating after a shortened page while later pages exist.
            next_url=SINA+'?'+up.urlencode({'node':node,'page':page+1,'num':100,'sort':'symbol','asc':1})
            probe=json_body(http(next_url))
            if not isinstance(probe,list) or probe:
                raise ValueError(node+': terminal page unconfirmed '+str(page))
        pages.append(page);urls.append(url);counts.append(len(batch));records.extend(batch)
        if len(batch)<100:break
    else:raise ValueError(node+': no terminal page')
    symbols=[r['symbol'] for r in records]
    if len(set(symbols))!=len(symbols):raise ValueError(node+': duplicate pagination')
    if minimum_rows and len(records)<minimum_rows:
        raise ValueError(node+': market coverage below previous verified close')
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

def verified_60_session_warmup(ipo, bars, asof):
    """One-day technical readiness exception, not a listing_lt_60 exclusion."""
    if not isinstance(ipo,dict) or ipo.get('sessions')!=60 or len(bars)!=60 or not ipo.get('source_url') or not ipo.get('date'):
        return None
    start,end=dt.date.fromisoformat(ipo['date']),dt.date.fromisoformat(asof)
    expected=[(start+dt.timedelta(days=i)).isoformat()
              for i in range(max(0,(end-start).days+1)) if trading(start+dt.timedelta(days=i))]
    if len(expected)!=60 or [b.get('date') for b in bars]!=expected:
        return None
    for b in bars:
        try:
            op,hi,lo,cl,vol,amt=(float(b[k]) for k in ('open','high','low','close','volume','amount'))
            if not all(math.isfinite(x) for x in (op,hi,lo,cl,vol,amt)) or not 0<lo<=min(op,cl)<=max(op,cl)<=hi or vol<0 or amt<0:
                return None
        except (ValueError,TypeError,KeyError):
            return None
    return {'status':'TECHNICAL_WARMUP_NOT_LISTING_LT_60','date':ipo['date'],
            'asof_date':asof,'sessions':60,'observed_bars':60,'required_bars':61,
            'calendar_reconciled':True,'source_url':ipo['source_url']}

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
    if d==target:return float(sina['trade'] if float(sina.get('trade') or 0)>0 else sina['settlement']),qq['price']
    if d==step(target,1):return float(sina['trade'] if sina.get('_security_date')==asof else sina['settlement']),qq['previous']
    raise ValueError('Quote security date incompatible with required close')

# Session-scoped exchange-announcement reproductions. Never carry a halt into a later session:
# notably sz301314 resumed trading on 2026-10-26.
VERIFIED_HALTS = {
    '2026-10-23': {
        'sh601326': {'first_public': '2026-10-20', 'halt_from': '2026-10-21',
                     'source_url': 'https://finance.sina.com.cn/jjxw/2026-10-21/doc-inivwyrf6436359.shtml'},
        'sz301314': {'first_public': '2026-10-12', 'halt_from': '2026-10-13',
                     'source_url': 'https://www.cfi.net.cn/p20261012002119.html'},
        'sz301468': {'first_public': '2026-10-18', 'halt_from': '2026-10-19',
                     'source_url': 'https://cfi.net.cn/p20261018000136.html'},
    },
    # Dated public halt announcements; do not carry these exclusions past Oct 29.
    # Every item below had a prior-public notice covering the specific session.
    '2026-10-28': {
        'sh601326': {'first_public': '2026-10-21', 'halt_from': '2026-10-21',
                     'source_url': 'https://static.cninfo.com.cn/finalpage/2026-10-21/1225612840.PDF'},
        'sh605258': {'first_public': '2026-10-27', 'halt_from': '2026-10-28',
                     'source_url': 'https://static.sse.com.cn/disclosure/listedinfo/announcement/c/new/2026-10-28/605258_20261028_5MJ7.pdf'},
        'sz002827': {'first_public': '2026-10-26', 'halt_from': '2026-10-26',
                     'source_url': 'https://static.cninfo.com.cn/finalpage/2026-10-26/1225621271.PDF'},
        'sz301468': {'first_public': '2026-10-19', 'halt_from': '2026-10-19',
                     'source_url': 'https://static.cninfo.com.cn/finalpage/2026-10-19/1225609720.PDF'},
    },
    '2026-10-29': {
        'sh601326': {'first_public': '2026-10-21', 'halt_from': '2026-10-21',
                     'source_url': 'https://static.cninfo.com.cn/finalpage/2026-10-21/1225612840.PDF'},
        'sh605258': {'first_public': '2026-10-27', 'halt_from': '2026-10-28',
                     'source_url': 'https://static.sse.com.cn/disclosure/listedinfo/announcement/c/new/2026-10-28/605258_20261028_5MJ7.pdf'},
        'sz002827': {'first_public': '2026-10-26', 'halt_from': '2026-10-26',
                     'source_url': 'https://static.cninfo.com.cn/finalpage/2026-10-26/1225621271.PDF'},
        'sz301468': {'first_public': '2026-10-19', 'halt_from': '2026-10-19',
                     'source_url': 'https://static.cninfo.com.cn/finalpage/2026-10-19/1225609720.PDF'},
    }
}

SESSION_HALTS={}

def session_halts(asof,rows,forecast=None):return {},{'security_date':asof,'complete':False,'reason':'No dynamic source installed'}
def independent_sample_bars(symbol,asof):return east_bars(symbol,asof,0)
def frozen_universe(asof):return None
def set_industry_context(rows,histories,asof):pass
def industry_audit():return {}

def verified_halt(symbol, session):
    """Return prior-public dated halt evidence, otherwise None; never infer from stale bars."""
    return SESSION_HALTS.get(session,{}).get(symbol) or VERIFIED_HALTS.get(session, {}).get(symbol)

def sample_codes(rows, asof=None, histories=None):
    def activity(row):
        if histories is None:return float(row.get('volume') or 0)>0
        bars=histories.get(row['symbol'],{}).get('bars') or []
        return bool(bars and bars[-1].get('date')==asof and bars[-1].get('volume',0)>0 and bars[-1].get('amount',0)>0)
    def change(row):
        if histories is None:return float(row.get('changepercent') or 0)
        bars=histories[row['symbol']]['bars']
        return bars[-1]['close']/bars[-2]['close']-1 if len(bars)>1 else 0.
    result=[]
    for label in ('sh_main','star','sz_main','chinext'):
        group=sorted((r for r in rows if board(r['symbol'])==label and 'ST' not in r['name'].upper()
                      and (histories is not None or float(r.get('trade') or 0)>0) and activity(r)
                      and not verified_halt(r['symbol'], asof)),
                     key=lambda r:(change(r),r['symbol']))
        if len(group)<5:raise ValueError('Missing sample board '+label)
        result += [group[round(i*(len(group)-1)/4)]['symbol'] for i in range(5)]
    return result

def sample_histories(codes,asof):
    """Fetch every independent sample concurrently, preserving every failure."""
    histories={};errors={}
    with cf.ThreadPoolExecutor(max_workers=8) as pool:
        futures={pool.submit(independent_sample_bars,code,asof):code for code in codes}
        for future in cf.as_completed(futures):
            code=futures[future]
            try:histories[code]=future.result()
            except Exception as error:errors[code]=str(error)
    return histories,errors

def assemble(rows, cover, sector_map, histories, quotes, asof, forecast, cutoff, generated_at):
    errors=[]; normalized=[]; codes=sample_codes(rows,asof,histories); by_code={r['symbol']:r for r in rows}
    samples=[]
    independent,independent_errors=sample_histories(codes,asof)
    for symbol in codes:
        try:
            q=quotes[symbol];p,t=reference(by_code[symbol],q,asof)
            if symbol in independent_errors:raise ValueError(independent_errors[symbol])
            h=independent[symbol]
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
            'source_urls':list(cover['sh_a' if symbol.startswith('sh') else 'sz_a']['source_urls']),
            'amount_unit':'CNY','raw_close':None,'adjustment_basis':None,'bars':[]}
        if 'ST' in r['name'].upper():
            record.update(eligible=False,exclude_reason='st',exclusion_evidence='Sina current name: '+r['name'])
        elif verified_halt(symbol, asof) or verified_halt(symbol, forecast):
            halt=verified_halt(symbol, asof) or verified_halt(symbol, forecast)
            record.update(eligible=False, exclude_reason='known_suspension',
                          exclusion_evidence=halt)
            record['source_urls'].append(halt['source_url'])
            # This stock did not trade in the previous session; a stale daily bar is correct.
            # No synthetic OHLCVA and no retrospective exclusion on a later session.
        elif h is None:
            try:
                ipo=listing_info(symbol,asof)
                if ipo['sessions']<60:record.update(eligible=False,exclude_reason='listing_lt_60_sessions',exclusion_evidence=ipo)
                else:errors.append(symbol+': daily history not available for established listing')
            except Exception as e:errors.append(symbol+': missing history and independent listing evidence: '+str(e))
        else:
            record.update(bars=h['bars'],adjustment_basis=h['adjustment_basis'])
            record['source_urls'] = list(record['source_urls']) + list(h.get('source_urls') or [h['source_url']])
            if h['bars'][-1]['date']!=asof:errors.append(symbol+': last daily bar stale (suspension not yet independently verified)')
            if len(h['bars'])<61:
                try:
                    ipo=listing_info(symbol,asof)
                    if ipo['sessions']<60:
                        record.update(eligible=False,exclude_reason='listing_lt_60_sessions',exclusion_evidence=ipo)
                    else:
                        warmup=verified_60_session_warmup(ipo,h['bars'],asof)
                        if warmup:record.update(eligible=False,exclude_reason='technical_warmup_60_bars',exclusion_evidence=warmup)
                        else:errors.append(symbol+': fewer than61 bars for established listing (warmup not verified)')
                except Exception as e:errors.append(symbol+': listing status unverified: '+str(e))
            if r.get('_security_date')==asof:record['raw_close']=float(r['trade'])
            elif len(dates)==1:
                record['raw_close']=float(r['trade'] if target.strftime('%Y%m%d') in dates else r['settlement'])
            else:errors.append(symbol+': mixed quote dates require per-symbol snapshot')
            if record['eligible'] and not record['sector']:errors.append(symbol+': missing industry membership')
        normalized.append(record)
    coverage={**cover,'unique_count':len(rows),'samples':samples}
    warmup_codes=[r['symbol'] for r in normalized if r.get('exclude_reason')=='technical_warmup_60_bars']
    return {'forecast_date':forecast,'previous_session':asof,'cutoff':cutoff,
      'generated_at':generated_at,'run_kind':'late_research' if generated_at>forecast+'T08:50:00+08:00' else 'scheduled',
      'data_commit':os.getenv('GITHUB_SHA','local-research'),'source_urls':[SINA,CALENDAR_SOURCE],
      'coverage':coverage,'universe':normalized,'adapter_errors':errors,'technical_warmup_codes':warmup_codes},errors

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


def incremental_cached_history(root, asof, row):
    """Extend a verified adjacent cache using the real, dated closed Sina bar.

    Fail closed when a split, adjustment, stale cache, unit mismatch or missing
    daily field might silently corrupt 61-bar technical features. The current
    Sina page and prior Eastmoney URL must both remain in the evidence chain.
    """
    symbol = row.get('symbol')
    if not symbol or asof is None:
        return None
    try:
        previous = step(dt.date.fromisoformat(asof), -1).isoformat()
        source = Path(root) / 'history' / previous / (symbol + '.json')
        if not source.exists():
            return None
        h = json.loads(source.read_text(encoding='utf-8'))
        bars = h.get('bars') or []
        basis=str(h.get('adjustment_basis',''))
        if (not usable_cached_history(h,previous)
                or not basis.startswith(('Eastmoney_fqt=1','Sina_qfq_anchor='))
                or not h.get('source_url') or h.get('volume_unit') != 'lots_100_shares'
                or h.get('amount_unit') != 'CNY'):
            return None
        current_factors=None
        if basis.startswith('Sina_qfq_anchor='):
            # Published factor ratios must be unchanged for every retained bar.
            # A corporate action, changed factor history, or missing old factors
            # forces full independent retrieval; no synthetic rebase is permitted.
            from alpha5d_provider_resilience import parse_sina_factors
            old_factors=h.get('adjustment_factors')
            if not old_factors:return None
            factor_url='https://finance.sina.com.cn/realstock/company/'+symbol+'/qfq.js'
            current_factors=parse_sina_factors(http(factor_url),symbol)
            def factor(entries,date):
                available=[entry for entry in entries if entry['d']<=date]
                return float(max(available,key=lambda entry:entry['d'])['f'])
            old_anchor=factor(old_factors,previous);new_anchor=factor(current_factors,asof)
            if any(not math.isclose(factor(old_factors,bar['date'])/old_anchor,
                                    factor(current_factors,bar['date'])/new_anchor,
                                    rel_tol=1e-12,abs_tol=1e-12) for bar in bars):return None
        prior_close = float(bars[-1]['close'])
        reference = float(row['settlement'])
        opening = float(row['open'])
        closing = float(row['trade'])
        high = float(row['high'])
        low = float(row['low'])
        shares = float(row['volume'])
        amount = float(row['amount'])
        prices = (prior_close, reference, opening, closing, high, low)
        if (not all(math.isfinite(x) and x > 0 for x in prices)
                or not math.isfinite(shares) or shares <= 0
                or not math.isfinite(amount) or amount <= 0
                or abs(prior_close-reference)>max(.00051,abs(prior_close)*1e-7)
                or not low <= min(opening, closing) <= max(opening, closing) <= high):
            return None
        vwap = amount / shares
        if not low * .995 <= vwap <= high * 1.005:
            return None
        dates = [b.get('date') for b in bars]
        if dates != sorted(set(dates)):
            return None
        new = dict(h)
        new['bars'] = (bars + [{
            'date': asof, 'open': opening, 'close': closing,
            'high': high, 'low': low, 'volume': shares / 100, 'amount': amount,
        }])[-240:]
        new['adjustment_basis'] = ('Sina_qfq_anchor=' if current_factors else 'Eastmoney_fqt=1_verified_Sina_incremental_asof=') + asof
        if current_factors:new['adjustment_factors']=current_factors
        new['incremental_source_url'] = SINA
        new['source_urls']=sorted(set(list(h.get('source_urls') or [h['source_url']])+[SINA]))
        new['retrieved_at'] = now().isoformat()
        new['retained_bars'] = len(new['bars'])
        new['incremental_guard'] = {'prior_close_matches_sina_settlement': True,
                                     'price_volume_amount_units_verified': True,
                                     'corporate_action_rebase_not_needed': True}
        return new
    except (ValueError, TypeError, KeyError, OSError, RuntimeError, json.JSONDecodeError):
        return None

def usable_cached_history(history,asof):
    """A file's presence or date alone cannot certify a usable 61-bar cache."""
    bars=history.get('bars') or []
    if len(bars)<61 or bars[-1].get('date')!=asof:return False
    if (history.get('volume_unit')!='lots_100_shares' or history.get('amount_unit')!='CNY'
            or not history.get('source_url') or not history.get('adjustment_basis')):return False
    dates=[bar.get('date') for bar in bars]
    if not all(isinstance(date,str) for date in dates) or dates!=sorted(set(dates)):return False
    for bar in bars:
        if any(not isinstance(bar.get(key),(int,float)) or isinstance(bar.get(key),bool)
               or not math.isfinite(bar[key]) for key in ('open','close','high','low','volume','amount')):return False
        if not 0<bar['low']<=min(bar['open'],bar['close'])<=max(bar['open'],bar['close'])<=bar['high']:return False
        if bar['volume']<0 or bar['amount']<0:return False
    return True

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--forecast');ap.add_argument('--workers',type=int,default=8)
    ap.add_argument('--output',default='data/alpha5d')
    args=ap.parse_args();started=now();root=Path(args.output)
    fday=dt.date.fromisoformat(args.forecast) if args.forecast else (step(started.date(),1) if started.time()>=dt.time(15,15) else started.date())
    if not trading(fday):
        atomic(root/'pipeline_status.json',{'status':'NOT_TRADING_DAY','date':fday.isoformat(),'generated_at':started.isoformat()});return
    asof=step(fday,-1).isoformat();forecast=fday.isoformat();run_id=started.strftime('%Y%m%dT%H%M%S')
    run_dir=root/'runs'/run_id; manifest={'adapter_version':VERSION,'run_id':run_id,'stage':'market','started_at':started.isoformat(),
       'forecast_date':forecast,'previous_session':asof,'sessions':forecast_sessions(fday),
       'calendar_source':CALENDAR_SOURCE,'status':'RUNNING','run_kind':'late_research' if started.time()>dt.time(8,50) else 'prefetch'}
    atomic(root/'pipeline_status.json',manifest)
    try:
        rows=[];cover={}
        verified_minima=verified_market_minima(asof)
        frozen=frozen_universe(asof)
        if frozen:
            rows,cover,market_audit=frozen;manifest['market_snapshot_audit']=market_audit
            for node,prefix in [('sh_a','sh'),('sz_a','sz')]:
                if cover[node]['raw_rows']<verified_minima[node]:raise ValueError('Frozen universe below verified coverage')
                atomic(run_dir/(node+'.json'),{'records':[r for r in rows if r['symbol'].startswith(prefix)],'manifest':cover[node]})
        else:
            for node in ('sh_a','sz_a'):
                batch,g=paged(node,minimum_rows=verified_minima[node]);rows+=batch;cover[node]=g
                atomic(run_dir/(node+'.json'),{'records':batch,'manifest':g})
        if len(rows)!=len({r['symbol'] for r in rows}):raise ValueError('Cross-exchange duplicate')
        manifest.update(raw_count=len(rows),coverage=cover)
        manifest['stage']='session_halts';atomic(root/'pipeline_status.json',manifest)
        try:
            halts,halt_audit=session_halts(asof,rows,forecast);SESSION_HALTS[asof]=halts;manifest['session_halt_audit']=halt_audit
        except Exception as error:
            SESSION_HALTS[asof]={};manifest['session_halt_audit']={'complete':False,'error':str(error)}
        # Daily history retrieval is independent of industry availability; cache it first.
        histories={};history_errors={}
        manifest['stage']='history';atomic(root/'pipeline_status.json',manifest)
        targets=[r for r in rows if 'ST' not in r['name'].upper() and not verified_halt(r['symbol'],asof) and not verified_halt(r['symbol'],forecast)]
        def load(r):
            sym=r['symbol'];path=root/'history'/asof/(sym+'.json')
            if path.exists():
                try:h=json.loads(path.read_text())
                except (ValueError,OSError):h={}
                if usable_cached_history(h,asof):return sym,h
            h=incremental_cached_history(root,asof,r) if r.get('_security_date')==asof else None
            if h is not None:
                atomic(path,h)
                return sym,h
            h=east_bars(sym,asof);atomic(path,h);return sym,h
        with cf.ThreadPoolExecutor(max_workers=args.workers) as pool:
            futures={pool.submit(load,r):r['symbol'] for r in targets}
            for f in cf.as_completed(futures):
                sym=futures[f]
                try:_,histories[sym]=f.result()
                except Exception as e:history_errors[sym]=str(e)
        manifest.update(history_success=len(histories),history_missing=history_errors)
        manifest['stage']='industry';atomic(root/'pipeline_status.json',manifest)
        set_industry_context(rows,histories,asof)
        try:industry,url=industries(args.workers)
        finally:manifest['industry_source_audit']=industry_audit()
        atomic(run_dir/'industry.json',{'source':url,'memberships':industry,'audit':industry_audit()})
        manifest['stage']='cross_source_samples';atomic(root/'pipeline_status.json',manifest)
        quotes=tencent(sample_codes(rows,asof,histories))
        actual=now().isoformat();cutoff=max(forecast+'T08:50:00+08:00',actual) if started.date()==fday else forecast+'T08:50:00+08:00'
        payload,errors=assemble(rows,cover,industry,histories,quotes,asof,forecast,cutoff,actual)
        manifest['technical_warmup_codes']=payload.get('technical_warmup_codes',[])
        atomic(run_dir/'input.json',payload)
        if errors:raise ValueError('INPUT_INCOMPLETE: '+repr(errors[:20]))
        manifest['stage']='ranking';atomic(root/'pipeline_status.json',manifest)
        import alpha5d_engine as engine
        result=run_rank(engine,payload);atomic(run_dir/'ranking.json',result)
        review={'forecast_date':forecast,'previous_session':asof,'sessions':manifest['sessions'],'cutoff':cutoff,
                'generated_at':actual,'run_kind':payload['run_kind'],'input_sha256':result['input_sha256'],
                'data_commit':payload['data_commit'],'ranker_version':engine.VERSION,'ranking_health':result['ranking_health'],
                'input_count':len(rows),'eligible_count':len(result.get('eligible_codes',[])),
                'eligible_codes':result.get('eligible_codes',[]),'excluded':result.get('excluded',{}),
                'industry_classification':payload.get('industry_classification'),'industry_source_audit':payload.get('industry_source_audit'),
                'provider_route_health':payload.get('provider_route_health'),'sample_checks':payload['coverage']['samples'],
                'top20':result.get('ranking',[])[:20],'top5':result.get('top5',[]),'baselines':result.get('baselines',{}),
                'technical_warmup_codes':payload.get('technical_warmup_codes',[]),
                'publication_state':'AWAITING_EVIDENCE_REVIEW','is_0850_live_prediction':False if payload['run_kind']=='late_research' else None}
        atomic(run_dir/'review.json',review)
        manifest.update(status=result['ranking_health'],input_sha256=sha(payload),
                        ranking_path='data/alpha5d/runs/'+run_id+'/ranking.json',ranker_version=engine.VERSION,
                        top5=result.get('top5',[]),errors=result.get('errors',[]))
        manifest['publication_state']='AWAITING_EVIDENCE_REVIEW'
        manifest.update(review_path='data/alpha5d/runs/'+run_id+'/review.json',run_kind=payload['run_kind'],generated_at=actual,
                        provider_route_health=payload.get('provider_route_health'),industry_classification=payload.get('industry_classification'),
                        verified_sample_count=len(payload['coverage']['samples']),is_0850_live_prediction=result.get('is_0850_live_prediction'))
        manifest['formal_top5_allowed']=False
        manifest['stage']='evidence_review'
    except Exception as e:
        manifest.update(status='INCOMPLETE',failed_stage=manifest.get('stage'),error=f'{type(e).__name__}: {e}',formal_top5_allowed=False)
    finally:
        manifest['ended_at']=now().isoformat();atomic(run_dir/'receipt.json',manifest)
        atomic(root/'pipeline_status.json',manifest)
        print(json.dumps({k:manifest.get(k) for k in ('status','raw_count','history_success','error','publication_state')},ensure_ascii=False))
    if manifest['status']!='COMPLETE':raise SystemExit(2)

if __name__=='__main__':main()
