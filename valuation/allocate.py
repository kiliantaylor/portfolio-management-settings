"""Suggested allocation from the cash to deploy: one joint solve over every name (optimize.py), run after combine.py.

Since 2026-09-29 (Kilian: "solve as one portfolio against the factor model") all candidates are sized together:
    maximize sum_i w_i (mu_i - rf) - (gamma/2) w' Sigma w,   gamma = 4 (quarter Kelly),
with w the share of total wealth (current account + new cash), mu_i the conviction-adjusted 1Y expected return
(conviction.py; the options market's part at quarter Kelly, the research part scaled 0.5x-2x), and
Sigma_ij = rho_ij s_i s_j: s_i the s.d. of the name's final blended 1Y distribution (result.json, the same one that sets
target, TP and SL), rho_ij from the Risk Prism factor model recalibrated to the option market (riskcal.py), else 2Y
realized correlation shrunk toward the pool average. Constraints per name, the smallest binding:
  - Gate: no buy unless the NTM target (blended median) is above the last price ('target_above_last').
  - Stop budget: a stop-out at the SL may cost at most stop_budget_share_of_wealth of total wealth (data/review_settings.json;
    5% since Kilian 2026-09-29, was 2%): w <= budget / ((spot - SL) / spot).
  - The supervisor's sizing.max_weight_pct, if given, and a 20% single-name ceiling.
  - Index fund (review_settings index_fund, SPY since Kilian 2026-09-29): an S&P 500 fund is always a candidate, expected
    to earn the risk-free rate plus the measured 7.69% equity premium with its option-implied vol; no stop, supervisor or conviction caps, only
    its own max_weight. It soaks up cash the stock picks leave idle, sized against them in the same solve.
  - Kelly split (Kilian 2026-09-29): the options-implied part of each expected return is sized at market_kelly_fraction
    (half Kelly), the research part at quarter Kelly; done by solving with mu_solve = rf + MKF*gamma*(m - rf) + alpha.
    Reported expected returns are the true mu, not mu_solve.
  - Conviction: no buy unless the supervisor's sizing.conviction is at least min_supervisor_conviction
    (data/review_settings.json; 'medium' from Kilian 2026-09-29; null = off since Kilian 2026-10-21 'get rid of the
    rubric', so only the Sharpe tests gate buys). While it is set, a name with no rating gets no buy.
  - Big-miss block (Kilian 2026-10-19 card 'Add flag'): no buy, and a staged new name's buy pauses, for 6 months after a
    big earnings miss (earnings_miss.py -> data/earnings_miss.json; gate 'big_miss_block').
  - Held names (Kilian 2026-10-19 15:21Z, SIZING = 'max_sharpe'): review.py sizes them to Sharpe-optimal (tangency)
    targets on mu_eff, scaled to the total this rule would hold; here they stay fixed and a new name must still raise
    the book's Sharpe (Sharpe test below).
Across the book: total buys <= cash. An optional industry limit (max_industry_risk_share; null = off, Kilian removed
the 40% limit 2026-09-29) caps any one Risk Prism industry's share of the book's variance. Held names (holdings mode) stay fixed at
their current weight; review.py decides trims and adds for those. Whole shares: rounded down, then single shares added
back while they raise the objective and fit the cash and limits. The rest stays in cash.
runs/DATE/portfolio.json carries the book's expected return, volatility (recalibrated model, plus the raw model and the
option market's own view), Sharpe, beta, factor and industry risk shares.

Usage: python3 allocate.py DATE [SYM ...]   (default: every runs/DATE/<SYM>/result.json for a symbol not held)
Writes runs/DATE/SYM/allocation.json and runs/DATE/portfolio.json, prints one line per name."""
import glob, csv, glob, json, math, os, sys
HERE = os.path.dirname(os.path.abspath(__file__)); PORT = os.path.join(HERE, '..', 'portfolio')
# Kilian 2026-10-19 15:21Z ("Switch book sizing to Sharpe-optimal allocation given among held names based on our expected
# return"): review.py sizes held names to the long-only tangency portfolio of the held names on mu_eff, scaled to the total
# the mean-variance rule would hold in them. 'mean_variance' rolls back. New names here are unchanged (gates, rubric, Sharpe test).
SIZING = 'max_sharpe'  # 'max_sharpe' | 'mean_variance'; the environment variable SIZING overrides it for one run
TRIM_PACE_SHARES = None  # max_sharpe: a trim toward a lower target sells at most this many shares a day (None: no pace).
# Kilian 2026-10-20 14:34Z card 'Buy to size': no pace on either side (no evidence for pacing). Was 1 (Claude 10-19).
ADD_PACE_SHARES = None  # None: the next-share table buys the whole Sharpe-best count at today's price in one order; 1: one share a day (old)
BAR_BASIS = 'target'  # Kilian 2026-10-22 19:39Z card 'Use Entry below': the beta-adjusted floor tests the target's 12-month return plus dividend yield (price below the page's Entry below); 'mean' = mu_eff (00:47Z-19:39Z)
ADD_TEST = 'whole_book'  # Kilian 2026-10-22 18:42Z card 'Whole-book test + target Sharpe': held-name adds tested on the same whole-book objective the targets are solved on (cash at rf), so add levels agree with the Sharpe-optimal targets; the stock-only test blocked adds that only looked lopsided between staged dates (AMAT target 6.8 sh, first add 353.73 at 10-21 marks). Earlier: Kilian 2026-10-21 08:17Z card 'Use book Sharpe for adds too': held-name adds (and staged tranches) must raise the page's invested-basis book Sharpe, the test new buys use. Was 'whole_book' from Kilian 2026-10-20 card 19:34Z 'Whole-book test': adds to held names tested on whole-book utility (cash sleeve + 15% cap) under max_sharpe; 'invested_sharpe' = old stocks-only Sharpe at cost
ZERO_TARGET_HOLD = True  # max_sharpe: a held name whose target is 0 is held (no trim) until its staged date or the day after
                         # its next earnings report (data/earnings_miss.json next_report), whichever is first; then it trims at
                         # TRIM_PACE_SHARES a day (None: all at once) if the target is still 0. False: trim from today (coordinator 2026-10-19, option b)
KELLY_FRAC, CEILING = 0.25, 0.15  # Kilian 2026-10-20 card 16:44Z 'Flat 15%': no name above 15% of the $24k sizing book (cap test sizing_backtest/results/cap_test_README.md: ~1.5/N best with a no-skill research tilt; 10 names -> 15%). History: 20% until 10-13, none 10-13..10-20 (ABT then came out 27% under the stop budget alone); the 5% stop-out budget still applies
try: STOP_BUDGET = json.load(open(os.path.join(HERE, 'data', 'review_settings.json'))).get('stop_budget_share_of_wealth', 0.02)
except Exception: STOP_BUDGET = 0.02
sys.path.insert(0, os.path.join(HERE, '..', 'risk'))
try:
    import riskcalc; RM = riskcalc.load()
except Exception as e:  # no model yet: realized correlations only
    print('risk model unavailable, using realized correlations:', e); RM = None

def closes(s):
    for d in (os.path.join(HERE, 'data'), os.path.join(PORT, 'data')):
        p = f'{d}/daily_{s}.csv'
        if os.path.exists(p): return {r['date']: float(r['close']) for r in csv.DictReader(open(p))}
    return None

def corr(a, b):
    ds = sorted(set(a) & set(b))[-505:]
    x = [math.log(a[ds[i]]/a[ds[i-1]]) for i in range(1, len(ds))]; y = [math.log(b[ds[i]]/b[ds[i-1]]) for i in range(1, len(ds))]
    mx, my = sum(x)/len(x), sum(y)/len(y)
    c = sum((u-mx)*(v-my) for u, v in zip(x, y)); vx = sum((u-mx)**2 for u in x); vy = sum((v-my)**2 for v in y)
    return c / math.sqrt(vx*vy), len(x)

def vol(s, date):
    p = os.path.join(HERE, 'priors', date, f'{s}.json')
    if os.path.exists(p):
        m = json.load(open(p))
        if m.get('atm_iv_1y'): return m['atm_iv_1y']
    c = closes(s); v = list(c.values())[-253:]; r = [math.log(v[i]/v[i-1]) for i in range(1, len(v))]; mu = sum(r)/len(r)
    return math.sqrt(sum((x-mu)**2 for x in r)/(len(r)-1)*252)

def rf():
    # Kilian 2026-10-20 04:31Z: "model a risk-free sleeve yielding 4% apy and resolve for the sharpe-optimal allocations from
    # there". Cash outside stocks sits in that sleeve, so its yield is the hurdle every excess return and Sharpe is measured
    # against (data/review_settings.json cash_sleeve_apy; drop the key to go back to the 3M T-bill).
    try:
        v = json.load(open(os.path.join(HERE, 'data', 'review_settings.json'))).get('cash_sleeve_apy')
        if v is not None: return float(v)
    except Exception: pass
    try: return float(next(csv.DictReader(open(f'{PORT}/data/tbill_3m_weekly.csv')))['yield_pct']) / 100
    except Exception: return 0.04

def beta_bar(s, date):
    """Kilian 2026-10-22 00:47Z ("let's use the adjusted expected return by beta as a threshold"; NEE passed the Sharpe test on
    low volatility alone, 00:37Z "not good enough return for me to add to the book"): a buy must expect more than the market
    pays for its beta, T-bill + 7.69% x (0.768 + 0.232 beta) (French beta deciles), = priors rf + premium (market_dist.py).
    Total return, like mu_eff. None when the name has no priors for the date (no bar applied)."""
    try: p = json.load(open(os.path.join(HERE, 'priors', date, s + '.json'))); return p['rf'] + p['premium']
    except Exception: return None

def bar_return(s, mu_s, target, P):
    """The return the beta-adjusted floor tests (BAR_BASIS): the target's 12-month return at price P plus the dividend yield, or mu_eff ('mean')."""
    if BAR_BASIS == 'target':
        import divs; return target / P - 1 + divs.dy(s, P)
    return mu_s

def moments(res):
    """Arithmetic 1Y return mean and s.d. from the blended distribution (lognormal fit to q10/q50/q90, mean from result)."""
    q, S = res['quantiles'], res['spot']
    s = (math.log(q['q90']) - math.log(q['q10'])) / (2*1.2816)
    mu = res['expected']/S - 1
    import divs  # Kilian 2026-10-12: total return = price return + trailing dividend yield (targets stay ex-dividend prices)
    return mu + divs.dy(res.get('symbol', ''), S), (1 + mu) * math.sqrt(math.exp(s*s) - 1)

if __name__ == '__main__':
    import fscache; fscache.install()  # 2026-10-21: read-once cache for the shared network folder (fscache.py; same output)
    date, syms = sys.argv[1], [s.upper() for s in sys.argv[2:]]
    import book, riskcal, optimize; H = book.load(); held = {p['symbol']: p['shares'] for p in H['positions']}  # data/book.json
    if not syms:
        syms = [os.path.basename(os.path.dirname(p)) for p in sorted(glob.glob(os.path.join(HERE, 'runs', date, '*', 'result.json')))]
        syms = [s for s in syms if s not in held]
    IDX = (json.load(open(os.path.join(HERE, 'data', 'review_settings.json'))).get('index_fund') or {})
    if IDX.get('symbol') and IDX['symbol'] not in syms and IDX['symbol'] not in held: syms.append(IDX['symbol'])
    cfg = json.load(open(os.path.join(HERE, 'data', 'review_settings.json')))
    GAMMA = float(cfg.get('risk_aversion_gamma') or 1 / KELLY_FRAC); MAX_IND = cfg.get('max_industry_risk_share')
    MIN_CONV = cfg.get('min_supervisor_conviction'); RANK = {'low': 0, 'medium': 1, 'high': 2}
    cash = H.get('cash_to_deploy_external', 0) + H['cash_in_account']
    last = {s: list(closes(s).items())[-1][1] for s in held}
    mv = {s: held[s]*last[s] for s in held}; W = sum(mv.values()) + cash
    w_h = {s: mv[s]/W for s in held}
    try: RM = riskcal.load(date, syms + list(held))
    except Exception as e: print('recalibrated risk model unavailable:', e)
    names = list(held) + syms
    pool = list(held) if len(held) > 1 else syms  # from scratch: average realized correlation among the candidates
    rho_pairs = [corr(closes(a), closes(b))[0] for i, a in enumerate(pool) for b in pool[i+1:]]
    rho_bar = sum(rho_pairs)/len(rho_pairs); r_f = rf()
    import conviction; CONV = conviction.fractions(date)  # agents' view scaled by conviction (conviction.py)
    import earnings_miss; BM = earnings_miss.blocks(date)  # Kilian 2026-10-19 card 'Add flag': no adds for 6 months after a big earnings miss
    if BM: print('big-miss add block:', {x: v['block_until'] for x, v in BM.items()})
    res, mu, mu_raw, sig, caps, sizing, BAR = {}, {}, {}, {}, {}, {}, {}
    for s in names:
        rp = os.path.join(HERE, 'runs', date, s, 'result.json')
        if s == IDX.get('symbol'):  # index fund (Kilian 2026-09-29): T-bill + equity premium, vol = its option-implied vol
            S = list(closes(s).items())[-1][1]; mu[s] = mu_raw[s] = r_f + IDX.get('erp', 0.0769)
            sig[s] = (RM.calib['market_iv'] if RM and getattr(RM, 'calib', None) else vol(s, date))
            res[s] = dict(spot=S, target=S*(1 + mu[s]), stop_loss=None, index_fund=True)
        elif os.path.exists(rp):
            res[s] = json.load(open(rp)); m_, sig[s] = moments(res[s])
            mu_raw[s] = m_; mu[s] = (CONV.get(s) or {}).get('mu_eff', m_)
        else:  # a holding with no run today: carries risk, expected to earn rf
            sig[s] = vol(s, date); mu[s] = mu_raw[s] = r_f
    _SUPS = {}  # every runs/*/SYM/supervisor.json, listed once (a glob per name took ~2 minutes on the shared folder)
    for f in sorted(glob.glob(os.path.join(HERE, 'runs', '*', '*', 'supervisor.json'))): _SUPS.setdefault(f.split(os.sep)[-2], []).append(f)
    import rubric; rubric.warm([s for s in syms if s != IDX.get('symbol')])  # with fscache on: reads the rubric's files 12 names at a time
    for s in syms:
        if s == IDX.get('symbol'): sizing[s] = {}; caps[s] = {'index_max': IDX.get('max_weight', 1.0)}; continue
        r = res[s]; sup_p = os.path.join(HERE, 'runs', r.get('research_as_of') or date, s, 'supervisor.json')  # research may predate a re-mark
        sizing[s] = (json.load(open(sup_p)).get('sizing') if os.path.exists(sup_p) else None) or {}
        sp2 = os.path.join(os.path.dirname(sup_p), 'sizing_pass.json')  # sizing-only pass for names researched while held
        if not sizing[s] and os.path.exists(sp2): sizing[s] = dict(json.load(open(sp2)), source='sizing pass 2026-09-29')
        old_label = sizing[s].get('conviction_label_before') or sizing[s].get('conviction')  # the supervisor's own label (pre-Sharpe scale)
        import sup_confidence; sizing[s] = sup_confidence.annotate(sizing[s])  # Platt-scaled 0-1 Sharpe confidence, shown as information
        if cfg.get('conviction_scale', 'old') == 'old' and old_label:  # Kilian 2026-10-01 13:40Z chose 'Old scale' for the gate
            sizing[s] = dict(sizing[s], conviction=old_label, conviction_source='supervisor label (old scale)')
        _new = _SUPS.get(s, [])  # Kilian 2026-10-05: a newer research round's
        import combine as _cb; _new = [f for f in _new if _cb.citation_gate(f.split('/')[-3], s)]  # 2026-10-06: gated research only
        if _new and _new[-1].split('/')[-3] > (r.get('research_as_of') or date):  # conviction gates the buy before the next re-mark picks it up
            _sz = json.load(open(_new[-1])).get('sizing') or {}; _lab = _sz.get('conviction_label_before') or _sz.get('conviction')
            if _lab: sizing[s] = dict(sizing[s], conviction=_lab, conviction_source=f"newer research {_new[-1].split('/')[-3]}")
        if cfg.get('conviction_scale') == 'rubric':  # Kilian 2026-10-05 15:42Z: label computed from p, upside, reward/risk (rubric.py)
            import rubric; _rb = rubric.label(s)
            if _rb: sizing[s] = dict(sizing[s], conviction=_rb['label'], rubric=_rb, conviction_source='rubric' + (f" (override: {_rb['override_reason']})" if _rb['override_reason'] else ''))
        S, SL = r['spot'], r['stop_loss']
        c = {'stop_budget': STOP_BUDGET / ((S - SL)/S), 'ceiling': CEILING}
        if sizing[s].get('max_weight_pct') and cfg.get('use_supervisor_max_weight', True): c['supervisor'] = sizing[s]['max_weight_pct'] / 100  # off: Kilian 2026-10-07 'the per-name cap is dropped'
        if r['target'] <= S: c['target_above_last'] = 0.0  # gate (Kilian, 2026-09-27)
        if MIN_CONV and s not in (cfg.get('conviction_exceptions') or []) and RANK.get(sizing[s].get('conviction'), -1) < RANK[MIN_CONV]:  # exceptions: Kilian's picks  # Kilian 2026-09-29: only 'medium' or better
            c['conviction_below_min'] = 0.0
        if s in (cfg.get('excluded_names') or []): c['excluded'] = 0.0  # Kilian 2026-10-01: MCHP out (moves too much with AVGO)
        if s in (cfg.get('news_holds') or {}): c['news_hold'] = 0.0  # 2026-10-09: verified news outside the research holds a new buy until a gate-clean re-run (same rule as held names' trims/adds)
        if s in (cfg.get('saved_for_later') or []): c['saved_for_later'] = 0.0  # Kilian 2026-09-30: medium names parked, not bought
        _st = (cfg.get('staged_entries') or {}).get(s)  # Kilian 2026-10-03: a new name staged for after its print (APP after Q3)
        if _st and _st.get('new_name') and date < _st['after']: c['staged_wait'] = 0.0
        if s in BM: c['big_miss_block'] = 0.0  # 2026-10-19 'Add flag': big earnings miss, no buy until BM[s]['block_until'] (earnings_miss.py)
        if s in (cfg.get('limit_orders') or {}) and s not in w_h: c['limit_order'] = 0.0  # Kilian 2026-10-03: AMZN GTC limit at $230
        BAR[s] = beta_bar(s, date)  # Kilian 2026-10-22 00:47Z: expected return (mu_eff, the Sharpe test's) must beat the beta-adjusted return
        if BAR[s] is not None and bar_return(s, mu[s], r['target'], S) <= BAR[s]: c['below_beta_return'] = 0.0
        caps[s] = c
    rho, src = {}, {}
    for a in names:
        for b in names:
            if a == b: rho[a, b] = 1.0; continue
            if RM and RM.covered(a) and RM.covered(b): rho[a, b], src[a, b] = RM.corr(a, b), 'model'
            else:
                r_, n = corr(closes(a), closes(b)); k = min(n/504, 1); rho[a, b], src[a, b] = k*r_ + (1-k)*rho_bar, 'realized'
    C = [[rho[a, b]*sig[a]*sig[b] for b in names] for a in names]
    # Kilian 2026-10-15 05:33Z card "Use increment": the rubric's p is the change in P(book's 12M Sharpe beats SPY's) when a
    # name joins the book at its solo Sharpe-test size (book_dp.py), not the supervisor's stock-vs-SPY probability. It needs
    # the covariance, so labels are set here and the conviction gate re-applied.
    if cfg.get('conviction_scale') == 'rubric':
        import book_dp, rubric
        _tr0 = json.load(open(os.path.join(HERE, 'data', 'trades.json'))); _tr0 = _tr0.get('trades', _tr0) if isinstance(_tr0, dict) else _tr0
        _c0 = {}
        for t in sorted(_tr0, key=lambda t: t.get('date', '')):
            q, c_ = _c0.get(t['symbol'], (0, 0.0))
            if t.get('side', 'buy') == 'buy': q, c_ = q + t['shares'], c_ + t['shares']*t['price'] + (0 if cfg.get('sizing_ignores_past_fees') else t.get('fees', 0))
            else: c_, q = c_*(q - t['shares'])/q if q else 0.0, q - t['shares']
            _c0[t['symbol']] = (q, c_)
        _base = {x: (_c0[x][1]*held[x]/_c0[x][0] if x in _c0 and _c0[x][0] else mv[x]) for x in held}
        _com = {x: (st_['full_shares'], res[x]['spot']) for x, st_ in (cfg.get('staged_entries') or {}).items()
                if isinstance(st_, dict) and st_.get('new_name') and not st_.get('model_sized') and x in syms and x not in w_h and x not in BM}  # a big-miss block pauses the staged buy
        _com.update({x: (lo_['shares'], lo_['limit']) for x, lo_ in (cfg.get('limit_orders') or {}).items() if x in syms and x not in w_h})
        _base.update({x: n*px for x, (n, px) in _com.items()})
        _cash_c = cash - sum(n*px for n, px in _com.values())
        _cand = {x: res[x]['spot'] for x in syms if x not in w_h and x not in _com and x != IDX.get('symbol')}
        _ub = {x: int(min(min(v for k_, v in caps[x].items() if k_ in ('stop_budget', 'ceiling', 'supervisor')) * W, max(_cash_c, 0)) // _cand[x]) for x in _cand}
        _spy_vol = (RM.calib['market_iv'] if RM is not None and getattr(RM, 'calib', None) else riskcal.market_iv(date)[0])
        _spy_c = closes('SPY')
        def _cs(x):
            if RM is not None and RM.covered(x): return RM.corr(x, 'SPY')
            return corr(closes(x), _spy_c)[0]
        _dp = book_dp.run(date, names, C, mu, r_f, _base, _cand, _ub, _cs, _spy_vol, held)
        print(f"book dP: base P(beats SPY Sharpe) {_dp['p_base']:.3f};", {x: v['dp'] for x, v in _dp['names'].items() if v['kind'] == 'add' and v['dp']})
        for s in syms:
            if s == IDX.get('symbol') or s not in _dp['names']: continue
            _rb = rubric.label(s, _dp['names'][s]['dp'])
            if not _rb: continue
            sizing[s] = dict(sizing[s], conviction=_rb['label'], rubric=_rb, conviction_source='rubric' + (f" (override: {_rb['override_reason']})" if _rb['override_reason'] else ''))
            caps[s].pop('conviction_below_min', None)
            if MIN_CONV and s not in (cfg.get('conviction_exceptions') or []) and RANK.get(sizing[s].get('conviction'), -1) < RANK[MIN_CONV]:
                caps[s]['conviction_below_min'] = 0.0
    ind = {}
    for s in names:
        if RM and RM.covered(s):
            k = max((k for k, f in enumerate(RM.factors) if f.startswith('ind_')), key=lambda k: RM.X[s][k])
            ind[s] = RM.factors[k][4:]
        else: ind[s] = 'unmodelled:' + s
    if IDX.get('symbol') in ind: ind[IDX['symbol']] = 'Index'
    groups = {}
    for i, s in enumerate(names): groups.setdefault(ind[s], []).append(i)
    lo = [w_h.get(s, 0.0) for s in names]; hi = [w_h[s] if s in held else min(caps[s].values()) for s in names]
    budget = sum(w_h.values()) + cash / W
    # Kilian 2026-09-29: half Kelly on the options-implied part of each return, quarter Kelly on the research part.
    # With one gamma that is the same as scaling the market excess by MKF * GAMMA before solving.
    MKF = cfg.get('market_kelly_fraction') or 1 / GAMMA
    def mu_mkt(s): return (CONV.get(s) or {}).get('mu_market', mu_raw[s]) if s != IDX.get('symbol') else mu[s]
    mu_solve = {s: r_f + MKF*GAMMA*(mu_mkt(s) - r_f) + (mu[s] - mu_mkt(s)) for s in names}
    wopt, info = optimize.solve([mu_solve[s] for s in names], r_f, C, GAMMA, lo, hi, budget, groups, MAX_IND)
    # Kilian 2026-09-30: at most max_positions single stocks open at once (the index fund does not count). While the solve
    # holds more, drop the name whose removal costs the least utility (each candidate re-solved without it), then re-solve.
    MAX_POS = cfg.get('max_positions'); dropped = []
    def u_of(w): return sum(w[i]*(mu_solve[s] - r_f) for i, s in enumerate(names)) - GAMMA/2*sum(w[i]*C[i][j]*w[j] for i in range(len(names)) for j in range(len(names)))
    # Kilian 2026-09-30: the last slot is for high conviction only, so names below 'high' fill at most max_positions_below_high.
    MAX_BH = cfg.get('max_positions_below_high')
    def below_high(s): return RANK.get((sizing.get(s) or {}).get('conviction'), -1) < RANK['high']
    # 2026-10-21 (Kilian 14:14Z 'yes'): when held names plus committed buys (the same staged new names and open limits the
    # Sharpe test counts below) already fill max_positions, that test has no free slot and every new name ends at 0 shares,
    # so drop them all in one step instead of one at a time. With the rubric gate off ~70 names pass the gates, and the
    # one-at-a-time loop below re-solved the ~110-name book ~2,400 times (48 minutes).
    n_comm = len({x for x, st_ in (cfg.get('staged_entries') or {}).items() if isinstance(st_, dict) and st_.get('new_name') and not st_.get('model_sized')
                  and x in syms and x not in w_h and x not in BM} | {x for x in (cfg.get('limit_orders') or {}) if x in syms and x not in w_h})
    # Repeat until no new name holds a share: dropping some names moves their weight to others (2026-10-21: one pass left
    # ~15 names on, and the one-at-a-time loop below then re-solved the book a few hundred times, ~40 minutes).
    while MAX_POS and len(held) + n_comm >= MAX_POS:
        full = [s for i, s in enumerate(names) if s in syms and s not in held and s != IDX.get('symbol') and wopt[i]*W >= res[s]['spot']]
        if not full: break
        for s in full: hi[names.index(s)] = 0.0; caps[s]['position_limit'] = 0.0; dropped.append(s)
        wopt, info = optimize.solve([mu_solve[x] for x in names], r_f, C, GAMMA, lo, hi, budget, groups, MAX_IND)
    while MAX_POS:
        on = [s for i, s in enumerate(names) if s in syms and s != IDX.get('symbol') and wopt[i]*W >= res[s]['spot']]  # at least one share
        bh = [s for s in on if below_high(s)]
        CORE = set(cfg.get('core_names') or [])  # empty since 2026-10-14 (Kilian: no "core names"); kept so a list could be set again
        if len(on) + len(held) > MAX_POS: cand = [x for x in on if x not in CORE] or on
        elif MAX_BH is not None and len(bh) + len([h for h in held if below_high(h)]) > MAX_BH: cand = [x for x in bh if x not in CORE] or bh
        else: break
        best = None
        for s in cand:
            h2 = list(hi); h2[names.index(s)] = 0.0
            w2, i2 = optimize.solve([mu_solve[x] for x in names], r_f, C, GAMMA, lo, h2, budget, groups, MAX_IND)
            if best is None or u_of(w2) > best[1]: best = (s, u_of(w2), w2, i2)
        s = best[0]; hi[names.index(s)] = 0.0; caps[s]['position_limit'] = 0.0; dropped.append(s); wopt, info = best[2], best[3]
    if dropped: print('position limit', MAX_POS, 'dropped (least utility lost first):', dropped)
    wo = dict(zip(names, wopt))
    # whole shares: round down, then add single shares back while they help and fit
    sh = {s: int(wo[s]*W // res[s]['spot']) for s in syms}
    def book_w(sh_): return dict(w_h, **{s: sh_[s]*res[s]['spot']/W for s in syms})
    def util(w):
        v = [w.get(s, 0) for s in names]
        return sum(v[i]*(mu_solve[s] - r_f) for i, s in enumerate(names)) - GAMMA/2*sum(v[i]*C[i][j]*v[j] for i in range(len(names)) for j in range(len(names)))
    def ind_share(w):
        v = [w.get(s, 0) for s in names]; cw = [sum(C[i][j]*v[j] for j in range(len(names))) for i in range(len(names))]
        tv = sum(a*b for a, b in zip(v, cw)); return {g: sum(v[i]*cw[i] for i in idx)/tv for g, idx in groups.items()} if tv else {}, tv
    while True:
        base = util(book_w(sh)); spent = sum(sh[s]*res[s]['spot'] for s in syms); best = None
        for s in syms:
            if hi[names.index(s)] <= 0: continue
            t = dict(sh); t[s] += 1; wt = book_w(t)
            if spent + res[s]['spot'] > cash or wt[s] > hi[names.index(s)] + 1e-9: continue
            if MAX_IND and max(ind_share(wt)[0].values()) > max(MAX_IND, max(ind_share(book_w(sh))[0].values() or [0])) + 1e-9: continue
            gain = util(wt) - base
            if gain > 0 and (best is None or gain > best[1]): best = (s, gain)
        if not best: break
        sh[best[0]] += 1
    # Kilian 2026-10-03: book Sharpe on the invested cost basis only. Weights = each name's share of invested dollars:
    # held names at cost (fills, data/trades.json; recorded fees left out since Kilian 2026-10-13 cancelled fees for sizing), new buys at spot. Cash is left out entirely.
    _tr = json.load(open(os.path.join(HERE, 'data', 'trades.json'))); _tr = _tr.get('trades', _tr) if isinstance(_tr, dict) else _tr
    _cost = {}
    for t in sorted(_tr, key=lambda t: t.get('date', '')):
        q, c = _cost.get(t['symbol'], (0, 0.0))
        if t.get('side', 'buy') == 'buy': q, c = q + t['shares'], c + t['shares']*t['price'] + (0 if cfg.get('sizing_ignores_past_fees') else t.get('fees', 0))
        else: c, q = c*(q - t['shares'])/q if q else 0.0, q - t['shares']  # sells take average cost out
        _cost[t['symbol']] = (q, c)
    def inv_stats(sh_):
        dol = {s: (_cost[s][1]*held[s]/_cost[s][0] if s in _cost and _cost[s][0] else mv[s]) for s in held}
        dol.update({s: sh_[s]*res[s]['spot'] for s in syms if sh_.get(s)})
        tot = sum(dol.values()); wc = {s: v/tot for s, v in dol.items()} if tot else {}
        if not wc: return None, None, None, None, {}, 0.0
        r_ = sum(wc[s]*mu[s] for s in wc); m_ = sum(wc[s]*((CONV.get(s) or {}).get('mu_market', mu_raw[s])) for s in wc)
        v_ = math.sqrt(ind_share(wc)[1]); return r_, m_, v_, (r_ - r_f)/v_ if v_ else None, wc, tot
    # Sharpe test (Kilian 2026-10-03: "the test is raising the book's Sharpe"; 19:57Z "check every combo"). Held names stay
    # fixed. Every eligible new name (passes the gates: target above last, conviction, not excluded or saved) gets 0..max
    # whole shares under its caps; every combination within cash and the name cap is scored on the invested-basis Sharpe and
    # the best one is kept (no buys if nothing beats the current book). Industry limit checked on the winner.
    GATES = ('target_above_last', 'conviction_below_min', 'excluded', 'saved_for_later', 'staged_wait', 'limit_order', 'big_miss_block', 'below_beta_return')
    # Committed buys (Kilian 2026-10-05: the page should count APP after Q3 and the AMZN limit): staged new names and open
    # limit orders are treated as already in the book for the Sharpe test, at full_shares x spot and shares x limit price.
    committed = {x: (st_['full_shares'], res[x]['spot']) for x, st_ in (cfg.get('staged_entries') or {}).items()
                 if isinstance(st_, dict) and st_.get('new_name') and not st_.get('model_sized') and x in syms and x not in w_h and x not in BM}  # big-miss block pauses it  # model_sized (Kilian 10-22 23:09Z): sized, not committed
    committed.update({x: (lo['shares'], lo['limit']) for x, lo in (cfg.get('limit_orders') or {}).items() if x in syms and x not in w_h})
    elig = [x for x in syms if x not in w_h and x != IDX.get('symbol') and not any(g in caps[x] for g in GATES)]
    ub = {x: int(min(v for k, v in caps[x].items() if k != 'position_limit')*W // res[x]['spot']) for x in elig}
    elig = [x for x in elig if ub[x] >= 1]
    sharpe_cut, combo_info = {}, {}
    if held:
        import itertools
        ix = {x: names.index(x) for x in list(held) + list(committed) + elig}
        hdol = {x: (_cost[x][1]*held[x]/_cost[x][0] if x in _cost and _cost[x][0] else mv[x]) for x in held}
        hdol.update({x: n*px for x, (n, px) in committed.items()})
        def fast_sharpe(cnt):
            dol = dict(hdol); dol.update({x: n*res[x]['spot'] for x, n in cnt.items() if n})
            tot = sum(dol.values()); ks = list(dol); w_ = [dol[k]/tot for k in ks]; ii = [ix[k] for k in ks]
            r_ = sum(w_[a]*mu[ks[a]] for a in range(len(ks)))
            v_ = math.sqrt(sum(w_[a]*w_[b_]*C[ii[a]][ii[b_]] for a in range(len(ks)) for b_ in range(len(ks))))
            return (r_ - r_f)/v_ if v_ else -1e9
        slots = max(MAX_POS - len(held) - len(committed), 0) if MAX_POS else len(elig)
        cash_c = cash - sum(n*px for n, px in committed.values())
        scored = []
        # 2026-10-15: enumerate only name subsets that fit the free slots (a full product over 6+ eligible names never ends)
        for k_ in range(0, min(slots, len(elig)) + 1):
            for sub in itertools.combinations(elig, k_):
                for combo in itertools.product(*[range(1, ub[x] + 1) for x in sub]):
                    cnt = dict(zip(sub, combo))
                    if sum(n*res[x]['spot'] for x, n in cnt.items()) > cash_c: continue
                    scored.append((fast_sharpe(cnt), cnt))
        scored.sort(key=lambda t: -t[0])
        best = None
        for sc, cnt in scored:
            if MAX_IND and any(cnt.values()):
                w_try = book_w(dict({x: 0 for x in syms}, **cnt))
                if max(ind_share(w_try)[0].values()) > max(MAX_IND, max(ind_share(book_w({x: 0 for x in syms}))[0].values() or [0])) + 1e-9: continue
            best = (sc, cnt); break
        base_sh = fast_sharpe({})
        combo_info = dict(committed={x: dict(shares=n, price=px) for x, (n, px) in committed.items()}, candidates={x: ub[x] for x in elig}, combos_checked=len(scored), base_sharpe=round(base_sh, 4),
                          best_sharpe=round(best[0], 4) if best else None)
        for x in syms:
            new_n = (best[1].get(x, 0) if best and best[0] > base_sh + 1e-9 else 0) if x in elig else (0 if x not in w_h else sh[x])
            if x in elig and new_n != sh[x]: sharpe_cut[x] = sh[x] - new_n
            if x not in w_h: sh[x] = new_n
        print(f"Sharpe test: {len(scored)} combos of {elig} (max shares {ub}); base {base_sh:.3f}, best {best[0] if best else float('nan'):.3f} ->",
              {x: n for x, n in sh.items() if n and x not in w_h})
    # brokerage fee (Kilian 2026-10-02): a new name must gain at least an opening and a closing fee a year
    FEE = cfg.get('fee_per_trade', 0.0)
    for s in syms:
        if sh[s] and s not in w_h and util(book_w(sh)) - util(book_w(dict(sh, **{s: 0}))) < 2 * FEE / W:
            print(f'{s}: {sh[s]} sh dropped, gains less than two ${FEE:.0f} fees'); sh[s] = 0
    wf = book_w(sh); shares_ind, tv = ind_share(wf)
    exp_ret = sum(wf[s]*mu[s] for s in names) + (1 - sum(wf.values()))*r_f
    exp_mkt = sum(wf[s]*((CONV.get(s) or {}).get('mu_market', mu_raw[s])) for s in names) + (1 - sum(wf.values()))*r_f
    vol_p = math.sqrt(tv) if tv > 0 else 0.0
    inv_w = sum(wf.values())
    inv_ret, inv_mkt, inv_vol, _, wc, tot = inv_stats(sh)
    cw = {s: sum(C[i][j]*wf.get(names[j], 0) for j in range(len(names))) for i, s in enumerate(names)}
    risk_share = {s: round(wf[s]*cw[s]/tv, 4) for s in names if wf.get(s)} if tv else {}
    port = dict(as_of=date, sizing=os.environ.get('SIZING') or SIZING, total_wealth=round(W, 2), cash_available=round(cash, 2), rf=r_f, gamma=GAMMA, max_industry_risk_share=MAX_IND, max_positions=MAX_POS, dropped_for_position_limit=dropped, sharpe_trims=sharpe_cut, sharpe_combo=combo_info,
                invested=round(sum(wf.values()), 4), tbills=round(1 - sum(wf.values()), 4),
                expected_return=round(exp_ret, 4), expected_return_market_only=round(exp_mkt, 4), volatility=round(vol_p, 4),
                # Kilian 2026-10-03: Sharpe on the invested money only (cash left out, not counted at 0% or the T-bill rate)
                invested_expected_return=round(inv_ret, 4) if inv_ret is not None else None,
                invested_expected_return_market_only=round(inv_mkt, 4) if inv_mkt is not None else None,
                invested_volatility=round(inv_vol, 4) if inv_vol else None, invested_dollars_cost_basis=round(tot, 2),
                sharpe=round((inv_ret - r_f)/inv_vol, 3) if inv_vol else None,
                risk_share=risk_share, industry_risk_share={g: round(v, 4) for g, v in shares_ind.items() if v > 1e-6},
                industry_of=ind, solver=dict(penalty=info['penalty'], iterations=info['iterations']),
                weights_continuous={s: round(wo[s], 4) for s in syms}, shares=sh,
                index_fund=(dict(symbol=IDX['symbol'], shares=sh[IDX['symbol']], dollars=round(sh[IDX['symbol']]*res[IDX['symbol']]['spot'], 2),
                                 expected_return=round(mu[IDX['symbol']], 4), vol=round(sig[IDX['symbol']], 4), weight_continuous=round(wo[IDX['symbol']], 4))
                            if IDX.get('symbol') in sh else None),
                method=__doc__.split('\n\n')[1])
    if RM:
        rk = RM.risk(wf); rr = RM.raw.risk(wf)
        port.update(volatility_option_market=round(rk['total_vol'], 4), volatility_raw_model=round(rr['total_vol'], 4),
                    beta=round(RM.beta_spy(wf), 3), invested_beta=round(RM.beta_spy(wc), 3) if wc else None, factor_share=round(rk['factor_share'], 4),
                    # Kilian 2026-10-09 "the beta figures between kilian's holdings and price targets disagree": the book beta shown on
                    # both pages = Risk Prism forecast on the names held, at market value (the dashboard's Risk tab definition)
                    held_beta_mv=(lambda mv: round(RM.beta_spy({x: v / sum(mv.values()) for x, v in mv.items()}), 3) if mv else None)(
                        {x: n * res[x]['spot'] for x, n in held.items() if n > 0 and x in res and RM.covered(x)}),
                    factor_var_share={f: round(v/rk['total_vol']**2, 4) for f, v in sorted(rk['factor_var_contrib'].items(), key=lambda x: -abs(x[1]))[:8]},
                    model=RM.m['tag'], calibration={k: v for k, v in RM.calib.items() if k != 'method'}, calibration_method=RM.calib['method'])
    json.dump(port, open(os.path.join(HERE, 'runs', date, 'portfolio.json'), 'w'), indent=1)
    rmod = dict(model=RM.m['tag'], model_as_of=RM.m['as_of'], vol_before=round(RM.risk(w_h)['total_vol'], 4) if w_h else 0.0,
                vol_after=round(vol_p, 4), share_of_risk_after=risk_share,
                note='Annualized vol of the whole account (holdings + all suggested buys, rest in cash): blended vols, recalibrated factor-model correlations.') if RM else None
    for s in syms:
        r = res[s]; S = r['spot']; c = caps[s]; w_ = wf[s]; hs = hi[names.index(s)]
        if 'target_above_last' in c: bind = 'target_above_last'
        elif 'conviction_below_min' in c: bind = 'conviction_below_min'
        elif 'saved_for_later' in c: bind = 'saved_for_later'
        elif 'excluded' in c: bind = 'excluded'
        elif 'big_miss_block' in c: bind = 'big_miss_block'
        elif 'below_beta_return' in c: bind = 'below_beta_return'
        elif 'position_limit' in c: bind = 'position_limit'
        elif s in sharpe_cut: bind = 'sharpe_gate'
        elif hs - wo[s] < 1e-4: bind = min(c, key=c.get)
        elif MAX_IND and info["group_share"].get(ind[s], 0) >= MAX_IND - 0.005 and info["penalty"] > 0: bind = "industry"
        elif cash - sum(sh[o]*res[o]['spot'] for o in syms) < max(res[o]['spot'] for o in syms) and sum(wopt) >= budget - 1e-4: bind = 'cash'
        else: bind = 'joint'
        o = dict(symbol=s, as_of=date, spot=S, total_wealth=round(W, 2), cash_available=round(cash, 2), rf=r_f,
                 mu=round(mu[s], 4), mu_for_sizing=round(mu_solve[s], 4), market_kelly_fraction=MKF, sigma=round(sig[s], 4), full_kelly=round((mu[s] - r_f)/sig[s]**2, 4), mu_before_conviction=round(mu_raw[s], 4),
                 conviction=CONV.get(s), industry=ind[s], rho_to_holdings={h: round(rho[s, h], 3) for h in held},
                 rho_to_candidates={h: round(rho[s, h], 3) for h in syms if h != s}, rho_source={h: src[s, h] for h in names if h != s},
                 caps={k: round(v, 4) for k, v in c.items()}, binding=bind, weight_continuous=round(wo[s], 4), weight=round(w_, 4),
                 target=r['target'], gate_passed=r['target'] > S, supervisor_sizing=sizing[s], method=__doc__.split('\n\n')[0],
                 risk_model=rmod, portfolio={k: port[k] for k in ('expected_return', 'volatility', 'sharpe', 'beta') if k in port},
                 shares=sh[s], dollars=round(sh[s]*S, 2), pct_of_new_cash=round(sh[s]*S/cash, 4) if cash else 0,
                 risk_share=risk_share.get(s, 0.0), loss_at_stop=round(sh[s]*(S - r['stop_loss']), 2) if r.get('stop_loss') is not None else None,
                 index_fund=bool(r.get('index_fund')), big_miss_block=BM.get(s), beta_bar=None if BAR.get(s) is None else round(BAR[s], 4))
        os.makedirs(os.path.join(HERE, 'runs', date, s), exist_ok=True)
        json.dump(o, open(os.path.join(HERE, 'runs', date, s, 'allocation.json'), 'w'), indent=1)
        print(f"{s:6} buy {sh[s]:3d} sh = ${o['dollars']:7,.0f} ({o['dollars']/W*100:4.1f}% of wealth, {o['risk_share']*100:4.1f}% of risk)  {ind[s]:6} "
              f"binding={bind:17} mu={mu[s]:+.3f} sigma={sig[s]:.3f} cap={hs:.3f} w*={wo[s]:.3f}")
    print(f"book: expected {exp_ret:+.1%} (options market alone {exp_mkt:+.1%}), vol {vol_p:.1%} (option market {port.get('volatility_option_market', 0):.1%}, raw model {port.get('volatility_raw_model', 0):.1%}), "
          f"Sharpe {port['sharpe']}, beta {port.get('beta')}, invested {port['invested']:.0%}, industries {port['industry_risk_share']}")
