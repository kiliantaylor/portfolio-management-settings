"""Last check on a build before Price Targets is published (Kilian 2026-11-05: rules in scripts, not text). It re-checks the
page's due trades against the rules that broke recently, whatever code path produced them:

  - Reg T (11-04/05): no 'now' sell, trim or swap-out of a name with a lot bought on the session or the one before (regt.py).
  - Buy gates (11-02/04): no 'now' add, new-name buy or swap-in without a driver model, or on research dated before the last print.
  - Tape hold (11-05): no 'now' add, new-name buy or swap-in in a name on a tape-triggered news hold (tape_hold.py).
  - AI cap (11-05 cards '50% of stocks, buys only'; ai_cap.py): no 'now' buy, add or swap-in of an AI name when AI names
    would end above the cap of the money in stocks after the due trades (held at the page's spot, due buys and sells applied).
  - Official close (11-04 MOD 94.44 vs 98.56): each held name's spot within 1% of D's official close in data/daily_SYM.csv.
  - Shared drivers (11-05 dc_mor): every name in driver_models/shared/*.json on the shared values.

    python3 rules_check.py D      prints {"ok": bool, "violations": [...]}; exit 1 on any violation (do NOT publish)
nightly.py runs it after build_report and lists violations as errors; publish_check.py D refuses to publish while it fails."""
import csv, glob, json, os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
SPOT_TOL = 0.01


def _j(p, d=None):
    try: return json.load(open(p))
    except (OSError, ValueError): return d


def close_on(s, D):
    for d in (os.path.join(HERE, 'data'), os.path.join(HERE, '..', 'portfolio', 'data')):
        try:
            for r in csv.DictReader(open(os.path.join(d, f'daily_{s}.csv'))):
                if r['date'] == D: return float(r['close'])
        except (OSError, ValueError, KeyError): pass


def check(D):
    import book, regt, last_print, shared_drivers
    from staged_dates import next_session
    session = next_session(D)  # a nightly build's levels are for the next session
    pos = {p['symbol']: p for p in book.load().get('positions') or [] if p.get('shares')}
    rev = {f.split('/')[-2]: _j(f, {}) for f in glob.glob(os.path.join(HERE, 'runs', D, '*', 'review.json'))}
    res = lambda s: _j(os.path.join(HERE, 'runs', D, s, 'result.json'), {}) or {}
    V = []
    buys, sells = {}, {}
    for s, r in rev.items():
        m = (r.get('decider') or {}).get('marginal') or {}
        for x in m.get('trim') or []:
            if x.get('now'): sells[s] = sells.get(s, 0) + x.get('shares', 0)
        for x in m.get('add') or []:
            if x.get('now'): buys[s] = buys.get(s, 0) + x.get('shares', 0)
    P = _j(os.path.join(HERE, 'runs', D, 'portfolio.json'), {}) or {}
    for s, n in (P.get('shares') or {}).items():
        if n and s not in pos: buys[s] = buys.get(s, 0) + n
    sw = (_j(os.path.join(HERE, 'runs', D, 'swap_test.json'), {}) or {}).get('swap') or {}
    if sw.get('buy'): buys[sw['buy']] = buys.get(sw['buy'], 0) + (sw.get('buy_shares') or 0)
    if sw.get('sell'): sells[sw['sell']] = sells.get(sw['sell'], 0) + (sw.get('sell_shares') or 0)
    for s, n in sorted(sells.items()):
        if s in pos:
            u = regt.sellable_from(pos[s].get('lots'), session)
            if u: V.append(dict(rule='reg-t', symbol=s, shares=n, why=f'sell due on {session} but {regt.reason(u)}'))
    if buys:
        lp = last_print.last_all(D, sorted(buys))
        import tape_hold; tape = tape_hold.active(session)
        for s, n in sorted(buys.items()):
            ra = (rev.get(s) or {}).get('research_as_of') or res(s).get('research_as_of')
            if s in tape: V.append(dict(rule='tape-hold', symbol=s, shares=n, why=tape[s]['why']))
            if not last_print.driver_model(s, ra): V.append(dict(rule='no-buy-without-driver-model', symbol=s, shares=n, why=f'research {ra} has no driver model'))
            elif not lp.get(s): V.append(dict(rule='research-after-print', symbol=s, shares=n, why='last print unknown'))
            elif ra < lp[s]: V.append(dict(rule='research-after-print', symbol=s, shares=n, why=f'research {ra} is before the {lp[s]} print'))
    if buys:  # AI cap (Kilian 2026-11-05 cards): due AI buys may not leave AI names above the cap of the money in stocks
        import ai_cap
        AIC = ai_cap.load(); cap_ = float(AIC['cap']); syms_ = sorted(set(pos) | set(buys))
        mem = (ai_cap.read(D) or {}).get('members') or {}
        if any(s not in mem for s in syms_):
            try: import riskcal; RM_ = riskcal.load(D, syms_)
            except Exception: RM_ = None
            mem = dict(mem, **ai_cap.members(syms_, RM_, AIC))
        px_ = lambda s: (rev.get(s) or {}).get('spot') or res(s).get('spot') or 0
        aft = {s: (pos[s]['shares'] if s in pos else 0) * px_(s) for s in syms_}
        for s, n in buys.items(): aft[s] = aft.get(s, 0) + n * px_(s)
        for s, n in sells.items():
            if s in aft: aft[s] = max(aft[s] - n * px_(s), 0)
        A_, T_, sh_ = ai_cap.share(aft, mem)
        ai_buys = {s: n for s, n in buys.items() if s in mem and n}
        if ai_buys and sh_ is not None and sh_ > cap_ + 0.005:  # half a point of slack for rounding between spot sources
            for s, n in sorted(ai_buys.items()):
                V.append(dict(rule='ai-cap', symbol=s, shares=n, why=f'AI names {sh_:.1%} of stocks after the due trades, cap {cap_:.0%} ({mem[s]})'))
    for s in sorted(pos):
        sp, oc = (rev.get(s) or {}).get('spot') or res(s).get('spot'), close_on(s, D)
        if sp and oc and abs(sp / oc - 1) > SPOT_TOL:
            V.append(dict(rule='official-close', symbol=s, why=f'page spot {sp} vs official close {oc} on {D} ({sp / oc - 1:+.1%})'))
    for p in shared_drivers.check(D) if D >= min((x.get('as_of') or '') for x in shared_drivers.specs().values()) else []:
        V.append(dict(rule='shared-driver', symbol=p['symbol'], why=f"{p['driver']}: {p['problem']}"))
    return V


if __name__ == '__main__':
    if len(sys.argv) < 2: print(__doc__); sys.exit(1)
    v = check(sys.argv[1]); print(json.dumps(dict(ok=not v, violations=v), indent=1)); sys.exit(1 if v else 0)
