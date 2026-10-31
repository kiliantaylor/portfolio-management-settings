"""Trim, hold or add for names already held: Sharpe-optimal targets with a tax no-trade band (SIZING, Kilian 2026-10-19).

Sizing since Kilian 2026-10-19 15:21Z ("Switch book sizing to Sharpe-optimal allocation given among held names based on
our expected return"): allocate.SIZING = 'max_sharpe' (the environment variable SIZING overrides it for one run;
'mean_variance' rolls back to the rule described below). Each held name's target weight is the long-only maximum-Sharpe
(tangency) portfolio of the held names,

    w  proportional to  Sigma^-1 (mu - rf),   w >= 0,

with mu the conviction-adjusted mu_eff (the expected return the page shows: no half-Kelly scaling of the options part)
and the same Sigma and rf, scaled so the held names add up to B, the total that the mean-variance rule below would hold
in them with the timing gates (staged tranches, big-miss block) switched off. Equivalently: the long-only mean-variance
solve on mu_eff at the single gamma* whose solution sums to B. A name whose stop-out would cost more than the stop budget
is clipped at its cap and gamma* is re-solved so the total stays B. A name whose excess return is too small for its
correlation with the others gets weight 0. Today's trade moves each name toward its own target, with the other held names
at their targets (so a staged name still below target does not swell the rest), through the same tax no-trade band,
entry gate, caps, staged and big-miss timing and cash. Adds keep the Sharpe test and the one-share-a-day pace of the
next-share table. A trim toward a lower target (including 0) sells at most allocate.TRIM_PACE_SHARES (1) share a day;
exits at the stop-loss are not paced. The decider schedule, next-share levels, take-profit and trigger prices use the
same mu_eff price curve and gamma* around today's plan. review_summary.json 'sizing' carries B, gamma*, the targets,
the uncapped tangency weights and the Sharpe ratios of the current, mean-variance and Sharpe-optimal books.

The mean-variance rule (SIZING = 'mean_variance'; also used above to set B).
A tax-aware mean-variance rebalance with a no-trade band.

The problem. Choose portfolio weights w (share of total wealth: account + cash to deploy; the rest earns the T-bill
rate rf) to maximize the yearly certainty-equivalent return

    U(w) = sum_i w_i (mu_i - rf)  -  (gamma / 2) w' Sigma w,         gamma from data/review_settings.json (4 = quarter Kelly),

minus the one-off cost of getting there. mu_i is the 12-month expected return from today's blended distribution
(result.json), which re-prices the next 12 months from TODAY every close: the options market moves with the price, the
agents' and analysts' views stay in dollars. So a fast run toward the target lowers mu_i, and what is left must be
earned over a whole new year. That is how time enters. Sigma_ij = rho_ij sigma_i sigma_j: sigma_i from the same
distribution, rho_ij from the Risk Prism factor model recalibrated to the option market (riskcal.py; 2Y realized where it has no
coverage), as in allocate.py.
gamma = 4 is the same risk aversion allocate.py uses to size new names, so held and new names follow one rule.

The marginal value of one more dollar in name i, per year, is the gradient

    g_i = (mu_i - rf) - gamma * (Sigma w)_i.

Costs. Selling $1 of name i realizes a gain fraction q_i = 1 - basis / price (negative for a loss). Paying that tax now
rather than when you would otherwise sell, T years from now, costs the growth the tax money would have earned:
PV cost = tau * q_i * (1 - (1 + d)^-T), with tau the long- or short-term rate + state + NIIT (data/review_settings.json) and d the
discount rate. A loss gives the same formula with the sign flipped: harvesting it now is worth that much. Plus a bid-ask
spread. The better allocation pays off every year for those same T years, so the one-off cost is spread over T:

    sell band  c_sell_i = (tau q_i (1 - (1+d)^-T) + spread) / T,      buy band  c_buy = spread / T.

The rule (Constantinides / Davis-Norman no-trade region with proportional costs):
    trim  when g_i < -c_sell_i: sell until g_i = -c_sell_i (w_i falls by (g_i + c_sell_i) / (gamma Sigma_ii));
    add   when g_i > c_buy and the NTM target is above the last price (the entry gate): buy until g_i = c_buy, capped
          like a new name (2% stop budget, supervisor cap, 20%) and by the cash left after the suggested new buys;
    hold  in between. Exit is forced at or below the stop-loss.
All names are solved together (coordinate descent), with allocate.py's suggested buys counted in the book.
Trigger prices ("trim above", "add below") are the prices at which a name leaves the band with everything else fixed;
"at 90% of the way to the target in one month" re-solves the name at that price. The pace flag (75%+ of the way to the
target within the first quarter of the horizon) asks the supervisor to re-underwrite the target at the next research round.
The implied gamma is the risk aversion at which the current book (before trades) would be optimal, as a sanity check.

Usage: python3 review.py DATE  -> runs/DATE/SYM/review.json for every held name, runs/DATE/review_summary.json."""
import divs
import datetime as dt, glob, json, math, os, sys
import allocate as A
from combine import survival_fn



def expectation(Ks, ps, S):
    sf = survival_fn(Ks, [min(max(p, 1e-4), 1 - 1e-4) for p in ps])
    g = [S * math.exp(-4 + 8 * i / 2000) for i in range(2001)]
    return g[0] + sum((sf(g[i]) + sf(g[i - 1])) / 2 * (g[i] - g[i - 1]) for i in range(1, len(g)))


def mu_curve(res):
    """mu as a function of the price S': the market part scales with S', the agents' and analysts' parts stay in dollars."""
    S, m, lad = res['spot'], res['method'], res['ladder']
    Ks = [l['K'] for l in lad]
    E_m = expectation(Ks, [l['market'] for l in lad], S); E_l = expectation(Ks, [l['llm_platt'] for l in lad], S)
    an = res.get('analysts') or {}; w_an = an.get('weight') or 0
    E_a = E_m * an['median'] / res['market_only']['target'] if w_an and an.get('median') else 0
    wm, wl = m['w_market'], m['w_llm']
    E_now = (1 - w_an) * (wm * E_m + wl * E_l) + w_an * E_a
    k = res['expected'] / E_now if E_now else 1  # calibrate to combine.py's own mean
    D = divs.dps(res.get('symbol', ''))  # Kilian 2026-10-12: the year's dividends in $, a yield of D/S' at price S'
    return lambda S_: k * ((1 - w_an) * (wm * E_m * S_ / S + wl * E_l) + w_an * E_a) / S_ - 1 + D / S_


def main(date):
    cfg = json.load(open(os.path.join(A.HERE, 'data', 'review_settings.json'))); tax = cfg['tax']
    # Kilian 2026-10-20 19:16Z ('yea' to holding trims a pending method change might reverse; VST was trimmed at 11:11 ET while
    # the ABT card was open, then the re-solve raised its target): while any open card in data/decisions.json is marked
    # affects_sizing, trims toward a lower target wait (stop exits do not); adds are unaffected
    try: _dj = json.load(open(os.path.join(A.HERE, 'data', 'decisions.json')))
    except Exception: _dj = {}
    PENDING = [e for e in (_dj.get('decisions', []) if isinstance(_dj, dict) else []) if isinstance(e, dict) and e.get('status') == 'open' and e.get('affects_sizing')]
    # Kilian 2026-10-21 16:19Z ('yes'): after a held name's earnings print, adds and trims wait until its post-earnings rerun
    # is done (data/earnings_reruns.json, written by routines/earnings_rerun_check.py); stop exits do not wait
    try: _er = json.load(open(os.path.join(A.HERE, 'data', 'earnings_reruns.json'))).get('reruns') or {}
    except Exception: _er = {}
    RERUN = {k: v for k, v in _er.items() if isinstance(v, dict) and v.get('status') == 'pending_rerun'}
    try:  # correlations from the factor model recalibrated to the option market (riskcal.py), as in allocate.py
        import riskcal; A.RM = riskcal.load(date)
    except Exception as e: print('recalibrated risk model unavailable, using the raw one:', e)
    GAMMA = float(os.environ.get('REVIEW_GAMMA') or cfg.get('risk_aversion_gamma') or 1 / A.KELLY_FRAC)
    T, d, spread = tax['years_until_you_would_otherwise_sell'], tax['discount_rate'], tax['spread_cost']

    v_later = 0.0 if tax.get('ignore_taxes') else tax.get('future_rate_on_deferred_gains', 0.21) * (1 + d) ** -T  # PV per $ of gain of the tax you'd pay later

    def lt_of(sym):
        lot = tax.get('lots', {}).get(sym)
        return not lot or (dt.date.fromisoformat(date) - dt.date.fromisoformat(lot)).days > 365

    def prog(x, brackets):
        t, lo = 0.0, 0.0
        for top, r in brackets:
            if x > lo: t += (min(x, top) - lo) * r
            lo = top
        return t

    def year_tax(g_lt, g_st=0.0):
        """Federal + state + NIIT for the year, given net long- and short-term gains realized this year. With the kiddie
        tax, unearned income above the threshold is taxed at the parent's marginal rates (Form 8615, FTB 3800)."""
        if tax.get('ignore_taxes') or tax.get('account', 'taxable') != 'taxable': return 0.0
        g_lt += tax.get('realized_gains_ytd', 0)
        if tax.get('mode') == 'flat':  # Kilian 2026-10-01: kiddie tax at the parent's flat marginal rates (review_settings tax._flat_note)
            r_st, r_lt = tax['flat_short_term_rate'], tax['flat_long_term_rate']; g_st += tax.get('realized_short_term_ytd', 0)
            if g_st < 0 < g_lt: g_lt, g_st = max(g_lt + g_st, 0), min(g_lt + g_st, 0)
            elif g_lt < 0 < g_st: g_st, g_lt = max(g_st + g_lt, 0), min(g_st + g_lt, 0)
            c_st, c_lt = tax.get('flat_child_short_term_rate', r_st), tax.get('flat_child_long_term_rate', r_lt)
            if g_st + g_lt < 0: return max(g_st + g_lt, -tax.get('capital_loss_limit', 3000)) * c_st
            st, lt = max(g_st, 0), max(g_lt, 0); gains = st + lt
            # kiddie tax: unearned income up to the threshold at his own rates, the rest at the parent's (Form 8615);
            # dividends use the threshold first, the gains share what is left in proportion
            room = max(tax.get('kiddie_threshold', 2700) - tax.get('other_unearned_income', 0), 0) if tax.get('kiddie_tax', True) else 1e18
            own = min(gains, room); f = own / gains if gains else 0
            return st * (f * c_st + (1 - f) * r_st) + lt * (f * c_lt + (1 - f) * r_lt)
        if g_st < 0 < g_lt: g_lt, g_st = max(g_lt + g_st, 0), min(g_lt + g_st, 0)
        elif g_lt < 0 < g_st: g_st, g_lt = max(g_st + g_lt, 0), min(g_st + g_lt, 0)
        net = g_lt + g_st; lim = tax.get('capital_loss_limit', 3000)
        wages, other = tax['wages'], tax.get('other_unearned_income', 0)
        if net < 0: g_lt, g_st = 0.0, max(net, -lim)
        unearned = max(other + g_lt + g_st, 0)
        nui = max(unearned - tax.get('kiddie_threshold', 2700), 0) if tax.get('kiddie_tax') else 0.0
        nui_lt = min(nui, max(g_lt, 0)); nui_st = nui - nui_lt  # parent's rate hits gains first (conservative)
        ordinary = wages + other + g_st; ltcg = max(g_lt, 0)
        # federal: the child's own tax on everything except the NUI, plus the parent's rate on the NUI
        sd = tax['federal_standard_deduction']
        ti = max(ordinary + ltcg - sd, 0); ti_ord = max(ordinary - nui_st - sd, 0)
        ti_own = max(ti - nui, 0); lt_own = max(ti_own - ti_ord, 0); ti_ord = ti_own - lt_own
        fed = prog(ti_ord, tax['federal_ordinary_brackets'])
        z, f = tax['ltcg_0pct_top_taxable_income'], tax['ltcg_15pct_top_taxable_income']
        in0 = max(min(ti_own, z) - ti_ord, 0); in15 = max(min(ti_own, f) - max(ti_ord, z), 0)
        fed += 0.15 * in15 + 0.20 * max(lt_own - in0 - in15, 0)
        fed += nui_lt * tax.get('parent_federal_ltcg_rate', 0.15) + nui_st * tax.get('parent_federal_ordinary_rate', 0.24)
        agi = ordinary + ltcg
        fed += 0.038 * max(min(unearned, agi - tax.get('niit_threshold', 200000)), 0)
        # state (CA taxes gains as ordinary income; the NUI at the parent's rate)
        st_ti = max(agi - tax.get('state_standard_deduction', 0), 0)
        st = prog(max(st_ti - nui, 0), tax.get('state_brackets', [])) + min(nui, st_ti) * tax.get('parent_state_rate', 0.093)
        return fed + st

    def tax_of(gains):
        """Extra tax this year from realizing {sym: gain $}."""
        lt = sum(g for s_, g in gains.items() if lt_of(s_)); st = sum(g for s_, g in gains.items() if not lt_of(s_))
        return year_tax(lt, st) - year_tax(0.0)

    plan = {}  # realized gains of the current plan, {sym: $}; the marginal rate depends on it (iterated below)

    def marginal(sym, sign, extra=None):
        base = dict(plan, **(extra or {})); b = tax_of(base); base[sym] = base.get(sym, 0) + 100 * sign
        return (tax_of(base) - b) / (100 * sign)

    import conviction; CONV = conviction.fractions(date)  # agents' view scaled by conviction: mu_eff = m + mult * (mu - m)
    import earnings_miss; BM = earnings_miss.blocks(date)  # Kilian 2026-10-19 card 'Add flag': no adds for 6 months after a big earnings miss
    GI = lambda s: SZ['gamma']  # gamma (mean_variance) or gamma* (max_sharpe), set below
    import book; H = book.load(); held = {p['symbol']: p for p in H['positions']}  # data/book.json
    cash0 = H.get('cash_to_deploy_external', 0) + H['cash_in_account']
    if H.get('mode') == 'live':  # realized gains so far this year come from the trade log (book.py), not the settings file
        yr = date[:4]; rz = [x for x in H.get('realized', []) if x['date'][:4] == yr]
        tax['realized_gains_ytd'] = sum(x['gain'] for x in rz if x['long_term']); tax['realized_short_term_ytd'] = sum(x['gain'] for x in rz if not x['long_term'])
        tax.setdefault('lots', {}).update({p['symbol']: p['first_bought'] for p in H['positions']})
    res, alloc = {}, {}
    for p in glob.glob(os.path.join(A.HERE, 'runs', date, '*', 'result.json')):
        s = p.split(os.sep)[-2]; res[s] = json.load(open(p))
        ap = os.path.join(os.path.dirname(p), 'allocation.json')
        if s not in held and os.path.exists(ap):
            a = json.load(open(ap))
            if a.get('shares'): alloc[s] = a
    held = {s: v for s, v in held.items() if s in res}
    names = list(held) + list(alloc)
    S = {s: res[s]['spot'] for s in names}
    W = sum(held[s]['shares'] * S[s] for s in held) + cash0
    mom = {s: A.moments(res[s]) for s in names}
    mu_raw = {s: mom[s][0] for s in names}; sig = {s: mom[s][1] for s in names}
    cv = {s: CONV.get(s) or {} for s in names}
    mu = {s: cv[s].get('mu_eff', mu_raw[s]) for s in names}

    def eff_curve(s):
        f = mu_curve(res[s]); c = cv[s]
        if not c: return f
        m, k, t = c['mu_market'], c['multiplier'], c.get('tilt', 0.0)  # the options part keeps its return as the price moves; alpha is rescaled
        F = (cfg.get('market_kelly_fraction') or 1 / GAMMA) * GAMMA  # half Kelly on the options part (Kilian 2026-09-29)
        return lambda P: rf + F * (m - rf) + t + k * (f(P) - m - t)  # revision tilt at quarter Kelly, not scaled by conviction (2026-10-08)
    mu_at = {s: eff_curve(s) for s in held}
    def true_curve(s):  # the expected return itself (no half-Kelly scaling of the options part), for Sharpe tests
        f = mu_curve(res[s]); c = cv[s]
        if not c: return f
        m, k, t = c['mu_market'], c['multiplier'], c.get('tilt', 0.0)
        return lambda P: m + t + k * (f(P) - m - t)
    mu_true_at = {s: true_curve(s) for s in held}
    # Kilian 2026-10-29 card 'Expected value vs cash': a held name's take-profit is the price where its 12-month expected
    # value in dollars (the close's mean incl. a year of dividends, both legs fixed in dollars) no longer beats the 4% cash
    # sleeve, E / (1 + rf). The decider holds zero shares at or above it, so it is also the full exit; trims below are unchanged.
    _rf0 = A.rf()
    TPX = {s: round(res[s]['spot'] * (1 + mu_true_at[s](res[s]['spot'])) / (1 + _rf0), 2) for s in held}
    # Kilian 2026-10-22 00:47Z: an add, like a new name, must expect more than the beta-adjusted return (allocate.beta_bar,
    # T-bill + 7.69% x (0.768 + 0.232 beta)); tested on the true expected return at the add price
    BAR = {s: A.beta_bar(s, date) for s in held}
    def bar_ok(s, P): return BAR.get(s) is None or s not in mu_true_at or A.bar_return(s, mu_true_at[s](P), res[s]['target'], P) > BAR[s]
    # Kilian 2026-10-22 18:39Z ("i never even said to only make trades due at close"): LIVE_QUOTES=1 re-prices the held names
    # at data/live_quotes.json (quotes from a session after D): research fixed in dollars, the options leg on its price curve,
    # the rule the page's price levels already use. The close run's files are kept once as *_close.json.
    LIVE_AS_OF, today = None, date  # today: the session the staged dates and holds are checked against (the quote's date in live mode)
    if os.environ.get('LIVE_QUOTES'):
        import shutil
        lq = json.load(open(os.path.join(A.HERE, 'data', 'live_quotes.json'))); LIVE_AS_OF = lq.get('as_of')
        live = {x: q['price'] for x, q in (lq.get('quotes') or {}).items() if x in held and q.get('price') and (q.get('date') or '') > date}
        for x, P in live.items(): S[x] = P; mu[x] = mu_true_at[x](P)
        today = max([q.get('date') or date for q in (lq.get('quotes') or {}).values()] + [date])
        W = sum(held[x]['shares'] * S[x] for x in held) + cash0
        for f in [os.path.join(A.HERE, 'runs', date, 'review_summary.json')] + [os.path.join(A.HERE, 'runs', date, x, n) for x in held for n in ('review.json', 'decider.json')]:
            if os.path.exists(f) and not os.path.exists(f[:-5] + '_close.json'): shutil.copy(f, f[:-5] + '_close.json')
        print('live prices', LIVE_AS_OF, live)
    pool = list(held) if len(held) > 1 else names
    pairs = [A.corr(A.closes(a), A.closes(b))[0] for i, a in enumerate(pool) for b in pool[i + 1:]]
    rho_bar, rf = sum(pairs) / len(pairs), A.rf()
    # Kilian 2026-10-15 'Fix all': the held-name plan sizes the options part at half Kelly too, as allocate.py does for
    # new buys (was quarter Kelly on the whole return). Sizing return only; Sharpe tests and reports keep the true mu.
    F_MK = (cfg.get('market_kelly_fraction') or 1 / GAMMA) * GAMMA
    mu_solve = {s: mu[s] + (F_MK - 1) * (cv[s]['mu_market'] - rf) if cv[s] else mu[s] for s in names}
    # Kilian 2026-10-19 15:21Z: Sharpe-optimal sizing among held names (allocate.SIZING; env SIZING overrides; 'mean_variance' rolls back)
    SIZING = os.environ.get('SIZING') or getattr(A, 'SIZING', 'mean_variance'); MS = SIZING == 'max_sharpe'
    TRIM_PACE = getattr(A, 'TRIM_PACE_SHARES', 1)  # None since 2026-10-20 ('Buy to size'): trims go to target in one order
    SZ = dict(gamma=GAMMA, mu=mu_solve, fixed=None)  # what grad() sizes with; max_sharpe swaps in gamma*, mu_eff and the targets

    def rho(a, b):
        if a == b: return 1.0
        if A.RM and A.RM.covered(a) and A.RM.covered(b): return A.RM.corr(a, b)
        r, n = A.corr(A.closes(a), A.closes(b)); k = min(n / 504, 1); return k * r + (1 - k) * rho_bar
    R = {(a, b): rho(a, b) for a in names for b in names}
    cov = lambda a, b: R[(a, b)] * sig[a] * sig[b]
    w0 = {s: held[s]['shares'] * S[s] / W for s in held}
    w0.update({s: alloc[s]['dollars'] / W for s in alloc})
    cash_left = cash0 - sum(alloc[s]['dollars'] for s in alloc)

    def grad(s, w, mu_s=None):
        m_ = SZ['mu'][s] if mu_s is None else mu_s
        if SZ['fixed'] is not None: w = dict(SZ['fixed'], **{s: w[s]})  # max_sharpe: the rest of the book at its targets
        return (m_ - rf) - GI(s) * sum(w[j] * cov(s, j) for j in w)

    def sell_cost(s, price):
        """Per-year cost of selling $1: tax paid now at this year's marginal rate instead of later at the future rate
        (floored at zero: when realizing now is cheaper, the gain-harvest suggestion captures it without moving the
        weights), plus the spread, spread over T years."""
        q = 1 - held[s]['cost_basis'] / (held[s]['shares'] * price); lt = lt_of(s)
        tau = marginal(s, 1 if q >= 0 else -1, {s: plan.get(s, 0)})
        if tax.get('assume_short_term_all') and not tax.get('ignore_taxes'):
            # Kilian 2026-10-28 card 'Assume short-term on all': every holding is sold within the year at the short-term rate
            # either way, so trimming now only pays the same tax H years sooner (H = short_term_hold_years, 1): the band is
            # the time value of that tax, q tau (1 - (1+d)^-H) / H, plus spread / T
            H = tax.get('short_term_hold_years', 1.0)
            return max(q * tau * (1 - (1 + d) ** -H), 0) / H + spread / T, q, tau, lt
        if not lt and tax.get('lot_aware_horizon') and not tax.get('ignore_taxes') and tax.get('lots', {}).get(s):
            # Kilian 2026-10-20 card 'Lot-aware': the alternative to trimming a short-term lot now is holding it to its
            # one-year date and selling long-term, so the saving is spread over the X years until then (FIFO: oldest lot)
            X = max(366 - (dt.date.fromisoformat(date) - dt.date.fromisoformat(tax['lots'][s])).days, 1) / 365
            v_lt = tax.get('flat_long_term_rate', tax.get('future_rate_on_deferred_gains', 0.21)) * (1 + d) ** -X
            return max(q * (tau - v_lt), 0) / X + spread / T, q, tau, lt
        return (max(q * (tau - v_later), 0) + spread) / T, q, tau, lt

    # Kilian 2026-10-21 card 'Drop forced exit' (Assumption audit; audit/stop_test: the nightly re-centred stop fired on 3.2% of
    # one-year holds, cost 0.19%/yr, t -2.7, no tail help): no exit at the stop. The stop stays for the stop-budget cap, taken
    # as today's gap fraction (S - SL)/S at any price, since the stop re-centres on each close. forced_stop_exit true rolls back.
    FSE = bool(cfg.get('forced_stop_exit', True))
    below = lambda s, P: FSE and P <= res[s]['stop_loss']

    def caps(s, price):
        r = res[s]; c = {'ceiling': A.CEILING}
        if not FSE:
            gap = (S[s] - r['stop_loss']) / S[s]
            if gap > 0: c['stop_budget'] = A.STOP_BUDGET / gap
        elif price > r['stop_loss']: c['stop_budget'] = A.STOP_BUDGET / ((price - r['stop_loss']) / price)
        sp = os.path.join(A.HERE, 'runs', r.get('research_as_of') or date, s, 'supervisor.json')
        mw = ((json.load(open(sp)).get('sizing') or {}) if os.path.exists(sp) else {}).get('max_weight_pct')
        if mw and cfg.get('use_supervisor_max_weight', True): c['supervisor'] = mw / 100  # off: Kilian 2026-10-07 'the per-name cap is dropped'
        return c

    def loss_note(s, price):
        """Kilian 2026-10-26 18:33Z: a loss sale is never credited with a harvest (sell_cost floors at zero), so say so, and
        flag the wash-sale rule (IRC 1091) when shares were bought within 30 days before the sale: the loss is disallowed now
        and moves to the newer shares' basis, so the trim rests on sizing alone."""
        lots = held[s].get('lots') or []; first = min((l['date'] for l in lots), default=None)  # FIFO sells the oldest lot first
        recent = [l for l in lots if l['date'] > first and 0 <= (dt.date.fromisoformat(today) - dt.date.fromisoformat(l['date'])).days <= 30]
        if recent:
            return (f"a loss sale: no tax saving counted; wash sale, {sum(l['shares'] for l in recent):g} sh bought since "
                    f"{min(l['date'] for l in recent)}, so the loss moves to those shares' basis")
        return 'a loss sale: no tax now, and no harvest value is counted'

    def solve_one(s, w, price=None, mu_s=None):
        """New weight for held name s given the others; returns (weight, action, reason, gradient, band)."""
        price = price or S[s]; r = res[s]
        if below(s, price): return 0.0, 'exit', f"at or below the stop-loss {r['stop_loss']:.2f}", None, None
        g = grad(s, w, mu_s); cs, q, tau, lt = sell_cost(s, price); cb = spread / T
        band = (-cs, cb)
        if g < -cs:
            nw = max(w[s] + (g + cs) / (GI(s) * sig[s] ** 2), 0.0)
            why = (f'one more dollar here is worth {g:+.1%} a year, below the {-cs:+.1%} a year it costs to sell '
                   f'({loss_note(s, price) if q < 0 else "tax on the gain, spread over the holding period"})')
            return nw, 'trim', why, g, band
        if g > cb:
            if r['target'] <= price:
                return w[s], 'hold', f'one more dollar is worth {g:+.1%} a year, but the NTM target is not above the price (entry gate)', g, band
            nw = w[s] + (g - cb) / (GI(s) * sig[s] ** 2)
            c = caps(s, price); nw = min(nw, max(min(c.values()), w[s]))
            return nw, 'add', f'one more dollar here is worth {g:+.1%} a year, above the cost of buying', g, band
        return w[s], 'hold', f'one more dollar here is worth {g:+.1%} a year, inside the no-trade band ({-cs:+.1%} to {cb:+.2%})', g, band

    def descend(timing=True):  # timing=False: staged and big-miss holds off (max_sharpe uses it to find B)
        w = dict(w0); act = {}
        for _ in range(50):
            moved = 0.0
            for s in held:
                nw, a, why, g, band = solve_one(s, w)
                if a == 'add':  # cash constraint across adds
                    spent = sum(max(w[j] - w0[j], 0) for j in held if j != s) * W
                    nw = min(nw, w0[s] + max(cash_left - spent, 0) / W)
                    _st = (cfg.get('staged_entries') or {}).get(s)  # 2026-10-05: a staged second tranche is not bought before its print,
                    if timing and _st and today < _st['after']: nw = w0[s]  # so the joint plan must not trim other names to make room for it
                    if timing and s in BM: nw = w0[s]  # 2026-10-19 'Add flag': big earnings miss, no add (trims and exits unchanged)
                moved = max(moved, abs(nw - w[s])); w[s] = nw; act[s] = (a, why, g, band)
            if moved < 1e-7: break
        return w, act

    def gains_of(w):
        out = {}
        for s in held:
            sold = max(w0[s] - w[s], 0) * W
            if sold > 0: out[s] = sold * (1 - held[s]['cost_basis'] / (held[s]['shares'] * S[s]))
        return out

    # joint solve: coordinate descent over held names (new buys fixed at their suggested size); the tax rate depends on
    # the gains the plan realizes this year, so iterate plan -> marginal rates -> plan until it settles.
    sizing = dict(mode=SIZING); ZHOLD = {}  # ZHOLD: zero-target names held until their date (allocate.ZERO_TARGET_HOLD)
    if MS:  # Kilian 2026-10-19 15:21Z: targets = long-only tangency of the held names on mu_eff, scaled to B
        w_mv, _ = descend(timing=False)  # B: what the mean-variance rule would hold in the held names, timing gates off
        B = sum(w_mv[s] for s in held)
        RESERVE = float(cfg.get('cash_reserve') or 0)  # Kilian 2026-10-20 card 20:47Z '10% reserve': held names stop at 1 - reserve of the
        SLOT = float(cfg.get('tenth_name_slot') or 0) if len(held) < int(cfg.get('max_positions') or 10) else 0.0  # Kilian 2026-10-22 23:10Z: room held for a
        # 10th name (tbd) until one is bought, so the held names are not sized onto the whole book
        if RESERVE or SLOT: B = min(B, max(1 - RESERVE - SLOT, 0))  # book, so short-term names outside the 10 can be bought from cash without trims
        capw = {s: 0.0 if below(s, S[s]) else min(caps(s, S[s]).values()) for s in held}
        fixed_new = {s: w0[s] for s in alloc}  # today's suggested new buys stay at their size
        def ms_weights(g, cap=True, extra=True):  # long-only mean-variance on mu_eff at gamma g (coordinate descent)
            ww = {s: 0.0 for s in held}; base_ = fixed_new if extra else {}
            for _ in range(5000):
                mv_ = 0.0
                for s in held:
                    if below(s, S[s]): continue
                    gr = mu[s] - rf - g * (sum(ww[j] * cov(s, j) for j in held) + sum(base_[j] * cov(s, j) for j in base_))
                    nw = max(ww[s] + gr / (g * sig[s] ** 2), 0.0)
                    if cap: nw = min(nw, capw[s])
                    mv_ = max(mv_, abs(nw - ww[s])); ww[s] = nw
                if mv_ < 1e-12: break
            return ww
        lo_g, hi_g = 0.05, 500.0
        if B <= 1e-9: GSTAR = hi_g
        elif sum(ms_weights(lo_g).values()) <= B: GSTAR = lo_g  # the caps bind: the held names cannot hold B
        else:
            for _ in range(60):
                g_ = math.sqrt(lo_g * hi_g)
                lo_g, hi_g = (g_, hi_g) if sum(ms_weights(g_).values()) > B else (lo_g, g_)
            GSTAR = math.sqrt(lo_g * hi_g)
        wT = ms_weights(GSTAR) if B > 1e-9 else {s: 0.0 for s in held}
        x_ = ms_weights(1.0, cap=False, extra=False); tx = sum(x_.values())
        tang = {s: x_[s] / tx if tx else 0.0 for s in held}  # the held names' own long-only tangency, no caps
        def sh_of(ww):  # Sharpe of a set of weights (mu_eff, model covariance), on the invested money
            ks = [k for k in ww if ww[k] > 0]; t_ = sum(ww[k] for k in ks)
            if t_ <= 0: return None
            v_ = math.sqrt(sum(ww[a] * ww[b] * cov(a, b) for a in ks for b in ks)) / t_
            return round((sum(ww[k] * mu[k] for k in ks) / t_ - rf) / v_, 4) if v_ else None
        SZ.update(gamma=GSTAR, mu=dict(mu), fixed=dict(wT, **fixed_new))
        sizing.update(cash_reserve=RESERVE, tenth_name_slot=SLOT, invested_total_mv=round(B, 4), invested_total_target=round(sum(wT.values()), 4), gamma_star=round(GSTAR, 4),
                      target_weight={s: round(wT[s], 4) for s in held}, target_shares={s: round(wT[s] * W / S[s], 2) for s in held},
                      tangency_weight={s: round(tang[s], 4) for s in held}, cap_weight={s: round(capw[s], 4) for s in held},
                      mv_weight={s: round(w_mv[s], 4) for s in held}, mv_shares={s: round(w_mv[s] * W / S[s], 2) for s in held},
                      trim_pace_shares=TRIM_PACE, sharpe_current=sh_of({s: w0[s] for s in held}),
                      sharpe_mv_target=sh_of({s: w_mv[s] for s in held}), sharpe_target=sh_of(wT), sharpe_tangency=sh_of(tang),
                      note='Sharpe ratios on market-value weights of the held names only (mu_eff, model covariance); the page figure is portfolio.json sharpe_combo (held at cost).')
        if getattr(A, 'ZERO_TARGET_HOLD', False):  # 2026-10-19 option (b): a target of 0 waits for the staged date or the next report
            import re
            try: EM = json.load(open(os.path.join(A.HERE, 'data', 'earnings_miss.json')))
            except Exception: EM = {}
            for s in held:
                if wT[s] * W / S[s] >= 0.5 or below(s, S[s]): continue
                c_ = []; st_ = (cfg.get('staged_entries') or {}).get(s)
                if isinstance(st_, dict) and st_.get('after') and today < st_['after']: c_.append((st_['after'], st_.get('event') or 'its staged date'))
                nr = (EM.get(s) or {}).get('next_report') if isinstance(EM.get(s), dict) else None
                if isinstance(nr, dict) and nr.get('date'):  # trims resume the day after the report
                    af = (dt.date.fromisoformat(nr['date']) + dt.timedelta(days=1)).isoformat()
                    if today < af: c_.append((af, f"earnings on {nr['date']}" + (f" {nr['timing']}" if nr.get('timing') else '')))
                if c_:
                    u, ev = min(c_, key=lambda x: x[0]); ev = re.sub(r'\s*\(.*$', '', ev)
                    ZHOLD[s] = dict(until=u, event=ev, note=f'held until {u} (target 0 under max-Sharpe; review after {ev})')
            sizing['zero_target_hold'] = ZHOLD
    paced = {}
    def pace(w):  # max_sharpe: a trim toward a lower target sells at most TRIM_PACE shares a day (stop exits are not paced)
        if not MS: return
        for s in held:
            if s in ZHOLD:  # option (b): no trim before its date
                if w[s] < w0[s]: w[s] = w0[s]
                continue
            if not TRIM_PACE: continue
            fl = w0[s] - TRIM_PACE * S[s] / W
            if w[s] < fl - 1e-12 and not below(s, S[s]): paced[s] = w[s]; w[s] = fl
    for it in range(8):
        w, act = descend(); pace(w); new_plan = gains_of(w)
        if all(abs(new_plan.get(k, 0) - plan.get(k, 0)) < 1 for k in set(new_plan) | set(plan)): break
        plan.clear(); plan.update(new_plan)
    first = {s: solve_one(s, w0)[1:] for s in held}  # each name's own read at today's book

    # implied gamma: risk aversion at which the current book (before trades) is mean-variance optimal (least squares)
    num = sum(sum(w0[j] * cov(i, j) for j in w0) * (mu_raw[i] - rf) for i in held)
    den = sum(sum(w0[j] * cov(i, j) for j in w0) ** 2 for i in held)
    gamma_impl = num / den if den else None

    summary = []
    plan_final = {}
    for s in held:
        a_, *_ = act[s]; sh = -held[s]['shares'] if a_ == 'exit' else max(int(round((w[s] - w0[s]) * W / S[s])), -held[s]['shares'])
        if sh < 0: plan_final[s] = -sh * (S[s] - held[s]['cost_basis'] / held[s]['shares'])
    plan.clear(); plan.update(plan_final)
    # gain harvest: sell and immediately rebuy shares whose gain is cheaper to realize now than later (no wash-sale rule
    # for gains). Take a share when this year's extra tax on it is below the present value of the tax it saves later.
    harvest, hv_gain = {}, dict(plan_final)
    for s in sorted(held, key=lambda k: -(S[k] - held[k]['cost_basis'] / held[k]['shares'])):
        left = held[s]['shares'] + min(int(round((w[s] - w0[s]) * W / S[s])), 0) if act[s][0] != 'exit' else 0
        gps = S[s] - held[s]['cost_basis'] / held[s]['shares']
        if gps <= 0 or left <= 0 or v_later <= 0: continue
        n, tx = 0, 0.0
        for _ in range(int(left)):
            trial = dict(hv_gain); trial[s] = trial.get(s, 0) + gps
            dtax = tax_of(trial) - tax_of(hv_gain)
            if dtax + 2 * spread * S[s] >= gps * v_later: break
            hv_gain = trial; n += 1; tx += dtax
        fee_ = float(os.environ.get('REVIEW_FEE', cfg.get('fee_per_trade', 0.0)))
        nb = n * gps * v_later - tx - 2 * spread * S[s] * n - 2 * fee_  # 2026-10-12: a sell and a rebuy ticket ($6 each); one less if it rides an add's ticket
        if n and nb > 0: harvest[s] = dict(shares=n, gain=round(n * gps, 2), tax_now=round(tx, 2),
                                pv_tax_saved_later=round(n * gps * v_later, 2), fees=2 * fee_, net_benefit=round(nb, 2))
    # Price-only decider (Kilian 2026-09-29): with the research held fixed in dollars (no fresh catalyst), the
    # quarter-Kelly share count as a function of the name's own price, the rest of the book at today's suggested
    # weights. Acting only when the change is worth at least MIN_TRADE of wealth keeps it from churning.
    MIN_TRADE = cfg.get('min_trade_share_of_wealth', 0.005)
    FEE_MODE = os.environ.get('REVIEW_FEE_MODE', cfg.get('fee_hurdle', 'ce_gain'))
    FEE = float(os.environ.get('REVIEW_FEE', cfg.get('fee_per_trade', 0.0)))  # flat brokerage fee per order (Kilian 2026-10-02: $6)
    for s in alloc: mu_at[s] = eff_curve(s)
    cash_w = max(1 - sum(w.values()), 0)
    # Kilian 2026-10-14 20:57Z ("resize them to only be based on the size of the active book (not the uninvested cash)"):
    # the price levels below are measured on the stock book (held stocks at market plus today's suggested buys), not on
    # the $24k sizing book. Today's plan (the joint solve above) and the cash that funds adds stay on the sizing book.
    # On the stock book one share is a bigger slice, so the quarter-Kelly count and the tax no-trade band (in shares)
    # move by a whole share on a smaller price move.
    LEVEL_BOOK = os.environ.get('REVIEW_LEVEL_BOOK') or cfg.get('level_book', 'stock')
    Wd = sum(held[j]['shares'] * S[j] for j in held) + sum(alloc[j]['dollars'] for j in alloc) if LEVEL_BOOK == 'stock' else W
    wd = {j: w[j] * W / Wd for j in w}  # today's planned weights as shares of that book

    MU_D = lambda s: (mu_true_at if MS and s in held else mu_at)[s]  # max_sharpe: held names re-priced on the mu_eff curve

    def ms_target(s, P):
        """max_sharpe (Kilian 2026-10-19): the Sharpe-optimal target in shares at price P, unrounded and before caps: the
        name re-priced on its mu_eff curve (research fixed in dollars), the other names at their targets, $24k book."""
        W_ = W + held[s]['shares'] * (P - S[s])
        others = sum(v * cov(s, j) for j, v in SZ['fixed'].items() if j != s) * W / W_
        return (mu_true_at[s](P) - rf - GSTAR * others) / (GSTAR * sig[s] ** 2) * W_ / P, W_

    def raw_shares(s, P):
        """Unrounded quarter-Kelly share count at price P (before caps), the rest of the book at today's weights."""
        own = held[s]['shares'] if s in held else 0
        W_ = Wd + own * (P - S[s])  # the level book moves with the name's own price
        others = sum(wd[j] * cov(s, j) for j in wd if j != s) * Wd / W_
        return (mu_at[s](P) - rf - GAMMA * others) / (GAMMA * sig[s] ** 2) * W_ / P, W_

    def target_shares(s, P, base, fee=True):
        """Today's suggested size, scaled by how much the Kelly size changes between today's price and P (so the
        decider agrees with today's suggestion at today's price), then capped like a new position."""
        r = res[s]
        if below(s, P): return 0
        if s in TPX and P >= TPX[s]: return 0  # expected-value take-profit: full exit (Kilian 2026-10-29)
        if MS and s in held:  # Kilian 2026-10-19: the Sharpe-optimal target itself at P (not today's plan rescaled), so a
            # name below its target trims only once the target falls below the shares held; same tax band as today's plan
            n, W_ = ms_target(s, P)
            if n < held[s]['shares'] and not below(s, P):
                n = min(n + sell_cost(s, P)[0] / (GSTAR * sig[s] ** 2) * W_ / P, held[s]['shares'])
        else:
            n0, _ = raw_shares(s, S[s]); n1, W_ = raw_shares(s, P)
            # scale today's size by the relative change in the Kelly size (Kilian 2026-10-02: an additive change took the
            # uncapped Kelly slope, several times today's capped size, and trimmed AVGO on a 2% move)
            n = base * n1 / n0 if n0 > 0 and n1 > 0 else (base + (n1 - n0) if n0 > 0 else base)
            if s in held and n < held[s]['shares'] and not below(s, P):  # a trim must also clear the tax on the gain (sell_cost, per year)
                n = min(n + sell_cost(s, P)[0] / (GAMMA * sig[s] ** 2) * W_ / P, held[s]['shares'])
        c_ = caps(s, P); sb = c_.pop('stop_budget', None)
        lim = min(c_.values()) * W_ / P
        if sb is not None:  # the stop budget stays a share of total wealth (the $24k book), not of the level book
            lim = min(lim, sb * (W + (held[s]['shares'] if s in held else 0) * (P - S[s])) / P)
        n = min(max(n, 0), lim)
        n_raw = n; n = int(math.floor(n + 0.5))  # round, not truncate, so a tiny move does not drop a whole share
        if n != base and fee:  # the trade must earn its brokerage fee (Kilian 2026-10-02): CE gain over a year vs the fee
            if FEE_MODE == 'trade_share':  # fee at most fee_max_share_of_trade of the trade's value
                if abs(n - base) * P * cfg.get('fee_max_share_of_trade', 0.02) < FEE: return base
            else:
                gain = GAMMA * sig[s] ** 2 * P * P / W_ / 2 * ((base - n_raw) ** 2 - (n - n_raw) ** 2)
                if gain < FEE: return base
        if n > base:
            if r['target'] <= P: return base  # entry gate: no adds unless the target is above the price
            n = min(n, base + int(cash_w * W // P))  # adds limited to the cash left after today's plan
        return n

    dec = {}
    for s in names:
        if s in cfg.get('decider_exclude', []) or (res[s].get('method') or {}).get('special_situation'): continue  # deal-driven value (GPRO): price action says nothing
        base = int(round(w[s] * W / S[s]))
        grid = sorted({round(S[s] * (1 + x / 100), 2) for x in range(-35, 51)} | {res[s]['stop_loss']})
        sched = [(P, target_shares(s, P, base)) for P in grid]
        step = lambda P: max(1, math.ceil(MIN_TRADE * Wd / P))
        # noise floor (Kilian 2026-10-02): no trigger inside one week's one-sigma implied move, so a normal day or two
        # of drift with no news never flips a signal
        nf = (res[s].get('iv30') or sig[s]) * math.sqrt(cfg.get('noise_floor_days', 5) / 252)
        up = next(((P, n) for P, n in sched if P > S[s] * (1 + nf) and base - n >= step(P)), None)
        dn = next(((P, n) for P, n in reversed(sched) if P < S[s] * (1 - nf) and n - base >= step(P)), None)
        zero_t = MS and s in held and target_shares(s, S[s], base, fee=False) == 0  # 2026-10-19: Sharpe-optimal target already 0
        if s in ZHOLD: up = None  # option (b): no trim level before the hold date (the page says 'held until')
        ex = next((P for P, n in reversed(sched) if P < S[s] and n == 0 and base > 0), None) if not zero_t else None
        fx = next((P for P, n in sched if P > S[s] and n == 0), None) if base > 0 and not zero_t else None  # full exit above spot (take-profit rule); none when the target is already 0 (trimmed a share a day, TP stays q75)
        pick = [round(S[s] * (1 + x / 100), 2) for x in (-20, -10, 0, 10, 20)]
        m1 = (res[s].get('iv30') or sig[s]) * math.sqrt(21 / 252)  # one-month one-sigma move: beyond it, check for news first
        chk = lambda P: abs(P / S[s] - 1) > m1
        dec[s] = dict(base_shares=base, min_trade_share_of_wealth=MIN_TRADE,
                      next_trim=dict(price=up[0], shares=up[1], news_check=chk(up[0])) if up else None,
                      next_add=dict(price=dn[0], shares=dn[1], news_check=chk(dn[0])) if dn else None,
                      one_month_sigma=round(m1, 4), noise_floor=round(nf, 4), fee_per_trade=FEE,
                      exit_below=ex if ex and dn is None else (res[s]['stop_loss'] if FSE else None),
                      full_exit=fx,
                      ladder=[dict(price=P, shares=target_shares(s, P, base), news_check=chk(P)) for P in pick],
                      schedule=[dict(price=P, shares=n) for P, n in sched],
                      note='Target shares at each price with the research fixed in dollars (no fresh catalyst); '
                           'act only when the change is worth at least MIN_TRADE of wealth.')
        json.dump(dict(symbol=s, as_of=date, spot=S[s], **dec[s]), open(os.path.join(A.HERE, 'runs', date, s, 'decider.json'), 'w'), indent=1)
        print(f"{s:6} decider: now {base} sh; trim above {up}; add below {dn}; ladder {[(d['price'], d['shares']) for d in dec[s]['ladder']]}")

    # Take-profit for held names (Kilian 2026-10-29 card 'Expected value vs cash', replacing the 2026-10-06 last-share rule
    # with its q75 fallback): TPX, where the 12-month expected value no longer beats the cash sleeve. target_shares is 0 at
    # or above it, so the decider's full exit and the next-share table's last trim sit there. take_profit_q75 and
    # reward_risk (q75-based, used by the conviction rubric) stay.
    import combine as _cb
    for s in held:
        r = res[s]; rp = os.path.join(A.HERE, 'runs', date, s, 'result.json')
        if 'take_profit_q75' not in r: r['take_profit_q75'] = r['take_profit']; r['p_touch_tp_q75'] = r.get('p_touch_tp')
        tp = TPX[s]
        Ks = [l['K'] for l in r['ladder']]; sf = _cb.survival_fn(Ks, [l['final'] for l in r['ladder']])
        Th = json.load(open(os.path.join(A.HERE, 'priors', date, f'{s}.json')))['horizon_years']  # not T: sell_cost spreads over T years
        r.update(take_profit=tp, take_profit_basis='expected value: above it the 12-month expected price no longer beats the 4% cash sleeve (full exit)',
                 p_touch_tp=round(_cb.touch_prob(r['spot'], tp, Th, sf, r['target']), 3) if tp > r['spot'] else 1.0)
        if r.get('position'): r['position']['to_take_profit'] = round(r['position']['shares'] * (r['take_profit'] - r['spot']), 2)
        json.dump(r, open(rp, 'w'), indent=1)

    # Invested-basis book Sharpe, as allocate.py scores new buys (Kilian 2026-10-03): held names at cost, today's suggested
    # and committed buys (portfolio.json sharpe_combo: staged new names, open limit orders) at their dollars; cash left out.
    _pf = os.path.join(A.HERE, 'runs', date, 'portfolio.json')
    _pc = json.load(open(_pf)) if os.path.exists(_pf) else {}
    sdol = {x: held[x]['cost_basis'] for x in held}
    sdol.update({x: alloc[x]['dollars'] for x in alloc})
    for x, c_ in ((_pc.get('sharpe_combo') or {}).get('committed') or {}).items():
        if x in res and x not in sdol: sdol[x] = c_['shares'] * c_['price']
    for x in sdol:
        if x not in mu: mu[x] = (CONV.get(x) or {}).get('mu_eff', A.moments(res[x])[0]); sig[x] = A.moments(res[x])[1]
    def book_sharpe(dol):
        tot = sum(dol.values()); ks = list(dol); w_ = {k: dol[k] / tot for k in ks}
        v_ = math.sqrt(sum(w_[a_] * w_[b_] * rho(a_, b_) * sig[a_] * sig[b_] for a_ in ks for b_ in ks))
        return (sum(w_[k] * mu[k] for k in ks) - rf) / v_ if v_ else -1e9
    def sharpe_add(s, n):
        sh0 = book_sharpe(sdol); best = (0, sh0)
        for k in range(1, n + 1):
            v = book_sharpe(dict(sdol, **{s: sdol[s] + k * S[s]}))
            if v > best[1] + 1e-9: best = (k, v)
        return best[0], best[1], sh0
    sharpe_adds = {}

    # Next marginal share (Kilian 2026-10-08: "i should be able to clearly see when to add/trim the next marginal share
    # for each name"). From the shares actually held, the price at which the decider's target count first drops by one
    # trade lot (trim) or rises by one (add), on a 0.25% grid, research fixed in dollars. A lot is one share unless the
    # $6 fee is more than fee_max_share_of_trade of one share's value (then the fewest shares that clear it). No level
    # sits inside a normal week's implied move unless the signal already holds at today's price. An add must also raise
    # the invested-basis book Sharpe at that price (Kilian 2026-10-07 card "Sharpe test"), else the scan keeps going down.
    # The 1%-of-wealth minimum used by next_trim/next_add does not apply here.
    fmax = cfg.get('fee_max_share_of_trade', 0.05)
    def sharpe_at(s, P, k):
        keep = mu[s]; mu[s] = mu_true_at[s](P)  # Kilian 2026-10-09 'go': was mu_at (sizing return, options part doubled for half Kelly), which passed adds the book test fails
        try: return book_sharpe(dict(sdol, **{s: sdol[s] + k * P})) - book_sharpe(sdol)
        finally: mu[s] = keep
    # Kilian 2026-10-20 card 19:34Z "Whole-book test": with Sharpe-optimal sizing an add is tested on the whole book, as the
    # targets are set: utility w'(mu_eff - rf) - GSTAR/2 w'Sigma w with weights as shares of the $24k book at market value,
    # the 4% sleeve as the rest (allocate.ADD_TEST; 'invested_sharpe' rolls back to the stocks-only Sharpe at cost). The
    # stocks-only test ignored the cash and the 15% cap: at the 10-19 marks it stopped adds at ~39% in stocks because the
    # small ABT-heavy stock mix out-scored the capped target mix. Share counts stay capped by the target (15% ceiling).
    if MS and getattr(A, 'ADD_TEST', 'invested_sharpe') == 'whole_book':
        mdol = {x: held[x]['shares'] * S[x] for x in held}; mdol.update({x: v for x, v in sdol.items() if x not in held})
        def book_U(dol, W_=W):
            ks = list(dol)
            return (sum(dol[k] / W_ * (mu[k] - rf) for k in ks)
                    - GSTAR / 2 * sum(dol[a_] * dol[b_] / W_ ** 2 * rho(a_, b_) * sig[a_] * sig[b_] for a_ in ks for b_ in ks))
        def sharpe_add(s, n):
            u0 = book_U(mdol); best = (0, u0)
            for k in range(1, n + 1):
                v = book_U(dict(mdol, **{s: mdol[s] + k * S[s]}))
                if v > best[1] + 1e-12: best = (k, v)
            return best[0], best[1], u0
        def sharpe_at(s, P, k):
            keep = mu[s]; mu[s] = mu_true_at[s](P)
            try:
                W_ = W + held[s]['shares'] * (P - S[s]); b_ = dict(mdol, **{s: held[s]['shares'] * P})
                return book_U(dict(b_, **{s: (held[s]['shares'] + k) * P}), W_) - book_U(b_, W_)
            finally: mu[s] = keep
    # Kilian 2026-10-29 card 'Per-name noise band': a trim or add rung is at least max(floor, the name's own day-to-day target
    # noise) of total wealth, the noise being the rms daily change of its Sharpe-optimal target weight over the last
    # window_sessions close runs before D (runs/*/review_summary.json sizing.target_weight; measured median across held
    # names 1.0% of book, 10-19 to 10-28). A gap smaller than that is the model's own daily noise, not a signal. The
    # expected-value take-profit, the stop and the first add at Entry below are not bound by it.
    NB = cfg.get('min_trade_noise_band') or {}
    _past = sorted(p for p in glob.glob(os.path.join(A.HERE, 'runs', '*', 'review_summary.json')) if p.split(os.sep)[-2] < date)
    _tw = []
    for p in _past[-(int(NB.get('window_sessions', 10)) + 1):]:
        try: _tw.append(((json.load(open(p)).get('sizing') or {}).get('target_weight')) or {})
        except Exception: pass
    def noise_band(s):
        xs = [t[s] for t in _tw if s in t]
        dv = [b - a for a, b in zip(xs, xs[1:])]
        rms = math.sqrt(sum(v * v for v in dv) / len(dv)) if len(dv) >= int(NB.get('min_changes', 3)) else 0.0
        return max(float(NB.get('floor', 0.01)), rms), rms, len(dv)
    for s in held:
        if s not in dec: continue
        h, base = held[s]['shares'], dec[s]['base_shares']
        band, band_rms, band_n = noise_band(s) if NB else (0.0, 0.0, 0)
        lot = lambda P: max(1, math.ceil(band * W / P - 1e-9), math.ceil(FEE / (fmax * P) - 1e-9) if FEE else 1)
        nf = dec[s]['noise_floor']; m1 = dec[s]['one_month_sigma']
        def level(P, n_after, lot_):  # trims step up in price, adds step down
            return dict(price=round(P, 2), shares=lot_, total_after=n_after,
                        pct_from_last=round(P / S[s] - 1, 4), now=abs(P / S[s] - 1) < 1e-9, big_move=abs(P / S[s] - 1) > m1)
        out_t = []
        need = h
        for i in range(0, 241):  # up to +60%
            P = S[s] * (1 + 0.0025 * i)
            if len(out_t) >= 3 or need <= 0: break
            L = lot(P); goal = max(need - L, 0)
            if i == 0:  # 2026-10-13 (Kilian 'yes'): every share the model already sells at today's price is one 'now' level
                # (TJX 5 -> 0 showed 1 now and the rest pushed up to the noise floor); whole lots, never below 0
                t0 = target_shares(s, P, base, fee=False)
                if t0 <= goal: goal = max(need - L * math.ceil((need - max(t0, 0)) / L - 1e-9), 0)
                if s in ZHOLD and max(t0, 0) < need:  # option (b): target 0, the paced trims start on the hold date, none today
                    day = 0
                    while need > max(t0, 0) and len(out_t) < 3:
                        k_ = min(TRIM_PACE or need, need - max(t0, 0)); day += 1
                        out_t.append(dict(level(P, need - k_, k_), now=False, pace_day=day, held_until=ZHOLD[s]['until'])); need -= k_
                    continue
                if MS and TRIM_PACE and need - max(t0, 0) > TRIM_PACE:  # 2026-10-19: toward a lower Sharpe-optimal target, one
                    day = 0  # share a day at today's price ('now' is today's; pace_day 2, 3 are the next days)
                    while need > max(t0, 0) and len(out_t) < 3:
                        k_ = min(TRIM_PACE, need - max(t0, 0)); day += 1
                        out_t.append(dict(level(P, need - k_, k_), now=day == 1, pace_day=day)); need -= k_
                    continue
            if i and P >= TPX.get(s, float('inf')):  # the expected-value take-profit: every share left goes in one order there
                out_t.append(level(max(TPX[s], out_t[-1]['price'] if out_t else 0), 0, need)); need = 0; break
            if target_shares(s, P, base, fee=False) <= goal:
                Pq = P if i == 0 else max(P, S[s] * (1 + nf), out_t[-1]['price'] if out_t else 0)
                out_t.append(level(Pq, goal, need - goal)); need = goal
        out_a = []
        have = h; stop = res[s]['stop_loss']; gate_bound = not bar_ok(s, S[s])
        for i in range(0, 241):  # down to -60% or the stop
            P = S[s] * (1 - 0.0025 * i)
            if len(out_a) >= 3 or below(s, P): break
            L = lot(P); goal = have + L
            if i == 0 and not getattr(A, 'ADD_PACE_SHARES', 1):  # Kilian 2026-10-20 card 'Buy to size': every share the model
                # buys at today's price is one 'now' level, sized to the count with the best book Sharpe (strict test kept)
                kmax = int(math.floor(target_shares(s, P, base, fee=False) + 1e-9)) - h
                best = max(((sharpe_at(s, P, k), k) for k in range(L, kmax + 1)), default=(0, 0))  # at least one noise-band lot
                if best[0] > 1e-9 and bar_ok(s, P): out_a.append(level(P, h + best[1], best[1])); have = h + best[1]
                continue
            if target_shares(s, P, base, fee=False) >= goal and sharpe_at(s, P, goal - h) > 1e-9 and bar_ok(s, P):
                Pq = P if i == 0 else min(P, S[s] * (1 - nf), out_a[-1]['price'] if out_a else P)
                if i and gate_bound and A.BAR_BASIS == 'target' and not out_a:  # Kilian 2026-10-22 20:29Z 'yes': when the beta-return
                    # floor is what holds the add back, the first add sits where the floor opens (the page's Entry below, a fixed price),
                    # not a week's noise lower, and buys the whole gap to the target there in one order (Kilian 20:46Z 'yes');
                    # later rungs keep the noise rule
                    E = round((res[s]['target'] + divs.dps(s)) / (1 + BAR[s]), 2) - 0.01  # 2026-10-27: same Entry below as the page (yield at the entry price)
                    Pq = max(P, E)
                    goal = max(goal, int(math.floor(target_shares(s, P, base, fee=False) + 1e-9))); L = goal - have
                out_a.append(level(Pq, goal, L)); have = goal
        st = (cfg.get('staged_entries') or {}).get(s)
        dec[s]['marginal'] = dict(held=h, trim=out_t, add=out_a, stop=stop,
                                  lot_rule=f'at least {band:.1%} of the book (noise band)' if band else 'one share',
                                  noise_band=dict(min_trade=round(band, 4), target_noise=round(band_rms, 4), changes=band_n,
                                                  min_shares_at_last=lot(S[s])) if NB else None,
                                  staged_after=st['after'] if st and today < st['after'] else None,
                                  staged_event=st['event'] if st and today < st['after'] else None)
        if s in ZHOLD: dec[s]['marginal']['zero_target_hold'] = ZHOLD[s]  # option (b): page shows 'held until <date> ...'
        TH = (cfg.get('trim_hold_until') or {}).get(s)  # per-name trim hold to a date (Kilian 2026-10-20 card 'Hold to print': GOOGL)
        if TH and today < TH['until']:
            for x in out_t:
                if x.get('now') or x.get('pace_day'): x['now'] = False; x['held_until'] = TH
        if PENDING:  # trims wait for the open sizing card(s); the levels stay for reference
            for x in out_t:
                if x.get('now') or x.get('pace_day'): x['now'] = False; x['held_for_decision'] = PENDING[0].get('question') or PENDING[0]['id']
        if s in RERUN:  # adds and trims wait for the post-earnings rerun; the levels stay for reference
            dec[s]['marginal']['earnings_rerun'] = RERUN[s]
            for x in out_t + out_a:
                if x.get('now') or x.get('pace_day'): x['now'] = False; x['held_for_rerun'] = f"the full rerun after the {RERUN[s].get('report_date')} earnings print"
        if s in BM:  # 2026-10-19 'Add flag': the add levels pause until the block ends; the page's 'from <date>, after <event>' shows it
            bm = BM[s]; mg = dec[s]['marginal']
            mg['big_miss_block'] = dict(block_from=bm['block_from'], block_until=bm['block_until'], reason=bm['reason'], source=bm.get('source'))
            if not mg['staged_after'] or bm['block_until'] > mg['staged_after']:
                mg['staged_after'] = bm['block_until']
                mg['staged_event'] = f"the big-miss pause ({bm['reason']})"
            for x in out_a: x['paused_until'] = bm['block_until']; x['now'] = False
        json.dump(dict(symbol=s, as_of=date, spot=S[s], **dec[s]), open(os.path.join(A.HERE, 'runs', date, s, 'decider.json'), 'w'), indent=1)
        print(f"{s:6} marginal: held {h}; trim {[(x['price'], x['shares'], x['total_after']) for x in out_t]}; add {[(x['price'], x['shares'], x['total_after']) for x in out_a]}")

    for s in held:
        r = res[s]; a, why, g, band = act[s]
        trade_sh = int(round((w[s] - w0[s]) * W / S[s]))
        if a == 'exit': trade_sh = -held[s]['shares']
        trade_sh = max(trade_sh, -held[s]['shares'])
        if a != 'exit':  # label from the net trade from today's book (the loop's last state sits on the band edge)
            a0, why0, g0, _ = first[s]
            a = 'trim' if trade_sh < 0 else 'add' if trade_sh > 0 else 'hold'
            why = why0 if a0 == a else (why0 + '; after the other trades' + (' it is still worth trimming' if a == 'trim' else ' it is worth adding' if a == 'add' else ' it sits inside the band'))
            g = g0
        if trade_sh and a != 'exit':  # the trade must earn its brokerage fee: CE gain over a year vs FEE
            n_star = held[s]['shares'] + (w[s] - w0[s]) * W / S[s]
            gain = GAMMA * sig[s] ** 2 * S[s] ** 2 / W / 2 * ((held[s]['shares'] - n_star) ** 2 - (held[s]['shares'] + trade_sh - n_star) ** 2)
            small = abs(trade_sh) * S[s] * cfg.get('fee_max_share_of_trade', 0.02) < FEE if FEE_MODE == 'trade_share' else gain < FEE
            if small:
                why = (f"{why}; but the ${FEE:.0f} fee is more than {cfg.get('fee_max_share_of_trade', 0.02):.0%} of a {trade_sh:+d} sh trade" if FEE_MODE == 'trade_share'
                       else f"{why}; but a {trade_sh:+d} sh trade gains only ${gain:.2f} a year, less than the ${FEE:.0f} fee")
                trade_sh, a = 0, 'hold'
        nj = os.path.join(A.HERE, 'runs', date, s, 'news.json')  # daily news check (Kilian 2026-10-02, agents/news.md)
        news = json.load(open(nj)) if os.path.exists(nj) else None
        if news and news.get('verdict') == 'material' and a != 'exit' and (res[s].get('research_as_of') or '') < date:
            if trade_sh: why = f"{why}; on hold until the agents re-research a material development from today"
            trade_sh, a = 0, 'hold'
        st = (cfg.get('staged_entries') or {}).get(s)
        if st and trade_sh > 0 and today < st['after']:  # staged entry (Kilian 2026-10-01): the second tranche waits for the print
            trade_sh = 0
            a, why = 'hold', f"second tranche waits for {st['event']}; after it the model sizes the add on the $24k book (Kilian 2026-10-13: re-size, not the Oct 1 share count) if {st['condition']}"
        if trade_sh > 0 and a != 'exit' and not bar_ok(s, S[s]):  # Kilian 2026-10-22 00:47Z: beta-adjusted return floor on adds
            trade_sh, a = 0, 'hold'
            why = f"{why}; but no add: {'target return' if A.BAR_BASIS == 'target' else 'expected return'} {A.bar_return(s, mu_true_at[s](S[s]), res[s]['target'], S[s]):.1%} is not above its beta-adjusted return {BAR[s]:.1%}"
        if s in BM and a not in ('trim', 'exit'):  # Kilian 2026-10-19 card 'Add flag': no adds for 6 months after a big earnings miss
            if trade_sh > 0: trade_sh, why = 0, f"{why}; but no add: {BM[s]['reason']} (staged tranches too; trims and stops unchanged)"
            else: why = f"{why}; adds paused: {BM[s]['reason']}"
            a = 'hold'
        _th = (cfg.get('trim_hold_until') or {}).get(s)
        if _th and today < _th['until'] and trade_sh < 0 and a != 'exit':
            why = f"{why}; trim held until {_th['until']}: {_th['reason']}"; trade_sh, a = 0, 'hold'
        if s in RERUN and trade_sh and a != 'exit':  # Kilian 2026-10-21: adds and trims wait for the post-earnings rerun
            why = f"{why}; {'add' if trade_sh > 0 else 'trim'} on hold until the rerun after the {RERUN[s].get('report_date')} earnings print"; trade_sh, a = 0, 'hold'
        if PENDING and trade_sh < 0 and a != 'exit':  # Kilian 2026-10-20: a trim waits while a sizing card is open
            why = f"{why}; trim on hold until the open card is answered: {PENDING[0].get('question') or PENDING[0]['id']}"; trade_sh, a = 0, 'hold'
        if trade_sh > 0:  # Sharpe test for adds (Kilian 2026-10-07 card "Sharpe test"): an add to a held name must raise the
            # invested-basis book Sharpe, as a new buy must (allocate.py); shares by exhaustive search over 0..trade_sh
            k_best, sh_best, sh0 = sharpe_add(s, trade_sh)
            if k_best < trade_sh:
                why = (f"{why}; the Sharpe test caps it at +{k_best} sh (more shares lower the book's Sharpe)" if k_best
                       else f"{why}; but adding does not raise the book's Sharpe, so hold")
                trade_sh = k_best; a = 'add' if k_best else 'hold'
            sharpe_adds[s] = dict(proposed=int(round((w[s] - w0[s]) * W / S[s])), kept=k_best, base_sharpe=round(sh0, 4), sharpe_after=round(sh_best, 4))
            n_now = sum(x['shares'] for x in ((dec.get(s) or {}).get('marginal') or {}).get('add', []) if x.get('now'))
            if trade_sh > n_now:  # Kilian 2026-10-12 ("should i buy 1 or 2 tjx?"): one answer per name. The add today is capped
                # at what the next-share table says to buy at today's price; further shares wait for its lower levels.
                why = f"{why}; the next-share table buys +{n_now} sh now, the rest at its lower add levels" if n_now else f"{why}; the next-share table has no add at today's price, so hold"
                trade_sh = n_now; a = 'add' if n_now else 'hold'
        if MS:  # Kilian 2026-10-19: say where the Sharpe-optimal sizing wants the name
            nT = sizing['target_shares'][s]
            why = f"{why}; Sharpe-optimal target {nT:.1f} sh" + (f" (a trim sells {TRIM_PACE} sh a day toward it)" if s in paced and trade_sh < 0 else '')
            if s in ZHOLD: why = f"{first[s][1]}; Sharpe-optimal target 0 sh, but {ZHOLD[s]['note']}"
        cs, q, tau, lt = sell_cost(s, S[s])
        basis_ps = held[s]['cost_basis'] / held[s]['shares']
        g_trade = -trade_sh * (S[s] - basis_ps) if trade_sh < 0 else 0.0
        rest = {k: v for k, v in plan_final.items() if k != s}
        tax_now = tax_of(dict(rest, **{s: g_trade})) - tax_of(rest) if trade_sh < 0 else 0.0

        def book_at(P):
            W_ = W + held[s]['shares'] * (P - S[s]); ww = {j: w0[j] * W / W_ for j in w0}
            ww[s] = held[s]['shares'] * P / W_
            return ww, W_

        def flip(direction):
            for i in range(1, 800):
                P = S[s] * (1 + direction * 0.0025 * i)
                if P <= 0: return None
                ww, _ = book_at(P)
                _, a_, *_ = solve_one(s, ww, P, MU_D(s)(P))
                if direction > 0 and a_ in ('trim', 'exit'): return round(P, 2)
                if direction < 0 and a_ in ('add', 'exit'): return (round(P, 2), a_)
            return None

        up = flip(+1) if (a == 'hold' or a == 'add') and s not in ZHOLD else None  # option (b): held, no trigger
        dn = flip(-1) if a == 'hold' else None
        rdate = r.get('research_as_of') or date
        r0p = os.path.join(A.HERE, 'runs', rdate, s, 'result.json'); r0 = json.load(open(r0p)) if os.path.exists(r0p) else r
        S0, T0 = r0['spot'], r0['target']
        elapsed = max((dt.date.fromisoformat(date) - dt.date.fromisoformat(rdate)).days, 0) / 365
        progress = (S[s] - S0) / (T0 - S0) if abs(T0 - S0) > 1e-9 else None
        ex90 = None
        if T0 > S0:
            P = S0 + 0.9 * (T0 - S0); ww, W_ = book_at(P)
            nw, a90, why90, g90, _ = solve_one(s, ww, P, MU_D(s)(P))
            sh90 = max(int(round((nw - ww[s]) * W_ / P)), -held[s]['shares']) if a90 != 'hold' else 0
            if a90 == 'exit': sh90 = -held[s]['shares']
            tax90 = tax_of(dict(rest, **{s: -sh90 * (P - basis_ps)})) - tax_of(rest) if sh90 < 0 else 0.0
            ex90 = dict(price=round(P, 2), action=a90, reason=why90, shares=sh90, weight=round(ww[s], 4), new_weight=round(nw, 4),
                        mu=round(MU_D(s)(P), 4), tax=round(tax90, 2))
        out = dict(symbol=s, as_of=date, spot=S[s], shares=held[s]['shares'], cost_basis=held[s]['cost_basis'],
                   gain_fraction=round(q, 4), tax_rate=round(tau, 4), long_term=lt,
                   weight=round(w0[s], 4), suggested_weight=round(w[s], 4), action=a, reason=why, trade_shares=trade_sh,
                   tax_on_trade=round(tax_now, 2), gain_harvest=harvest.get(s), gradient=None if g is None else round(g, 4),
                   band=None if band is None else [round(band[0], 4), round(band[1], 4)],
                   mu=round(mu[s], 4), sigma=round(sig[s], 4), rf=rf, gamma=GAMMA, mu_before_conviction=round(mu_raw[s], 4), conviction=CONV.get(s),
                   staged_entry=st, big_miss_block=BM.get(s),
                   sizing=dict(mode=SIZING, **({k: sizing[k][s] for k in ('target_shares', 'target_weight', 'tangency_weight', 'cap_weight', 'mv_shares')} if MS else {}),
                               **(dict(gamma_star=sizing['gamma_star'], trim_paced=s in paced, zero_target_hold=ZHOLD.get(s)) if MS else {})), trim_above=up, add_below=dn[0] if dn and dn[1] == 'add' else None, exit_below=dn[0] if dn and dn[1] == 'exit' else None,
                   at_90pct_of_target=ex90, decider=dec.get(s), target=r['target'], take_profit=r['take_profit'], stop_loss=r['stop_loss'],
                   research_as_of=rdate, research_spot=S0, research_target=T0,
                   progress_to_target=None if progress is None else round(progress, 3), horizon_elapsed=round(elapsed, 3),
                   ahead_of_schedule=bool(progress is not None and progress >= 0.75 and elapsed <= 0.25 and T0 > S0),
                   tax_assumptions={k: v for k, v in tax.items() if k != 'lots'},
                   method=__doc__.split('\n\n')[0] if MS else 'Trim, hold or add for names already held: a tax-aware mean-variance rebalance with a no-trade band.')
        json.dump(out, open(os.path.join(A.HERE, 'runs', date, s, 'review.json'), 'w'), indent=1)
        summary.append(out)
        print(f"{s:6} {a:5} {trade_sh:+4d} sh  w={w0[s]*100:5.1f}% -> {w[s]*100:5.1f}%  g={g if g is None else round(g, 4)} band={band and [round(b, 4) for b in band]}  "
              f"tax=${tax_now:,.0f}  trim>{up} add<{dn}  90%: {ex90 and (ex90['action'], ex90['shares'], ex90['new_weight'], ex90['tax'])}")
    U = lambda ww: sum(ww[i] * (mu[i] - rf) for i in ww) - GAMMA / 2 * sum(ww[i] * ww[j] * cov(i, j) for i in ww for j in ww)  # reported at the base gamma
    tot = dict(as_of=date, gamma=GAMMA, implied_gamma_of_current_book=None if gamma_impl is None else round(gamma_impl, 2),
               ce_before=round(U(w0), 4), ce_after=round(U(w), 4),
               tax_total=round(tax_of(plan_final), 2), realized_gain_total=round(sum(plan_final.values()), 2),
               gain_harvest=harvest, harvest_tax_total=round(sum(h['tax_now'] for h in harvest.values()), 2),
               harvest_net_benefit=round(sum(h['net_benefit'] for h in harvest.values()), 2),
               marginal_rate_after_trades=round(marginal(next(iter(held)), 1), 4) if held else None, book_mode=H.get("mode"), total_wealth=round(W, 2), tax_set_aside=H.get('tax_set_aside', 0), cash_left_for_adds=round(cash_left, 2),
               sharpe_test_adds=sharpe_adds, sizing=sizing, live_quotes_as_of=LIVE_AS_OF, tax_assumptions={k: v for k, v in tax.items() if k != 'lots'})
    json.dump(tot, open(os.path.join(A.HERE, 'runs', date, 'review_summary.json'), 'w'), indent=1)
    print(tot)


if __name__ == '__main__':
    import fscache; fscache.install()  # 2026-10-21: read-once cache for the shared network folder (fscache.py; same output)
    main(sys.argv[1])
