"""Market prior for the valuation/risk agents: the option-implied 12-month price distribution for any optionable US ticker.

Usage: python3 market_dist.py SYM [SYM ...] [--chains DIR] [--horizon-days 365]
  (--horizon-days 30 writes the 1-month distribution to priors/DATE/1m/SYM.json on the LADDER_1M strikes)
Writes priors/<market_date>/<SYM>.json (outside the agents' run folders, so forecasters stay blind to it) with:
  spot, horizon date, forward, risk-free rate, 1Y ATM implied vol, beta (vs SPY, 2Y daily; 1.0 if no price file),
  ladder:  P(S_T >= K) for the standard strike ladder (risk-neutral and real-world),
  quantiles of the real-world distribution, and a dense CDF grid used by combine.py.

Method: lognormal at the horizon's ATM implied vol (VOL_MODE 'atm', default since 2026-10-09), read off the smile fit
below; the full smile is used only with VOL_MODE 'smile'.
Cboe free delayed chains (all strikes). Per expiry, forward from put-call parity; Black-76 IV from OTM mids;
smile smoothed in log-moneyness; total variance interpolated linearly in time at fixed log-moneyness between the two
expiries bracketing the horizon. Breeden-Litzenberger: P_Q(S_T >= K) = -e^{rT} dC/dK on a fine strike grid.
Real-world ("P") measure = risk-neutral scaled by (1 + premium)^T, premium = 7.69% x (0.768 + 0.232 x beta) a year: the measured
market premium (Ken French Mkt-RF, simple average of yearly excess returns 1964-2025, Kilian 2026-10-14 decision card) on the
realized premium-by-beta-decile line (data/beta_premium.json; PREMIUM_MODE 'capm' = beta x premium); this is
the stand-in for the paper's "market consensus" forecast."""
import csv, json, math, os, re, sys, datetime as dt, urllib.request, bisect

HERE = os.path.dirname(os.path.abspath(__file__))
PORT = os.path.join(HERE, '..', 'portfolio', 'data')
LADDER = [0.25, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0, 1.1, 1.2, 1.35, 1.5, 1.75, 2.0, 2.5]  # multiples of spot
LADDER_1M = [0.8, 0.85, 0.9, 0.93, 0.96, 1.0, 1.04, 1.07, 1.1, 1.15, 1.2]  # 1-month ladder (Kilian 2026-09-29)
# Measured equity premium (Kilian 2026-10-14 20:57Z "switch to the measured equity premium rather than a flat 5%"; decision card
# 21:18Z "7.69% simple"): Ken French Mkt-RF, simple average of yearly excess returns over T-bills 1964-2025 (the same years
# the beta-decile line is fitted on; in log terms, mean ln((1+Rm)/(1+Rf)), it is 5.85%; s.e. ~2.2 pt). Was a flat 5% until 2026-10-14.
# Premium by beta (Kilian 2026-10-09 16:36Z "replace the textbook capm with the realized return by beta %ile"): realized
# 1964-2025 excess returns of Ken French's value-weighted beta deciles rise much less than beta (data/beta_premium.json),
# so premium = ERP x (c0 + c1 x beta), fitted through the ten deciles and equal to ERP at beta 1. 'capm' restores ERP x beta.
PREMIUM_MODE = 'beta_decile'
_BP = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', 'beta_premium.json')))
ERP = _BP['market_excess_pct'] / 100  # 0.0769
def premium(b): return ERP * b if PREMIUM_MODE == 'capm' else ERP * (_BP['c0'] + _BP['c1'] * b)
# 'atm' (Kilian 2026-10-09): lognormal at the horizon's ATM implied vol. The 2026-10-05 backtest found the full smile
# 0.5% worse on CRPS than ATM at one month (t 2.1; too much crash mass). 'smile' restores the full-strike distribution.
VOL_MODE = 'atm'

N = lambda x: 0.5 * (1 + math.erf(x / math.sqrt(2)))
def Ninv(p):
    lo, hi = -10.0, 10.0
    for _ in range(80):
        m = (lo + hi) / 2
        if N(m) < p: lo = m
        else: hi = m
    return (lo + hi) / 2

def black(F, K, T, r, v, call):
    if v <= 0: return math.exp(-r*T) * max((F-K) if call else (K-F), 0)
    d1 = (math.log(F/K) + 0.5*v*v*T) / (v*math.sqrt(T)); d2 = d1 - v*math.sqrt(T)
    return math.exp(-r*T) * (F*N(d1) - K*N(d2) if call else K*N(-d2) - F*N(-d1))

def implied(px, F, K, T, r, call):
    lo, hi = 1e-4, 6.0
    if not (black(F, K, T, r, lo, call) < px < black(F, K, T, r, hi, call)): return None
    for _ in range(100):
        m = (lo+hi)/2
        if black(F, K, T, r, m, call) < px: lo = m
        else: hi = m
    return (lo+hi)/2

def fetch_chain(s, chain_dir):
    if chain_dir and os.path.exists(f'{chain_dir}/{s}.json'):
        return json.load(open(f'{chain_dir}/{s}.json'))['data']
    u = f'https://cdn.cboe.com/api/global/delayed_quotes/options/{s}.json'
    d = json.load(urllib.request.urlopen(urllib.request.Request(u, headers={'User-Agent': 'Mozilla/5.0'}), timeout=90))
    if chain_dir:
        os.makedirs(chain_dir, exist_ok=True); json.dump(d, open(f'{chain_dir}/{s}.json', 'w'))
    return d['data']

def risk_free():
    try:
        rf = float(next(csv.DictReader(open(f'{PORT}/tbill_3m_weekly.csv')))['yield_pct']) / 100
    except Exception:
        rf = 0.04
    return math.log(1 + rf), rf

def beta(s):
    """2Y daily beta vs SPY from portfolio/data or valuation/data price files (date,...,close). 1.0 if unavailable."""
    def load(sym):
        for d in (os.path.join(HERE, 'data'), PORT):
            p = f'{d}/daily_{sym}.csv'
            if os.path.exists(p):
                return {r['date']: float(r['close']) for r in csv.DictReader(open(p))}
        return None
    a, m = load(s), load('SPY')
    if not a or not m: return 1.0, 'default (no price history file)'
    ds = sorted(set(a) & set(m))[-505:]
    ra = [a[ds[i]]/a[ds[i-1]] - 1 for i in range(1, len(ds))]
    rm = [m[ds[i]]/m[ds[i-1]] - 1 for i in range(1, len(ds))]
    ma, mm = sum(ra)/len(ra), sum(rm)/len(rm)
    cov = sum((x-ma)*(y-mm) for x, y in zip(ra, rm)); var = sum((y-mm)**2 for y in rm)
    b = cov/var
    if len(ra) < 504:  # short history (recent IPO): shrink toward 1, or toward comparable companies' median beta when
        # data/beta_peers.json lists peers (Kilian 2026-10-13 "use comparable companies to get a better beta estimate"),
        # in proportion to the missing sample
        w = len(ra)/504; prior, plab = 1.0, '1'
        try: peers = json.load(open(os.path.join(HERE, 'data', 'beta_peers.json'))).get(s, {}).get('peers')
        except Exception: peers = None
        if peers:
            pb = sorted(beta(p)[0] for p in peers if p != s)
            if pb:
                n = len(pb); prior = pb[n//2] if n % 2 else (pb[n//2-1] + pb[n//2])/2
                plab = f'peer median {prior:.2f} ({", ".join(peers)})'
        return prior + (b-prior)*w, f'daily vs SPY ({len(ra)} obs, raw {b:.2f} shrunk toward {plab} by {1-w:.0%})'
    return b, f'2Y daily vs SPY ({len(ra)} obs)'

def realized_vol(s):
    for d in (os.path.join(HERE, 'data'), PORT):
        p = f'{d}/daily_{s}.csv'
        if os.path.exists(p):
            c = [float(r['close']) for r in csv.DictReader(open(p))][-253:]
            x = [math.log(c[i]/c[i-1]) for i in range(1, len(c))]
            m = sum(x)/len(x)
            return math.sqrt(sum((a-m)**2 for a in x)/(len(x)-1)*252)
    return None

def expiry_smile(opts, T, r, relaxed=False):
    """Forward and smoothed smile [(k=ln(K/F), total variance w)] for one expiry from OTM mids. Thin chains (low-priced
    stocks with $1 strikes, e.g. ADT 2026-10-01) get a second pass with a wider spread filter and a one-strike forward."""
    q = {}
    for o in opts:
        b, a = o['bid'], o['ask']
        if b and a and b > 0 and a >= b and (a - b) <= (max(1.2*(a+b)/2, 0.20) if relaxed else max(0.6*(a+b)/2, 0.05)):
            q[(o['K'], o['cp'])] = (a+b)/2
    both = [k for k in {k for k, _ in q} if (k, 'C') in q and (k, 'P') in q]
    if len(both) < (1 if relaxed else 2): return None if relaxed else expiry_smile(opts, T, r, relaxed=True)
    both.sort(key=lambda k: abs(q[(k, 'C')] - q[(k, 'P')]))
    fw = sorted(k + math.exp(r*T)*(q[(k, 'C')] - q[(k, 'P')]) for k in both[:3])
    F = fw[len(fw)//2]
    def collect(otm_only):
        by_k = {}
        for (K, cp), px in q.items():
            if otm_only and (cp == 'C') != (K >= F): continue
            v = implied(px, F, K, T, r, cp == 'C')
            if v: by_k.setdefault(K, []).append(v)
        return [(math.log(K/F), (sum(v)/len(v))**2*T) for K, v in by_k.items()]
    pts = collect(True)
    if len(pts) < 4: pts = collect(False)  # thin chains (penny stocks): use ITM quotes too
    if len(pts) < 4: return None
    # weighted least-squares quadratic in k for total variance (weights favour near-the-money strikes),
    # evaluated only inside the quoted range and held flat beyond it
    sd = math.sqrt(sorted(w for _, w in pts)[len(pts)//2])
    X = [(k, w, math.exp(-0.5*(k/(1.5*sd))**2) + 0.05) for k, w in pts]
    A = [[sum(wt*k**(i+j) for k, _, wt in X) for j in range(3)] for i in range(3)]
    y = [sum(wt*w*k**i for k, w, wt in X) for i in range(3)]
    try:
        c = solve3(A, y)
    except ZeroDivisionError:
        return None
    kmin, kmax = pts[0][0], pts[-1][0]
    fit = lambda k: max(c[0] + c[1]*k + c[2]*k*k, 0.25*sd*sd)
    grid = [kmin + (kmax - kmin)*i/60 for i in range(61)]
    return F, [(k, fit(k)) for k in grid]

def solve3(A, y):
    M = [row[:] + [v] for row, v in zip(A, y)]
    for i in range(3):
        p = max(range(i, 3), key=lambda r_: abs(M[r_][i])); M[i], M[p] = M[p], M[i]
        for r_ in range(3):
            if r_ != i:
                f = M[r_][i] / M[i][i]
                M[r_] = [a - f*b for a, b in zip(M[r_], M[i])]
    return [M[i][3] / M[i][i] for i in range(3)]

def interp(xs, ys, x):
    if x <= xs[0]: return ys[0]
    if x >= xs[-1]: return ys[-1]
    i = bisect.bisect_left(xs, x)
    w = (x - xs[i-1]) / (xs[i] - xs[i-1])
    return ys[i-1] + w*(ys[i] - ys[i-1])

def market_dist(s, chain_dir=None, horizon_days=365):
    short = horizon_days <= 45; lad_m = LADDER_1M if short else LADDER
    raw = fetch_chain(s, chain_dir)
    S = raw['close'] or raw['current_price']
    ld = (raw.get('last_trade_time') or dt.date.today().isoformat())[:10]
    t0 = dt.datetime.fromisoformat(ld + 'T16:00')
    r, rf = risk_free()
    by_exp = {}
    for o in raw['options']:
        # 2026-10-06: skip adjusted contracts (root with a digit, e.g. MOD1 after the Gentherm spin): their deliverable
        # includes other shares, which skewed put-call parity (MOD forward came out 13% above spot).
        m = re.match(r'^[A-Z.]+(\d{6})([CP])(\d{8})$', o['option'])
        if not m: continue
        e = dt.datetime.strptime(m.group(1), '%y%m%d').replace(hour=16)
        by_exp.setdefault(e, []).append(dict(o, cp=m.group(2), K=int(m.group(3))/1000))
    Th = horizon_days / 365
    smiles = []
    for e in sorted(by_exp):
        T = (e - t0).total_seconds() / (365*86400)
        if T < (5 if short else 30)/365: continue
        sm = expiry_smile(by_exp[e], T, r)
        if sm: smiles.append((T, e.date().isoformat(), sm[0], sm[1]))
    if not smiles: raise SystemExit(f'{s}: no usable expiries')
    lo = [x for x in smiles if x[0] <= Th]; hi = [x for x in smiles if x[0] > Th]
    pair = (lo[-1], hi[0]) if lo and hi else ((smiles[-1], smiles[-1]) if not hi else (hi[0], hi[0]))
    (Ta, ea, Fa, sa), (Tb, eb, Fb, sb) = pair
    wgt = 0 if Tb == Ta else (Th - Ta) / (Tb - Ta)
    lnF = (math.log(Fa) + wgt*(math.log(Fb) - math.log(Fa))) if Ta != Tb else math.log(S) + math.log(Fa/S)/Ta*Th  # single expiry: extend its carry
    F = math.exp(lnF)
    ka, wa = zip(*sa); kb, wb = zip(*sb)
    def sigma(K):
        k = math.log(K / F)
        # compare at the same standardized moneyness so each expiry's smile is read at a like-for-like point
        w_a = interp(ka, wa, k * math.sqrt(Ta/Th)) * Th/Ta
        w_b = interp(kb, wb, k * math.sqrt(Tb/Th)) * Th/Tb
        w = w_a + wgt*(w_b - w_a) if Ta != Tb else w_a
        return math.sqrt(max(w, 1e-6) / Th)
    atm = sigma(F)
    low_conf = S < 5 or min(len(sa), len(sb)) < 5 or any(len({round(w, 8) for _, w in x}) < 3 for x in (sa, sb))
    rv = realized_vol(s)
    if S < 5:  # penny-stock chains ($0.50-$1 strikes, wide quotes) don't pin a smile: lognormal at max(ATM IV, 1Y realized)
        flat = max(atm, rv or 0)
        sigma = lambda K: flat
        atm = flat
    elif VOL_MODE == 'atm':  # Kilian 2026-10-09 05:05Z "switch to atm vol ... everywhere": lognormal at the ATM IV, no smile
        sigma = lambda K, _a=atm: _a
    # dense strike grid in log space, +-5 ATM sd
    grid = [F*math.exp(atm*math.sqrt(Th)*(-5 + 10*i/400)) for i in range(401)]
    C = [black(F, K, Th, r, sigma(K), True) for K in grid]
    surv = []  # P_Q(S_T >= K) at midpoints
    for i in range(1, len(grid)):
        p = -math.exp(r*Th) * (C[i] - C[i-1]) / (grid[i] - grid[i-1])
        surv.append((math.sqrt(grid[i]*grid[i-1]), min(max(p, 0.0), 1.0)))
    # monotone (non-increasing) clean-up
    for i in range(1, len(surv)):
        if surv[i][1] > surv[i-1][1]: surv[i] = (surv[i][0], surv[i-1][1])
    b, bsrc = beta(s)
    shift = (1 + premium(b)) ** Th  # real-world = risk-neutral scaled up by the beta-decile premium: mean excess return = premium a year
    xs = [k for k, _ in surv]; ps = [p for _, p in surv]
    def surv_q(K): return interp(xs, ps, K)
    def surv_p(K): return interp(xs, ps, K / shift)
    def quant(sf, q):  # price with P(S_T >= K) = 1 - q
        lo_, hi_ = grid[0], grid[-1]
        for _ in range(80):
            m = math.sqrt(lo_*hi_)
            if sf(m) > 1 - q: lo_ = m
            else: hi_ = m
        return math.sqrt(lo_*hi_)
    horizon = (t0 + dt.timedelta(days=horizon_days)).date().isoformat()
    out = dict(symbol=s, market_date=ld, spot=S, horizon=horizon, horizon_years=round(Th, 4), rf=rf, forward=round(F, 4),
               atm_iv_1y=round(atm, 4), vol_mode='lognormal_max_atm_rv' if S < 5 else VOL_MODE, realized_vol_1y=rv and round(rv, 4), low_confidence=low_conf, beta=round(b, 3), beta_source=bsrc, erp=ERP, premium=round(premium(b), 4), premium_mode=PREMIUM_MODE, expiries_used=[ea, eb], interp_weight=round(wgt, 3),
               ladder=[dict(mult=m_, K=round(S*m_, 4), p_q=round(surv_q(S*m_), 4), p_p=round(surv_p(S*m_), 4)) for m_ in lad_m],
               quantiles={f'q{int(q*100)}': round(quant(surv_p, q), 4) for q in (0.05, 0.1, 0.15, 0.25, 0.5, 0.75, 0.85, 0.9, 0.95)},
               cdf_grid=[[round(k*shift, 5), round(p, 5)] for k, p in surv],
               method=__doc__.split('Method: ')[1].strip())
    return out

if __name__ == '__main__':
    args = sys.argv[1:]; chain_dir = None; hd = 365
    if '--chains' in args: i = args.index('--chains'); chain_dir = args[i+1]; del args[i:i+2]
    if '--horizon-days' in args: i = args.index('--horizon-days'); hd = int(args[i+1]); del args[i:i+2]
    for s in args:
        s = s.upper()
        m = market_dist(s, chain_dir, hd)
        d = os.path.join(HERE, 'priors', m['market_date'], *(['1m'] if hd <= 45 else [])); os.makedirs(d, exist_ok=True)  # kept out of the agents' run folders
        json.dump(m, open(f'{d}/{s}.json', 'w'), indent=1)
        qq = m['quantiles']
        print(f"{s:6} spot={m['spot']:<9} F={m['forward']:<10} iv1y={m['atm_iv_1y']*100:.1f}% beta={m['beta']:.2f} exp={m['expiries_used']} "
              f"q10={qq['q10']:.2f} q50={qq['q50']:.2f} q90={qq['q90']:.2f}  P(>=spot)={[l['p_p'] for l in m['ladder'] if l['mult'] == 1.0][0]}")
