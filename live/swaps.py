"""Intraday swap test for researched names (Kilian 2026-10-30 19:17Z: "start testing swapping in researched names if they
clear the entry bar").

    python3 live/swaps.py SLICE CANDIDATES_JSON

Runs inside live/run.py after live/remark.py, which has re-marked each swap candidate (an unheld name with gate-clean
research at or below its Entry below at the last close) on a fresh option chain. Here:
  floor  = the re-marked Entry below, (target + a year of dividends) / (1 + rf + beta premium), against the live price;
           a name above it drops out (the beta-adjusted floor every buy needs);
  score  = market_color/beats_check.py on the names left: the nightly whole-book test, held names at cost and at their
           latest mark, candidates at the re-marked spot. A candidate buys 1..max whole shares with the planned held-name
           adds' dollars plus the dollars of the held name it replaces. Kilian 2026-10-30 19:40Z ("i don't really want to
           swap out nflx right now though, i'd rather do amzn or one of the other smaller positions"): the replaced name is
           the best of the held names outside SWAP_OUT_NEVER and under BIG_SHARE of the book's invested cost;
  winner = with the 10-name cap full, the swap's book Sharpe beats doing the planned adds instead; with a slot free, the
           plain buy (adds' dollars, nothing sold) does.
A winner is shown on the page's Holdings tab for Kilian to pick; the next-share table and the nightly sizing are unchanged.
Prints one JSON line (the page's DATA.live_swaps)."""
import contextlib, json, os, sys
SWAP_OUT_NEVER = ('NFLX',)  # Kilian 2026-10-30 19:40Z
BIG_SHARE = 0.20  # positions at or above this share of invested cost (NFLX, ABT, T on 10-30, ~25% each) are not swapped out

if __name__ == '__main__':
    SLICE = os.path.abspath(sys.argv[1]); cands = json.loads(sys.argv[2])
    V = f'{SLICE}/valuation'; sys.path.insert(0, V); sys.path.insert(0, f'{SLICE}/market_color'); os.chdir(V)
    import book, divs
    D = json.load(open(f'{SLICE}/slice.json'))['date']
    quotes = json.load(open(f'{V}/data/live_quotes.json')).get('quotes') or {}
    pos = {p['symbol']: p.get('cost_basis') or 0 for p in book.load()['positions'] if p.get('shares')}; held = list(pos)
    pool = [h for h in held if h not in SWAP_OUT_NEVER and pos[h] < BIG_SHARE * sum(pos.values())]
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
        os.environ['BEATS_SWAP_POOL'] = ','.join(pool)
        with contextlib.redirect_stdout(sys.stderr): beats_check.main(D, passed, f)  # its summary lines go to the log
        t = json.load(open(f))
        out.update({k: t[k] for k in ('base_sharpe', 'planned_adds', 'planned_budget', 'sharpe_all_planned')}, swap_pool=pool)
        for c in t['candidates']:
            x = rows[c['sym']]
            x.update({k: c.get(k) for k in ('shares', 'sharpe', 'swap_out', 'swap_shares', 'swap_sharpe', 'beats_all_planned')}, scored_spot=c.get('spot'), note=c.get('note'))
            x['wins'] = bool(c.get('beats_all_planned')) if out['slot_free'] else (c.get('swap_sharpe') or -9) > t['sharpe_all_planned'] + 1e-9
        for s in t['gated_out']:
            if s in rows: rows[s]['note'] = 'held back by a buy gate'
        out['winners'] = sorted((s for s, x in rows.items() if x.get('wins')), key=lambda s: -(rows[s].get('swap_sharpe') or rows[s].get('sharpe') or 0))
    print(json.dumps(out))
