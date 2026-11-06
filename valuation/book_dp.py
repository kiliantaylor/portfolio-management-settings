"""Book-level increment for the conviction rubric (Kilian 2026-10-15 05:33Z card "Use increment").

Replaces the supervisor's P(the stock's own 12M Sharpe beats SPY's) as the rubric's p. A single stock's Sharpe is
a priori below the index's (Sharpe_i = rho(i, mkt) x Sharpe_SPY when it earns only its market risk), so that number
mostly penalised stock-specific volatility. The rubric now asks what the name does to the book:

    dP_i = P(book with i beats SPY's 12M Sharpe) - P(book without i beats SPY's 12M Sharpe)

  - Name not held: added at its solo Sharpe-test size n_i (whole shares maximising the invested-basis book Sharpe of
    held names + committed buys + n_i shares, within the name's caps and the cash), against that base book.
  - Held name: P(book) - P(book without it), shown for information (the rubric gates new buys only).
Book weights are the Sharpe test's: held names at cost (fills, fees left out), committed buys at their price, new
shares at spot; cash left out.
Simulation: N_PATHS one-year paths of 252 daily returns, multivariate normal in logs with the allocate.py covariance
(riskcal factor model recalibrated to the option market) plus SPY (vol = market implied vol, correlation from the same
model, expected return = T-bill + measured equity premium, data/beta_premium.json). Daily log drift = ln(1+mu)/252 -
var/504, so a year's expected gross return is 1 + mu. Realised Sharpe = mean / s.d. of daily excess returns (the sqrt(252)
cancels in the comparison). Weights are rebalanced daily. Every configuration uses the same paths (common random numbers,
fixed seed), so the difference carries little simulation noise (about +/-0.002 at 40,000 paths).
Bars (rubric.py): medium dP >= +0.01, high dP >= +0.03.
Usage: called from allocate.py (writes runs/DATE/book_dp.json); python3 book_dp.py DATE prints the stored table."""
import json, math, os, sys
import numpy as np
HERE = os.path.dirname(os.path.abspath(__file__))
N_PATHS, BATCH, DAYS, SEED = 40000, 1000, 252, 7
WORKERS = max(1, min(4, os.cpu_count() or 1))  # threads for the simulation batches (results do not depend on it)

def erp():
    try: return json.load(open(os.path.join(HERE, 'data', 'beta_premium.json')))['market_excess_pct'] / 100
    except Exception: return 0.0769

def solo_sizes(base_dol, cands, spot, ub, mu, C, ix, r_f):
    """Whole shares 0..ub[s] that maximise the invested-basis Sharpe of base + s (the Sharpe test, one name at a time)."""
    ks = list(base_dol); bd = np.array([base_dol[k] for k in ks]); bi = [ix[k] for k in ks]; bm = np.array([mu[k] for k in ks])
    out = {}
    for s in cands:
        n = np.arange(ub.get(s, 0) + 1); dol = np.concatenate([np.repeat(bd[None, :], len(n), 0), (n*spot[s])[:, None]], 1)
        w = dol / dol.sum(1, keepdims=True); ii = bi + [ix[s]]; Cs = C[np.ix_(ii, ii)]; m = np.append(bm, mu[s])
        sh = (w @ m - r_f) / np.sqrt(np.einsum('ij,jk,ik->i', w, Cs, w))
        out[s] = int(n[int(np.argmax(sh))])
    return out

def run(date, names, C, mu, r_f, base_dol, cand_dol_per_share, ub, corr_spy, spy_vol, held):
    """base_dol: {name: dollars} of the base book (held at cost + committed). cand_dol_per_share: {name: spot} for names
    not held. Returns and writes the table."""
    C = np.array(C); ix = {s: i for i, s in enumerate(names)}
    sizes = solo_sizes(base_dol, list(cand_dol_per_share), cand_dol_per_share, ub, mu, C, ix, r_f)
    U = list(base_dol) + [s for s, n in sizes.items() if n > 0 and s not in base_dol]
    k = len(U); Cu = np.zeros((k + 1, k + 1))
    for a, s in enumerate(U):
        for b, t in enumerate(U): Cu[a, b] = C[ix[s], ix[t]]
        Cu[a, k] = Cu[k, a] = corr_spy(s) * math.sqrt(C[ix[s], ix[s]]) * spy_vol
    Cu[k, k] = spy_vol**2
    m = np.array([mu[s] for s in U] + [r_f + erp()])
    ev, V = np.linalg.eigh(Cu); L = V * np.sqrt(np.clip(ev, 0, None))  # factor of a possibly near-singular matrix
    dm = np.log1p(m)/DAYS - np.diag(Cu)/(2*DAYS); L = L / math.sqrt(DAYS)
    def wv(d):
        w = np.zeros(k); tot = sum(d.values())
        for x, v in d.items(): w[U.index(x)] = v/tot
        return w
    cfg = {'__base__': wv(base_dol)}
    for s, n in sizes.items():
        if n > 0: cfg['add:' + s] = wv(dict(base_dol, **{s: n*cand_dol_per_share[s]}))
    for h in held:
        rest = {x: v for x, v in base_dol.items() if x != h}
        if rest: cfg['drop:' + h] = wv(rest)
    keys = list(cfg); Wm = np.stack([cfg[x] for x in keys], 1)
    def batch(z):  # one batch of paths -> hit counts per configuration (integers, so the sum does not depend on order)
        ex = (z.reshape(-1, k + 1) @ L.T).reshape(z.shape)  # one 2-D product: same numbers as the stacked one, about 2x faster
        ex += dm; np.expm1(ex, out=ex); ex -= r_f/DAYS  # in place: same values as z + dm, expm1, R - rf/DAYS, less memory
        spy = ex[:, :, k]; s_spy = spy.mean(1) / spy.std(1)
        bk = (np.ascontiguousarray(ex[:, :, :k]).reshape(-1, k) @ Wm).reshape(BATCH, DAYS, -1); s_bk = bk.mean(1) / bk.std(1)
        return (s_bk > s_spy[:, None]).sum(0)
    # 2026-11-02 (Kilian: "everything to run as fast as possible"): the normals are drawn in order from the one seeded
    # generator on this thread (same stream as before); the rest of each batch runs on WORKERS threads (numpy releases the
    # GIL). Same paths, same arithmetic per batch, integer hit counts: the table is identical to the one-thread loop.
    import collections, concurrent.futures as cf
    rng = np.random.default_rng(SEED); hits = np.zeros(len(keys)); q = collections.deque()
    bufs = [np.empty((BATCH, DAYS, k + 1)) for _ in range(WORKERS + 1)]  # reused draw buffers (filling one is ~2x faster than a new array)
    with cf.ThreadPoolExecutor(WORKERS) as pool:
        for i in range(N_PATHS // BATCH):
            while len(q) >= WORKERS + 1: hits += q.popleft().result()  # the buffer about to be refilled is no longer in use
            z = bufs[i % (WORKERS + 1)]; rng.standard_normal(out=z)
            q.append(pool.submit(batch, z))
        while q: hits += q.popleft().result()
    P = dict(zip(keys, hits / N_PATHS)); pb = P['__base__']
    tab = {}
    for s, n in sizes.items():
        tab[s] = dict(kind='add', shares=n, p_with=round(P['add:' + s], 4) if n > 0 else round(pb, 4), dp=round(P['add:' + s] - pb, 4) if n > 0 else 0.0)
    for h in held:
        if 'drop:' + h in P: tab[h] = dict(kind='held', shares=held[h], p_without=round(P['drop:' + h], 4), dp=round(pb - P['drop:' + h], 4))
    out = dict(as_of=date, p_base=round(pb, 4), n_paths=N_PATHS, seed=SEED, spy_vol=round(spy_vol, 4), spy_mu=round(r_f + erp(), 4),
               method='book_dp.py: change in P(book 12M Sharpe > SPY 12M Sharpe) at the solo Sharpe-test size', names=tab)
    json.dump(out, open(os.path.join(HERE, 'runs', date, 'book_dp.json'), 'w'), indent=1)
    return out

def lookup(sym):
    """Newest stored dP for sym (rubric.py reads this outside allocate.py)."""
    import glob
    for f in sorted(glob.glob(os.path.join(HERE, 'runs', '*', 'book_dp.json')), reverse=True):
        t = json.load(open(f)).get('names', {}).get(sym)
        if t: return dict(t, as_of=f.split('/')[-2])
    return None

if __name__ == '__main__':
    t = json.load(open(os.path.join(HERE, 'runs', sys.argv[1], 'book_dp.json')))
    print(f"P(book beats SPY Sharpe) base {t['p_base']:.3f}")
    for s, v in sorted(t['names'].items(), key=lambda kv: -kv[1]['dp']):
        if v['dp'] or v['kind'] == 'held': print(f"{s:6} {v['kind']:4} {v['shares']:>5} sh  dP {v['dp']:+.4f}")
