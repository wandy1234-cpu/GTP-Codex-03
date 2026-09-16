#!/usr/bin/env python3
import concurrent.futures
import datetime as dt
import json
import math
import os
import re
import statistics
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

TZ = dt.timezone(dt.timedelta(hours=8))
NOW = dt.datetime.now(TZ)

SYMBOLS = [
    "sh601872","sh688766","sh688008","sh688183","sz300765","sz001389",
    "sh688333","sh603268","sh600988","sz159246","sz301122","sh688002",
    "sh518880","sh513500","sh589720",
    "sh688331","sz300779","sz002001","sz002838","sh601233","sh600989",
    "sh603993","sz000703","sz002493","sh600299","sz159731",
    "hk00883","hk01530","hk01456"
]

OUT = Path("data/latest_market.json")
OUT.parent.mkdir(parents=True, exist_ok=True)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/124 Safari/537.36",
    "Accept": "application/json,text/plain,*/*",
}


def get(url, referer=None, timeout=8):
    h = dict(HEADERS)
    if referer:
        h["Referer"] = referer
    req = urllib.request.Request(url, headers=h)
    t0 = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            b = r.read(2_000_000)
            status = r.status
            ct = r.headers.get("Content-Type", "")
        return {"ok": True, "status": status, "content_type": ct,
                "elapsed": round(time.monotonic()-t0, 3), "body": b}
    except Exception as e:
        return {"ok": False, "elapsed": round(time.monotonic()-t0, 3),
                "error": f"{type(e).__name__}: {e}"[:300]}


def to_text(resp, enc="utf-8"):
    if not resp.get("ok"):
        return ""
    return resp["body"].decode(enc, errors="replace").strip()


def parse_jsonish(text):
    s = text.strip()
    if not s:
        raise ValueError("empty body")
    if s.startswith(("{", "[")):
        return json.loads(s)
    m = re.search(r"=\s*([\[{].*[\]}])\s*;?\s*$", s, re.S)
    if m:
        return json.loads(m.group(1))
    raise ValueError("unsupported wrapper")


def num(x):
    try:
        v = float(x)
        return v if math.isfinite(v) else None
    except Exception:
        return None


def parse_ts(s):
    s = str(s or "").strip()
    for fmt in ("%Y%m%d%H%M%S", "%Y-%m-%d %H:%M", "%Y%m%d%H%M", "%Y-%m-%d %H:%M:%S"):
        try:
            return dt.datetime.strptime(s, fmt).replace(tzinfo=TZ).isoformat()
        except Exception:
            pass
    return None


def tencent_quote_batch(symbols):
    url = "https://qt.gtimg.cn/q=" + ",".join(symbols)
    r = get(url, "https://gu.qq.com/")
    out = {"source": "tencent_quote", "url": url, "transport": {k:v for k,v in r.items() if k != "body"}, "records": {}}
    if not r.get("ok"):
        return out
    text = to_text(r, "gb18030")
    for sym, body in re.findall(r"v_(\w+)=\"([^\"]*)\"", text):
        f = body.split("~")
        rec = {"field_count": len(f), "raw_head": f[:45]}
        if len(f) > 5:
            rec.update(name=f[1], code=f[2], price=num(f[3]), prev_close=num(f[4]), open=num(f[5]))
        if len(f) > 30:
            rec["quote_time"] = parse_ts(f[30])
        if len(f) > 34:
            rec["change"] = num(f[31]); rec["change_pct"] = num(f[32]); rec["high"] = num(f[33]); rec["low"] = num(f[34])
        out["records"][sym] = rec
    return out


def find_series(obj, wanted):
    found = []
    def walk(x, path=""):
        if isinstance(x, dict):
            for k,v in x.items():
                walk(v, path + "/" + str(k))
        elif isinstance(x, list):
            if x and all(isinstance(z, (list,str)) for z in x[:min(4,len(x))]):
                score = 0
                sample = x[-1]
                row = sample if isinstance(sample,list) else str(sample).split()
                if len(row) >= wanted:
                    score += 2
                if row and re.match(r"^(\d{4}|\d{8,14}|\d{4}-\d{2}-\d{2})", str(row[0])):
                    score += 3
                if score:
                    found.append((score, len(x), path, x))
            for i,v in enumerate(x[:5]):
                if isinstance(v,(dict,list)): walk(v, path+f"/{i}")
    walk(obj)
    if not found:
        return None, None
    found.sort(key=lambda z:(z[0],z[1]), reverse=True)
    return found[0][2], found[0][3]


def normalize_bar(row):
    if isinstance(row, str):
        row = row.replace(",", " ").split()
    if not isinstance(row, list) or len(row) < 6:
        return None
    vals = [str(x) for x in row]
    # Tencent kline convention: time, open, close, high, low, volume, [amount...]
    return {
        "time": vals[0], "open": num(vals[1]), "close": num(vals[2]),
        "high": num(vals[3]), "low": num(vals[4]), "volume_raw": num(vals[5]),
        "extra": vals[6:10]
    }


def tencent_kline(symbol, interval="m5", limit=80):
    if interval == "day":
        url = f"https://proxy.finance.qq.com/ifzqgtimg/appstock/app/newfqkline/get?param={symbol},day,,,{limit},qfq"
    else:
        url = f"https://web.ifzq.gtimg.cn/appstock/app/kline/mkline?param={symbol},{interval},,{limit}"
    r = get(url, "https://gu.qq.com/")
    out = {"source": "tencent_"+interval, "url": url, "transport": {k:v for k,v in r.items() if k != "body"}}
    if not r.get("ok"):
        return out
    try:
        obj = parse_jsonish(to_text(r))
        path, series = find_series(obj, 6)
        bars = [normalize_bar(x) for x in (series or [])]
        bars = [x for x in bars if x and x["close"] is not None]
        out.update(path=path, bars=bars[-limit:])
    except Exception as e:
        out["parse_error"] = f"{type(e).__name__}: {e}"[:250]
    return out


def eastmoney_quote(symbol):
    market = "1" if symbol.startswith("sh") else "0"
    code = symbol[2:]
    url = ("https://push2.eastmoney.com/api/qt/stock/get?" + urllib.parse.urlencode({
        "secid": f"{market}.{code}", "fields": "f43,f44,f45,f46,f47,f48,f57,f58,f59,f60,f86,f168,f169,f170"
    }))
    r = get(url, "https://quote.eastmoney.com/")
    out = {"source":"eastmoney_quote", "url":url, "transport":{k:v for k,v in r.items() if k != "body"}}
    if not r.get("ok"): return out
    try:
        o = json.loads(to_text(r)); d = o.get("data") or {}; scale = 10 ** int(d.get("f59") or 2)
        out["record"] = {
            "name": d.get("f58"), "code": d.get("f57"),
            "price": (d.get("f43")/scale if isinstance(d.get("f43"),(int,float)) else None),
            "high": (d.get("f44")/scale if isinstance(d.get("f44"),(int,float)) else None),
            "low": (d.get("f45")/scale if isinstance(d.get("f45"),(int,float)) else None),
            "open": (d.get("f46")/scale if isinstance(d.get("f46"),(int,float)) else None),
            "prev_close": (d.get("f60")/scale if isinstance(d.get("f60"),(int,float)) else None),
            "quote_time": dt.datetime.fromtimestamp(d["f86"], TZ).isoformat() if d.get("f86") else None,
            "volume_raw": d.get("f47"), "amount_raw": d.get("f48"), "turnover_raw": d.get("f168"),
            "change_raw": d.get("f169"), "change_pct_raw": d.get("f170")
        }
    except Exception as e: out["parse_error"] = f"{type(e).__name__}: {e}"[:250]
    return out


def metrics(day_bars, m5_bars):
    out = {}
    closes = [x["close"] for x in day_bars if x.get("close") is not None]
    highs = [x["high"] for x in day_bars if x.get("high") is not None]
    lows = [x["low"] for x in day_bars if x.get("low") is not None]
    for n in (5,10,20,60):
        if len(closes) >= n: out[f"ma{n}"] = round(sum(closes[-n:])/n, 6)
    if len(closes) >= 15 and len(highs) >= 15 and len(lows) >= 15:
        trs=[]
        for i in range(1, len(closes)):
            trs.append(max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1])))
        if len(trs)>=14: out["atr14"] = round(sum(trs[-14:])/14, 6)
    valid=[x for x in m5_bars if all(x.get(k) is not None for k in ("open","close","high","low"))]
    if len(valid)>=4:
        last=valid[-6:]
        out["m5_last"] = last
        out["m5_higher_lows"] = all(last[i]["low"] >= last[i-1]["low"] for i in range(1,len(last)))
        out["m5_lower_lows"] = all(last[i]["low"] <= last[i-1]["low"] for i in range(1,len(last)))
        out["m5_higher_highs"] = all(last[i]["high"] >= last[i-1]["high"] for i in range(1,len(last)))
        out["m5_lower_highs"] = all(last[i]["high"] <= last[i-1]["high"] for i in range(1,len(last)))
    return out


def per_symbol(symbol):
    # Hong Kong quote is supported by Tencent; kline paths differ, so relay quote only until separately validated.
    eq = eastmoney_quote(symbol) if symbol.startswith(("sh","sz")) else {"source":"eastmoney_quote","skipped":"non-mainland"}
    m5 = tencent_kline(symbol,"m5",80)
    m15 = tencent_kline(symbol,"m15",50)
    day = tencent_kline(symbol,"day",100)
    db=day.get("bars") or []; b5=m5.get("bars") or []
    return {"eastmoney":eq,"m5":m5,"m15":m15,"day":day,"metrics":metrics(db,b5)}


def main():
    market_minutes = ((9,25) <= (NOW.hour,NOW.minute) <= (11,35)) or ((13,0) <= (NOW.hour,NOW.minute) <= (15,10))
    tq = tencent_quote_batch(SYMBOLS)
    result = {
        "schema_version":"2.0",
        "generated_at_beijing": NOW.isoformat(),
        "weekday": NOW.weekday(),
        "market_window": market_minutes,
        "relay_policy": "Public data only. Source timestamps are authoritative; fetch time is never treated as quote time.",
        "tencent_quote": tq,
        "symbols": {}
    }
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
        futs={ex.submit(per_symbol,s):s for s in SYMBOLS}
        for fut in concurrent.futures.as_completed(futs):
            s=futs[fut]
            try: result["symbols"][s]=fut.result()
            except Exception as e: result["symbols"][s]={"error":f"{type(e).__name__}: {e}"[:300]}
    # Health summary based only on source-stamped quote freshness.
    fresh=0; total=0
    now=NOW
    qrecs=tq.get("records") or {}
    for s in SYMBOLS:
        if not s.startswith(("sh","sz")): continue
        total+=1; times=[]
        qt=qrecs.get(s,{}).get("quote_time")
        et=((result["symbols"].get(s) or {}).get("eastmoney") or {}).get("record",{}).get("quote_time")
        for t in (qt,et):
            if t:
                try: times.append(dt.datetime.fromisoformat(t))
                except Exception: pass
        if times and min(abs((now-t).total_seconds()) for t in times) <= 600: fresh+=1
    result["health"]={"mainland_symbols":total,"fresh_quotes_le_10m":fresh,
                      "quote_health":"GREEN" if total and fresh/total>=0.8 else ("YELLOW" if total and fresh/total>=0.5 else "RED")}
    tmp=OUT.with_suffix(".tmp")
    tmp.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
    tmp.replace(OUT)
    print(json.dumps(result["health"],ensure_ascii=False))

if __name__ == "__main__":
    main()
