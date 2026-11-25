"""Plain-Python portfolio risk on the flattened Risk Prism model (see fetch_model.py). Same math as
riskprism.RiskModel.portfolio_risk: factor variance x'Fx with x = X'w, specific variance sum w_i^2 s_i^2,
annualized decimals. Used by build_v5.py (dashboard Risk tab) and valuation/allocate.py (sizing)."""
import glob, json, math, os
HERE = os.path.dirname(os.path.abspath(__file__))
STYLES = ['size', 'value', 'growth', 'momentum', 'beta', 'volatility', 'liquidity', 'quality', 'leverage']

def load(path=None):
    path = path or sorted(glob.glob(os.path.join(HERE, 'model', 'riskprism_*.json')))[-1]
    return Model(json.load(open(path)))

class Model:
    def __init__(self, m):
        self.m, self.factors, self.F = m, m['factors'], m['cov']
        self.X, self.S = m['exposures'], m['specific']
    def covered(self, t): return t in self.X and self.S.get(t) is not None
    def x(self, w):  # portfolio factor exposures
        K = len(self.factors); x = [0.0]*K
        for t, wt in w.items():
            if self.covered(t):
                for k, e in enumerate(self.X[t]): x[k] += wt*e
        return x
    def Fx(self, x): return [sum(r[j]*x[j] for j in range(len(x))) for r in self.F]
    def cov(self, a, b):  # annualized covariance of two assets
        xa, xb = self.X[a], self.X[b]; F = self.F
        v = sum(xa[i]*sum(F[i][j]*xb[j] for j in range(len(xb))) for i in range(len(xa)))
        return v + (self.S[a]**2 if a == b else 0.0)
    def vol(self, t): return math.sqrt(self.cov(t, t))
    def corr(self, a, b): return self.cov(a, b) / (self.vol(a)*self.vol(b))
    def risk(self, w):
        """w: {ticker: weight of total wealth}; cash is simply left out (zero risk)."""
        w = {t: v for t, v in w.items() if v}
        cov_w = {t: v for t, v in w.items() if self.covered(t)}
        x = self.x(cov_w); Fx = self.Fx(x)
        fvar = sum(a*b for a, b in zip(x, Fx)); svar = sum(v*v*self.S[t]**2 for t, v in cov_w.items())
        tv = math.sqrt(fvar + svar)
        ctr = {}  # each asset's contribution to total vol (sums to tv): w_i (X_i Fx + s_i^2 w_i) / tv
        for t, v in cov_w.items():
            ctr[t] = v*(sum(e*f for e, f in zip(self.X[t], Fx)) + self.S[t]**2*v)/tv if tv else 0.0
        fvc = {f: x[k]*Fx[k] for k, f in enumerate(self.factors)}  # factor variance contributions, sum = fvar
        return dict(total_vol=tv, factor_vol=math.sqrt(fvar), specific_vol=math.sqrt(svar),
                    factor_share=fvar/(fvar+svar) if tv else None, exposures=dict(zip(self.factors, x)),
                    factor_var_contrib=fvc, asset_vol_contrib=ctr, uncovered=[t for t in w if t not in cov_w])
    def beta_spy(self, w):
        """Forecast beta to the S&P 500 (SPY look-through exposures; factor part only)."""
        xs = self.m['spy']['x']; Fs = self.Fx(xs); vs = sum(a*b for a, b in zip(xs, Fs))
        return sum(a*b for a, b in zip(self.x(w), Fs)) / vs
    def stress(self, w, shocks):
        x = dict(zip(self.factors, self.x(w)))
        return {f: x[f]*s for f, s in shocks.items()}
