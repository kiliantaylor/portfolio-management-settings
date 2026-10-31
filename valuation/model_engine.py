"""Standard valuation model (Option 1, Kilian 2026-10-13): every forecaster hands over one assumption set in a fixed
format (`model_inputs`), this script runs the same DCF + horizon-multiple (+ optional sum-of-the-parts and custom
methods) on each, and the supervisor's reconciled set gives the model's 12-month target. Runs beside the current
target (trial) until Kilian decides how much weight it gets.

Usage:
  python3 model_engine.py check FILE.json            -> validates and values the `model_inputs` in an agent/supervisor
                                                         file (or a bare model_inputs file); prints the result
  python3 model_engine.py run DATE SYM               -> values every agent set and the supervisor's reconciled set,
                                                         writes runs/DATE/SYM/model_engine.json

Model (all money in the set's `unit`, e.g. $M; rates as fractions):
  Years i = 1..N follow the base fiscal year (base.fy_end). For each: revenue = prior x (1 + revenue_growth);
  EBIT = revenue x ebit_margin (GAAP, after stock comp, unless ebit_basis = "adjusted");
  NOPAT = EBIT x (1 - tax_rate); FCFF = NOPAT + D&A - capex - nwc_pct_incr_rev x (revenue - prior revenue)
  [- SBC x (1 - tax) when ebit_basis = "adjusted" and sbc_treatment = "cost"].
  Value at the horizon date H (as_of + 1 year):
   DCF leg: FCFF after H (the year straddling H pro rata) discounted at the WACC to H, plus terminal value at year N
     (Gordon: FCFF_N x (1+g) / (WACC - g); or exit: terminal EV/EBITDA x EBITDA_N), discounted to H.
   Multiple legs: each horizon multiple x its metric at H (EV/EBITDA, EV/EBIT, EV/sales, P/E or FCF yield), next or
     last twelve months (`basis`), interpolated between fiscal years; P/E and FCF yield can take a stated EPS / FCF
     per share. Diagnostic: the WACC at which the DCF equals the multiples (reverse DCF).
   SOTP leg (optional): sum of segment NTM metric x multiple.
   Equity = EV - net debt at H - minorities + adjustments (probability-weighted); per share = / shares at H.
   Net debt at H = net debt now - FCFF earned between the balance date and H x (1 - interest drag) + shareholder
   returns paid (dividends + buybacks); shares at H = shares now x (1 + shares.annual_change) ^ years.
   Custom methods: any other per-share value with its own weight and explanation (allowance for creative modeling).
  Horizon value = weighted mean of the legs (weights normalised). Scenarios (bear/base/bull) shift growth, margins,
  the multiple and the WACC; the 12-month distribution is a probability-weighted mix of lognormals around the
  scenario values (scenario_vol, default 0.15), and the model target is its median.

No DCF where it cannot be accurate (Kilian 2026-10-21 07:23Z): dcf_weight defaults to 0 and is forced to 0 for any name
not in data/review_settings.json dcf_policy.allowed; with no DCF leg the set needs no discount_rate and takes 3 projected
years (2 detailed years plus the year the horizon multiple reads). Sets written before keep their years, flagged.

Deep-modeling legs (Kilian 2026-10-19 20:31Z: "modeling work you would expect from a high-quality analysis rather than just
slapping multiples on stuff"); all optional, a set without them values exactly as before:
  segment_build: per-segment revenue growth and EBIT margin by year; total revenue and EBIT are the sums (plus any
     corporate_ebit per year), and every other driver (tax, D&A, capex, NWC, SBC) still applies to the total.
  rnpv: risk-adjusted NPV of programmes (peak sales, launch, ramp, loss of exclusivity, margin, probability of
     success, probability-weighted development costs), at the horizon; mode "leg" (own weight) or "adjustment".
  nav: reserve-life net asset value (production x (price deck x (1 - royalty) - unit cost) - capex, taxed, owned share,
     discounted at a real rate), less corporate G&A, x a P/NAV multiple; an EV-level leg.
  reit: P/AFFO (AFFO per share at the horizon, stated or built from the rows) and NAV (forward NOI / cap rate + other
     assets), each its own leg.
  sotp: explicit sum of the parts with a method per part (multiple, value, cash_flows, fixed_dcf, rnpv, nav), EV- or
     equity-level, an optional holding-company discount; its own leg. fixed_dcf (Kilian 2026-10-22 05:07Z: value SpaceX's
     compute segment like the neoclouds): the part states its own segment base, 3 years and signed contracts; the engine
     runs the neoclouds' fixed DCF on them (script rate for the whole name, fade to year 10, steady-state terminal,
     contracts to full term at their credit rate) and blends it with the part's EV/EBITDA multiple at dcf_policy
     segment_dcf.weight (0.6, the neocloud weight).
  stakes: probability-weighted equity stakes (scenario values x probabilities x ownership, less tax on the gain and a
     liquidity discount), added to equity on every leg like adjustments.
  financing_events: probable equity raises, convertibles and large debt deals not yet done (Kilian 2026-10-28): each states
     probability, probability_basis and its effect by the horizon (shares_added, net_debt_change, preferred_added,
     annual_cost after tax); shares, net debt and the preferred claim at H take probability x each, and earnings-based legs
     the after-tax interest on the expected net-debt change plus probability x annual_cost. Events after H add nothing.
  Extra scenario deltas: peak_sales_pct, pos_pct (rNPV), price_pct (NAV commodity deck), cap_rate (REIT NAV),
  stake_pct (stakes), segment-level growth/margin deltas follow revenue_growth / ebit_margin."""
import json, math, os, sys, datetime as dt, copy
HERE = os.path.dirname(os.path.abspath(__file__))
N_ = lambda x: 0.5 * (1 + math.erf(x / math.sqrt(2)))
def Ninv_(p):
    lo, hi = -10.0, 10.0
    for _ in range(100):
        mid = (lo + hi) / 2
        if N_(mid) < p: lo = mid
        else: hi = mid
    return (lo + hi) / 2
Z90 = Ninv_(0.90)  # 1.2816: downside = P10, upside = P90
SD_MIN = 0.02  # a side whose case sits on (or across) the base gets this vol, flagged
SCEN_ALIAS = {'bear': 'downside', 'bull': 'upside'}  # Kilian 2026-10-30: "rename these to upside/downside going forward everywhere"
SCEN_PCTL = {'downside': 10, 'base': 50, 'upside': 90}

def yfrac(a, b): return (dt.date.fromisoformat(b) - dt.date.fromisoformat(a)).days / 365.25

class ModelError(Exception): pass
MAX_YEARS = 5  # Kilian 2026-10-13 16:43Z: "don't try and model out to fy36 that's crazy"
MAX_YEARS_NO_DCF = 3  # Kilian 2026-10-21 07:23Z: "models should generally be 2-3 years of detailed multiples-based analysis"

# Fixed DCF (card dcf_v2_reenable; Kilian chose "Turn it on, tested weights" 2026-10-21 08:05Z). Agents no longer set the rate,
# the horizon past their detailed years, the terminal value or the DCF weight; the script does, from data/review_settings.json
# dcf_policy and data/erp/erp.json (erp_update.py). Evidence: wrds/results/dcf_bias/damodaran_erp.csv, damodaran_weights.csv.
_POL = {}
def dcf_policy():
    if 'p' not in _POL:
        try: _POL['p'] = (json.load(open(os.path.join(HERE, 'data', 'review_settings.json'))).get('dcf_policy') or {})
        except Exception: _POL['p'] = {}
    return _POL['p']

def erp_inputs():
    try: return json.load(open(os.path.join(HERE, 'data', 'erp', 'erp.json')))
    except Exception: return {}

def dcf_allowed(sym):
    """True when the name gets a DCF leg with weight under dcf_policy (mode v2: every name except the exclusions and the
    segments weighted 0; before 2026-10-21 08:05Z only names listed in `allowed`)."""
    pol = dcf_policy()
    if pol.get('mode') != 'v2': return sym in set(pol.get('allowed', []))
    return dcf_weight_for(sym)[0] > 0

def dcf_weight_for(sym, cap_usd_m=None):
    """(weight, why): the DCF weight for a name. Since 2026-10-21 13:50Z the default is 0 and only names in name_weights (the
    neoclouds) get a leg; otherwise a segment listed in segment_weights lowers the default; exclusions get 0."""
    pol = dcf_policy(); ex = pol.get('excluded') or {}
    if sym in ex: return 0.0, f'excluded: {ex[sym]}'
    nw = (pol.get('name_weights') or {}).get(sym)  # Kilian 2026-10-21 13:50Z: only the neoclouds keep the DCF leg
    if nw: return float(nw['weight']), f"named: {nw.get('why', '')}"
    w = pol.get('weight_default', 0.0); why = [f"default {w:.2f}"]
    segs = []
    try: sec = json.load(open(os.path.join(HERE, 'data', 'sectors.json'))).get(sym)
    except Exception: sec = None
    if sec: segs.append('sector:' + sec)
    for g, names in (pol.get('gics_groups') or {}).items():
        if sym in names: segs.append(g)
    if cap_usd_m and cap_usd_m > pol.get('mega_cap_usd_m', 2e5): segs.append('mega_200B')
    sw = pol.get('segment_weights') or {}
    for sg in segs:
        if sg in sw and sw[sg] < w: w = sw[sg]; why.append(f'{sg} {sw[sg]:.2f}')
    return w, '; '.join(why) + (f" (segments: {', '.join(segs)})" if segs else '')

def script_rate(mi, sym, spot, beta):
    """WACC = E/(E+D) x (rf + ERP x (c0 + c1 x beta)) + D/(E+D) x kd x (1 - t): rf = latest 10-year Treasury, ERP = the
    month's Damodaran implied premium, beta = the name's 2-year beta (priors), E = spot x diluted shares, D = positive net
    debt, kd = the set's argued pre-tax marginal cost of debt or rf + the policy spread, t = 21% unless argued."""
    pol = dcf_policy(); e = erp_inputs()
    if e.get('erp') is None or e.get('rf') is None: raise ModelError('data/erp/erp.json has no erp / rf: run erp_update.py')
    if spot is None or beta is None: raise ModelError('the fixed DCF needs the run\'s spot and beta (priors/DATE/SYM.json)')
    c0, c1 = pol.get('beta_line', [0.768, 0.232]); dr = mi.get('discount_rate') or {}
    ke = e['rf'] + e['erp'] * (c0 + c1 * beta)
    sh = mi['shares']; E = spot * need(sh, 'diluted_now', 'shares') * (1e-6 if mi.get('unit') == '$' else 1)
    if mi.get('unit') not in (None, '$M', '$'): raise ModelError(f"unit {mi.get('unit')}: the fixed DCF needs $M or $")
    D = max(need(mi['balance'], 'net_debt', 'balance'), 0) * (1e-6 if mi.get('unit') == '$' else 1)
    kd = dr.get('pre_tax_cost_of_debt') or e['rf'] + pol.get('kd_spread', 0.01); t = dr.get('debt_tax_rate', 0.21)
    w = (ke * E + kd * (1 - t) * D) / (E + D)
    return dict(wacc=w, ke=ke, kd=kd, debt_tax=t, rf=e['rf'], rf_date=e.get('rf_date'), erp=e['erp'], erp_month=e.get('erp_month'),
                beta=beta, equity_weight=E / (E + D), basis=f"rf {e['rf']:.4f} ({e.get('rf_date')}) + ERP {e['erp']:.4f} ({e.get('erp_month')}) x ({c0} + {c1} x beta {beta:.3f}) = ke {ke:.4f}; kd {kd:.4f} x (1 - {t:.2f}); equity weight {E / (E + D):.3f} -> WACC {w:.4f}")

def apply_dcf_policy(mi, sym, spot=None, beta=None):
    """(set, note). Mode v2: the DCF weight and rate are set by script (agents' dcf_weight and discount_rate.wacc are ignored for
    the DCF leg); weight 0 for exclusions, pre-profit names, segments weighted 0, or when spot / beta are missing.
    Before v2: the DCF weight set to 0 when the name is not allowed a DCF."""
    pol = dcf_policy(); w0 = mi.get('dcf_weight', 0.0) or 0.0
    if pol.get('mode') == 'v2' and any(p.get('method') == 'fixed_dcf' for p in ((mi.get('sotp') or {}).get('parts') or [])):
        # segment neocloud DCF (Kilian 2026-10-22 05:07Z, SpaceX compute segment): the script sets the rate, fade and weight
        if spot is None or beta is None: raise ModelError('a fixed_dcf SOTP part needs the run\'s spot and beta (priors/DATE/SYM.json)')
        r_ = script_rate(mi, sym, spot, beta); sw_ = pol.get('segment_dcf', {})
        mi = dict(mi, _seg_dcf=dict(r_, weight=sw_.get('weight', 0.6), weight_why=sw_.get('why', ''), fade_to_year=pol.get('fade_to_year', 10),
                                    g=pol.get('terminal_growth', 0.03), ronic_default=pol.get('ronic_default', 0.15)))
    if pol.get('mode') != 'v2':
        if w0 > 0 and not dcf_allowed(sym): return dict(mi, dcf_weight=0.0), f'DCF weight {w0:.2f} -> 0 (dcf_policy, Kilian 2026-10-21)'
        return mi, None
    cap = spot * mi['shares']['diluted_now'] * (1e-6 if mi.get('unit') == '$' else 1) if spot and (mi.get('shares') or {}).get('diluted_now') else None
    w, why = dcf_weight_for(sym, cap)
    if w > 0 and (spot is None or beta is None): w, why = 0.0, 'no spot / beta for the script rate'
    if w > 0:
        try:
            rows = project(dict(mi, dcf_weight=0.0, _allow_long=True))
            if rows[-1]['nopat'] <= 0: w, why = 0.0, f"pre-profit: NOPAT in the last detailed year {rows[-1]['nopat']:.0f} <= 0"
        except ModelError: pass
    if w <= 0: return dict(mi, dcf_weight=0.0, _dcf=None), f'DCF weight {w0:.2f} -> 0 ({why})'
    r = script_rate(mi, sym, spot, beta)
    ctx = dict(r, weight=w, weight_why=why, fade_to_year=pol.get('fade_to_year', 10), g=pol.get('terminal_growth', 0.03),
               ronic=(mi.get('terminal') or {}).get('ronic') or pol.get('ronic_default', 0.15))
    return dict(mi, dcf_weight=w, _dcf=ctx), f"DCF v2: weight {w0:.2f} -> {w:.2f} ({why}); {r['basis']}"

def need(d, k, where):
    if d.get(k) is None: raise ModelError(f'{where}.{k} is missing')
    return d[k]

def project(mi):
    b = need(mi, 'base', 'model_inputs'); yrs = need(mi, 'years', 'model_inputs')
    if len(yrs) < 3: raise ModelError('give at least 3 projected years')
    cap = MAX_YEARS_NO_DCF if dcf_policy().get('mode') == 'v2' or not (mi.get('dcf_weight', 0.0) or 0) > 0 else MAX_YEARS  # v2: the engine extends to year 10
    if len(yrs) > cap and not mi.get('_allow_long'): raise ModelError(f'{len(yrs)} projected years: use 3' + (f' to {cap}' if cap > 3 else '') + ' (Kilian 2026-10-21: 2-3 detailed years, the third is the year the horizon multiple reads; the engine itself fades the DCF to year 10)')
    rev = need(b, 'revenue', 'base'); end0 = need(b, 'fy_end', 'base'); out = []
    prev = dict(revenue=rev); last = {}
    sb = seg_build(mi, len(yrs))  # 2026-10-19: per-segment driver build (None when the set has no segment_build)
    for i, y in enumerate(yrs, 1):
        y = {**{k: v for k, v in last.items() if k != 'label'}, **y}  # a year inherits drivers it does not restate
        last = y
        if sb:
            r = sb['revenue'][i - 1]; ebit = sb['ebit'][i - 1] + y.get('corporate_ebit', 0)
        else:
            r = prev['revenue'] * (1 + need(y, 'revenue_growth', f'years[{i}]'))
            ebit = r * need(y, 'ebit_margin', f'years[{i}]')
        tax = need(y, 'tax_rate', f'years[{i}]')
        da = r * y.get('da_pct_rev', 0); capex = r * y.get('capex_pct_rev', 0); sbc = r * y.get('sbc_pct_rev', 0)
        dnwc = y.get('nwc_pct_incr_rev', 0) * (r - prev['revenue'])
        fcff = ebit * (1 - tax) + da - capex - dnwc
        if mi.get('ebit_basis') == 'adjusted' and mi.get('sbc_treatment', 'cost') == 'cost': fcff -= sbc * (1 - tax)
        end = (dt.date.fromisoformat(end0) + dt.timedelta(days=round(365.25 * i))).isoformat()
        row = dict(i=i, label=y.get('label', f'Y{i}'), end=end, revenue=r, ebit=ebit, ebitda=ebit + da, nopat=ebit * (1 - tax),
                   da=da, capex=capex, dnwc=dnwc, sbc=sbc, fcff=fcff, tax=tax,
                   returns=r * y.get('shareholder_returns_pct_rev', 0) + y.get('shareholder_returns', 0) + y.get('other_uses', 0),
                   adj_ebitda=ebit + da + r * y.get('adj_ebitda_addback_pct_rev', 0))
        if sb: row['segments'] = {k: dict(revenue=round(v['revenue'][i - 1], 2), ebit=round(v['ebit'][i - 1], 2)) for k, v in sb['by'].items()}
        out.append(row); prev = row
    return out

def extend_rows(mi, rows, ctx):
    """Fixed DCF horizon (card dcf_v2_reenable): years N+1 .. T (T = 10) follow the last detailed year. Revenue growth fades
    linearly from year N's rate to the terminal g by year T; EBIT margin, tax, D&A, SBC and the NWC ratio stay at year N's;
    capex moves linearly to the steady state by year T, where net reinvestment (capex - D&A + NWC) = g / RONIC x NOPAT."""
    T = ctx['fade_to_year']; g = ctx['g']; ron = max(ctx['ronic'], ctx['wacc']); last, prev = rows[-1], rows[-2]
    N = last['i']; out = [dict(r) for r in rows]
    if N >= T: return out
    rev = last['revenue']; gN = rev / prev['revenue'] - 1; m = last['ebit'] / rev; tax = last['tax']
    da_p = last['da'] / rev; cap0 = last['capex'] / rev; sbc_p = last['sbc'] / rev
    nwc = last['dnwc'] / (rev - prev['revenue']) if abs(rev - prev['revenue']) > 1e-9 else 0.0
    cost_sbc = mi.get('ebit_basis') == 'adjusted' and mi.get('sbc_treatment', 'cost') == 'cost'
    nopat_p = m * (1 - tax) - (sbc_p * (1 - tax) if cost_sbc else 0)
    steady = da_p + g / ron * nopat_p - nwc * g / (1 + g)
    for k in range(N + 1, T + 1):
        f = (k - N) / (T - N); gk = gN + (g - gN) * f; r = rev * (1 + gk); ebit = r * m
        da = r * da_p; capex = r * (cap0 + (steady - cap0) * f); dnwc = nwc * (r - rev); sbc = r * sbc_p
        fcff = ebit * (1 - tax) + da - capex - dnwc - (sbc * (1 - tax) if cost_sbc else 0)
        end = (dt.date.fromisoformat(mi['base']['fy_end']) + dt.timedelta(days=round(365.25 * k))).isoformat()
        out.append(dict(i=k, label=f'fade Y{k}', end=end, revenue=r, ebit=ebit, ebitda=ebit + da, nopat=ebit * (1 - tax), da=da, capex=capex,
                        dnwc=dnwc, sbc=sbc, fcff=fcff, tax=tax, growth=gk, returns=0.0, adj_ebitda=ebit + da, extended=True))
        rev = r
    return out

def contracts_value(mi, H, tH, d):
    """Signed contracts (lease, PPA, offtake) to their full term at the counterparty's credit rate: `contracts` = [{name,
    cash_flows: after-tax cash flow per fiscal year from year 1, credit_rate, source}]. Returns (PV at H, cash flow by year)."""
    tot, by = 0.0, {}
    for c in mi.get('contracts') or []:
        cr = need(c, 'credit_rate', 'contracts') + d.get('wacc', 0)
        for k, cf in enumerate(need(c, 'cash_flows', 'contracts'), 1):
            by[k] = by.get(k, 0.0) + cf
            if k <= tH: continue
            tot += cf * (k - max(k - 1, tH)) / (1 + cr) ** (k - tH)  # the year straddling H pro rata, end-of-year discounting as the DCF
    return tot, by

def ntm(rows, base_end, at, key):
    """Next-twelve-months value of `key` starting at date `at`, from fiscal-year rows (linear by time)."""
    t0 = yfrac(base_end, at); tot = 0.0
    for r in rows:
        a, b = r['i'] - 1, r['i']  # fiscal year r spans [a, b] in years after base_end
        ov = max(0.0, min(b, t0 + 1) - max(a, t0))
        tot += ov * r[key]
    if t0 + 1 > rows[-1]['i']: raise ModelError('projection too short for the horizon NTM: add years')
    return tot

def quarter_rows(mi, rows):
    """Quarterly sets (model_drivers, Kilian 2026-10-29): the quarter rows scaled to their fiscal year's row, so scenario and
    segment deltas applied to the years reach the quarters. Forecast quarters take the year's change (actual quarters stay as
    reported) unless the forecast sum would flip sign; D&A, capex and SBC the set does not build by quarter, NWC change and
    returns follow the quarter's revenue share; tax, NOPAT, EBITDA and FCFF are derived as in project()."""
    Q = mi.get('_quarters')
    if not Q: return None
    cost_sbc = mi.get('ebit_basis') == 'adjusted' and mi.get('sbc_treatment', 'cost') == 'cost'; out = []; start = Q['start']
    def fit(qs, key, tot):
        a = sum(x[key] for x in qs if x['actual']); f = sum(x[key] for x in qs if not x['actual'])
        if abs(f) > 1e-9 and (tot - a) / f > 0: return [x[key] * ((tot - a) / f if not x['actual'] else 1.0) for x in qs]
        s_ = sum(x[key] for x in qs)
        return [x[key] * tot / s_ for x in qs] if abs(s_) > 1e-9 else [tot / len(qs)] * len(qs)
    for i, r in enumerate(rows):
        qs = Q['rows'][4 * i:4 * i + 4]
        if len(qs) < 4: break
        rv = fit(qs, 'revenue', r['revenue']); eb = fit(qs, 'ebit', r['ebit']); sh_ = [v / r['revenue'] if r['revenue'] else 0.25 for v in rv]
        part = {k: (fit(qs, k, r[k]) if all(k in x for x in qs) else [r[k] * w for w in sh_]) for k in ('da', 'capex', 'sbc')}
        for j, x in enumerate(qs):
            t = x.get('tax_rate', r['tax']); da, capex, sbc = part['da'][j], part['capex'][j], part['sbc'][j]; dnwc = r['dnwc'] * sh_[j]
            fc = eb[j] * (1 - t) + da - capex - dnwc - (sbc * (1 - t) if cost_sbc else 0)
            out.append(dict(label=x['label'], start=start, end=x['end'], actual=x['actual'], fy=r['label'], revenue=rv[j], ebit=eb[j],
                            ebitda=eb[j] + da, nopat=eb[j] * (1 - t), da=da, capex=capex, dnwc=dnwc, sbc=sbc, fcff=fc, tax=t,
                            **({'eps_reported': x['eps']} if 'eps' in x else {}), returns=r['returns'] * sh_[j], adj_ebitda=eb[j] + da + (r['adj_ebitda'] - r['ebitda']) * sh_[j],
                            segments={k: dict(revenue=round(v['revenue'], 2), ebit=round(v['ebit'], 2)) for k, v in (x.get('segments') or {}).items()}))
            start = x['end']
    return out

def qsum(qrows, a, b, key):
    """Sum of `key` over [a, b] (dates) from quarter rows, pro rata by days within a quarter; None when not covered."""
    D = dt.date.fromisoformat; a, b = D(a) if isinstance(a, str) else a, D(b) if isinstance(b, str) else b
    if not qrows or a < D(qrows[0]['start']) - dt.timedelta(days=3) or b > D(qrows[-1]['end']) + dt.timedelta(days=3): return None
    tot = 0.0
    for q in qrows:
        s, e = D(q['start']), D(q['end']); ov = (min(e, b) - max(s, a)).days
        if ov > 0: tot += q[key] * ov / (e - s).days
    return tot

def ntm_q(qrows, at, key):
    a = dt.date.fromisoformat(at); return qsum(qrows, a, a + dt.timedelta(days=365), key)

def ltm_q(qrows, at, key):
    b = dt.date.fromisoformat(at); return qsum(qrows, b - dt.timedelta(days=365), b, key)

def ltm(rows, base_end, at, key, base_val=None):
    """Trailing-twelve-months value of `key` ending at `at` (the base year counts as year 0)."""
    t1 = yfrac(base_end, at); tot = 0.0
    for r in [dict(i=0, **{key: base_val})] + rows if base_val is not None else rows:
        a, b = r['i'] - 1, r['i']; ov = max(0.0, min(b, t1) - max(a, t1 - 1))
        tot += ov * r[key]
    return tot

def seg_build(mi, n):
    """Segment driver build (2026-10-19): each segment carries base_revenue and per-year revenue_growth / ebit_margin (a year
    inherits what it leaves out); returns per-year totals and the per-segment detail, or None without a segment_build."""
    sbs = mi.get('segment_build')
    if not sbs: return None
    segs = sbs['segments'] if isinstance(sbs, dict) else sbs
    tot_r, tot_e, by = [0.0] * n, [0.0] * n, {}
    for s in segs:
        nm = need(s, 'name', 'segment_build'); rv = need(s, 'base_revenue', f'segment_build[{nm}]'); last = {}
        ys = s.get('years') or []
        if len(ys) < n and not ys: raise ModelError(f'segment_build[{nm}] has no years')
        R, E = [], []
        for i in range(n):
            y = {**last, **(ys[i] if i < len(ys) else {})}; last = y
            rv = rv * (1 + need(y, 'revenue_growth', f'segment_build[{nm}].years[{i+1}]'))
            R.append(rv); E.append(rv * need(y, 'ebit_margin', f'segment_build[{nm}].years[{i+1}]'))
        by[nm] = dict(revenue=R, ebit=E)
        tot_r = [a + b for a, b in zip(tot_r, R)]; tot_e = [a + b for a, b in zip(tot_e, E)]
    base_sum = sum(s['base_revenue'] for s in segs); br = mi['base']['revenue']
    if br and abs(base_sum / br - 1) > 0.02:
        raise ModelError(f'segment_build base revenues sum to {base_sum:,.1f}, not base.revenue {br:,.1f} (add an "other" segment)')
    return dict(revenue=tot_r, ebit=tot_e, by=by)

def apply_seg_deltas(mi, d):
    """Scenario growth / margin deltas reach every segment-year of a segment build."""
    sbs = mi.get('segment_build')
    if not sbs or not (d.get('revenue_growth') or d.get('ebit_margin')): return
    for s in (sbs['segments'] if isinstance(sbs, dict) else sbs):
        ys = s.get('years') or []
        for y in ys:
            if 'revenue_growth' in y: y['revenue_growth'] += d.get('revenue_growth', 0)
            if 'ebit_margin' in y: y['ebit_margin'] += d.get('ebit_margin', 0)

def rnpv_value(cfg, H, wacc_default, d):
    """Risk-adjusted NPV at the horizon H of a list of programmes (2026-10-19). Per programme: sales ramp linearly from
    launch to peak_sales over ramp_years, hold to loe, then fall by loe_erosion in the first year and tail_decline a year
    after; cash flow = sales x share x margin x (1 - tax); x probability of success (p_success, or the product of
    phases[].p); minus development costs x the probability they are spent; discounted mid-year at the programme's rate."""
    progs = need(cfg, 'programs', 'rnpv'); out = {}; tot = 0.0
    for p in progs:
        nm = need(p, 'name', 'rnpv.programs'); r = p.get('discount_rate', cfg.get('discount_rate', wacc_default))
        if r is None: raise ModelError(f'rnpv[{nm}] needs a discount_rate (no DCF WACC to fall back on since 2026-10-21)')
        r += d.get('wacc', 0)
        pos = p.get('p_success')
        if pos is None: pos = math.prod(ph['p'] for ph in need(p, 'phases', f'rnpv[{nm}]'))
        pos = min(1.0, pos * (1 + d.get('pos_pct', 0)))
        peak = need(p, 'peak_sales', f'rnpv[{nm}]') * (1 + d.get('peak_sales_pct', 0))
        t_l = yfrac(H, need(p, 'launch', f'rnpv[{nm}]')); ramp = max(p.get('ramp_years', 5), 0.25)
        t_loe = yfrac(H, p['loe']) if p.get('loe') else t_l + p.get('life_years', 15)
        ero = p.get('loe_erosion', 0.8); tail = p.get('tail_decline', 0.3)
        m = need(p, 'margin', f'rnpv[{nm}]'); tax = p.get('tax_rate', cfg.get('tax_rate', 0.18)); share = p.get('share', 1.0)
        pv_cf = 0.0; horizon_years = int(p.get('max_years', cfg.get('max_years', 30)))
        for k in range(horizon_years):
            t = k + 0.5
            if t < t_l: s = 0.0
            elif t < t_loe: s = peak * min(1.0, (t - t_l) / ramp)
            else: s = peak * min(1.0, (t_loe - t_l) / ramp) * (1 - ero) * (1 - tail) ** (t - t_loe)
            pv_cf += s * share * m * (1 - tax) / (1 + r) ** t
        pv_cost = sum(c['amount'] * c.get('probability', 1.0) / (1 + r) ** max(yfrac(H, c['date']), 0) for c in p.get('dev_costs', []))
        v = pos * pv_cf - pv_cost + p.get('milestones_pv', 0)
        out[nm] = dict(rnpv=v, p_success=pos, unrisked_npv=pv_cf, pv_dev_costs=pv_cost, discount_rate=r)
        tot += v
    return tot, out

def nav_value(cfg, H, d):
    """Reserve-life NAV at the horizon (2026-10-19): per asset and year (from max(start, H)), owned share x
    (production x (price x (1 - royalty) - unit_cost) - capex) less tax on the operating margin, discounted at the real
    rate; price decks and production may be per-year lists (the last value repeats) or flat numbers."""
    r = cfg.get('discount_rate', 0.08) + d.get('wacc', 0); assets = need(cfg, 'assets', 'nav'); out = {}; tot = 0.0
    at = lambda x, k: (x[min(k, len(x) - 1)] if isinstance(x, list) else x)
    for a in assets:
        nm = need(a, 'name', 'nav.assets'); start = max(yfrac(H, a.get('start', H)), 0.0); life = need(a, 'life_years', f'nav[{nm}]')
        own = a.get('ownership', 1.0); roy = a.get('royalty_pct', 0.0); tax = a.get('tax_rate', cfg.get('tax_rate', 0.25))
        prod = need(a, 'production', f'nav[{nm}]'); price = need(a, 'price', f'nav[{nm}]'); cost = need(a, 'unit_cost', f'nav[{nm}]')
        capex = a.get('capex', 0.0); pv = 0.0
        for k in range(int(math.ceil(life))):
            frac = min(1.0, life - k); t = start + k + 0.5 * frac
            pr = at(price, k) * (1 + d.get('price_pct', 0)); q = at(prod, k) * frac
            op = q * (pr * (1 - roy) - at(cost, k)); cx = at(capex, k) * frac
            cf = own * (op - max(op, 0) * tax - cx)
            pv += cf / (1 + r) ** t
        pv -= own * sum(c['amount'] / (1 + r) ** max(yfrac(H, c['date']), 0) for c in a.get('initial_capex', []))
        pv += own * a.get('closure_cost_pv', 0)  # negative number: reclamation
        out[nm] = dict(npv=pv, ownership=own); tot += pv
    ga = cfg.get('corporate_ga', 0.0)
    if ga:
        yrs_ga = cfg.get('corporate_years', 10)
        tot -= sum(ga / (1 + r) ** (k + 0.5) for k in range(int(yrs_ga)))
    return tot, out

def stakes_value(stakes, d):
    """Probability-weighted equity stakes (2026-10-19), equity-level: ownership x sum(prob x equity value) [or value],
    less tax on the gain over cost_basis, x (1 - liquidity_discount), x probability the stake is realised at all."""
    tot = 0.0; out = {}
    for s in stakes or []:
        nm = need(s, 'name', 'stakes')
        if s.get('scenarios'):
            ps = sum(x['prob'] for x in s['scenarios'])
            if abs(ps - 1) > 0.02: raise ModelError(f'stakes[{nm}] scenario probabilities sum to {ps:.2f}, not 1')
            ev = sum(x['prob'] * x['equity_value'] for x in s['scenarios']) / ps
        else: ev = need(s, 'equity_value', f'stakes[{nm}]')
        gross = s.get('ownership', 1.0) * ev * (1 + d.get('stake_pct', 0))
        tax = s.get('tax_rate', 0.0) * max(gross - s.get('cost_basis', 0.0), 0)
        v = (gross - tax) * (1 - s.get('liquidity_discount', 0.0)) * s.get('probability', 1.0)
        out[nm] = dict(value=v, gross=gross, tax=tax); tot += v
    return tot, out

def segment_dcf_value(mi, p, as_of, H, d):
    """EV at H of a fixed_dcf SOTP part: w x fixed DCF of the segment's own rows (the neoclouds' v2 machinery: script rate,
    fade to year 10, steady-state terminal, the part's signed contracts to full term at their credit rate) + (1 - w) x the
    part's horizon-NTM EV/EBITDA multiple on the same rows. Part keys: name, base {revenue, fy_end}, years (3, the usual
    driver keys), multiple (ebitda: 'adjusted' to read adj. EBITDA), optional contracts [{name, cash_flows, credit_rate,
    source}], optional ronic."""
    ctx = mi.get('_seg_dcf'); nm = p.get('name')
    if not ctx: raise ModelError(f"sotp[{nm}] fixed_dcf needs the script rate: run through apply_dcf_policy (spot and beta)")
    sub = dict(base=need(p, 'base', f'sotp[{nm}]'), years=copy.deepcopy(need(p, 'years', f'sotp[{nm}]')),
               ebit_basis=mi.get('ebit_basis'), sbc_treatment=mi.get('sbc_treatment'), contracts=p.get('contracts') or [], dcf_weight=0.0)
    for y in sub['years']:
        if 'revenue_growth' in y: y['revenue_growth'] += d.get('revenue_growth', 0)
        if 'ebit_margin' in y: y['ebit_margin'] += d.get('ebit_margin', 0)
    rows = project(sub); b = sub['base']; tH = yfrac(b['fy_end'], H)
    if tH >= rows[-1]['i'] - 1: raise ModelError(f'sotp[{nm}] fixed_dcf: the segment years must run a full year past the horizon')
    wacc = ctx['wacc'] + d.get('wacc', 0); g = ctx['g']; ron = max(p.get('ronic') or ctx['ronic_default'], wacc)
    if wacc <= g + 0.01: raise ModelError(f'WACC {wacc:.3f} must exceed terminal growth {g:.3f} by at least 1 point')
    drows = extend_rows(sub, rows, dict(fade_to_year=ctx['fade_to_year'], g=g, ronic=ron, wacc=wacc)); tN = drows[-1]['i']
    c_pv, c_by = contracts_value(sub, H, tH, d) if sub['contracts'] else (0.0, {})
    pv = 0.0
    for r in drows:
        a, e = r['i'] - 1, r['i']
        if e <= tH: continue
        pv += (r['fcff'] - c_by.get(r['i'], 0.0)) * (e - max(a, tH)) / (e - a) / (1 + wacc) ** (e - tH)
    L = drows[-1]; cost_sbc = sub.get('ebit_basis') == 'adjusted' and sub.get('sbc_treatment', 'cost') == 'cost'
    base_n = (L['nopat'] - (L['sbc'] * (1 - L['tax']) if cost_sbc else 0)) * (1 + g) - c_by.get(tN + 1, 0.0)
    tv = base_n * (1 - g / ron) / (wacc - g); ev_dcf = pv + tv / (1 + wacc) ** (tN - tH) + c_pv
    ebitda = ntm(rows, b['fy_end'], H, 'adj_ebitda' if p.get('ebitda') == 'adjusted' else 'ebitda'); ev_mult = need(p, 'multiple', f'sotp[{nm}]') * ebitda * (1 + d.get('multiple_pct', 0))
    w = ctx['weight'] if rows[-1]['nopat'] > 0 else 0.0  # pre-profit segment: no DCF weight, as for the neoclouds
    return w * ev_dcf + (1 - w) * ev_mult, dict(dcf_weight=w, ev_dcf=ev_dcf, ev_multiple=ev_mult, contracts_pv=c_pv, wacc=wacc, ronic=ron,
                                                tv_share=(tv / (1 + wacc) ** (tN - tH)) / ev_dcf if ev_dcf else None, ntm_ebitda=ebitda,
                                                fade_rows=[{k: round(v, 2) if isinstance(v, float) else v for k, v in r.items() if k in ('i', 'revenue', 'nopat', 'capex', 'da', 'fcff')} for r in drows if r.get('extended')])

def resolve_drivers(mi, dd=None, report=None):
    """Driver-led sets (Kilian 2026-10-22): 4-6 cited drivers compute the line items; see model_drivers.py."""
    import model_drivers as MD
    MD.me.ModelError = ModelError
    return MD.resolve(mi, dd, report)

def financing_expected(evs, H):
    """Probable financing events at expected value (Kilian 2026-10-28 16:33Z "yes"): an equity raise, convertible or large
    debt deal that has not happened yet but has a cited, sizeable probability. Each event states what it does by the
    horizon if it happens (shares_added, net_debt_change, preferred_added, annual_cost after tax to common holders, e.g.
    preferred dividends or coupon above the set's interest rate); the engine adds probability x each to the bridge at H.
    Events expected after H add nothing. Returns the expected totals and the per-event detail."""
    tot = dict(shares=0.0, net_debt=0.0, preferred=0.0, annual_cost=0.0); det = []
    for e in evs or []:
        nm = need(e, 'name', 'financing_events'); p = need(e, 'probability', f'financing_events[{nm}]')
        if not 0 < p <= 1: raise ModelError(f'financing_events[{nm}] probability {p} must be in (0, 1]')
        if not (e.get('probability_basis') or '').strip(): raise ModelError(f'financing_events[{nm}] needs probability_basis (the evidence and derivation)')
        if not any(e.get(k) for k in ('shares_added', 'net_debt_change', 'preferred_added', 'annual_cost')):
            raise ModelError(f'financing_events[{nm}] states no effect (shares_added, net_debt_change, preferred_added, annual_cost)')
        live = not e.get('expected_date') or str(e['expected_date'])[:10] <= str(H)[:10]
        x = {k: (e.get(k) or 0.0) * p if live else 0.0 for k in ('shares_added', 'net_debt_change', 'preferred_added', 'annual_cost')}
        tot['shares'] += x['shares_added']; tot['net_debt'] += x['net_debt_change']
        tot['preferred'] += x['preferred_added']; tot['annual_cost'] += x['annual_cost']
        det.append(dict(name=nm, kind=e.get('kind', ''), probability=p, by_horizon=live, **{'expected_' + k: v for k, v in x.items()}))
    return tot, det

def value(mi, as_of, horizon=None, deltas=None):
    d = dict(deltas or {}); mi = resolve_drivers(mi, d.pop('drivers', None)); mi = copy.deepcopy(mi)
    for y in mi['years']:
        if 'revenue_growth' in y: y['revenue_growth'] += d.get('revenue_growth', 0)
        if 'ebit_margin' in y: y['ebit_margin'] += d.get('ebit_margin', 0)
    apply_seg_deltas(mi, d)
    tm = mi.get('terminal', {}); w_dcf = mi.get('dcf_weight', 0.0) or 0.0  # default 0 since Kilian 2026-10-21 (was 0.5)
    ctx = mi.get('_dcf')
    if w_dcf > 0 and ctx:  # fixed DCF (v2): the script's rate
        wacc = ctx['wacc'] + d.get('wacc', 0)
    elif w_dcf > 0:
        dr = need(mi, 'discount_rate', 'model_inputs'); wacc = need(dr, 'wacc', 'discount_rate') + d.get('wacc', 0)
    else:  # no DCF leg: a discount rate is only needed by rNPV / NAV / cash-flow parts that state their own
        wacc = (mi.get('discount_rate') or {}).get('wacc'); wacc = None if wacc is None else wacc + d.get('wacc', 0)
    rows = project(mi); b = mi['base']; H = horizon or (dt.date.fromisoformat(as_of) + dt.timedelta(days=365)).isoformat()
    qrows = quarter_rows(mi, rows)  # quarterly sets (2026-10-29): NTM / LTM at H and the cash roll read the quarters
    tH = yfrac(b['fy_end'], H); tN = rows[-1]['i']
    if tH >= tN - 1: raise ModelError('projection must run at least one full year past the horizon')
    # DCF at H (only when the DCF leg has weight); v2 discounts the extended rows (to year 10) less signed contracts
    pv = 0.0; drows = rows; c_pv, c_by = 0.0, {}
    if w_dcf > 0 and ctx:
        drows = extend_rows(mi, rows, dict(ctx, wacc=wacc)); tN = drows[-1]['i']
        if mi.get('contracts'): c_pv, c_by = contracts_value(mi, H, tH, d)
    for r in (drows if w_dcf > 0 else []):
        a, e = r['i'] - 1, r['i']
        if e <= tH: continue
        share = (e - max(a, tH)) / (e - a)
        pv += (r['fcff'] - c_by.get(r['i'], 0.0)) * share / (1 + wacc) ** (e - tH)
    g = ctx['g'] if (w_dcf > 0 and ctx) else tm.get('growth', 0.02)
    if w_dcf <= 0: tv = ev_dcf = None
    elif ctx:  # steady state: TV = NOPAT_{T+1} x (1 - g / RONIC) / (WACC - g), RONIC argued (default 15%), never below the WACC
        if wacc <= g + 0.01: raise ModelError(f'WACC {wacc:.3f} must exceed terminal growth {g:.3f} by at least 1 point')
        ron = max(ctx['ronic'], wacc); L = drows[-1]
        cost_sbc = mi.get('ebit_basis') == 'adjusted' and mi.get('sbc_treatment', 'cost') == 'cost'
        base_n = (L['nopat'] - (L['sbc'] * (1 - L['tax']) if cost_sbc else 0)) * (1 + g) - c_by.get(tN + 1, 0.0)
        tv = base_n * (1 - g / ron) / (wacc - g)
    elif tm.get('method', 'gordon') == 'exit_multiple':
        tv = need(tm, 'exit_ev_ebitda', 'terminal') * rows[-1]['ebitda']
    else:
        if wacc <= g + 0.01: raise ModelError(f'WACC {wacc:.3f} must exceed terminal growth {g:.3f} by at least 1 point')
        tv = rows[-1]['fcff'] * (1 + g) / (wacc - g)
    if w_dcf > 0: ev_dcf = pv + tv / (1 + wacc) ** (tN - tH) + c_pv
    # bridge to equity at H
    bal = need(mi, 'balance', 'model_inputs'); sh = need(mi, 'shares', 'model_inputs')
    bal_date = bal.get('as_of', as_of); tb = yfrac(b['fy_end'], bal_date)
    earned = 0.0; paid = 0.0
    qe = qsum(qrows, bal_date, H, 'fcff') if qrows and bal_date < H else None
    if qe is not None: earned, paid = qe, qsum(qrows, bal_date, H, 'returns')
    else:
        for r in rows:
            a, e = r['i'] - 1, r['i']; ov = max(0.0, min(e, tH) - max(a, tb))
            earned += ov * r['fcff']; paid += ov * r['returns']
    net_debt_now = need(bal, 'net_debt', 'balance')
    int_drag = bal.get('interest_rate', 0.05) * (1 - rows[0]['tax']) * max(net_debt_now, 0) * max(tH - tb, 0)
    nd_H = net_debt_now - earned + int_drag + paid
    if bal.get('net_debt_horizon') is not None: nd_H = bal['net_debt_horizon']
    adj = sum(x.get('value', 0) * x.get('probability', 1) for x in mi.get('adjustments', []))
    st_tot, st_detail = stakes_value(mi.get('stakes'), d) if mi.get('stakes') else (0.0, None)
    adj += st_tot  # 2026-10-19: probability-weighted equity stakes, equity-level like adjustments
    minor = bal.get('minorities', 0) + bal.get('preferred', 0)  # 2026-10-14: optional preferred stock claim (default 0), deducted like minorities on the EV legs
    ts = yfrac(b['fy_end'], sh.get('as_of', bal_date))
    sh_H = sh['horizon'] if sh.get('horizon') else need(sh, 'diluted_now', 'shares') * (1 + sh.get('annual_change', 0)) ** max(tH - ts, 0)
    fin, fin_det = financing_expected(mi.get('financing_events'), H)  # 2026-10-29: probable financing at expected value
    sh_pre = sh_H; sh_H += fin['shares']; nd_H += fin['net_debt']; minor += fin['preferred']
    fin_eps = bal.get('interest_rate', 0.05) * (1 - rows[0]['tax']) * fin['net_debt'] + fin['annual_cost']  # earnings cost of the expected events
    per = lambda ev: (ev - nd_H - minor + adj) / sh_H
    legs = {}
    if w_dcf > 0:
        legs['dcf'] = dict(value=per(ev_dcf), weight=w_dcf, ev=ev_dcf, tv_share=(tv / (1 + wacc) ** (tN - tH)) / ev_dcf)
        if ctx: legs['dcf'].update(v2=True, contracts_pv=c_pv, years_discounted=tN, terminal_growth=g, ronic=max(ctx['ronic'], wacc),
                                   fade_rows=[{k: round(v, 4) if isinstance(v, float) else v for k, v in r.items() if k in ('i', 'revenue', 'growth', 'ebit', 'nopat', 'capex', 'da', 'fcff')} for r in drows if r.get('extended')])
    hms = mi.get('horizon_multiples') or ([mi['horizon_multiple']] if mi.get('horizon_multiple') else [])
    for j, hm in enumerate(hms):
        k = hm['metric']; m = hm['value'] + (d.get('multiple', 0) if k != 'FCF yield' else 0) * (1 if k != 'EV/sales' else 0.1)
        if k == 'FCF yield': m = hm['value'] * (1 - d.get('multiple_pct', 0))
        basis = hm.get('basis', 'ntm')
        def M(key, bv=None, basis=basis):
            qv = (ntm_q if basis == 'ntm' else ltm_q)(qrows, H, key) if qrows else None
            if qv is not None: return qv
            return ntm(rows, b['fy_end'], H, key) if basis == 'ntm' else ltm(rows, b['fy_end'], H, key, bv)
        # quarterly sets (2026-10-29): net cash earns the same rate, so EPS keeps its interest income (consensus EPS carries it)
        interest = bal.get('interest_rate', 0.05) * (1 - rows[0]['tax']) * (nd_H if qrows else max(nd_H, 0)) + fin['annual_cost']
        if k == 'EV/EBITDA': v = per(m * M('adj_ebitda' if hm.get('ebitda') == 'adjusted' else 'ebitda'))
        elif k == 'EV/sales': v = per(m * M('revenue', b['revenue']))
        elif k == 'EV/EBIT': v = per(m * M('ebit'))
        elif k == 'P/E':
            if hm.get('eps') is not None:  # e.g. NTM consensus EPS on the peers' basis (cite it)
                v = m * (hm['eps'] * sh_pre - fin_eps) / sh_H * (1 + d.get('eps_pct', 0)) + adj / sh_H  # stated EPS is before the expected financing
            else: v = m * (M('nopat') - interest) / sh_H + adj / sh_H
        elif k == 'FCF yield':
            if hm.get('fcf_per_share') is not None: v = (hm['fcf_per_share'] * sh_pre - fin_eps) / sh_H * (1 + d.get('eps_pct', 0)) / m + adj / sh_H
            else: v = (M('fcff') - (interest if hm.get('levered', True) else 0)) / m / sh_H + adj / sh_H  # levered by default
        else: raise ModelError(f'horizon multiple metric {k} not one of EV/EBITDA, EV/sales, EV/EBIT, P/E, FCF yield')
        legs[f'multiple: {k} ({basis})' + (f' #{j+1}' if len(hms) > 1 and sum(1 for x in hms if x['metric'] == k) > 1 else '')] = \
            dict(value=v, weight=hm.get('weight', (1 - w_dcf) / max(len(hms), 1)), metric=k, multiple=m, basis=basis)
    if mi.get('segments'):
        ev = sum(s['ntm_metric'] * (s['multiple'] + d.get('multiple_pct', 0) * s['multiple']) for s in mi['segments'])
        legs['sotp'] = dict(value=per(ev), weight=mi.get('sotp_weight', 0), ev=ev)
    for c in mi.get('custom', []):
        legs['custom: ' + c['name']] = dict(value=c['value_per_share'] * (1 + d.get('custom_pct', 0)), weight=c.get('weight', 0))
    # --- deep-modeling legs (Kilian 2026-10-19 20:31Z); absent keys leave the value unchanged ---
    extra = {}
    if mi.get('rnpv'):
        rv, rdet = rnpv_value(mi['rnpv'], H, wacc, d); extra['rnpv'] = dict(total=rv, programs=rdet)
        if mi['rnpv'].get('mode', 'leg') == 'adjustment':
            adj += rv  # pipeline on top of a core business valued by the other legs
            for k in legs:
                if not k.startswith('custom'): legs[k]['value'] += rv / sh_H
        else: legs['rnpv'] = dict(value=per(rv), weight=mi['rnpv'].get('weight', 0), ev=rv)
    if mi.get('nav'):
        nv, ndet = nav_value(mi['nav'], H, d); pm = mi['nav'].get('p_nav_multiple', 1.0) * (1 + d.get('multiple_pct', 0))
        extra['nav'] = dict(total=nv, assets=ndet, p_nav=pm)
        legs['nav'] = dict(value=per(nv) * pm, weight=mi['nav'].get('weight', 0), ev=nv, p_nav=pm)
    if mi.get('reit'):
        rc = mi['reit']; interest = bal.get('interest_rate', 0.05) * max(nd_H, 0) + fin['annual_cost']
        if rc.get('p_affo'):
            pa = rc['p_affo']; mult = pa['value'] + d.get('multiple', 0)
            if pa.get('affo_per_share') is not None: aps = (pa['affo_per_share'] * (1 + d.get('eps_pct', 0)) * sh_pre - fin_eps) / sh_H
            else:  # AFFO = NTM EBITDA - interest - maintenance capex - cash taxes, per share at H
                ek = 'adj_ebitda' if pa.get('ebitda') == 'adjusted' else 'ebitda'
                ebd = (ntm_q(qrows, H, ek) if qrows else None) or ntm(rows, b['fy_end'], H, ek)
                rv_ = (ntm_q(qrows, H, 'revenue') if qrows else None) or ntm(rows, b['fy_end'], H, 'revenue')
                aps = (ebd - interest - pa.get('maint_capex_pct_rev', 0) * rv_ - pa.get('cash_taxes', 0)) / sh_H
            legs['p_affo'] = dict(value=mult * aps + adj / sh_H, weight=pa.get('weight', 0), multiple=mult, affo_per_share=aps)
        if rc.get('nav'):
            na = rc['nav']; cap = na['cap_rate'] + d.get('cap_rate', 0)
            gav = na['noi_ntm'] / cap + na.get('development_cip', 0) + na.get('land_other', 0)
            legs['reit_nav'] = dict(value=per(gav) * na.get('p_nav_multiple', 1.0), weight=na.get('weight', 0), gav=gav, cap_rate=cap)
    if mi.get('sotp'):
        so = mi['sotp']; ev_p = 0.0; eq_p = 0.0; parts = {}; parts_info = {}
        for p in need(so, 'parts', 'sotp'):
            nm = need(p, 'name', 'sotp.parts'); mth = p.get('method', 'multiple')
            if mth == 'multiple': v = need(p, 'metric_value', f'sotp[{nm}]') * need(p, 'multiple', f'sotp[{nm}]') * (1 + d.get('multiple_pct', 0))
            elif mth == 'value': v = need(p, 'value', f'sotp[{nm}]') * (1 + d.get('multiple_pct', 0) if p.get('scale_with_multiple') else 1)
            elif mth == 'cash_flows':  # stated yearly cash flows after H plus a Gordon terminal
                r_ = p.get('discount_rate', wacc)
                if r_ is None: raise ModelError(f'sotp[{nm}] cash_flows needs a discount_rate')
                r_ += d.get('wacc', 0); cfs = need(p, 'cash_flows', f'sotp[{nm}]'); g_ = p.get('terminal_growth', 0.02)
                if r_ <= g_ + 0.01: raise ModelError(f'sotp[{nm}] discount rate must exceed terminal growth by 1 point')
                v = sum(c / (1 + r_) ** (k + 0.5) for k, c in enumerate(cfs)) + cfs[-1] * (1 + g_) / (r_ - g_) / (1 + r_) ** len(cfs)
            elif mth == 'fixed_dcf': v, parts_info[nm] = segment_dcf_value(mi, p, as_of, H, d)
            elif mth == 'rnpv': v = rnpv_value(p, H, wacc, d)[0]
            elif mth == 'nav': v = nav_value(p, H, d)[0] * p.get('p_nav_multiple', 1.0)
            else: raise ModelError(f'sotp[{nm}] method {mth} not one of multiple, value, cash_flows, fixed_dcf, rnpv, nav')
            v *= p.get('ownership', 1.0)
            if p.get('level', 'ev') == 'equity': eq_p += v
            else: ev_p += v
            parts[nm] = dict(value=v, method=mth, level=p.get('level', 'ev'), **({'segment_dcf': parts_info[nm]} if nm in parts_info else {}))
        hd = so.get('holdco_discount', 0.0)
        legs['sotp_parts'] = dict(value=(per(ev_p * (1 - hd)) + eq_p * (1 - hd) / sh_H), weight=so.get('weight', 0), ev=ev_p, equity_parts=eq_p, parts=parts)
    if ctx and 'dcf' in legs:  # v2: the DCF takes its tested weight, the set's other legs share the rest in their own proportions
        Wo = sum(l['weight'] for k, l in legs.items() if k != 'dcf')
        if Wo > 0:
            for k, l in legs.items():
                if k != 'dcf': l['weight'] = l['weight'] * (1 - w_dcf) / Wo
    W = sum(l['weight'] for l in legs.values())
    if W <= 0: raise ModelError('leg weights sum to zero')
    hv = sum(l['value'] * l['weight'] for l in legs.values()) / W
    out = dict(horizon=H, horizon_value=hv, legs=legs, wacc=wacc, net_debt_H=nd_H, shares_H=sh_H,
                rows=[{k: (round(v, 2) if isinstance(v, float) else v) for k, v in r.items()} for r in rows])
    if qrows:
        ir = bal.get('interest_rate', 0.05) * max(net_debt_now, 0) / 4; sh0 = need(sh, 'diluted_now', 'shares')
        for q in qrows:  # model quarter EPS on the set's ebit_basis: (EBIT - interest on positive net debt) x (1 - tax) / shares; other income and
            # claims are left out (the pre-print log adds them from the filings); actual quarters show reported EPS when given
            tq = yfrac(sh.get('as_of', bal_date), q['end']); shq = sh0 * (1 + sh.get('annual_change', 0)) ** max(tq, 0)
            q['eps'] = q['eps_reported'] if q['actual'] and 'eps_reported' in q else ((q['ebit'] - ir) * (1 - q['tax'])) / shq
        out['quarters'] = [{k: (round(v, 4) if isinstance(v, float) else v) for k, v in q.items()} for q in qrows]
    if st_detail is not None: extra['stakes'] = dict(total=st_tot, detail=st_detail)
    if fin_det: extra['financing'] = dict(total=fin, detail=fin_det)
    if extra: out['deep'] = extra
    return out

def run_set(mi, as_of, spot=None):
    base = value(mi, as_of)
    sc = mi.get('scenarios') or {}
    out = dict(base=base, scenarios={})
    for k, s in sc.items():  # probabilities no longer weight anything (2026-10-30); older sets' bear/bull read as downside/upside
        k = SCEN_ALIAS.get(k, k)
        if k not in SCEN_PCTL: raise ModelError(f'scenario {k!r}: use downside, base and upside')
        if k == 'base':  # the base case is the reconciled set itself, the research target (12m); base deltas are not used
            if s.get('deltas'): out.setdefault('scenario_flags', []).append(f"base scenario deltas {s['deltas']} ignored: the base case is the reconciled set")
            continue
        out['scenarios'][k] = dict(percentile=SCEN_PCTL[k], value=value(mi, as_of, deltas=s.get('deltas', {}))['horizon_value'],
                                   deltas=s.get('deltas', {}), story=s.get('story', ''))
    out['scenarios'].setdefault('base', dict(percentile=50, value=base['horizon_value'], deltas={}))
    # reverse DCF diagnostic: the WACC at which the DCF leg equals the multiple legs' weighted value
    ml = [(l['value'], l['weight']) for k, l in base['legs'].items() if k.startswith('multiple')]
    if 'dcf' in base['legs'] and ml and sum(w for _, w in ml) > 0:
        target = sum(v * w for v, w in ml) / sum(w for _, w in ml)
        lo, hi = 0.03, 0.30; g = (mi['_dcf'] or {}).get('g', 0.03) if mi.get('_dcf') else (mi.get('terminal') or {}).get('growth', 0.02)
        try:
            for _ in range(60):
                mid = (lo + hi) / 2; t = copy.deepcopy(mi)
                if t.get('_dcf'): t['_dcf'] = dict(t['_dcf'], wacc=mid)
                else: t['discount_rate'] = dict(t['discount_rate'], wacc=mid)
                if mid <= g + 0.0105: lo = mid; continue
                if value(t, as_of)['legs']['dcf']['value'] > target: lo = mid
                else: hi = mid
            out['implied_wacc_for_multiples'] = (lo + hi) / 2
        except ModelError: pass
    # DCF check (Kilian 2026-10-14 20:37Z, "undervaluation on some of the dcf models"): a DCF more than 20% off the other
    # legs, or a WACC more than 3 points off the one the multiples imply, must be fixed or argued before it gets weight
    dl = base['legs'].get('dcf'); nd = [(l['value'], l['weight']) for k, l in base['legs'].items() if k != 'dcf' and l.get('weight')]
    if dl and nd and sum(w for _, w in nd) > 0:
        ref = sum(v * w for v, w in nd) / sum(w for _, w in nd); iw = out.get('implied_wacc_for_multiples'); fl = []
        if ref > 0 and abs(dl['value'] / ref - 1) > 0.20: fl.append(f"DCF {dl['value']:.2f} is {dl['value'] / ref - 1:+.0%} vs the other legs' {ref:.2f}")
        if iw and abs(iw - base['wacc']) > 0.03: fl.append(f"WACC {base['wacc']:.3f} vs {iw:.3f} implied by the multiples")
        out['dcf_check'] = dict(dcf=dl['value'], other_legs=ref, ratio=dl['value'] / ref if ref else None, flags=fl)
    # Base case with downside and upside (Kilian 2026-10-30: "just go base case and use [downside/upside] to determine
    # risk/return"): the base value is the median, the downside the 10th and the upside the 90th percentile of the 12-month
    # price, so each side of a split lognormal takes its own vol; a set without one side uses scenario_vol (0.15) there
    sv = mi.get('scenario_vol', 0.15); V = {k: s['value'] for k, s in out['scenarios'].items()}
    if any(v <= 0 for v in V.values()): raise ModelError('a scenario values the equity at or below zero; check the inputs')
    b = V['base']; out.setdefault('scenario_flags', [])
    sd = dict(down=math.log(b / V['downside']) / Z90 if 'downside' in V else sv, up=math.log(V['upside'] / b) / Z90 if 'upside' in V else sv)
    for side in ('down', 'up'):
        if sd[side] < SD_MIN:
            out['scenario_flags'].append(f"{side}side case {'above' if side == 'down' else 'below'} or within {SD_MIN:.0%} vol of base: side vol floored at {SD_MIN:.0%}")
            sd[side] = SD_MIN
    out['side_vol'] = dict(sd)
    P = lambda K: 1 - N_(math.log(K / b) / (sd['down'] if K < b else sd['up']))
    out['median'] = b
    out['mean'] = b * (math.exp(sd['down'] ** 2 / 2) * N_(-sd['down']) + math.exp(sd['up'] ** 2 / 2) * N_(sd['up']))
    q = {f'q{int(qq*100)}': b * math.exp(Ninv_(qq) * (sd['down'] if qq < 0.5 else sd['up'])) for qq in (0.10, 0.25, 0.75, 0.90)}
    out['quantiles'] = q
    out['P'] = P
    if spot: out['upside'] = out['median'] / spot - 1
    return out

def prior_spot_beta(date, sym):
    """(spot, beta) from priors/DATE/SYM.json (market_dist.py: the 2-year daily beta vs SPY); (None, None) when missing."""
    try: p = json.load(open(f'{HERE}/priors/{date}/{sym}.json')); return p.get('spot'), p.get('beta')
    except Exception: return None, None

def strip(o):
    o = {k: v for k, v in o.items() if k != 'P'}
    return json.loads(json.dumps(o, default=lambda x: round(x, 4) if isinstance(x, float) else str(x)))

def load(path):  # Kilian 2026-10-14 04:03Z: runs from before Option 1 get their sets from model_backfill/ (agents/model_backfill.md)
    d = json.load(open(path))
    bf = os.path.join(os.path.dirname(path), 'model_backfill', os.path.basename(path))
    if os.path.exists(bf) and 'model_inputs' not in d and not (isinstance(d.get('model'), dict) and 'reconciled_inputs' in d['model']):
        b = json.load(open(bf))
        for k in ('model_inputs', 'model'):
            if k in b: d[k] = b[k]
        d['_model_backfill'] = True
    return d

def set_hash(path):
    """md5 of only what extract() reads from a set file (model_inputs and model.reconciled_inputs), so a text-only edit elsewhere
    in supervisor.json (reasons, sizing notes, news) does not make model_engine.json look stale (Kilian 2026-10-30 'yes fix this')."""
    import hashlib
    try: d = json.load(open(path))
    except Exception: return None
    m = d.get('model') if isinstance(d.get('model'), dict) else {}
    return hashlib.md5(json.dumps(dict(model_inputs=d.get('model_inputs'), reconciled_inputs=m.get('reconciled_inputs')),
                                  sort_keys=True).encode()).hexdigest()

def extract(d):
    if 'model_inputs' in d: return d['model_inputs']
    if 'model' in d and isinstance(d['model'], dict) and 'reconciled_inputs' in d['model']: return d['model']['reconciled_inputs']
    if 'base' in d and 'years' in d: return d
    raise ModelError('no model_inputs found')

def guidance_note(mi):
    """Kilian 2026-10-21 18:38Z ("stop taking management at face value on guidance"): flags a set with no guidance_check, a
    check without a position against the guide, or an input labelled "guidance" (the agent's own forecast is an estimate)."""
    gc = mi.get('guidance_check'); fl = []
    if not gc: fl.append('no guidance_check (agents/model_inputs.md: score past guides vs actuals and state above/inside/below)')
    for g in gc or []:
        if str(g.get('metric', '')).lower().startswith('none'): continue
        if not ((g.get('track_record') or {}).get('periods')): fl.append(f"guidance_check {g.get('metric')} {g.get('fy')}: no track-record periods")
        if g.get('position') not in ('above', 'inside', 'below'): fl.append(f"guidance_check {g.get('metric')} {g.get('fy')}: position must be above | inside | below")
    lab = [i.get('input') for i in mi.get('inputs_detail') or [] if str(i.get('basis', '')).strip().lower() == 'guidance']
    if lab: fl.append(f"inputs labelled basis 'guidance' (a forecast set from the guide is an 'estimate' citing guidance_check): {', '.join(str(x)[:40] for x in lab[:5])}")
    fl += peer_note(mi)
    return '; '.join(fl) or None

PEER_MIN_SHARE = 0.25  # Kilian 2026-10-30 17:04Z ("fix this so it doesn't happen anymore", after AVGO was padded with QCOM/ADI/TXN)

def peer_note(mi):
    """Peer quality before count (Kilian 2026-10-26 04:59Z: "10-12 good peers ... less than that was fine ... just flag it").
    Quarterly (new or rebuilt) sets only: every peer in the whole-company set needs `fit` = {segment, revenue_share} (the part
    of the subject it matches and that part's share of the subject's revenue in the year the multiple reads); a peer that
    matches less than PEER_MIN_SHARE of revenue is segment-only and stays out of the whole-company median; fewer than 10
    peers needs comps_summary.peer_count_flag. Never pad the set to reach 10."""
    if not mi.get('quarters'): return []
    cs = mi.get('comps_summary') or {}; rows = cs.get('rows') or mi.get('comps') or []
    peers = cs.get('peers') or [r.get('peer') or r.get('ticker') for r in rows]
    fl = []
    if not peers: return ['no comps_summary peers (agents/model_inputs.md "Peers")'] if mi.get('horizon_multiples') else []
    if len(peers) < 10 and not cs.get('peer_count_flag'): fl.append(f'{len(peers)} peers and no comps_summary.peer_count_flag (say how many and why no more; never pad)')
    for r in rows:
        nm = r.get('peer') or r.get('ticker')
        if nm not in peers: continue
        fit = r.get('fit') or {}
        if not fit.get('segment') or not isinstance(fit.get('revenue_share'), (int, float)): fl.append(f'peer {nm}: no fit {{segment, revenue_share}}')
        elif fit['revenue_share'] < PEER_MIN_SHARE: fl.append(f"peer {nm} matches {fit['segment']} = {fit['revenue_share']:.0%} of revenue: segment-only, keep it out of the whole-company median")
    return fl

def summary(name, r):
    b = r['base']
    legs = ', '.join(f"{k} {v['value']:.2f} (w {v['weight']:.2f})" for k, v in b['legs'].items())
    sc = ', '.join(f"{k} {v['value']:.2f} (P{v['percentile']})" for k, v in r['scenarios'].items())
    iw = r.get('implied_wacc_for_multiples')
    return (f"{name}: base horizon value {b['horizon_value']:.2f} [{legs}]; scenarios {sc}; median {r['median']:.2f}, q25-q75 "
            f"{r['quantiles']['q25']:.2f}-{r['quantiles']['q75']:.2f}" + (f"; WACC {b['wacc']:.3f}, TV share of DCF {b['legs']['dcf']['tv_share']:.0%}" if 'dcf' in b['legs'] else '; no DCF leg')
            + (f"; the multiples imply a WACC of {iw:.3f} (reverse DCF)" if iw else '')
            + ((('; DCF gap (information, the tested weight stands): ' if r['base']['legs']['dcf'].get('v2') else '; DCF CHECK: ') + '; '.join(r['dcf_check']['flags'])
                + ('' if r['base']['legs']['dcf'].get('v2') else ' -> fix or argue in dcf_check before weighting the DCF'))
               if r.get('dcf_check', {}).get('flags') else ''))

if __name__ == '__main__':
    a = sys.argv[1:]
    if not a: print(__doc__); sys.exit(1)
    if a[0] == 'check':
        d = json.load(open(a[1])); as_of = d.get('as_of') or a[2] if len(a) > 2 else d.get('as_of')
        try:
            sym_ = str(d.get('symbol', '')).upper(); spot_, beta_ = prior_spot_beta(d.get('as_of') or as_of, sym_)
            mi_, note = apply_dcf_policy(extract(d), sym_, spot_, beta_)
            if note: print('NOTE:', note)
            if len(mi_.get('years') or []) > MAX_YEARS_NO_DCF and dcf_policy().get('mode') == 'v2': mi_ = dict(mi_, _allow_long=True); print('NOTE: more than 3 projected years; use 3 (the engine fades the DCF to year 10)')
            r = run_set(mi_, as_of)
            gn = guidance_note(mi_)
            if gn: print('GUIDANCE:', gn)
            print('OK', summary(os.path.basename(a[1]), r))
        except (ModelError, KeyError, TypeError, ValueError) as e:
            print('MODEL ERROR:', e); sys.exit(2)
    elif a[0] == 'run':
        date, sym = a[1], a[2].upper(); rd = f'{HERE}/runs/{date}/{sym}'
        import glob
        spot, beta = prior_spot_beta(date, sym)
        res = dict(symbol=sym, date=date, spot=spot, beta=beta, sets={}, errors={})
        if dcf_policy().get('mode') == 'v2': res['dcf_inputs'] = {k: v for k, v in erp_inputs().items()}
        for f in sorted(glob.glob(f'{rd}/agent_*.json')) + [f'{rd}/supervisor.json']:
            if not os.path.exists(f): continue
            nm = os.path.basename(f)[:-5]
            try:
                d = load(f); mi_ = extract(d)
                mi_, note = apply_dcf_policy(mi_, sym, spot, beta)
                if note: res.setdefault('dcf_policy', {})[nm] = note
                v2 = dcf_policy().get('mode') == 'v2'
                cap = MAX_YEARS_NO_DCF if v2 or not (mi_.get('dcf_weight', 0.0) or 0) > 0 else MAX_YEARS
                if len(mi_.get('years') or []) > cap and (v2 or nm != 'supervisor' or note or len(mi_['years']) <= MAX_YEARS):  # sets written before the cap: valued, flagged
                    mi_ = dict(mi_, _allow_long=True); res.setdefault('warnings', {})[nm] = f"{len(mi_['years'])} projected years (over the {cap}-year cap)"
                r = run_set(mi_, d.get('as_of', date), spot)
                gn = guidance_note(mi_)
                if gn: res.setdefault('guidance_notes', {})[nm] = gn
                res['sets'][nm] = strip(r); print(summary(nm, r))
                if nm == 'supervisor':  # prep for model-first rounds (Kilian 2026-10-21 18:38Z "do any easy prep work"): the engine's
                    # own 12-month distribution on the research ladder's strikes, logged at weight 0 for scoring beside the agents' ladders
                    Ks = [x['K'] for x in (d.get('ladder') or []) if isinstance(x.get('K'), (int, float)) and x['K'] > 0]
                    if not Ks:
                        a1 = f'{rd}/agent_1.json'
                        if os.path.exists(a1): Ks = [x['K'] for x in (json.load(open(a1)).get('ladder') or []) if isinstance(x.get('K'), (int, float)) and x['K'] > 0]
                    if not Ks:  # 2026-10-30 (Kilian card "Drop 12-month, keep 1-month"): ladderless rounds take the market leg's strikes, else a grid on spot
                        try: Ks = [x['K'] for x in (json.load(open(f'{HERE}/priors/{date}/{sym}.json')).get('ladder') or []) if isinstance(x.get('K'), (int, float)) and x['K'] > 0]
                        except (OSError, ValueError): Ks = []
                        if not Ks and spot: Ks = [round(m * spot, 2) for m in (0.25, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0, 1.1, 1.2, 1.35, 1.5, 1.75, 2.0, 2.5, 3.0)]
                    if Ks: res['model_ladder'] = dict(weight=0, horizon=str(r['base']['horizon']), note='P(close on the horizon >= K) from the base case and its downside (P10) / upside (P90); information only',
                                                      ladder=[dict(K=K, p=round(r['P'](K), 4)) for K in sorted(Ks)])
            except (ModelError, KeyError, TypeError, ValueError) as e:
                res['errors'][nm] = str(e); print(nm, 'MODEL ERROR:', e)
        if 'supervisor' in res['sets']:
            s = res['sets']['supervisor']
            res['model_target'] = s['median']; res['model_quantiles'] = s['quantiles']
            res['model_fair_value'] = s['base']['horizon_value']
            print(f"MODEL TARGET (trial) {sym}: {s['median']:.2f}" + (f" vs spot {spot:.2f} ({s['median']/spot-1:+.1%})" if spot else ''))
        # 2026-10-19: content hashes of the inputs, so combine.py's freshness test does not depend on network-mount mtimes
        import hashlib
        res['inputs_md5'] = {os.path.relpath(f, rd): hashlib.md5(open(f, 'rb').read()).hexdigest()
                             for f in [f'{rd}/supervisor.json', f'{rd}/model_backfill/supervisor.json'] if os.path.exists(f)}
        res['inputs_set_hash'] = {os.path.relpath(f, rd): set_hash(f)
                                  for f in [f'{rd}/supervisor.json', f'{rd}/model_backfill/supervisor.json'] if os.path.exists(f)}
        json.dump(res, open(f'{rd}/model_engine.json', 'w'), indent=1)
    else: print(__doc__); sys.exit(1)
