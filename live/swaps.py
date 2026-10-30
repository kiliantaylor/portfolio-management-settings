"""Intraday swap test for researched names (Kilian 2026-10-30 19:17Z: "start testing swapping in researched names if they
clear the entry bar").

    python3 live/swaps.py SLICE CANDIDATES_JSON

Runs inside live/run.py after live/remark.py, which has re-marked each swap candidate (an unheld name with gate-clean
research at or below its Entry below at the last close) on a fresh option chain. Here:
  floor  = the re-marked Entry below, (target + a year of dividends) / (1 + rf + beta premium), against the live price;
           a name above it drops out (the beta-adjusted floor every buy needs);
  score  = market_color/beats_check.py on the names left: the nightly whole-book test, held names at cost and at their
           latest mark, candidates at the re-marked spot. Each holding is tried against its full planned position (Kilian
           2026-10-30 19:46Z: names end up near the 15% cap, so today's size says little): sell it and drop its planned
           add, put both dollars into the candidate (1..max whole shares), keep every other planned add; the best is kept.
           SWAP_OUT_HOLDS keeps a holding out of the pool until the day after its next print;
  winner = with the 10-name cap full, the swap's book Sharpe beats doing the planned adds instead; with a slot free, the
           plain buy (adds' dollars, nothing sold) does.
A winner is shown on the page's Holdings tab for Kilian to pick; the next-share table and the nightly sizing are unchanged.
Prints one JSON line (the page's DATA.live_swaps)."""
import contextlib, datetime as dt, json, os, sys
# Kilian 2026-10-30 19:55Z: "our thesis on nflx has not played out yet in one direction or another so i'd like to wait and see on
# that before liquidating the position (especially for a loss)". Held out until the day after its next print (data/earnings_miss.json
# next_report; while that date is unknown the hold stands). Remove the entry to lift it.
SWAP_OUT_HOLDS = {'NFLX': "thesis not played out yet; Kilian waits for its next print"}

if __name__ == '__main__':
    SLICE = os.path.abspath(sys.argv[1]); cands = json.loads(sys.argv[2])
    V = f'{SLICE}/valuation'; sys.path.insert(0, V); sys.path.insert(0, f'{SLICE}/market_color'); os.chdir(V)
    import book, divs
    D = json.load(open(f'{SLICE}/slice.json'))['date']
    quotes = json.load(open(f'{V}/data/live_quotes.json')).get('quotes') or {}
    held = [p['symbol'] for p in book.load()['positions'] if p.get('shares')]
    cap = json.load(open(f'{V}/data/review_settings.json')).get('max_positions')
    rows, passed = {}, []
    for s, c in sorted(cands.items()):
        r, pr = json.load(open(f'{V}/runs/{D}/{s}/result.json')), json.load(open(f'{V}/priors/{D}/{s}.json'))
        P = (quotes.get(s) or {}).get('price') or c['price']
        E = (r['target'] + divs.dps(s)) / (1 + pr['rf'] + pr['premium'])
        rows[s] = dict(c, price=P, spot=r['spot'], target=r['target'], entry_below=round(E, 2), floor_ok=P <= E)
        if P <= E and rows[s]['remarked']: passed.append(s)
    out = dict(as_of=D, session=max([q.get('date') or '' for q in quotes.values()] + ['']), slot_free=bool(cap) and len(held) < cap,
               checked=rows, winners=[])
    if passed:
        import beats_check
        f = '/tmp/live_swaps.json'
        em = json.load(open(f'{V}/data/earnings_miss.json'))
        def hold_on(h):
            nx = ((em.get(h) or {}).get('next_report') or {}).get('date')
            return h in SWAP_OUT_HOLDS and not (nx and out['session'] > nx)
        out['swap_holds'] = {h: SWAP_OUT_HOLDS[h] for h in held if hold_on(h)}
        os.environ['BEATS_SWAP_POOL'] = ','.join(h for h in held if not hold_on(h))
        with contextlib.redirect_stdout(sys.stderr): beats_check.main(D, passed, f)  # its summary lines go to the log
        t = json.load(open(f))
        out.update({k: t[k] for k in ('base_sharpe', 'planned_adds', 'planned_budget', 'sharpe_all_planned')})
        for c in t['candidates']:
            x = rows[c['sym']]
            x.update({k: c.get(k) for k in ('shares', 'sharpe', 'swap_out', 'swap_shares', 'swap_sharpe', 'beats_all_planned')}, scored_spot=c.get('spot'), note=c.get('note'))
            x['wins'] = bool(c.get('beats_all_planned')) if out['slot_free'] else (c.get('swap_sharpe') or -9) > t['sharpe_all_planned'] + 1e-9
        for s in t['gated_out']:
            if s in rows: rows[s]['note'] = 'held back by a buy gate'
        out['winners'] = sorted((s for s, x in rows.items() if x.get('wins')), key=lambda s: -(rows[s].get('swap_sharpe') or rows[s].get('sharpe') or 0))
    print(json.dumps(out))
