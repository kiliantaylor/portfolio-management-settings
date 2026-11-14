"""Whole-book swap test, shared by the nightly and the live job (Kilian 2026-11-02: "why the swap doesn't just go there",
the trim/hold/add table). Same rules at the close (nightly, step 3b2s, right after review.py) and at live prices
(live/swaps.py, every live run), so both give the same answer on the same prices.

    python3 swap_test.py D [--out PATH]      -> runs/D/swap_test.json (the page's DATA.swap_test)

Candidates: unheld names with citation-audited research (result.json citation_gate 'clean') dated on or after their last
earnings report (last_print.py: earnings_miss.json dates, a passed next-report date, SEC 8-K 2.02), priced at or below their Entry below,
(target + a year of dividends) / (1 + rf + beta premium). Nightly: the close and the close mark. Live: the live price
and the mark live/remark.py re-made on a fresh option chain.
Score: market_color/beats_check.py, the nightly whole-book Sharpe test (allocate.py's own code up to its Sharpe test; the
name cap, 15% name cap and stop budget bound the shares). Each holding is tried against its full planned position: sell
it and drop its planned add, put both dollars into the candidate, keep every other planned add; the best is kept.
SWAP_OUT_HOLDS keeps a holding out of the pool until the day after its next print. Planned adds on an expected-miss or
big-miss hold, or staged for a later date, are left out of the comparison (they cannot be bought today).
Winner: with the 10-name cap full, the swap's book Sharpe beats doing the planned adds instead by at least MIN_EDGE; with a slot free, the
plain buy (the adds' dollars, nothing sold) does. The best winner goes into the next-share table as the trade to make
(report_template.html nextShareTable); trades happen in regular market hours only."""
import contextlib, json, os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
# Kilian 2026-10-30 19:55Z: "our thesis on nflx has not played out yet in one direction or another so i'd like to wait and see on
# that before liquidating the position (especially for a loss)". Held out until the day after its next print (data/earnings_miss.json
# next_report; while that date is unknown the hold stands). Remove the entry to lift it. Not a ban: it lifts on its own.
# 2026-11-02 (Kilian card 'Require +0.01 book Sharpe'): a swap sells a whole position, so it must beat
# doing the planned adds by at least this much book Sharpe; set 0 for any edge
MIN_EDGE = 0.01
SWAP_OUT_HOLDS = {'NFLX': "thesis not played out yet; Kilian waits for its next print"}


def _j(p):
    return json.load(open(p))


def entry_below(D, s, r=None):
    import divs
    r = r or _j(f'{HERE}/runs/{D}/{s}/result.json'); pr = _j(f'{HERE}/priors/{D}/{s}.json')
    return (r['target'] + divs.dps(s)) / (1 + pr['rf'] + pr['premium'])


def eligible(D, s, r, em, held):
    """Unheld, citation-audited research dated on or after the last print (Kilian 2026-10-30 19:40Z)."""
    if s in held or not os.path.exists(f'{HERE}/priors/{D}/{s}.json'): return None
    import last_print; last = last_print.last(D, s, em)  # 2026-11-04: earnings_miss last_report_date alone was stale for unheld names (MTRN)
    if (r.get('citation_gate') or {}).get('status') != 'clean' or not last or (r.get('research_as_of') or '') < last: return None
    if not last_print.driver_model(s, r.get('research_as_of')): return None  # Kilian 2026-11-04: no swap-in without a driver model
    import tape_hold
    if s in tape_hold.active(D): return None  # Kilian 2026-11-05: no swap-in while a tape-triggered news hold is open on the name
    return last


def held_names():
    import book
    return sorted(p['symbol'] for p in book.load()['positions'] if p.get('shares'))


def close_candidates(D):
    """Nightly: the same filter live/remark.py uses, on the close and the close mark (already fresh, so remarked=True)."""
    import glob
    held, em, cands = set(held_names()), _j(f'{HERE}/data/earnings_miss.json'), {}
    for f in sorted(glob.glob(f'{HERE}/runs/{D}/*/result.json')):
        s = f.split('/')[-2]
        try: r = _j(f)
        except Exception: continue
        last = eligible(D, s, r, em, held)
        if not last or not r.get('spot') or not r.get('target'): continue
        E = entry_below(D, s, r)
        if r['spot'] <= E: cands[s] = dict(price=r['spot'], entry_below_close=round(E, 2), target_close=r['target'],
                                           research=r.get('research_as_of'), last_report=last, remarked=True)
    return cands


def run(D, cands, quotes=None, session=None):
    """cands: {sym: {price, entry_below_close, target_close, research, last_report, remarked}} (live/remark.py or
    close_candidates). quotes: live quotes {sym: {price}} or None at the close. session: the trading date the trades are for."""
    quotes, session = quotes or {}, session or D
    held = held_names()
    import last_print  # 2026-11-04: the same gates on every caller (the live job's own filter in live/remark.py may lag)
    em0 = _j(f'{HERE}/data/earnings_miss.json')
    def _ok(s):
        r = _j(f'{HERE}/runs/{D}/{s}/result.json'); lp = last_print.last(D, s, em0)
        return last_print.driver_model(s, r.get('research_as_of')) and lp and (r.get('research_as_of') or '') >= lp
    import tape_hold; TAPE = tape_hold.active(session)  # Kilian 2026-11-05: no swap-in during a tape-triggered news hold
    cands = {s: c for s, c in cands.items() if _ok(s) and s not in TAPE}
    cap = _j(f'{HERE}/data/review_settings.json').get('max_positions')
    rows, passed = {}, []
    for s, c in sorted(cands.items()):
        r = _j(f'{HERE}/runs/{D}/{s}/result.json')
        P = (quotes.get(s) or {}).get('price') or c['price']
        E = entry_below(D, s, r)
        rows[s] = dict(c, price=P, spot=r['spot'], target=r['target'], entry_below=round(E, 2), floor_ok=P <= E)
        if P <= E and rows[s]['remarked']: passed.append(s)
    out = dict(as_of=D, session=session, slot_free=bool(cap) and len(held) < cap, checked=rows, winners=[], swap=None)
    if not passed: return out
    sys.path.insert(0, os.path.join(os.path.dirname(HERE), 'market_color'))
    import beats_check, earnings_miss, expected_miss
    em = _j(f'{HERE}/data/earnings_miss.json')
    def hold_on(h):
        nx = ((em.get(h) or {}).get('next_report') or {}).get('date')
        return h in SWAP_OUT_HOLDS and not (nx and session > nx)
    out['swap_holds'] = {h: SWAP_OUT_HOLDS[h] for h in held if hold_on(h)}
    # Kilian 2026-11-04/05 Reg T: a name with a lot bought this session or the one before can't be sold (swapped out)
    # until its cash settles (regt.py); a nightly run (no quotes) checks the next session
    import book, regt
    from staged_dates import next_session
    chk = session if quotes else next_session(D)
    for p_ in book.load()['positions']:
        u = regt.sellable_from(p_.get('lots'), chk) if p_['symbol'] in held else None
        if u: out['swap_holds'][p_['symbol']] = regt.reason(u)
    out['add_blocks'] = sorted((set(expected_miss.blocks(session)) | set(earnings_miss.blocks(session)) | set(TAPE)) & set(held))
    f = f'/tmp/swap_test_{os.getpid()}.json'
    env0 = {k: os.environ.get(k) for k in ('BEATS_SWAP_POOL', 'BEATS_ADD_BLOCKS')}
    os.environ['BEATS_SWAP_POOL'] = ','.join(h for h in held if h not in out['swap_holds'])
    os.environ['BEATS_ADD_BLOCKS'] = ','.join(out['add_blocks'])
    try:
        with contextlib.redirect_stdout(sys.stderr): beats_check.main(D, passed, f)  # its summary lines go to the log
    finally:
        for k, v in env0.items():
            if v is None: os.environ.pop(k, None)
            else: os.environ[k] = v
    t = _j(f); os.remove(f)
    out.update({k: t[k] for k in ('base_sharpe', 'planned_adds', 'planned_budget', 'sharpe_all_planned')}, min_edge=MIN_EDGE)
    for c in t['candidates']:
        x = rows[c['sym']]
        x.update({k: c.get(k) for k in ('shares', 'sharpe', 'swap_out', 'swap_shares', 'swap_sharpe', 'beats_all_planned')}, scored_spot=c.get('spot'), note=c.get('note'))
        x['wins'] = ((c.get('sharpe') if out['slot_free'] else c.get('swap_sharpe')) or -9) > t['sharpe_all_planned'] + max(MIN_EDGE, 1e-9)
    for s in t['gated_out']:
        if s in rows: rows[s]['note'] = 'held back by a buy gate'
    key = (lambda s: -(rows[s].get('sharpe') or 0)) if out['slot_free'] else (lambda s: -(rows[s].get('swap_sharpe') or 0))
    out['winners'] = sorted((s for s, x in rows.items() if x.get('wins')), key=key)
    if out['winners']:  # the one trade the next-share table carries: the best winner (one swap at a time)
        s = out['winners'][0]; x = rows[s]
        if out['slot_free']:
            out['swap'] = dict(buy=s, buy_shares=x['shares'], price=x['price'], sharpe=x['sharpe'])
        else:
            h = x['swap_out']; hs = next((p['shares'] for p in __import__('book').load()['positions'] if p['symbol'] == h), None)
            out['swap'] = dict(buy=s, buy_shares=x['swap_shares'], price=x['price'], sell=h, sell_shares=hs,
                               skip_add=(out['planned_adds'] or {}).get(h), sharpe=x['swap_sharpe'])
        out['swap']['vs_planned_adds'] = out['sharpe_all_planned']
    return out


if __name__ == '__main__':
    os.chdir(HERE); sys.path.insert(0, HERE)
    D = sys.argv[1]
    o = sys.argv[sys.argv.index('--out') + 1] if '--out' in sys.argv else f'{HERE}/runs/{D}/swap_test.json'
    res = run(D, close_candidates(D))
    json.dump(res, open(o, 'w'), indent=1)
    sw = res.get('swap')
    print(json.dumps(dict(out=o, candidates=sorted(res['checked']), winners=res['winners'], swap=sw)))
