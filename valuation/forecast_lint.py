"""Hard stop on two forecast failures (Kilian 2026-11-09, VST FY2027 = guide midpoint 8,100 x 1.019 with gen_price back-solved,
and retail_twh / ercot_dc_gw set from trend fits, all passed by the gate: "how is this still happening ... how do I hard stop
this from happening?"). Memory and brief text alone did not stop it, so the script checks every reconciled set:

  guide-anchor   a projected guidance_check year whose our_value / drivers_imply sits within TOL (0.3%) of the guide midpoint,
                 the record-adjusted guide, or midpoint x (1 + a past period's beat, the median or the mean beat): the year was
                 set to the guide (or guide x usual beat), not built bottom-up. A closed period (actual in the track record)
                 is skipped.
  back-solve     derivation / guidance why wording that sets a driver to reach a total: "back-solved", "set so the build",
                 "to hit", "absorbs the gap", "calibrated to the guide", "solved for", "plug", "midpoint x 1.0x" ...
                 (actual quarters reconciled to filed totals are fine and are skipped).
  trend-only     a sentence that uses a past trend or fit as the path ("extend the observed trend", "trend is extended",
                 "least-squares slope", "trend fit", "/yr fit", "extrapolate"), cited or not: the source shows the past, not the
                 forecast year. A past-years "CAGR" fails only with no cited source, target, guide or consensus beside it.
                 Filling a past quarter between observations ("interpolated", "back-extrapolated") is skipped.
Negated wording ("not back-solved", "never extend the trend") is skipped.

A failure can only be passed with a logged reason, one key at a time:
  python3 forecast_lint.py check SET.json            print failures (exit 1 if any are not overridden)
  python3 forecast_lint.py round D SYM               the same for runs/D/SYM/supervisor.json, with that round's overrides
  python3 forecast_lint.py override D SYM KEY --why "<reason>"
                                                     writes runs/D/SYM/lint_overrides.json and logs to data/rule_overrides.jsonl
  python3 forecast_lint.py all [D]                   every name's current research set (newest result.json research_as_of
                                                     on or before D) -> data/forecast_lint.json and a summary
Wired into: name_update.py gate (refuses to start the gate) and finish (refuses), run_digest.py gate-merge (each failure is a
gate failure, so no clean citation_audit.json), rules_check.py (no due buy / add / swap-in on a failing set)."""
import glob, json, os, re, sys
HERE = os.path.dirname(os.path.abspath(__file__)).replace('/mnt/attach/project-files', '/mnt/project-files')
RUNS = os.environ.get('VAL_RUN_ROOT', os.path.join(HERE, 'runs'))
sys.path.insert(0, HERE)
TOL = 0.003

NEG = re.compile(r"\b(not|never|no|nor|without|instead of|rather than|isn't|wasn't|don't|avoid\w*|against|would|need|needs)\b[^.;]{0,40}$", re.I)
BACK = [
    r'back[- ]?solv\w*', r'reverse[- ]engineer\w*', r'\bsolv(?:e|ed|es|ing) (?:for|to|so)\b',
    r'\bset so (?:that )?(?:the )?(?:build|total|year|sum|ebitda|revenue|eps|segment|fy)', r'\bso (?:that )?the (?:build|total) (?:hits|reaches|matches|lands)',
    r'\bto hit\b', r'\babsorbs? (?:the )?(?:gap|residual|difference|rest|balance|remainder)', r'\bcalibrated to (?:the )?(?:guide|guidance|midpoint|range)',
    r'\bto (?:reach|match|land on|tie to|tie out to|equal) (?:the )?(?:guide|guidance|midpoint|mid\b|record[- ]adjusted)',
    r'\bplug(?:ged)?\b', r'\b(?:guide |guided |narrowed |the )?mid(?:point)?\s*(?:[\d,.]+\s*)?(?:x|×|\*|times)\s*(?:\(1\s*\+|1\.\d)',
    r'\bimplied by (?:the )?(?:guide|guidance|midpoint)\b',
]
TREND = [  # strong: fail even when the sentence cites the history it was fitted to (the source must be for the forecast, not the past)
    r'\bextend(?:s|ed|ing)? (?:the |its |a )?(?:observed |past |historical |recent |reported |pre-\w+ |\d+[- ]\w+ )*(?:trend|slope|pace|run[- ]rate|decline|growth rate)',
    r'\btrend (?:is |was |gets )?extended', r'\bobserved (?:[\w-]+ ){0,3}trend', r'\btrend[- ](?:fit|line|extrapolation)',
    r'\bfitted (?:trend|slope|line|path|growth|rate|decline)', r'\b(?:linear|log|exponential|ols) (?:fit|regression|trend)', r'\bleast[- ]squares\b',
    r'/\s*(?:yr|year) fit\b', r'\bfit (?:to|of|on) (?:the )?(?:\d+|past|history|reported|actuals|observations)',
    r'(?<!back-)\bextrapolat(?!\w*\s+(?:back|method))\w*', r'\bcontinu(?:e|es|ing) (?:the |its )?(?:observed |past |historical |recent )?(?:trend|pace|decline|slope)',
    r'\bregression (?:line|fit|slope)', r'\bslope of (?:the )?(?:\w+ ){0,4}(?:observations|actuals|history|points)',
]
SOFT = [r'\bCAGR\b']  # soft: a past-years CAGR used as the path; fine beside a target, guide, consensus or cited outside source
BACK_RE = [re.compile(p, re.I) for p in BACK]; TREND_RE = [re.compile(p, re.I) for p in TREND]; SOFT_RE = [re.compile(p) for p in SOFT]
SOFT_OK = re.compile(r'\b(target|guide|guidance|framework|outlook|consensus|plan|deck|slide|management|market (?:report|data|forecast))\b', re.I)
HIST_FILL = re.compile(r'(back-extrapolat|extrapolated back|interpolat)', re.I)
QUARTER_SPLIT = re.compile(r'\bquarter', re.I)
YEAR_TOTAL = re.compile(r"\b(?:so the year|so the years|each year's total|the live year|the year's total|equal the (?:live )?year)", re.I)
NOT_USED = re.compile(r'\b(not used|shown only|cross-check only|but not used|for reference only)\b', re.I)
ACTUAL_OK = re.compile(r'\b(actual|reported|filed|10-[KQ]|reconcil\w*|base quarters|base year)\b', re.I)
FWD = re.compile(r'\b(guide|guidance|midpoint|forecast|FY20(?:2[6-9]|3\d)E?|projected|target)\b', re.I)
CITE = re.compile(r'\bsrcs?\.?\s*#?\d|https?://|\bsource_ids?\b', re.I)


def _j(p, d=None):
    try: return json.load(open(p))
    except (OSError, ValueError): return d


def _sentences(t):
    import run_digest
    return run_digest.sentences(t)


def _hit(rx, s):
    for r in rx:
        for m in r.finditer(s):
            if not NEG.search(s[:m.start()]): return m.group(0)


def _num(x): return x if isinstance(x, (int, float)) and not isinstance(x, bool) else None


def _pct_unit(tr):
    """Units of a track record's actual_vs_mid_pct / median_vs_mid_pct: 'pct' (3.4 = +3.4%), 'frac' (0.034) or None (unclear).
    Sets are mixed (AMAT writes percent, MOD and SPCX fractions), so read it off periods whose actual and first guide give the
    beat directly; with none, a value over 1 in size is percent (a >100% beat is not plausible); else unclear."""
    votes = {'pct': 0, 'frac': 0}; vals = []
    for p in tr.get('periods') or []:
        b, a = _num(p.get('actual_vs_mid_pct')), _num(p.get('actual'))
        lo, hi = _num(p.get('first_guide_low')), _num(p.get('first_guide_high'))
        if b is None: continue
        vals.append(b)
        if a is None or lo is None or hi is None or not (lo + hi): continue
        d = a / ((lo + hi) / 2) - 1
        if abs(d) < 1e-6: continue
        ep, ef = abs(b / 100 - d), abs(b - d)
        if min(ep, ef) <= 0.1 * abs(d) + 1e-4: votes['pct' if ep < ef else 'frac'] += 1
    if votes['pct'] != votes['frac']: return 'pct' if votes['pct'] > votes['frac'] else 'frac'
    m = _num(tr.get('median_vs_mid_pct'))
    if any(abs(v) > 1 for v in vals + ([m] if m is not None else [])): return 'pct'
    return None


def _beats(tr):
    """[(label, beat as a fraction)] for each past period, the stored median and the mean; both readings when units are unclear."""
    u = _pct_unit(tr)
    scale = {'pct': [(100, '')], 'frac': [(1, '')], None: [(100, ' if percent'), (1, ' if fraction')]}[u]
    per = [(p.get('period'), _num(p.get('actual_vs_mid_pct'))) for p in tr.get('periods') or []]
    per = [(k, b) for k, b in per if b is not None]
    out = []
    for k, sfx in scale:
        out += [(f"{per_k} beat{sfx}", b / k) for per_k, b in per]
        m = _num(tr.get('median_vs_mid_pct'))
        if m is not None: out.append((f"median beat{sfx}", m / k))
        if len(per) > 1: out.append((f"mean beat{sfx}", sum(b for _, b in per) / len(per) / k))
    return out


def guide_anchor(RI):
    out = []
    for g in RI.get('guidance_check') or []:
        lo, hi = _num(g.get('guide_low')), _num(g.get('guide_high'))
        if lo is None or hi is None or (lo == 0 and hi == 0): continue
        fy = str(g.get('fy') or ''); tr = g.get('track_record') or {}
        per = tr.get('periods') or []
        if any(str(p.get('period')) == fy and _num(p.get('actual')) is not None for p in per): continue  # closed period
        mid = (lo + hi) / 2
        anchors = [('guide midpoint', mid)]
        if _num(g.get('record_adjusted_guide')): anchors.append(('record-adjusted guide', g['record_adjusted_guide']))
        for name, b in _beats(tr): anchors.append((f"midpoint x (1 + {name} {b:+.2%})", mid * (1 + b)))
        seen = set()
        for k in ('our_value', 'drivers_imply'):
            v = _num(g.get(k))
            if v is None or v in seen: continue
            seen.add(v)
            for name, a in anchors:
                if a and abs(v / a - 1) <= TOL:
                    key = f"guide-anchor:{g.get('metric')}:{fy}"
                    out.append(dict(rule='guide-anchor', key=key, where=f"guidance_check {g.get('metric')} {fy}", value=v,
                                    why=f"{k} {v:,.6g} is within {TOL:.1%} of the {name} {a:,.6g} (guide {lo:,.6g}-{hi:,.6g}): "
                                        'build the year from its drivers; the guide is only a cross-check'))
                    break
            else: continue
            break
    return out


def _texts(RI):
    t = []
    for g in RI.get('guidance_check') or []:
        t += [(f"guidance_check {g.get('metric')} {g.get('fy')} {k}", g.get(k)) for k in ('why', 'drivers_note')]
    ds = RI.get('drivers') or []
    for d in (ds.values() if isinstance(ds, dict) else ds):
        if not isinstance(d, dict): continue
        for k in ('derivation', 'quarter_derivation', 'variant', 'flag'):
            if k in ('variant', 'flag'): continue  # variants describe alternatives; flags describe weaknesses
            t.append((f"driver {d.get('id')} {k}", d.get(k)))
    for i in RI.get('inputs_detail') or []:
        if isinstance(i, dict) and not re.search(r'scenario[ _]vol', str(i.get('input')), re.I): t.append((f"inputs_detail {i.get('input')} derivation", i.get('derivation')))
    return [(w, x) for w, x in t if isinstance(x, str) and x.strip()]


def wording(RI):
    out = []
    for where, t in _texts(RI):
        for n, s in enumerate(_sentences(t)):
            b = _hit(BACK_RE, s)
            if b and ACTUAL_OK.search(s) and not FWD.search(s): b = None  # actual quarters reconciled to filed totals
            if b and QUARTER_SPLIT.search(s) and YEAR_TOTAL.search(s): b = None  # quarters shaped to sum to their own year
            if b and NOT_USED.search(s): b = None  # a record-adjusted figure shown as a cross-check
            if b:
                out.append(dict(rule='back-solve', key=f'back-solve:{where}:{n}', where=where, phrase=b, sentence=s[:400],
                                why='a driver or total set so it reaches a target (the guide, the guide x usual beat, an old value); build it from its own evidence'))
            tr = _hit(TREND_RE, s)
            if tr and HIST_FILL.search(s) and not FWD.search(s): tr = None  # filling a past quarter between observations
            if not tr:
                tr = _hit(SOFT_RE, s)
                if tr and (CITE.search(s) or SOFT_OK.search(s)): tr = None
            if tr:
                out.append(dict(rule='trend-only', key=f'trend-only:{where}:{n}', where=where, phrase=tr, sentence=s[:400],
                                why='a past trend or fit used as the forecast path; build the real-world case (market, share, capacity, contracts) with its own source'))
    return out


def lint(RI): return guide_anchor(RI) + wording(RI)


def overrides(D, s): return (_j(os.path.join(RUNS, D, s, 'lint_overrides.json'), {}) or {}).get('overrides') or {}


def round_fail(D, s, all_=False):
    """Failures for runs/D/SYM/supervisor.json not passed by a logged override (all_=True: every failure, override noted)."""
    sup = _j(os.path.join(RUNS, D, s, 'supervisor.json'), {}) or {}
    RI = (sup.get('model') or {}).get('reconciled_inputs')
    if not isinstance(RI, dict): return []
    ov = overrides(D, s); out = []
    for f in lint(RI):
        if f['key'] in ov:
            if all_: out.append(dict(f, overridden=ov[f['key']]))
        else: out.append(f)
    return out


def gate_rows(D, s):
    """Rows for run_digest.py gate-merge (result back_solved / trend_only fail the gate)."""
    res = {'guide-anchor': 'back_solved', 'back-solve': 'back_solved', 'trend-only': 'trend_only'}
    return [dict(origin='forecast_lint.py (script)', item=f['where'], value=f.get('value', f.get('phrase')), basis='', source_url='',
                 result=res[f['rule']], page_shows='', note=f"{f['why']} | key {f['key']}" + (f" | {f['sentence']}" if f.get('sentence') else ''),
                 checked_by='forecast_lint.py (script)') for f in round_fail(D, s)]


def refuse(D, s, step):
    f = round_fail(D, s)
    if not f: return
    lines = '\n'.join(f"  [{x['rule']}] {x['where']}: {x['why']}" + (f"\n      \"{x['sentence'][:220]}\"" if x.get('sentence') else '') + f"\n      key {x['key']}" for x in f)
    sys.exit(f"REFUSED (forecast-lint, {step} {s} {D}): {len(f)} forecast(s) set to the guide, back-solved or trend-only (Kilian 2026-11-09).\n{lines}\n"
             'Rebuild those years from their drivers. Only if a hit is wrong (a bottom-up build that happens to land on the guide, '
             f'quoted wording): python3 {HERE}/forecast_lint.py override {D} {s} KEY --why "<reason>" (logged).')


def override(D, s, key, why):
    import guards
    keys = {f['key'] for f in round_fail(D, s, all_=True)}
    if key not in keys: sys.exit(f'no such failure key for {s} {D}: {key}')
    guards.need_why('forecast-lint', why, f'{s} {D} {key}', symbol=s, round=D, key=key)
    p = os.path.join(RUNS, D, s, 'lint_overrides.json'); o = _j(p, {}) or {}
    import datetime as dt
    o.setdefault('overrides', {})[key] = dict(why=why, utc=dt.datetime.now(dt.timezone.utc).strftime('%Y-%m-%dT%H:%MZ'))
    json.dump(o, open(p, 'w'), indent=1); print(f'override logged: {s} {D} {key}')


def current_sets(D=None):
    """{SYM: research_as_of} from the newest run folder on or before D that has the name's result.json."""
    import datetime as dt
    D = D or dt.date.today().isoformat()
    dates = sorted(x for x in os.listdir(RUNS) if len(x) == 10 and x[4] == '-' and x <= D)[-15:]
    out = {}
    for d in dates:
        for f in glob.glob(os.path.join(RUNS, d, '*', 'result.json')):
            ra = (_j(f, {}) or {}).get('research_as_of')
            if ra: out[f.split('/')[-2]] = ra
    for d in dates:  # research folders newer than the last re-mark (an update round not re-marked yet)
        for f in glob.glob(os.path.join(RUNS, d, '*', 'update.json')):
            s = f.split('/')[-2]
            if os.path.exists(os.path.join(RUNS, d, s, 'citation_audit.json')) and d > out.get(s, ''): out[s] = d
    return out


def all_(D=None):
    import concurrent.futures as cf
    cs = current_sets(D)
    def one(it):
        s, ra = it
        sup = _j(os.path.join(RUNS, ra, s, 'supervisor.json'), {}) or {}
        RI = (sup.get('model') or {}).get('reconciled_inputs')
        if not isinstance(RI, dict) or not RI.get('drivers'): return s, ra, None
        return s, ra, round_fail(ra, s, all_=True)
    with cf.ThreadPoolExecutor(16) as ex: rows = list(ex.map(one, sorted(cs.items())))
    rep = {s: dict(round=ra, failures=f) for s, ra, f in rows if f is not None}
    json.dump(rep, open(os.path.join(HERE, 'data', 'forecast_lint.json'), 'w'), indent=1)
    for s, r in rep.items():
        c = {}
        for f in r['failures']:
            if not f.get('overridden'): c[f['rule']] = c.get(f['rule'], 0) + 1
        print(f"{s:6} {r['round']}  " + (', '.join(f'{k} {v}' for k, v in sorted(c.items())) or 'clean'))
    return rep


if __name__ == '__main__':
    a = sys.argv[1:]
    def opt(k):
        if k in a: i = a.index(k); v = a[i + 1]; del a[i:i + 2]; return v
    why = opt('--why')
    if not a: print(__doc__); sys.exit(1)
    if a[0] == 'check':
        RI = (_j(a[1], {}).get('model') or {}).get('reconciled_inputs') or _j(a[1], {})
        f = lint(RI); print(json.dumps(f, indent=1)); sys.exit(1 if f else 0)
    elif a[0] == 'round':
        f = round_fail(a[1], a[2].upper(), all_=True); print(json.dumps(f, indent=1)); sys.exit(1 if any(not x.get('overridden') for x in f) else 0)
    elif a[0] == 'override': override(a[1], a[2].upper(), a[3], why or '')
    elif a[0] == 'all': all_(a[1] if len(a) > 1 else None)
    else: print(__doc__); sys.exit(1)
