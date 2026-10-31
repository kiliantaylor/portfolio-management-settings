"""Trailing 12-month dividends per share (Kilian 2026-10-12: expected return = price return + dividend yield).

Targets, fair values and ladders stay ex-dividend prices (the option forward and the agents both price ex-dividend),
so the dividends a holder collects over the year are added back wherever an expected return feeds the Sharpe test,
the conviction rubric or sizing. Cache: data/dividends.json {SYM: {dps, as_of, n, source}}, refreshed from the Yahoo
chart feed (events=div, no key) by `python3 divs.py SYM ...` (REFRESH step 3a0). Trailing, not forward: a raise
announced but not yet paid is left out (an estimate built only from paid dividends)."""
import datetime, json, os, sys, time, urllib.request
HERE = os.path.dirname(os.path.abspath(__file__)); PATH = os.path.join(HERE, 'data', 'dividends.json')
UA = {'User-Agent': 'Mozilla/5.0'}
_C = None


def _cache():
    global _C
    if _C is None:
        try: _C = json.load(open(PATH))
        except Exception: _C = {}
    return _C


def dps(sym):
    """Trailing 12-month dividends per share in $; 0 when none paid or not fetched."""
    return float((_cache().get(sym.upper()) or {}).get('dps') or 0.0)


def dy(sym, price):
    return dps(sym) / price if price else 0.0


def fetch(sym):
    url = f'https://query1.finance.yahoo.com/v8/finance/chart/{sym}?range=1y&interval=1d&events=div'
    r = json.load(urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=20))['chart']['result'][0]
    cut = time.time() - 365 * 86400
    ev = [v for v in ((r.get('events') or {}).get('dividends') or {}).values() if v['date'] >= cut]
    return dict(dps=round(sum(v['amount'] for v in ev), 4), n=len(ev), as_of=datetime.date.today().isoformat(),
                source=f'Yahoo chart feed, dividends paid in the 365 days before as_of ({url.split("?")[0]})')


if __name__ == '__main__':
    c = _cache()
    for s in [a.upper() for a in sys.argv[1:]]:
        try: c[s] = fetch(s); print(f"{s:6} dps {c[s]['dps']:.4f} ({c[s]['n']} payments)")
        except Exception as e: print(f'{s:6} not fetched ({e}); keeping {c.get(s, {}).get("dps", 0)}')
    json.dump(c, open(PATH, 'w'), indent=1, sort_keys=True)
