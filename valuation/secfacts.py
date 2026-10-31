"""Reported figures straight from SEC XBRL (data.sec.gov companyfacts), so the forecasters stop fetching the same
basics five times and the citation gate can check them by script (token cut, Kilian 2026-10-13).

Usage:
  python3 secfacts.py pack DATE SYM        -> runs/DATE/SYM/facts.json + facts.md (company fact pack, ids F1..)
  python3 secfacts.py peers SYM [SYM ...]  -> prints a filing-based peer table (ids like VZ.ev_ebitda_ttm)
  python3 secfacts.py show SYM             -> prints the fact pack without writing anything
A symbol missing from data/cik_map.json can be given as SYM:CIK (checked against the SEC's own ticker list).

Every figure carries the filing it came from (accession + EDGAR folder URL) and the API URL it was read from.
Derived figures (TTM, EBITDA, EV, multiples) say basis "derived" and show the arithmetic.
Generic User-Agent with no personal details (privacy rule). Cache: /tmp/secfacts_cache (24 h).
Prices for the peer table: Yahoo chart feed (last close)."""
import json, os, subprocess, sys, time, datetime as dt
HERE = os.path.dirname(os.path.abspath(__file__))
STALE_BEFORE = (dt.date.today() - dt.timedelta(days=600)).isoformat()
UA = 'valuation-research script'
CACHE = '/tmp/secfacts_cache'
os.makedirs(CACHE, exist_ok=True)

def _get(url, path, max_age=86400):
    if os.path.exists(path) and time.time() - os.path.getmtime(path) < max_age:
        return json.load(open(path))
    r = subprocess.run(['curl', '-sS', '-A', UA, '-o', path + '.tmp', '-w', '%{http_code}', url], capture_output=True, text=True)
    time.sleep(0.15)
    if r.stdout.strip() != '200':
        if os.path.exists(path + '.tmp'): os.remove(path + '.tmp')
        raise RuntimeError(f'{url} -> HTTP {r.stdout.strip() or r.stderr.strip()}')
    os.replace(path + '.tmp', path)
    return json.load(open(path))

def cik_of(sym):
    if ':' in sym:
        s, c = sym.split(':', 1); c = int(c)
        sub = submissions(c)
        if s.upper() not in [t.upper() for t in sub.get('tickers', [])]:
            raise RuntimeError(f'CIK {c} is {sub.get("name")} with tickers {sub.get("tickers")}, not {s}')
        return s.upper(), c
    m = json.load(open(os.path.join(HERE, 'data', 'cik_map.json')))
    if sym.upper() in m: return sym.upper(), m[sym.upper()]['cik']
    raise RuntimeError(f'{sym}: CIK unknown; pass it as {sym}:CIK (find it on EDGAR company search)')

def submissions(cik): return _get(f'https://data.sec.gov/submissions/CIK{cik:010d}.json', f'{CACHE}/sub_{cik:010d}.json')
def companyfacts(cik): return _get(f'https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json', f'{CACHE}/cf_{cik:010d}.json')

def folder(cik, accn): return f'https://www.sec.gov/Archives/edgar/data/{cik}/{accn.replace("-", "")}/'

# concept preference lists (first one with the newest data wins)
C = {
 'revenue': ['RevenueFromContractWithCustomerExcludingAssessedTax', 'Revenues', 'SalesRevenueNet',
             'RevenueFromContractWithCustomerIncludingAssessedTax', 'Revenue'],
 'operating_income': ['OperatingIncomeLoss', 'ProfitLossFromOperatingActivities'],
 'net_income': ['NetIncomeLoss', 'ProfitLossAttributableToOwnersOfParent', 'ProfitLoss'],
 'eps_diluted': ['EarningsPerShareDiluted', 'DilutedEarningsLossPerShare'],
 'diluted_shares_wavg': ['WeightedAverageNumberOfDilutedSharesOutstanding', 'WeightedAverageShares'],
 'd_and_a': ['DepreciationDepletionAndAmortization', 'DepreciationAndAmortization', 'DepreciationAmortizationAndAccretionNet',
             'DepreciationAmortizationAndImpairment', 'DepreciationAndAmortisationExpense'],
 'sbc': ['ShareBasedCompensation', 'AllocatedShareBasedCompensationExpense'],
 'cfo': ['NetCashProvidedByUsedInOperatingActivities', 'CashFlowsFromUsedInOperatingActivities'],
 'capex': ['PaymentsToAcquirePropertyPlantAndEquipment', 'PaymentsToAcquireProductiveAssets',
           'PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities'],
 'buybacks': ['PaymentsForRepurchaseOfCommonStock'],
 'dividends_paid': ['PaymentsOfDividendsCommonStock', 'PaymentsOfDividends', 'DividendsPaidClassifiedAsFinancingActivities'],
 'interest_expense': ['InterestExpense', 'InterestExpenseNonoperating', 'InterestExpenseDebt'],
 'income_tax': ['IncomeTaxExpenseBenefit'],
 'pretax_income': ['IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest',
                   'IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments'],
}
B = {  # balance-sheet instants
 'cash': ['CashAndCashEquivalentsAtCarryingValue', 'CashAndCashEquivalents', 'CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents'],
 'st_investments': ['ShortTermInvestments', 'MarketableSecuritiesCurrent', 'AvailableForSaleSecuritiesDebtSecuritiesCurrent'],
 'debt_total': ['LongTermDebt', 'DebtLongtermAndShorttermCombinedAmount', 'LongTermDebtAndCapitalLeaseObligationsIncludingCurrentMaturities'],
 'debt_noncurrent': ['LongTermDebtNoncurrent', 'LongTermDebtAndCapitalLeaseObligations'],
 'debt_current': ['LongTermDebtCurrent', 'DebtCurrent', 'LongTermDebtAndCapitalLeaseObligationsCurrent'],
 'st_borrowings': ['ShortTermBorrowings', 'CommercialPaper'],
 'equity': ['StockholdersEquity', 'EquityAttributableToOwnersOfParent'],
}
ANNUAL_FORMS = ('10-K', '10-K/A', '20-F', '20-F/A', '40-F', '10-KT')
Q_FORMS = ('10-Q', '10-Q/A')

def _days(e): return (dt.date.fromisoformat(e['end']) - dt.date.fromisoformat(e['start'])).days if e.get('start') else 0

def _series(cf, names):
    """(concept, unit, entries) for the concept with the newest end date."""
    best = None
    for tax in ('us-gaap', 'ifrs-full', 'dei'):
        for n in names:
            c = cf.get('facts', {}).get(tax, {}).get(n)
            if not c: continue
            for unit, es in c['units'].items():
                es = [e for e in es if e.get('form') in ANNUAL_FORMS + Q_FORMS]
                if not es: continue
                newest = max(e['end'] for e in es)
                if best is None or newest > best[3]: best = (f'{tax}:{n}', unit, es, newest)
    return best and best[:3]

def _dedupe(es):  # one value per (start,end): latest filing wins
    d = {}
    for e in sorted(es, key=lambda e: e['filed']): d[(e.get('start'), e['end'])] = e
    return list(d.values())

def flows(cf, names):
    s = _series(cf, names)
    if not s: return None
    concept, unit, es = s; es = _dedupe(es)
    fy = sorted([e for e in es if 330 <= _days(e) <= 400 and e['form'] in ANNUAL_FORMS], key=lambda e: e['end'])
    if STALE_BEFORE and (not fy or fy[-1]['end'] < STALE_BEFORE): return None  # tag no longer used: say nothing rather than old data
    out = dict(concept=concept, unit=unit, fy=fy[-3:], ytd=None, ytd_prior=None, q=None)
    qs = sorted([e for e in es if e['form'] in Q_FORMS and 80 <= _days(e) <= 100], key=lambda e: e['end'])
    if qs: out['q'] = qs[-1]
    if fy:
        last_fy_end = dt.date.fromisoformat(fy[-1]['end'])
        ytds = [e for e in es if e['form'] in Q_FORMS and e.get('start') and
                abs((dt.date.fromisoformat(e['start']) - last_fy_end).days - 1) <= 7 and _days(e) < 330]
        if ytds:
            y = max(ytds, key=lambda e: e['end']); out['ytd'] = y
            ye = dt.date.fromisoformat(y['end']); ys = dt.date.fromisoformat(y['start'])
            pri = [e for e in es if e.get('start') and abs((ye - dt.date.fromisoformat(e['end'])).days - 365) <= 10
                   and abs((ys - dt.date.fromisoformat(e['start'])).days - 365) <= 10]
            if pri: out['ytd_prior'] = max(pri, key=lambda e: e['filed'])
    return out

def instant(cf, names, at):
    """Value at the latest balance-sheet date `at` only (never mixes dates); first concept tagged at that date wins."""
    for tax in ('us-gaap', 'ifrs-full'):
        for n in names:
            c = cf.get('facts', {}).get(tax, {}).get(n)
            if not c: continue
            for unit, es in c['units'].items():
                es = [e for e in es if not e.get('start') and e['end'] == at and e.get('form') in ANNUAL_FORMS + Q_FORMS]
                if es: return dict(concept=f'{tax}:{n}', unit=unit, e=max(es, key=lambda e: e['filed']))
    return None

def balance_date(cf):
    ends = []
    for tax in ('us-gaap', 'ifrs-full'):
        for n in ('StockholdersEquity', 'EquityAttributableToOwnersOfParent', 'Assets'):
            for es in cf.get('facts', {}).get(tax, {}).get(n, {}).get('units', {}).values():
                ends += [e['end'] for e in es if not e.get('start') and e.get('form') in ANNUAL_FORMS + Q_FORMS]
    return max(ends) if ends else None

def shares_out(cf):
    c = cf.get('facts', {}).get('dei', {}).get('EntityCommonStockSharesOutstanding')
    if not c: return None
    es = c['units'].get('shares', [])
    if not es: return None
    last = max(es, key=lambda e: (e['filed'], e['end']))
    same = [e for e in es if e['accn'] == last['accn'] and e['end'] == last['end']]  # several share classes: sum them
    return dict(value=sum(e['val'] for e in same), e=last, classes=len(same))

def build(sym):
    sym, cik = cik_of(sym)
    cf = companyfacts(cik); api = f'https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json'
    F = []; ids = {}
    def add(key, label, value, unit, e=None, basis='reported', derivation='', period=''):
        fid = f'F{len(F)+1}'
        row = dict(id=fid, key=key, label=label, value=value, unit=unit, basis=basis, period=period)
        if e: row.update(start=e.get('start'), end=e['end'], form=e['form'], filed=e['filed'], accn=e['accn'],
                         url=folder(cik, e['accn']), api=api)
        if derivation: row['derivation'] = derivation
        F.append(row); ids[key] = row; return row
    for k, names in C.items():
        f = flows(cf, names)
        if not f: continue
        for e in f['fy']:
            add(f'{k}_fy{e["end"][:4]}', f'{k} FY ending {e["end"]} ({f["concept"]})', e['val'], f['unit'], e, period='FY')
        if f['ytd'] and f['ytd_prior'] and f['fy']:
            y, p, last = f['ytd'], f['ytd_prior'], f['fy'][-1]
            add(f'{k}_ytd', f'{k} YTD {y["start"]} to {y["end"]}', y['val'], f['unit'], y, period='YTD')
            add(f'{k}_ytd_prior', f'{k} prior-year YTD {p["start"]} to {p["end"]}', p['val'], f['unit'], p, period='YTD')
            if k != 'eps_diluted' and k != 'diluted_shares_wavg':
                ttm = last['val'] + y['val'] - p['val']
                add(f'{k}_ttm', f'{k} TTM to {y["end"]}', ttm, f['unit'], basis='derived', period='TTM',
                    derivation=f'FY {last["end"]} {last["val"]/1e6:,.1f}M + YTD to {y["end"]} {y["val"]/1e6:,.1f}M - prior-year YTD {p["val"]/1e6:,.1f}M')
        elif f['fy'] and k not in ('eps_diluted', 'diluted_shares_wavg'):
            last = f['fy'][-1]
            if not f['q'] or f['q']['end'] <= last['end']:
                add(f'{k}_ttm', f'{k} TTM to {last["end"]} (latest filing is the annual report)', last['val'], f['unit'],
                    basis='derived', period='TTM', derivation=f'= FY ending {last["end"]}')
        if f['q'] and (not f['fy'] or f['q']['end'] > f['fy'][-1]['end']):
            add(f'{k}_q', f'{k} quarter {f["q"]["start"]} to {f["q"]["end"]}', f['q']['val'], f['unit'], f['q'], period='Q')
    # TTM diluted EPS from quarters is not additive across share counts; use net income TTM / latest diluted wavg as a check only
    at = balance_date(cf)
    for k, names in B.items():
        i = at and instant(cf, names, at)
        if i: add(k, f'{k} at {i["e"]["end"]} ({i["concept"]})', i['e']['val'], i['unit'], i['e'], period='instant')
    so = shares_out(cf)
    if so and at and abs((dt.date.fromisoformat(so['e']['end']) - dt.date.fromisoformat(at)).days) > 150: so = None  # stale cover tag
    if not so and 'diluted_shares_wavg_q' in ids:
        q = ids['diluted_shares_wavg_q']
        add('shares_outstanding', 'shares: latest quarter diluted weighted average (cover-page count not in standard tags)',
            q['value'], 'shares', basis='derived', derivation=f"= {q['id']}")
    if so: add('shares_outstanding', f'shares outstanding (cover page, {so["classes"]} class(es)) as of {so["e"]["end"]}',
               so['value'], 'shares', so['e'], period='instant')
    # derived: debt, net debt, EBITDA, FCF
    debt = None
    if 'debt_total' in ids: debt = (ids['debt_total']['value'], f"{ids['debt_total']['id']}")
    elif 'debt_noncurrent' in ids:
        v = ids['debt_noncurrent']['value'] + (ids['debt_current']['value'] if 'debt_current' in ids else 0)
        debt = (v, ids['debt_noncurrent']['id'] + ('+' + ids['debt_current']['id'] if 'debt_current' in ids else ''))
    if not debt:
        add('debt', f'total debt: not in standard XBRL tags at {at} (company-specific tags); read the balance sheet and debt note',
            None, 'USD', basis='missing')
    if debt:
        v, how = debt
        if 'st_borrowings' in ids: v += ids['st_borrowings']['value']; how += '+' + ids['st_borrowings']['id']
        add('debt', 'total debt (XBRL tags; excludes leases unless tagged in; check the balance sheet)', v, 'USD',
            basis='derived', derivation=how)
        cash = (ids['cash']['value'] if 'cash' in ids else 0) + (ids['st_investments']['value'] if 'st_investments' in ids else 0)
        add('net_debt', 'net debt = debt - cash - short-term investments', v - cash, 'USD', basis='derived',
            derivation=f"{ids['debt']['id']} - {ids.get('cash', {}).get('id', '0')} - {ids.get('st_investments', {}).get('id', '0')}")
    if 'operating_income_ttm' in ids and 'd_and_a_ttm' in ids:
        add('ebitda_ttm', 'EBITDA TTM (GAAP operating income + D&A; not the company\'s adjusted EBITDA)',
            ids['operating_income_ttm']['value'] + ids['d_and_a_ttm']['value'], 'USD', basis='derived',
            derivation=f"{ids['operating_income_ttm']['id']} + {ids['d_and_a_ttm']['id']}")
    if 'cfo_ttm' in ids and 'capex_ttm' in ids:
        add('fcf_ttm', 'free cash flow TTM = operating cash flow - capex (before SBC adjustment)',
            ids['cfo_ttm']['value'] - ids['capex_ttm']['value'], 'USD', basis='derived',
            derivation=f"{ids['cfo_ttm']['id']} - {ids['capex_ttm']['id']}")
    if 'net_income_ttm' in ids and 'diluted_shares_wavg_q' in ids:
        add('eps_ttm_approx', 'diluted EPS TTM approx = net income TTM / latest quarter diluted shares',
            ids['net_income_ttm']['value'] / ids['diluted_shares_wavg_q']['value'], 'USD/shares', basis='derived',
            derivation=f"{ids['net_income_ttm']['id']} / {ids['diluted_shares_wavg_q']['id']}")
    sub = submissions(cik)
    r = sub['filings']['recent']; fl = []
    for i in range(len(r['form'])):
        fm = r['form'][i]
        if fm in ('10-K', '10-Q', '20-F', '40-F', '6-K', 'DEF 14A') or (fm == '8-K' and '2.02' in (r.get('items', [''] * 999)[i] or '')):
            fl.append(dict(form=fm, filed=r['filingDate'][i], period=r['reportDate'][i], items=(r.get('items') or [''] * 999)[i],
                           url=folder(cik, r['accessionNumber'][i]) + r['primaryDocument'][i]))
        if len(fl) >= 10: break
    cur = {u for f in F for u in [f['unit']] if u not in ('shares', 'pure')}
    rc = next((f['unit'] for f in F if f['key'].startswith('revenue')), next(iter(cur), None))
    return dict(symbol=sym, cik=cik, name=sub.get('name'), fiscal_year_end=sub.get('fiscalYearEnd'), units=sorted(cur), currency=rc,
                built=dt.datetime.utcnow().strftime('%Y-%m-%dT%H:%MZ'), source_api=api, facts=F, filings=fl)

def fmt(v, unit):
    if v is None: return 'n/a'
    if unit in ('USD/shares',) or (isinstance(v, float) and abs(v) < 1000): return f'{v:,.2f}'
    if abs(v) >= 1e6: return f'{v/1e6:,.1f}M'
    return f'{v:,.0f}'

def to_md(p):
    L = [f"# {p['symbol']} fact pack ({p['name']}, CIK {p['cik']}), built {p['built']} from SEC XBRL",
         f"Units: {', '.join(p['units'])} (M = millions). Cite a fact by id in `fact_ids` and add its filing URL to your sources.",
         'Reported = as tagged in the filing; derived = arithmetic shown. Tags can miss items (leases, preferred, segment detail):',
         'check the filing itself before relying on a figure the tags may not capture.', '',
         '| id | figure | value | period end | form, filed | basis |', '|---|---|---|---|---|---|']
    for f in p['facts']:
        if f['period'] == 'YTD': continue  # in facts.json (behind the TTM arithmetic), left out here to keep the pack short
        L.append(f"| {f['id']} | {f['label']} | {fmt(f['value'], f['unit'])} | {f.get('end', '')} | "
                 f"{f.get('form', '')} {f.get('filed', '')} | {f['basis']}{(': ' + f['derivation']) if f.get('derivation') else ''} |")
    L += ['', 'Filings (newest first):'] + [f"- {x['form']} filed {x['filed']} (period {x['period']}{', items ' + x['items'] if x['items'] else ''}): {x['url']}" for x in p['filings']]
    L += ['', 'Filing folders: each fact\'s `url` in facts.json; API source: ' + p['source_api']]
    return '\n'.join(L) + '\n'

def price(sym):
    d = _get(f'https://query1.finance.yahoo.com/v8/finance/chart/{sym}?range=5d&interval=1d', f'{CACHE}/px_{sym}.json', 3600)
    m = d['chart']['result'][0]['meta']
    return m['regularMarketPrice'], dt.datetime.utcfromtimestamp(m['regularMarketTime']).date().isoformat(), m.get('currency')

def peer_rows(syms):
    rows = []
    for s in syms:
        try: p = build(s)
        except Exception as e: rows.append(dict(symbol=s.split(':')[0].upper(), error=str(e))); continue
        g = {f['key']: f for f in p['facts']}
        row = dict(symbol=p['symbol'], name=p['name'])
        if p.get('currency') != 'USD':
            row['error'] = f"reports in {p.get('currency')}: convert currency and ADR ratio yourself (facts: python3 secfacts.py show {p['symbol']})"
            rows.append(row); continue
        try: px, pdate, ccy = price(p['symbol'])
        except Exception as e: row['error'] = f'no price: {e}'; rows.append(row); continue
        row.update(price=px, price_date=pdate)
        so = g.get('shares_outstanding'); nd = g.get('net_debt')
        if so: row['mcap'] = px * so['value']
        if so and nd: row['ev'] = row['mcap'] + nd['value']
        for k, lab in (('revenue_ttm', 'rev'), ('ebitda_ttm', 'ebitda'), ('net_income_ttm', 'ni'), ('fcf_ttm', 'fcf')):
            if k in g: row[lab + '_ttm'] = g[k]['value']
        if 'ev' in row and row.get('ebitda_ttm', 0) > 0: row['ev_ebitda_ttm'] = row['ev'] / row['ebitda_ttm']
        if 'ev' in row and row.get('rev_ttm', 0) > 0: row['ev_sales_ttm'] = row['ev'] / row['rev_ttm']
        if 'mcap' in row and row.get('ni_ttm', 0) > 0: row['pe_ttm'] = row['mcap'] / row['ni_ttm']
        if 'mcap' in row and row.get('fcf_ttm', 0) > 0: row['fcf_yield_ttm'] = row['fcf_ttm'] / row['mcap']
        row['inputs'] = {k: dict(id=g[k]['id'], value=g[k]['value'], end=g[k].get('end'), url=g[k].get('url'),
                                 derivation=g[k].get('derivation')) for k in
                         ('shares_outstanding', 'net_debt', 'debt', 'cash', 'revenue_ttm', 'ebitda_ttm', 'net_income_ttm', 'fcf_ttm') if k in g}
        rows.append(row)
    return rows

def peers_md(rows):
    L = ['Filing-based TRAILING multiples (SEC XBRL + Yahoo last close). EBITDA = GAAP operating income + D&A, not adjusted;',
         'P/E = market cap / GAAP net income TTM. Forward multiples still need NTM consensus; build them as price / consensus',
         'and cite the consensus page. Cite these by id, e.g. VZ.ev_ebitda_ttm.', '',
         '| peer | price (date) | mkt cap $B | EV $B | EV/EBITDA ttm | EV/sales ttm | P/E ttm | FCF yield | inputs |',
         '|---|---|---|---|---|---|---|---|---|']
    for r in rows:
        if 'error' in r: L.append(f"| {r['symbol']} | {r['error']} |  |  |  |  |  |  |  |"); continue
        f = lambda k, d=1, m=1: (f'{r[k]*m:,.{d}f}' if k in r else 'n/a')
        L.append(f"| {r['symbol']} | {r['price']:.2f} ({r['price_date']}) | {f('mcap', 1, 1e-9)} | {f('ev', 1, 1e-9)} | {f('ev_ebitda_ttm')}x | "
                 f"{f('ev_sales_ttm', 2)}x | {f('pe_ttm')}x | {f('fcf_yield_ttm', 1, 100)}% | "
                 + ', '.join(f"{k.replace('_outstanding', '').replace('_ttm', '')} {v['id']}" for k, v in r['inputs'].items()) + ' |')
    return '\n'.join(L) + '\n'

if __name__ == '__main__':
    a = sys.argv[1:]
    if not a: print(__doc__); sys.exit(1)
    if a[0] == 'pack':
        date, sym = a[1], a[2]
        p = build(sym); rd = os.path.join(HERE, 'runs', date, p['symbol']); os.makedirs(rd, exist_ok=True)
        json.dump(p, open(f'{rd}/facts.json', 'w'), indent=1); open(f'{rd}/facts.md', 'w').write(to_md(p))
        print(p['symbol'], len(p['facts']), 'facts ->', f'{rd}/facts.md')
    elif a[0] == 'show':
        print(to_md(build(a[1])))
    elif a[0] == 'peers':
        rows = peer_rows(a[1:])
        if '--json' in a: print(json.dumps(rows, indent=1))
        else: print(peers_md(rows))
    else: print(__doc__); sys.exit(1)
