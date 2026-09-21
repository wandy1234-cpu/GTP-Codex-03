#!/usr/bin/env python3
import datetime as dt
import json
from pathlib import Path

TZ = dt.timezone(dt.timedelta(hours=8))
P = Path("data/latest_market.json")
obj = json.loads(P.read_text(encoding="utf-8"))
now = dt.datetime.fromisoformat(obj["generated_at_beijing"])
health = obj.get("health") or {}
per = health.get("per_holding") or {}
candidates = obj.get("deep_candidates") or []

for sym in candidates:
    deep = ((obj.get("symbols") or {}).get(sym) or {}).get("deep") or {}
    mq = deep.get("minute_query") or {}
    date = str(mq.get("date") or "")
    hhmm = str(mq.get("last_hhmm") or "")
    if len(date) == 8 and len(hhmm) == 4 and date.isdigit() and hhmm.isdigit():
        try:
            t = dt.datetime.strptime(date + hhmm, "%Y%m%d%H%M").replace(tzinfo=TZ)
            mq["last_time"] = t.isoformat()
            mq["last_age_seconds"] = abs((now - t).total_seconds())
        except Exception:
            pass
    row = per.setdefault(sym, {})
    row["minute_query_fresh"] = (
        date == now.strftime("%Y%m%d") and
        mq.get("last_age_seconds") is not None and
        mq["last_age_seconds"] <= 600
    )

fresh = int(health.get("fresh_quotes_le_10m") or 0)
total = int(health.get("holdings") or len(obj.get("holdings") or []))
ready = 0
uses_rebuild = False
for sym in candidates:
    row = per.get(sym) or {}
    if row.get("minute_query_fresh") and row.get("deep_kline_ready") and row.get("dual_agree") is True:
        ready += 1
        if row.get("route") == "tencent-rebuild":
            uses_rebuild = True

health["deep_gate_ready"] = ready
if fresh <= total // 2:
    health["quote_health"] = "RED"
elif fresh >= total - 1 and ready == len(candidates):
    health["quote_health"] = "GREEN-T" if uses_rebuild else "GREEN"
elif fresh >= total - 1 and all((per.get(s) or {}).get("minute_query_fresh") and (per.get(s) or {}).get("deep_kline_ready") for s in candidates):
    health["quote_health"] = "GREEN-T"
else:
    health["quote_health"] = "YELLOW"

P.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps({"quote_health": health.get("quote_health"), "deep_gate_ready": ready, "fresh": fresh}, ensure_ascii=False))
