"""Risk model for sizing correlations (used by allocate.py, optimize.py, score.py, review.py, build_report.py, book_dp.py).

LIVE since 2026-11-02 (Kilian's pick, after the out-of-sample test in risk/model_study/report.md and the GICS
sub-industry comparison in risk/finer_model/report.md). Sizing takes only rho_ij from here; each name's vol comes from its
own 1Y distribution. Correlations come from:
  1. Risk Prism exposures and factor returns, with the factor covariance re-estimated from weekly factor returns
     (exponentially weighted, half-life 26 weeks; no scale-up to implied vol).
  2. Two extra semis factors, each the equal-weight average residual of a basket: AI chips and chip equipment, and analog
     chips. Each name's loadings come from a ridge regression of its weekly residual on the basket factors (half-life 52 weeks,
     prior worth 26 weeks at loading 0, the name left out of its own basket).
  3. Specific risk from each name's own weekly residuals (half-life 26 weeks; no top-up to implied or realized vol).
     Names with under 26 weeks of residuals use Risk Prism's specific x 1.2 (our weekly residual vols run ~1.2x theirs).
  4. The sample correlation of weekly returns blended in: rho = d x model + (1 - d) x sample, with d from the Ledoit-Wolf
     fixed-target rule (sampling noise / distance). 104 weeks where both names have them, else 52, else the model alone.
Replaced (backup riskcal.py.bak-v2_1102): Risk Prism with each name's specific risk topped up to its 1Y implied vol.
Usage: M = riskcal.load(DATE, syms)  -> riskcalc.Model subclass; M.corr/M.cov use the blend,
M.risk/M.beta_spy keep the factor structure (reporting only). M.calib records the market's implied vol for reference."""
import csv, glob, json, math, os, sys
import numpy as np
HERE = os.path.dirname(os.path.abspath(__file__)); PORT = os.path.join(HERE, '..', 'portfolio')
sys.path.insert(0, os.path.join(HERE, '..', 'risk')); import riskcalc
HL_F, HL_S, HL_B, PRIOR_WEEKS, MIN_RESID = 26, 26, 52, 26, 26
AI_SEMIS = ['NVDA', 'AMD', 'AVGO', 'MU', 'INTC', 'AMAT', 'LRCX', 'ASML', 'TSM', 'MRVL', 'ALAB', 'CRDO', 'RMBS']
ANALOG = ['MCHP', 'NXPI', 'ON', 'STM', 'TXN', 'QCOM', 'LSCC']
BASKETS = [('semis_ai', AI_SEMIS), ('semis_analog', ANALOG)]

def market_iv(date):
    fs = sorted(f for f in glob.glob(os.path.join(PORT, 'data', 'iv', '*.json')) if os.path.basename(f)[:10] <= date)
    if fs:
        v = (json.load(open(fs[-1]))['symbols'].get('SPY') or {}).get('iv30')
        if v: return v, 'SPY 30D full-strike IV ' + os.path.basename(fs[-1])[:10]
    rows = list(csv.DictReader(open(os.path.join(PORT, 'data', 'volindex_VIX.csv'))))
    return float(rows[-1][list(rows[-1])[1]]) / 100, 'VIX ' + rows[-1][list(rows[-1])[0]]

def _closes(s, date):
    for d in (os.path.join(HERE, 'data'), os.path.join(PORT, 'data')):
        p = f'{d}/daily_{s}.csv'
        if os.path.exists(p): return {r['date']: float(r['close']) for r in csv.DictReader(open(p)) if r['date'] <= date and r['close']}
    return None

def _fridays(dates):
    """Last trading day of each ISO week (Fri-to-Fri weekly bars)."""
    import datetime as dt
    out = {}
    for d in sorted(dates):
        y, w, _ = dt.date.fromisoformat(d).isocalendar(); out[(y, w)] = d
    return out

def _ew(n, hl): w = 0.5 ** (np.arange(n)[::-1] / hl); return w / w.sum()

def _ewcov(Y, hl): w = _ew(len(Y), hl); Z = Y - w @ Y; return (Z.T * w) @ Z * 52

def _corr(C): d = np.sqrt(np.diag(C)); return C / np.outer(d, d)

def _blend(Y, Rm):
    T, N = Y.shape; Z = (Y - Y.mean(0)) / Y.std(0); Rs = Z.T @ Z / T
    pi = (Z ** 2).T @ (Z ** 2) / T - Rs ** 2; off = ~np.eye(N, dtype=bool)
    d = max(0.0, min(1.0, pi[off].sum() / T / ((Rs - Rm) ** 2)[off].sum())); return d * Rm + (1 - d) * Rs, d

class Blended(riskcalc.Model):
    def __init__(self, m, R, idx):
        super().__init__(m); self.R, self.idx = R, idx
    def corr(self, a, b):
        if a in self.idx and b in self.idx: return float(self.R[self.idx[a], self.idx[b]])
        return super().corr(a, b)
    def cov(self, a, b):
        if a in self.idx and b in self.idx and a != b: return self.corr(a, b) * self.vol(a) * self.vol(b)
        return super().cov(a, b)
    def vol(self, t): return math.sqrt(super().cov(t, t))

def load(date, syms=None):
    raw = riskcalc.load(); m = json.loads(json.dumps(raw.m)); K = len(m['factors'])
    syms = syms or [os.path.basename(os.path.dirname(p)) for p in glob.glob(os.path.join(HERE, 'runs', date, '*', 'result.json'))]
    syms = [s for s in dict.fromkeys(syms) if raw.covered(s) and s != 'SPY']
    pool = list(dict.fromkeys(syms + AI_SEMIS + ANALOG))
    # weekly factor returns (compounded daily; weeks with >= 3 days) and weekly stock returns on the same Fridays
    fr = m['factor_returns']; fdates = [d for d in fr['dates'] if d <= date]
    fw = _fridays(fdates); fpos = {d: i for i, d in enumerate(fr['dates'])}
    weeks = sorted(fw); Fd = np.array([fr[f] for f in m['factors']]).T
    wk_f, wk_end, start = [], [], 0
    for key in weeks:
        end = fpos[fw[key]]; seg = [i for i in range(start, end + 1)]; start = end + 1
        if len(seg) >= 3: wk_f.append(np.prod(1 + Fd[seg], axis=0) - 1); wk_end.append(fw[key])
    wk_f = np.array(wk_f)
    px = {s: _closes(s, date) for s in pool}
    allweeks = sorted({d for c in px.values() if c for d in c})
    stock_fri = sorted(_fridays(allweeks).values())
    Rw = np.full((len(stock_fri), len(pool)), np.nan)
    for j, s in enumerate(pool):
        c = px[s]
        if not c: continue
        for i in range(1, len(stock_fri)):
            a, b = c.get(stock_fri[i - 1]), c.get(stock_fri[i])
            if a and b: Rw[i, j] = b / a - 1
    rpos = {d: i for i, d in enumerate(stock_fri)}
    common = [d for d in wk_end if d in rpos]
    Fc = wk_f[[wk_end.index(d) for d in common]]; Rc = Rw[[rpos[d] for d in common]]
    X = np.array([m['exposures'][s] for s in pool]); E = Rc - Fc @ X.T          # weekly residuals vs Risk Prism factors
    # extra semis factors
    G, parts = [], []
    for name, bk in BASKETS:
        cols = [pool.index(s) for s in bk if s in pool]; eb = E[:, cols]; cnt = (~np.isnan(eb)).sum(1); tot = np.nansum(eb, 1)
        parts.append((cols, eb, cnt, tot)); G.append(np.where(cnt > 0, tot / np.maximum(cnt, 1), np.nan))
    G = np.array(G).T; ok = np.all([p[2] >= 4 for p in parts], axis=0)
    Fc, E, G, Rc_ok = Fc[ok], E[ok], G[ok], Rc[ok]; parts = [(c, eb[ok], n[ok], t[ok]) for c, eb, n, t in parts]
    w = _ew(len(G), HL_B); P = PRIOR_WEEKS * w.mean() * np.diag((w[:, None] * G ** 2).sum(0))
    B, S = {}, {}
    for j, s in enumerate(pool):
        Gi = np.column_stack([np.where(n - (~np.isnan(eb[:, cols.index(j)]) if j in cols else 0) > 0,
                                       (t - (np.nan_to_num(eb[:, cols.index(j)]) if j in cols else 0)) /
                                       np.maximum(n - ((~np.isnan(eb[:, cols.index(j)])) if j in cols else 0), 1), np.nan)
                              for cols, eb, n, t in parts])
        y = E[:, j]; mk = ~np.isnan(y) & ~np.isnan(Gi).any(1)
        if mk.sum() < MIN_RESID:
            B[s] = [0.0] * len(BASKETS); S[s] = raw.S[s] * 1.2; continue
        A = (Gi[mk].T * w[mk]) @ Gi[mk] + P; b = np.linalg.solve(A, (Gi[mk].T * w[mk]) @ y[mk]); B[s] = b.tolist()
        e2 = (y - Gi @ b)[mk]; ws = _ew(len(y), HL_S)[mk]; S[s] = math.sqrt((ws * e2 ** 2).sum() / ws.sum() * 52)
    Fa = _ewcov(np.column_stack([Fc, G]), HL_F)
    m['factors'] = m['factors'] + [n for n, _ in BASKETS]; m['cov'] = Fa.tolist()
    # daily basket returns too (equal-weight daily residual vs Risk Prism factors), so the dashboard's factor replay has them
    fdx = fr['dates']; Fdd = np.array([fr[f] for f in m['factors'][:K]]).T
    for name, bk in BASKETS:
        acc = np.zeros(len(fdx)); cnt = np.zeros(len(fdx))
        for s_ in bk:
            c = px.get(s_)
            if not c or s_ not in m['exposures']: continue
            x = np.array(m['exposures'][s_][:K]); fac = Fdd @ x
            for i in range(1, len(fdx)):
                a, b = c.get(fdx[i - 1]), c.get(fdx[i])
                if a and b: acc[i] += b / a - 1 - fac[i]; cnt[i] += 1
        m['factor_returns'][name] = [float(v) for v in np.where(cnt > 0, acc / np.maximum(cnt, 1), 0.0)]
    for s, x in m['exposures'].items(): m['exposures'][s] = list(x) + B.get(s, [0.0] * len(BASKETS))
    for s, v in S.items(): m['specific'][s] = v
    m['spy']['x'] = list(m['spy']['x']) + [0.0] * len(BASKETS)
    m['exposures']['SPY'] = list(m['spy']['x']); m['specific']['SPY'] = 0.01
    base = riskcalc.Model(m)
    Cm = np.array([[base.cov(a, b) for b in syms] for a in syms]); Rm = _corr(Cm)
    # sample blend: 104 weeks where both names have them, else 52, else the model alone
    Rs_all = Rw[[rpos[d] for d in stock_fri[-104:]]][:, [pool.index(s) for s in syms]]
    Rh = Rm.copy(); deltas = {}
    for W in (52, 104):
        Y = Rs_all[-W:]; have = [i for i in range(len(syms)) if not np.isnan(Y[:, i]).any()]
        if len(have) < 3: continue
        Rb, d = _blend(Y[:, have], Rm[np.ix_(have, have)]); Rh[np.ix_(have, have)] = Rb; deltas[W] = round(d, 4)
    M = Blended(m, Rh, {s: i for i, s in enumerate(syms)})
    spy_vol = math.sqrt(sum(a * b for a, b in zip(m['spy']['x'], base.Fx(m['spy']['x']))))
    iv_mkt, iv_src = market_iv(date)
    M.calib = dict(k=1.0, spy_model_vol=round(spy_vol, 4), market_iv=round(iv_mkt, 4), market_iv_source=iv_src, blend_delta=deltas,
                   semis_loadings={s: [round(v, 3) for v in B[s]] for s in syms if any(abs(v) > 0.2 for v in B[s])},
                   weeks=len(G), method=__doc__.split('\n\n')[1].strip())
    M.raw = raw
    return M
