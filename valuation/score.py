"""Single ensemble score, run beside the conviction rubric (Kilian 2026-10-07 15:33Z "yes" to adhoc/ensemble_score_draft.md).

Shadow mode until the 1-month scorecard has about 20 scored forecast dates (Kilian 2026-10-19 card "Extend trial";
was until 2026-10-21): the page shows the score next to the rubric; buys are gated by allocate.py (its rubric gate is off since Kilian 2026-10-21, so only the Sharpe tests).
Switching the buy gate needs Kilian's yes.

Per name:
    mu_ens = m + tilt + a   (tilt = EPS-revision tilt from conviction.py, 2026-10-08)
    m  = options-market part: conviction.py mu_market (T-bill + beta x ERP on the options-implied distribution).
    a  = research alpha = multiplier x w_llm x (r_research - m), where
         r_research = average of the agents' 12M mean return (supervisor ladder) and the reconciled fair value's return,
         w_llm      = the research weight in combine.py (33%),
         multiplier = conviction.py's shrink, clip(t / median t, 0.5, 1) (upper clip 2 until Kilian's 2026-10-20 card 'Cap at 1x'), t = |agents vs market| / sqrt(disp^2 + 10%^2).
    Open supervisor flags halve a; a thesis-break or fact-check flag blocks the name (score shown, no buy).
    score = gain in book Sharpe at the name's best share count (0..cap, exhaustive) against the live book at cost plus the
            committed buys (portfolio.json sharpe_combo), every name at mu_ens, riskcal correlations, same Sharpe formula as
            allocate.py (invested dollars, cash left out). Caps: stop budget, cash after committed
            buys. Would buy = score > 0 and a free name slot (cap 10).

Usage: python3 score.py DATE -> runs/DATE/score.json."""
import divs
import glob, json, math, os, sys
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import allocate as A, conviction, rubric, book
from conviction import _expectation
SHADOW_UNTIL = '~20 scored 1-month dates (scorecard)'  # Kilian 2026-10-19 "Extend trial"; was '2026-10-21'
BLOCK_WORDS = ('thesis', 'fact-check', 'fact check')
DEFENSE = {'RTX', 'LMT', 'NOC', 'GD', 'LHX', 'HII', 'BA', 'LDOS', 'KTOS', 'AVAV', 'TDG', 'HEI'}  # Kilian 2026-09-30: no defense names


def agents_mean(res):
    S, lad, m = res['spot'], res['ladder'], res['method']
    Ks = [l['K'] for l in lad]
    E_m = _expectation(Ks, [l['market'] for l in lad], S); E_l = _expectation(Ks, [l['llm_platt'] for l in lad], S)
    an = res.get('analysts') or {}; w_an = an.get('weight') or 0
    E_a = E_m * an['median'] / res['market_only']['target'] if w_an and an.get('median') else 0
    E_now = (1 - w_an) * (m['w_market'] * E_m + m['w_llm'] * E_l) + w_an * E_a
    k = res['expected'] / E_now if E_now else 1  # same calibration as conviction.market_mu
    return k * E_l / S - 1


def main(date):
    cfg = json.load(open(os.path.join(HERE, 'data', 'review_settings.json')))
    pf = json.load(open(os.path.join(HERE, 'runs', date, 'portfolio.json')))
    import riskcal; RM = riskcal.load(date)
    CONV = conviction.fractions(date); rf = A.rf(); FEE = cfg.get('fee_per_trade', 6); FEE_MAX = cfg.get('fee_max_share_of_trade', 0.05)
    H = book.load(); held = {p['symbol']: p for p in H['positions'] if p.get('shares')}
    committed = ((pf.get('sharpe_combo') or {}).get('committed') or {})
    res = {}
    for p in glob.glob(os.path.join(HERE, 'runs', date, '*', 'result.json')):
        s = p.split(os.sep)[-2]
        if s in ('SPY', 'SIVE') or s in (cfg.get('removed_names') or []): continue  # removed names (defense, Kilian 2026-10-07) are not scored
        r = json.load(open(p))
        if r.get('ladder') and s in CONV: res[s] = r
    SECT = json.load(open(os.path.join(HERE, 'data', 'sectors.json')))
    rows = {}
    rubric.warm(list(res))
    for s, r in res.items():
        c = CONV[s]; m = c['mu_market']; lab = rubric.label(s) or {}
        r_ag = agents_mean(r) - c.get('tilt', 0.0); fv = lab.get('fair_value') or r.get('fair_value_median')  # 2026-10-14: the agents' mean carries the revision tilt; mu_ens adds it once below
        r_fv = fv / r['spot'] - 1 if isinstance(fv, (int, float)) else None
        d = divs.dy(s, r['spot']); r_ag += d; r_fv = None if r_fv is None else r_fv + d  # 2026-10-12: total return, like mu_market
        r_res = (r_ag + r_fv) / 2 if r_fv is not None else r_ag
        a = c['multiplier'] * r['method']['w_llm'] * (r_res - m)
        flags = [str(f) for f in (lab.get('notch_down') or []) if str(f).startswith('open flags')]
        block = any(w in f.lower() for f in flags for w in BLOCK_WORDS)
        if flags: a *= 0.5
        why_out = ('excluded by you' if s in (cfg.get('excluded_names') or []) else 'financials (avoid, Kilian 2026-10-03)' if SECT.get(s) == 'Financials'
                   else 'defense (no defense names, Kilian 2026-09-30)' if s in DEFENSE else 'thesis-break or fact-check flag' if block else None)
        bar = A.beta_bar(s, date)  # Kilian 2026-10-22 00:47Z: allocate.py's buy gate, on mu_eff (the Sharpe test's return)
        _br = A.bar_return(s, c['mu_eff'], r['target'], r['spot'])
        if not why_out and bar is not None and _br <= bar:
            why_out = f"{'target return' if A.BAR_BASIS == 'target' else 'expected return'} {_br:.1%} not above its beta-adjusted {bar:.1%} (Kilian 2026-10-22)"
        rows[s] = dict(mu_market=round(m, 4), r_agents=round(r_ag, 4), r_fair_value=None if r_fv is None else round(r_fv, 4),
                       multiplier=c['multiplier'], alpha=round(a, 4), mu_ens=round(m + c.get('tilt', 0.0) + a, 4), flags=flags, blocked=bool(why_out), blocked_reason=why_out,
                       rubric=lab.get('label'), held=s in held, beta_bar=None if bar is None else round(bar, 4))
    mu = {s: v['mu_ens'] for s, v in rows.items()}; sig = {s: A.moments(res[s])[1] for s in rows}
    cache = {}
    def rho(a, b):
        if a == b: return 1.0
        k = (a, b) if a < b else (b, a)
        if k not in cache: cache[k] = RM.corr(a, b) if RM.covered(a) and RM.covered(b) else A.corr(A.closes(a), A.closes(b))[0]
        return cache[k]
    base = {s: held[s]['cost_basis'] for s in held if s in rows}
    base.update({s: c_['shares'] * c_['price'] for s, c_ in committed.items() if s in rows and s not in base})
    def sharpe(dol):
        tot = sum(dol.values()); w = {k: v / tot for k, v in dol.items()}
        v = math.sqrt(sum(w[x] * w[y] * rho(x, y) * sig[x] * sig[y] for x in w for y in w))
        return (sum(w[k] * mu[k] for k in w) - rf) / v
    sh0 = sharpe(base); W = pf['total_wealth']
    cash = pf['cash_available'] - sum(c_['shares'] * c_['price'] for c_ in committed.values())
    slots = (pf.get('max_positions') or 10) - len(set(held) | set(committed))
    for s, v in rows.items():
        r = res[s]; S = r['spot']; lim = [A.CEILING * W, cash]
        if S > r['stop_loss']: lim.append(A.STOP_BUDGET * W / ((S - r['stop_loss']) / S))
        ub = max(int(min(lim) // S), 0); best = (0, sh0)
        for k in range(1, ub + 1):
            if FEE > FEE_MAX * k * S: continue
            x = sharpe(dict(base, **{s: base.get(s, 0) + k * S}))
            if x > best[1] + 1e-9: best = (k, x)
        v.update(max_shares=ub, best_shares=best[0], score=round(best[1] - sh0, 4),
                 would_buy=bool(best[0] and not v['blocked'] and (v['held'] or s in committed or slots > 0)))
    out = dict(as_of=date, shadow_until=SHADOW_UNTIL, base_sharpe=round(sh0, 4), free_name_slots=slots, method=__doc__.split('\n\n')[2], names=rows)
    json.dump(out, open(os.path.join(HERE, 'runs', date, 'score.json'), 'w'), indent=1)
    for s, v in sorted(rows.items(), key=lambda kv: -kv[1]['score']):
        if v['score'] > 0 or v['rubric'] in ('medium', 'high'):
            print(f"{s:6} score {v['score']:+.4f} at {v['best_shares']} sh  mu_ens {v['mu_ens']:+.3f}  rubric {v['rubric']}  {'HELD' if v['held'] else ''} {'BLOCKED' if v['blocked'] else ''}")
    print(f"base Sharpe {sh0:.3f}; free name slots {slots}; {sum(v['score'] > 0 for v in rows.values())} of {len(rows)} names score > 0")


if __name__ == '__main__':
    import fscache; fscache.install()  # 2026-10-21: read-once cache for the shared network folder (fscache.py; same output)
    main(sys.argv[1])
