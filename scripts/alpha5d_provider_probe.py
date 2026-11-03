#!/usr/bin/env python3
"""Bounded public-source diagnostics; no credentials, portfolio data or orders."""
import concurrent.futures as cf
import datetime as dt
import json
import io
import zipfile
import xml.etree.ElementTree as ET
import re
import subprocess
import time
import urllib.request as ur
import urllib.parse as up
from pathlib import Path

SAMPLES = {
    'east_daily': 'https://push2his.eastmoney.com/api/qt/stock/kline/get?secid=1.600000&klt=101&fqt=1&beg=20260101&end=20261102&lmt=240&fields1=f1,f2,f3,f4,f5,f6&fields2=f51,f52,f53,f54,f55,f56,f57',
    'qq_daily': 'https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param=sh600000,day,2026-01-01,2026-11-02,240,qfq',
    'qq_raw_daily': 'https://proxy.finance.qq.com/ifzqgtimg/appstock/app/newfqkline/get?param=sh600000,day,2026-01-01,2026-11-02,240',
    'sina_full_daily': 'https://finance.sina.com.cn/realstock/company/sh600000/hisdata_klc2/klc_kl.js',
    'sina_qfq': 'https://finance.sina.com.cn/realstock/company/sh600000/qfq.js',
    'sina_corpinfo': 'https://vip.stock.finance.sina.com.cn/corp/go.php/vCI_CorpInfo/stockid/601326.phtml',
    'east_halts': 'https://datacenter-web.eastmoney.com/api/data/v1/get?reportName=RPT_CUSTOM_SUSPEND_DATA&columns=ALL&pageNumber=1&pageSize=500&filter=%28MARKET%3D%22%E6%B2%AA%E6%B7%B1A%E8%82%A1%22%29%28TRADE_DATE%3D%272026-11-02%27%29',
    'east_halts_page': 'https://data.eastmoney.com/tfpxx/',
    'east_halts_interface': 'https://datacenter-web.eastmoney.com/api/data/v1/get?reportName=RPT_CUSTOM_SUSPEND_DATA_INTERFACE&columns=ALL&pageNumber=1&pageSize=500&filter=%28TRADE_DATE%3D%272026-11-02%27%29',
    'east_daily_legacy_header': 'https://push2his.eastmoney.com/api/qt/stock/kline/get?secid=1.600000&klt=101&fqt=1&beg=20250928&end=20261102&lmt=240&fields1=f1,f2,f3,f4,f5,f6&fields2=f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61',
    'east_daily_correct_header': 'https://push2his.eastmoney.com/api/qt/stock/kline/get?secid=1.600000&klt=101&fqt=1&beg=20250928&end=20261102&lmt=240&fields1=f1,f2,f3,f4,f5,f6&fields2=f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61',
    'sina_industry_https': 'https://vip.stock.finance.sina.com.cn/q/view/newSinaHy.php',
    'sina_industry_http': 'http://vip.stock.finance.sina.com.cn/q/view/newSinaHy.php',
    'sina_industry_mirror': 'https://money.finance.sina.com.cn/q/view/newSinaHy.php',
    'east_industry': 'https://push2.eastmoney.com/api/qt/clist/get?pn=1&pz=100&po=0&np=1&fltt=2&invt=2&fid=f12&fs=m:90+t:2+f:!50&fields=f12,f13,f14',
    'east_industry_82': 'https://82.push2.eastmoney.com/api/qt/clist/get?pn=1&pz=100&po=0&np=1&fltt=2&invt=2&fid=f12&fs=m:90+t:2+f:!50&fields=f12,f13,f14',
    'sse_profiles': 'https://query.sse.com.cn/sseQuery/commonQuery.do?STOCK_TYPE=1&REG_PROVINCE=&CSRC_CODE=&STOCK_CODE=&sqlId=COMMON_SSE_CP_GPJCTPZ_GPLB_GP_L&COMPANY_STATUS=2,4,5,7,8&type=inParams&isPagination=true&pageHelp.pageSize=5000',
    'szse_profiles': 'https://www.szse.cn/api/report/ShowReport?SHOWTYPE=xlsx&CATALOGID=1110&TABKEY=tab1',
}

def one(item):
    name,url=item
    start=time.monotonic()
    referer='https://quote.eastmoney.com/' if 'eastmoney.com' in url else 'https://www.sse.com.cn/' if 'sse.com.cn' in url else 'https://finance.sina.com.cn/'
    if 'szse.cn' in url:referer='https://www.szse.cn/'
    if name.endswith('legacy_header'):referer='https://finance.sina.com.cn/'
    try:
        req=ur.Request(url,headers={'User-Agent':'Mozilla/5.0','Referer':referer})
        with ur.urlopen(req,timeout=10) as response:
            body=response.read(4_000_000)
        if name=='szse_profiles':
            ns={'s':'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
            with zipfile.ZipFile(io.BytesIO(body)) as book:
                strings=[]
                if 'xl/sharedStrings.xml' in book.namelist():
                    strings=[''.join(x.itertext()) for x in ET.fromstring(book.read('xl/sharedStrings.xml')).findall('s:si',ns)]
                sheet=ET.fromstring(book.read('xl/worksheets/sheet1.xml'))
                rows=[]
                for row in sheet.findall('.//s:sheetData/s:row',ns):
                    cells=[]
                    for cell in row.findall('s:c',ns):
                        value=cell.findtext('s:v',default='',namespaces=ns)
                        if cell.get('t')=='s':value=strings[int(value)]
                        elif cell.get('t')=='inlineStr':value=''.join(cell.itertext())
                        cells.append([cell.get('r'),value])
                    rows.append(cells)
            entry={'name':name,'url':url,'ok':True,'bytes':len(body),'rows':len(rows),'first_rows':rows[:4],'last_rows':rows[-2:]}
            print('PROBE '+json.dumps(entry,ensure_ascii=False),flush=True);return entry
        try:text=body.decode('utf-8-sig')
        except UnicodeDecodeError:text=body.decode('gb18030')
        entry={'name':name,'url':url,'ok':True,'bytes':len(body),'seconds':round(time.monotonic()-start,2),'prefix':text[:350]}
        if name=='sina_full_daily':
            found=re.search(r'var\s+\w*sh600000\s*=\s*"([A-Za-z0-9+/=]+)"',text)
            if not found:raise ValueError('Compressed symbol identity not verified')
            decoded=json.loads(subprocess.run(['node','scripts/vendor/sina_klc_decode.cjs'],input=json.dumps({'encoded':found.group(1),'asof':'2026-11-02','limit':240}),text=True,capture_output=True,check=True,timeout=10).stdout)
            entry['decoded']={'rows':len(decoded),'first':decoded[0],'last':decoded[-1]}
        if name=='sina_corpinfo':
            clean=re.sub(r'<[^>]+>',' ',text)
            entry['listing_excerpt']=clean[max(0,clean.find('上市日期')-80):clean.find('上市日期')+220]
        if name=='east_halts_page':
            links=[up.urljoin(url,s) for s in re.findall(r'<script[^>]+src=["\x27]([^"\x27]+)',text)]
            links=[s for s in links if up.urlparse(s).hostname and up.urlparse(s).hostname.endswith('eastmoney.com')]
            entry['public_scripts']=links
            entry['report_names']=[]
            for link in links[-6:]:
                try:
                    req=ur.Request(link,headers={'User-Agent':'Mozilla/5.0','Referer':url})
                    with ur.urlopen(req,timeout=8) as r:script=r.read(3_000_000).decode('utf-8',errors='replace')
                    names=sorted(set(re.findall(r'RPT_[A-Z0-9_]+',script)))
                    if names:
                        entry['report_names'].append({'url':link,'names':names})
                        if 'tfpxx/index.js' in link:
                            i=script.find('RPT_CUSTOM_SUSPEND_DATA_INTERFACE')
                            entry['report_configuration']=script[max(0,i-900):i+1400]
                except Exception as e:entry.setdefault('script_errors',[]).append(type(e).__name__)
        if name.startswith('qq_'):
            data=json.loads(text).get('data',{}).get('sh600000',{})
            entry['keys']=list(data)
            for key in ['day','qfqday']:
                if data.get(key):entry[key]={'rows':len(data[key]),'first':data[key][0],'last':data[key][-1]}
        elif (name.startswith('east_') and name!='east_halts_page') or name=='sse_profiles':
            data=json.loads(text)
            entry['data_keys']=list((data.get('data') or data).keys())
            if name in ('east_halts','east_halts_interface'):entry['result_sample']=(data.get('result') or {}).get('data',[])[:5]
            if name=='sse_profiles':entry['sample']=data.get('result',[None])[0]
        print('PROBE '+json.dumps(entry,ensure_ascii=False),flush=True)
        return entry
    except Exception as error:
        entry={'name':name,'url':url,'ok':False,'seconds':round(time.monotonic()-start,2),'error':type(error).__name__+': '+str(error)}
        print('PROBE '+json.dumps(entry,ensure_ascii=False),flush=True)
        return entry

def main():
    active={k:v for k,v in SAMPLES.items() if k in ('east_daily','sina_full_daily','sina_qfq','sina_corpinfo','sina_industry_https','east_industry','east_industry_82')}
    with cf.ThreadPoolExecutor(max_workers=4) as pool:results=list(pool.map(one,active.items()))
    try:
        import alpha5d_classification as classification
        boards,audit=classification.page_list('m:90 t:2 f:!50')
        names=[b['f14'] for b in boards]
        print('TAXONOMY '+json.dumps({'count':len(boards),'missing':sorted(classification.FIRST_LEVEL-set(names)),
              'matching':[b for b in boards if b['f14'] in classification.FIRST_LEVEL],
              'names':names,'audit':audit},ensure_ascii=False),flush=True)
        selected=classification.select_first_level(boards)
        members,url=classification.industries(8)
        import shutil
        snapshot=classification.LAST_INDUSTRY_AUDIT.get('snapshot_path')
        if snapshot:shutil.copy2(snapshot,'provider-probe-industry.json')
        print('INDUSTRY_FULL '+json.dumps({'count':len(members),'url':url,
              'sample':{s:members.get(s) for s in ['sh600025','sh600901','sz000001','sz301999']}},ensure_ascii=False),flush=True)
    except Exception as error:print('INDUSTRY_ERROR '+type(error).__name__+': '+str(error),flush=True)
    try:
        import alpha5d_pipeline as p
        import alpha5d_provider_resilience as resilient
        status=json.loads(Path('data/alpha5d/pipeline_status.json').read_text())
        if status.get('status')=='COMPLETE':
            review=json.loads(Path(status['review_path']).read_text())
            quotes=p.tencent([r['symbol'] for r in review['top5']]);checks=[]
            for row in review['top5']:
                symbol=row['symbol'];daily=resilient.sina_history(symbol,review['previous_session'],0)
                bar=daily['bars'][-1];quote=quotes[symbol]
                if bar['date']!=review['previous_session']:raise ValueError('Candidate independent daily date mismatch')
                if abs(bar['close']/row['reference_raw_close']-1)>.005:raise ValueError('Candidate reference close mismatch '+symbol)
                quote_date=quote['stamp'][:8]
                if quote_date==review['previous_session'].replace('-',''):cross=quote['price']
                elif quote_date==review['forecast_date'].replace('-',''):cross=quote['previous']
                else:raise ValueError('Candidate Tencent date mismatch')
                if quote['code']!=symbol[2:] or abs(cross/row['reference_raw_close']-1)>.005:raise ValueError('Candidate Tencent identity/close mismatch')
                checks.append({'symbol':symbol,'name':row['name'],'reference_raw_close':row['reference_raw_close'],
                    'independent_daily_close':bar['close'],'independent_daily_bar':bar,'daily_source_url':daily['source_url'],
                    'quote':quote,'passed':True})
            p.atomic('provider-probe-publication.json',{'schema':'alpha5d-publication-references-v1','observed_at':p.now().isoformat(),
                'ranking_path':status['ranking_path'],'input_sha256':review['input_sha256'],'previous_session':review['previous_session'],
                'forecast_date':review['forecast_date'],'checks':checks,'not_execution_fill':True})
            print('CANDIDATE_CHECKS '+json.dumps({'passed':len(checks),'quotes':[{'symbol':x['symbol'],'stamp':x['quote']['stamp'],'price':x['quote']['price']} for x in checks]},ensure_ascii=False),flush=True)
    except Exception as error:print('CANDIDATE_CHECK_ERROR '+type(error).__name__+': '+str(error),flush=True)
    Path('provider-probe.json').write_text(json.dumps({'generated_at':dt.datetime.now(dt.timezone.utc).isoformat(),'results':results},ensure_ascii=False,indent=2))

if __name__=='__main__':main()
