#!/usr/bin/env python3
"""Auditable, fail-closed supplement to the existing market relay; no trading."""
from __future__ import annotations
import concurrent.futures as cf
import datetime as dt
import json
import math
import os
import time
from pathlib import Path

TZ = dt.timezone(dt.timedelta(hours=8))
ROOT = Path('data')
SCHEMA = '2.6.0'
GROUPS = {
    'tactical_watch': ['sh688331','sz300779','sh600988','sz002001','hk00883',
                      'sz002838','sh601233','sh600989','sh603993','sz000703',
                      'hk01530','sz002493','sh600299','hk01456','sz159731'],
    'fund_watch': ['sh513500','sh518880','sh589720'],
}

def finite(v):
    try:
        x=float(v)
        return x if math.isfinite(x) else None
    except (ValueError,TypeError):
        return None

def phase(now):
    if now.weekday() >= 5:
        return 'WEEKEND'
    m=now.hour*60+now.minute
    if 570 <= m <= 690 or 780 <= m <= 900:
        return 'SESSION_WINDOW_CALENDAR_UNCONFIRMED'
    if 690 < m < 780:
        return 'LUNCH'
    return 'PREOPEN' if m < 570 else 'CLOSED'

def age(stamp, now):
    try:
        t=dt.datetime.fromisoformat(stamp)
        if t.tzinfo is None:
            return None
        d=(now-t).total_seconds()
        return d if d >= -30 else None
    except (ValueError,TypeError):
        return None

def minute_gate(mq, quote, now):
    """A-share continuous-session data only. Keep raw excluded rows visible."""
    valid=[]; excluded=[]; anomalies=[]; prev_v=prev_a=None
    for row in mq.get('rows',[]):
        s=str(row.get('hhmm',''))
        try:
            h,m=int(s[:2]),int(s[2:])
            n=h*60+m
            ok=len(s)==4 and 0<=m<60 and (570<=n<=690 or 780<=n<=900)
        except ValueError:
            ok=False
        if not ok:
            excluded.append(s); continue
        if valid and s<=valid[-1]['hhmm']:
            anomalies.append('duplicate_or_unsorted_minute'); continue
        p,v,a=finite(row.get('price')),finite(row.get('cum_volume')),finite(row.get('cum_amount'))
        if p is None or p<=0:
            anomalies.append('invalid_price'); continue
        if v is None or a is None or v<0 or a<0:
            anomalies.append('missing_cumulative_volume_amount')
        elif (prev_v is not None and v<prev_v) or (prev_a is not None and a<prev_a):
            anomalies.append('cumulative_reset')
        prev_v,prev_a=v,a
        valid.append(row)
    stamp=None
    if valid:
        try:
            stamp=dt.datetime.strptime(str(mq.get('date'))+valid[-1]['hhmm'],'%Y%m%d%H%M').replace(tzinfo=TZ).isoformat()
        except ValueError:
            anomalies.append('invalid_security_date')
    fresh=(mq.get('date')==now.strftime('%Y%m%d') and age(stamp,now) is not None and -30<=age(stamp,now)<=600)
    vwap=None
    if valid and not anomalies:
        # Mainland provider volume: lots (100 shares); amount: CNY.
        # Never choose a scale merely because it fits the last price.
        v,a=finite(valid[-1].get('cum_volume')),finite(valid[-1].get('cum_amount'))
        if v and a is not None:
            candidate=a/(100*v)
            lo,hi=finite(quote.get('low')),finite(quote.get('high'))
            if lo is not None and hi is not None and 0<lo<=candidate<=hi:
                vwap=round(candidate,8)
            else:
                anomalies.append('vwap_units_or_daily_range_unverified')
    return {'security_date':mq.get('date'),'last_continuous_minute':stamp,
            'fresh_for_live':fresh,'vwap':vwap,'volume_unit':'lot_100_shares',
            'amount_unit':'CNY','excluded_out_of_session_rows':excluded,
            'anomalies':sorted(set(anomalies)),
            'sampled_prices_are_not_true_intraminute_ohlc':True}

def true_kline_ready(deep, key):
    k=deep.get(key) or {}; bars=(k.get('metrics') or {}).get('last_bars') or []
    if k.get('quality') not in ('primary','backup') or len(bars)<4:
        return False
    return all(all(finite(b.get(f)) is not None for f in ('open','close','high','low')) for b in bars)

def evaluate(sym,q,deep,daily,now):
    if sym.startswith('hk'):
        return {'health':'UNVERIFIED','reason':'HK_adapter_not_yet_independently_validated','live_action_allowed':False}
    mg=minute_gate(deep.get('minute_query') or {},q,now)
    sec=(deep.get('secondary_quote') or {}).get('record') or {}
    qa,sa=age(q.get('quote_time'),now),age(sec.get('quote_time'),now)
    p,s=finite(q.get('price')),finite(sec.get('price'))
    paired=bool(p and s and qa is not None and sa is not None and -30<=qa<=600 and -30<=sa<=600
                and abs(qa-sa)<=300 and abs(p-s)/p<=0.005)
    sufficient=all(finite(daily.get(k)) is not None for k in ('ma5','ma10'))
    shape=true_kline_ready(deep,'m5') and true_kline_ready(deep,'m15')
    live=phase(now)=='SESSION_WINDOW_CALENDAR_UNCONFIRMED'
    core=bool(p and qa is not None and -30<=qa<=600 and mg['fresh_for_live'] and mg['vwap'] and not mg['anomalies'])
    if not live:
        health='NOT_LIVE'
    elif not p or qa is None or not (-30<=qa<=600) or not mg['fresh_for_live']:
        health='RED'
    elif not core or not sufficient or not shape:
        health='YELLOW'
    else:
        health='GREEN' if paired else 'GREEN-T'
    return {'health':health,'market_phase':phase(now),'minute_gate':mg,
            'independent_quote_agrees':paired,'true_5m_15m_ohlc_ready':shape,
            'daily_sequence_ready':sufficient,'live_action_allowed':health in ('GREEN','GREEN-T'),
            'official_iopv_verified':False if sym in ('sh513500','sh518880') else None,
            'premium_action_allowed':False if sym in ('sh513500','sh518880') else None}

def write(name,obj):
    ROOT.mkdir(exist_ok=True)
    path=ROOT/name; temp=path.with_suffix('.tmp')
    temp.write_text(json.dumps(obj,ensure_ascii=False,indent=2),encoding='utf-8'); temp.replace(path)

def fetch_json(core,url):
    errors=[]
    for attempt in range(2):
        r=core.get(url,'https://finance.sina.com.cn/',timeout=6)
        if r.get('ok'):
            try:
                return core.parse_jsonish(core.txt(r)),errors
            except Exception as e:
                errors.append(type(e).__name__+': '+str(e)[:120])
        else:
            errors.append(r.get('error','transport_failure'))
        if attempt==0: time.sleep(0.25)
    return None,errors

def full_market(core,now):
    nodes={}; records={}; urls=[]
    started=time.monotonic()
    for node in ('sh_a','sz_a'):
        successful=[]; missing=[]; terminal=None; rows=[]
        for page in range(1,101):
            if time.monotonic()-started>95:
                missing.append(page); break
            url=('https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/'
                 f'Market_Center.getHQNodeData?page={page}&num=100&sort=symbol&asc=1&node={node}')
            data,errors=fetch_json(core,url); urls.append({'url':url,'errors':errors})
            if not isinstance(data,list):
                missing.append(page); break
            successful.append(page); rows.extend(data)
            if len(data)<100:
                if rows: terminal=page
                else: missing.append(page)
                break
        nodes[node]={'successful_pages':successful,'missing_pages':missing,'terminal_page':terminal,
                     'raw_row_count':len(rows),'unique_count':len({str(x.get('symbol')) for x in rows})}
        for r in rows:
            code=str(r.get('symbol',''))
            if code.startswith(('sh','sz')): records[code]=r
    sample=[]
    for pref in ('sh','sz'):
        eligible=sorted([s for s,r in records.items() if s.startswith(pref) and (finite(r.get('trade')) or 0)>0],
                        key=lambda s: finite(records[s].get('changepercent')) or 0)
        if len(eligible)>=10:
            sample.extend(eligible[round(i*(len(eligible)-1)/9)] for i in range(10))
    tq=core.tencent_quotes(sample) if len(set(sample))>=20 else {'records':{}}
    checks=[]; dates=set()
    for s in sample:
        q=tq.get('records',{}).get(s,{})
        p,a=finite(q.get('price')),finite(records[s].get('trade'))
        stamp=q.get('quote_time'); st=str(records[s].get('ticktime') or '')
        synced=False
        try:
            t=dt.datetime.fromisoformat(stamp)
            stime=dt.datetime.strptime(st,'%H:%M:%S').time()
            other=dt.datetime.combine(t.date(),stime,TZ)
            synced=abs((t-other).total_seconds())<=300
            dates.add(t.date().isoformat())
        except (ValueError,TypeError): pass
        checks.append({'symbol':s,'tencent_time':stamp,'sina_ticktime':st,
                       'same_time_gate':synced,'price_agrees':bool(p and a and abs(p-a)/p<=0.005)})
    required=('symbol','name','trade','pricechange','changepercent','buy','sell','settlement','open','high','low','volume','amount','ticktime')
    missing_fields={k:sum(1 for r in records.values() if k not in r or r[k] is None) for k in required}
    complete=(all(n['terminal_page'] and not n['missing_pages'] for n in nodes.values())
              and sum(n['raw_row_count'] for n in nodes.values())==len(records))
    verified=complete and len(checks)>=20 and len(dates)==1 and not any(missing_fields.values()) and all(c['same_time_gate'] and c['price_agrees'] for c in checks)
    out={'schema_version':SCHEMA,'captured_at_beijing':now.isoformat(),'nodes':nodes,
         'unique_code_count':len(records),'missing_fields':missing_fields,'stratified_checks':checks,
         'security_dates_from_samples':sorted(dates),'complete_pagination':complete,
         'stock_pick_gate':'VERIFIED_DATASET' if verified else 'INCOMPLETE',
         'point_in_time_note':'Consumer must verify dataset security date and cutoff; retrieval time is not trade time.',
         'source_requests':urls,'records':records}
    write('universe_latest.json',out)
    return {k:v for k,v in out.items() if k not in ('records','source_requests')}

def main():
    import market_relay as core
    now=dt.datetime.now(TZ)
    paths={}
    obj=json.loads((ROOT/'latest_market.json').read_text(encoding='utf-8'))
    qrec=(obj.get('tencent_quote') or {}).get('records') or {}
    audited={}
    for s in obj.get('deep_candidates',[]):
        item=(obj.get('symbols') or {}).get(s,{})
        audited[s]=evaluate(s,qrec.get(s,{}),item.get('deep',{}),item.get('daily_metrics',{}),now)
    obj['audit_v26']={'schema_version':SCHEMA,'evaluated_at_beijing':now.isoformat(),'market_phase':phase(now),
                       'symbols':audited,'legacy_health_is_not_authoritative':True}
    write('latest_market.json',obj)
    radar=json.loads((ROOT/'radar_summary.json').read_text(encoding='utf-8'))
    radar['audit_v26']=obj['audit_v26']
    radar['legacy_data_health']=radar.get('data_health')
    states=[x['health'] for x in audited.values()]
    radar['data_health']=('NOT_LIVE' if phase(now)!='SESSION_WINDOW_CALENDAR_UNCONFIRMED' else
                         'GREEN' if states and all(x=='GREEN' for x in states) else
                         'GREEN-T' if states and all(x in ('GREEN','GREEN-T') for x in states) else
                         'RED' if 'RED' in states else 'YELLOW')
    radar['live_action_allowed']=radar['data_health'] in ('GREEN','GREEN-T')
    write('radar_summary.json',radar)
    symbols=sorted(set(sum(GROUPS.values(),[])))
    snapshot=core.tencent_quotes(symbols)
    quotes=snapshot.get('records',{})
    candidates=sorted(GROUPS['tactical_watch'],key=lambda s:abs((quotes.get(s) or {}).get('change_pct') or 0),reverse=True)[:5]
    candidates=sorted(set(candidates+GROUPS['fund_watch']))
    def one(s):
        if s.startswith('hk'):
            return {'audit':{'health':'UNVERIFIED','reason':'HK_adapter_validation_required'},'quote':quotes.get(s,{})}
        base=core.base_symbol(s); deep=core.deep_symbol(s,(quotes.get(s) or {}).get('price'))
        return {'quote':quotes.get(s,{}),'daily':base,'deep':deep,
                'audit':evaluate(s,quotes.get(s,{}),deep,base.get('daily_metrics',{}),dt.datetime.now(TZ))}
    assets={}
    with cf.ThreadPoolExecutor(max_workers=5) as pool:
        jobs={pool.submit(one,s):s for s in candidates}
        for f in cf.as_completed(jobs):
            s=jobs[f]
            try: assets[s]=f.result()
            except Exception as e: assets[s]={'audit':{'health':'UNVERIFIED','error':str(e)[:160]}}
    write('watch_assets.json',{'schema_version':SCHEMA,'generated_at_beijing':now.isoformat(),
                             'groups':GROUPS,'quotes':snapshot,'assets':assets,
                             'never_infer_personal_positions_from_groups':True})
    fut_url=('https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/'
             'Market_Center.getHQFuturesData?page=1&num=20&sort=position&asc=0&node=zzgz_qh&base=futures')
    fut,errors=fetch_json(core,fut_url)
    write('ic_snapshot.json',{'schema_version':SCHEMA,'retrieved_at_beijing':dt.datetime.now(TZ).isoformat(),
                             'raw_url':fut_url,'single_response':fut,'errors':errors,
                             'cffex_identity_verified':False,'roll_action_allowed':False,
                             'note':'Raw evidence only. Agent must verify CFFEX listed identities, contemporaneous bids/asks and net >=4% gate.'})
    hm=now.hour*60+now.minute
    do_universe=os.getenv('GITHUB_EVENT_NAME') in ('push','workflow_dispatch') or 690<=hm<=700 or 900<=hm<=915
    if do_universe: paths['universe']=full_market(core,dt.datetime.now(TZ))
    elif (ROOT/'universe_latest.json').exists():
        old=json.loads((ROOT/'universe_latest.json').read_text(encoding='utf8'))
        paths['universe']={k:old.get(k) for k in ('captured_at_beijing','unique_code_count','stock_pick_gate')}
        paths['universe']['reused_not_refreshed']=True
    receipt={'schema_version':SCHEMA,'generated_at_beijing':dt.datetime.now(TZ).isoformat(),
             'run_id':os.getenv('GITHUB_RUN_ID'),'commit':os.getenv('GITHUB_SHA'),
             'market_phase':phase(now),'personal_candidate_gates':audited,
             'watch_candidate_gates':{s:v.get('audit') for s,v in assets.items()},
             'coverage':paths,'analysis_signals_not_generated_here':True,
             'iopv_verified':False,'actual_agent_execution_not_proved_by_relay':True}
    write('audit_receipt.json',receipt)
    print(json.dumps({'bridge_version':SCHEMA,'market_phase':phase(now),'personal_candidates':len(audited),
                      'watch_candidates':len(assets),'universe_refreshed':do_universe},ensure_ascii=False))

if __name__=='__main__': main()
