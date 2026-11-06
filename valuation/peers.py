"""Gate-ready peer comps table (Kilian 2026-10-14 16:20Z: "if peer multiples fail frequently figure out a way to fix that").

Why: in every full run most peer rows failed the citation gate. Agents cited aggregator pages (stockanalysis) for forward
EV/EBITDA the pages never show, or labelled their own price / EPS arithmetic "reported". This builds one table per run
before the agents start, with every figure carrying an id, a basis and its arithmetic, so agents cite ids and the gate
checks them by script instead of opening pages.

Sources:
  - trailing figures, EV and shares: SEC XBRL companyfacts via secfacts.py (filings); price: Yahoo chart feed last close
  - forward (NTM) figures: consensus snapshot data/estimates/<PEER>.json. Since Bigdata.com credits ran out (2026-10-15):
    nasdaq_est.py saves Zacks fiscal-year EPS (Nasdaq.com) and Zacks sales (zacks.com, since 2026-11-04) in one call,
    so EV/NTM sales and revenue growth exist for loss-makers. Older snapshots: Bigdata.com tearsheet (provider FMP).
    NTM = the 12 months from the PRICE DATE (calendar NTM, the window the price trades on): each fiscal period's consensus
    weighted by its overlap with [price date, price date + 365 days] / its length. The next 12 months after that give the
    forward growth. Basis "estimate" (consensus), never "reported".
    Fix 2026-11-02 (MRVL round 2): NTM was anchored at the last FILED quarter end, so it lagged the price by 1-5 months
    (AVGO still weighted its FY26, which had already ended) and overstated P/E NTM for growers.

Usage:
  python3 peers.py need PEER...               -> which consensus snapshots are missing, older than 7 days or lack sales
                                                 (refresh them with: python3 nasdaq_est.py PEER...)
  python3 peers.py build DATE SYM PEER...     -> runs/DATE/SYM/peers.json + peers.md (ids like FAST.ev_ebitda_ntm)
  python3 peers.py show PEER...               -> print the table only
"""
import datetime as dt, json, os, sys
import secfacts
HERE = os.path.dirname(os.path.abspath(__file__))
EST = f'{HERE}/data/estimates'
PE_MAX = 150  # P/E NTM above this means near-zero EPS: not shown
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
        elif d.get('format') == 'alphavantage' and not any(r.get('revenue_estimate_average') not in (None, '', 'None') for r in d.get('records', [])):
            out.append((s, 'no sales consensus'))
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

def _d(x): return dt.date.fromisoformat(x[:10])

def window_sum(periods, start, days=365):
    """periods: [(start_date, end_date, value)] in order. Sum of value x overlap with [start, start+days) / period length.
    Returns (total, coverage in days, [(label weight)]) ; None total if any overlapping period lacks a value."""
    lo, hi = start, start + dt.timedelta(days=days)
    tot, cov, parts = 0.0, 0, []
    for a, b, v, lab in periods:
        ov = (min(b, hi) - max(a, lo)).days
        if ov <= 0: continue
        if v is None: return None, cov, parts
        w = ov / max(1, (b - a).days)
        tot += w * v; cov += ov; parts.append((lab, w, v))
    return tot, cov, parts

def fmt_parts(parts, money=False):
    return ' + '.join(f"{w:.3f} x {lab} {(f'{v/1e9:,.3f}B' if money else f'{v:.3f}')}" for lab, w, v in parts)

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
    if secfacts.PRICE_SOURCE.get(sym) == 'twelvedata':
        fact('price', f'last close {pdate}', px, 'USD', 'reported', f'Twelve Data daily close {pdate} (Yahoo unavailable)', f'https://twelvedata.com/markets/stocks/{sym}')
    else:
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
        # years (no EBITDA consensus there). Since 2026-11-02 NTM is calendar NTM from the price date.
        av_fy(d, lq, px, ev, fact, note, pdate)
    else:
        qtr_ntm(d, lq, pdate, px, ev, ttm, fact, note)
    return dict(symbol=sym, name=p.get('name'), price_date=pdate, last_quarter_end=lq, notes=note, facts=F)

def qtr_ntm(d, lq, pdate, px, ev, ttm, fact, note):
    """Bigdata quarterly snapshot: calendar NTM from the price date over quarterly consensus (report_date = period end)."""
    by = {}
    for r in d.get('records', []):
        k = (r['report_date'], r.get('fiscal_year'), str(r.get('fiscal_period')))
        by.setdefault(k, {})[r['metric']] = r
    ks = sorted(by)
    snapsrc = f"{SRC}; snapshot data/estimates/{d.get('symbol')}.json retrieved {d['retrieved_utc']}"
    t0 = _d(pdate)
    def per(m):
        out, prev = [], None
        for k in ks:
            e = _d(k[0]); a = prev or (e - dt.timedelta(days=91)); prev = e
            r = by[k].get(m); v = r.get('estimate_mean') if r else None
            out.append((a, e, v, f"FY{k[1]} Q{k[2]} (to {k[0]})"))
        return out
    def ntm(m, off=0):
        t, cov, parts = window_sum(per(m), t0 + dt.timedelta(days=off))
        return (t, parts) if t is not None and cov >= 360 else (None, parts)
    eps, pe_ = ntm('EPS'); ebitda, pb = ntm('EBITDA'); rev, pr = ntm('SALES')
    lab = f'calendar NTM from the price date {pdate}'
    if eps is None and ebitda is None and rev is None:
        note.append(f'snapshot does not cover the 12 months from {pdate}: forward columns blank'); return
    if eps: fact('eps_ntm', f'consensus EPS, {lab}', eps, 'USD', 'estimate', f'{fmt_parts(pe_)} (quarter weight = overlap with the window / quarter length)', snapsrc)
    if ebitda: fact('ebitda_ntm', f'consensus EBITDA, {lab}', ebitda, 'USD', 'estimate', fmt_parts(pb, True), snapsrc)
    if rev: fact('revenue_ntm', f'consensus revenue, {lab}', rev, 'USD', 'estimate', fmt_parts(pr, True), snapsrc)
    if eps and eps > 0: fact('pe_ntm', 'P/E NTM (price / calendar-NTM consensus EPS)', px / eps, 'x', 'estimate', f'price {px} / NTM consensus EPS {eps:.3f}', snapsrc)
    if ev and ebitda and ebitda > 0:
        fact('ev_ebitda_ntm', 'EV / EBITDA NTM (filings EV / calendar-NTM consensus EBITDA)', ev / ebitda, 'x', 'estimate', f'EV {ev/1e9:,.2f}B / NTM consensus EBITDA {ebitda/1e9:,.3f}B', snapsrc)
        if ttm.get('ebitda_ttm', 0) > 0 and not 0.6 < ebitda / ttm['ebitda_ttm'] < 1.6:
            note.append(f"consensus EBITDA NTM is {ebitda/ttm['ebitda_ttm']:.2f}x GAAP EBITDA TTM: check the basis before using it")
    if ev and rev and rev > 0: fact('ev_sales_ntm', 'EV / revenue NTM', ev / rev, 'x', 'estimate', f'EV {ev/1e9:,.2f}B / NTM consensus revenue {rev/1e9:,.3f}B', snapsrc)
    eps2, _ = ntm('EPS', 365); rev2, _ = ntm('SALES', 365)
    if eps and eps2 and eps > 0:
        fact('eps_growth_fwd', 'consensus EPS growth, months 13-24 vs 1-12 from the price date', eps2 / eps - 1, 'fraction', 'estimate', f'{eps2:.3f} / {eps:.3f} - 1', snapsrc)
    if rev and rev2 and rev > 0:
        fact('revenue_growth_fwd', 'consensus revenue growth, months 13-24 vs 1-12 from the price date', rev2 / rev - 1, 'fraction', 'estimate', f'{rev2/1e9:,.3f}B / {rev/1e9:,.3f}B - 1', snapsrc)
        if not -0.05 < rev2 / rev - 1 < 0.30: note.append(f'forward revenue growth {rev2/rev-1:+.1%} looks odd (fiscal calendar or thin estimates): check before using')
    ks4 = [k for k in ks if _d(k[0]) > t0][:4]
    nan = min(((by[k].get('EPS') or {}).get('num_analysts') or 0) for k in ks4) if ks4 else 0
    if nan and nan < 3: note.append(f'thin coverage: as few as {nan} EPS estimates in a quarter')

def av_fy(d, lq, px, ev, fact, note, pdate=None):
    """Nasdaq/Zacks (or Alpha Vantage) fiscal-year consensus: calendar NTM from the price date, each fiscal year weighted by
    its overlap with [price date, +365 days] / its length. A fiscal year that ended before the price date gets no weight
    (before 2026-11-02 the window started at the last filed quarter, so an ended, unreported year still counted)."""
    num = lambda r, k: float(r[k]) if r.get(k) not in (None, '', 'None') else None
    fys = sorted((r for r in d['records'] if r.get('horizon') == 'fiscal year'), key=lambda r: r['date'])
    src = f"{d['source']}; snapshot data/estimates/{d['symbol']}.json retrieved {d['retrieved_utc']}"
    if not pdate: note.append('no price date: forward columns blank'); return
    t0 = _d(pdate)
    def per(k):
        out, prev = [], None
        for r in fys:
            e = _d(r['date']); a = prev or (e - dt.timedelta(days=365)); prev = e
            out.append((a, e, num(r, k), f"FY ending {r['date']}"))
        return out
    def ntm(k, off=0):
        t, cov, parts = window_sum(per(k), t0 + dt.timedelta(days=off))
        return (t, parts) if t is not None and cov >= 360 else (None, parts)
    eps, pe_ = ntm('eps_estimate_average'); rev, pr = ntm('revenue_estimate_average')
    if eps is None and rev is None:
        note.append(f'snapshot fiscal years do not cover the 12 months from {pdate}: forward columns blank'); return
    lab = f'weight = overlap of each fiscal year with {pdate} to +365 days / fiscal-year length'
    if eps is not None: fact('eps_ntm', f'consensus EPS, calendar NTM from the price date {pdate}', eps, 'USD', 'estimate', f'{fmt_parts(pe_)} ({lab})', src)
    if rev is not None: fact('revenue_ntm', f'consensus revenue, calendar NTM from the price date {pdate}', rev, 'USD', 'estimate', f'{fmt_parts(pr, True)} ({lab})', src)
    if eps and eps > 0 and px / eps > PE_MAX:  # near-zero EPS (CEVA 1944x on 11-03): not a usable multiple
        note.append(f'NTM EPS {eps:.3f} is near zero (P/E {px/eps:,.0f}x): P/E NTM and EPS growth left out, use EV/sales'); eps = None
    if eps and eps > 0: fact('pe_ntm', 'P/E NTM (price / calendar-NTM consensus EPS)', px / eps, 'x', 'estimate', f'price {px} / NTM consensus EPS {eps:.3f}', src)
    if ev and rev and rev > 0: fact('ev_sales_ntm', 'EV / revenue NTM', ev / rev, 'x', 'estimate', f'EV {ev/1e9:,.2f}B / NTM consensus revenue {rev/1e9:,.3f}B', src)
    eps2, _ = ntm('eps_estimate_average', 365); rev2, _ = ntm('revenue_estimate_average', 365)
    if eps and eps2 and eps > 0: fact('eps_growth_fwd', 'consensus EPS growth, months 13-24 vs 1-12 from the price date', eps2 / eps - 1, 'fraction', 'estimate', f'{eps2:.3f} / {eps:.3f} - 1', src)
    elif eps:  # snapshot too short for months 13-24: next two fiscal years ending after the price date
        nx = [r for r in fys if _d(r['date']) > t0][:2]
        if len(nx) == 2:
            e1, e2 = num(nx[0], 'eps_estimate_average'), num(nx[1], 'eps_estimate_average')
            if e1 and e2 and e1 > 0: fact('eps_growth_fwd', f"consensus EPS growth, FY ending {nx[1]['date']} vs FY ending {nx[0]['date']}", e2 / e1 - 1, 'fraction', 'estimate', f'{e2} / {e1} - 1', src)
    if rev and rev2 and rev > 0:
        fact('revenue_growth_fwd', 'consensus revenue growth, months 13-24 vs 1-12 from the price date', rev2 / rev - 1, 'fraction', 'estimate', f'{rev2/1e9:,.3f}B / {rev/1e9:,.3f}B - 1', src)
        if not -0.05 < rev2 / rev - 1 < 0.30: note.append(f'forward revenue growth {rev2/rev-1:+.1%} looks odd: check before using')
    elif rev:  # Zacks sales cover two fiscal years only: next fiscal year vs the one after the price date
        nx = [r for r in fys if _d(r['date']) > t0][:2]
        if len(nx) == 2:
            r1, r2 = num(nx[0], 'revenue_estimate_average'), num(nx[1], 'revenue_estimate_average')
            if r1 and r2 and r1 > 0:
                fact('revenue_growth_fwd', f"consensus revenue growth, FY ending {nx[1]['date']} vs FY ending {nx[0]['date']}", r2 / r1 - 1, 'fraction', 'estimate', f'{r2/1e9:,.3f}B / {r1/1e9:,.3f}B - 1', src)
                if not -0.05 < r2 / r1 - 1 < 0.30: note.append(f'forward revenue growth {r2/r1-1:+.1%} looks odd: check before using')
    a = next((r for r in fys if _d(r['date']) > t0), None)
    n = num(a, 'eps_estimate_analyst_count') if a else None
    if n is not None and n < 3: note.append(f'thin coverage: {n:.0f} EPS estimates')
    used = [r for r in fys if t0 < _d(r['date']) and (_d(r['date']) - t0).days < 2 * 365 + 366]
    thin = [f"FY ending {r['date']} ({num(r, 'eps_estimate_analyst_count'):.0f})" for r in used[1:] if (num(r, 'eps_estimate_analyst_count') or 99) < 3]
    if thin: note.append('EPS growth fwd leans on thin out-year estimates: ' + ', '.join(thin))
    has_rev = any(num(r, 'revenue_estimate_average') for r in fys)
    note.append('consensus from Zacks fiscal years (EPS via Nasdaq.com' + ('; sales via zacks.com' if has_rev else '; no sales consensus in the snapshot, rerun nasdaq_est.py') +
                '; calendar NTM from the price date; no EBITDA consensus, so no EV/EBITDA NTM)')

def table_md(rows):
    L = ['## Peer comps table (peers.py; cite figures by id in `fact_ids`)', '',
         'Trailing columns come from SEC filings (secfacts.py) and the last close; NTM columns are price or filings EV over',
         f'consensus from a saved snapshot (Zacks EPS via Nasdaq.com and Zacks sales via zacks.com, nasdaq_est.py; older rows: {SRC}). Basis for NTM figures is "estimate" (consensus), never "reported".',
         'In each comps entry put the ids you used in `fact_ids` (e.g. ["FAST.pe_ntm", "FAST.ev_sales_ntm", "FAST.revenue_growth_fwd"])',
         'and copy the table value (rounding fine) into the matching field (fwd_pe, ev_ebitda_fwd, ev_sales_ntm, growth_next_yr); a figure the',
         'table lacks is null, never 0.0. The gate checks every field against its cited id by script and sends only the',
         'fields without a table id to a page checker. A peer that is not in this table, or a figure this table lacks, you source yourself:',
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
