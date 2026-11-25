"""Forecasts solved to a stated figure, caught on the VALUES (Kilian 2026-11-23, MRVL comms_rev: Q4 FY27 = FY27 (+8% on FY26,
from Murphy's "approach our 10% target") less Q1-Q3, and FY28-29 at +2.5% a year, "the rate that reaches" his ~$2.5B FY31
figure; the wording check missed it and a guide_use declaration cleared it. "don't do that is this not enforced"; "yes" to a
code check on the driver values). Rule 5 in RULES.md. Called by forecast_lint.lint (gate, finish, gate-merge, buys).

  year-residual    a fiscal year with reported AND forecast quarters whose total equals a stated year figure: the forecast
                   quarters are that figure less the reported quarters. Stated figures: the guidance_check row for that year
                   (guide low / high / midpoint, record-adjusted guide, midpoint x (1 + a past beat), consensus) and, from the
                   driver's own text, the prior year x (1 +/- every percentage written there) and every $ amount written
                   beside that year.
  compound-target  forecast years that grow at one constant rate, where that rate (as written, at its own precision)
                   compounds from the first year of the run to a stated later figure: a $ amount written beside a year in
                   the set's text, or a guidance_check row for a later year. Long-term targets are never a guide use.

GUIDE USE STAYS ALLOWED (Kilian 2026-11-23: "this needs to be reconciled with allowing guide use when no better source exists
and the guide is reasonably trustworthy"; rule 5 last resort, 2026-11-17): a year-residual whose year sits on the company's
guide for that year (guidance_check guide low / high / midpoint, or midpoint x usual beat), with a complete guide_use
declaration of the matching kind on the guidance_check row or the driver, is a FLAG shown to Kilian (rule guide-use), not a
failure. Everything else (a haircut or reading of a management statement, consensus, a long-term target, our own percentage
of the prior year) has no guide_use way out: build the quarters from their drivers. A wrong hit is passed only by
forecast_lint.py override with a logged --why.

"Equal" means equal at the precision the set stores: each forecast quarter is rounded to the decimals it is written with, so a
year total can differ from the figure it was solved to by up to half a unit per forecast quarter; a written growth rate
(2.5%) stands for every rate that rounds to it. No other tolerance."""
import math, re
from decimal import Decimal

PCT = re.compile(r'(?<![\w.])([-+−]?)\s*(\d+(?:\.\d+)?)\s*%')
AMT = re.compile(r'\$\s?(\d[\d,]*(?:\.\d+)?)\s*(trillion|billion|million|thousand|bn|tn|mm|T|B|M|K)?\b', re.I)
YEAR = re.compile(r"\b(?:FY\s?'?(\d{4}|\d{2})|fiscal(?: year)?\s+(\d{4})|CY\s?'?(\d{4}|\d{2})|(20[2-4]\d))\b(?![-/]\d)", re.I)
SCALE = dict(t=1e12, trillion=1e12, tn=1e12, b=1e9, billion=1e9, bn=1e9, m=1e6, million=1e6, mm=1e6, k=1e3, thousand=1e3)


def _num(x): return x if isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x) else None


def _dec(x):
    """Decimals x is written with (620.9 -> 1, 8.57988 -> 5)."""
    e = Decimal(repr(float(x))).normalize().as_tuple().exponent
    return max(0, -e) if isinstance(e, int) else 0


def _half(vals, dec):
    """Half a unit of the series' last stored decimal per value (the rounding a year total can carry). dec = the most
    decimals any value of the series is written with (a float drops trailing zeros: 488.0 beside 595.3 is written to 0.1)."""
    return len(vals) * 0.5 * 10 ** -dec


def _fy(label):
    m = re.search(r'FY\s?(\d{4})', str(label or ''))
    return int(m.group(1)) if m else None


def _unit_scale(unit):
    u = str(unit or '')
    if re.search(r'\$\s?B\b|\$bn|billion', u, re.I): return 1e9
    if re.search(r'\$\s?M\b|\$mm|million', u, re.I): return 1e6
    if re.search(r'\$\s?K\b|thousand', u, re.I): return 1e3
    if re.search(r'\$', u): return 1.0
    return None


def series_dec(values): return min(max((_dec(v) for v in values), default=0), 12)


def years_of(labels, values, actual):
    """[(fy, total, n_forecast_quarters, forecast_values)] for complete fiscal years on the quarter grid."""
    by = {}
    for i, (l, v) in enumerate(zip(labels, values)):
        fy = _fy(l)
        if fy is None: return []
        by.setdefault(fy, []).append((i, v))
    out = []
    for fy in sorted(by):
        q = by[fy]
        if len(q) < 4 and fy != max(by) and fy != min(by): return []
        if len(q) < 4: continue
        f = [v for i, v in q if i >= actual]
        out.append((fy, sum(v for _, v in q), len(f), f))
    return out


def _year_tok(m, fy_end_month):
    a, b, c, d = m.groups()
    if a: n = int(a); return [n + 2000 if n < 100 else n]
    if b: return [int(b)]
    n = int(c or d); n = n + 2000 if n < 100 else n
    return [n, n + 1] if fy_end_month and fy_end_month <= 6 else [n]  # a calendar year in a Jan-Jun fiscal-year company


STATED = re.compile(r"\b(?:guide[sd]?|guidance|outlook|target\w*|goal|ambition|aspiration|framework|commitment|consensus|Street|"
                    r"expects?|expected|expecting|said|says|stated?|statement|indicat\w*|management|CEO|CFO|chair\w*|call|investor day|"
                    r"analyst day|deck|slide|plan|long[- ]term model)\b", re.I)
GUIDE_SAID = re.compile(r'\b(?:guide[sd]?|guidance|outlook)\b', re.I)


def _clause(s, i):
    """The clause around position i: between the nearest ; , ( ) : boundaries."""
    a = max(s.rfind(c, 0, i) for c in ';,()') + 1
    b = [j for j in (s.find(c, i) for c in ';,()') if j >= 0]
    return s[a:min(b) if b else len(s)]


def _said(s, i, whole=False):
    """'guide' when the figure at s[i] is the company's own (inside a quote, or its clause names a guide / outlook), 'statement'
    when the clause attributes it to someone (management, a target, consensus), None when nothing does (our own number)."""
    from forecast_lint import _quoted
    c = s if whole else _clause(s, i)
    if _quoted(s[:i]) or GUIDE_SAID.search(c): return 'guide'
    if STATED.search(c): return 'statement'


def amounts(text, scale, fy_end_month=None, whole=False):
    """[(value in the series unit, fiscal years, phrase, kind)]: $ amounts written beside a year in the same sentence and
    attributed to a stated source (an amount we only print as our own total is not a stated figure)."""
    if not scale or not text: return []
    out = []
    for s in re.split(r'(?<=[.;])\s+(?=[A-Z(\'"])|\n', text):
        ys = [(m.start(), _year_tok(m, fy_end_month)) for m in YEAR.finditer(s)]
        if not ys: continue
        for m in AMT.finditer(s):
            k = _said(s, m.start(), whole)
            if not k: continue
            v = float(m.group(1).replace(',', '')) * SCALE.get((m.group(2) or '').lower(), 1.0) / scale
            pos, fys = min(ys, key=lambda y: abs(y[0] - m.start()))
            out.append((v, fys, m.group(0).strip(), k))
    return out


XCHECK = re.compile(r'\bcross-?checks?\b|\bnot (?:an? )?inputs?\b|\bnot used\b|\bshown only\b|\bfor reference\b', re.I)
DESCR = re.compile(r'[\d,.]+\s*[BMK]?\s*(?:=|\(|,\s*(?:i\.e\.\s*)?|is\s+|was\s+|or\s+)\s*$')


def percents(text):
    """Percentages a year can be set from: not those in a cross-check sentence, nor one that only describes the number
    written just before it ("2,235.9 = +6.8%", "2,235.9 (+6.8% y/y)")."""
    out = set()
    for s in re.split(r'(?<=[.;])\s+(?=[A-Z(\'"])|\n', text or ''):
        if XCHECK.search(s): continue
        for m in PCT.finditer(s):
            p = float(m.group(2)) / 100
            if p and not DESCR.search(s[max(0, m.start() - 30):m.start()]): out.add((p, m.group(0).strip(), _said(s, m.start()) or 'statement'))
    return out


def _written_rate(lo, hi):
    """The shortest-written growth rate inside [lo, hi] and the half unit it stands for, or None."""
    for d in range(0, 5):  # percent decimals
        u = 10 ** -d / 100
        k = math.ceil(lo / u - 1e-9)
        if k * u <= hi + 1e-15: return k * u, u / 2
    return None


def _guide_figures(g, beats):
    """[(value, kind, name)] the guidance_check row states for its year."""
    lo, hi = _num(g.get('guide_low')), _num(g.get('guide_high'))
    F = []
    if lo is not None and hi is not None and not (lo == 0 and hi == 0):
        mid = (lo + hi) / 2
        F += [(lo, 'guide', 'guide low'), (hi, 'guide', 'guide high'), (mid, 'guide', 'guide midpoint')]
        F += [(mid * (1 + b), 'guide_x_beat', f'guide midpoint x (1 + {nm} {b:+.2%})') for nm, b in beats(g.get('track_record') or {})]
    if _num(g.get('record_adjusted_guide')): F.append((g['record_adjusted_guide'], 'guide_x_beat', 'record-adjusted guide'))
    c = g.get('consensus')
    if isinstance(c, dict): c = c.get('mean', c.get('value', c.get('mid')))
    if _num(c): F.append((c, 'consensus', 'consensus'))
    return F


STOP = {'non', 'gaap', 'adjusted', 'total', 'incl', 'including', 'excl', 'excluding', 'and', 'the', 'other', 'net', 'line', 'driver'}


def _words(t): return {w for w in re.findall(r'[a-z]{3,}', str(t or '').lower().replace('_', ' ')) if w not in STOP}


def _metric_fits(metric, name, label):
    """A guidance_check row applies to the series its metric names (revenue -> the revenue line, capex -> capex)."""
    return bool(_words(metric) & (_words(name) | _words(label)))


def _texts_of(d):
    t = [d.get(k) for k in ('source', 'derivation', 'quarter_derivation')]
    gu = d.get('guide_use') if isinstance(d.get('guide_use'), dict) else {}
    t.append(gu.get('why_nothing_better'))
    return ' \n'.join(x for x in t if isinstance(x, str))


def _fy_end_month(RI):
    ends = (RI.get('quarters') or {}).get('ends') or []
    labels = (RI.get('quarters') or {}).get('labels') or []
    for l, e in zip(labels, ends):
        if str(l).startswith('Q4') and re.match(r'\d{4}-\d\d', str(e or '')): return int(str(e)[5:7])


def check(RI, lint_mod):
    """Failures and guide-use flags (flag_only) for one reconciled set. lint_mod = forecast_lint (for _beats, _texts,
    guide_use_problems)."""
    import carry_trace as CT
    Q = RI.get('quarters') or {}; L = Q.get('labels') or []; A = Q.get('actual')
    if not isinstance(A, int) or not L or A < 1: return []
    S = CT.series(RI)
    drivers = {}
    ds = RI.get('drivers') or []
    for d in (ds.values() if isinstance(ds, dict) else ds):
        if isinstance(d, dict): drivers[str(d.get('id'))] = d
    fyem = _fy_end_month(RI)
    gcs = [g for g in RI.get('guidance_check') or [] if isinstance(g, dict) and re.fullmatch(r'\s*FY\s?\d{4}\s*', str(g.get('fy') or ''))]
    all_text = ' \n'.join(str(x) for _, x in lint_mod._texts(RI)) + ' \n' + ' \n'.join(_texts_of(d) for d in drivers.values())
    set_scale = _unit_scale(RI.get('unit'))
    driver_vals = [tuple(round(v, 9) for v in s['values']) for s in S.values() if s['kind'] == 'driver']
    out = []
    for name, s in S.items():
        if s['kind'] == 'constant': continue  # constants carry no aggregation rule; the lines they feed are checked
        d = drivers.get(s['id']) if s['kind'] == 'driver' else None
        if d is not None:
            if not str(d.get('agg') or '').startswith('sum'): continue  # rates, shares, margins: a year is not their total
            scale = _unit_scale(d.get('unit')) or set_scale
            own = _texts_of(d)
            decl = d.get('guide_use'); label = str(d.get('label') or '')
        else:
            if tuple(round(v, 9) for v in s['values']) in driver_vals: continue  # a line that is one driver: checked as the driver
            scale, own, decl, label = set_scale, '', None, ''
        Y = years_of(s['labels'], s['values'], s['actual'])
        if len(Y) < 2: continue
        dec = series_dec(s['values'])
        hit = _year_residual(name, Y, own, scale, fyem, gcs, decl, lint_mod, dec, label) if own or gcs else None
        if hit: out.append(hit)
        c = _compound(name, Y, all_text, scale, fyem, gcs, dec)
        if c: out.append(c)
    seen, res = set(), []  # a built line that repeats a driver's hit (same years, value, anchor) is the same finding
    for f in sorted(out, key=lambda f: not f['where'].startswith('driver ')):
        sig = (f['rule'], f['where'].split()[-1], str(f.get('value')), f.get('anchor'))
        if sig not in seen: seen.add(sig); res.append(f)
    return res


def _year_residual(name, Y, own, scale, fyem, gcs, decl, lint_mod, dec, label=''):
    for j, (fy, tot, nf, fv) in enumerate(Y):
        if not (0 < nf < 4) or not tot: continue  # only a year with reported quarters can be solved as "the year less what is reported"
        tol = _half(fv, dec) + 1e-9 * max(1.0, abs(tot))
        cand = []
        for g in gcs:
            if _fy(g.get('fy')) == fy and _metric_fits(g.get('metric'), name, label):
                cand += [(v, k, f"{nm} {v:,.6g} ({g.get('metric')} {g.get('fy')})", g) for v, k, nm in _guide_figures(g, lint_mod._beats)]
        if j > 0 and Y[j - 1][1]:
            prev = Y[j - 1][1]
            for p, ph, k in percents(own):
                for sg in (1, -1):
                    cand.append((prev * (1 + sg * p), k, f"FY{fy - 1} {prev:,.6g} x (1 {'+' if sg > 0 else '-'} {p * 100:.4g}%) ('{ph}' in the text)", None))
        for v, fys, ph, k in amounts(own, scale, fyem):
            if fy in fys: cand.append((v, k, f"'{ph}' written beside FY{fy}", None))
        for v, kind, what, g in cand:
            if abs(tot - v) > tol: continue
            base = dict(where=f'{name} FY{fy}', value=round(tot, 6), anchor=what, kind=kind)
            if kind in ('guide', 'guide_x_beat'):
                for dl in ((g or {}).get('guide_use'), decl):
                    if dl is not None and not lint_mod.guide_use_problems(dl, kind):
                        return dict(base, rule='guide-use', key=f'guide-use:{name}:FY{fy}', decl=dl, flag_only=True,
                                    why=f'FY{fy} total {tot:,.6g} sits on the {what} with its forecast quarters the year less the reported '
                                        f'quarters: declared guide use ({kind}), flagged to Kilian')
            return dict(base, rule='year-residual', key=f'year-residual:{name}:FY{fy}',
                        why=f'FY{fy} total {tot:,.6g} equals the {what}, so its {nf} forecast quarter(s) are that figure less the reported '
                            'quarters (solved to a target, rule 5). Build the quarters from their drivers. '
                            + ('Only the company guide for the year, with a complete guide_use of the matching kind, is allowed as a last resort.'
                               if kind != 'guide' else 'Sits on the guide: declare a complete guide_use (kind "guide") if nothing better exists.'))
    return None


def _compound(name, Y, text, scale, fyem, gcs, dec):
    targets = [(v, fys, f"'{ph}'") for v, fys, ph, _ in amounts(text, scale, fyem, whole=True)]  # a later-year figure attributed anywhere in its sentence
    for g in gcs:  # guidance_check rows for later years (long-term targets)
        n = re.search(r'\d{4}', str(g.get('fy'))); lo, hi = _num(g.get('guide_low')), _num(g.get('guide_high'))
        if n and lo is not None and hi is not None:
            targets += [(lo, [int(n.group())], f"{g.get('metric')} {g.get('fy')} low"), (hi, [int(n.group())], f"{g.get('metric')} {g.get('fy')} high"),
                        ((lo + hi) / 2, [int(n.group())], f"{g.get('metric')} {g.get('fy')} midpoint")]
    if not targets: return None
    first_f = next((j for j, y in enumerate(Y) if y[2] > 0), None)
    if first_f is None: return None
    h = [_half(y[3], dec) for y in Y]
    for k in range(max(0, first_f - 1), len(Y) - 1):  # from the last reported year on
        fy0, y0 = Y[k][0], Y[k][1]
        if y0 <= 0: continue
        for e in range(len(Y) - 1, k + 1, -1):  # the longest constant-rate run from year k (two steps at least: one step is any rate)
            lo, hi = -math.inf, math.inf
            for j in range(k + 1, e + 1):
                a, b = Y[j - 1][1], Y[j][1]
                if a - h[j - 1] <= 0: lo = math.inf; break
                lo = max(lo, (b - h[j]) / (a + h[j - 1]) - 1); hi = min(hi, (b + h[j]) / (a - h[j - 1]) - 1)
            if lo > hi: continue
            wr = _written_rate(lo, hi)
            if not wr: continue
            r, hu = wr
            for T, fys, what in targets:
                for N in fys:
                    if N < fy0 + 2 or N < Y[e][0] or T <= 0: continue
                    gi = (T / y0) ** (1 / (N - fy0)) - 1
                    if abs(gi - r) <= hu + 1e-12:
                        return dict(rule='compound-target', key=f'compound-target:{name}:FY{fy0}', where=f'{name} FY{Y[k + 1][0]}-FY{Y[e][0]}',
                                    value=f'{r * 100:.{max(0, round(-math.log10(hu * 200)))}f}%/yr', anchor=f'{what} (FY{N})',
                                    why=f'FY{Y[k + 1][0]}-FY{Y[e][0]} grow at {r:.2%} a year from FY{fy0} {y0:,.6g}, the rate that compounds to '
                                        f'{what} = {T:,.6g} in FY{N} ({gi:.3%} a year): growth solved to a stated long-term figure (rule 5). '
                                        'Build each year from its drivers; a long-term target is a cross-check, never the path.')
            break
    return None
