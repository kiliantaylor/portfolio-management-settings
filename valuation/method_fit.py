"""Does the standard toolkit (peer multiples + a plain DCF) fit this name? (Kilian 2026-10-14 20:37Z: "going forward you
should be able to identify cases like this where standard approaches will not provide a useful valuation").

Run before prep_briefs, after the peer table step (it reads the name's own consensus snapshot, data/estimates/SYM.json,
when one exists). Objective tests on SEC filings (secfacts.py), the last close and consensus:
  - losses: GAAP operating income or EBITDA TTM <= 0, or EBITDA margin under 5%
  - EV/sales TTM above 15x
  - net cash above 25% of market cap
  - forward revenue growth above 40% (consensus, quarters 5-8 vs 1-4) or TTM revenue under $500M with EV above $5B
  - binary pipeline: SEC SIC code in pharma/biotech (only if loss-making or revenue TTM under $1B), or the name listed in data/method_fit.json "binary"
  - filings too thin to test (no TTM revenue or market cap in XBRL: a recent listing): counts as one test
  - Kilian's or the thread's own call: data/method_fit.json "force" (SYM -> reason)
Two or more tests (or a binary/force flag) -> verdict "non_standard": writes runs/D/SYM/method_fit.md, which prep_briefs
appends to every forecaster and supervisor brief (non-multiple methods, multiples capped at 0.10 weight).
One test -> "watch": the flag is listed in the briefs, standard methods stay.
This script never sets a DCF weight (checked 2026-10-19): the DCF was zeroed by the DCF CHECK rule in
agents/model_supervisor.md, which since Kilian 2026-10-19 20:31Z keeps a well-built driver DCF's weight and makes a >20%
disagreement a flag the supervisor must explain.

Usage: python3 method_fit.py D SYM [SYM ...]   -> runs/D/SYM/method_fit.json (+ method_fit.md when non_standard)
"""
import json, os, sys
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import secfacts, peers

CFG = f'{HERE}/data/method_fit.json'
BIO_SIC = {'2834', '2835', '2836', '8731'}

ADDENDUM = """
## Valuation approach: standard methods do not fit {SYM} (method_fit.py, {D})
Tests that fired: {FLAGS}.
Kilian (2026-10-14): when peer multiples and a plain DCF cannot give a useful value, build the value another way. So in this run:
1. Peer multiples are a SANITY CROSS-CHECK only, weight at most 0.10 in your fair value, and only if they add information.
   The 10-12 peer rule applies to that check only; say if fewer genuine listed comparables exist. Multiples ARE allowed
   for any revenue-generating unit valued against its own industry peers inside a sum of the parts.
2. Build fair value from at least THREE of these (your lane decides which you lean on; say why), each a method in
   `fair_value.methods` with every input sourced or labelled judgment and arithmetic shown, carried by the model engine's
   custom legs (`custom` / `adjustments`):
   a. Milestone / risk-adjusted NPV: the company's own published roadmap or pipeline as stages, a probability of reaching
      each on time, late or never (grounded in base rates and the company's own record of hits and misses), the cash flows
      each success state unlocks, discounted at a rate you justify. Show the probability tree.
   b. Market-share scenarios: a published market forecast (source and date), the share the company could hold by a stated
      year, a terminal margin from mature companies (sourced), at least three probability-weighted states including failure.
   c. Reverse DCF: the revenue and margin path today's EV requires (EV = market cap minus net cash, with every share already
      agreed); compare it with (a)/(b) and guidance and turn the gap into a value view.
   d. Sum of the parts: each revenue-generating unit against its own peers, plus the speculative core as a separate leg;
      check against what the company paid for each unit.
   e. Cash floor plus option: net cash per diluted share after expected burn and committed deal cash, plus the core
      programme as an explicit option or scenario tree (state the volatility or probabilities and why).
   f. Transaction marks: recent private rounds and M&A prices in the field, translated on a stated common unit.
3. Dilution and cash burn are first-order: count every share that pending deals, convertibles, warrants and stock comp
   add by the horizon (stock comp exactly once) and the cash used. Name pending all-stock deals and their status.
4. Post-horizon risks (obsolescence, dilution, another technology winning) are priced into the horizon value.
5. (Kilian 2026-10-19 20:31Z, deep modeling) Carry these methods as first-class engine legs where they fit: `rnpv`
   (pipeline), `nav` (reserves), `sotp` with a method per part, `stakes` (probability-weighted holdings), `segment_build`.
   The multiples cap above does not apply to a driver-built DCF: when it disagrees with the other legs by more than 20%,
   explain the gap in `dcf_check` rather than zeroing it.
"""

def cfg():
    try: return json.load(open(CFG))
    except FileNotFoundError: return {}

def assess(sym):
    sym = sym.upper(); c = cfg(); flags, data = [], {}
    p = secfacts.build(sym); g = {f['key']: f for f in p['facts']}
    row = peers.row_for(sym); rf = {f['key']: f['value'] for f in row.get('facts', [])}
    rev, ebitda, opi = (g.get(k, {}).get('value') for k in ('revenue_ttm', 'ebitda_ttm', 'operating_income_ttm'))
    nd, mcap, ev = g.get('net_debt', {}).get('value'), rf.get('mcap'), rf.get('ev')
    data.update(revenue_ttm=rev, ebitda_ttm=ebitda, operating_income_ttm=opi, net_debt=nd, mcap=mcap, ev=ev,
                ev_sales_ttm=rf.get('ev_sales_ttm'), revenue_growth_fwd=rf.get('revenue_growth_fwd'))
    if (opi is not None and opi <= 0) or (ebitda is not None and ebitda <= 0):
        flags.append(f"losses (operating income TTM {opi/1e6:,.0f}M, EBITDA TTM {ebitda/1e6:,.0f}M)" if opi is not None and ebitda is not None else 'losses')
    elif rev and ebitda is not None and ebitda / rev < 0.05:
        flags.append(f'EBITDA margin {ebitda / rev:.1%} TTM (under 5%)')
    if rf.get('ev_sales_ttm') and rf['ev_sales_ttm'] > 15:
        flags.append(f"EV/sales TTM {rf['ev_sales_ttm']:.1f}x (above 15x)")
    if nd is not None and mcap and -nd / mcap > 0.25:
        flags.append(f'net cash {-nd / 1e9:,.2f}B = {-nd / mcap:.0%} of market cap (above 25%)')
    gf = rf.get('revenue_growth_fwd')
    if gf is not None and gf > 0.40: flags.append(f'consensus forward revenue growth {gf:.0%} (above 40%)')
    elif rev and ev and rev < 5e8 and ev > 5e9: flags.append(f'revenue TTM {rev/1e6:,.0f}M against EV {ev/1e9:,.1f}B')
    if rev is None or mcap is None:
        flags.append('filings too thin to test (no TTM revenue or market cap from XBRL: recent listing or untagged filings)')
    hard = []
    try:
        sic = str(secfacts.submissions(p['cik']).get('sic') or '')
        # 2026-10-15 (ABT run): a pharma SIC code alone flagged Abbott, a profitable diversified medtech, as a binary
        # pipeline. The SIC test now fires only for a pharma/biotech filer that is loss-making or under $1B revenue TTM.
        if sic in BIO_SIC and not (opi and opi > 0 and (ebitda is None or ebitda > 0) and rev and rev >= 1e9):
            hard.append(f'binary pipeline (SEC SIC {sic}, pharma/biotech, loss-making or under $1B revenue)')
    except Exception: pass
    if sym in set(c.get('binary', [])): hard.append('binary technology roadmap (data/method_fit.json)')
    if sym in c.get('force', {}): hard.append(f"flagged by hand: {c['force'][sym]}")
    allf = hard + flags
    verdict = 'non_standard' if hard or len(flags) >= 2 else ('watch' if flags else 'standard')
    return dict(symbol=sym, verdict=verdict, flags=allf, data=data)

if __name__ == '__main__':
    if len(sys.argv) < 3: print(__doc__); sys.exit(1)
    D = sys.argv[1]
    for s in sys.argv[2:]:
        rd = f'{HERE}/runs/{D}/{s.upper()}'; os.makedirs(rd, exist_ok=True)
        try: r = assess(s)
        except Exception as e: r = dict(symbol=s.upper(), verdict='unknown', flags=[f'could not test: {str(e)[:150]}'], data={})
        json.dump(r, open(f'{rd}/method_fit.json', 'w'), indent=1)
        md = f'{rd}/method_fit.md'
        if r['verdict'] == 'non_standard':
            open(md, 'w').write(ADDENDUM.replace('{SYM}', r['symbol']).replace('{D}', D).replace('{FLAGS}', '; '.join(r['flags'])))
        elif os.path.exists(md): os.remove(md)
        print(r['symbol'], r['verdict'], '|', '; '.join(r['flags']) or 'no flags')
