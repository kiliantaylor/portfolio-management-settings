"""Risk Prism factor model recalibrated to today's option market (used by optimize.py).

The raw weekly model (../risk/riskcalc.py) runs below what stocks actually do: on 2026-09-28 its single-name vols sat
under both 1Y implied and realized vol (SPCX 27% vs 53% implied, EL 29% vs 42%, META 32% vs 42%), and its S&P 500 factor
vol was 14.1% vs 16.0% implied. Most of the shortfall is stock-specific risk; the factors run a little low too.
Two fixes, the same idea as Barra's volatility regime adjustment:
  1. Factor scale: multiply the whole factor covariance by k^2 so the model's S&P 500 vol equals the market's implied vol
     (SPY 30D full-strike IV from ../portfolio/data/iv, else the latest VIX).
  2. Specific top-up: set each name's specific variance so its total vol equals its 1Y implied vol from the option chain
     (priors/DATE/SYM.json atm_iv_1y; else 1Y realized): s_i^2 = max(target_i^2 - k^2 * factor_var_i, (0.05)^2).
Factor exposures and factor correlations are untouched, so names still co-move through the same factors, but a name whose
extra risk is its own (a new listing, a deal, earnings) no longer looks more correlated than it is.
Usage: import riskcal; M = riskcal.load(DATE)  -> riskcalc.Model with .calib (k, targets, raw vols) attached."""
import csv, glob, json, math, os, sys
HERE = os.path.dirname(os.path.abspath(__file__)); PORT = os.path.join(HERE, '..', 'portfolio')
sys.path.insert(0, os.path.join(HERE, '..', 'risk')); import riskcalc
SPEC_FLOOR = 0.05

def market_iv(date):
    fs = sorted(f for f in glob.glob(os.path.join(PORT, 'data', 'iv', '*.json')) if os.path.basename(f)[:10] <= date)
    if fs:
        v = (json.load(open(fs[-1]))['symbols'].get('SPY') or {}).get('iv30')
        if v: return v, 'SPY 30D full-strike IV ' + os.path.basename(fs[-1])[:10]
    rows = list(csv.DictReader(open(os.path.join(PORT, 'data', 'volindex_VIX.csv'))))
    return float(rows[-1][list(rows[-1])[1]]) / 100, 'VIX ' + rows[-1][list(rows[-1])[0]]

def name_target(s, date):
    p = os.path.join(HERE, 'priors', date, f'{s}.json')
    if os.path.exists(p):
        m = json.load(open(p))
        if m.get('atm_iv_1y'): return m['atm_iv_1y'], '1Y implied'
    import allocate; c = list(allocate.closes(s).values())[-253:]
    r = [math.log(c[i]/c[i-1]) for i in range(1, len(c))]; mu = sum(r)/len(r)
    return math.sqrt(sum((x-mu)**2 for x in r)/(len(r)-1)*252), '1Y realized'

def load(date, syms=None):
    raw = riskcalc.load(); m = json.loads(json.dumps(raw.m))
    spy_raw = math.sqrt(sum(a*b for a, b in zip(m['spy']['x'], raw.Fx(m['spy']['x']))))
    iv_mkt, iv_src = market_iv(date); k = iv_mkt / spy_raw
    m['cov'] = [[v*k*k for v in r] for r in raw.F]
    syms = syms or [os.path.basename(os.path.dirname(p)) for p in glob.glob(os.path.join(HERE, 'runs', date, '*', 'result.json'))]
    tg = {}
    for s in syms:
        if not raw.covered(s) or s == 'SPY': continue
        fv = (raw.cov(s, s) - raw.S[s]**2) * k * k; t, src = name_target(s, date)
        m['specific'][s] = math.sqrt(max(t*t - fv, SPEC_FLOOR**2))
        tg[s] = dict(target_vol=round(t, 4), source=src, raw_vol=round(raw.vol(s), 4), raw_specific=round(raw.S[s], 4),
                     factor_vol=round(math.sqrt(fv), 4), specific=round(m['specific'][s], 4))
    # an S&P 500 fund is its look-through exposures with ~no specific risk, so its vol is the market's implied vol
    m['exposures']['SPY'] = list(m['spy']['x']); m['specific']['SPY'] = 0.01
    M = riskcalc.Model(m)
    M.calib = dict(k=round(k, 4), market_iv=round(iv_mkt, 4), market_iv_source=iv_src, spy_raw_vol=round(spy_raw, 4), names=tg,
                   method=__doc__.split('\n\n')[1].strip())
    M.raw = raw
    return M
