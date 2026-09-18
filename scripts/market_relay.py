#!/usr/bin/env python3
import concurrent.futures
import datetime as dt
import json
import math
import re
import time
import urllib.parse
import urllib.request
from pathlib import Path

TZ = dt.timezone(dt.timedelta(hours=8))
NOW = dt.datetime.now(TZ)
OUT = Path("data/latest_market.json")
OUT.parent.mkdir(parents=True, exist_ok=True)

HOLDINGS = [
    "sz001389","sz159246","sz300765","sz301122","sh600150",
    "sh600900","sh600988","sh601872","sh603268","sh688002",
    "sh688008","sh688183","sh688333","sh688621","sh688766"
]

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
            body = r.read(2_000_000)
            return {"ok": True, "status": r.status, "elapsed": round(time.monotonic()-t0,3),
                    "content_type": r.headers.get("Content-Type",""), "body": body}
    except Exception as e:
        return {"ok": False, "elapsed": round(time.monotonic()-t0,3),
                "error": f"{type(e).__name__}: {e}"[:300]}

def text(resp, enc="utf-8"):
    return resp["body"].decode(enc, errors="replace").strip() if resp.get("ok") else ""

def num(x):
    try:
        v = float(x)
        return v if math.isfinite(v) else None
    except Exception:
        return None

def ts(s):
    s = str(s or "").strip()
    for fmt in ("%Y%m%d%H%M%S","%Y-%m-%d %H:%M","%Y%m%d%H%M","%Y-%m-%d %H:%M:%S"):
        try:
            return dt.datetime.strptime(s,fmt).replace(tzinfo=TZ).isoformat()
        except Exception:
            pass
    return None

def age_seconds(stamp):
    if not stamp:
        return None
    try:
        return abs((NOW-dt.datetime.fromisoformat(stamp)).total_seconds())
    except Exception:
        return None

def tencent_quotes(symbols):
    url = "https://qt.gtimg.cn/q=" + ",".join(symbols)
    r = get(url, "https://gu.qq.com/")
    out = {"source":"tencent_qt","transport":{k:v for k,v in r.items() if k!="body"},"records":{}}
    if not r.get("ok"):
        return out
    for sym, body in re.findall(r'v_(\w+)="([^"]*)"', text(r,"gb18030")):
        f = body.split("~")
        rec = {"name": f[1] if len(f)>1 else None, "code": f[2] if len(f)>2 else None,
               "price": num(f[3]) if len(f)>3 else None, "prev_close": num(f[4]) if len(f)>4 else None,
               "open": num(f[5]) if len(f)>5 else None, "quote_time": ts(f[30]) if len(f)>30 else None,
               "change": num(f[31]) if len(f)>31 else None, "change_pct": num(f[32]) if len(f)>32 else None,
               "high": num(f[33]) if len(f)>33 else None, "low": num(f[34]) if len(f)>34 else None}
        rec["age_seconds"] = age_seconds(rec["quote_time"])
        out["records"][sym] = rec
    return out

def secid(sym):
    return ("1" if sym.startswith("sh") else "0") + "." + sym[2:]

def east_quote(sym):
    url = "https://push2.eastmoney.com/api/qt/stock/get?" + urllib.parse.urlencode({
        "secid":secid(sym),"fields":"f43,f44,f45,f46,f47,f48,f57,f58,f59,f60,f86,f168,f169,f170"})
    r = get(url,"https://quote.eastmoney.com/")
    out = {"source":"eastmoney_push2","transport":{k:v for k,v in r.items() if k!="body"}}
    if not r.get("ok"):
        return out
    try:
        d = (json.loads(text(r)).get("data") or {})
        scale = 10 ** int(d.get("f59") or 2)
        rec = {
            "name":d.get("f58"),"code":d.get("f57"),
            "price":d.get("f43")/scale if isinstance(d.get("f43"),(int,float)) else None,
            "high":d.get("f44")/scale if isinstance(d.get("f44"),(int,float)) else None,
            "low":d.get("f45")/scale if isinstance(d.get("f45"),(int,float)) else None,
            "open":d.get("f46")/scale if isinstance(d.get("f46"),(int,float)) else None,
            "prev_close":d.get("f60")/scale if isinstance(d.get("f60"),(int,float)) else None,
            "quote_time":dt.datetime.fromtimestamp(d["f86"],TZ).isoformat() if d.get("f86") else None,
            "volume_raw":d.get("f47"),"amount_raw":d.get("f48")
        }
        rec["age_seconds"] = age_seconds(rec["quote_time"])
        out["record"] = rec
    except Exception as e:
        out["parse_error"] = f"{type(e).__name__}: {e}"[:250]
    return out

def east_kline(sym,klt,limit):
    url = "https://push2his.eastmoney.com/api/qt/stock/kline/get?" + urllib.parse.urlencode({
        "secid":secid(sym),"klt":klt,"fqt":1,"lmt":limit,"end":"20500101",
        "fields1":"f1,f2,f3,f4,f5,f6","fields2":"f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61"})
    r = get(url,"https://quote.eastmoney.com/")
    out = {"source":f"eastmoney_klt{klt}","transport":{k:v for k,v in r.items() if k!="body"},"bars":[]}
    if not r.get("ok"):
        return out
    try:
        for line in ((json.loads(text(r)).get("data") or {}).get("klines") or []):
            f = str(line).split(",")
            if len(f) >= 7:
                out["bars"].append({"time":f[0],"open":num(f[1]),"close":num(f[2]),"high":num(f[3]),
                                    "low":num(f[4]),"volume":num(f[5]),"amount":num(f[6])})
    except Exception as e:
        out["parse_error"] = f"{type(e).__name__}: {e}"[:250]
    return out

def parse_jsonish(s):
    s=s.strip()
    if s.startswith(("{","[")):
        return json.loads(s)
    m=re.search(r"=\s*([\[{].*[\]}])\s*;?\s*$",s,re.S)
    if m:
        return json.loads(m.group(1))
    raise ValueError("unsupported wrapper")

def find_series(obj):
    found=[]
    def walk(x,path=""):
        if isinstance(x,dict):
            for k,v in x.items(): walk(v,path+"/"+str(k))
        elif isinstance(x,list):
            if x and all(isinstance(z,(list,str)) for z in x[:min(4,len(x))]):
                sample=x[-1]
                row=sample if isinstance(sample,list) else str(sample).split()
                score=(2 if len(row)>=6 else 0)+(3 if row and re.match(r"^(\d{4}|\d{8,14}|\d{4}-\d{2}-\d{2})",str(row[0])) else 0)
                if score: found.append((score,len(x),path,x))
            for i,v in enumerate(x[:4]):
                if isinstance(v,(dict,list)): walk(v,path+f"/{i}")
    walk(obj)
    if not found: return None,None
    found.sort(key=lambda z:(z[0],z[1]),reverse=True)
    return found[0][2],found[0][3]

def tencent_kline(sym,interval,limit):
    if interval=="day":
        url=f"https://proxy.finance.qq.com/ifzqgtimg/appstock/app/newfqkline/get?param={sym},day,,,{limit},qfq"
    else:
        url=f"https://web.ifzq.gtimg.cn/appstock/app/kline/mkline?param={sym},{interval},,{limit}"
    r=get(url,"https://gu.qq.com/")
    out={"source":f"tencent_{interval}","transport":{k:v for k,v in r.items() if k!="body"},"bars":[]}
    if not r.get("ok"): return out
    try:
        _,series=find_series(parse_jsonish(text(r)))
        for row in (series or []):
            if isinstance(row,str): row=row.replace(","," ").split()
            if isinstance(row,list) and len(row)>=6:
                out["bars"].append({"time":str(row[0]),"open":num(row[1]),"close":num(row[2]),
                                    "high":num(row[3]),"low":num(row[4]),"volume":num(row[5]),
                                    "amount":num(row[6]) if len(row)>6 else None})
    except Exception as e:
        out["parse_error"]=f"{type(e).__name__}: {e}"[:250]
    return out

def choose(primary,backup,minbars=4):
    p=primary.get("bars") or []
    b=backup.get("bars") or []
    if len(p)>=minbars: return p,primary["source"],"primary"
    if len(b)>=minbars: return b,backup["source"],"backup"
    return p or b,(primary["source"] if p else backup["source"]),"insufficient"

def daily_metrics(bars):
    valid=[x for x in bars if all(x.get(k) is not None for k in ("close","high","low"))]
    closes=[x["close"] for x in valid]; highs=[x["high"] for x in valid]; lows=[x["low"] for x in valid]
    out={}
    for n in (5,10,20,60):
        if len(closes)>=n: out[f"ma{n}"]=round(sum(closes[-n:])/n,6)
    if len(closes)>=15:
        tr=[]
        for i in range(1,len(closes)):
            tr.append(max(highs[i]-lows[i],abs(highs[i]-closes[i-1]),abs(lows[i]-closes[i-1])))
        if len(tr)>=14: out["atr14"]=round(sum(tr[-14:])/14,6)
    if len(valid)>=6:
        out["recent_6d_low"]=min(x["low"] for x in valid[-6:])
        out["recent_6d_high"]=max(x["high"] for x in valid[-6:])
    return out

def intraday_metrics(bars,price):
    v=[x for x in bars if all(x.get(k) is not None for k in ("open","close","high","low"))][-8:]
    out={"last_bars":v}
    if len(v)>=4:
        out["lower_lows"]=all(v[i]["low"]<=v[i-1]["low"] for i in range(1,len(v)))
        out["lower_highs"]=all(v[i]["high"]<=v[i-1]["high"] for i in range(1,len(v)))
    today=NOW.strftime("%Y-%m-%d")
    same=[x for x in bars if str(x.get("time","")).startswith(today) and x.get("amount") not in (None,0) and x.get("volume") not in (None,0)]
    if same and price:
        amt=sum(x["amount"] for x in same); vol=sum(x["volume"] for x in same)
        if vol:
            raw=amt/vol
            candidates=[raw,raw/100.0]
            plausible=[x for x in candidates if abs(x-price)/price<=0.20]
            if plausible:
                out["vwap"]=round(min(plausible,key=lambda x:abs(x-price)),6)
                out["vwap_source"]="eastmoney amount/volume with automatic provider-unit validation"
    return out

def base_symbol(sym):
    emday=east_kline(sym,101,100)
    tqday=tencent_kline(sym,"day",100)
    bars,source,quality=choose(emday,tqday,20)
    return {"daily_source":source,"daily_quality":quality,
            "daily_metrics":daily_metrics(bars),"daily_recent":bars[-65:]}

def danger_score(q,m):
    p=q.get("price")
    if not p: return -999
    s=max(0,-(q.get("change_pct") or 0))
    ma10=m.get("ma10"); ma20=m.get("ma20"); low=m.get("recent_6d_low")
    if ma10 and p<ma10: s+=2
    if ma20 and p<ma20: s+=3
    if low and p<low: s+=4
    return s

def deep_symbol(sym,price):
    eq=east_quote(sym)
    em5=east_kline(sym,5,120); tq5=tencent_kline(sym,"m5",120)
    em15=east_kline(sym,15,80); tq15=tencent_kline(sym,"m15",80)
    b5,s5,q5=choose(em5,tq5); b15,s15,q15=choose(em15,tq15)
    return {"eastmoney_quote":eq,
            "m5":{"source":s5,"quality":q5,"metrics":intraday_metrics(b5,price)},
            "m15":{"source":s15,"quality":q15,"metrics":intraday_metrics(b15,price)}}

def main():
    tq=tencent_quotes(HOLDINGS)
    qrec=tq.get("records") or {}
    result={"schema_version":"2.2","generated_at_beijing":NOW.isoformat(),"holdings":HOLDINGS,
            "policy":"Data Route V2.2; security timestamps authoritative; fetch time never substitutes quote time.",
            "tencent_quote":tq,"symbols":{}}

    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as ex:
        futs={ex.submit(base_symbol,s):s for s in HOLDINGS}
        for fut in concurrent.futures.as_completed(futs):
            s=futs[fut]
            try: result["symbols"][s]=fut.result()
            except Exception as e: result["symbols"][s]={"error":f"{type(e).__name__}: {e}"[:300]}

    ranked=[]
    for s in HOLDINGS:
        ranked.append((danger_score(qrec.get(s,{ }),(result["symbols"].get(s) or {}).get("daily_metrics") or {}),s))
    ranked.sort(reverse=True)
    candidates=[s for _,s in ranked[:5]]
    result["deep_candidates"]=candidates

    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as ex:
        futs={ex.submit(deep_symbol,s,(qrec.get(s) or {}).get("price")):s for s in candidates}
        for fut in concurrent.futures.as_completed(futs):
            s=futs[fut]
            try: result["symbols"][s]["deep"]=fut.result()
            except Exception as e: result["symbols"][s]["deep"]={"error":f"{type(e).__name__}: {e}"[:300]}

    fresh=0; per={}; deep_green=0
    for s in HOLDINGS:
        q=qrec.get(s,{})
        qfresh=q.get("price") is not None and q.get("age_seconds") is not None and q["age_seconds"]<=600
        if qfresh: fresh+=1
        row={"tencent_fresh":qfresh}
        if s in candidates:
            d=(result["symbols"].get(s) or {}).get("deep") or {}
            er=(d.get("eastmoney_quote") or {}).get("record") or {}
            efresh=er.get("price") is not None and er.get("age_seconds") is not None and er["age_seconds"]<=600
            agree=None
            if qfresh and efresh and q.get("price") and er.get("price"):
                timediff=abs(dt.datetime.fromisoformat(q["quote_time"]).timestamp()-dt.datetime.fromisoformat(er["quote_time"]).timestamp())
                pricediff=abs(q["price"]-er["price"])/((q["price"]+er["price"])/2)
                agree=(timediff<=300 and pricediff<=0.005)
            kready=(d.get("m5",{}).get("quality")!="insufficient" and d.get("m15",{}).get("quality")!="insufficient")
            row.update({"eastmoney_fresh":efresh,"dual_agree":agree,"deep_kline_ready":kready})
            if agree and kready: deep_green+=1
        per[s]=row

    if fresh <= len(HOLDINGS)//2:
        health="RED"
    elif fresh==len(HOLDINGS) and deep_green==len(candidates):
        health="GREEN"
    else:
        health="YELLOW"
    result["health"]={"holdings":len(HOLDINGS),"fresh_quotes_le_10m":fresh,
                      "deep_candidates_green":deep_green,"deep_candidates":len(candidates),
                      "quote_health":health,"per_holding":per}

    tmp=OUT.with_suffix(".tmp")
    tmp.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
    tmp.replace(OUT)
    print(json.dumps(result["health"],ensure_ascii=False))

if __name__=="__main__":
    main()
