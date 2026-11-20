"""Numeric carry trace for forecast_lint.py (Kilian 2026-11-18, AMZN: "if you're not providing reasons for why you need extra
rounds the gate is not working as intended"; yes to the AMZN thread's two fixes the same minute).

What slipped: AMZN gate rounds 1 and 2 (2026-11-17) passed a booked Q4 FY2026 AWS revenue of $49,663M that was the 10-29 view
"Q3 x 1.08". AWS revenue is not a driver: it is aws_ppe x aws_turn / 4 (driver_build.quarters.segments). Round 1 moved
aws_ppe (326.5 -> 340.7) and re-solved aws_turn so the product stayed $49,663M; the text said "(held)", then "capacity in service".
forecast_lint's carry-num only looked at drivers and constants one at a time, and its wording rules only at wording, so the
number was carried while the reason changed. This module traces the numbers the model books:

  carry-step   the first forecast quarter of a level series (a driver or constant whose values are levels, or a quarter line
               the engine builds: segment revenue / EBIT, revenue, EBIT, D&A, capex, gross profit) = the last actual x a
               round two-decimal factor (x1.08): a hand step off the last quarter (rule 4). Derived lines also get
               forecast_lint's past same-quarter q/q, y/y and held-flat tests.
  carry-held   a quarter line the engine builds sits exactly at its value in a reference round while the drivers in its
               formula moved: inputs re-solved to hold an old output (what AMZN round 1 did).
  carry-base   a forecast quarter (driver, constant or built line) sits exactly at its value in a reference round, and the
               text that quotes that figure is not the reference round's text: an unchanged value with a changed reason.
Reference rounds: the update's base round (update.json `base`, runs/BASE/SYM/supervisor.json) and every earlier citation-gate
round of this update (gate/values_rN.json, saved by name_update.py gate through gate_rounds.snapshot). A value equal to the
reference with the same quoting text is not flagged (nothing bears on it and nothing claims otherwise).

Each hit is a forecast_lint failure (gate, finish, gate-merge as trend_only, buys on rounds from BUY_BLOCK_FROM_1118), passed
only by `forecast_lint.py override D SYM KEY --why` with the sourced economic case (>= 60 chars, src id or URL), logged.

  python3 carry_trace.py D SYM      print the hits for runs/D/SYM (exit 1 if any)
"""
import json, math, os, re, sys

HERE = os.path.dirname(os.path.abspath(__file__)).replace('/mnt/attach/project-files', '/mnt/project-files')
sys.path.insert(0, HERE)
EQ = 1e-6        # "the same value" (relative)
MOVED = 1e-4     # an input "moved" (relative)
STEP_TOL = 5e-6  # x1.08 within this (relative) of the factor (stored drivers carry ~6 significant digits)
LINE_KEYS = ('revenue', 'ebit', 'da', 'capex', 'sbc', 'gross_profit')
LINE_NAMES = dict(revenue='revenue', ebit='EBIT', da='D&A', capex='capex', sbc='SBC', gross_profit='gross profit')


def _j(p, d=None):
    try: return json.load(open(p))
    except (OSError, ValueError): return d


def _num(x): return x if isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x) else None


def _same(a, b, tol=EQ): return abs(a - b) <= tol * max(abs(a), abs(b), 1e-12)


# ---------------------------------------------------------------------------------------------------------------- series
def series(RI):
    """{name: dict(labels, values, actual (n), inputs (names in its formula) or None, kind 'driver'|'constant'|'line')}"""
    Q = RI.get('quarters') or {}; L = Q.get('labels') or []; A = Q.get('actual')
    if not isinstance(A, int) or not L: return {}
    out = {}
    ds = RI.get('drivers') or []
    for d in (ds.values() if isinstance(ds, dict) else ds):
        q = d.get('quarters') if isinstance(d, dict) else None
        if isinstance(q, list) and len(q) == len(L) and all(_num(x) is not None for x in q) and (d.get('basis') or 'estimate') != 'reported':
            out[f"driver {d.get('id')}"] = dict(labels=L, values=[float(x) for x in q], actual=A, inputs=None, kind='driver', id=str(d.get('id')))
    db = RI.get('driver_build') if isinstance(RI.get('driver_build'), dict) else {}
    qb = db.get('quarters') if isinstance(db.get('quarters'), dict) else {}
    for k, v in (qb.get('constants') or {}).items():
        if isinstance(v, list) and len(v) == len(L) and all(_num(x) is not None for x in v):
            out[f'constant {k}'] = dict(labels=L, values=[float(x) for x in v], actual=A, inputs=None, kind='constant', id=k)
    out.update(_lines(RI, qb))
    return out


def _idents(expr): return set(re.findall(r'(?<![\w.])(?:p_|ly_)?([A-Za-z_]\w*)', str(expr or '')))


def _lines(RI, qb):
    """Quarter lines the engine builds (model_drivers.resolve -> _quarters.rows): segment revenue / EBIT and the totals."""
    if not qb: return {}
    try:
        import model_drivers as MD
        rows = (MD.resolve(RI, None, {}).get('_quarters') or {}).get('rows') or []
    except Exception:
        return {}
    if not rows or not any(not r.get('actual') for r in rows): return {}
    A = sum(1 for r in rows if r.get('actual')); L = [r['label'] for r in rows]
    if A < 1 or any(rows[i].get('actual') for i in range(A, len(rows))): return {}
    segs = qb.get('segments') or {}
    def ins(*exprs):
        s = set()
        for e in exprs: s |= _idents(e)
        return s
    out = {}
    def put(name, vals, inputs):
        if len(vals) == len(rows) and all(_num(v) is not None for v in vals) and any(abs(v) > 0 for v in vals):
            out[name] = dict(labels=L, values=[float(v) for v in vals], actual=A, inputs=inputs, kind='line', id=name)
    all_in = set()
    for nm, f in segs.items():
        f = f if isinstance(f, dict) else {}
        all_in |= ins(*f.values())
        put(f'line {nm} revenue', [((r.get('segments') or {}).get(nm) or {}).get('revenue') for r in rows], ins(f.get('revenue')))
        put(f'line {nm} EBIT', [((r.get('segments') or {}).get(nm) or {}).get('ebit') for r in rows], ins(f.get('ebit'), f.get('revenue'), f.get('ebit_margin')))
    for k in LINE_KEYS:
        if k in qb or (k in ('revenue', 'ebit') and segs):
            e = qb.get(k) if isinstance(qb.get(k), str) else None
            put(f'line {LINE_NAMES[k]}', [r.get(k) for r in rows], ins(e) if e else set(all_in) | ins(qb.get('revenue'), qb.get('ebit'), qb.get('corporate_ebit')))
    return out


# ---------------------------------------------------------------------------------------------------------------- texts
def texts(RI):
    """[(where, sentence)] of the set's current text (forecast_lint._texts: drivers, inputs, guidance rows, constants notes)."""
    import forecast_lint as fl
    out = []
    for w, t in fl._texts(RI):
        for s in fl._sentences(t): out.append((w, s))
    for g in RI.get('guidance_check') or []:  # drivers_note quotes the built quarter lines (AMZN "AWS $49,663M (...)")
        if isinstance(g, dict) and isinstance(g.get('drivers_note'), str):
            w = f"guidance_check {g.get('metric')} {g.get('fy')} drivers_note"
            if not any(x == w for x, _ in out):
                for s in fl._sentences(fl._current(g['drivers_note'])): out.append((w, s))
    return out


NUM = re.compile(r'(?<![\w.])-?\$?(\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+\.\d+|\d{4,})(?![\d])')
SCALES = (1.0, 1e3, 1e-3, 1e6, 1e-6, 100.0)
PREFIX = re.compile(r'^\s*(?:[A-Z0-9][A-Z0-9 /&+.\'-]{3,}\s+)?\d{4}-\d\d-\d\d(?:\s*\([^)]*\))?\s*:\s*')


def _sig(tok):
    d = tok.replace(',', '').lstrip('-').lstrip('0').replace('.', '').lstrip('0')
    return len(d)


def quotes(s, v):
    """True when sentence s prints value v (at some scale: $M / $B / %, rounded as printed) with >= 4 significant digits."""
    for m in NUM.finditer(s):
        tok = m.group(1);
        if _sig(tok) < 4: continue
        x = float(tok.replace(',', '')); dec = len(tok.split('.')[1]) if '.' in tok else 0; half = 0.5 * 10 ** -dec * 1.0001
        for k in SCALES:
            if abs(abs(v) * k - x) <= half + 1e-9 * x: return True
    return False


def clauses(s):
    """A sentence cut at its top-level joins (; + = |, outside brackets): each figure's own reason ("advertising $24,941M (+17%,
    the release's 'high teens')" stays the same clause when the AWS clause beside it changes)."""
    out, cur, depth, i = [], '', 0, 0
    while i < len(s):
        c = s[i]
        if c in '([': depth += 1
        elif c in ')]': depth = max(0, depth - 1)
        if depth == 0 and (c in ';|' or s[i:i + 3] in (' + ', ' = ')):
            out.append(cur); cur = ''; i += 1 if c in ';|' else 3; continue
        cur += c; i += 1
    return [x for x in out + [cur] if x.strip()]


def reasons(s, v):
    """The clauses of sentence s that print value v, as word sets (the figures themselves and fix labels left out)."""
    return [words(c) for c in clauses(s) if quotes(c, v)]


SIMILAR = 0.6  # a clause is the same reason when its words overlap a reference clause this much (Jaccard)


def words(c):
    c = norm(c); t = set()
    for w in re.findall(r"[a-z_][\w'+/-]*|[-+$]?\d[\d,.]*%?[bmk]?", c):
        if re.match(r'[-+$]?\d', w) and _sig(re.sub(r'[^\d.,]', '', w)) >= 4: continue  # the figure, not its reason
        t.add(w.strip("'"))
    return frozenset(t)


def _sim(a, b): return len(a & b) / len(a | b) if a | b else 1.0


def norm(s):
    """A sentence without its fix-label prefix ("GATE ROUND 3 FIX 2026-11-17:") and spacing: what the reason says."""
    s = PREFIX.sub('', s.strip()); return re.sub(r'\s+', ' ', s).strip().lower()


# ---------------------------------------------------------------------------------------------------------------- checks
def steps(S):
    """carry-step on level series (+ past same-quarter / y/y / flat tests on built lines, which forecast_lint does not see)."""
    import forecast_lint as fl
    out = []
    for name, x in S.items():
        q, A, L = x['values'], x['actual'], x['labels']
        if A < 1 or len(q) <= A or not q[A - 1]: continue
        if x['kind'] == 'line': level = name.endswith(' revenue') and abs(q[A - 1]) > 2  # EBIT / D&A / capex lines are margin outputs
        else: level = all(abs(v) > 2 for v in q) and len(set(round(v, 9) for v in q[:A])) > 1
        if level:
            r = q[A] / q[A - 1]; f = round(r, 2)
            tol = max(STEP_TOL * abs(q[A]), 0.5 * 10 ** (math.floor(math.log10(abs(q[A]))) - 4)) if q[A] else 0  # the view may be rounded to 5 digits
            if f > 0 and abs(f - 1) > 1e-9 and abs(q[A] - q[A - 1] * f) <= tol and abs(r - 1) > 1e-6:
                out.append(dict(rule='carry-step', key=f'carry-step:{name}', where=name, value=f'{L[A]} {q[A]:,.6g} = {L[A - 1]} {q[A - 1]:,.6g} x {f:.2f}',
                                why=f'{L[A]} = the last actual {L[A - 1]} x {f:.2f}, a hand step off the last quarter: build the quarter from its economic '
                                    'drivers (capacity in service, backlog, orders, customers, prices) with their own sources (rule 4, Kilian 2026-11-17)'))
                continue
        if x['kind'] == 'line':
            RI = dict(quarters=dict(labels=L, actual=A), drivers=[dict(id=name, quarters=q)])
            for h in fl.numeric_carry(RI):
                out.append(dict(h, key=f'carry-num:{name}', where=name))
    return out


def refs(D, s):
    """Reference rounds for runs/D/SYM: [(label, series, texts)]: the update's base round and this update's earlier gate rounds."""
    import forecast_lint as fl
    rd = os.path.join(fl.RUNS, D, s); out = []
    u = _j(os.path.join(rd, 'update.json'), {}) or {}
    b = u.get('base')
    if b and str(b) != str(D):
        sp = _j(os.path.join(fl.RUNS, str(b), s, 'supervisor.json'), {}) or {}
        RI = (sp.get('model') or {}).get('reconciled_inputs')
        if isinstance(RI, dict): out.append((f'base round {b}', series(RI), texts(RI)))
    gd = os.path.join(rd, 'gate')
    if os.path.isdir(gd):
        for f in sorted(os.listdir(gd), key=lambda f: int(re.sub(r'\D', '', f) or 0)):
            m = re.match(r'values_r(\d+)\.json$', f)
            if not m: continue
            V = _j(os.path.join(gd, f), {}) or {}
            out.append((f'gate round {m.group(1)} ({V.get("utc", "")})', V.get('series') or {}, [tuple(t) for t in V.get('texts') or []]))
    return out


def holds(S, T, R):
    """carry-held and carry-base against each reference round. One hit per series and rule (the first quarter)."""
    out = []; seen = set()
    for rlab, RS, RT in R:
        for name, x in S.items():
            r = RS.get(name)
            if not r: continue
            Lr = r['labels']; ra = r['actual']
            for i in range(x['actual'], len(x['values'])):
                lab = x['labels'][i]
                if lab not in Lr: continue
                j = Lr.index(lab)
                if j < ra: continue  # an actual in the reference: a carry from it is the forecast_lint checks' job
                v, v0 = x['values'][i], r['values'][j]
                if not _same(v, v0) or abs(v) < 1e-12: continue
                if x['kind'] == 'line' and ('carry-held', name) not in seen and x.get('inputs'):
                    moved = []
                    for k in sorted(x['inputs']):
                        for nm in (f'driver {k}', f'constant {k}'):
                            a, b = S.get(nm), RS.get(nm)
                            if a and b and lab in a['labels'] and lab in b['labels']:
                                aa, bb = a['values'][a['labels'].index(lab)], b['values'][b['labels'].index(lab)]
                                if not _same(aa, bb, MOVED): moved.append(f'{k} {bb:.6g} -> {aa:.6g}')
                    if moved:
                        seen.add(('carry-held', name))
                        out.append(dict(rule='carry-held', key=f'carry-held:{name}:{lab}', where=name, value=f'{lab} {v:,.6g} = {rlab}',
                                        why=f'{lab} {name} stays exactly at its {rlab} value {v0:,.6g} while its inputs moved ({"; ".join(moved)}): the inputs '
                                            'were re-solved to hold an old number. Build the quarter from its economic drivers with their own sources, '
                                            'and let the line land where they put it (rules 4, 5; Kilian 2026-11-18)'))
                if ('carry-base', name) in seen: continue
                was = [r_ for _, rt in RT if quotes(rt, v) for r_ in reasons(rt, v)]
                if not was: continue  # the reference gave this figure no reason in words: nothing to compare
                cur = [(w, t, r_) for w, t in T if quotes(t, v) for r_ in reasons(t, v)]
                if not cur or any(max(_sim(r_, x) for x in was) >= SIMILAR for _, _, r_ in cur): continue  # the old reason still stands
                new = [(w, t) for w, t, _ in cur]
                if new:
                    seen.add(('carry-base', name)); w, t = new[0]
                    out.append(dict(rule='carry-base', key=f'carry-base:{name}:{lab}', where=name, value=f'{lab} {v:,.6g} = {rlab}', sentence=t[:400],
                                    why=f'{lab} {name} is unchanged from {rlab} ({v0:,.6g}) but the text that gives its reason changed ({w}): an unchanged '
                                        'number with a new justification is a carry until the new reason is shown to produce it. Rebuild the quarter '
                                        'from the evidence the new text names, or say plainly it is the old value and card it (rule 4; Kilian 2026-11-18)'))
    seg = {(h['rule'], h['value'].split(' ')[0] + ' ' + h['value'].split(' ')[1]) for h in out if h['where'].startswith('line ') and h['where'].count(' ') > 1}
    return [h for h in out if not (h['where'] in ('line revenue', 'line EBIT') and (h['rule'], ' '.join(h['value'].split(' ')[:2])) in seg)]  # the segment hit says it


def trace(D, s, RI=None):
    import forecast_lint as fl
    if RI is None:
        sp = _j(os.path.join(fl.RUNS, D, s, 'supervisor.json'), {}) or {}
        RI = (sp.get('model') or {}).get('reconciled_inputs')
    if not isinstance(RI, dict): return []
    S = series(RI)
    if not S: return []
    return steps(S) + holds(S, texts(RI), refs(D, s))


def snapshot(rd, n, RI, utc=''):
    """gate/values_rN.json: the series values and quoting text as gate round n saw them (a later round compares against it)."""
    gd = os.path.join(rd, 'gate'); os.makedirs(gd, exist_ok=True)
    S = series(RI) if isinstance(RI, dict) else {}
    json.dump(dict(round=n, utc=utc, series={k: dict(labels=v['labels'], values=v['values'], actual=v['actual'], kind=v['kind']) for k, v in S.items()},
                   texts=[list(t) for t in texts(RI)] if isinstance(RI, dict) else []), open(os.path.join(gd, f'values_r{n}.json'), 'w'))


if __name__ == '__main__':
    if len(sys.argv) < 3: print(__doc__); sys.exit(2)
    H = trace(sys.argv[1], sys.argv[2].upper())
    for h in H: print(f"[{h['rule']}] {h['where']}: {h['value']}\n   {h['why']}" + (f"\n   \"{h['sentence'][:220]}\"" if h.get('sentence') else '') + f"\n   key {h['key']}")
    print(f'{len(H)} hit(s)'); sys.exit(1 if H else 0)
