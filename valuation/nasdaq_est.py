"""Save Nasdaq.com (Zacks consensus EPS) earnings-forecast snapshots to data/estimates/SYM.json in the alphavantage record layout."""
import json, sys, time, calendar, datetime as dt, urllib.request
OUT = '/mnt/project-files/valuation/data/estimates'
MON = {m: i for i, m in enumerate(calendar.month_abbr) if m}
def end(s):
    m, y = s.split(); y = int(y); mo = MON[m]
    return dt.date(y, mo, calendar.monthrange(y, mo)[1]).isoformat()
for sym in sys.argv[1:]:
    u = f'https://api.nasdaq.com/api/analyst/{sym}/earnings-forecast'
    d = json.load(urllib.request.urlopen(urllib.request.Request(u, headers={'User-Agent': 'Mozilla/5.0', 'Accept': 'application/json'}), timeout=30))['data']
    recs = []
    for key, hz in (('yearlyForecast', 'fiscal year'), ('quarterlyForecast', 'fiscal quarter')):
        for r in (d.get(key) or {}).get('rows') or []:
            recs.append(dict(date=end(r['fiscalEnd']), horizon=hz, eps_estimate_average=str(r['consensusEPSForecast']),
                             eps_estimate_high=str(r['highEPSForecast']), eps_estimate_low=str(r['lowEPSForecast']),
                             eps_estimate_analyst_count=str(r['noOfEstimates']), eps_estimate_revision_up_trailing_30_days=str(r['up']),
                             eps_estimate_revision_down_trailing_30_days=str(r['down']), revenue_estimate_average=None, raw_row=r))
    snap = dict(symbol=sym, format='alphavantage', format_note='record layout only; data is Nasdaq.com/Zacks consensus EPS (no revenue)',
                retrieved_utc=dt.datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ'),
                source=f'Nasdaq.com analyst earnings forecast (Zacks consensus EPS), {u}', records=recs)
    json.dump(snap, open(f'{OUT}/{sym}.json', 'w'), indent=1)
    fy = [(r['date'], r['eps_estimate_average'], r['eps_estimate_analyst_count']) for r in recs if r['horizon'] == 'fiscal year']
    print(sym, fy); time.sleep(1)
