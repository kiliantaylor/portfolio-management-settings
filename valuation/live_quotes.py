"""Intraday last prices for every name on the Price Targets page (Kilian 2026-10-08: "it should also be updating last
price from the intraday yahoo pulls"). Yahoo chart feed, no key, one request per name.

python3 live_quotes.py [DATE]  -> data/live_quotes.json {as_of, quotes: {SYM: {price, prev_close, time_utc, date}}}
build_report.py shows a quote as Last when its trading date is after the run date D (so the nightly build at D's close
keeps D's close). Targets, fair values and trim/add levels stay from the close; only Last and what is measured from it move."""
import concurrent.futures as cf, datetime, glob, json, os, sys, urllib.request
HERE = os.path.dirname(os.path.abspath(__file__)); UA = {'User-Agent': 'Mozilla/5.0'}

def quote(s):
    url = f'https://query1.finance.yahoo.com/v8/finance/chart/{s}?range=1d&interval=5m'
    m = json.load(urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=20))['chart']['result'][0]['meta']
    t = m.get('regularMarketTime')
    return s, dict(price=m['regularMarketPrice'], prev_close=m.get('chartPreviousClose') or m.get('previousClose'), time_utc=t,
                   date=datetime.datetime.utcfromtimestamp(t).strftime('%Y-%m-%d') if t else None)

if __name__ == '__main__':
    D = sys.argv[1] if len(sys.argv) > 1 else sorted(os.path.basename(os.path.dirname(p)) for p in glob.glob(f'{HERE}/runs/*/review_summary.json'))[-1]
    syms = sorted(os.path.basename(os.path.dirname(p)) for p in glob.glob(f'{HERE}/runs/{D}/*/result.json'))
    out, bad = {}, []
    with cf.ThreadPoolExecutor(8) as ex:
        for f in [ex.submit(quote, s) for s in syms]:
            try: s, q = f.result(); out[s] = q
            except Exception as e: bad.append(str(e)[:80])
    json.dump(dict(as_of=datetime.datetime.utcnow().strftime('%Y-%m-%dT%H:%MZ'), run_date=D, quotes=out), open(f'{HERE}/data/live_quotes.json', 'w'), indent=1)
    print(f'{len(out)}/{len(syms)} quotes', f'{len(bad)} failed' if bad else '')
