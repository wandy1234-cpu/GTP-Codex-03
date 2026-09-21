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

# Wandy personal credit-account holdings, 2026-09-18 15:43 baseline.
HOLDINGS = [
    "sh603268",  # 松发股份
    "sh688766",  # 普冉股份
    "sh688008",  # 澜起科技
    "sh688758",  # 赛分科技
    "sh600988",  # 赤峰黄金
    "sz300765",  # 石药创新
    "sh688002",  # 睿创微纳
    "sh600150",  # 中国船舶
    "sz002768",  # 国恩股份
    "sh601233",  # 桐昆股份
    "sh688676",  # 金盘科技
    "sh600926",  # 杭州银行
    "sz300759",  # 康龙化成
    "sh600900",  # 长江电力
]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/124 Safari/537.36",
    "Accept": "application/json,text/plain,*/*",
}


def get(url, referer=None, timeout=9):
    h = dict(HEADERS)
    if referer:
        h["Referer"] = referer
    req = urllib.request.Request(url, headers=h)
    t0 = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read(3_000_000)
            return {"ok": True, "status": r.status,
                    "elapsed": round(time.monotonic() - t0, 3),
                    "content_type": r.headers.get("Content-Type", ""),
                    "body": body}
    except Exception as e:
        return {"ok": False, "elapsed": round(time.monotonic() - t0, 3),
                "error": f"{type(e).__name__}: {e}"[:300]}


def txt(resp, enc="utf-8"):
    if not resp.get("ok"):
        return ""
    return resp["body"].decode(enc, errors="replace").strip()


def num(x):
    try:
        v = float(x)
        return v if math.isfinite(v) else None
    except Exception:
        return None


def parse_ts(s):
    s = str(s or "").strip()
    for fmt in ("%Y%m%d%H%M%S", "%Y%m%d%H%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return dt.datetime.strptime(s, fmt).replace(tzinfo=TZ).isoformat()
        except Exception:
            pass
    return None


def age_seconds(stamp):
    if not stamp:
        return None
    try:
        return abs((NOW - dt.datetime.fromisoformat(stamp)).total_seconds())
    except Exception:
        return None


def secid(sym):
    return ("1" if sym.startswith("sh") else "0") + "." + sym[2:]


def tencent_quotes(symbols):
    url = "https://qt.gtimg.cn/q=" + ",".join(symbols)
    r = get(url, "https://gu.qq.com/")
    out = {"source": "tencent_qt", "url": url,
           "transport": {k: v for k, v in r.items() if k != "body"},
           "records": {}}
    if not r.get("ok"):
        return out
    text = txt(r, "gb18030")
    for sym, body in re.findall(r'v_(\w+)="([^"]*)"', text):
        f = body.split("~")
        rec = {
            "name": f[1] if len(f) > 1 else None,
            "code": f[2] if len(f) > 2 else None,
            "price": num(f[3]) if len(f) > 3 else None,
            "prev_close": num(f[4]) if len(f) > 4 else None,
            "open": num(f[5]) if len(f) > 5 else None,
            "quote_time": parse_ts(f[30]) if len(f) > 30 else None,
            "change": num(f[31]) if len(f) > 31 else None,
            "change_pct": num(f[32]) if len(f) > 32 else None,
            "high": num(f[33]) if len(f) > 33 else None,
            "low": num(f[34]) if len(f) > 34 else None,
        }
        rec["age_seconds"] = age_seconds(rec.get("quote_time"))
        out["records"][sym] = rec
    return out


def east_quote(sym):
    url = "https://push2.eastmoney.com/api/qt/stock/get?" + urllib.parse.urlencode({
        "secid": secid(sym),
        "fields": "f43,f44,f45,f46,f47,f48,f57,f58,f59,f60,f86,f168,f169,f170"
    })
    r = get(url, "https://quote.eastmoney.com/")
    out = {"source": "eastmoney_push2", "url": url,
           "transport": {k: v for k, v in r.items() if k != "body"}}
    if not r.get("ok"):
        return out
    try:
        d = (json.loads(txt(r)).get("data") or {})
        scale = 10 ** int(d.get("f59") or 2)
        rec = {
            "name": d.get("f58"), "code": d.get("f57"),
            "price": d.get("f43") / scale if isinstance(d.get("f43"), (int, float)) else None,
            "high": d.get("f44") / scale if isinstance(d.get("f44"), (int, float)) else None,
            "low": d.get("f45") / scale if isinstance(d.get("f45"), (int, float)) else None,
            "open": d.get("f46") / scale if isinstance(d.get("f46"), (int, float)) else None,
            "prev_close": d.get("f60") / scale if isinstance(d.get("f60"), (int, float)) else None,
            "quote_time": dt.datetime.fromtimestamp(d["f86"], TZ).isoformat() if d.get("f86") else None,
            "volume_raw": d.get("f47"), "amount_raw": d.get("f48"),
        }
        rec["age_seconds"] = age_seconds(rec.get("quote_time"))
        out["record"] = rec
    except Exception as e:
        out["parse_error"] = f"{type(e).__name__}: {e}"[:250]
    return out


def sina_quote(sym):
    url = "https://hq.sinajs.cn/list=" + sym
    r = get(url, "https://finance.sina.com.cn/")
    out = {"source": "sina_hq", "url": url,
           "transport": {k: v for k, v in r.items() if k != "body"}}
    if not r.get("ok"):
        return out
    try:
        m = re.search(r'="([^"]*)"', txt(r, "gb18030"))
        f = (m.group(1) if m else "").split(",")
        if len(f) >= 32:
            rec = {"name": f[0], "price": num(f[3]), "prev_close": num(f[2]),
                   "open": num(f[1]), "high": num(f[4]), "low": num(f[5]),
                   "quote_time": parse_ts(f[30] + " " + f[31])}
            rec["age_seconds"] = age_seconds(rec.get("quote_time"))
            out["record"] = rec
    except Exception as e:
        out["parse_error"] = f"{type(e).__name__}: {e}"[:250]
    return out


def east_kline(sym, klt, limit):
    url = "https://push2his.eastmoney.com/api/qt/stock/kline/get?" + urllib.parse.urlencode({
        "secid": secid(sym), "klt": klt, "fqt": 1, "lmt": limit, "end": "20500101",
        "fields1": "f1,f2,f3,f4,f5,f6",
        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61"
    })
    r = get(url, "https://quote.eastmoney.com/")
    out = {"source": f"eastmoney_klt{klt}", "url": url,
           "transport": {k: v for k, v in r.items() if k != "body"}, "bars": []}
    if not r.get("ok"):
        return out
    try:
        for line in ((json.loads(txt(r)).get("data") or {}).get("klines") or []):
            f = str(line).split(",")
            if len(f) >= 7:
                out["bars"].append({"time": f[0], "open": num(f[1]), "close": num(f[2]),
                                    "high": num(f[3]), "low": num(f[4]),
                                    "volume": num(f[5]), "amount": num(f[6])})
    except Exception as e:
        out["parse_error"] = f"{type(e).__name__}: {e}"[:250]
    return out


def parse_jsonish(s):
    s = s.strip()
    if s.startswith(("{", "[")):
        return json.loads(s)
    m = re.search(r"=\s*([\[{].*[\]}])\s*;?\s*$", s, re.S)
    if m:
        return json.loads(m.group(1))
    raise ValueError("unsupported wrapper")


def find_series(obj):
    found = []
    def walk(x, path=""):
        if isinstance(x, dict):
            for k, v in x.items():
                walk(v, path + "/" + str(k))
        elif isinstance(x, list):
            if x and all(isinstance(z, (list, str)) for z in x[:min(4, len(x))]):
                sample = x[-1]
                row = sample if isinstance(sample, list) else str(sample).split()
                score = (2 if len(row) >= 6 else 0) + (3 if row and re.match(r"^(\d{4}|\d{8,14}|\d{4}-\d{2}-\d{2})", str(row[0])) else 0)
                if score:
                    found.append((score, len(x), path, x))
            for i, v in enumerate(x[:4]):
                if isinstance(v, (dict, list)):
                    walk(v, path + f"/{i}")
    walk(obj)
    if not found:
        return None, None
    found.sort(key=lambda z: (z[0], z[1]), reverse=True)
    return found[0][2], found[0][3]


def tencent_kline(sym, interval, limit):
    if interval == "day":
        url = f"https://proxy.finance.qq.com/ifzqgtimg/appstock/app/newfqkline/get?param={sym},day,,,{limit},qfq"
    else:
        url = f"https://web.ifzq.gtimg.cn/appstock/app/kline/mkline?param={sym},{interval},,{limit}"
    r = get(url, "https://gu.qq.com/")
    out = {"source": f"tencent_{interval}", "url": url,
           "transport": {k: v for k, v in r.items() if k != "body"}, "bars": []}
    if not r.get("ok"):
        return out
    try:
        _, series = find_series(parse_jsonish(txt(r)))
        for row in (series or []):
            if isinstance(row, str):
                row = row.replace(",", " ").split()
            if isinstance(row, list) and len(row) >= 6:
                out["bars"].append({"time": str(row[0]), "open": num(row[1]), "close": num(row[2]),
                                    "high": num(row[3]), "low": num(row[4]), "volume": num(row[5]),
                                    "amount": num(row[6]) if len(row) > 6 else None})
    except Exception as e:
        out["parse_error"] = f"{type(e).__name__}: {e}"[:250]
    return out


def minute_query(sym):
    url = f"https://web.ifzq.gtimg.cn/appstock/app/minute/query?code={sym}&_ts={int(time.time()*1000)}"
    r = get(url, "https://gu.qq.com/")
    out = {"source": "tencent_minute_query", "url": url,
           "transport": {k: v for k, v in r.items() if k != "body"},
           "date": None, "rows": []}
    if not r.get("ok"):
        return out
    try:
        obj = parse_jsonish(txt(r))
        dates = []
        candidates = []
        def walk(x):
            if isinstance(x, dict):
                for k, v in x.items():
                    if str(k).lower() == "date" and re.fullmatch(r"\d{8}", str(v or "")):
                        dates.append(str(v))
                    walk(v)
            elif isinstance(x, list):
                if x and all(isinstance(z, str) for z in x[:min(5, len(x))]):
                    hits = [z for z in x if re.match(r"^\d{4}\s+[-+]?\d", z)]
                    if len(hits) >= 2:
                        candidates.append(hits)
                for v in x[:8]:
                    if isinstance(v, (dict, list)):
                        walk(v)
        walk(obj)
        if dates:
            out["date"] = dates[0]
        if candidates:
            rows = max(candidates, key=len)
            parsed = []
            for s in rows:
                f = re.split(r"\s+", s.strip())
                if len(f) < 2:
                    continue
                parsed.append({"hhmm": f[0], "price": num(f[1]),
                               "cum_volume": num(f[2]) if len(f) > 2 else None,
                               "cum_amount": num(f[3]) if len(f) > 3 else None,
                               "raw": s})
            out["rows"] = parsed
            if parsed:
                out["last_hhmm"] = parsed[-1]["hhmm"]
                if out.get("date"):
                    out["last_time"] = parse_ts(out["date"] + parsed[-1]["hhmm"])
                    out["last_age_seconds"] = age_seconds(out["last_time"])
    except Exception as e:
        out["parse_error"] = f"{type(e).__name__}: {e}"[:250]
    return out


def choose(primary, backup, minbars=4):
    p = primary.get("bars") or []
    b = backup.get("bars") or []
    if len(p) >= minbars:
        return p, primary.get("source"), "primary"
    if len(b) >= minbars:
        return b, backup.get("source"), "backup"
    return (p or b), (primary.get("source") if p else backup.get("source")), "insufficient"


def daily_metrics(bars):
    v = [x for x in bars if all(x.get(k) is not None for k in ("close", "high", "low"))]
    closes = [x["close"] for x in v]
    highs = [x["high"] for x in v]
    lows = [x["low"] for x in v]
    out = {}
    for n in (5, 10, 20, 60):
        if len(closes) >= n:
            out[f"ma{n}"] = round(sum(closes[-n:]) / n, 6)
    if len(closes) >= 15:
        trs = []
        for i in range(1, len(closes)):
            trs.append(max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1])))
        if len(trs) >= 14:
            out["atr14"] = round(sum(trs[-14:]) / 14, 6)
    if len(v) >= 6:
        out["recent_6d_low"] = min(x["low"] for x in v[-6:])
        out["recent_6d_high"] = max(x["high"] for x in v[-6:])
    if len(v) >= 12:
        out["recent_12d_low"] = min(x["low"] for x in v[-12:])
    return out


def aggregate_from_minute(mq, bucket=5):
    rows = mq.get("rows") or []
    date = mq.get("date")
    if not date or not rows:
        return []
    items = []
    pv = pa = None
    for row in rows:
        p = row.get("price")
        if p is None:
            continue
        cv, ca = row.get("cum_volume"), row.get("cum_amount")
        dv = cv - pv if cv is not None and pv is not None and cv >= pv else None
        da = ca - pa if ca is not None and pa is not None and ca >= pa else None
        pv, pa = cv if cv is not None else pv, ca if ca is not None else pa
        items.append({"hhmm": row["hhmm"], "price": p, "volume": dv, "amount": da})
    groups = {}
    order = []
    for x in items:
        try:
            hh = int(x["hhmm"][:2]); mm = int(x["hhmm"][2:4])
        except Exception:
            continue
        mins = hh*60 + mm
        end = ((mins + bucket - 1) // bucket) * bucket
        key = f"{date[:4]}-{date[4:6]}-{date[6:8]} {end//60:02d}:{end%60:02d}"
        if key not in groups:
            groups[key] = []; order.append(key)
        groups[key].append(x)
    bars = []
    for key in order:
        g = groups[key]
        prices = [x["price"] for x in g if x.get("price") is not None]
        if not prices:
            continue
        bars.append({"time": key, "open": prices[0], "close": prices[-1],
                     "high": max(prices), "low": min(prices),
                     "volume": sum(x.get("volume") or 0 for x in g) if any(x.get("volume") is not None for x in g) else None,
                     "amount": sum(x.get("amount") or 0 for x in g) if any(x.get("amount") is not None for x in g) else None})
    return bars


def intraday_metrics(bars, price):
    v = [x for x in bars if all(x.get(k) is not None for k in ("open", "close", "high", "low"))][-8:]
    out = {"last_bars": v}
    if len(v) >= 4:
        out["lower_lows"] = all(v[i]["low"] <= v[i-1]["low"] for i in range(1, len(v)))
        out["lower_highs"] = all(v[i]["high"] <= v[i-1]["high"] for i in range(1, len(v)))
        out["higher_lows"] = all(v[i]["low"] >= v[i-1]["low"] for i in range(1, len(v)))
        out["higher_highs"] = all(v[i]["high"] >= v[i-1]["high"] for i in range(1, len(v)))
    same = [x for x in bars if str(x.get("time", "")).startswith(NOW.strftime("%Y-%m-%d"))
            and x.get("amount") not in (None, 0) and x.get("volume") not in (None, 0)]
    if same and price:
        amt = sum(x["amount"] for x in same)
        vol = sum(x["volume"] for x in same)
        if vol:
            raw = amt / vol
            candidates = [raw, raw/100.0]
            plausible = [x for x in candidates if abs(x-price)/price <= 0.20]
            if plausible:
                out["vwap"] = round(min(plausible, key=lambda x: abs(x-price)), 6)
    return out


def base_symbol(sym):
    em = east_kline(sym, 101, 100)
    tq = tencent_kline(sym, "day", 100)
    bars, source, quality = choose(em, tq, 20)
    return {"daily_source": source, "daily_quality": quality,
            "daily_metrics": daily_metrics(bars), "daily_recent": bars[-65:]}


def danger_score(q, m):
    p = q.get("price")
    if not p:
        return -999
    s = max(0, -(q.get("change_pct") or 0))
    if m.get("ma10") and p < m["ma10"]:
        s += 2
    if m.get("ma20") and p < m["ma20"]:
        s += 3
    if m.get("recent_6d_low") and p < m["recent_6d_low"]:
        s += 4
    return s


def deep_symbol(sym, price):
    mq = minute_query(sym)
    em5 = east_kline(sym, 5, 120)
    em15 = east_kline(sym, 15, 80)
    tq5 = tencent_kline(sym, "m5", 120)
    tq15 = tencent_kline(sym, "m15", 80)
    b5, s5, q5 = choose(em5, tq5)
    b15, s15, q15 = choose(em15, tq15)
    route = "standard"
    if q5 == "insufficient":
        d5 = aggregate_from_minute(mq, 5)
        if len(d5) >= 4:
            b5, s5, q5 = d5, "tencent_minute_query_derived_m5", "derived"
            route = "tencent-rebuild"
    if q15 == "insufficient":
        d15 = aggregate_from_minute(mq, 15)
        if len(d15) >= 4:
            b15, s15, q15 = d15, "tencent_minute_query_derived_m15", "derived"
            route = "tencent-rebuild"

    eq = east_quote(sym)
    sec = eq
    sec_name = "eastmoney_push2"
    er = (eq.get("record") or {})
    efresh = er.get("price") is not None and er.get("age_seconds") is not None and er["age_seconds"] <= 600
    if not efresh:
        sq = sina_quote(sym)
        sr = (sq.get("record") or {})
        sfresh = sr.get("price") is not None and sr.get("age_seconds") is not None and sr["age_seconds"] <= 600
        if sfresh:
            sec, sec_name = sq, "sina_hq"
    return {"minute_query": mq, "route": route,
            "secondary_quote": sec, "secondary_quote_source": sec_name,
            "m5": {"source": s5, "quality": q5, "metrics": intraday_metrics(b5, price)},
            "m15": {"source": s15, "quality": q15, "metrics": intraday_metrics(b15, price)}}


def main():
    tq = tencent_quotes(HOLDINGS)
    qrec = tq.get("records") or {}
    result = {"schema_version": "2.5", "generated_at_beijing": NOW.isoformat(),
              "holdings": HOLDINGS,
              "policy": "Wandy personal holdings only. Tencent batch + minute-query gate; Eastmoney/Tencent K-line fallback.",
              "tencent_quote": tq, "symbols": {}}

    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as ex:
        futs = {ex.submit(base_symbol, s): s for s in HOLDINGS}
        for fut in concurrent.futures.as_completed(futs):
            s = futs[fut]
            try:
                result["symbols"][s] = fut.result()
            except Exception as e:
                result["symbols"][s] = {"error": f"{type(e).__name__}: {e}"[:300]}

    ranked = []
    for s in HOLDINGS:
        ranked.append((danger_score(qrec.get(s, {}), (result["symbols"].get(s) or {}).get("daily_metrics") or {}), s))
    ranked.sort(reverse=True)
    candidates = [s for _, s in ranked[:5]]
    result["deep_candidates"] = candidates

    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as ex:
        futs = {ex.submit(deep_symbol, s, (qrec.get(s) or {}).get("price")): s for s in candidates}
        for fut in concurrent.futures.as_completed(futs):
            s = futs[fut]
            try:
                result["symbols"][s]["deep"] = fut.result()
            except Exception as e:
                result["symbols"][s]["deep"] = {"error": f"{type(e).__name__}: {e}"[:300]}

    fresh = 0
    per = {}
    green = 0
    green_t = 0
    for s in HOLDINGS:
        q = qrec.get(s, {})
        qfresh = q.get("price") is not None and q.get("age_seconds") is not None and q["age_seconds"] <= 600
        if qfresh:
            fresh += 1
        row = {"tencent_fresh": qfresh}
        if s in candidates:
            d = (result["symbols"].get(s) or {}).get("deep") or {}
            mq = d.get("minute_query") or {}
            mqfresh = (mq.get("date") == NOW.strftime("%Y%m%d") and mq.get("last_age_seconds") is not None and mq["last_age_seconds"] <= 600)
            sec = (d.get("secondary_quote") or {}).get("record") or {}
            sfresh = sec.get("price") is not None and sec.get("age_seconds") is not None and sec["age_seconds"] <= 600
            agree = None
            if qfresh and sfresh and q.get("price") and sec.get("price"):
                timediff = abs(dt.datetime.fromisoformat(q["quote_time"]).timestamp() - dt.datetime.fromisoformat(sec["quote_time"]).timestamp())
                pricediff = abs(q["price"] - sec["price"]) / ((q["price"] + sec["price"]) / 2)
                agree = timediff <= 300 and pricediff <= 0.005
            kready = (d.get("m5", {}).get("quality") != "insufficient" and d.get("m15", {}).get("quality") != "insufficient")
            row.update({"minute_query_fresh": mqfresh, "secondary_quote_source": d.get("secondary_quote_source"),
                        "secondary_fresh": sfresh, "dual_agree": agree,
                        "deep_kline_ready": kready, "route": d.get("route")})
            if mqfresh and kready and agree is True:
                if d.get("route") == "tencent-rebuild":
                    green_t += 1
                else:
                    green += 1
        per[s] = row

    total = len(HOLDINGS)
    if fresh <= total // 2:
        health = "RED"
    elif fresh >= total - 1 and green + green_t == len(candidates):
        health = "GREEN-T" if green_t else "GREEN"
    elif fresh >= total - 1 and all(per[s].get("minute_query_fresh") and per[s].get("deep_kline_ready") for s in candidates):
        health = "GREEN-T"
    else:
        health = "YELLOW"
    result["health"] = {"holdings": total, "fresh_quotes_le_10m": fresh,
                        "deep_candidates": len(candidates), "deep_green": green,
                        "deep_green_t": green_t, "quote_health": health,
                        "per_holding": per}

    tmp = OUT.with_suffix(".tmp")
    tmp.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(OUT)
    print(json.dumps(result["health"], ensure_ascii=False))


if __name__ == "__main__":
    main()
