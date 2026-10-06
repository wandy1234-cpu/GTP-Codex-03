#!/usr/bin/env python3
"""V8.0 deterministic five-session research ranker and separate cohort evaluator.
Standard-library only. No broker access. No trained model or profitability claim.
Input is a point-in-time, adapter-normalized JSON dataset; see CONTRACT.md.
"""
from __future__ import annotations
import argparse
import datetime as dt
import hashlib
import json
import math
import statistics as st
from pathlib import Path

VERSION = 'V8.0-bootstrap-1'
TZ = dt.timezone(dt.timedelta(hours=8))
ALLOWED_EXCLUSIONS = {'non_sh_sz_a', 'st', 'known_suspension', 'listing_lt_60_sessions', 'liquidity_lt_50m'}

def iso(s):
    t = dt.datetime.fromisoformat(s)
    if t.tzinfo is None:
        raise ValueError('timezone required')
    return t.astimezone(TZ)

def digest(obj):
    return hashlib.sha256(json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()

def finite(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)

def percentiles(values):
    """Ascending average-tie percentiles, not calibrated probabilities."""
    ordered = sorted(values.items(), key=lambda p: (p[1], p[0]))
    out, n, i = {}, len(ordered), 0
    while i < n:
        j = i + 1
        while j < n and ordered[j][1] == ordered[i][1]:
            j += 1
        p = ((i + j - 1) / 2) / (n - 1) if n > 1 else .5
        for code, _ in ordered[i:j]:
            out[code] = p
        i = j
    return out

def validate(data):
    errors = []
    cutoff = iso(data['cutoff'])
    previous = data['previous_session']
    forecast = data['forecast_date']
    if cutoff.strftime('%Y-%m-%d') != forecast or cutoff.strftime('%H:%M:%S') != '08:50:00':
        errors.append('cutoff must be forecast-date 08:50:00 Asia/Shanghai')
    if not previous < forecast or not data.get('source_urls') or not data.get('data_commit'):
        errors.append('missing temporal/source provenance')
    for node in ('sh_a', 'sz_a'):
        g = data['coverage'].get(node, {})
        end = g.get('terminal_page', 0)
        if not end or g.get('missing_pages') or g.get('successful_pages') != list(range(1, end + 1)):
            errors.append(node + ': pagination incomplete')
        if not g.get('raw_rows', 0) or not g.get('terminal_verified'):
            errors.append(node + ': empty-first-page/terminal unverified')
    rows = data.get('universe', [])
    codes = [x.get('symbol') for x in rows]
    if len(codes) != len(set(codes)) or len(codes) != data['coverage'].get('unique_count'):
        errors.append('universe count or duplicate mismatch')
    if sum(data['coverage'].get(n, {}).get('raw_rows', 0) for n in ('sh_a', 'sz_a')) != len(codes):
        errors.append('raw rows not reconciled; deduplicate with explicit source manifest')
    samples = data['coverage'].get('samples', [])
    if len({x.get('symbol') for x in samples}) < 20:
        errors.append('fewer than 20 distinct cross-source samples')
    groups = set()
    by_code = {x['symbol']: x for x in rows}
    for x in samples:
        groups.add(x.get('board'))
        ps = [x.get(k) for k in ('sina_close', 'tencent_close', 'daily_close')]
        if (x.get('security_date') != previous or x.get('symbol') not in by_code
            or not x.get('identity_matches') or not x.get('source_urls')
            or any(not finite(p) or p <= 0 for p in ps)
            or max(ps) / min(ps) - 1 > .005
            or not finite(by_code[x['symbol']].get('raw_close'))
            or abs(by_code[x['symbol']]['raw_close'] / ps[0] - 1) > .005):
            errors.append('invalid sample: ' + str(x.get('symbol')))
    if not {'sh_main', 'star', 'sz_main', 'chinext'} <= groups:
        errors.append('sample board stratification incomplete')
    good, excluded = {}, {}
    for r in rows:
        code = r['symbol']
        if not r.get('eligible', True):
            why = r.get('exclude_reason')
            if why not in ALLOWED_EXCLUSIONS or not r.get('exclusion_evidence'):
                errors.append(code + ': unexplained ex-ante exclusion')
            excluded[code] = why
            continue
        try:
            if iso(r['available_at']) > cutoff or r['asof_date'] != previous:
                raise ValueError('future/stale data')
            if not r.get('sector') or not r.get('source_urls') or r.get('amount_unit') != 'CNY':
                raise ValueError('missing sector/source or unknown amount unit')
            bars = r['bars']
            dates = [b['date'] for b in bars]
            if len(bars) < 61 or dates != sorted(set(dates)) or dates[-1] != previous:
                raise ValueError('need 61 unique ordered completed daily bars')
            for b in bars:
                if any(not finite(b.get(k)) for k in ('open', 'high', 'low', 'close', 'volume', 'amount')):
                    raise ValueError('non-finite OHLCVA')
                if not 0 < b['low'] <= min(b['open'], b['close']) <= max(b['open'], b['close']) <= b['high']:
                    raise ValueError('invalid OHLC')
                if b['volume'] < 0 or b['amount'] < 0:
                    raise ValueError('negative volume/amount')
            if st.median(b['amount'] for b in bars[-20:]) < 50_000_000:
                excluded[code] = 'liquidity_lt_50m'
                continue
            if not r.get('adjustment_basis') or not finite(r.get('raw_close')) or r['raw_close'] <= 0:
                raise ValueError('missing adjustment basis/raw close')
            for key in ('catalyst_points', 'earnings_points'):
                val = r.get(key, 0)
                if not finite(val) or not 0 <= val <= 5:
                    raise ValueError('event score must be 0..5')
                if val and (not r.get(key + '_evidence') or iso(r[key + '_public_at']) > cutoff):
                    raise ValueError('positive event score lacks as-of evidence')
            good[code] = r
        except (KeyError, TypeError, ValueError) as e:
            errors.append(code + ': ' + str(e))
    if len(good) < 20:
        errors.append('fewer than 20 eligible verified histories')
    return errors, good, excluded

def rank(data):
    errors, stocks, excluded = validate(data)
    receipt = {'version': VERSION, 'input_sha256': digest(data), 'cutoff': data['cutoff'],
               'data_commit': data.get('data_commit'), 'input_count': len(data['universe']),
               'verified_histories': len(stocks), 'excluded': excluded, 'errors': errors,
               'model_state': 'HEURISTIC_NOT_TRAINED_OR_VALIDATED', 'top5': []}
    if errors:
        receipt.update(ranking_health='INCOMPLETE', formal_top5_allowed=False)
        return receipt
    features = {}
    for code, r in stocks.items():
        b = r['bars']; c = [x['close'] for x in b]
        ma10 = st.mean(c[-10:]); prior_ma10 = st.mean(c[-13:-3])
        high, low = b[-1]['high'], b[-1]['low']
        close_pos = (c[-1] - low) / (high - low) if high > low else .5
        v20 = st.mean(x['volume'] for x in b[-20:])
        v5 = st.mean(x['volume'] for x in b[-5:])
        features[code] = {'r5': c[-1] / c[-6] - 1, 'r10': c[-1] / c[-11] - 1,
          'r20': c[-1] / c[-21] - 1, 'breakout': c[-1] / max(c[-61:-1]) - 1,
          'close_pos': close_pos, 'volume_ratio': v5 / v20 if v20 > 0 else 0,
          'trend': ma10 / prior_ma10 - 1, 'above_ma10': c[-1] >= ma10,
          'overheat': max(0., c[-1] / ma10 - 1 - .15), 'sector': r['sector']}
    sectors = {r['sector'] for r in stocks.values()}
    ss = {}
    for s in sectors:
        group = [f for f in features.values() if f['sector'] == s]
        ss[s] = st.median(f['r5'] for f in group) + .05 * st.mean(f['above_ma10'] for f in group)
    p = {k: percentiles({s:f[k] for s,f in features.items()}) for k in ('r5','r10','r20','breakout','volume_ratio','trend')}
    sectorp = percentiles(ss)
    out = []
    for code, f in features.items():
        r = stocks[code]
        momentum = .5*p['r5'][code] + .3*p['r10'][code] + .2*p['r20'][code]
        # Never penalize a prior gain on its own: require overheating AND weak close.
        penalty = min(15., 100*f['overheat']) if f['close_pos'] < .4 else 0.
        score = (30*momentum + 15*p['breakout'][code] + 10*f['close_pos']
                 + 15*sectorp[f['sector']] + 10*p['volume_ratio'][code] + 10*p['trend'][code]
                 + r.get('catalyst_points',0) + r.get('earnings_points',0) - penalty)
        out.append({'symbol':code,'name':r['name'],'sector':r['sector'], 'score':round(score,8),
                    'features':f,'penalty':penalty, 'reference_raw_close':r['raw_close'],
                    'catalyst_points':r.get('catalyst_points',0),'earnings_points':r.get('earnings_points',0)})
    out.sort(key=lambda r:(-r['score'], r['symbol']))
    for i, row in enumerate(out, 1): row['rank'] = i
    receipt.update(ranking_health='COMPLETE', formal_top5_allowed=True, ranking=out, top5=out[:5],
                   eligible_codes=sorted(stocks),
                   baselines={'momentum20_top5':sorted(stocks, key=lambda s:(-features[s]['r20'],s))[:5]},
                   notes=['Scores are not probabilities.', 'NO TRADE never removes a pick from selection evaluation.',
                          'This is a transparent bootstrap heuristic, not an estimated expected return.'])
    return receipt

def evaluate(cohort, outcomes):
    """Outcome mapping contains same-horizon corporate-action-adjusted returns.
    Missing universe outcomes block rank/precision statistics, never drop losers.
    NO TRADE is deliberately ignored here; execution belongs to a separate ledger.
    """
    codes = cohort['eligible_codes']; picks = [x['symbol'] for x in cohort['top5']]
    if len(picks) != 5 or len(set(picks)) != 5 or not set(picks) <= set(codes):
        raise ValueError('require five frozen eligible picks')
    if outcomes.get('forecast_date') != cohort['forecast_date'] or outcomes.get('sessions') != cohort['sessions']:
        raise ValueError('cohort/horizon mismatch')
    if len(cohort['sessions']) != 5 or cohort['sessions'] != sorted(set(cohort['sessions'])):
        raise ValueError('require five official distinct trading sessions')
    if not outcomes.get('source_urls') or iso(outcomes['observed_at']) < iso(cohort['sessions'][-1]+'T15:00:00+08:00'):
        raise ValueError('outcomes not yet mature or unsourced')
    if outcomes.get('basis') != 'total_return_previous_close_to_fifth_close':
        raise ValueError('unrecognized outcome horizon/adjustment basis')
    returns = {}
    for code, prices in outcomes.get('prices', {}).items():
        a, b = prices.get('reference'), prices.get('final')
        if finite(a) and finite(b) and a > 0 and b >= 0:
            returns[code] = b/a-1
    missing = [s for s in codes if not finite(returns.get(s))]
    selected = {s:returns.get(s) if finite(returns.get(s)) else None for s in picks}
    answer = {'cohort_id':cohort['forecast_date'], 'cohort_sha256':digest(cohort),
              'outcome_source_urls':outcomes.get('source_urls',[]), 'selected_returns':selected,
              'missing_outcomes':missing, 'selection_includes_no_trade':True,
              'mean_5d_return':st.mean(selected.values()) if all(v is not None for v in selected.values()) else None,
              'median_5d_return':st.median(selected.values()) if all(v is not None for v in selected.values()) else None,
              'rank_metrics_state':'INCOMPLETE' if missing else 'COMPLETE'}
    if missing: return answer
    winners = sorted(codes, key=lambda s:(-returns[s],s)); n = len(winners)
    ranks = {s:i+1 for i,s in enumerate(winners)}
    top5pct = set(winners[:max(1, math.ceil(.05*n))])
    top20 = set(winners[:min(20,n)])
    answer.update(exact_top5_overlap=len(set(picks)&set(winners[:5])),
                  precision5_top5pct=len(set(picks)&top5pct)/5,
                  actual_top20_captured=len(set(picks)&top20),
                  selected_actual_ranks={s:ranks[s] for s in picks},
                  actual_top20=[{'symbol':s,'return':returns[s]} for s in winners[:20]],
                  eligible_universe_mean=st.mean(returns[s] for s in codes),
                  baseline_mean_returns={k:st.mean(returns[s] for s in v) for k,v in cohort.get('baselines',{}).items()})
    return answer

def freeze(path, payload):
    """Create-only: never overwrite an existing prediction or silently replace it."""
    target = Path(path); target.parent.mkdir(parents=True,exist_ok=True)
    text = json.dumps(payload,ensure_ascii=False,indent=2,allow_nan=False)+'\n'
    with target.open('x',encoding='utf8') as f: f.write(text)
    return digest(payload)

def main():
    ap = argparse.ArgumentParser(); ap.add_argument('mode',choices=['rank','evaluate'])
    ap.add_argument('input'); ap.add_argument('output'); ap.add_argument('--outcomes')
    a = ap.parse_args(); data=json.loads(Path(a.input).read_text(encoding='utf8'))
    result = rank(data) if a.mode=='rank' else evaluate(data,json.loads(Path(a.outcomes).read_text(encoding='utf8')))
    freeze(a.output,result)
    print(json.dumps({k:v for k,v in result.items() if k not in ('ranking','excluded','errors','eligible_codes')},ensure_ascii=False))

if __name__=='__main__': main()
