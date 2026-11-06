"""Save consensus snapshots to data/estimates/SYM.json in the alphavantage record layout (peers.py av_fy reads them).

EPS: Nasdaq.com analyst earnings forecast (Zacks consensus EPS), api.nasdaq.com, fiscal years + quarters.
Revenue (since 2026-11-04): Zacks consensus sales from the same provider, www.zacks.com detailed-earning-estimates
  (current/next quarter and current/next fiscal year), matched to the EPS records by fiscal period end. Same provider as
  the EPS, so one basis per row. Gives peers.py an EV/NTM sales column for loss-makers (Kilian 2026-11-04: peer multiples
  kept failing because agents hand-sourced peer revenue from web pages).
No free EBITDA consensus feed: EV/EBITDA NTM stays blank for these rows.

  python3 nasdaq_est.py SYM ...
"""
import json, re, sys, time, calendar, datetime as dt, urllib.request
OUT = '/mnt/project-files/valuation/data/estimates'
MON = {m: i for i, m in enumerate(calendar.month_abbr) if m}
UA = {'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/124 Safari/537.36', 'Accept': '*/*'}
MULT = {'K': 1e3, 'M': 1e6, 'B': 1e9, 'T': 1e12}

def mend(y, mo): return dt.date(y, mo, calendar.monthrange(y, mo)[1]).isoformat()
def end(s):
    m, y = s.split(); return mend(int(y), MON[m])
def get(u): return urllib.request.urlopen(urllib.request.Request(u, headers=UA), timeout=30).read().decode('utf-8', 'replace')

def money(s):
    s = s.replace(',', '').replace('$', '').strip()
    m = re.fullmatch(r'(-?[\d.]+)\s*([KMBT]?)', s)
    return float(m.group(1)) * MULT.get(m.group(2), 1) if m else None

def zacks_sales(sym):
    """Zacks consensus sales table -> {period_end: dict(mean, n, high, low, label)}; {} if the page has none."""
    u = f'https://www.zacks.com/stock/quote/{sym}/detailed-earning-estimates'
    t = get(u); i = t.find('<h1>Sales Estimates</h1>')
    if i < 0: return u, {}
    tbl = t[i:t.find('</table>', i)]
    heads = re.findall(r'<th[^>]*>\s*([^<]*?)\s*<br\s*/?>\s*\((\d{1,2})/(\d{4})\)', tbl)
    rows = {}
    for tr in re.findall(r'<tr>(.*?)</tr>', tbl, re.S):
        cells = [re.sub(r'<[^>]+>', '', c).strip() for c in re.findall(r'<td[^>]*>(.*?)</td>', tr, re.S)]
        if cells: rows[cells[0]] = cells[1:]
    out = {}
    for j, (lab, mo, y) in enumerate(heads):
        g = lambda k: (rows.get(k) or [None] * len(heads))[j]
        mean = money(g('Zacks Consensus Estimate') or '')
        if mean is None: continue
        n = g('# of Estimates')
        out[mend(int(y), int(mo))] = dict(mean=mean, n=int(n) if n and n.isdigit() else None, high=money(g('High Estimate') or ''),
                                          low=money(g('Low Estimate') or ''), label=f'{lab} ({mo}/{y})')
    return u, out

def save(sym):
    u = f'https://api.nasdaq.com/api/analyst/{sym}/earnings-forecast'
    d = json.loads(get(u))['data']
    recs = []
    for key, hz in (('yearlyForecast', 'fiscal year'), ('quarterlyForecast', 'fiscal quarter')):
        for r in (d.get(key) or {}).get('rows') or []:
            recs.append(dict(date=end(r['fiscalEnd']), horizon=hz, eps_estimate_average=str(r['consensusEPSForecast']),
                             eps_estimate_high=str(r['highEPSForecast']), eps_estimate_low=str(r['lowEPSForecast']),
                             eps_estimate_analyst_count=str(r['noOfEstimates']), eps_estimate_revision_up_trailing_30_days=str(r['up']),
                             eps_estimate_revision_down_trailing_30_days=str(r['down']), revenue_estimate_average=None, raw_row=r))
    try:
        time.sleep(1); zu, zs = zacks_sales(sym)
    except Exception as e:
        zu, zs = None, {}; print(sym, 'Zacks sales fetch failed:', str(e)[:120], file=sys.stderr)
    matched = []
    for r in recs:
        z = zs.get(r['date'])
        if z:
            r.update(revenue_estimate_average=str(z['mean']), revenue_estimate_high=str(z['high']), revenue_estimate_low=str(z['low']),
                     revenue_estimate_analyst_count=str(z['n']), revenue_source=f"Zacks consensus sales, {z['label']}, {zu}")
            matched.append(r['date'])
    for e in sorted(set(zs) - set(matched)):  # a period Zacks has sales for but no EPS row (SYNA FY Jun-28): sales-only record
        z = zs[e]
        recs.append(dict(date=e, horizon='fiscal year' if 'Year' in z['label'] else 'fiscal quarter', eps_estimate_average=None,
                         revenue_estimate_average=str(z['mean']), revenue_estimate_high=str(z['high']), revenue_estimate_low=str(z['low']),
                         revenue_estimate_analyst_count=str(z['n']), revenue_source=f"Zacks consensus sales, {z['label']}, {zu}", sales_only=True))
    unmatched = [r['date'] for r in recs if r.get('sales_only')]
    src = f'Nasdaq.com analyst earnings forecast (Zacks consensus EPS), {u}'
    if matched: src += f'; Zacks consensus sales (revenue), {zu}'
    snap = dict(symbol=sym, format='alphavantage',
                format_note='record layout only; data is Zacks consensus EPS (Nasdaq.com) and Zacks consensus sales (zacks.com, 3 significant figures); no EBITDA',
                retrieved_utc=dt.datetime.now(dt.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'), source=src, records=recs,
                sales_only_periods=unmatched)
    json.dump(snap, open(f'{OUT}/{sym}.json', 'w'), indent=1)
    fy = [(r['date'], r['eps_estimate_average'], r['revenue_estimate_average'] and f"{float(r['revenue_estimate_average'])/1e9:.3f}B")
          for r in sorted(recs, key=lambda r: r['date']) if r['horizon'] == 'fiscal year']
    print(sym, fy)

if __name__ == '__main__':
    bad = []
    for sym in sys.argv[1:]:
        try: save(sym.upper())
        except Exception as e: bad.append(sym); print(sym, 'FAILED:', str(e)[:150], file=sys.stderr)
        time.sleep(1)
    if bad: print('failed:', ' '.join(bad)); sys.exit(1)
