"""Gate-ready peer comps table (Kilian 2026-10-14 16:20Z: "if peer multiples fail frequently figure out a way to fix that").

Why: in every full run most peer rows failed the citation gate. Agents cited aggregator pages (stockanalysis) for forward
EV/EBITDA the pages never show, or labelled their own price / EPS arithmetic "reported". This builds one table per run
before the agents start, with every figure carrying an id, a basis and its arithmetic, so agents cite ids and the gate
checks them by script instead of opening pages.

Sources:
  - trailing figures, EV and shares: SEC XBRL companyfacts via secfacts.py (filings); price: Yahoo chart feed last close
  - forward (NTM) figures: consensus snapshot data/estimates/<PEER>.json, saved from the Bigdata.com company tearsheet
    (analyst_estimates section, provider FMP; https://bigdata.com). NTM = the next four fiscal quarters after the latest
    quarter in the peer's filings; the following four give the forward growth. Basis "estimate" (consensus), never "reported".

Usage:
  python3 peers.py need PEER...               -> which consensus snapshots are missing or older than 7 days
  python3 peers.py build DATE SYM PEER...     -> runs/DATE/SYM/peers.json + peers.md (ids like FAST.ev_ebitda_ntm)
  python3 peers.py show PEER...               -> print the table only
"""
import datetime as dt, json, os, sys
import secfacts
HERE = os.path.dirname(os.path.abspath(__file__))
EST = f'{HERE}/data/estimates'
SRC = 'Bigdata.com company tearsheet, analyst_estimates (provider FMP consensus), https://bigdata.com'

def snap(sym):
    p = f'{EST}/{sym}.json'
    return json.load(open(p)) if os.path.exists(p) else None

def need(syms, max_age=7):
    out = []
    for s in syms:
        d = snap(s)
        if not d: out.append((s, 'missing')); continue
        age = (dt.datetime.utcnow() - dt.datetime.fromisoformat(d['retrieved_utc'].replace('Z', '').split('+')[0])).days
        if age > max_age: out.append((s, f'{age} days old'))
    return out

def last_quarter_end(p):
    ends = [f['end'] for f in p['facts'] if f.get('end') and f['key'].startswith('revenue') and f.get('period') in ('Q', 'YTD', 'FY')]
    return max(ends) if ends else None

def quarters(d, after):
    """Quarterly consensus records by period, keeping only periods ending more than 20 days after `after`."""
    by = {}
    cut = (dt.date.fromisoformat(after) + dt.timedelta(days=20)).isoformat() if after else '0000'
    for r in d.get('records', []):
        if r.get('report_date', '') <= cut: continue
        k = (r['report_date'], r.get('fiscal_year'), str(r.get('fiscal_period')))
        by.setdefault(k, {})[r['metric']] = r
    return [dict(period=k, m=v) for k, v in sorted(by.items())]

def row_for(sym):
    sym = sym.upper(); F = []
    def fact(key, label, value, unit, basis, derivation, source):
        F.append(dict(id=f'{sym}.{key}', key=key, label=label, value=value, unit=unit, basis=basis, derivation=derivation, source=source))
        return value
    try: p = secfacts.build(sym)
    except Exception as e: return dict(symbol=sym, error=f'filings: {e}', facts=F)
    if p.get('currency') != 'USD':
        return dict(symbol=sym, error=f"reports in {p.get('currency')}: not in this table; source it by hand", facts=F)
    g = {f['key']: f for f in p['facts']}
    try: px, pdate, _ = secfacts.price(sym)
    except Exception as e: return dict(symbol=sym, error=f'no price: {e}', facts=F)
    yahoo = f'https://query1.finance.yahoo.com/v8/finance/chart/{sym}'
    fact('price', f'last close {pdate}', px, 'USD', 'reported', f'Yahoo chart feed regularMarketPrice at {pdate}', yahoo)
    so, nd = g.get('shares_outstanding'), g.get('net_debt')
    mcap = ev = None
    if so:
        mcap = fact('mcap', 'market cap', px * so['value'], 'USD', 'derived', f"price {px} x shares {so['value']:,.0f} ({so['id']}, {so.get('end')})", so.get('url'))
    if mcap and nd:
        ev = fact('ev', 'enterprise value (net debt from filings; leases only if tagged)', mcap + nd['value'], 'USD', 'derived',
                  f"mcap {mcap/1e9:,.2f}B + net debt {nd['value']/1e9:,.2f}B ({nd['derivation']})", nd.get('api'))
    ttm = {k: g[k]['value'] for k in ('revenue_ttm', 'ebitda_ttm', 'net_income_ttm') if k in g}
    if ev and ttm.get('ebitda_ttm', 0) > 0:
        fact('ev_ebitda_ttm', 'EV / EBITDA TTM (GAAP op. income + D&A)', ev / ttm['ebitda_ttm'], 'x', 'derived',
             f"EV {ev/1e9:,.2f}B / EBITDA TTM {ttm['ebitda_ttm']/1e9:,.3f}B ({g['ebitda_ttm']['derivation']})", g['ebitda_ttm'].get('api'))
    if ev and ttm.get('revenue_ttm', 0) > 0:
        fact('ev_sales_ttm', 'EV / revenue TTM', ev / ttm['revenue_ttm'], 'x', 'derived', f"EV {ev/1e9:,.2f}B / revenue TTM {ttm['revenue_ttm']/1e9:,.3f}B", None)
    if mcap and ttm.get('net_income_ttm', 0) > 0:
        fact('pe_ttm', 'P/E TTM (GAAP)', mcap / ttm['net_income_ttm'], 'x', 'derived', f"mcap {mcap/1e9:,.2f}B / GAAP net income TTM {ttm['net_income_ttm']/1e9:,.3f}B", None)
    d = snap(sym); lq = last_quarter_end(p); note = []
    if not d: note.append('no consensus snapshot: forward columns blank')
    elif d.get('format') == 'alphavantage':
        # 2026-10-15: Bigdata.com credits ran out; Alpha Vantage EARNINGS_ESTIMATES gives only 2 forward quarters plus fiscal
        # years, so NTM is the time-weighted blend of the current and next fiscal year (no EBITDA consensus there).
        av_fy(d, lq, px, ev, fact, note)
    else:
        qs = quarters(d, lq)
        def tot(lo, hi, m):
            vals = [q['m'][m]['estimate_mean'] for q in qs[lo:hi] if m in q['m'] and q['m'][m].get('estimate_mean') is not None]
            return sum(vals) if len(vals) == hi - lo else None
        per = ', '.join(f"FY{q['period'][1]} Q{q['period'][2]} ({q['period'][0]})" for q in qs[:4])
        nan = min((q['m'].get('EPS', {}).get('num_analysts') or 0) for q in qs[:4]) if len(qs) >= 4 else 0
        snapsrc = f"{SRC}; snapshot data/estimates/{sym}.json retrieved {d['retrieved_utc']}"
        if len(qs) < 4: note.append(f'only {len(qs)} future quarters in the snapshot after {lq}')
        else:
            eps, ebitda, rev = tot(0, 4, 'EPS'), tot(0, 4, 'EBITDA'), tot(0, 4, 'SALES')
            if eps: fact('eps_ntm', f'consensus EPS, next 4 quarters ({per})', eps, 'USD', 'estimate', f'sum of quarterly consensus means: {per}', snapsrc)
            if ebitda: fact('ebitda_ntm', f'consensus EBITDA, next 4 quarters ({per})', ebitda, 'USD', 'estimate', f'sum of quarterly consensus means: {per}', snapsrc)
            if rev: fact('revenue_ntm', f'consensus revenue, next 4 quarters ({per})', rev, 'USD', 'estimate', f'sum of quarterly consensus means: {per}', snapsrc)
            if eps and eps > 0:
                fact('pe_ntm', 'P/E NTM (price / consensus EPS)', px / eps, 'x', 'estimate', f'price {px} / NTM consensus EPS {eps:.3f}', snapsrc)
            if ev and ebitda and ebitda > 0:
                fact('ev_ebitda_ntm', 'EV / EBITDA NTM (filings EV / consensus EBITDA)', ev / ebitda, 'x', 'estimate',
                     f'EV {ev/1e9:,.2f}B / NTM consensus EBITDA {ebitda/1e9:,.3f}B', snapsrc)
                if ttm.get('ebitda_ttm', 0) > 0 and not 0.6 < ebitda / ttm['ebitda_ttm'] < 1.6:
                    note.append(f"consensus EBITDA NTM is {ebitda/ttm['ebitda_ttm']:.2f}x GAAP EBITDA TTM: check the basis before using it")
            if ev and rev and rev > 0:
                fact('ev_sales_ntm', 'EV / revenue NTM', ev / rev, 'x', 'estimate', f'EV {ev/1e9:,.2f}B / NTM consensus revenue {rev/1e9:,.3f}B', snapsrc)
            eps2, rev2 = tot(4, 8, 'EPS'), tot(4, 8, 'SALES')
            if eps and eps2 and eps > 0:
                fact('eps_growth_fwd', 'consensus EPS growth, quarters 5-8 vs 1-4', eps2 / eps - 1, 'fraction', 'estimate',
                     f'{eps2:.3f} / {eps:.3f} - 1', snapsrc)
            if rev and rev2 and rev > 0:
                fact('revenue_growth_fwd', 'consensus revenue growth, quarters 5-8 vs 1-4', rev2 / rev - 1, 'fraction', 'estimate',
                     f'{rev2/1e9:,.3f}B / {rev/1e9:,.3f}B - 1', snapsrc)
            if rev and rev2 and not -0.05 < rev2 / rev - 1 < 0.30:
                note.append(f'forward revenue growth {rev2/rev-1:+.1%} looks odd (fiscal calendar or thin estimates): check before using')
            if nan and nan < 3: note.append(f'thin coverage: as few as {nan} EPS estimates in a quarter')
    return dict(symbol=sym, name=p.get('name'), price_date=pdate, last_quarter_end=lq, notes=note, facts=F)

def av_fy(d, lq, px, ev, fact, note):
    num = lambda r, k: float(r[k]) if r.get(k) not in (None, '', 'None') else None
    fys = sorted((r for r in d['records'] if r.get('horizon') == 'fiscal year' and r['date'] > (lq or '0000')), key=lambda r: r['date'])
    src = f"{d['source']}; snapshot data/estimates/{d['symbol']}.json retrieved {d['retrieved_utc']}"
    if len(fys) < 2: note.append(f'only {len(fys)} future fiscal years in the snapshot after {lq}'); return
    if not lq: note.append('no last filed quarter end in the filings tags: forward columns blank'); return  # 2026-10-20 (XEL run): CNP
    a, b = fys[0], fys[1]
    f = min(1.0, max(0.0, (dt.date.fromisoformat(a['date']) - dt.date.fromisoformat(lq)).days / 365.0))
    lab = f"{f:.3f} x FY ending {a['date']} + {1-f:.3f} x FY ending {b['date']} (weight = days from last filed quarter {lq} to FY end / 365)"
    def blend(k):
        x, y = num(a, k), num(b, k)
        return (f * x + (1 - f) * y, x, y) if x is not None and y is not None else (None, x, y)
    eps, e1, e2 = blend('eps_estimate_average'); rev, r1, r2 = blend('revenue_estimate_average')
    if eps is not None: fact('eps_ntm', 'consensus EPS, NTM (fiscal-year blend)', eps, 'USD', 'estimate', f'{lab}: {f:.3f} x {e1} + {1-f:.3f} x {e2}', src)
    if rev is not None: fact('revenue_ntm', 'consensus revenue, NTM (fiscal-year blend)', rev, 'USD', 'estimate', f'{lab}: {f:.3f} x {r1:,.0f} + {1-f:.3f} x {r2:,.0f}', src)
    if eps and eps > 0: fact('pe_ntm', 'P/E NTM (price / consensus EPS)', px / eps, 'x', 'estimate', f'price {px} / NTM consensus EPS {eps:.3f}', src)
    if ev and rev and rev > 0: fact('ev_sales_ntm', 'EV / revenue NTM', ev / rev, 'x', 'estimate', f'EV {ev/1e9:,.2f}B / NTM consensus revenue {rev/1e9:,.3f}B', src)
    if e1 and e2 and e1 > 0: fact('eps_growth_fwd', f"consensus EPS growth, FY ending {b['date']} vs FY ending {a['date']}", e2 / e1 - 1, 'fraction', 'estimate', f'{e2} / {e1} - 1', src)
    if r1 and r2 and r1 > 0: fact('revenue_growth_fwd', f"consensus revenue growth, FY ending {b['date']} vs FY ending {a['date']}", r2 / r1 - 1, 'fraction', 'estimate', f'{r2/1e9:,.3f}B / {r1/1e9:,.3f}B - 1', src)
    if r1 and r2 and not -0.05 < r2 / r1 - 1 < 0.30: note.append(f'forward revenue growth {r2/r1-1:+.1%} looks odd: check before using')
    n = num(a, 'eps_estimate_analyst_count')
    if n is not None and n < 3: note.append(f'thin coverage: {n:.0f} EPS estimates')
    note.append('consensus from Alpha Vantage (fiscal-year blend; no EBITDA consensus, so no EV/EBITDA NTM)')

def table_md(rows):
    L = ['## Peer comps table (peers.py; cite figures by id in `fact_ids`)', '',
         'Trailing columns come from SEC filings (secfacts.py) and the last close; NTM columns are price or filings EV over',
         f'consensus from a saved snapshot ({SRC}; or Alpha Vantage EARNINGS_ESTIMATES where the row notes say so). Basis for NTM figures is "estimate" (consensus), never "reported".',
         'In each comps entry put the ids you used in `fact_ids` (e.g. ["FAST.pe_ntm", "FAST.ev_ebitda_ntm", "FAST.eps_growth_fwd"]);',
         'the gate checks them by script. A peer that is not in this table, or a figure this table lacks, you source yourself:',
         'trailing from `python3 secfacts.py peers SYM`, forward as price / consensus EPS with basis "estimate" and the consensus',
         'page cited; never cite an aggregator page for a figure it does not show.', '',
         '| peer | price | EV $B | EV/EBITDA ttm | EV/EBITDA NTM | P/E ttm | P/E NTM | EV/sales NTM | EPS growth fwd | rev growth fwd | notes |',
         '|---|---|---|---|---|---|---|---|---|---|---|']
    for r in rows:
        if r.get('error'): L.append(f"| {r['symbol']} | {r['error']} |" + ' |' * 9); continue
        v = {f['key']: f['value'] for f in r['facts']}
        x = lambda k, fmt='{:.1f}x': (fmt.format(v[k]) if k in v and v[k] is not None else 'n/a')
        evb = f"{v['ev']/1e9:,.1f}" if v.get('ev') is not None else 'n/a'
        L.append(f"| {r['symbol']} | {x('price', '{:.2f}')} | {evb} | "
                 f"{x('ev_ebitda_ttm')} | {x('ev_ebitda_ntm')} | {x('pe_ttm')} | {x('pe_ntm')} | {x('ev_sales_ntm', '{:.2f}x')} | "
                 f"{x('eps_growth_fwd', '{:+.1%}') if 'eps_growth_fwd' in v else 'n/a'} | {x('revenue_growth_fwd', '{:+.1%}') if 'revenue_growth_fwd' in v else 'n/a'} | "
                 f"{'; '.join(r.get('notes') or [])} |")
    L += ['', 'Ids per peer: `.price .mcap .ev .ev_ebitda_ttm .ev_sales_ttm .pe_ttm .eps_ntm .ebitda_ntm .revenue_ntm .pe_ntm',
          '.ev_ebitda_ntm .ev_sales_ntm .eps_growth_fwd .revenue_growth_fwd` (only those shown with a value exist).', '']
    return '\n'.join(L)

if __name__ == '__main__':
    a = sys.argv[1:]
    if not a: print(__doc__); sys.exit(1)
    if a[0] == 'need':
        miss = need([s.upper() for s in a[1:]])
        print(json.dumps(miss) if miss else 'all snapshots present and fresh')
    elif a[0] == 'show':
        print(table_md([row_for(s) for s in a[1:]]))
    elif a[0] == 'build':
        date, sym, peers = a[1], a[2].upper(), [s.upper() for s in a[3:]]
        rows = [row_for(s) for s in peers]
        rd = f'{HERE}/runs/{date}/{sym}'; os.makedirs(rd, exist_ok=True)
        json.dump(dict(symbol=sym, built=dt.datetime.utcnow().strftime('%Y-%m-%dT%H:%MZ'), peers=rows), open(f'{rd}/peers.json', 'w'), indent=1)
        open(f'{rd}/peers.md', 'w').write(table_md(rows))
        print(sym, len(rows), 'peers ->', f'{rd}/peers.md'); print(table_md(rows))
    else: print(__doc__); sys.exit(1)
