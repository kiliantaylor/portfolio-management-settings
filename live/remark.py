"""Intraday market-leg re-mark for every researched name (Kilian 2026-11-05 18:35Z "change to all researched names", after
"they are supposed to update throughout the session"; before that, 2026-10-29, held names only, when they moved past their
implied move).

    python3 live/remark.py SLICE OUT_DIR

Runs inside live/run.py after live_quotes.py and before review.py. Every run pulls a fresh Cboe option chain for each
researched name: every held name (book.py) and every unheld name in runs/D whose research round has a driver model
(last_print.driver_model; Kilian 2026-11-04: no chain retries for names without one). No move trigger: the implied move
since the name's last market leg (30-day ATM vol x sqrt(sessions since that leg / 252)) is still reported in 'checked'.
One pull per name feeds both the 12-month and the 1-month distribution (a per-run chain folder), pulls are paced, and a
429 is retried once, so ~26 names stay under Cboe's limit of about 40 quick pulls from one IP.
Session rule (2026-11-05, "why are you remarking on yesterday's close, market is already open"): a chain counts only when it
is quoted in the live price's session; before the open (pre-market quotes) or on a stale chain nothing is re-marked from it,
and a name keeps the leg an earlier run pulled this session, if any.
Swap candidates (Kilian 2026-10-30 19:17Z; 19:40Z "only names audited and up-to-date research"): an unheld name whose
research passed the citation audit ('clean', not a pre-gate round), has a driver model, and is dated on or after its last
earnings report (valuation/last_print.py), whose live price is at or below its Entry below at the last close,
(target + a year of dividends) / (1 + rf + beta premium); live/swaps.py scores it, and only on a leg pulled in this run.
Each pulled leg (12-month and 1-month market_dist.py distributions) is kept in OUT_DIR/remark, which the workflow pushes to
live-out and the next session drops. Every name holding a market leg from the live session then has it written over the
slice's priors/D (and its 30-day ATM vol into the slice's newest IV file), and combine.py re-marks it: market leg, page
target, take-profit and stop (the research target (12m) does not move). forecast_log.csv is never written here; the
nightly re-mark logs the day's forecasts and writes the official priors on the official close. The joint steps
(conviction scaling is recomputed by review.py itself; allocate.py and the book Sharpe) stay at the nightly.
Research pushed intraday (Kilian 2026-11-05; live/nightly.sh research SYM, slice.json 'research' {SYM: RDATE}): a model
update finished after the slice date is re-marked on every run with combine.py's research=RDATE, on the session's market
leg if one was pulled, else on the slice's priors/D; the candidate test above then sees its new target.
Prints one JSON line."""
import datetime as dt, glob, json, math, os, shutil, sys, tempfile, time, urllib.error


def sessions(a, b):  # weekdays after date a up to and including date b, at least 1
    a, b = dt.date.fromisoformat(a), dt.date.fromisoformat(b)
    return max(1, sum((a + dt.timedelta(d)).weekday() < 5 for d in range(1, (b - a).days + 1)))


def pull(s, chains):  # one Cboe pull per name per run, shared by the 12-month and 1-month legs; paced, one retry on 429
    for wait in (5, None):
        try:
            time.sleep(0.3)
            return market_dist.market_dist(s, chains), market_dist.market_dist(s, chains, 30)
        except urllib.error.HTTPError as e:
            if e.code != 429 or wait is None: raise
            time.sleep(wait)


if __name__ == '__main__':
    SLICE, OUT = (os.path.abspath(a) for a in sys.argv[1:3])
    V = f'{SLICE}/valuation'; sys.path.insert(0, V); os.chdir(V)
    import book, combine, divs, last_print, market_dist
    combine._LOG_OPS = {}  # collect forecast_log.csv rows and drop them
    META = json.load(open(f'{SLICE}/slice.json')); D = META['date']
    PUSHED = {k: v for k, v in (META.get('research') or {}).items() if v > D}  # intraday research rounds (live/nightly.sh research)
    quotes = json.load(open(f'{V}/data/live_quotes.json')).get('quotes') or {}
    held = sorted(p['symbol'] for p in book.load()['positions'] if p.get('shares'))
    R = f'{OUT}/remark'; os.makedirs(R, exist_ok=True)
    session = max([q.get('date') or '' for q in quotes.values()] + [''])
    for f in glob.glob(f'{R}/*.json'):  # a market leg from an earlier session is the nightly's job now
        if json.load(open(f)).get('market_date') != session: os.remove(f)
    pulled, checked, failed, cands, rdm = [], {}, {}, {}, []
    for s in sorted(PUSHED):  # the new round first, so the Entry below and candidate tests read its target
        try: combine.combine(D, s, PUSHED[s])
        except (Exception, SystemExit) as e: failed[s] = f'combine on research {PUSHED[s]}: {str(e)[:200]}'
    EM = json.load(open(f'{V}/data/earnings_miss.json'))  # last_report_date per name: research must postdate the last print
    for f in sorted(glob.glob(f'{V}/runs/{D}/*/result.json')):  # swap candidates: unheld, gate-clean, at or below Entry below
        s = f.split('/')[-2]; q = quotes.get(s) or {}; P = q.get('price')
        if s in held or not P or (q.get('date') or '') <= D or not os.path.exists(f'{V}/priors/{D}/{s}.json'): continue
        r, pr = json.load(open(f)), json.load(open(f'{V}/priors/{D}/{s}.json'))
        if not last_print.driver_model(s, r.get('research_as_of')): continue  # not a researched name: no intraday chain
        rdm.append(s)
        last = last_print.last(D, s, EM)  # 2026-11-04: earnings_miss last_report_date alone was stale for unheld names (MTRN)
        if (r.get('citation_gate') or {}).get('status') != 'clean' or not last or (r.get('research_as_of') or '') < last: continue
        E = (r['target'] + divs.dps(s)) / (1 + pr['rf'] + pr['premium'])
        if P <= E: cands[s] = dict(price=P, entry_below_close=round(E, 2), target_close=r['target'], research=r.get('research_as_of'),
                                   last_report=last, remarked=False)
    scope = held + sorted(s for s in rdm if s not in held)  # every researched name, every run
    chains = tempfile.mkdtemp(prefix='chains-')
    for s in scope:
        q = quotes.get(s) or {}; P = q.get('price')
        if not P or (q.get('date') or '') <= D: continue
        mine = f'{R}/{s}.json'
        if s in cands: os.path.exists(mine) and os.remove(mine)  # a candidate is pulled fresh on every run
        if not os.path.exists(mine) and not os.path.exists(f'{V}/priors/{D}/{s}.json'): continue
        ref = json.load(open(mine if os.path.exists(mine) else f'{V}/priors/{D}/{s}.json'))
        v30 = (json.load(open(f'{R}/{s}_1m.json'))['atm_iv_1y'] if os.path.exists(mine) else None) \
            or combine.iv30(s, D)[0] or ref['atm_iv_1y']
        n = sessions(ref['market_date'], q['date']) if ref['market_date'] < q['date'] else 1
        move, bar = math.log(P / ref['spot']), v30 * math.sqrt(n / 252)
        checked[s] = dict(price=P, ref_spot=ref['spot'], ref_date=ref['market_date'], move=round(move, 4), implied_move=round(bar, 4))
        try:
            m12, m1 = pull(s, chains)
        except (Exception, SystemExit) as e:
            failed[s] = str(e)[:200]; continue
        if m12['market_date'] != q['date']:
            failed[s] = f"chain quoted {m12['market_date']}, not {q['date']}"; continue
        for m in (m12, m1): m['intraday'] = dict(pulled_utc=dt.datetime.now(dt.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'), move=checked[s])
        json.dump(m12, open(mine, 'w'), indent=1); json.dump(m1, open(f'{R}/{s}_1m.json', 'w'), indent=1)
        pulled.append(s)
    # every market leg from this session goes into the slice, then combine re-marks those names
    legs = sorted(os.path.basename(f)[:-5] for f in glob.glob(f'{R}/*.json') if not f.endswith('_1m.json'))
    legs = [s for s in legs if s not in cands and os.path.exists(f'{V}/priors/{D}/{s}.json') and json.load(open(f'{R}/{s}.json'))['market_date'] > json.load(open(f'{V}/priors/{D}/{s}.json'))['market_date']] \
        + [s for s in pulled if s in cands]  # a candidate's leg is this run's own (its file was removed before the pull)
    again = [s for s in sorted(PUSHED) if s not in legs and s not in failed]  # pushed research on the slice's own leg: combined above, re-run after the IV patch
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
    for s in legs + again:
        try:
            r = combine.combine(D, s, PUSHED.get(s))
            remarked[s] = dict(spot=r['spot'], target=r['target'], take_profit=r['take_profit'], stop_loss=r['stop_loss'],
                               market_target=r['market_only']['target'], as_of=r['as_of'],
                               **({'research': PUSHED[s]} if s in PUSHED else {}))
            if s in cands: cands[s]['remarked'] = True
        except (Exception, SystemExit) as e:
            failed[s] = f'combine: {str(e)[:200]}'
    print(json.dumps(dict(session=session, checked=checked, pulled=pulled, remarked=remarked, failed=failed, candidates=cands, research=PUSHED)))
