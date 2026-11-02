"""Intraday swap test for researched names (Kilian 2026-10-30 19:17Z: "start testing swapping in researched names if they
clear the entry bar"; 2026-11-02: "why the swap doesn't just go there", so a winner is now the trade in the next-share table).

    python3 live/swaps.py SLICE CANDIDATES_JSON

Runs inside live/run.py after live/remark.py, which has re-marked each swap candidate (an unheld name with gate-clean
research dated after its last print, at or below its Entry below at the last close) on a fresh option chain. The test
itself is valuation/swap_test.py, the same code the nightly runs at the close (step 3b2s), so the two agree on the same
prices:
  floor  = the re-marked Entry below, (target + a year of dividends) / (1 + rf + beta premium), against the live price;
  score  = market_color/beats_check.py's whole-book Sharpe test; each holding is tried against its full planned position
           (sell it, drop its planned add, put both dollars into the candidate, keep every other planned add); NFLX is
           held out of the pool until the day after its next print; adds on an earnings hold are left out;
  winner = with the 10-name cap full, the swap's book Sharpe beats doing the planned adds instead; with a slot free, the
           plain buy does. The best winner is `swap`, which the page's next-share table shows as today's trade.
Prints one JSON line (the page's DATA.live_swaps)."""
import json, os, sys

if __name__ == '__main__':
    SLICE = os.path.abspath(sys.argv[1]); cands = json.loads(sys.argv[2])
    V = f'{SLICE}/valuation'; sys.path.insert(0, V); sys.path.insert(0, f'{SLICE}/market_color'); os.chdir(V)
    import swap_test
    D = json.load(open(f'{SLICE}/slice.json'))['date']
    quotes = json.load(open(f'{V}/data/live_quotes.json')).get('quotes') or {}
    session = max([q.get('date') or '' for q in quotes.values()] + [''])
    print(json.dumps(swap_test.run(D, cands, quotes=quotes, session=session)))
