"""Buy gates for unheld names: driver_model() and the latest earnings print on or before a date, for the research-after-print gate on new buys and swaps.

Rule (Kilian 2026-10-30 19:40Z for swaps; 2026-11-04 for every new buy): an unheld name is bought only on research dated on or
after its last print. Before 11-04 allocate.py had no such gate and swap_test.py read data/earnings_miss.json
last_report_date, which earnings_miss.py refreshes nightly only for held/staged names: MTRN printed 11-04 before the open
(-16.75%), its file still said 2026-08-05, and the Oct 2 research sized an 11-share buy.

last(D, sym) = the newest of
  - data/earnings_miss.json: last_report_date and history dates (Nasdaq/Zacks),
  - its next_report date when that date is on or before D (the expected print has passed and the file has no newer record:
    treated as printed, which can only hold a buy back),
  - the newest SEC 8-K Item 2.02 filing date (staged_dates.last_202), cached for 3 hours in data/last_print_8k.json.
None when nothing is known (the caller treats that as 'last print unknown': no buy).

    python3 last_print.py D [SYM ...]     prints {SYM: date} (default: every runs/D/*/result.json name)"""
import json, os, sys, glob
from concurrent.futures import ThreadPoolExecutor
HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(HERE, 'data', 'last_print_8k.json')
EM = os.path.join(HERE, 'data', 'earnings_miss.json')
MAX_AGE_S = 3 * 3600  # the 8-K cache is refetched after 3 hours, so a live run the next morning sees a pre-market print


def _j(p, default):
    try: return json.load(open(p))
    except Exception: return default


def sec_dates(D, syms):
    """{SYM: newest 8-K 2.02 filing date or None}, fetched once per D (8 at a time) and cached."""
    import time
    c = _j(CACHE, {})
    if c.get('_as_of') != D or time.time() - c.get('_t', 0) > MAX_AGE_S: c = {'_as_of': D, '_t': time.time()}
    need = [s for s in syms if s not in c]
    if need:
        import staged_dates
        def one(s):
            try: return s, staged_dates.last_202(s)[0]
            except Exception as e: print(f'last_print: SEC 8-K lookup failed for {s}: {e}', file=sys.stderr); return s, '?'
        with ThreadPoolExecutor(8) as ex:
            for s, d in ex.map(one, need): c[s] = d
        try:
            tmp = CACHE + f'.{os.getpid()}'; json.dump(c, open(tmp, 'w'), indent=0); os.replace(tmp, CACHE)
        except Exception as e: print('last_print: cache write failed:', e, file=sys.stderr)
    return {s: (None if c.get(s) == '?' else c.get(s)) for s in syms}


def last_all(D, syms, em=None, sec=True):
    em = _j(EM, {}) if em is None else em
    sd = sec_dates(D, syms) if sec else {}
    out = {}
    for s in syms:
        r = em.get(s) if isinstance(em.get(s), dict) else {}
        ds = [r.get('last_report_date')] + [h.get('date_reported') for h in r.get('history') or []]
        nx = (r.get('next_report') or {}).get('date')
        if nx and nx <= D: ds.append(nx)
        ds.append(sd.get(s))
        ds = [d for d in ds if d and d <= D]
        out[s] = max(ds) if ds else None
    return out


def last(D, sym, em=None, sec=True):
    return last_all(D, [sym], em, sec)[sym]


def driver_model(s, research_as_of):
    """True when the research round the name's target rests on (result.json research_as_of) has a driver-led set
    (supervisor.json model.reconciled_inputs.drivers, as name_update.driver_model). Kilian 2026-11-02 ("im not buying anything
    without one + a memo") and 2026-11-04 ("i said stop recommening buys from names without driver models"): no new buy,
    staged tranche or swap-in without one."""
    if not research_as_of: return False
    for f in (os.path.join(HERE, 'runs', research_as_of, s, 'supervisor.json'), os.path.join(HERE, 'runs', research_as_of, s, 'model_backfill', 'supervisor.json')):
        ri = (_j(f, {}).get('model') or {}).get('reconciled_inputs')
        if isinstance(ri, dict): return bool(ri.get('drivers'))
    return False


if __name__ == '__main__':
    D = sys.argv[1]; syms = sys.argv[2:] or sorted(f.split('/')[-2] for f in glob.glob(f'{HERE}/runs/{D}/*/result.json'))
    print(json.dumps(last_all(D, syms), indent=1))
