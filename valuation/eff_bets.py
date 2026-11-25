"""Weighted effective bets of the live book (Kilian 2026-11-09 card 'Keep caps, show bets on page').
A number to watch, NOT a rule: nothing in sizing, allocate or rules_check reads it. The AI 50% cap and the 8-name cap stay.

Measure (same as risk/eff_bets/six_bets.py and risk/position_count/stress_bets.py dr2): squared diversification ratio
    bets = (sum_i w_i s_i)^2 / (w' C w)
w = dollar weights of the stocks held (cash left out; the ratio does not depend on scale), s = annualised daily vols,
C = covariance. Equal weights and equal vols give exactly N / (1 + (N-1) rho_bar).

Two versions:
  last2y : all trading days in the 2 years to the latest close on file; vols and correlations from that window.
  stress : days since 2018-01-02 when QQQ fell 3% or more (Kilian 2026-11-09 card 'QQQ down 3% or more': the book's risk is
           AI/tech, which QQQ tracks; 66 days to 11-05 vs 33 for SPY, about the same result); correlations from those days only, vols from all days since
           2018 (only the correlation is swapped, as in stress_bets.py). If there are no more stress days than names the
           correlations are shrunk 10% toward 0 (six_bets.py) so C stays invertible; otherwise they are used as is.
A name without enough history for a window is left out of that window and listed under 'left_out'.
Daily closes (QQQ too; cboe_closes.py appends every file there nightly): portfolio/data/daily_SYM.csv, then valuation/data/daily_SYM.csv (both are in the live slice).
"""
import csv, os, datetime as dt
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SRC = ((os.path.join(ROOT, 'portfolio', 'data', 'daily_{}.csv'), 'date', 'close'),
       (os.path.join(HERE, 'data', 'daily_{}.csv'), 'date', 'close'))
STRESS_START, STRESS_IDX, STRESS_DROP = '2018-01-02', 'QQQ', -0.03
_CACHE = {}


def load(s):
    """Date -> close, from the file with the widest coverage."""
    if s in _CACHE: return _CACHE[s]
    best = None
    for f, dk, ck in SRC:
        f = f.format(s)
        if not os.path.exists(f): continue
        try: r = {x[dk][:10]: float(x[ck]) for x in csv.DictReader(open(f)) if x.get(ck) not in ('', None)}
        except Exception: continue
        if r and (best is None or (min(r) <= min(best) and max(r) >= max(best))): best = r
    _CACHE[s] = best or {}
    return _CACHE[s]


def dr2(w, vol, C): return float((w @ vol) ** 2 / (w @ C @ w))


def _window(names, start, end, idx='SPY'):
    D = {s: load(s) for s in names + [idx]}
    if not D[idx]: return None
    ok = [s for s in names if D[s] and min(D[s]) <= start and max(D[s]) >= end]
    if not ok: return None
    days = sorted(d for d in set.intersection(*[set(D[s]) for s in ok + [idx]]) if start <= d <= end)
    if len(days) < 3: return None
    px = np.array([[D[s][d] for d in days] for s in ok + [idx]])
    R = px[:, 1:] / px[:, :-1] - 1
    return ok, R[:-1], R[-1], days


def compute(dollars):
    """dollars: {SYM: $ held} (stocks only). Returns the page/JSON block, or None if nothing can be computed."""
    w0 = {s: float(v) for s, v in (dollars or {}).items() if v and float(v) > 0 and s != 'SPY'}
    if len(w0) < 2: return None
    names = sorted(w0)
    spy = load('SPY')
    if not spy: return None
    end = max(spy)
    out = dict(measure='(sum w s)^2 / w\'Cw', data_to=end, rule=False)
    # last 2 years
    start2 = (dt.date.fromisoformat(end) - dt.timedelta(days=730)).isoformat()
    W = _window(names, start2, end)
    if W:
        ok, R, _, days = W
        w = np.array([w0[s] for s in ok]); w = w / w.sum(); vol = R.std(1) * np.sqrt(252)
        out['last2y'] = dict(bets=round(dr2(w, vol, np.cov(R) * 252), 2), n_names=len(ok), n_days=R.shape[1], window=[days[0], days[-1]],
                             left_out=[s for s in names if s not in ok],
                             avg_corr=round(float(np.mean(np.corrcoef(R)[np.triu_indices(len(ok), 1)])), 2) if len(ok) > 1 else None)
    # QQQ <= -3% days since 2018
    W = _window(names, STRESS_START, end, STRESS_IDX)
    if W:
        ok, R, idxR, days = W
        k = idxR <= STRESS_DROP
        if k.sum() >= 3:
            w = np.array([w0[s] for s in ok]); w = w / w.sum(); vol = R.std(1) * np.sqrt(252)
            Cr = np.corrcoef(R[:, k]); shrunk = int(k.sum()) <= len(ok)
            if shrunk: Cr = 0.9 * Cr + 0.1 * np.eye(len(ok))
            out['stress'] = dict(bets=round(dr2(w, vol, np.outer(vol, vol) * Cr), 2), n_names=len(ok), n_days=int(k.sum()),
                                 index=STRESS_IDX, threshold=STRESS_DROP, window=[days[0], days[-1]], left_out=[s for s in names if s not in ok],
                                 shrunk=shrunk, avg_corr=round(float(np.mean(Cr[np.triu_indices(len(ok), 1)])), 2) if len(ok) > 1 else None)
    return out if ('last2y' in out or 'stress' in out) else None


if __name__ == '__main__':
    import json, sys
    D = sys.argv[1] if len(sys.argv) > 1 else sorted(os.listdir(os.path.join(HERE, 'runs')))[-1]
    rs = json.load(open(os.path.join(HERE, 'runs', D, 'review_summary.json')))
    print(json.dumps(rs.get('eff_bets'), indent=1))
