"""Short-interest days-to-cover tilt, live data (Kilian 2026-10-19 card "Add short-interest days-to-cover as a 12-month return tilt").

Fetches FINRA's public consolidated equity short interest (twice-monthly settlement dates; FINRA Query API dataset
otcMarket/consolidatedShortInterest, no credentials) for the newest settlement date, ranks days to cover across a broad
$1B+ US universe, and writes data/short_interest.json (and an archive copy data/short_interest/SETTLE.json) for combine.py.

  universe   Nasdaq screener file (the list live_scores.py / ideas/screen.py use): US common stock, country United States,
             market cap >= $1B, price >= $5, no preferreds / depositary shares / notes / warrants / units / rights, and a FINRA
             record with average daily volume > 0. Mirrors the backtest universe (CRSP US-incorporated common, $1B+, $5+).
  dtc        currentShortPositionQuantity / averageDailyVolumeQuantity (FINRA's own daysToCoverQuantity is the same ratio but
             rounded and floored at 1, which ties most large caps, so it is recomputed unrounded)
  pct        percentile rank of dtc in the universe (average ties; 1 = hardest to cover); quintile = ceil(5 pct)
  tilt       excess 12M return e = SLOPE (0.5 - pct), log_shift = ln(1 + e); SLOPE below. Names outside the universe keep
             their dtc and where it falls in the universe (pct_vs_universe) for information but get log_shift 0, because the
             evidence covers US-incorporated $1B+ common stock only.

Evidence (WRDS, used to set the slope only; no WRDS data feeds the live signal): wrds/code/short_long.py walk-forward
regression of the 12M return minus the $1B+ universe mean on 0.5 - pct(dtc), expanding window using only months whose
12-month outcome was known (12-month gap), forecasts formed 2010-2024: last slope 0.0548 (top minus bottom fifth 4.4%), OOS R^2
0.18%, t across years 2.67, 11 of 15 years positive (wrds/results/short_long_walk.csv). Shrunk like the EPS tilt by
t^2/(1 + t^2) = 0.877 -> SLOPE 0.0480: about +1.9% for the easiest fifth and -1.9% for the hardest (+/-2.4% at the ends).
Quintile shape check: wrds/code/short_dtc_tilt_fit.py (results/short_dtc_tilt_fit.csv).

Usage: python3 short_interest.py [--settle YYYY-MM-DD] [--force]
  Without --settle: probes the most recent settlement dates and takes the newest one FINRA has published. If
  data/short_interest.json already holds that date, exits after the probe (cheap; safe to call daily). --force refetches."""
import bisect, datetime as dt, glob, json, math, os, re, sys, time, urllib.request
HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, 'data', 'short_interest.json'); ARCH = os.path.join(HERE, 'data', 'short_interest')
API = 'https://api.finra.org/data/group/otcMarket/name/consolidatedShortInterest'
SCREENER = 'https://api.nasdaq.com/api/screener/stocks?tableonly=true&limit=25&download=true'
UA = 'Mozilla/5.0 (research script)'  # generic; no personal details in any request
SLOPE_WF, T_YEARS = 0.054769, 2.668  # wrds/results/short_long_walk.csv (slope_last, t_years)
SHRINK = T_YEARS ** 2 / (1 + T_YEARS ** 2); SLOPE = round(SLOPE_WF * SHRINK, 5)
MIN_CAP, MIN_PRICE = 1e9, 5.0
BAD_NAME = re.compile(r'\b(Preferred|Depositary|Notes|Warrants?|Limited Partner(ship)?|Units?|Rights)\b')


def tilt(pct):
    """Excess 12M return and log shift for a universe percentile of days to cover (1 = hardest to cover)."""
    e = SLOPE * (0.5 - pct); return e, math.log1p(e)


def _req(url, body=None, timeout=90, tries=3):
    for i in range(tries):
        try:
            data = json.dumps(body).encode() if body is not None else None
            r = urllib.request.Request(url, data=data, headers={'User-Agent': UA, 'Accept': 'application/json', **({'Content-Type': 'application/json'} if data else {})})
            with urllib.request.urlopen(r, timeout=timeout) as f:
                raw = f.read(); return (json.loads(raw) if raw.strip() else None), dict(f.headers)  # FINRA sends an empty body for no rows
        except Exception as e:
            if i == tries - 1: raise
            time.sleep(3 * (i + 1))


def finra(settle, limit=5000, offset=0, fields=None):
    body = dict(limit=limit, offset=offset, compareFilters=[dict(compareType='equal', fieldName='settlementDate', fieldValue=settle)])
    if fields: body['fields'] = fields
    rows, h = _req(API, body)
    return rows or [], int({k.lower(): v for k, v in h.items()}.get('record-total') or 0)


def candidates(today):
    """Possible settlement dates, newest first: weekdays 10th-15th and the last 7 days of each of the last 3 months."""
    out = set()
    for k in range(3):
        y, m = today.year, today.month - k
        while m < 1: y, m = y - 1, m + 12
        last = (dt.date(y + (m == 12), m % 12 + 1, 1) - dt.timedelta(days=1)).day
        for d in list(range(10, 16)) + list(range(last - 6, last + 1)):
            x = dt.date(y, m, d)
            if x <= today and x.weekday() < 5: out.add(x)
    return sorted(out, reverse=True)


def latest_settle(today):
    for d in candidates(today):
        _, n = finra(d.isoformat(), limit=1)
        if n: return d.isoformat()
    raise SystemExit('no FINRA short-interest settlement date found in the last 3 months')


def add_weekdays(d, n):
    while n:
        d += dt.timedelta(days=1); n -= d.weekday() < 5
    return d


def num(x):
    try: return float(str(x).replace('$', '').replace(',', ''))
    except Exception: return float('nan')


def norm(s): return ''.join(ch for ch in str(s).upper().strip() if ch.isalnum())


def project_symbols():
    """Names this project researches or holds: the newest priors/ date plus the book."""
    syms = set()
    ds = sorted(d for d in os.listdir(os.path.join(HERE, 'priors')) if d[:2] == '20')
    if ds: syms |= {os.path.basename(f)[:-5] for f in glob.glob(os.path.join(HERE, 'priors', ds[-1], '*.json'))}
    try:
        sys.path.insert(0, HERE); import book; syms |= {p['symbol'] for p in book.load()['positions']}
    except Exception: pass
    return syms


def main():
    args = sys.argv[1:]; today = dt.date.today()
    settle = args[args.index('--settle') + 1] if '--settle' in args else latest_settle(today)
    old = json.load(open(OUT)) if os.path.exists(OUT) else {}
    if old.get('settle_date') == settle and '--force' not in args:
        print(f'short_interest: {settle} already in {os.path.relpath(OUT, HERE)} ({old.get("n_universe")} names); nothing to do'); return
    fields = ['symbolCode', 'issueName', 'marketClassCode', 'currentShortPositionQuantity', 'averageDailyVolumeQuantity', 'daysToCoverQuantity', 'revisionFlag']
    rows, total = finra(settle, fields=fields)
    while len(rows) < total:
        more, _ = finra(settle, offset=len(rows), fields=fields)
        if not more: break
        rows += more
    if not rows: raise SystemExit(f'FINRA returned no rows for {settle}')
    fin = {}
    for r in rows:
        if r.get('marketClassCode') == 'OTC': continue  # exchange-listed only
        fin[norm(r['symbolCode'])] = r
    scr, _ = _req(SCREENER); scr = scr['data']['rows']
    uni, info = {}, {}
    for r in scr:
        s = r['symbol'].strip(); k = norm(s); cap, px = num(r.get('marketCap')), num(r.get('lastsale'))
        f = fin.get(k); adv = (f or {}).get('averageDailyVolumeQuantity') or 0
        dtc = f['currentShortPositionQuantity'] / adv if f and adv > 0 else None
        info[s] = dict(cap=cap, price=px, country=r.get('country'), dtc=dtc, finra=f)
        ok = (cap >= MIN_CAP and px >= MIN_PRICE and r.get('country') == 'United States' and '^' not in s
              and not BAD_NAME.search(r.get('name') or ''))
        if ok and dtc is not None: uni[s] = dtc
    vals = sorted(uni.values()); n = len(vals)
    if n < 500: raise SystemExit(f'universe too small ({n}); screener or FINRA download looks incomplete')
    def pct_of(x):  # average-tie percentile rank within the universe (pandas rank(pct=True) for members)
        lo, hi = bisect.bisect_left(vals, x), bisect.bisect_right(vals, x)
        return ((lo + 1 + hi) / 2) / n if hi > lo else (lo + 0.5) / n
    names = {}
    for s, d in uni.items():
        p = pct_of(d); e, ls = tilt(p)
        names[s] = dict(dtc=round(d, 3), pct=round(p, 4), quintile=min(5, max(1, math.ceil(p * 5))), excess_12m=round(e, 5), log_shift=round(ls, 5), in_universe=True)
    for s in sorted(project_symbols() - set(names)):
        i = info.get(s) or {}; f = i.get('finra') or fin.get(norm(s))
        adv = (f or {}).get('averageDailyVolumeQuantity') or 0
        d = i.get('dtc') if i.get('dtc') is not None else (f['currentShortPositionQuantity'] / adv if f and adv > 0 else None)
        if not f: why = 'no FINRA record'
        elif d is None: why = 'FINRA average daily volume 0'
        elif not i: why = 'not in the Nasdaq screener file'
        else:
            bits = [f"country {i.get('country') or 'blank'}" if i.get('country') != 'United States' else '',
                    'cap < $1B' if not i.get('cap', 0) >= MIN_CAP else '', 'price < $5' if not i.get('price', 0) >= MIN_PRICE else '']
            why = 'outside the backtest universe (' + ('; '.join(b for b in bits if b) or 'share class/type excluded') + ')'
        names[s] = dict(dtc=round(d, 3) if d is not None else None, pct=None, pct_vs_universe=round(pct_of(d), 4) if d is not None else None,
                        quintile=None, excess_12m=0.0, log_shift=0.0, in_universe=False, reason=why)
    sd = dt.date.fromisoformat(settle)
    out = dict(settle_date=settle, available_est=add_weekdays(sd, 8).isoformat(), fetched=today.isoformat(),
               source=f'{API} (settlementDate = {settle}); FINRA consolidated short interest, public data',
               universe_source=SCREENER, n_finra_rows=len(rows), n_universe=n,
               universe='Nasdaq screener: country United States, market cap >= $1B, price >= $5, no preferred/depositary/notes/warrants/units/rights; FINRA exchange-listed record with average daily volume > 0',
               dtc_definition='currentShortPositionQuantity / averageDailyVolumeQuantity (FINRA settlement-period average daily volume), unrounded',
               mapping=dict(formula='excess_12m = slope * (0.5 - pct); log_shift = ln(1 + excess_12m)', slope=SLOPE, slope_walk_forward=SLOPE_WF,
                            t_years=T_YEARS, shrink=round(SHRINK, 4), evidence='wrds/results/short_long_walk.csv, wrds/results/short_dtc_tilt_fit.csv',
                            sizing='quarter Kelly like research alpha; not scaled by conviction (conviction.py)'),
               quintile_bounds={f'q{q}': round(vals[min(n - 1, math.ceil(n * q / 5) - 1)], 3) for q in range(1, 6)},
               names=dict(sorted(names.items())))
    os.makedirs(ARCH, exist_ok=True)
    for p in (OUT, os.path.join(ARCH, f'{settle}.json')):
        tmp = p + '.tmp'; json.dump(out, open(tmp, 'w'), indent=1); os.replace(tmp, p)
    print(f'short_interest: settlement {settle} (available ~{out["available_est"]}), {len(rows)} FINRA rows, universe {n} names -> {os.path.relpath(OUT, HERE)}')
    try: held = {p['symbol'] for p in __import__('book').load()['positions']}  # print the book's names only
    except Exception: held = set()
    print(f"  {sum(1 for r in names.values() if not r['in_universe'])} project names outside the universe get 0; held names:")
    for s in sorted(held & set(names)):
        r = names[s]
        if r['dtc'] is not None:
            print(f"  {s:6} dtc {r['dtc']:6.2f}  pct {r['pct'] if r['pct'] is not None else '-':>6}  q{r['quintile'] or '-'}  shift {r['log_shift']:+.4f}" + ('' if r['in_universe'] else f"  [{r['reason']}]"))


if __name__ == '__main__':
    main()
