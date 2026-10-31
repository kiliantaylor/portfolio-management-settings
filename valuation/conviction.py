"""Conviction scaling of the agents' view (Kilian chose conviction-scaled Kelly 2026-09-29, then this form the same day).

Each name's expected return mu is split into what the options market already implies (m, the real-world options-only
mean: risk-neutral forward plus the beta-decile equity premium, market_dist.premium()) and what the research adds (alpha = mu - m, from the agents'
curve and the analysts). The market part is sized at the base quarter Kelly for every name; that is the market's normal
risk premium, not a forecast. Only alpha is scaled by conviction:

    mu_eff = m + multiplier * (mu - m).

Conviction is how large the agents' view is relative to its uncertainty:
    view = |ln(agents' NTM target / options-market NTM target)|,
    se   = sqrt(disp^2 + FLOOR^2), disp = s.d. of ln of the five agents' round-one fair values, FLOOR = 10% for the
           model error agents share (same sources, same methods), so agreement among them cannot look precise,
    t    = view / se;  multiplier = clip(t / median(t), LO, HI) across the run's names (0.5, 1 since Kilian 2026-10-20 card "Cap at 1x"; was 0.5, 2).
EPS-revision tilt (Kilian 2026-10-08 15:20Z and 15:42Z "go ahead"; spec ../revisions_sleeve/SPEC.md): combine.py shifts the
whole blended distribution by log_shift t, so res['expected'] and the calibrated market mean both carry it. Here the
options part m is the untilted one, tilt = (1 + m)(e^t - 1) in return terms, and
    mu_eff = m + tilt + multiplier * (mu - m - tilt),
so conviction never scales the tilt, and allocate.py/review.py size it at quarter Kelly like research alpha (only m gets
the half-Kelly options treatment).
Short-interest days-to-cover tilt (Kilian 2026-10-19 card "Add short-interest days-to-cover as a 12-month return tilt"):
combine.py adds its log shift to the EPS one, so t above is the sum of both (res['revision'] and res['short_interest']
log_shift) and the days-to-cover tilt gets exactly the same treatment: quarter Kelly, never scaled by conviction.
So a name with no view of its own stays at quarter Kelly whatever the agents' agreement, and a strong, well-separated
view gets up to 2x on its alpha (1/8 to 1/2 Kelly on that part). Weak signal until the forecasts have a track record.

Usage: python3 conviction.py DATE -> runs/DATE/conviction.json. allocate.py and review.py call fractions(date)."""
import divs
import glob, json, math, os, statistics as st, sys
from combine import survival_fn
HERE = os.path.dirname(os.path.abspath(__file__))
BASE, LO, HI, FLOOR = 0.25, 0.5, 1.0, 0.10  # HI 2.0 -> 1.0: Kilian 2026-10-20 card 'Cap at 1x' (no doubling until ~20 scored 1-month dates show research skill); LO, HI, FLOOR are untested Claude defaults (2026-09-29)


def _fv(a):
    x = a.get('fair_value'); x = x.get('value') if isinstance(x, dict) else x
    return x if isinstance(x, (int, float)) and x > 0 else None


def _expectation(Ks, ps, S):
    sf = survival_fn(Ks, [min(max(p, 1e-4), 1 - 1e-4) for p in ps])
    g = [S * math.exp(-4 + 8 * i / 2000) for i in range(2001)]
    return g[0] + sum((sf(g[i]) + sf(g[i - 1])) / 2 * (g[i] - g[i - 1]) for i in range(1, len(g)))


def market_mu(res):
    """Options-only real-world mean return, on the same numerical footing as res['expected'] (review.mu_curve)."""
    S, lad, m = res['spot'], res['ladder'], res['method']
    Ks = [l['K'] for l in lad]
    E_m = _expectation(Ks, [l['market'] for l in lad], S); E_l = _expectation(Ks, [l['llm_platt'] for l in lad], S)
    an = res.get('analysts') or {}; w_an = an.get('weight') or 0
    E_a = E_m * an['median'] / res['market_only']['target'] if w_an and an.get('median') else 0
    E_now = (1 - w_an) * (m['w_market'] * E_m + m['w_llm'] * E_l) + w_an * E_a
    k = res['expected'] / E_now if E_now else 1
    return k * E_m / S - 1


def _read(date, p):
    s = p.split(os.sep)[-2]; r = json.load(open(p))
    rd = os.path.join(HERE, 'runs', r.get('research_as_of') or date, s)
    files = sorted(glob.glob(os.path.join(rd, 'round1', 'agent_*.json'))) or sorted(glob.glob(os.path.join(rd, 'agent_*.json')))
    return s, r, [v for v in (_fv(json.load(open(f))) for f in files) if v]


def fractions(date):
    rows = {}
    import concurrent.futures as cf  # 2026-10-21: the files are read 12 names at a time (shared-folder round trips); same rows, same order
    with cf.ThreadPoolExecutor(12) as ex:
        got = list(ex.map(lambda p: _read(date, p), sorted(glob.glob(os.path.join(HERE, 'runs', date, '*', 'result.json')))))
    for s, r, fv in got:
        disp = st.pstdev([math.log(v) for v in fv]) if len(fv) > 1 else 0.0
        lt, mt = (r.get('llm_only') or {}).get('target'), (r.get('market_only') or {}).get('target')
        if not (lt and mt): continue
        view = math.log(lt / mt); se = math.hypot(disp, FLOOR)
        mu = r['expected'] / r['spot'] - 1; m_t = market_mu(r)  # calibrated to res['expected'], so it carries the tilt
        lt_rev = (r.get('revision') or {}).get('log_shift') or 0.0
        lt_si = (r.get('short_interest') or {}).get('log_shift') or 0.0  # days-to-cover tilt, 2026-10-19
        lt = lt_rev + lt_si
        m = (1 + m_t) / math.exp(lt) - 1; tilt = m_t - m  # untilted options part; tilt in return terms
        d = divs.dy(s, r['spot']); mu += d; m += d  # Kilian 2026-10-12: dividends collected over the year count in the return
        rows[s] = dict(view=view, disp=disp, se=se, t=abs(view) / se, mu=mu, mu_market=m, tilt=tilt, alpha=mu - m - tilt, n_agents=len(fv),
                       tilt_log=dict(revision=round(lt_rev, 5), short_interest=round(lt_si, 5)))
    if not rows: return {}
    med = st.median(r['t'] for r in rows.values()) or 1e-9
    for s, r in rows.items():
        mult = min(max(r['t'] / med, LO), HI)
        r.update(multiplier=round(mult, 3), alpha_fraction=round(BASE * mult, 4), mu_eff=round(r['mu_market'] + r['tilt'] + mult * r['alpha'], 4),
                 median_t=round(med, 3), **{k: round(r[k], 4) for k in ('view', 'disp', 'se', 't', 'mu', 'mu_market', 'tilt', 'alpha')})
    return rows


if __name__ == '__main__':
    import fscache; fscache.install()  # 2026-10-21: read-once cache for the shared network folder (fscache.py; same output)
    d = sys.argv[1]; out = fractions(d)
    json.dump(dict(as_of=d, base=BASE, floor=FLOOR, alpha_range=[BASE * LO, BASE * HI], method=__doc__.split('\n\n')[1], names=out),
              open(os.path.join(HERE, 'runs', d, 'conviction.json'), 'w'), indent=1)
    for s, r in sorted(out.items(), key=lambda kv: -kv[1]['multiplier']):
        print(f"{s:6} view={r['view']:+.3f} disp={r['disp']:.3f} t={r['t']:.2f} x{r['multiplier']:.2f}  mu={r['mu']:+.3f} = mkt {r['mu_market']:+.3f} + alpha {r['alpha']:+.3f} -> mu_eff {r['mu_eff']:+.3f}")
