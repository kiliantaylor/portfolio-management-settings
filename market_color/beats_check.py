"""Daily Sharpe beat check (Kilian 2026-10-21 01:17Z "you should be checking this every day"; 01:31Z "check the sharpe let's get
rid of the rubric": Sharpe only, no MEDIUM bars).

python3 beats_check.py D     -> market_color/beats_check/D.json and a markdown summary on stdout

Question: does any researched but unheld name raise the page's invested-basis book Sharpe (portfolio.json sharpe_combo
base_sharpe, held names at cost, committed buys such as APP at spot, cash left out) by more than the planned held-name adds
do? The name cap (8 since 2026-11-08; 9 held) is full, so a beating name is also scored as a replacement (it takes the dollars of the held name whose
removal helps the Sharpe most, plus the planned adds' dollars).
No rubric: the conviction label, dP, upside, R/R and FV bars are not used. Eligible = gate-clean research (combine.citation_gate)
and the non-conviction buy gates (target above last, excluded, saved for later, staged wait, limit order, big-miss block).
Method (same inputs as allocate.py's Sharpe test, same fast_sharpe): this file runs allocate.py's own code up to its Sharpe
test (text-patched so the position-limit drop loop is skipped and nothing is written), then the probe below. Since 2026-11-09
(Kilian "get rid of conviction rubric") allocate.py has no rubric step, no book dP and no conviction gate, so nothing about
conviction is patched; research weight (conviction.py, via score.py and research_inputs.py) is already inside mu_eff.
  planned adds = held names whose review target_shares (runs/D/review_summary.json sizing) exceed shares held, rounded down,
                 minus adds that cannot be bought today (staged until a later 'after' date; expected-miss/big-miss holds via env).
  budget       = the planned adds' total dollars (at most the cash left after committed buys).
A candidate buys 1..max whole shares (stop budget, 15% name cap, budget) and keeps the count with the best Sharpe.
A beat is a screen on stored numbers; it goes to Kilian as a pick for a full run (a model update if the name has a driver model), never a buy."""
import json, os, subprocess, sys
HERE = os.path.dirname(os.path.abspath(__file__)).replace('/mnt/attach/project-files', '/mnt/project-files')
VAL = os.path.join(os.path.dirname(HERE), 'valuation')

PROBE = r'''
    # ---- beats_check probe: Sharpe only, no writes ----
    import combine as _cmb
    _out = os.environ['BEATS_OUT']
    G2 = tuple(g for g in GATES if g != 'conviction_below_min')  # that gate left allocate.py with the rubric (2026-11-09); kept tolerant
    def _researched(x):
        return any(_cmb.citation_gate(f.split('/')[-3], x) for f in glob.glob(os.path.join(HERE, 'runs', '*', x, 'supervisor.json')))
    el2 = [x for x in syms if x not in w_h and x != IDX.get('symbol') and not any(g in caps[x] for g in G2)]
    gated = sorted(x for x in syms if x not in w_h and x not in el2 and x != IDX.get('symbol'))
    el2 = [x for x in el2 if _researched(x)]
    ub2 = {x: int(min(v for k, v in caps[x].items() if k in ('stop_budget', 'ceiling', 'supervisor')) * W // res[x]['spot']) for x in el2}
    ix.update({x: names.index(x) for x in el2})
    base = fast_sharpe({})
    rs = json.load(open(os.path.join(HERE, 'runs', date, 'review_summary.json')))['sizing']['target_shares']
    plan = {x: int(rs[x] - held[x]) for x in held if x in rs and rs[x] - held[x] >= 1 and x in res}
    # live/swaps.py (Kilian 2026-10-30 20:00Z): BEATS_ADD_BLOCKS names held names whose adds cannot be bought today (expected-miss
    # or big-miss hold), so the planned adds a swap is compared with are only the ones allowed now
    plan = {x: n for x, n in plan.items() if x not in os.environ.get('BEATS_ADD_BLOCKS', '').split(',')}
    # 2026-11-02 (Kilian: stale tranche plan in the Sharpe test): a held name's add staged for after its print (review_settings
    # staged_entries 'after', e.g. NVDA Nov 20) cannot be bought today either, so it is not in the baseline adds until that date
    import datetime as _dt
    try:
        from zoneinfo import ZoneInfo as _ZI; _today = _dt.datetime.now(_ZI('America/New_York')).date().isoformat()
    except Exception: _today = _dt.date.today().isoformat()
    _stg = {x for x, e in (cfg.get('staged_entries') or {}).items() if isinstance(e, dict) and max(date, _today) < (e.get('after') or '')}
    plan = {x: n for x, n in plan.items() if x not in _stg}
    def _with(extra, drop=None):
        h0 = dict(hdol)
        for x, n in extra.items(): hdol[x] = hdol.get(x, 0) + n * res[x]['spot']
        if drop: hdol[drop] = 0.0
        s = fast_sharpe({}); hdol.clear(); hdol.update(h0); return s
    bud_all = min(sum(n * res[x]['spot'] for x, n in plan.items()), cash_c)
    s_all = _with(plan)
    s_each = {x: _with({x: n}) for x, n in plan.items()}
    dol_each = {x: n * res[x]['spot'] for x, n in plan.items()}
    def best_for(x, bud, drop=None):
        nmax = min(ub2[x], int(bud // res[x]['spot']))
        if nmax < 1: return None
        h0 = dict(hdol)
        if drop: hdol[drop] = 0.0
        b = max((fast_sharpe({x: n}), n) for n in range(1, nmax + 1)); hdol.clear(); hdol.update(h0); return b
    # replacement: the held name whose removal raises the base Sharpe most (None if no removal helps)
    rem = {x: _with({}, drop=x) for x in held}
    out_x = max(rem, key=rem.get); swap_out = out_x if rem[out_x] > base + 1e-9 else min(held, key=lambda x: hdol[x])
    swap_dol = hdol[swap_out]
    # live/swaps.py (Kilian 2026-10-30 19:46-19:55Z): BEATS_SWAP_POOL lists the held names a candidate may replace. Each is tried
    # against its full planned position: sell it and drop its planned add, put both dollars into the candidate, keep every other
    # planned add; the best swap is kept per candidate and compared with doing all the planned adds
    _pool = [h for h in os.environ.get('BEATS_SWAP_POOL', '').split(',') if h in held]
    def swap_full(x, h):
        bud = hdol[h] + plan.get(h, 0) * res[h]['spot']
        nmax = min(ub2[x], int(bud // res[x]['spot']))
        if nmax < 1: return None
        h0 = dict(hdol)
        for k, n in plan.items():
            if k != h: hdol[k] = hdol.get(k, 0) + n * res[k]['spot']
        hdol[h] = 0.0
        b = max((fast_sharpe({x: n}), n) for n in range(1, nmax + 1)); hdol.clear(); hdol.update(h0); return b
    rows = []
    for x in el2:
        b = best_for(x, bud_all)
        if not b: rows.append(dict(sym=x, note='budget below one share', spot=res[x]['spot'])); continue
        if _pool: sw, so = max(((swap_full(x, h), h) for h in _pool), key=lambda t: t[0][0] if t[0] else -9)
        else: sw, so = best_for(x, bud_all + swap_dol, drop=swap_out), swap_out
        beats_each = [h for h in plan if (lambda bb: bb is not None and bb[0] - base > s_each[h] - base)(best_for(x, dol_each[h]))]
        rows.append(dict(sym=x, spot=res[x]['spot'], shares=b[1], dollars=round(b[1] * res[x]['spot'], 2), sharpe=round(b[0], 4), delta=round(b[0] - base, 4),
                         beats_all_planned=bool(b[0] > s_all + 1e-9), beats_individual_adds=beats_each,
                         swap_out=so if sw else None, swap_shares=sw[1] if sw else None, swap_sharpe=round(sw[0], 4) if sw else None,
                         swap_delta=round(sw[0] - base, 4) if sw else None))
    rows.sort(key=lambda r: -(r.get('sharpe') or -9))
    json.dump(dict(as_of=date, base_sharpe=round(base, 4), committed={x: dict(shares=n, price=px) for x, (n, px) in committed.items()},
                   planned_adds=plan, planned_budget=round(bud_all, 2), sharpe_all_planned=round(s_all, 4),
                   sharpe_each_planned={x: round(v, 4) for x, v in s_each.items()}, swap_out=swap_out, swap_out_dollars=round(swap_dol, 2),
                   candidates=rows, gated_out=gated, not_researched_skipped=len([x for x in syms if x not in w_h and x not in el2 and x not in gated and x != IDX.get('symbol')])),
              open(_out, 'w'), indent=1)
    sys.exit(0)
'''

def main(d, syms=(), out=None):
    src = open(os.path.join(VAL, 'allocate.py')).read()
    marker = "    # brokerage fee (Kilian 2026-10-02)"
    need = [marker, "GATES = (", "def fast_sharpe(cnt):", "cash_c = ", "    while MAX_POS:"]  # the rubric block is gone (2026-11-09)
    miss = [m for m in need if m not in src]
    if miss: sys.exit(f"beats_check: allocate.py changed, cannot patch (missing {miss}); tell the Project improvements thread")
    if 'import rubric' in src or 'import book_dp' in src: sys.exit("beats_check: allocate.py imports rubric/book_dp again; they were retired 2026-11-09")
    src = src.replace("    while MAX_POS:", "    while False:  # position-limit drop loop skipped: it only sizes buys, and with the rubric gate off it re-solves once per candidate per drop (48 min on 10-20)", 1)
    src = src.replace(marker, PROBE + marker, 1)
    out = out or os.path.join(HERE, 'beats_check', d + '.json'); os.makedirs(os.path.dirname(out), exist_ok=True)
    env = dict(os.environ, BEATS_OUT=out)
    r = subprocess.run([sys.executable, '-I', '-c', f"import sys; sys.path.insert(0, {VAL!r}); __file__ = {os.path.join(VAL, 'allocate.py')!r}; sys.argv = ['allocate.py', {d!r}, *{list(syms)!r}]; exec(compile({src!r}, __file__, 'exec'), {{'__name__': '__main__', '__file__': __file__}})"],
                       cwd=VAL, env=env, capture_output=True, text=True)
    if r.returncode or not os.path.exists(out): sys.exit('beats_check failed:\n' + r.stdout[-1500:] + r.stderr[-2500:])
    t = json.load(open(out))
    print(f"Book Sharpe base {t['base_sharpe']:.4f} (page figure). Planned held-name adds {t['planned_adds']} (${t['planned_budget']:,.0f}) -> {t['sharpe_all_planned']:.4f}; each alone {t['sharpe_each_planned']}")
    print(f"Replacement slot: {t['swap_out']} (${t['swap_out_dollars']:,.0f} at cost). Names gated out for non-conviction reasons: {t['gated_out']}")
    beat = [c for c in t['candidates'] if c.get('beats_all_planned')]
    print('BEATS the planned adds:', ', '.join(c['sym'] for c in beat) if beat else 'none')
    for c in t['candidates'][:12]:
        if 'sharpe' in c: print(f"  {c['sym']:6} {c['shares']:3d} sh ${c['dollars']:>7,.0f} -> {c['sharpe']:.4f} ({c['delta']:+.4f})  beats-all {c['beats_all_planned']}  beats adds {c['beats_individual_adds']}  swap-for-{c.get('swap_out')} {c['swap_shares']} sh -> {c['swap_sharpe']} ({c['swap_delta']})")

if __name__ == '__main__':
    main(sys.argv[1])
