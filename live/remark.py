"""Intraday market-leg re-mark for held names (Kilian 2026-10-29 15:38Z): when a held name has moved past its implied
move since its last market leg, pull its Cboe option chain again and re-mark that name only.

    python3 live/remark.py SLICE OUT_DIR

Runs inside live/run.py after live_quotes.py and before review.py. For each held name (book.py):
  reference = the newest market leg: OUT_DIR/remark/<SYM>.json when an earlier run re-marked it this session, else the
              slice's priors/D/<SYM>.json (the nightly's, or one a research thread placed there);
  implied move = the 30-day ATM implied vol x sqrt(sessions since the reference / 252), one standard deviation;
  trigger   = |ln(live price / reference spot)| above the implied move, and a chain quoted in the live price's session.
Swap candidates (Kilian 2026-10-30 19:17Z; 19:40Z "only names audited and up-to-date research"): an unheld name whose
research passed the citation audit ('clean', not a pre-gate round) and is dated on or after its last earnings report, whose
live price is at or below its Entry below at the last close, (target + a year of dividends) / (1 + rf + beta premium), is
re-marked the same way on every run, with no move trigger; live/swaps.py then scores it.
A triggered name gets market_dist.py's 12-month and 1-month distributions from a fresh chain (kept in OUT_DIR/remark,
which the workflow pushes to live-out, so later held-name runs measure from it). Every name holding a market leg from the live
session then has it written over the slice's priors/D (and its 30-day ATM vol into the slice's newest IV file), and
combine.py re-marks it: market leg, page target, take-profit and stop. forecast_log.csv is never written here; the
nightly re-mark logs the day's forecasts. The joint steps (conviction scaling is recomputed by review.py itself;
allocate.py and the book Sharpe) stay at the nightly. Prints one JSON line."""
import datetime as dt, glob, json, math, os, shutil, sys


def sessions(a, b):  # weekdays after date a up to and including date b, at least 1
    a, b = dt.date.fromisoformat(a), dt.date.fromisoformat(b)
    return max(1, sum((a + dt.timedelta(d)).weekday() < 5 for d in range(1, (b - a).days + 1)))


if __name__ == '__main__':
    SLICE, OUT = (os.path.abspath(a) for a in sys.argv[1:3])
    V = f'{SLICE}/valuation'; sys.path.insert(0, V); os.chdir(V)
    import book, combine, divs, market_dist
    combine._LOG_OPS = {}  # collect forecast_log.csv rows and drop them
    D = json.load(open(f'{SLICE}/slice.json'))['date']
    quotes = json.load(open(f'{V}/data/live_quotes.json')).get('quotes') or {}
    held = sorted(p['symbol'] for p in book.load()['positions'] if p.get('shares'))
    R = f'{OUT}/remark'; os.makedirs(R, exist_ok=True)
    session = max([q.get('date') or '' for q in quotes.values()] + [''])
    for f in glob.glob(f'{R}/*.json'):  # a market leg from an earlier session is the nightly's job now
        if json.load(open(f)).get('market_date') != session: os.remove(f)
    pulled, checked, failed, cands = [], {}, {}, {}
    EM = json.load(open(f'{V}/data/earnings_miss.json'))  # last_report_date per name: research must postdate the last print
    for f in sorted(glob.glob(f'{V}/runs/{D}/*/result.json')):  # swap candidates: unheld, gate-clean, at or below Entry below
        s = f.split('/')[-2]; q = quotes.get(s) or {}; P = q.get('price')
        if s in held or not P or (q.get('date') or '') <= D or not os.path.exists(f'{V}/priors/{D}/{s}.json'): continue
        r, pr = json.load(open(f)), json.load(open(f'{V}/priors/{D}/{s}.json'))
        last = (EM.get(s) or {}).get('last_report_date') if isinstance(EM.get(s), dict) else None
        if (r.get('citation_gate') or {}).get('status') != 'clean' or not last or (r.get('research_as_of') or '') < last: continue
        E = (r['target'] + divs.dps(s)) / (1 + pr['rf'] + pr['premium'])
        if P <= E: cands[s] = dict(price=P, entry_below_close=round(E, 2), target_close=r['target'], research=r.get('research_as_of'),
                                   last_report=last, remarked=False)
    for s in held + sorted(cands):
        q = quotes.get(s) or {}; P = q.get('price')
        if not P or (q.get('date') or '') <= D: continue
        mine = f'{R}/{s}.json'
        if s in cands: os.path.exists(mine) and os.remove(mine)  # a candidate is pulled fresh on every run
        ref = json.load(open(mine if os.path.exists(mine) else f'{V}/priors/{D}/{s}.json'))
        v30 = (json.load(open(f'{R}/{s}_1m.json'))['atm_iv_1y'] if os.path.exists(mine) else None) \
            or combine.iv30(s, D)[0] or ref['atm_iv_1y']
        n = sessions(ref['market_date'], q['date']) if ref['market_date'] < q['date'] else 1
        move, bar = math.log(P / ref['spot']), v30 * math.sqrt(n / 252)
        checked[s] = dict(price=P, ref_spot=ref['spot'], ref_date=ref['market_date'], move=round(move, 4), implied_move=round(bar, 4))
        if abs(move) <= bar and s not in cands: continue
        try:
            m12 = market_dist.market_dist(s)
            m1 = market_dist.market_dist(s, None, 30)
        except (Exception, SystemExit) as e:
            failed[s] = str(e)[:200]; continue
        if m12['market_date'] != q['date']:
            failed[s] = f"chain quoted {m12['market_date']}, not {q['date']}"; continue
        for m in (m12, m1): m['intraday'] = dict(pulled_utc=dt.datetime.now(dt.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'), trigger=checked[s])
        json.dump(m12, open(mine, 'w'), indent=1); json.dump(m1, open(f'{R}/{s}_1m.json', 'w'), indent=1)
        pulled.append(s)
    # every market leg from this session goes into the slice, then combine re-marks those names
    legs = sorted(os.path.basename(f)[:-5] for f in glob.glob(f'{R}/*.json') if not f.endswith('_1m.json'))
    legs = [s for s in legs if s in held and json.load(open(f'{R}/{s}.json'))['market_date'] > json.load(open(f'{V}/priors/{D}/{s}.json'))['market_date']] \
        + [s for s in pulled if s in cands]  # a candidate's leg is this run's own
    ivf = sorted(f for f in glob.glob(f'{SLICE}/portfolio/data/iv/*.json') if os.path.basename(f)[:-5] <= D)
    iv = json.load(open(ivf[-1])) if ivf else None
    remarked = {}
    for s in legs:
        shutil.copyfile(f'{R}/{s}.json', f'{V}/priors/{D}/{s}.json')
        os.makedirs(f'{V}/priors/{D}/1m', exist_ok=True); shutil.copyfile(f'{R}/{s}_1m.json', f'{V}/priors/{D}/1m/{s}.json')
        if iv is not None:  # combine.iv30 reads the newest IV file on or before D: the stop's noise floor uses today's vol
            iv['symbols'].setdefault(s, {})['atm30'] = json.load(open(f'{R}/{s}_1m.json'))['atm_iv_1y']
    if iv is not None and legs: json.dump(iv, open(ivf[-1], 'w'))
    combine._MEMO.clear()  # the IV file was read before it was patched
    for s in legs:
        try:
            r = combine.combine(D, s)
            remarked[s] = dict(spot=r['spot'], target=r['target'], take_profit=r['take_profit'], stop_loss=r['stop_loss'],
                               market_target=r['market_only']['target'], as_of=r['as_of'])
            if s in cands: cands[s]['remarked'] = True
        except (Exception, SystemExit) as e:
            failed[s] = f'combine: {str(e)[:200]}'
    print(json.dumps(dict(session=session, checked=checked, pulled=pulled, remarked=remarked, failed=failed, candidates=cands)))
