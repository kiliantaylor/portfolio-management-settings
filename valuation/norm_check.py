"""Normalizing-assumption check for the citation gate (Kilian 2026-11-03: MOD's 24% tax rate passed three gates as a
labelled estimate with no evidence for 24% itself; "this is the type of stuff you should check automatically").

A normalizing assumption is a rate or level the model holds steady or reverts to instead of building it from drivers:
tax rate, interest rate on net debt, diluted shares and their yearly change, D&A / capex / working-capital / SBC % of
revenue, and any "normalized / steady-state / mid-cycle / target" margin. A derivation, a confidence and agent votes are
not evidence for one. Each such row in a reconciled set's `inputs_detail` must carry an `anchor`:

  "anchor": {"value": 0.255, "kind": "company_stated | company_adjusted | reported_history",
             "as_of": "YYYY-MM-DD", "source_ids": [4], "fact_ids": ["F78"], "note": "FY27 adj. ETR 25-26% (Q1 call)"}

kind, in order of preference: the company's own stated estimate (guide, call, slides) > the company's adjusted / non-GAAP
figure for the last fiscal year or TTM > reported GAAP history with discrete items removed (derivation in `note`).
If the row's value is further from the anchor than the tolerance below, the row's `derivation` must bridge the gap with
cited evidence (its own source_ids), and the gate's text agent opens those sources.

Script-only results (no fetch, so no added gate time): `unanchored` (normalizing row with no anchor or no anchor source)
and `missing_row` (the set uses a normalizing field, e.g. years[].tax_rate, with no inputs_detail row for it).

Usage:
  python3 norm_check.py scan                 -> every live driver set (driver_models/*/set.json): normalizing rows and
                                                their anchor status, one line per problem
  python3 norm_check.py set PATH.json        -> the same for one supervisor.json or set.json
"""
import glob, json, os, re, sys, datetime, functools
HERE = os.path.dirname(os.path.abspath(__file__))

# (field, regex on the inputs_detail `input` name, tolerance kind, tolerance)
FIELDS = [
    ('tax_rate', r'tax[ _]rate|effective tax|\bETR\b|after-tax factor', 'abs', 0.01),
    ('interest_rate', r'interest[ _]rate', 'abs', 0.0075),
    ('shares', r'diluted[ _]shares|shares\.(diluted_now|annual_change|horizon)|share count|shares outstanding|^shares (now|at (the )?horizon)|share change|shares annual change|dilution|buyback', 'rel', 0.02),
    ('da_pct_rev', r'd&a|da_pct', 'absrel', (0.005, 0.15)),
    ('capex_pct_rev', r'^(years\.)?capex|capex[ _]?%|capex_pct', 'absrel', (0.005, 0.15)),
    ('nwc_pct_incr_rev', r'nwc|working capital', 'absrel', (0.005, 0.15)),
    ('sbc_pct_rev', r'^(years\.)?sbc|^stock comp|sbc[ _]?%|sbc_pct', 'absrel', (0.005, 0.15)),
    ('norm_margin', r'normali[sz]ed|steady[- ]state|mid[- ]cycle|through[- ]cycle|long[- ]run|target margin|terminal margin', 'abs', 0.01),
]
WEAK_SHARES_STRONG = r'diluted[ _]shares|shares\\.|share count|shares outstanding|^shares |share change'
STALE_DAYS = 90  # a carried (unchanged) row whose last clean check is older than this is re-checked
NUM = re.compile(r'-?\d[\d,]*\.?\d*')

def fields_of(name, value=None):
    n = str(name or '')
    if n.startswith('driver:'): return []  # drivers are built from filed bases with their own guidance_check
    if re.match(r'(sotp|segment_build|horizon_multiples|scenario)', n, re.I): return []
    if re.match(r'shareholder returns', n, re.I): return []
    if re.match(r'(driver-determined|from drivers)', str(value or ''), re.I): return []  # an output of drivers[] (their rows carry the evidence)  # cash returned (net-debt bridge); the share path has its own rows
    if re.match(r'(peer\b|[A-Z]{2,5} (Q[1-4] |FY ?)?20\d\d\b)', n): return []  # peer / other-company data rows (BKNG 'ABNB Q2 2026 ...')
    fs = [f for f, rx, *_ in FIELDS if re.search(rx, n, re.I)]
    if 'shares' in fs and len(fs) > 1 and not re.search(WEAK_SHARES_STRONG, n, re.I): fs.remove('shares')  # 'dilution' in an SBC row's label
    return fs

def num(v):
    if isinstance(v, (int, float)): return float(v)
    m = NUM.search(str(v or ''))
    if not m: return None
    x = float(m.group().replace(',', ''))
    return x / 100 if '%' in str(v)[m.end():m.end() + 2] else x

def gap_ok(field, value, anchor):
    v, a = num(value), num(anchor)
    if v is None or a is None: return None
    if field == 'shares' and abs(a) < 1 and abs(v) < 1: return abs(v - a) <= 0.005  # a yearly share change: 0.5 pt, not 2% of itself
    if field != 'shares':  # rates may be written 24 or 0.24
        if v > 1.5 and a <= 1.5: v /= 100
        if a > 1.5 and v <= 1.5: a /= 100
    kind, tol = next((k, t) for f, _, k, t in FIELDS if f == field)
    d = abs(v - a)
    if kind == 'abs': return d <= tol
    if kind == 'rel': return d <= tol * abs(a)
    return d <= max(tol[0], tol[1] * abs(a))

def used_fields(mi):
    """Normalizing fields the set's engine inputs actually use (so a row must exist for each)."""
    out = set(); yrs = mi.get('years') or []
    for f in ('tax_rate', 'da_pct_rev', 'capex_pct_rev', 'nwc_pct_incr_rev', 'sbc_pct_rev'):
        if any(isinstance(y, dict) and y.get(f) not in (None, 0) for y in yrs): out.add(f)
    bal = mi.get('balance') or {}
    if bal.get('interest_rate') not in (None, 0): out.add('interest_rate')
    elif bal.get('interest_rate') is None and bal.get('net_debt') not in (None, 0): out.add('interest_rate')  # engine default 5% applies
    sh = mi.get('shares') or {}
    if sh.get('diluted_now') is not None: out.add('shares')
    return out

def check_rows(mi):
    """-> list of dicts per normalizing row: input, field, status (ok | unanchored | combined | gap | no_number), and the
    set-level missing_row entries. `gap` rows go to the text agent with their bridge; `ok` rows only need the anchor
    itself checked like any cited figure."""
    res, seen = [], set()
    for r in mi.get('inputs_detail') or []:
        fs = fields_of(r.get('input'), r.get('value'))
        if not fs: continue
        seen.update(fs); f = fs[0]; a = r.get('anchor') or {}
        if len(fs) > 1: st = 'combined'  # one row per normalizing input, each with its own anchor
        elif not a or a.get('value') is None or not (a.get('source_ids') or a.get('fact_ids') or a.get('url')):
            st = 'unanchored'
        else:
            g = gap_ok(f, r.get('value'), a.get('value'))
            st = 'no_number' if g is None else ('ok' if g else 'gap')
        res.append(dict(input=r.get('input'), field=f, value=r.get('value'), anchor=a, status=st, row=r))
    for f in sorted(used_fields(mi) - seen):
        res.append(dict(input=f'(no row) {f}', field=f, value=None, anchor={}, status='missing_row', row={}))
    return res

@functools.lru_cache(maxsize=None)
def _load(p):
    try: return json.load(open(p))
    except Exception: return None

def last_checked(runs, sym, base, item, hops=25):
    """Date of the last clean gate that covered `item`, walking update.json base links back to the last full round."""
    d = base
    for _ in range(hops):
        rd = f'{runs}/{d}/{sym}'
        au = _load(f'{rd}/citation_audit.json')
        if au is None: return d
        ver = au.get('verified')
        if isinstance(ver, list) and any((v.get('item') or '') == item for v in ver if isinstance(v, dict)): return d
        u = _load(f'{rd}/update.json')
        if u is None: return d  # a full round's gate covered every row
        if u.get('mode') == 'build' or not u.get('base'): return d
        d = u['base']
    return d

def stale(date, checked, days=STALE_DAYS):
    try:
        return (datetime.date.fromisoformat(date) - datetime.date.fromisoformat(checked)).days > days
    except Exception: return False

def _mi(path):
    S = json.load(open(path))
    if 'model_inputs' in S: return S['model_inputs']
    # driver sets written as bare model_inputs (AMAT, ABT, ...) were read as empty before 2026-11-03
    return ((S.get('model') or {}).get('reconciled_inputs')) or (S if 'inputs_detail' in S or 'years' in S else {})

if __name__ == '__main__':
    cmd = sys.argv[1] if len(sys.argv) > 1 else 'scan'
    paths = [sys.argv[2]] if cmd == 'set' else sorted(glob.glob(f'{HERE}/driver_models/*/set.json'))
    tot = bad = 0
    for p in paths:
        name = p.split('/')[-2] if cmd == 'scan' else p
        for r in check_rows(_mi(p)):
            tot += 1
            if r['status'] != 'ok':
                bad += 1
                print(f"{name:6s} {r['status']:11s} {r['field']:16s} {str(r['input'])[:40]:40s} = {str(r['value'])[:30]}")
    print(f'{len(paths)} sets, {tot} normalizing rows, {bad} need an anchor or a bridge')
