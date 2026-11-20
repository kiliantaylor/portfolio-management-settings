"""Shared depreciation engine (2026-11-17, Kilian "ok so fix the depreciation path. it seems this is likely wrong for multiple names").

One method for every driver model, replacing flat rates (D&A = x% of revenue or of PP&E), fitted haircuts (GOOGL 0.693, META x0.85)
and depreciation charged on spending that is not yet in service (construction in progress). The method is the one the AMZN and GOOGL
rebuilds used on 2026-11-17 (driver_models/AMZN/dep_rebuild_1117, driver_models/GOOGL/dep_rebuild_1117):

  1. Every quarter's capex is a vintage. Each vintage splits into asset classes (e.g. servers, buildings, fleet).
  2. A class is placed in service `lag` quarters after the spend (fractional lags blend two quarters). Until then it is construction
     in progress and is NOT depreciated (the 10-K policy of every name checked: depreciation starts when an asset is placed in service).
  3. Once in service it depreciates straight-line over the class's useful life in quarters (half a quarter in the first quarter).
     A life can change by vintage (e.g. GOOGL servers 4y before 2021, 6y after), the way the filings disclose life changes.
  4. Assets placed before the capex history starts (`legacy`) run off linearly from a level fitted to the first reported year.
  5. Lags, class shares and lives inside the disclosed ranges are FIT to the filed depreciation history (and, when the filings tag it,
     the construction-in-progress balance). The fit reports the best version and the range across the near-best versions.

The forecast then runs the set's own capex path (the model's capex driver/constant, or capex_pct_rev x revenue) through the fitted book,
so a capex move moves depreciation with the right delay. Nothing here is a ratio to revenue or a calibrated scale factor.

  python3 dep_engine.py history SYM            filed quarterly capex, depreciation and CIP (SEC companyfacts + 10-K/10-Q instances)
  python3 dep_engine.py fit SYM [--out DIR]    fit the book from data/dep_specs.json[SYM]; forecast on the live set's capex;
                                               writes DIR/dep_fit.json (default driver_models/SYM/dep_1117/)
  python3 dep_engine.py compare SYM            the set's current D&A by fiscal year vs the rebuild (best, low, high)

data/dep_specs.json, per name: {dep: [concepts summed] | {"total": [...], "less": [...]}, capex: [concepts summed], start_year,
  classes: [{name, share: [grid] | null (= remainder), lag: [grid, quarters], life: [grid, quarters] | {"else": [grid of the first life], "changes": [[label, new life], ...]}}],
  (a change of estimate applies prospectively: the remaining book value of assets in service spreads over the new remaining life),
  legacy_life: [grid, quarters], fit_from: "YYYY-Qn" (first reported quarter the fit scores), cip: true|false, note}
"""
import copy, datetime as dt, importlib.util, itertools, json, math, os, re, sys

HERE = os.path.dirname(os.path.abspath(__file__)).replace('/mnt/attach/project-files', '/mnt/project-files')
sys.path.insert(0, HERE)
SPECS = os.path.join(HERE, 'data', 'dep_specs.json')
J = lambda p: json.load(open(p))


def _load(name):  # load helpers by path (11-10 Actions import clash)
    spec = importlib.util.spec_from_file_location(name, os.path.join(HERE, name + '.py')); m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m); return m


# ---------------------------------------------------------------- filed history
def _days(s, e): return (dt.date.fromisoformat(e) - dt.date.fromisoformat(s)).days


def _qkey(end):
    """Calendar-quarter key of a period end (fiscal quarters ending within 3 weeks of a calendar quarter end map to it)."""
    d = dt.date.fromisoformat(end) + dt.timedelta(days=20)
    q = (d.month - 1) // 3  # quarter the shifted date falls in; the period ended in the quarter before
    y = d.year
    if q == 0: return f'{y - 1}Q4'
    return f'{y}Q{q}'


def quarterly(cf, names):
    """Discrete quarterly flows {calendar quarter: value} for the summed concepts. Three-month facts are used directly; a quarter
    filed only inside a year-to-date figure is the YTD less the YTD to the quarter before (Q4 = year - nine months)."""
    tot = {}
    for n in names:
        if '|' in n:  # alternatives: per quarter, the first concept that has it (a company that changed tags)
            alts = [quarterly(cf, [a]) for a in n.split('|')]; merged = {}
            for al in alts:
                for k, v in al.items(): merged.setdefault(k, v)
            for k, v in merged.items(): tot[k] = tot.get(k, 0) + v
            continue
        es = []
        for tax in ('us-gaap', 'ifrs-full'):
            c = cf.get('facts', {}).get(tax, {}).get(n)
            if c: es += [e for u in c['units'].values() for e in u if e.get('start') and e.get('form', '').startswith(('10-K', '10-Q', '20-F', '40-F'))]
        if not es: continue
        best = {}
        for e in sorted(es, key=lambda e: e['filed']): best[(e['start'], e['end'])] = e['val']
        q, ytd = {}, {}
        for (s, e), v in best.items():
            d = _days(s, e)
            if 80 <= d <= 100: q[_qkey(e)] = v
            elif d > 100: ytd[(s, e)] = v
        # YTD differences: for each YTD (s, e) look for a shorter YTD with the same start ending ~a quarter before
        for (s, e), v in ytd.items():
            k = _qkey(e)
            if k in q: continue
            prev = [(e2, v2) for (s2, e2), v2 in list(ytd.items()) + [((s3, e3), v3) for (s3, e3), v3 in best.items() if 80 <= _days(s3, e3) <= 100]
                    if s2 == s and 70 <= _days(e2, e) <= 105]
            if prev: q[k] = v - prev[0][1]
            elif 330 <= _days(s, e) <= 400:
                pass
        # annual-only years (old filings): spread evenly over the year's quarters that are still missing
        for (s, e), v in best.items():
            if 330 <= _days(s, e) <= 400:
                y_end = _qkey(e); y, qq = int(y_end[:4]), int(y_end[-1])
                ks = []
                for i in range(4):
                    m = qq - i; yy = y
                    while m <= 0: m += 4; yy -= 1
                    ks.append(f'{yy}Q{m}')
                have = sum(q[k] for k in ks if k in q); miss = [k for k in ks if k not in q]
                for k in miss: q[k] = (v - have) / len(miss)
        for k, v in q.items(): tot[k] = tot.get(k, 0) + v
    return dict(sorted(tot.items()))


def cip_history(sym, n_filings=14):
    """Construction in progress at each balance-sheet date {calendar quarter: $}: ConstructionInProgressGross, or the PP&E gross
    fact on a construction-in-progress member of the PP&E type axis (dimensional, so read from the 10-K/10-Q instances)."""
    xa = _load('xbrl_actuals'); out = {}
    try: fl = xa.filings(sym, n_filings)
    except Exception: return out
    for f in reversed(fl):
        try: fs = xa.facts(f)
        except Exception: continue
        for c, s, e, dims, v, u in fs:
            if s is not None: continue
            cl = c.split(':')[-1]
            mem = ' '.join(m for _, m in dims)
            if cl == 'ConstructionInProgressGross' and not dims: out[_qkey(e)] = v
            elif cl in ('PropertyPlantAndEquipmentGross', 'PropertyPlantAndEquipmentNet') and len(dims) == 1 and re.search(r'ConstructionInProgress', mem, re.I):
                out.setdefault(_qkey(e), v)
    return dict(sorted(out.items()))


def history(sym, spec=None):
    sec = _load('secfacts'); spec = spec or J(SPECS)[sym]
    _, cik = sec.cik_of(sym); cf = sec.companyfacts(cik)
    dspec = spec.get('dep') or ['DepreciationDepletionAndAmortization']
    if isinstance(dspec, dict):
        dep = quarterly(cf, dspec['total']); less = quarterly(cf, dspec.get('less', []))
        dep = {k: v - less.get(k, 0) for k, v in dep.items()}
    else: dep = quarterly(cf, dspec)
    cap = quarterly(cf, spec.get('capex') or ['PaymentsToAcquirePropertyPlantAndEquipment'])
    cip = cip_history(sym) if spec.get('cip', True) else {}
    for k, v in (spec.get('cip_manual') or {}).items(): cip[k] = v  # cited balances the instances do not tag
    return dict(dep=dep, capex=cap, cip=cip)


# ---------------------------------------------------------------- the book
def _lagged(cap, L, t):
    lo = int(math.floor(L)); fr = L - lo
    a = cap[t - lo] if 0 <= t - lo < len(cap) else 0.0
    b = cap[t - lo - 1] if 0 <= t - lo - 1 < len(cap) else 0.0
    return (1 - fr) * a + fr * b


def book(cap, labels, classes, legacy0=0.0, legacy_life=0):
    """cap: capex per quarter (history then forecast); classes: [{share, lag, life (int quarters) or life_fn(label)}].
    Returns dep, placed, cip (spend not yet in service, from the start of the series) per quarter."""
    n = len(cap); dep = [0.0] * n; placed = [0.0] * n
    for c in classes:
        life_of = c['life'] if callable(c['life']) else (lambda lab, L=c['life']: L)
        chg = [(labels.index(b), x) for b, x in (c.get('changes') or []) if b in labels]  # prospective life changes
        ps = [c['share'] * _lagged(cap, c['lag'], t) for t in range(n)]
        for t in range(n): placed[t] += ps[t]
        for v in range(n):
            if not ps[v]: continue
            L = life_of(labels[v])
            for ti, x in chg:
                if ti <= v: L = x
            L = max(1, int(round(L))); nbv = ps[v]; rate = ps[v] / L; end = v + L; t = v
            while t < n and nbv > 1e-12:
                for ti, x in chg:  # a change of estimate: the remaining book value over the new remaining life (10-K practice)
                    if ti == t and ti > v:
                        rem = max(1, int(round(x)) - (t - v)); rate = nbv / rem; end = t + rem
                h = 0.5 if t == v else 1.0
                d = min(nbv, rate * h) if t < end else nbv
                dep[t] += d; nbv -= d; t += 1
    if legacy0 and legacy_life:
        for t in range(min(n, int(legacy_life))): dep[t] += legacy0 * (1 - t / legacy_life)
    cip, run = [], 0.0
    for t in range(n): run += cap[t] - placed[t]; cip.append(run)
    return dep, placed, cip


def _labels(a, b):
    y, q = int(a[:4]), int(a[-1]); out = []
    while True:
        lab = f'{y}Q{q}'; out.append(lab)
        if lab == b: return out
        q += 1
        if q == 5: q = 1; y += 1


def _class_grid(spec):
    """Every combination of the classes' grids (the last class with share null takes the remainder)."""
    axes = []
    for c in spec['classes']:
        lifes = c['life'] if isinstance(c['life'], list) else c['life'].get('else', [None])
        axes.append([(s, l, L) for s in (c.get('share') or [None]) for l in c['lag'] for L in lifes])
    for combo in itertools.product(*axes):
        fixed = sum(s for s, _, _ in combo if s is not None)
        if fixed > 1 + 1e-9: continue
        out = []
        for c, (s, l, L) in zip(spec['classes'], combo):
            sh = s if s is not None else 1 - fixed
            if sh < -1e-9: break
            out.append(dict(name=c['name'], share=sh, lag=l, life=L, life_q=L, changes=(c['life'].get('changes') if isinstance(c['life'], dict) else None)))
        else: yield out


def fit(sym, spec, hist, fcst_capex):
    """fcst_capex: {quarter label: capex} for the forecast quarters (labels continue the filed ones). Returns the fit."""
    capq = dict(hist['capex']); capq.update({k: v for k, v in fcst_capex.items() if k not in capq})
    start = f"{spec.get('start_year', min(int(k[:4]) for k in capq))}Q1"
    labels = _labels(start, max(capq)); idx = {l: i for i, l in enumerate(labels)}
    cap = [capq.get(l, 0.0) for l in labels]
    fit_from = spec.get('fit_from', '2023Q1'); last_actual = max(hist['dep'])
    tq = [l for l in labels if fit_from <= l <= last_actual and l in hist['dep']]
    cq = [l for l in hist['cip'] if l in idx and l >= fit_from] if spec.get('cip', True) else []
    # CIP the engine can see starts at zero at the series start; compare changes from the first tagged balance
    c0 = min(cq) if cq else None
    res = []
    for cls in _class_grid(spec):
        for LL in spec.get('legacy_life', [40]):
            dep0, placed, cip = book(cap, labels, cls)
            # legacy level: the first scored year's gap, run off linearly over LL quarters from the series start
            y0 = tq[:4]; gap = sum(hist['dep'][l] - dep0[idx[l]] for l in y0) / max(len(y0), 1)
            t0 = idx[y0[0]] if y0 else 0; left = max(1e-9, 1 - (t0 + 1.5) / LL)
            leg0 = max(0.0, gap / left) if t0 + 1.5 < LL else 0.0
            dep, _, _ = book(cap, labels, cls, leg0, LL)
            e = sum(((dep[idx[l]] - hist['dep'][l]) / hist['dep'][l]) ** 2 for l in tq) / max(len(tq), 1)
            if cq and len(cq) > 1:
                w = spec.get('cip_weight', 0.5)
                e += w * sum((((cip[idx[l]] - cip[idx[c0]]) - (hist['cip'][l] - hist['cip'][c0])) / max(hist['cip'][l], 1)) ** 2 for l in cq if l != c0) / (len(cq) - 1)
            res.append((e, cls, LL, leg0, dep, cip))
    res.sort(key=lambda r: r[0])
    best = res[0]; near = [r for r in res if r[0] <= max(best[0] * 1.5, best[0] + 2.5e-4)][:spec.get('near', 12)]
    fq = [l for l in labels if l > last_actual]
    def desc(r):
        return dict(err_rms=round(math.sqrt(r[0]), 4), legacy_life_q=r[2], legacy_q0=round(r[3], 2),
                    classes=[dict(name=c['name'], share=round(c['share'], 3), lag_q=c['lag'], life_q=c['life_q'], **({'life_changes': c['changes']} if c.get('changes') else {})) for c in r[1]])
    fitrows = {l: dict(filed=hist['dep'][l], model=round(best[4][idx[l]], 2)) for l in tq}
    out = dict(symbol=sym, method='dep_engine.py (vintage, placed in service, straight-line by class, CIP not depreciated)',
               best=desc(best), near=[desc(r) for r in near], fit=fitrows,
               cip_fit={l: dict(filed=hist['cip'][l], model_change=round(best[5][idx[l]] - best[5][idx[c0]], 1), filed_change=round(hist['cip'][l] - hist['cip'][c0], 1)) for l in cq} if cq else {},
               forecast={l: dict(best=round(best[4][idx[l]], 3), low=round(min(r[4][idx[l]] for r in near), 3), high=round(max(r[4][idx[l]] for r in near), 3),
                                 capex=round(cap[idx[l]], 3)) for l in fq},
               last_actual=last_actual, spec=spec)
    return out


# ---------------------------------------------------------------- the live set
def live(sym):
    """(path, model_inputs) of the name's newest research folder."""
    R = os.path.join(HERE, 'runs'); best = None
    for d in sorted(os.listdir(R)):
        p = os.path.join(R, d, sym, 'supervisor.json')
        if re.match(r'\d{4}-\d\d-\d\d$', d) and os.path.exists(p): best = p
    return best, J(best)['model']['reconciled_inputs']


def set_quarters(mi):
    """The set's quarter rows (engine): [{label, fy, actual, revenue, ebit, da, capex}], or annual rows split evenly."""
    me = _load('model_engine'); m = me.resolve_drivers(copy.deepcopy(mi)); rows = me.project(m); qr = me.quarter_rows(m, rows)
    if qr: return [dict(label=q['label'], end=q['end'], fy=q['fy'], actual=q['actual'], revenue=q['revenue'], ebit=q['ebit'], da=q['da'], capex=q['capex']) for q in qr]
    out = []
    for r in rows:
        e = dt.date.fromisoformat(r['end'])
        for k in range(4):
            qe = (e - dt.timedelta(days=round(91.3 * (3 - k)))).isoformat()
            out.append(dict(label=f"{r['label']} q{k + 1}", end=qe, fy=r['label'], actual=False, revenue=r['revenue'] / 4, ebit=r['ebit'] / 4, da=r['da'] / 4, capex=r['capex'] / 4))
    return out


def forecast_capex(sym, mi, scale):
    """{calendar quarter: capex in the history's units} for the set's forecast quarters (scale: set units -> $)."""
    return {_qkey(q['end']): q['capex'] * scale for q in set_quarters(mi) if not q['actual']}


def compare(sym, res, mi, scale):
    """Per fiscal year: the set's D&A vs the rebuild (set units)."""
    qs = set_quarters(mi); fy = {}
    for q in qs:
        k = _qkey(q['end']); f = res['forecast'].get(k)
        if q['actual'] or not f: continue
        d = fy.setdefault(q['fy'], dict(set=0.0, best=0.0, low=0.0, high=0.0, n=0))
        d['set'] += q['da']; d['best'] += f['best'] / scale; d['low'] += f['low'] / scale; d['high'] += f['high'] / scale; d['n'] += 1
    return {k: {kk: round(vv, 1) if isinstance(vv, float) else vv for kk, vv in v.items()} for k, v in fy.items()}


def main(a):
    if not a: print(__doc__); return
    cmd, sym = a[0], a[1]; spec = J(SPECS)[sym]; scale = spec.get('set_scale', 1e6)
    if cmd == 'history':
        h = history(sym, spec)
        for k in ('capex', 'dep'): print(k, {q: round(v / scale, 1) for q, v in list(h[k].items())[-14:]})
        print('cip', {q: round(v / scale, 1) for q, v in h['cip'].items()}); return
    h = history(sym, spec); p, mi = live(sym)
    res = fit(sym, spec, h, forecast_capex(sym, mi, scale))
    res['set_path'] = p; res['compare_fy'] = compare(sym, res, mi, scale); res['units_note'] = f'$ in the filings; compare_fy in set units (1 = {scale:g} $)'
    out = a[a.index('--out') + 1] if '--out' in a else os.path.join(HERE, 'driver_models', sym, 'dep_1117')
    os.makedirs(out, exist_ok=True); json.dump(res, open(os.path.join(out, 'dep_fit.json'), 'w'), indent=1, default=str)
    print(json.dumps(dict(best=res['best'], compare_fy=res['compare_fy'], n_near=len(res['near'])), indent=1))


if __name__ == '__main__': main(sys.argv[1:])
