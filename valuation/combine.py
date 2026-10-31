"""Combine forecaster runs into NTM price target, take-profit and stop-loss, following the AIA Forecaster recipe:
  1. ensemble: simple mean of the M independent forecasters' ladders (per strike)
  2. supervisor: its ladder replaces the mean only when it reports "high" confidence
  3. statistical correction: Platt scaling p' = sigmoid(a * logit(p)) with the paper's fixed a = sqrt(3), applied to the
     directional question P(close >= spot); the agents' distribution is shifted to match, keeping its width
  4. market blend: convex combination with the option-implied (real-world-shifted) probabilities,
     w_market = 0.67, w_llm = 0.33 (the paper's simplex-regression weights on its liquid-market benchmark);
     then 10% analyst consensus (market distribution re-centred on the median sell-side target), taken pro rata
  5. tilts: the EPS-revision tilt (revisions_sleeve/SPEC.md), the short-interest days-to-cover tilt (2026-10-19,
     short_interest.py) and the 12-1 momentum tilt (2026-10-30, momentum.py: bottom-fifth losers -3%), added as one log
     location shift of the whole blended distribution
Then levels:
  target      = median of the blended 12M distribution (also mean, 10/25/75/90th percentiles)
  take-profit = 75th percentile (25% chance of finishing above it)
  stop-loss   = the higher of the 15th percentile and the reconciled thesis-break price,
                but never closer to spot than 2 one-month implied standard deviations (noise floor)
  touch probabilities: chance of trading at the level at any time before the horizon (lognormal first-passage,
                with drift and per-level vol matched to the blended distribution)

Research-leg source (Kilian 2026-10-19 20:31Z, "switch to the standard model"): with RESEARCH_FV_SOURCE = 'standard_model'
the research leg's fair value is the engine's reconciled value (runs/RDATE/SYM/model_engine.json `model_fair_value`) and
the research distribution is re-centred, width kept, on the engine's model target (its median), whenever a gate-clean standard
model exists for that research round; since Kilian's 2026-10-26 16:12Z card ("Drop it for all model names") that leg gets no
Platt shift (STD_MODEL_PLATT = False; True restores the 10-19 harness: Platt-scaled and frozen per research round)
(standard_model() below). Otherwise, and with RESEARCH_FV_SOURCE = 'supervisor' (rollback), it is exactly as before.

Usage: python3 combine.py DATE SYM [SYM ...] [--research RDATE] [--dry-run]   (--dry-run prints, writes nothing)
Reads priors/DATE/SYM.json and runs/RDATE/SYM/{agent_*.json, supervisor.json?} (RDATE defaults to the newest agent
research on or before DATE, so a daily re-mark reuses the last research on fresh prices and chains);
writes runs/DATE/SYM/result.json,
appends to forecast_log.csv (for scoring later) and prints a summary line per ticker.
Speed (2026-10-21, Kilian approved the Price Targets speedup list; same output): on the shared network folder each file
lookup costs ~0.04s, so the command line runs the names 12 at a time in threads, reads the shared inputs (analyst targets,
IV files, revision snapshot, short interest, the book) once per process, and writes forecast_log.csv once at the end, applying
each name's row changes in the order the names were given."""
import copy, csv, glob, io, json, math, os, sys, threading
HERE = os.path.dirname(os.path.abspath(__file__))
A_PLATT = math.sqrt(3); W_MKT = 0.67; W_LLM = 0.33; W_ANALYST = 0.0  # Kilian 2026-10-05: analysts dropped (backtest: no stock-specific skill)
RESEARCH_FV_SOURCE = 'standard_model'  # Kilian 2026-10-19 20:31Z; 'supervisor' = rollback (supervisor fair_value, agents' ladder + Platt)
UPDATE_ROUNDS_SET_LEVELS = True  # 2026-10-21: a gate-clean name_update.py round's model is the research leg (False: such rounds fall back to the supervisor fair value)
STD_MODEL_RECENTRE = True  # standard model also moves the research distribution's centre (False: fair value field only)
STD_MODEL_OWN_DIST = True  # Kilian 2026-10-28 01:32Z card 'Model distribution, mean-centred': the research leg is the driver model's own scenario mix (model_engine model_ladder), not the forecasters' ladder
STD_MODEL_MEAN_CENTRE = 0.75  # same card: a model target behaves like a mean; WRDS I/B/E/S 2005-24 12M target overshoot slope on half-variance 0.75 (t 6.7, wrds/code/research_centre_test.py); 0 = median on model target
STD_MODEL_PLATT = False  # Kilian 2026-10-26 16:12Z card "Drop it for all model names": no Platt lift on a standard-model research leg (True: the 10-19 harness)
DRY = False  # --dry-run: compute and print, write nothing (no result.json, forecast_log.csv or platt.json)
N = lambda x: 0.5 * (1 + math.erf(x / math.sqrt(2)))
_MEMO = {}  # shared read-only inputs, loaded once per process (combine never writes these files)
_FAIL = object()
def _memo(key, fn):
    if key not in _MEMO:
        try: _MEMO[key] = fn()
        except Exception: _MEMO[key] = _FAIL
    v = _MEMO[key]
    if v is _FAIL: raise OSError(f'{key} unreadable')
    return v
_jfile = lambda f: _memo(('json', f), lambda: json.load(open(f)))
_gfiles = lambda pat: _memo(('glob', pat), lambda: glob.glob(pat))
_LOG_OPS = None  # batch mode (command line): {symbol: [(horizon key, fieldnames, new rows), ...]} applied to forecast_log.csv once
def Ninv(p):
    lo, hi = -12.0, 12.0
    for _ in range(90):
        m = (lo + hi) / 2
        if N(m) < p: lo = m
        else: hi = m
    return (lo + hi) / 2
clip = lambda p, e=1e-4: min(max(p, e), 1 - e)
logit = lambda p: math.log(clip(p) / (1 - clip(p)))
sig = lambda x: 1 / (1 + math.exp(-x))
platt = lambda p: sig(A_PLATT * logit(p))

def monotone(ps):
    """Non-increasing in K (pool-adjacent-violators on the survival curve)."""
    blocks = [[p, 1] for p in ps]
    i = 0
    while i < len(blocks) - 1:
        if blocks[i][0] < blocks[i+1][0]:
            a, b = blocks[i], blocks[i+1]
            blocks[i] = [(a[0]*a[1] + b[0]*b[1]) / (a[1] + b[1]), a[1] + b[1]]; del blocks[i+1]
            i = max(i - 1, 0)
        else:
            i += 1
    out = []
    for p, n in blocks: out += [p] * n
    return out

def read_ladder(obj, Ks):
    """P(close >= K) from an agent's ladder at the prior's strikes. Same strikes (the research day): read as is.
    Otherwise (a daily re-mark after the price moved, or rounded K) interpolate the agent's own curve in log K,
    so its view stays anchored to the same dollar prices."""
    lad = sorted(obj['ladder'], key=lambda x: float(x['K']))
    aK = [float(x['K']) for x in lad]; ap = monotone([float(x['p']) for x in lad])
    if len(aK) == len(Ks) and all(abs(a / K - 1) < 0.01 for a, K in zip(aK, Ks)): return ap
    f = survival_fn(aK, [clip(p) for p in ap])
    return [f(K) for K in Ks]

def research_date(date, s):
    """Newest run folder on or before DATE with a finished research round for SYM (supervisor.json is written last, so a
    round still in progress is skipped); the agents' research is reused between re-runs."""
    marker = 'supervisor.json' if glob.glob(os.path.join(HERE, 'runs', '*', s, 'supervisor.json')) else 'agent_1.json'
    ds = sorted(os.path.basename(os.path.dirname(os.path.dirname(f))) for f in glob.glob(os.path.join(HERE, 'runs', '*', s, marker)))
    ds = [d for d in ds if d <= date]
    if not ds: raise SystemExit(f'{s}: no agent research on or before {date}')
    return ds[-1]

def research_shift(research, s, sf_raw, special, used_sup, S, leg='supervisor'):
    """Platt location shift for the research round, computed at the research day's spot and stored in
    runs/RESEARCH/SYM/platt.json; later re-marks read it back. Special situations stay unscaled (shift 0)."""
    if special: return 0.0, dict(research=research, special_situation=True)
    f = os.path.join(HERE, 'runs', research, s, 'platt.json')
    if os.path.exists(f):
        c = json.load(open(f))
        if abs(c.get('platt_a', 0) - A_PLATT) < 1e-9 and c.get('supervisor_used') == used_sup \
                and c.get('research_leg', 'supervisor') == leg: return c['log_shift'], c  # 2026-10-19: frozen per research-leg source too
    pf = os.path.join(HERE, 'priors', research, f'{s}.json'); rf = os.path.join(HERE, 'runs', research, s, 'result.json')
    if os.path.exists(pf): S0, src = json.load(open(pf))['spot'], f'priors/{research}/{s}.json spot'
    elif os.path.exists(rf) and json.load(open(rf)).get('as_of') == research: S0, src = json.load(open(rf))['spot'], f'runs/{research}/{s}/result.json spot'
    else: S0, src = S, 'no research-day spot found; used this re-mark spot'
    p0 = sf_raw(S0); target = platt(p0); lo_d, hi_d = -2.0, 2.0
    for _ in range(80):
        d = (lo_d + hi_d) / 2
        if sf_raw(S0 * math.exp(-d)) < target: lo_d = d
        else: hi_d = d
    c = dict(research=research, research_spot=S0, spot_source=src, p_up_raw=round(p0, 4), p_up_platt=round(target, 4),
             log_shift=round((lo_d + hi_d) / 2, 6), platt_a=A_PLATT, supervisor_used=used_sup)
    if leg != 'supervisor': c['research_leg'] = leg  # old files carry no key and read as 'supervisor'
    if src.startswith('no research') or DRY: return c['log_shift'], c  # not stored: retry once the research-day spot exists
    json.dump(c, open(f, 'w'), indent=1)
    return c['log_shift'], c

def iv30(sym, date=None):
    files = sorted(_gfiles(os.path.join(HERE, '..', 'portfolio', 'data', 'iv', '*.json')))
    if date: files = [f for f in files if os.path.basename(f)[:-5] <= date]  # 2026-10-14: no look-ahead on back-dated re-runs
    for f in reversed(files):
        s = _jfile(f)['symbols'].get(sym)
        if s and s.get('atm30'): return s['atm30'], os.path.basename(f)[:-5]  # ATM 30-day IV (Kilian 2026-10-09 "atm vol everywhere")
        if s and s.get('iv30'): return s['iv30'], os.path.basename(f)[:-5]
    return None, None

def survival_fn(Ks, ps):
    """Smooth survival curve: linear interpolation of logit(p) in log K, extrapolated from the end segments."""
    xs = [math.log(K) for K in Ks]; ys = [logit(p) for p in ps]
    def f(K):
        x = math.log(K)
        if x <= xs[0]: i = 1
        elif x >= xs[-1]: i = len(xs) - 1
        else: i = next(j for j in range(1, len(xs)) if xs[j] >= x)
        w = (x - xs[i-1]) / (xs[i] - xs[i-1])
        return sig(ys[i-1] + w * (ys[i] - ys[i-1]))
    return f

def quantile(sf, q, lo, hi):
    for _ in range(100):
        m = math.sqrt(lo * hi)
        if sf(m) > 1 - q: lo = m
        else: hi = m
    return math.sqrt(lo * hi)

def touch_prob(S, L, T, sf, med):
    """P(price touches L before T): GBM first passage with drift from the blended median and a vol matched so the
    GBM's terminal P(S_T beyond L) equals the blended distribution's."""
    nu = math.log(med / S) / T  # log drift
    x = math.log(L / S)
    p_end = (1 - sf(L)) if L < S else sf(L)  # P(end beyond L)
    p_end = clip(p_end, 1e-6)
    z = Ninv(p_end)
    # L < S: P(ln S_T <= x) = N((x - nu T)/(s sqrt T)) = p_end  ->  s = (x - nu T)/(z sqrt T)   (z < 0 typically)
    # L > S: P(ln S_T >= x) = N((nu T - x)/(s sqrt T)) = p_end  ->  s = (nu T - x)/(z sqrt T)
    num = (x - nu*T) if L < S else (nu*T - x)
    if z == 0 or num / z <= 0: return min(1.0, 2 * p_end)
    s = num / (z * math.sqrt(T))
    mu = nu  # drift of ln S
    if L < S:
        a = x
        p = N((a - mu*T) / (s*math.sqrt(T))) + math.exp(2*mu*a/(s*s)) * N((a + mu*T) / (s*math.sqrt(T)))
    else:
        a = x
        p = N((mu*T - a) / (s*math.sqrt(T))) + math.exp(2*mu*a/(s*s)) * N((-a - mu*T) / (s*math.sqrt(T)))
    return min(max(p, p_end), 1.0)

def analyst(sym):
    """Sell-side consensus. Uses the weighted consensus (reputation x track record x freshness, analyst_weights/METHOD.md,
    data/analyst_weighted.json; Kilian 2026-09-29) as the median when it has targets, else stockanalysis.com's flat median."""
    try:
        flat = copy.deepcopy(_jfile(os.path.join(HERE, 'data', 'analyst_targets.json')).get(sym))
    except Exception:
        flat = None
    try:
        wt = copy.deepcopy(_jfile(os.path.join(HERE, 'data', 'analyst_weighted.json')).get(sym))
    except Exception:
        wt = None
    if wt and wt.get('n') and wt.get('weighted_median'):
        top = sorted(wt.get('firms') or [], key=lambda f: -(f.get('weight') or 0))[:5]
        out = dict(flat or {}, median=wt['weighted_median'], mean=wt.get('weighted_mean'), low=wt.get('low'), high=wt.get('high'),
                   n=wt['n'], effective_n=wt.get('effective_n'), flat_median=wt.get('flat_median'), as_of=wt.get('as_of'),
                   stockanalysis_median=(flat or {}).get('median'), method='weighted',
                   top_firms=[dict(firm=f.get('firm'), target=f.get('target'), date=f.get('date'), tier=f.get('tier'),
                                   weight=round(f.get('weight') or 0, 3)) for f in top])
        return out
    return dict(flat, method='flat') if flat else None

def revision_tilt(date, sym):
    """EPS-revision tilt (Kilian 2026-10-08 15:20Z, Backtests thread: fold revisions "into the weighting of the options
    market and the harness ... so that these all feed the same portfolio of up to 10 names"; spec revisions_sleeve/SPEC.md).
    Log location shift for the name's percentile in the newest revisions_sleeve/snapshots/*.csv on or before DATE, from the
    bucket table in revisions_sleeve/tilt.json. 0 when the name is missing, has fewer than min_analysts or is below min_market_cap."""
    rs = os.path.join(os.path.dirname(HERE), 'revisions_sleeve')
    snaps = sorted(f for f in _gfiles(os.path.join(rs, 'snapshots', '*.csv')) if os.path.basename(f)[:10] <= date)
    if not snaps or not _memo(('exists', os.path.join(rs, 'tilt.json')), lambda: os.path.exists(os.path.join(rs, 'tilt.json'))): return None
    cfg = _jfile(os.path.join(rs, 'tilt.json')); snap = os.path.basename(snaps[-1])[:10]
    row = next((r for r in _memo(('csv', snaps[-1]), lambda: list(csv.DictReader(open(snaps[-1])))) if r['symbol'] == sym), None)
    out = dict(snapshot=snap, pct=None, bucket='not in snapshot', log_shift=0.0)
    if not row: return out
    try: pct, n, cap = float(row['pct']), float(row.get('n_analysts') or 0), float(row.get('cap') or 0)
    except ValueError: return out
    out['pct'] = round(pct, 4)
    if n < cfg.get('min_analysts', 2) or cap < float(cfg.get('min_market_cap', 0)):
        out['bucket'] = 'too few analysts or below $10B'; return out
    b = next((b for b in cfg['buckets'] if b['pct_lo'] <= pct < b['pct_hi'] or (pct >= 1 and b['pct_hi'] >= 1)), None)
    if b: out.update(bucket=b['label'], log_shift=b['log_shift_12m'])
    return out


SI_MAX_AGE_DAYS = 50  # backtest carried a figure at most about a month past its first use; beyond this the tilt is 0

def short_interest_tilt(date, sym):
    """Short-interest days-to-cover tilt (Kilian 2026-10-19 card "Add short-interest days-to-cover as a 12-month return
    tilt"; METHOD.md). Reads the newest data/short_interest/SETTLE.json (short_interest.py: FINRA consolidated short
    interest, days to cover ranked across the $1B+ US universe) whose estimated publication date is on or before DATE (no
    look-ahead on back-dated runs), falling back to data/short_interest.json. log_shift = ln(1 + 0.048 (0.5 - pct)), about
    +/-2% between the easiest and hardest fifths; 0 when the name has no FINRA data, is outside the universe, or the
    settlement is older than SI_MAX_AGE_DAYS. None when no short-interest file exists at all."""
    files = sorted(_gfiles(os.path.join(HERE, 'data', 'short_interest', '*.json')))
    snap = None
    for f in reversed(files):
        try: d = _jfile(f)
        except Exception: continue
        if (d.get('available_est') or d.get('settle_date') or '9') <= date: snap = d; break
    if snap is None and not files and os.path.exists(os.path.join(HERE, 'data', 'short_interest.json')):
        d = _jfile(os.path.join(HERE, 'data', 'short_interest.json'))
        if (d.get('available_est') or '9') <= date: snap = d
    if snap is None: return None if not files else dict(dtc=None, pct=None, quintile=None, log_shift=0.0, settle_date=None, reason=f'no short-interest settlement published by {date}')
    r = (snap.get('names') or {}).get(sym)
    out = dict(dtc=None, pct=None, quintile=None, log_shift=0.0, settle_date=snap['settle_date'], source=snap.get('source'))
    if not r: return dict(out, reason='no FINRA data')
    out.update(dtc=r.get('dtc'), pct=r.get('pct'), quintile=r.get('quintile'))
    import datetime as _dt
    age = (_dt.date.fromisoformat(date) - _dt.date.fromisoformat(snap['settle_date'])).days
    if age > SI_MAX_AGE_DAYS: return dict(out, reason=f'settlement {age} days old (> {SI_MAX_AGE_DAYS}); run short_interest.py')
    if not r.get('in_universe'): return dict(out, pct_vs_universe=r.get('pct_vs_universe'), reason=r.get('reason') or 'outside the universe')
    return dict(out, log_shift=r.get('log_shift') or 0.0, excess_12m=r.get('excess_12m'))


MOM_MAX_AGE_DAYS = 10  # data/momentum.json is rebuilt every nightly; an older file means the step failed, so the tilt is 0

def momentum_tilt(date, sym):
    """12-1 momentum tilt (Kilian 2026-10-30 card "Add a momentum tilt for bottom-fifth 12-1 losers?" -> "Add -3% centre
    shift"; backtest wrds/code/falling_knife.py: among names whose view beats the beta line, bottom-fifth 12-1 momentum
    earned 4-6%/yr less above it, $10B+ 2-4%). Reads data/momentum.json (momentum.py D): log_shift = ln(1 - 0.03) when the
    name's 12-1 momentum is in the S&P 500's bottom fifth, else 0. Uses the file only when its as_of is on or before DATE
    (no look-ahead) and at most MOM_MAX_AGE_DAYS old. None when no file exists."""
    f = os.path.join(HERE, 'data', 'momentum.json')
    if not _memo(('exists', f), lambda: os.path.exists(f)): return None
    try: d = _jfile(f)
    except OSError: return None
    out = dict(mom=None, pct=None, bottom_fifth=False, log_shift=0.0, as_of=d.get('as_of'), cutoff=d.get('bottom_fifth_cutoff'))
    if (d.get('as_of') or '9') > date: return dict(out, reason=f"momentum file dated {d.get('as_of')} is after {date}")
    import datetime as _dt
    age = (_dt.date.fromisoformat(date) - _dt.date.fromisoformat(d['as_of'])).days
    if age > MOM_MAX_AGE_DAYS: return dict(out, reason=f'momentum file {age} days old (> {MOM_MAX_AGE_DAYS}); run momentum.py')
    r = (d.get('names') or {}).get(sym)
    if not r: return dict(out, reason='not in momentum file')
    return dict(out, mom=r.get('mom'), pct=r.get('pct'), bottom_fifth=bool(r.get('bottom_fifth')), log_shift=r.get('log_shift') or 0.0)

def holding(sym):
    try:
        import book
        for p in _memo(('book',), book.load)['positions']:
            if p['symbol'] == sym: return copy.deepcopy(p)
    except Exception:
        pass
    return None

GATE_FROM = '2026-10-06'  # Kilian 2026-10-06 14:39Z: "no unverified claims can make it into the valuation"

def citation_gate(research, s):
    """'clean' when runs/RESEARCH/SYM/citation_audit.json says clean; None (blocked) when it is missing or not clean for a
    round on or after GATE_FROM, or present but not clean for any round; older rounds without an audit pass as 'not audited'."""
    f = os.path.join(HERE, 'runs', research, s, 'citation_audit.json')
    if os.path.exists(f) and json.load(open(f)).get('clean') is True: return 'clean'
    # 2026-10-26 (Kilian 05:18Z "use the round 1 number and flag it", META): data/review_settings.json gate_override
    # {SYM: {research, why}} lets one named round through UNGATED; the status string says so and the page shows it.
    try: ov = (_jfile(os.path.join(HERE, 'data', 'review_settings.json')).get('gate_override') or {}).get(s) or {}
    except Exception: ov = {}
    if ov.get('research') == research: return f"NOT GATED: {ov.get('why', 'override')}"
    if os.path.exists(f): return None
    return None if research >= GATE_FROM else f'not audited (research {research} predates the citation gate)'

def gated_research(date, s):
    """Newest finished research round on or before DATE that passes the citation gate; blocked rounds are skipped."""
    marker = 'supervisor.json' if glob.glob(os.path.join(HERE, 'runs', '*', s, 'supervisor.json')) else 'agent_1.json'
    ds = sorted({os.path.basename(os.path.dirname(os.path.dirname(f))) for f in glob.glob(os.path.join(HERE, 'runs', '*', s, marker))})
    blocked = []
    for d in reversed([d for d in ds if d <= date]):
        g = citation_gate(d, s)
        if g: return d, g, blocked
        blocked.append(d)
    raise SystemExit(f'{s}: no research on or before {date} passes the citation gate (blocked: {blocked})')

def standard_model(research, s, gate):
    """Gate-clean standard model for this research round, or (None, reason). Kilian 2026-10-19 20:31Z. Needs:
    runs/RESEARCH/SYM/model_engine.json with a valued supervisor set (model_target, model_fair_value), written after the
    reconciled set it values; and EITHER a round run under the deep-modeling briefs (playbook.json from prep_briefs.py,
    deep_model true) whose citation audit is clean, OR the name listed in data/review_settings.json "standard_model_now"
    (Kilian's override), whose reconciled set, if it lives in model_backfill/, needs its own clean
    model_backfill/citation_audit.json; OR a model update round (update.json from name_update.py, 2026-10-21) whose citation
    audit is clean. Sets only backfilled from older multiples work do not switch on their own."""
    if RESEARCH_FV_SOURCE != 'standard_model': return None, 'RESEARCH_FV_SOURCE is supervisor (rollback)'
    rd = os.path.join(HERE, 'runs', research, s); mf = f'{rd}/model_engine.json'
    if not os.path.exists(mf): return None, 'no model_engine.json for this research round'
    try: me = json.load(open(mf))
    except Exception as e: return None, f'model_engine.json unreadable ({str(e)[:80]})'
    if 'supervisor' in (me.get('errors') or {}) or not isinstance(me.get('model_target'), (int, float)) \
            or not isinstance(me.get('model_fair_value'), (int, float)) or me['model_target'] <= 0:
        return None, 'model_engine.json has no valued reconciled set'
    try: now = set(_jfile(os.path.join(HERE, 'data', 'review_settings.json')).get('standard_model_now', []))
    except Exception: now = set()
    pb = f'{rd}/playbook.json'; deep = False
    if os.path.exists(pb):
        try: deep = json.load(open(pb)).get('deep_model') is True
        except Exception: deep = False
    sup_f = f'{rd}/supervisor.json'; bf = f'{rd}/model_backfill/supervisor.json'
    try: sup_has = isinstance(json.load(open(sup_f)).get('model'), dict) and 'reconciled_inputs' in json.load(open(sup_f))['model']
    except Exception: sup_has = False
    set_f = sup_f if sup_has else (bf if os.path.exists(bf) else None)
    if not set_f: return None, 'no reconciled set (supervisor.json model.reconciled_inputs or model_backfill/supervisor.json)'
    import hashlib; _md5 = lambda f: hashlib.md5(open(f, 'rb').read()).hexdigest()
    # 2026-10-19: fresh when the engine recorded this exact set (inputs_md5), else by mtime (network-mount mtimes can reorder).
    # 2026-10-30: also fresh when the model inputs alone are unchanged (inputs_set_hash), so text-only edits do not trigger the fallback
    from model_engine import set_hash
    _rel = os.path.relpath(set_f, rd)
    if ((me.get('inputs_md5') or {}).get(_rel) != _md5(set_f) and (me.get('inputs_set_hash') or {}).get(_rel) != set_hash(set_f)
            and os.path.getmtime(mf) < os.path.getmtime(set_f)):
        return None, f'model_engine.json is older than {os.path.relpath(set_f, rd)}: rerun model_engine.py run'
    if deep and sup_has:
        if gate != 'clean' and not str(gate).startswith('NOT GATED'): return None, f'research gate status {gate!r}, not clean'
        how = 'deep-modeling round (playbook.json), ' + ('clean citation audit' if gate == 'clean' else 'UNGATED by gate_override')
    elif sup_has and os.path.exists(f'{rd}/update.json') and UPDATE_ROUNDS_SET_LEVELS:  # name_update.py round (Kilian 2026-10-21 21:10Z): one modeler, gate on changed rows
        if gate != 'clean': return None, f'research gate status {gate!r}, not clean'
        how = 'model update round (update.json, name_update.py), clean citation audit'
    elif s in now:
        if set_f == bf:
            ca = f'{rd}/model_backfill/citation_audit.json'
            try: _ca = json.load(open(ca)); ok = _ca.get('clean') is True and (_ca.get('set_md5') == _md5(bf) or os.path.getmtime(ca) >= os.path.getmtime(bf))
            except Exception: ok = False
            if not ok: return None, 'standard_model_now, but model_backfill/supervisor.json has no clean citation audit written after it'
        elif gate != 'clean': return None, f'research gate status {gate!r}, not clean'
        how = 'data/review_settings.json standard_model_now override'
    else: return None, 'set predates the deep-modeling briefs (backfilled or older round): switches at its next full run'
    return dict(model_target=me['model_target'], model_fair_value=me['model_fair_value'], quantiles=me.get('model_quantiles'),
                model_ladder=(me.get('model_ladder') or {}).get('ladder'), file=os.path.relpath(mf, HERE), set_file=os.path.relpath(set_f, HERE), basis=how), None

def combine(date, s, research=None):
    if research:
        gate = citation_gate(research, s); blocked = []
        if not gate: raise SystemExit(f'{s}: research {research} has no clean citation_audit.json; result.json not written')
    else:
        research, gate, blocked = gated_research(date, s)
    if blocked: print(f'{s}: citation gate blocked research {blocked}; using {research}')
    ad = os.path.join(HERE, 'runs', research, s)  # agent + supervisor files
    rd = os.path.join(HERE, 'runs', date, s); os.makedirs(rd, exist_ok=True)  # result for this market date
    m = json.load(open(os.path.join(HERE, 'priors', date, f'{s}.json')))
    S, T = m['spot'], m['horizon_years']
    Ks = [l['K'] for l in m['ladder']]; p_mkt = [l['p_p'] for l in m['ladder']]
    agents = [json.load(open(f)) for f in sorted(glob.glob(f'{ad}/agent_*.json'))]
    if not agents: raise SystemExit(f'{s}: no agent files in {ad}')
    # Kilian 2026-10-30 card "Drop 12-month, keep 1-month": forecasters no longer write a 12-month ladder; older rounds keep theirs
    lads = [monotone(read_ladder(a, Ks)) for a in agents if a.get('ladder')]
    sup = json.load(open(f'{ad}/supervisor.json')) if os.path.exists(f'{ad}/supervisor.json') else None
    used_sup = bool(sup and str(sup.get('confidence', '')).lower() == 'high' and sup.get('ladder'))
    if lads:
        mean = [sum(col) / len(col) for col in zip(*lads)]
        disp = [math.sqrt(sum((x - mu)**2 for x in col) / max(len(col) - 1, 1)) for col, mu in zip(zip(*lads), mean)]
    else:  # a ladderless round: the research distribution can only be the reconciled model's own scenario mix
        _sm, _why = standard_model(research, s, gate)
        _ml = (_sm or {}).get('model_ladder')
        if not _ml or len(_ml) < 5:
            raise SystemExit(f"{s}: research {research} has no 12-month ladders and no usable model distribution "
                             f"({_why or 'model_engine.json has no model_ladder: rerun model_engine.py run'}); result.json not written")
        _f = survival_fn([x['K'] for x in _ml], [clip(x['p']) for x in _ml])
        mean = monotone([_f(K) for K in Ks]); disp = [0.0] * len(Ks)
    llm = monotone(read_ladder(sup, Ks)) if used_sup else mean
    # Platt scaling on the directional call only. Scaling every strike would divide the agents' volatility by sqrt(3)
    # (their spread is anchored on realized vol, not hedged), so instead: Platt-scale P(close >= spot) and shift the
    # agents' whole distribution in log price until it matches, keeping its width.
    # Special situations (a pending merger etc.): the supervisor redefines the target (target_definition). A location
    # shift would move the fixed deal consideration, so Platt is skipped; and when the market prior is only the lognormal
    # fallback (low_confidence), it carries little beyond spot and vol, so the weights flip to 1/3 market, 2/3 agents.
    # Kilian 2026-10-15 06:21Z card "Use 67/33": the flip now applies to special situations only. A name without a usable
    # smile (penny strikes, thin chain) keeps 67/33: its lognormal fallback has the same beta-line centre as every name and
    # its width from max(ATM IV, 1Y realized), and options add width, not direction (options backtest 2026-10-05).
    special = bool(used_sup and sup.get('target_definition'))
    w_mkt, w_llm = (W_LLM, W_MKT) if (m.get('low_confidence') and special) else (W_MKT, W_LLM)
    sf_raw = survival_fn(Ks, [clip(p) for p in llm])
    # Kilian 2026-10-17 19:43Z card "Freeze it" (Backtests thread): the shift is set once per research round, at the
    # research day's spot, and reused on every re-mark until the next research run. Recomputing it at each day's spot
    # read the fixed-dollar ladders further from 0.5 after a price move and sqrt(3) stretched that into a buy-the-dip tilt.
    sm, sm_why = standard_model(research, s, gate) if not special else (None, 'special situation: target_definition rules')
    leg = dict(source='supervisor', fallback_reason=sm_why)
    if sm:  # Kilian 2026-10-19 20:31Z: the reconciled standard model is the research leg
        mlad = sm.pop('model_ladder', None)
        leg = dict(source='standard_model', **sm)
        if STD_MODEL_OWN_DIST and mlad and len(mlad) >= 5:  # the model's own 12-month distribution replaces the forecasters' ladder
            sf_raw = survival_fn([x['K'] for x in mlad], [clip(x['p']) for x in mlad]); leg.update(distribution='model base case with downside (P10) and upside (P90)')
        else: leg.update(distribution='forecasters ladder' + (' (no model_ladder in model_engine.json)' if STD_MODEL_OWN_DIST else ''))
        if STD_MODEL_RECENTRE:
            med0 = quantile(sf_raw, .5, S * 0.01, S * 20); rc = math.log(sm['model_target'] / med0)
            # mean-centring: a model target behaves like a mean, so the leg's median sits c x half its log variance below it
            sd_l = math.log(quantile(sf_raw, .9, S * 0.01, S * 20) / quantile(sf_raw, .1, S * 0.01, S * 20)) / 2.5631
            mc = STD_MODEL_MEAN_CENTRE * sd_l ** 2 / 2; rc -= mc
            llm = monotone([sf_raw(K * math.exp(-rc)) for K in Ks]); sf_raw = survival_fn(Ks, [clip(p) for p in llm])
            leg.update(recentre_log=round(rc, 6), ladder_median_before=round(med0, 4), mean_centre_log=round(-mc, 6), leg_sd_log=round(sd_l, 4))
            # harness kept (Kilian 2026-10-19 20:42Z): Platt still applies, frozen per research round, on the re-centred leg
            if STD_MODEL_PLATT: shift, ref = research_shift(research, s, sf_raw, special, used_sup, S, leg='standard_model')
            else: shift, ref = 0.0, dict(research=research, research_leg='standard_model', log_shift=0.0, skipped='no Platt on a standard-model leg (Kilian 2026-10-26 16:12Z)')
        else: shift, ref = research_shift(research, s, sf_raw, special, used_sup, S)
    else: shift, ref = research_shift(research, s, sf_raw, special, used_sup, S)
    p_up = sf_raw(S); p_up_platt = sf_raw(S * math.exp(-shift))
    llm_platt = monotone([sf_raw(K * math.exp(-shift)) for K in Ks])
    lo, hi = S * 0.01, S * 20
    sf_m = survival_fn(Ks, p_mkt); sf_l = survival_fn(Ks, llm_platt)
    # Analyst consensus as a third, small-weight input: the market distribution re-centred on the analysts' median
    # target (width from the options market, since a target is only a point). Weight W_ANALYST comes pro rata out of
    # the other two. Skipped for special situations and names without published targets.
    an = analyst(s)
    p_an = None
    if an and an.get('median') and not special:
        a_shift = math.log(an['median'] / quantile(sf_m, .5, lo, hi))
        p_an = monotone([sf_m(K * math.exp(-a_shift)) for K in Ks])
    ex_an = monotone([w_mkt*pm + w_llm*pl for pm, pl in zip(p_mkt, llm_platt)])
    w_an = W_ANALYST if p_an else 0.0
    final = monotone([(1 - w_an)*pe + w_an*pa for pe, pa in zip(ex_an, p_an)]) if p_an else ex_an
    # EPS-revision tilt: shift the whole blended distribution in log price (target, mean, TP, stop all move together)
    rev = None if special else revision_tilt(date, s)
    # Short-interest days-to-cover tilt (Kilian 2026-10-19): a second log location shift, added to the EPS one and applied
    # in the same resampling; sized at quarter Kelly and not scaled by conviction (conviction.py), like the EPS tilt.
    si = None if special else short_interest_tilt(date, s)
    # 12-1 momentum tilt (Kilian 2026-10-30): bottom-fifth losers -3%, a third shift in the same resampling, not scaled by conviction
    mo = None if special else momentum_tilt(date, s)
    tilt_log = ((rev or {}).get('log_shift') or 0.0) + ((si or {}).get('log_shift') or 0.0) + ((mo or {}).get('log_shift') or 0.0)
    if tilt_log:
        sf_pre = survival_fn(Ks, [clip(p) for p in final])
        final = monotone([sf_pre(K * math.exp(-tilt_log)) for K in Ks])
    sf = survival_fn(Ks, final)
    qs = {f'q{q}': quantile(sf, q/100, lo, hi) for q in (5, 10, 15, 25, 50, 75, 85, 90, 95)}
    # mean via E[S_T] = integral_0^inf P(S_T >= K) dK
    grid = [S * math.exp(-4 + 8*i/2000) for i in range(2001)]
    mean_px = grid[0] + sum((sf(grid[i]) + sf(grid[i-1]))/2 * (grid[i] - grid[i-1]) for i in range(1, len(grid)))
    tb = [a.get('thesis_break_price') for a in agents if isinstance(a.get('thesis_break_price'), (int, float))]
    tb_mean = sorted(tb)[len(tb)//2] if tb else None
    tb_final = sup['thesis_break_price'] if sup and isinstance(sup.get('thesis_break_price'), (int, float)) else tb_mean
    v30, v30_date = iv30(s, date)
    v30 = v30 or m['atm_iv_1y']
    floor = S * math.exp(-2 * v30 * math.sqrt(21/252))
    stop_raw = max(qs['q15'], tb_final) if tb_final and tb_final < S else qs['q15']
    stop = min(stop_raw, floor)
    tp = qs['q75']; tgt = qs['q50']
    fv = [a['fair_value']['value'] for a in agents if isinstance(a.get('fair_value', {}).get('value'), (int, float))]
    # Fair value: the supervisor's reconciled one when it gives it (it fact-checks the agents' inputs), else the agents' median
    sfv = (sup or {}).get('fair_value') if isinstance((sup or {}).get('fair_value'), dict) else None
    if sm:
        fv_final, fv_src, fv_reason = round(sm['model_fair_value'], 4), 'standard_model', f"model_engine reconciled set ({sm['basis']}); supervisor narrative {sfv.get('value') if sfv else None}"
    elif sfv and isinstance(sfv.get('value'), (int, float)):
        fv_final, fv_src, fv_reason = sfv['value'], 'supervisor', sfv.get('reason')
    else:
        fv_final, fv_src, fv_reason = (sorted(fv)[len(fv)//2] if fv else None), 'agents median', None
    res = dict(symbol=s, as_of=m['market_date'], horizon=m['horizon'], spot=S,
               target=round(tgt, 4), expected=round(mean_px, 4), exp_return=round(mean_px/S - 1, 4),
               p_gain=round(sf(S), 4), take_profit=round(tp, 4), stop_loss=round(stop, 4),
               stop_basis='noise floor (2 x 1M IV)' if stop < stop_raw else ('thesis break' if tb_final and stop_raw == tb_final else '15th percentile'),
               p_touch_tp=round(touch_prob(S, tp, T, sf, tgt), 3), p_touch_sl=round(touch_prob(S, stop, T, sf, tgt), 3),
               reward_risk=round((tp - S) / (S - stop), 2) if stop < S else None,
               quantiles={k: round(v, 4) for k, v in qs.items()},
               analysts=dict(an or {}, weight=w_an, target_ex_analysts=round(quantile(survival_fn(Ks, ex_an), .5, lo, hi), 4)),
               market_only=dict(target=round(quantile(sf_m, .5, lo, hi), 4), q15=round(quantile(sf_m, .15, lo, hi), 4), q75=round(quantile(sf_m, .75, lo, hi), 4), p_gain=round(sf_m(S), 4)),
               llm_only=dict(target=round(quantile(sf_l, .5, lo, hi), 4), q15=round(quantile(sf_l, .15, lo, hi), 4), q75=round(quantile(sf_l, .75, lo, hi), 4), p_gain=round(sf_l(S), 4)),
               agents=len(agents), agent_targets=[round(quantile(survival_fn(Ks, [clip(p) for p in l]), .5, lo, hi), 2) for l in lads],
               research_as_of=research, agents_horizon=(agents[0].get('horizon') or None),
               fair_values=fv, fair_value_agents=sorted(fv)[len(fv)//2] if fv else None,
               fair_value_median=fv_final, fair_value_source=fv_src, fair_value_reason=fv_reason,
               fair_value_gap=round(fv_final / S - 1, 4) if fv_final else None,
               fair_value_range=[min(fv), max(fv)] if fv else None,
               thesis_break=dict(agents=tb, reconciled=tb_final), supervisor_used=used_sup,
               supervisor_confidence=sup and sup.get('confidence'), supervisor_summary=sup and sup.get('summary'),
               key_drivers=sup and sup.get('key_drivers'), iv30=v30, iv30_date=v30_date, atm_iv_1y=m['atm_iv_1y'],
               low_confidence=m.get('low_confidence'),
               ladder=[dict(K=K, market=round(pm, 4), llm_mean=round(pmn, 4), llm_sd=round(d, 4), llm_used=round(pl, 4),
                            llm_platt=round(pp, 4), final=round(pf, 4))
                       for K, pm, pmn, d, pl, pp, pf in zip(Ks, p_mkt, mean, disp, llm, llm_platt, final)],
               platt=dict(p_up_raw=round(p_up, 4), p_up_platt=round(p_up_platt, 4), log_shift=round(shift, 4), frozen_at=ref),
               revision=rev, short_interest=si, momentum=mo,
               method=dict(ensemble='mean of independent forecasters', supervisor='replaces mean only on high confidence', platt_applied_to='P(close >= research-day spot), as a location shift frozen per research round',
                           platt_a=round(A_PLATT, 4), w_market=w_mkt, w_llm=w_llm, special_situation=special,
                           target_definition=sup.get('target_definition') if special else None))
    h = holding(s)
    if h:
        sh = h['shares']
        res['position'] = dict(shares=sh, cost_basis=h['cost_basis'], market_value=round(sh*S, 2),
                               unrealized=round(sh*S - h['cost_basis'], 2), to_target=round(sh*(tgt - S), 2),
                               to_take_profit=round(sh*(tp - S), 2), to_stop=round(sh*(stop - S), 2))
    res['citation_gate'] = dict(status=gate, research=research, blocked_newer=blocked)
    res['research_leg'] = leg
    if DRY: return res
    json.dump(res, open(f'{rd}/result.json', 'w'), indent=1)
    log = os.path.join(HERE, 'forecast_log.csv')
    cols = ['as_of', 'horizon', 'symbol', 'spot', 'K', 'p_market', 'p_llm_mean', 'p_llm_platt', 'p_analyst', 'p_final', 'supervisor_used']
    new = [dict(as_of=res['as_of'], horizon=res['horizon'], symbol=s, spot=S, K=l['K'], p_market=l['market'],
                p_llm_mean=l['llm_mean'], p_llm_platt=l['llm_platt'], p_analyst=pa and round(pa, 4), p_final=l['final'],
                supervisor_used=used_sup) for l, pa in zip(res['ladder'], p_an or [None]*len(Ks))]
    if _LOG_OPS is not None: _LOG_OPS.setdefault(s, []).append(((res['as_of'], s, res['horizon']), cols, new))
    else:
        keep = [r for r in csv.DictReader(open(log))] if os.path.exists(log) else []
        keep = [r for r in keep if not (r['as_of'] == res['as_of'] and r['symbol'] == s and r['horizon'] == res['horizon'])]  # a re-run replaces its own rows
        with open(log, 'w', newline='') as f:
            w = csv.DictWriter(f, fieldnames=cols, extrasaction='ignore'); w.writeheader(); w.writerows(keep); w.writerows(new)
    one_month(date, research, s, agents, sup, used_sup, res)
    return res

W1M_MARKET = 0.67  # same split as the 12-month blend until the 1-month scores say otherwise

def one_month(date, research, s, agents, sup, used_sup, res):
    """1-month forecast (Kilian 2026-09-29), logged only on research days so every row is a fresh call: options-implied
    1-month distribution (priors/DATE/1m, market_dist.py --horizon-days 30) pooled 67/33 with the agents' ladder_1m (mean
    of the forecasters, or the supervisor's when it reports high confidence). No Platt shift or analysts. Rows go to
    forecast_log.csv with a horizon about 30 days out; scoring/score_live.py scores them within weeks."""
    p1 = os.path.join(HERE, 'priors', date, '1m', f'{s}.json')
    if research != date or not os.path.exists(p1) or not all(a.get('ladder_1m') for a in agents): return None
    m = json.load(open(p1)); Ks = [l['K'] for l in m['ladder']]; p_mkt = [l['p_p'] for l in m['ladder']]
    lads = [monotone(read_ladder({'ladder': a['ladder_1m']}, Ks)) for a in agents]
    mean = [sum(c) / len(c) for c in zip(*lads)]
    hi1 = bool(sup and str(sup.get('confidence', '')).lower() == 'high')  # 2026-10-30: ladderless rounds keep the high-confidence 1-month override
    llm = monotone(read_ladder({'ladder': sup['ladder_1m']}, Ks)) if (used_sup or hi1) and sup.get('ladder_1m') else mean
    fin = monotone([W1M_MARKET*a + (1 - W1M_MARKET)*b for a, b in zip(p_mkt, llm)])
    res['one_month'] = dict(horizon=m['horizon'], ladder=[dict(K=K, market=round(a, 4), llm=round(b, 4), final=round(c, 4))
                                                          for K, a, b, c in zip(Ks, p_mkt, llm, fin)])
    json.dump(res, open(os.path.join(HERE, 'runs', date, s, 'result.json'), 'w'), indent=1)
    log = os.path.join(HERE, 'forecast_log.csv')
    new = [dict(as_of=res['as_of'], horizon=m['horizon'], symbol=s, spot=m['spot'], K=K, p_market=round(a, 4),
                p_llm_mean=round(mn, 4), p_llm_platt=round(b, 4), p_analyst='', p_final=round(c, 4), supervisor_used=used_sup)
           for K, a, b, mn, c in zip(Ks, p_mkt, llm, mean, fin)]
    if _LOG_OPS is not None: _LOG_OPS.setdefault(s, []).append(((res['as_of'], s, m['horizon']), None, new)); return res['one_month']
    rows = list(csv.DictReader(open(log)))
    rows = [r for r in rows if not (r['as_of'] == res['as_of'] and r['symbol'] == s and r['horizon'] == m['horizon'])]
    rows += new
    with open(log, 'w', newline='') as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys())); w.writeheader(); w.writerows(rows)
    return res['one_month']

class _ThreadOut(io.TextIOBase):
    """stdout that each worker thread writes into its own buffer, so every name's lines print together and in order."""
    def __init__(self, real): self.real, self.local = real, threading.local()
    def write(self, t):
        b = getattr(self.local, 'buf', None)
        return (b if b is not None else self.real).write(t)
    def flush(self): self.real.flush()


def _write_log(ops_in_order):
    """Apply each name's forecast_log.csv changes in the given order, as the one-name-at-a-time loop did, and write once."""
    log = os.path.join(HERE, 'forecast_log.csv')
    rows = [r for r in csv.DictReader(open(log))] if os.path.exists(log) else []
    fields = None
    for (as_of, s, hz), cols, new in ops_in_order:
        rows = [r for r in rows if not (r['as_of'] == as_of and r['symbol'] == s and r['horizon'] == hz)] + new
        fields = cols or fields or (list(rows[0].keys()) if rows else None)
    if fields is None: return
    with open(log, 'w', newline='') as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction='ignore'); w.writeheader(); w.writerows(rows)


if __name__ == '__main__':
    args = sys.argv[1:]; research = None
    if '--research' in args: i = args.index('--research'); research = args[i+1]; del args[i:i+2]
    if '--dry-run' in args: args.remove('--dry-run'); DRY = True
    jobs = 12
    if '--jobs' in args: i = args.index('--jobs'); jobs = int(args[i+1]); del args[i:i+2]
    date, syms = args[0], list(dict.fromkeys(x.upper() for x in args[1:]))
    _LOG_OPS = {}
    out = _ThreadOut(sys.stdout); sys.stdout = out

    def _run(s):
        out.local.buf = io.StringIO()
        try: return s, combine(date, s, research), None, out.local.buf
        except SystemExit as e: return s, None, e, out.local.buf
        except Exception as e: return s, None, e, out.local.buf
        finally: out.local.buf = None

    import concurrent.futures as cf
    failed = None
    with cf.ThreadPoolExecutor(max(1, min(jobs, len(syms) or 1))) as ex:
        done = list(ex.map(_run, syms))
    ops = []
    for s, r, err, buf in done:
        print(buf.getvalue(), end='')
        ops += _LOG_OPS.get(s, []); _LOG_OPS[s] = []  # rows a name logged before failing were written by the old loop too
        if isinstance(err, SystemExit): print('BLOCKED', err); continue
        if err is not None:
            import traceback; print(f'{s}: FAILED', ''.join(traceback.format_exception(err)).strip()); failed = failed or err; continue
        print(f"{s:6} spot {r['spot']:.2f}  target {r['target']:.2f} ({r['target']/r['spot']-1:+.1%})  TP {r['take_profit']:.2f}  "
              f"SL {r['stop_loss']:.2f} [{r['stop_basis']}]  P(gain) {r['p_gain']:.0%}  mkt tgt {r['market_only']['target']:.2f}  "
              f"llm tgt {r['llm_only']['target']:.2f}  agents {r['agent_targets']}  sup={r['supervisor_confidence']}"
              + (f"  research leg: standard model {r['research_leg']['model_target']:.2f}" if r['research_leg']['source'] == 'standard_model' else '')
              + ('  [dry run: nothing written]' if DRY else ''))
    if not DRY and ops: _write_log(ops)
    sys.stdout = out.real
    if failed: raise SystemExit(1)
