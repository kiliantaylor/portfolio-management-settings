"""One joint mean-variance solve for the whole book (Kilian 2026-09-29: "solve as one portfolio against the factor model").

    maximize   sum_i w_i (mu_i - rf)  -  (gamma / 2) w' Sigma w           (gamma = 4: quarter Kelly)
    subject to lo_i <= w_i <= cap_i      (cap: stop budget, supervisor, 20% ceiling; 0 when the entry gate fails;
                                          held names in holdings mode are fixed at their current weight, lo = cap)
               sum_i w_i <= budget        (what the account plus new cash can buy; the rest earns rf in T-bills)
               risk from any one industry <= max_industry_risk_share of total variance
                                          (industry = the name's Risk Prism industry factor; risk = Euler contribution
                                          w_i (Sigma w)_i summed over its names)

w are shares of total wealth, mu_i the conviction-adjusted 1Y expected return (conviction.py), Sigma_ij = rho_ij s_i s_j
with s_i the s.d. of the name's blended 1Y distribution and rho_ij from the recalibrated factor model (riskcal.py).
Solved by projected gradient ascent (box + budget projection by bisection); the industry limit is a quadratic penalty
raised until it holds. Pure Python, 15-20 names, milliseconds."""
import math

def project(v, lo, hi, budget):
    w = [min(max(a, l), h) for a, l, h in zip(v, lo, hi)]
    if sum(w) <= budget + 1e-12: return w
    a, b = min(x - h for x, h in zip(v, hi)) - 1, max(v) + 1
    for _ in range(100):
        t = (a + b) / 2
        s = sum(min(max(x - t, l), h) for x, l, h in zip(v, lo, hi))
        a, b = (t, b) if s > budget else (a, t)
    return [min(max(x - b, l), h) for x, l, h in zip(v, lo, hi)]

def _solve_np(mu, rf, C, gamma, lo, hi, budget):
    """The same projected-gradient iteration as solve() without the industry limit, in numpy (Kilian 2026-10-21 'yes':
    with the rubric gate off ~100 names enter the solve and allocate.py's position-limit loop re-solves it ~n^2/2 times;
    pure Python took hours). Same start, step, projection, stopping rule and iteration cap; weights match to ~1e-16."""
    import numpy as np
    A = np.asarray(C, float); ex = np.asarray(mu, float) - rf; lo_ = np.asarray(lo, float); hi_ = np.asarray(hi, float)
    def proj(v):
        w = np.minimum(np.maximum(v, lo_), hi_)
        if w.sum() <= budget + 1e-12: return w
        a, b = float((v - hi_).min()) - 1, float(v.max()) + 1
        for _ in range(100):
            t = (a + b) / 2
            a, b = (t, b) if np.minimum(np.maximum(v - t, lo_), hi_).sum() > budget else (a, t)
        return np.minimum(np.maximum(v - b, lo_), hi_)
    L = float(np.abs(A).sum(axis=1).max()) * gamma; step = 1.0 / L
    w = proj(np.zeros(len(ex)))
    for it in range(20000):
        nw = proj(w + step * (ex - gamma * (A @ w)))
        d = float(np.abs(nw - w).max()); w = nw
        if d < 1e-10: break
    cw = A @ w; tv = float(w @ cw)
    return [float(x) for x in w], dict(iterations=0, penalty=0.0, variance=tv, group_share={})


def solve(mu, rf, C, gamma, lo, hi, budget, groups=None, max_share=None):
    """mu: list of expected returns; C: covariance (list of lists); groups: {name: [indices]}; returns (w, info)."""
    if not groups or not max_share: return _solve_np(mu, rf, C, gamma, lo, hi, budget)  # no industry limit: penalty never binds
    n = len(mu); ex = [m - rf for m in mu]
    Cw = lambda w: [sum(C[i][j]*w[j] for j in range(n)) for i in range(n)]
    L = max(sum(abs(x) for x in r) for r in C) * gamma  # Lipschitz bound of the plain objective
    def viol(w, cw):
        tv = sum(a*b for a, b in zip(w, cw)); out = {}
        if not groups or not max_share or tv <= 0: return out, tv
        for g, idx in groups.items():
            gv = sum(w[i]*cw[i] for i in idx) - max_share*tv
            if gv > 0: out[g] = gv
        return out, tv
    def grad(w, lam):
        cw = Cw(w); g = [e - gamma*c for e, c in zip(ex, cw)]
        v, tv = viol(w, cw)
        for grp, gv in v.items():  # d/dw of (sum_{i in G} w_i (Cw)_i - s w'Cw) = (1_G w)C + C(1_G w) - 2 s Cw
            wg = [w[i] if i in groups[grp] else 0 for i in range(n)]; cwg = Cw(wg)
            for i in range(n):
                dg = (cw[i] if i in groups[grp] else 0) + cwg[i] - 2*max_share*cw[i]
                g[i] -= lam * gv * dg
        return g
    w = project([0.0]*n, lo, hi, budget); lam = 0.0
    for outer in range(12):
        step = 1.0 / (L * (1 + lam * 0.05))
        for it in range(20000):
            g = grad(w, lam); nw = project([a + step*b for a, b in zip(w, g)], lo, hi, budget)
            d = max(abs(a - b) for a, b in zip(nw, w)); w = nw
            if d < 1e-10: break
        v, tv = viol(w, Cw(w))
        if not v or max(v.values()) / tv < 1e-3: break
        lam = 100.0 if lam == 0 else lam * 4
    cw = Cw(w); tv = sum(a*b for a, b in zip(w, cw))
    return w, dict(iterations=outer, penalty=lam, variance=tv,
                   group_share={g: sum(w[i]*cw[i] for i in idx)/tv for g, idx in (groups or {}).items()} if tv else {})
