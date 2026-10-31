"""Driver layer for the standard model (live for held names since 2026-10-22 ~20:10Z card "Yes, all 17", narrowed by Kilian 20:11Z "only do this for held names"; scratch first, Kilian 2026-10-22 19:30Z: "do that for the existing ones now and check how much
the valuation changes"). A set names 4-6 key drivers and the line items are computed from them, so changing a driver
re-computes the model. model_engine.value() resolves a set's `drivers` into plain model_inputs (segment_build / years / any path) before
every valuation, scenario and sensitivity; a set without drivers values exactly as before.

Set format (inside model_inputs):
  drivers: [{id, label, unit, base, years: [y1, y2, y3], basis: filing | primary | consensus | estimate, source,
             derivation (estimate: how the number was built), guidance_check (when the path leans on a guide), why}]
  driver_build: {
    segments: {SEGMENT NAME: {revenue: EXPR, ebit_margin: EXPR | ebit: EXPR}}  # name must match segment_build;
              # a segment not in segment_build is added (base_revenue = EXPR at the base)
    years:    {FIELD: EXPR}   # per-year fields of `years` (ebit_margin, capex_pct_rev, ...); level forms revenue, ebit,
                              # capex, da are turned into growth / ratios of the year's revenue
    set:      {PATH: EXPR}    # any number in the set, e.g. "custom[name=X].value", "sotp.parts[name=Y].metric",
                              # "adjustments[name=Z].value"; uses id_y1 / id_y2 / id_y3 / id_base names
  }
  scenarios.*.deltas.drivers: {id: {"pct": x} | {"add": x}} moves a driver in every projected year.
EXPR: arithmetic on driver ids at that year, p_ID = the prior year (the base for year 1), rev = the year's total revenue
(years and set only), seg_NAME (spaces as _) = a driven segment's revenue that year; min, max, abs, sqrt, log, exp.
Segment and total revenue keep the reported base: year growth = EXPR(year) / EXPR(prior year) - 1, so the base value of
the drivers only has to reproduce the reported base (the residual is reported as base_residual).

Quarterly sets (Kilian 2026-10-29 00:42Z "change driver models to quarterly"): the model is built quarter by quarter and the
fiscal years are the sums of their quarters. A set without `quarters` values exactly as before.
  quarters: {labels: [4 x (N + 1) labels, the base year's four quarters first, e.g. "Q1 FY2025"], ends: [quarter-end dates],
             actual: number of leading quarters that are reported (the base year's 4 plus this year's filed quarters)}
  drivers[].quarters: one value per label (reported values for the actual quarters, from filings / XBRL); drivers[].agg:
             sum (flows, e.g. capex $B) | end (stocks, e.g. backlog) | mean (rates, prices, indices; the default) |
             wmean:ID or wmean:EXPR (a rate weighted by another quarterly driver or a product of them, e.g. a segment margin
             by its revenue, a take rate by rn*gbrn). The driver's
             base and years are then the aggregates of its quarters (stated years that differ are reported as quarter_vs_years).
             Scenario deltas.drivers move the forecast quarters only (an `add` is in annual units: a flow takes add / 4 a quarter).
  driver_build.quarters: the quarter line items, evaluated for every forecast quarter:
    segments: {SEGMENT NAME: {revenue: EXPR, ebit: EXPR | ebit_margin: EXPR}}   # every segment of segment_build
    revenue / ebit / ebit_margin: EXPR                                         # sets without a segment_build
    corporate_ebit, da, capex, sbc: EXPR (levels for the quarter; optional: without da / capex / sbc the year's ratio applies)
    tax_rate: EXPR (optional): the quarter's tax rate for its NOPAT and model EPS (e.g. a quarter guide when the year's rate
              carries a one-off); the year's own tax_rate still sets the fiscal-year rows
    reported: {LABEL: {revenue, ebit, segments: {NAME: {revenue, ebit}}, corporate_ebit, da, capex, sbc, tax_rate, eps}} for every actual
              quarter of the projected years (filings); the model's fiscal year = reported quarters + forecast quarters.
    constants: {NAME: [one value per label]} cited per-quarter inputs that are not drivers (scenario moves do not touch them)
  Quarter EXPR names: driver ids at the quarter, p_ID = the quarter before, ly_ID = the same quarter a year before, q = fiscal
  quarter number (1-4), fy = model year (1-3; 0 = the base year), constants by name, seg_NAME = a segment's revenue that quarter, rev = the quarter's revenue (after the segments).
  Each year's revenue growth, EBIT margin (segment growth and margins, corporate_ebit), and D&A / capex / SBC ratios when given
  are set from the quarter sums, after driver_build.years and .set (the quarters win). The engine reads the quarter rows for the
  NTM / LTM metrics at the horizon and the cash roll to it (model_engine.quarter_rows)."""
import copy, math, re

class _Err(Exception): pass
class _ME:  # model_engine sets ModelError on import (the engine may run as __main__)
    ModelError = _Err
me = _ME

FUN = dict(min=min, max=max, abs=abs, sqrt=math.sqrt, log=math.log, exp=math.exp)

def _key(s): return re.sub(r'\W', '_', s)

def _vals(drv, dd):
    """{id: [base, y1, y2, ...]} after scenario / sensitivity moves (projected years only)."""
    out = {}
    for x in drv:
        v = [x['base']] + list(x['years']); m = (dd or {}).get(x['id'])
        if m:
            v = [v[0]] + [y * (1 + m.get('pct', 0)) + m.get('add', 0) for y in v[1:]]
        out[x['id']] = v
    return out

def _ev(expr, ns, where):
    try: return float(eval(expr, {'__builtins__': {}}, {**FUN, **ns}))
    except Exception as e: raise me.ModelError(f'driver_build {where}: {expr!r}: {e}')

def _ns(V, i, extra=None):
    ns = {k: v[min(i, len(v) - 1)] for k, v in V.items()}
    ns.update({f'p_{k}': v[max(i - 1, 0)] for k, v in V.items()})
    ns.update(extra or {}); return ns

def _path_set(mi, path, val):
    cur = mi; parts = re.findall(r'([^.\[\]]+)(\[[^\]]+\])?', path)
    for j, (k, sel) in enumerate(parts):
        last = j == len(parts) - 1
        if sel:
            lst = cur[k]; sel = sel[1:-1]
            if '=' in sel:
                f, v = sel.split('=', 1); hit = [x for x in lst if str(x.get(f)) == v]
                if len(hit) != 1: raise me.ModelError(f'driver_build.set {path}: {len(hit)} matches for {sel}')
                cur = hit[0]
            else: cur = lst[int(sel)]
            if last: raise me.ModelError(f'driver_build.set {path}: ends on a list item')
        elif last: cur[k] = val
        else: cur = cur[k]

def _agg(v, how, w=None):
    if how == 'sum': return sum(v)
    if how == 'end': return v[-1]
    if w is not None and abs(sum(w)) > 1e-12: return sum(a * b for a, b in zip(v, w)) / sum(w)
    return sum(v) / len(v)

def _quarter_drivers(mi, dd, rep):
    """Drivers with quarters: scenario moves on the forecast quarters, base and years from the quarter aggregates.
    Returns ({id: quarter values}, the moves left for the annual drivers)."""
    Q = mi.get('quarters'); drv = mi['drivers']; QV = {}
    if not Q or not any(x.get('quarters') for x in drv): return QV, dd
    n = len(mi['years']); nq = 4 * (n + 1); na = int(Q.get('actual', 4)); rest = dict(dd or {})
    if len(Q.get('labels') or []) != nq or len(Q.get('ends') or []) != nq:
        raise me.ModelError(f'quarters: {nq} labels and ends wanted (the base year and {n} model years)')
    for x in drv:
        q = x.get('quarters')
        if not q: continue
        if len(q) != nq or any(v is None for v in q): raise me.ModelError(f"driver {x['id']}: {len(q)} quarter values for {nq} quarters")
        q = [float(v) for v in q]; m = rest.pop(x['id'], None); how = x.get('agg', 'mean')
        if m:  # an `add` is in the driver's annual units: a quarter of it per quarter for a flow (agg sum)
            ad = m.get('add', 0) / (4 if how == 'sum' else 1); q = q[:na] + [v * (1 + m.get('pct', 0)) + ad for v in q[na:]]
        QV[x['id']] = q
    for x in drv:
        if x['id'] not in QV: continue
        q = QV[x['id']]; how = x.get('agg', 'mean'); m = (dd or {}).get(x['id'])
        wid = how.split(':', 1)[1] if how.startswith('wmean:') else None
        w = None
        if wid:  # weight = a quarterly driver, or an EXPR of them (e.g. rn*gbrn = gross bookings)
            C = ((mi.get('driver_build') or {}).get('quarters') or {}).get('constants') or {}
            w = QV.get(wid) or [_ev(wid, {**{k: float(v[j]) for k, v in C.items()}, **{k: v[j] for k, v in QV.items()}}, f"driver {x['id']} agg {how}") for j in range(len(q))]
        yrs = [_agg(q[4 * i:4 * i + 4], 'mean' if wid else how, w[4 * i:4 * i + 4] if w else None) for i in range(n + 1)]
        old = [x.get('base')] + list(x.get('years') or [])
        if not m and any(o is None or abs(o - v) > 5e-3 * max(abs(v), 1e-9) for o, v in zip(old, yrs)):
            rep.setdefault('quarter_vs_years', {})[x['id']] = dict(stated=old, from_quarters=[round(v, 6) for v in yrs])
        x['base'], x['years'] = yrs[0], yrs[1:]
    return QV, rest

def _quarter_build(mi, b, QV, rep):
    """Quarter line items (forecast quarters from driver_build.quarters, actual quarters from its `reported`); sets each year's
    growth, margins and ratios from the quarter sums and stores the rows in mi['_quarters'] for the engine."""
    qb = b.get('quarters'); Q = mi['quarters']; n = len(mi['years']); na = int(Q.get('actual', 4)); L = Q['labels']
    segs = mi.get('segment_build'); segs = segs['segments'] if isinstance(segs, dict) else segs
    qseg = qb.get('segments') or {}
    if segs:
        miss = [s['name'] for s in segs if s['name'] not in qseg]
        if miss: raise me.ModelError(f'driver_build.quarters.segments lacks {miss} (every segment of segment_build is built by quarter)')
    opt = [k for k in ('da', 'capex', 'sbc') if k in qb]; rows = []
    for j in range(4, 4 * (n + 1)):
        lab = L[j]
        if j < na:
            r = (qb.get('reported') or {}).get(lab)
            if not r: raise me.ModelError(f'driver_build.quarters.reported has no {lab} (an actual quarter)')
            sg = r.get('segments') or {}
            if segs and any(nm not in sg for nm in qseg): raise me.ModelError(f'reported {lab}: segments {sorted(qseg)} wanted')
            row = dict(label=lab, end=Q['ends'][j], actual=True, revenue=float(r['revenue']), ebit=float(r['ebit']),
                       segments={nm: dict(revenue=float(sg[nm]['revenue']), ebit=float(sg[nm]['ebit'])) for nm in qseg if nm in sg})
            for k in opt:
                if r.get(k) is None: raise me.ModelError(f'reported {lab}: no {k} (driver_build.quarters builds {k})')
                row[k] = float(r[k])
            for k in ('tax_rate', 'eps'):  # optional: the quarter's reported effective tax rate and diluted EPS
                if r.get(k) is not None: row[k] = float(r[k])
        else:
            ns = {k: v[j] for k, v in QV.items()}; ns.update({f'p_{k}': v[j - 1] for k, v in QV.items()})
            ns.update({f'ly_{k}': v[j - 4] for k, v in QV.items()}); ns['q'] = j % 4 + 1; ns['fy'] = j // 4
            for k, v in (qb.get('constants') or {}).items(): ns[k] = float(v[j])  # cited per-quarter constants (not drivers)
            sg = {}
            for nm, f in qseg.items():
                rv = _ev(f['revenue'], ns, f'quarters.{nm}.revenue {lab}')
                eb = _ev(f['ebit'], ns, f'quarters.{nm}.ebit {lab}') if 'ebit' in f else rv * _ev(f['ebit_margin'], ns, f'quarters.{nm}.ebit_margin {lab}')
                sg[nm] = dict(revenue=rv, ebit=eb); ns[f'seg_{_key(nm)}'] = rv
            if qseg and not segs and 'revenue' not in qb: rv = sum(x['revenue'] for x in sg.values())
            elif qseg and segs: rv = sum(x['revenue'] for x in sg.values())
            else: rv = _ev(qb['revenue'], ns, f'quarters.revenue {lab}')
            ns['rev'] = rv
            ce = _ev(qb['corporate_ebit'], ns, f'quarters.corporate_ebit {lab}') if 'corporate_ebit' in qb else 0.0
            if sg and ('ebit' not in qb and 'ebit_margin' not in qb): eb = sum(x['ebit'] for x in sg.values()) + ce
            elif 'ebit' in qb: eb = _ev(qb['ebit'], ns, f'quarters.ebit {lab}')
            else: eb = rv * _ev(qb['ebit_margin'], ns, f'quarters.ebit_margin {lab}')
            row = dict(label=lab, end=Q['ends'][j], actual=False, revenue=rv, ebit=eb, segments=sg, corporate_ebit=ce)
            for k in opt: row[k] = _ev(qb[k], ns, f'quarters.{k} {lab}')
            if 'tax_rate' in qb: row['tax_rate'] = _ev(qb['tax_rate'], ns, f'quarters.tax_rate {lab}')
        rows.append(row)
    prev_r = mi['base']['revenue']; prev_s = {s['name']: s['base_revenue'] for s in segs or []}; fy = []
    for i in range(n):
        qs = rows[4 * i:4 * i + 4]; R = sum(x['revenue'] for x in qs); E = sum(x['ebit'] for x in qs)
        y = mi['years'][i] = dict(mi['years'][i])
        if segs:
            se = 0.0
            for s in segs:
                nm = s['name']; sr = sum(x['segments'][nm]['revenue'] for x in qs); sv = sum(x['segments'][nm]['ebit'] for x in qs); se += sv
                s['years'] = [dict(t) for t in (s.get('years') or [])] + [{} for _ in range(n - len(s.get('years') or []))]
                s['years'][i] = dict(s['years'][i], revenue_growth=sr / prev_s[nm] - 1, ebit_margin=sv / sr if sr else 0.0); prev_s[nm] = sr
            y['corporate_ebit'] = E - se
        else:
            y['revenue_growth'] = R / prev_r - 1; y['ebit_margin'] = E / R
        for k in opt: y[{'da': 'da_pct_rev', 'capex': 'capex_pct_rev', 'sbc': 'sbc_pct_rev'}[k]] = sum(x[k] for x in qs) / R
        prev_r = R; fy.append(dict(label=y.get('label'), revenue=round(R, 2), ebit=round(E, 2), actual_quarters=sum(x['actual'] for x in qs)))
    if segs is not None and isinstance(mi.get('segment_build'), dict): mi['segment_build'] = dict(mi['segment_build'], segments=segs)
    mi['_quarters'] = dict(rows=rows, start=Q['ends'][3]); rep['quarter_years'] = fy

def resolve(mi, dd=None, report=None):
    drv = mi.get('drivers'); b = mi.get('driver_build')
    if not drv or not b: return mi
    mi = copy.deepcopy(mi); rep = report if report is not None else {}
    QV, dd = _quarter_drivers(mi, dd, rep); drv = mi['drivers']
    if b.get('quarters') and not QV: raise me.ModelError('driver_build.quarters needs quarters and drivers[].quarters')
    V = _vals(drv, dd); n = len(mi['years'])
    segs = mi.get('segment_build'); segs = segs['segments'] if isinstance(segs, dict) else segs
    if b.get('quarters'):  # the quarter build sets every segment-year; placeholders keep the annual pass from failing first
        for s_ in segs or []:
            s_['years'] = [{'revenue_growth': 0.0, 'ebit_margin': 0.0, **y} for y in (s_.get('years') or [])] + \
                          [{'revenue_growth': 0.0, 'ebit_margin': 0.0} for _ in range(n - len(s_.get('years') or []))]
        if not segs: mi['years'] = [{'revenue_growth': 0.0, 'ebit_margin': 0.0, **y} for y in mi['years']]
    seg_rev = {}  # driven segment revenue levels by year (model scale)
    for nm, f in (b.get('segments') or {}).items():
        s = next((x for x in segs or [] if x['name'] == nm), None)
        e0 = _ev(f['revenue'], _ns(V, 0), f'{nm}.revenue') if 'revenue' in f else None
        if s is None:
            if e0 is None: raise me.ModelError(f'driver_build segment {nm} is not in segment_build and has no revenue EXPR')
            s = dict(name=nm, base_revenue=e0, years=[{} for _ in range(n)]); segs.append(s)
            mi['base']['revenue'] += e0
        s['years'] = [dict(y) for y in (s.get('years') or [])] + [{} for _ in range(n - len(s.get('years') or []))]
        if e0 is not None: rep.setdefault('base_residual', {})[nm] = round(e0 / s['base_revenue'] - 1, 4)
        prev_e, lvl = e0, s['base_revenue']; L = []; inh = {}
        for i in range(1, n + 1):
            y = s['years'][i - 1] = {**inh, **s['years'][i - 1]}; inh = y; ns = _ns(V, i)
            if 'revenue' in f:
                e = _ev(f['revenue'], ns, f'{nm}.revenue y{i}'); y['revenue_growth'] = e / prev_e - 1; prev_e = e
            lvl *= 1 + y['revenue_growth']; L.append(lvl)
            if 'ebit_margin' in f: y['ebit_margin'] = _ev(f['ebit_margin'], ns, f'{nm}.ebit_margin y{i}')
            elif 'ebit' in f: y['ebit_margin'] = _ev(f['ebit'], ns, f'{nm}.ebit y{i}') / (e if 'revenue' in f else lvl)
        seg_rev[_key(nm)] = L
    if segs is not None and isinstance(mi.get('segment_build'), list): mi['segment_build'] = segs
    tot = None
    if segs:
        tot = [sum(_seg_level(s, i) for s in segs) for i in range(1, n + 1)]
        cov = sum(seg_rev[k][0] for k in seg_rev) / tot[0] if seg_rev else 0.0; rep['driven_revenue_share_y1'] = round(cov, 3)
    yf = b.get('years') or {}; prev_r = mi['base']['revenue']; prev_e = None
    if segs and ({'revenue', 'revenue_growth', 'ebit', 'ebit_margin'} & set(yf)):
        raise me.ModelError('driver_build.years drives revenue/EBIT but the set has a segment_build (the engine sums the segments): drive the segments, or drop segment_build')
    if 'revenue' in yf: prev_e = _ev(yf['revenue'], _ns(V, 0, dict(rev=prev_r)), 'years.revenue base'); rep['base_residual_total'] = round(prev_e / prev_r - 1, 4)
    for i in range(1, n + 1):
        y = mi['years'][i - 1] = dict(mi['years'][i - 1]); ns = _ns(V, i, {f'seg_{k}': v[i - 1] for k, v in seg_rev.items()})
        if 'revenue' in yf:
            e = _ev(yf['revenue'], ns, f'years.revenue y{i}'); y['revenue_growth'] = e / prev_e - 1; prev_e = e
        r = tot[i - 1] if tot else prev_r * (1 + y['revenue_growth']); ns['rev'] = r
        for k, ex in yf.items():
            if k == 'revenue': continue
            v = _ev(ex, ns, f'years.{k} y{i}')
            if k in ('ebit', 'capex', 'da'): y[{'ebit': 'ebit_margin', 'capex': 'capex_pct_rev', 'da': 'da_pct_rev'}[k]] = v / r
            else: y[k] = v
        prev_r = r
    flat = {}
    for k, v in V.items():
        flat[f'{k}_base'] = v[0]
        for i in range(1, len(v)): flat[f'{k}_y{i}'] = v[i]
    for p, ex in (b.get('set') or {}).items(): _path_set(mi, p, _ev(ex, flat, f'set {p}'))
    if b.get('quarters'): _quarter_build(mi, b, QV, rep)
    return mi

def _seg_level(s, i):
    lvl = s['base_revenue']; last = {}
    for j in range(i):
        y = {**last, **((s.get('years') or [{}])[j] if j < len(s.get('years') or []) else {})}; last = y
        lvl *= 1 + y['revenue_growth']
    return lvl

def check(mi):
    """Flags for the driver set itself (the gate checks the citations)."""
    fl = []; drv = mi.get('drivers') or []
    if not 4 <= len(drv) <= 6: fl.append(f'{len(drv)} drivers (want 4-6)')
    for x in drv:
        if x.get('basis') not in ('filing', 'primary', 'consensus', 'estimate'): fl.append(f"{x['id']}: basis {x.get('basis')!r}")
        if not x.get('source'): fl.append(f"{x['id']}: no source")
        if x.get('basis') == 'estimate' and not x.get('derivation'): fl.append(f"{x['id']}: estimate without derivation")
        if len(x.get('years') or []) < len(mi['years']): fl.append(f"{x['id']}: {len(x.get('years') or [])} years for {len(mi['years'])} model years")
        if x.get('flag'): fl.append(f"{x['id']}: FLAG {x['flag']}")
        if mi.get('quarters') and not x.get('quarters'): fl.append(f"{x['id']}: no quarters (the set is quarterly)")
        if x.get('quarters') and x.get('agg', 'mean') not in ('sum', 'end', 'mean') and not str(x.get('agg')).startswith('wmean:'): fl.append(f"{x['id']}: agg {x.get('agg')!r}")
    if mi.get('quarters') and not (mi.get('driver_build') or {}).get('quarters'): fl.append('quarters without driver_build.quarters (line items stay annual)')
    return fl
