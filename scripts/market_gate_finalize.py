#!/usr/bin/env python3
"""Verify frozen market snapshots and produce small, auditable data shards."""
from __future__ import annotations
import concurrent.futures as cf
import datetime as dt
import hashlib
import json
import math
import statistics
from pathlib import Path

TZ=dt.timezone(dt.timedelta(hours=8))
ROOT=Path('data')

def number(v):
    try:
        x=float(v)
        return x if math.isfinite(x) else None
    except (TypeError,ValueError): return None

def stamp(v):
    try:
        s=str(v)
        if s.isdigit() and len(s) in (12,14):
            return dt.datetime.strptime(s,'%Y%m%d%H%M' if len(s)==12 else '%Y%m%d%H%M%S').replace(tzinfo=TZ)
        t=dt.datetime.fromisoformat(s)
        return t.replace(tzinfo=TZ) if t.tzinfo is None else t.astimezone(TZ)
    except (ValueError,TypeError): return None

def ohlc_time_gate(deep,key,now):
    k=deep.get(key) or {}
    bars=(k.get('metrics') or {}).get('last_bars') or []
    if k.get('quality') not in ('primary','backup') or len(bars)<4: return False
    times=[]
    for b in bars:
        t=stamp(b.get('time'))
        p=[number(b.get(x)) for x in ('open','close','high','low')]
        if t is None or any(x is None or x<=0 for x in p): return False
        o,c,h,l=p
        if not l<=min(o,c)<=max(o,c)<=h or t>now+dt.timedelta(seconds=30): return False
        times.append(t)
    minutes=5 if key=='m5' else 15
    return bool(all(a<b for a,b in zip(times,times[1:])) and times[-1].date()==now.date()
                and (now-times[-1]).total_seconds()<=minutes*60+120)

def frozen_check(mq,day_bars,sina_price,qq_price,expected_date,cutoff):
    rows=[r for r in mq.get('rows',[]) if str(r.get('hhmm'))==cutoff]
    p=number(rows[-1].get('price')) if len(rows)==1 else None
    s,q=number(sina_price),number(qq_price)
    price_ok=bool(p and s and q and abs(s-p)/p<=0.005 and abs(q-p)/p<=0.005)
    minute_ok=str(mq.get('date'))==expected_date and len(rows)==1 and price_ok
    daily_ok=cutoff=='1130'
    close=None
    if cutoff=='1500':
        target=expected_date[:4]+'-'+expected_date[4:6]+'-'+expected_date[6:]
        bars=[b for b in day_bars if str(b.get('time',''))[:10]==target or str(b.get('time',''))==expected_date]
        if bars:
            close=number(bars[-1].get('close'))
            daily_ok=bool(p and close and abs(close-p)/p<=0.005)
    return {'passed':bool(minute_ok and daily_ok),'security_date':mq.get('date'),
            'cutoff_hhmm':cutoff,'cutoff_price':p,'sina_price':s,'tencent_snapshot_price':q,
            'minute_price_agrees':price_ok,'daily_close':close,'daily_close_checked':cutoff=='1500',
            'daily_close_agrees':daily_ok,'minute_source_url':mq.get('url')}

def write(name,obj):
    p=ROOT/name; p.parent.mkdir(parents=True,exist_ok=True)
    tmp=p.with_suffix('.tmp');tmp.write_text(json.dumps(obj,ensure_ascii=False,indent=2),encoding='utf8');tmp.replace(p)

def main():
    import market_relay as core
    now=dt.datetime.now(TZ)
    receipt=json.loads((ROOT/'audit_receipt.json').read_text(encoding='utf8'))
    market=json.loads((ROOT/'latest_market.json').read_text(encoding='utf8'))
    watch=json.loads((ROOT/'watch_assets.json').read_text(encoding='utf8'))
    for sym,gate in ((market.get('audit_v26') or {}).get('symbols') or {}).items():
        deep=((market.get('symbols') or {}).get(sym) or {}).get('deep') or {}
        ready=ohlc_time_gate(deep,'m5',now) and ohlc_time_gate(deep,'m15',now)
        gate['true_5m_15m_ohlc_time_verified']=ready
        if gate.get('health') in ('GREEN','GREEN-T') and not ready:
            gate.update(health='YELLOW',live_action_allowed=False,reason='true_ohlc_time_gate_failed')
    for sym,item in (watch.get('assets') or {}).items():
        gate=item.get('audit') or {}; deep=item.get('deep') or {}
        ready=ohlc_time_gate(deep,'m5',now) and ohlc_time_gate(deep,'m15',now)
        gate['true_5m_15m_ohlc_time_verified']=ready
        if gate.get('health') in ('GREEN','GREEN-T') and not ready:
            gate.update(health='YELLOW',live_action_allowed=False,reason='true_ohlc_time_gate_failed')
    radar=json.loads((ROOT/'radar_summary.json').read_text(encoding='utf8'))
    radar['audit_v26']=market.get('audit_v26')
    if any(x.get('health')=='YELLOW' for x in market['audit_v26']['symbols'].values()) and radar.get('data_health') in ('GREEN','GREEN-T'):
        radar.update(data_health='YELLOW',live_action_allowed=False)
    write('latest_market.json',market);write('watch_assets.json',watch);write('radar_summary.json',radar)
    receipt['personal_candidate_gates']=market['audit_v26']['symbols']
    receipt['watch_candidate_gates']={s:a.get('audit') for s,a in watch.get('assets',{}).items()}
    path=ROOT/'universe_latest.json'
    if not path.exists():
        receipt['frozen_verification']={'state':'NO_UNIVERSE'};write('audit_receipt.json',receipt);return
    universe=json.loads(path.read_text(encoding='utf8'))
    cap=stamp(universe.get('captured_at_beijing'))
    cutoff=None
    if cap and cap.date()==now.date() and cap.hour>=15 and now.hour>=15: cutoff='1500'
    elif cap and cap.date()==now.date() and 690<=cap.hour*60+cap.minute<780 and 690<=now.hour*60+now.minute<780: cutoff='1130'
    frozen=[]
    if cutoff and universe.get('complete_pagination'):
        symbols=[x['symbol'] for x in universe.get('stratified_checks',[])]
        quotes=core.tencent_quotes(symbols).get('records',{}) if len(set(symbols))>=20 else {}
        expected=cap.strftime('%Y%m%d')
        def one(s):
            mq=core.minute_query(s)
            day=core.tencent_kline(s,'day',5) if cutoff=='1500' else {'bars':[]}
            if cutoff=='1500' and not day.get('bars'): day=core.east_kline(s,101,5)
            check=frozen_check(mq,day.get('bars',[]),universe['records'][s].get('trade'),quotes.get(s,{}).get('price'),expected,cutoff)
            check.update(symbol=s,daily_source_url=day.get('url'),tencent_snapshot_update_time=quotes.get(s,{}).get('quote_time'))
            return check
        with cf.ThreadPoolExecutor(max_workers=8) as pool:
            futures={pool.submit(one,s):s for s in symbols}
            for f in cf.as_completed(futures):
                try: frozen.append(f.result())
                except Exception as e: frozen.append({'symbol':futures[f],'passed':False,'error':str(e)[:200]})
        frozen.sort(key=lambda r:r['symbol'])
        qualified=bool(len(frozen)>=20 and all(c.get('passed') for c in frozen)
                       and not any(universe.get('missing_fields',{}).values()))
        universe['frozen_verification']={'checked_at_beijing':dt.datetime.now(TZ).isoformat(),
            'cutoff_hhmm':cutoff,'security_date':expected,'sample_count':len(frozen),
            'passed_count':sum(bool(x.get('passed')) for x in frozen),'checks':frozen,
            'state':'PASS' if qualified else 'INCOMPLETE',
            'live_action_allowed':False,'note':'Frozen dataset verification is not real-time execution or proof of an earlier agent run.'}
        universe['stock_pick_gate']=('VERIFIED_CLOSED_DATASET' if cutoff=='1500' else 'VERIFIED_NOON_DATASET') if qualified else 'INCOMPLETE'
    manifest=[]
    for node,prefix in (('sh_a','sh'),('sz_a','sz')):
        items=sorted([(s,r) for s,r in universe.get('records',{}).items() if s.startswith(prefix)])
        for start in range(0,len(items),100):
            rows=dict(items[start:start+100]);name=f'universe_pages/{node}_{start//100+1:03d}.json'
            shard={'captured_at_beijing':universe.get('captured_at_beijing'),'node':node,'page':start//100+1,
                   'record_count':len(rows),'stock_pick_gate':universe.get('stock_pick_gate'),'records':rows}
            write(name,shard)
            digest=hashlib.sha256((ROOT/name).read_bytes()).hexdigest()
            manifest.append({'path':'data/'+name,'count':len(rows),'sha256':digest})
    summary={k:v for k,v in universe.items() if k not in ('records','source_requests')}
    values=[number(r.get('changepercent')) for r in universe.get('records',{}).values()]
    valid=[v for v in values if v is not None]
    summary['market_breadth']={'scope':'sh_a+sz_a','valid_returns':len(valid),
        'advancing':sum(v>0 for v in valid),'declining':sum(v<0 for v in valid),
        'unchanged':sum(v==0 for v in valid),'median_change_pct':statistics.median(valid) if valid else None,
        'amount_sum_raw_CNY':sum(number(r.get('amount')) or 0 for r in universe.get('records',{}).values())}
    summary['raw_shards']=manifest
    summary['no_future_data_permission']=True
    write('universe_latest.json',universe);write('universe_summary.json',summary)
    receipt.setdefault('coverage',{})['universe']={k:v for k,v in summary.items() if k!='raw_shards'}
    receipt['raw_shard_count']=len(manifest)
    receipt['finalized_at_beijing']=dt.datetime.now(TZ).isoformat()
    write('audit_receipt.json',receipt)
    print(json.dumps({'full_market':universe.get('unique_code_count'),'frozen_gate':universe.get('stock_pick_gate'),
                      'frozen_checks':len(frozen),'frozen_passed':sum(bool(x.get('passed')) for x in frozen),'shards':len(manifest)},ensure_ascii=False))

if __name__=='__main__': main()
