"""Balance sheet risk flag (Kilian 2026-11-01 card "Add an info flag only"). INFORMATION ONLY: nothing here feeds targets,
tilts, conviction, sizing, allocate or review. It is shown on the Price Targets page (name card + a chip on the rows) and on
idea-scout pitches.

Backtest behind it: ../bs_health_backtest (doc https://claude.ai/artifact/6NiHZrzWqgazir6MZa1GmA). Balance sheet health
did not predict 12-month returns, but the weak end had about two to three times the odds of a 50%+ fall within 12 months:
  (a) interest cover (EBIT / interest) under 2x, or EBIT below zero with debt ........ 10.4% vs 3.6% for other names with debt
  (b) asset-heavy industries only: DSCR = EBITDA / (interest + debt due within 1 year)
      under 1.25x, or EBITDA <= 0 with debt ......................................... 9.3% vs 5.0% for others in that set
  (c) weakest fifth on the peer composite (mean of within-GICS-industry percentile ranks
      of ND/EBITDA, book D/E, interest cover, net cash / market cap) ................. 6.5% vs 2.4% for the healthiest fifth
Utilities (GICS 55) get no flag (healthier utilities did worse; the weak-end rule does not carry over). Financials (GICS 40)
get no flag either: a bank's or insurer's interest is its cost of goods, the backtest never covered them, and the universe
excludes them anyway.

Data and definitions are the backtest's, so the flag is the tested signal and not a variant of it:
  Compustat Fundamentals Annual (wrds/raw/comp_gics.csv.gz, INDL/STD/consolidated), latest fiscal year per company
  within 550 days of AS_OF (a non-USD filer such as CCJ gets tests (a) and (b), which are unit-free, but no composite: the
  peer universe is USD filers only, as in the backtest); debt = dltt + dlc, EBIT = oiadp, EBITDA = oibdp, interest = xint, due in 1 year = dd1,
  cash = che, book equity = ceq, market cap = csho x prcc_f at the fiscal year end (the backtest used the CRSP month-end cap;
  the fiscal-year-end cap keeps every peer on one date).
  Peers: every USD company in the file with cap >= $1B and price >= $5 (all sectors except 40 and 55), grouped by 6-digit GICS
  industry; an industry with fewer than 6 names falls back to its 4-digit group, and fewer than 4 there gets no composite.
  "Weakest fifth" = bottom 20% of the composite across the whole peer universe, as the backtest's quintiles were.
  Asset-heavy set (DSCR test): transportation 2030xx, trading cos & distributors incl. leasing 201070, construction &
  engineering 201030, containers 151030, metals & mining 151040, paper & forest 151050, energy (sector 10).
  The four metrics use the backtest's edge rules (no debt and no interest = healthiest; EBIT/EBITDA <= 0 with debt = weakest).

usage:
  python3 bs_flags.py universe            -> data/bs_peer_universe.csv (rebuild after a new Compustat download; ~1 min)
  python3 bs_flags.py [AS_OF]             -> data/bs_flags.json for every name with a run folder, in sectors.json or held
  python3 bs_flags.py show SYM [SYM ...]  -> prints one line per name (idea-scout pitches; writes nothing)
"""
import datetime as dt, glob, json, os, sys
import numpy as np, pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
RAW = '/mnt/project-files/wrds/raw/comp_gics.csv.gz'
UNI = f'{HERE}/data/bs_peer_universe.csv'
OUT = f'{HERE}/data/bs_flags.json'
DOC = 'https://claude.ai/artifact/6NiHZrzWqgazir6MZa1GmA'
COLS = ['tic', 'conm', 'gvkey', 'datadate', 'fyear', 'curcd', 'indfmt', 'datafmt', 'consol', 'gind', 'gsector',
        'at', 'ceq', 'che', 'dd1', 'dlc', 'dltt', 'oiadp', 'oibdp', 'xint', 'csho', 'prcc_f']
ODDS = {'icov': ('10.4%', '3.6%', 'other names with debt'), 'dscr': ('9.3%', '5.0%', 'others in the asset-heavy set'),
        'weak5': ('6.5%', '2.4%', 'the healthiest fifth')}
NOTE = ('Information only: higher odds of a 50%+ fall within 12 months, not a return signal '
        '(balance sheet health showed no return edge in the backtest). Targets and sizing are unchanged.')


def build_universe():
    d = pd.read_csv(RAW, usecols=COLS, dtype={'gvkey': str, 'gind': str, 'gsector': str, 'tic': str})
    d = d[(d.indfmt == 'INDL') & (d.datafmt == 'STD') & (d.consol == 'C')].drop(columns=['indfmt', 'datafmt', 'consol'])
    d['datadate'] = pd.to_datetime(d.datadate)
    d = d[d.datadate >= pd.Timestamp('2023-01-01')]
    d = d.sort_values(['gvkey', 'datadate']).drop_duplicates('gvkey', keep='last')
    d['gind'] = d.gind.str.replace(r'\.0$', '', regex=True); d['gsector'] = d.gsector.str.replace(r'\.0$', '', regex=True)
    d.to_csv(UNI, index=False)
    print(f'{UNI}: {len(d)} companies, latest fiscal year end {d.datadate.max().date()}')


def _wb(h, worst=None, best=None, drop=None):
    h = h.copy()
    if worst is not None: h[worst] = -1e9
    if best is not None: h[best] = 1e9
    if drop is not None: h[drop] = np.nan
    return h


def compute(as_of):
    d = pd.read_csv(UNI, dtype={'gvkey': str, 'gind': str, 'gsector': str, 'tic': str}, parse_dates=['datadate'])
    d = d[(pd.Timestamp(as_of) - d.datadate).dt.days <= 550].copy()
    for c in ['at', 'ceq', 'che', 'dd1', 'dlc', 'dltt', 'oiadp', 'oibdp', 'xint', 'csho', 'prcc_f']:
        d[c] = pd.to_numeric(d[c], errors='coerce')
    d['debt'] = d.dltt.fillna(0) + d.dlc.fillna(0)
    d.loc[d.dltt.isna() & d.dlc.isna(), 'debt'] = np.nan
    d['nd'] = d.debt - d.che.fillna(0)
    d['cap'] = d.csho * d.prcc_f
    d = d.dropna(subset=['debt', 'at']); d = d[d['at'] > 0]
    eb, nd, debt, xi = d.oibdp, d.nd, d.debt, d.xint
    d['h_nd_ebitda'] = _wb(-(nd / eb), worst=(eb <= 0) & (nd > 0), drop=((eb <= 0) & (nd <= 0)) | eb.isna())
    d['h_de_book'] = _wb(-(debt / d.ceq), worst=(d.ceq <= 0) & (debt > 0), drop=(d.ceq <= 0) & (debt <= 0))
    h = _wb(d.oiadp / xi, best=(xi.fillna(0) <= 0) & (debt <= 0), drop=(xi.fillna(0) <= 0) & (debt > 0))
    neg = d.oiadp < 0
    h[neg] = -1e9 + d.oiadp[neg].clip(-1e6, 0)
    d['h_icov'] = h
    d['h_netcash'] = (d.che.fillna(0) - debt) / d.cap
    # raw ratios for display
    d['icov'] = np.where(xi > 0, d.oiadp / xi, np.nan)
    svc = xi.fillna(0) + d.dd1.fillna(0)
    d['dscr'] = np.where((eb > 0) & (svc > 0), eb / svc, np.nan)
    d['nd_ebitda'] = np.where(eb > 0, nd / eb, np.nan)
    d['de_book'] = np.where(d.ceq > 0, debt / d.ceq, np.nan)
    d['netcash_cap'] = (d.che.fillna(0) - debt) / d.cap
    g6 = d.gind.fillna('')
    d['asset_heavy'] = g6.str[:4].eq('2030') | g6.isin(['201070', '201030', '151030', '151040', '151050']) | d.gsector.eq('10')
    # the absolute tests apply to any covered company; the composite needs the peer universe
    d['f_icov'] = (d.icov < 2) | ((d.oiadp < 0) & (debt > 0))
    d['f_dscr'] = d.asset_heavy & ((d.dscr < 1.25) | ((eb <= 0) & (debt > 0)))
    U = d[(d.curcd == 'USD') & (d.cap >= 1000) & (d.prcc_f >= 5) & ~d.gsector.isin(['40', '55']) & d.gind.notna()].copy()
    U['ind'] = U.gind
    n = U.groupby('ind').gvkey.transform('size'); U.loc[n < 6, 'ind'] = U.loc[n < 6, 'gind'].str[:4]
    n = U.groupby('ind').gvkey.transform('size'); U = U[n >= 4].copy()
    U['n_peers'] = U.groupby('ind').gvkey.transform('size')
    M = ['h_nd_ebitda', 'h_de_book', 'h_icov', 'h_netcash']
    for c in M: U['k_' + c] = U.groupby('ind')[c].rank(pct=True, method='average')
    U['composite'] = U[['k_' + c for c in M]].mean(axis=1, skipna=False)
    U['comp_pct'] = U.composite.rank(pct=True, method='average')
    d = d.join(U[['ind', 'n_peers', 'composite', 'comp_pct'] + ['k_' + c for c in M]])
    d['f_weak5'] = d.comp_pct <= 0.2
    return d


def record(row):
    excl = {'55': 'utility (no flag: the weak-end rule does not hold for utilities)',
            '40': 'financial (no flag: interest is a bank or insurer\'s cost of goods; not in the backtest)'}.get(row.gsector)
    r = lambda x, k=2: None if pd.isna(x) else round(float(x), k)
    out = dict(company=row.conm, fiscal_year_end=row.datadate.date().isoformat(), gics_industry=row.gind,
               peer_group=None if pd.isna(row.ind) else row.ind, n_peers=None if pd.isna(row.n_peers) else int(row.n_peers),
               interest_cover=r(row.icov, 1), ebit_negative_with_debt=bool(row.oiadp < 0 and row.debt > 0),
               asset_heavy=bool(row.asset_heavy), dscr=r(row.dscr) if row.asset_heavy else None,
               nd_ebitda=r(row.nd_ebitda, 1), de_book=r(row.de_book), netcash_cap=r(row.netcash_cap, 3),
               composite_pct=r(row.comp_pct, 3), debt_musd=r(row.debt, 0))
    if excl:
        out.update(flag=False, tests=[], excluded=excl); return out
    tests = []
    if row.f_icov:
        tests.append(dict(test='icov', text=('EBIT below zero with debt' if row.oiadp < 0 else f'interest cover {row.icov:.1f}x (under 2x)'),
                          odds=ODDS['icov']))
    if row.f_dscr:
        tests.append(dict(test='dscr', text=(f'DSCR {row.dscr:.2f}x (under 1.25x, asset-heavy industry)' if not pd.isna(row.dscr)
                                             else 'EBITDA at or below zero with debt (asset-heavy industry)'), odds=ODDS['dscr']))
    if row.f_weak5:
        tests.append(dict(test='weak5', text=f'weakest fifth on the peer composite ({ord_(row.comp_pct)} percentile; {int(row.n_peers)} peers in GICS {row.ind})',
                          odds=ODDS['weak5']))
    out.update(flag=bool(tests), tests=tests)
    return out


def ord_(p):
    n = max(1, int(round(p * 100))); s = 'th' if 10 <= n % 100 <= 20 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')
    return f'{n}{s}'


def line(sym, rec):
    """One line for a scout pitch or a log."""
    if rec is None: return f'{sym}: balance sheet flag n/a (no recent Compustat annual for this ticker)'
    if rec.get('excluded'): return f'{sym}: no balance sheet flag ({rec["excluded"]})'
    if not rec['flag']:
        bits = [f'cover {rec["interest_cover"]}x' if rec['interest_cover'] is not None else ('no interest expense' if not rec['debt_musd'] else None),
                f'DSCR {rec["dscr"]}x' if rec['dscr'] is not None else None,
                f'composite {ord_(rec["composite_pct"])} percentile' if rec['composite_pct'] is not None else 'no peer composite']
        return f'{sym}: no balance sheet flag (FY {rec["fiscal_year_end"]}: ' + ', '.join(b for b in bits if b) + ')'
    t = '; '.join(f'{x["text"]}: {x["odds"][0]} of such names fell 50%+ within 12 months vs {x["odds"][1]} for {x["odds"][2]}' for x in rec['tests'])
    return f'{sym}: BALANCE SHEET FLAG (FY {rec["fiscal_year_end"]}): {t}. Information only, not a return signal.'


def lookup(d, syms):
    t = d.assign(tk=d.tic.str.upper().str.replace('.', '-', regex=False))
    out = {}
    for s in syms:
        k = s.upper().replace('.', '-')
        m = t[t.tk == k]
        if len(m) == 0 and '-' in k: m = t[t.tk == k.replace('-', '')]
        out[s] = record(m.sort_values('datadate').iloc[-1]) if len(m) else None
    return out


def page_names(as_of):
    # sectors.json lists every researched name; the newest run folders add any new one (one listdir each: the mount is slow)
    S = set(json.load(open(f'{HERE}/data/sectors.json')))
    for r in sorted(x for x in os.listdir(f'{HERE}/runs') if x[:2] == '20' and x <= as_of)[-3:]:
        S |= {x for x in os.listdir(f'{HERE}/runs/{r}') if '.' not in x}
    try:
        sys.path.insert(0, HERE); import book
        S |= {p['symbol'] for p in book.load()['positions'] if p.get('shares')}
    except Exception: pass
    return sorted(x for x in S if x.isupper() or '-' in x or '.' in x)


def main():
    a = sys.argv[1:]
    if a[:1] == ['universe']: return build_universe()
    if a[:1] == ['show']:
        d = compute(dt.date.today().isoformat())
        for s, rec in lookup(d, [x.upper() for x in a[1:]]).items(): print(line(s, rec))
        return
    as_of = a[0] if a else dt.date.today().isoformat()
    d = compute(as_of)
    names = page_names(as_of)
    recs = lookup(d, names)
    src_date = dt.date.fromtimestamp(os.path.getmtime(RAW)).isoformat()
    res = dict(as_of=as_of, source=f'Compustat Fundamentals Annual (download {src_date}), latest fiscal year per company',
               method='bs_flags.py; docs/how_it_works.md "Balance sheet risk flag"', backtest_doc=DOC, note=NOTE,
               peer_universe=int(d.comp_pct.notna().sum()), names={k: v for k, v in recs.items() if v is not None},
               not_covered=sorted(k for k, v in recs.items() if v is None))
    tmp = OUT + '.tmp'; json.dump(res, open(tmp, 'w'), indent=1); os.replace(tmp, OUT)
    fl = [k for k, v in res['names'].items() if v['flag']]
    print(f'{OUT}: {len(res["names"])} names, {len(fl)} flagged: {" ".join(fl)}; not covered: {" ".join(res["not_covered"])}')


if __name__ == '__main__':
    main()
