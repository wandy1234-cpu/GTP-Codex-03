#!/usr/bin/env python3
"""Bounded public-source diagnostics; no credentials, portfolio data or orders."""
import concurrent.futures as cf
import datetime as dt
import json
import time
import urllib.request as ur
from pathlib import Path

SAMPLES = {
    'east_daily': 'https://push2his.eastmoney.com/api/qt/stock/kline/get?secid=1.600000&klt=101&fqt=1&beg=20260101&end=20261102&lmt=240&fields1=f1,f2,f3,f4,f5,f6&fields2=f51,f52,f53,f54,f55,f56,f57',
    'qq_daily': 'https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param=sh600000,day,2026-01-01,2026-11-02,240,qfq',
    'qq_raw_daily': 'https://proxy.finance.qq.com/ifzqgtimg/appstock/app/newfqkline/get?param=sh600000,day,2026-01-01,2026-11-02,240',
    'sina_full_daily': 'https://finance.sina.com.cn/realstock/company/sh600000/hisdata/klc_kl.js',
    'sina_qfq': 'https://finance.sina.com.cn/realstock/company/sh600000/qfq.js',
    'sina_industry_https': 'https://vip.stock.finance.sina.com.cn/q/view/newSinaHy.php',
    'sina_industry_http': 'http://vip.stock.finance.sina.com.cn/q/view/newSinaHy.php',
    'sina_industry_mirror': 'https://money.finance.sina.com.cn/q/view/newSinaHy.php',
    'east_industry': 'https://push2.eastmoney.com/api/qt/clist/get?pn=1&pz=100&po=0&np=1&fltt=2&invt=2&fid=f12&fs=m:90+t:2+f:!50&fields=f12,f13,f14',
    'east_industry_82': 'https://82.push2.eastmoney.com/api/qt/clist/get?pn=1&pz=100&po=0&np=1&fltt=2&invt=2&fid=f12&fs=m:90+t:2+f:!50&fields=f12,f13,f14',
    'sse_profiles': 'https://query.sse.com.cn/sseQuery/commonQuery.do?STOCK_TYPE=1&REG_PROVINCE=&CSRC_CODE=&STOCK_CODE=&sqlId=COMMON_SSE_CP_GPJCTPZ_GPLB_GP_L&COMPANY_STATUS=2,4,5,7,8&type=inParams&isPagination=true&pageHelp.pageSize=5000',
}

def one(item):
    name,url=item
    start=time.monotonic()
    referer='https://quote.eastmoney.com/' if 'eastmoney.com' in url else 'https://www.sse.com.cn/' if 'sse.com.cn' in url else 'https://finance.sina.com.cn/'
    try:
        req=ur.Request(url,headers={'User-Agent':'Mozilla/5.0','Referer':referer})
        with ur.urlopen(req,timeout=10) as response:
            body=response.read(4_000_000)
        try:text=body.decode('utf-8-sig')
        except UnicodeDecodeError:text=body.decode('gb18030')
        entry={'name':name,'url':url,'ok':True,'bytes':len(body),'seconds':round(time.monotonic()-start,2),'prefix':text[:350]}
        if name.startswith('qq_'):
            data=json.loads(text).get('data',{}).get('sh600000',{})
            entry['keys']=list(data)
            for key in ['day','qfqday']:
                if data.get(key):entry[key]={'rows':len(data[key]),'first':data[key][0],'last':data[key][-1]}
        elif name.startswith('east_') or name=='sse_profiles':
            data=json.loads(text)
            entry['data_keys']=list((data.get('data') or data).keys())
            if name=='sse_profiles':entry['sample']=data.get('result',[None])[0]
        print('PROBE '+json.dumps(entry,ensure_ascii=False),flush=True)
        return entry
    except Exception as error:
        entry={'name':name,'url':url,'ok':False,'seconds':round(time.monotonic()-start,2),'error':type(error).__name__+': '+str(error)}
        print('PROBE '+json.dumps(entry,ensure_ascii=False),flush=True)
        return entry

def main():
    with cf.ThreadPoolExecutor(max_workers=4) as pool:results=list(pool.map(one,SAMPLES.items()))
    Path('provider-probe.json').write_text(json.dumps({'generated_at':dt.datetime.now(dt.timezone.utc).isoformat(),'results':results},ensure_ascii=False,indent=2))

if __name__=='__main__':main()
