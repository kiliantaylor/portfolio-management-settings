"""Call-put implied-vol spread tilt for $1-5B names, live data (Kilian 2026-11-12 card "Add it for $1-5B", skew thread;
evidence adhoc/skew_vrp_1111/report.md sections 4-5).

Signal  CW spread = 30-day IV of the 50-delta call minus 30-day IV of the -50-delta put (the OptionMetrics Volatility
        Surface construct the backtest used, wrds/code/om_retest.py). From the Cboe delayed chain (the same feed
        market_dist.py reads). Per expiry: Black-Scholes-Merton IV from each quote's mid on the official spot, the 3-month
        T-bill rate and the trailing dividend yield (Yahoo, like divs.py), so a put-call parity gap shows up in the spread
        the way it does in OptionMetrics (which also uses its own rate and projected dividends, not a parity forward);
        the 50-delta IV is a vega-weighted straight-line fit of IV on delta over the quotes with |delta| 0.25-0.75 that
        straddle 0.5, separately for calls and puts. Expiries 7-75 days out, spread read at 30 days linearly in days
        between the two expiries bracketing it (the nearest one, within 7-60 days, when only one side exists).
        Known gap: European formula on American quotes (OptionMetrics uses a binomial tree). The early-exercise premium of a
        30-day near-the-money put is small and similar across names, and the cross-sectional standardization below
        removes any common level, so the z-score barely moves.
z       standardized within today's $1-5B cross-section, as in the backtest (each month, within the size bucket): values
        winsorized at the 1st/99th percentiles, minus their mean, over their sd, then capped at +/-2 sd.
        Reference cross-section: every US common stock in the Nasdaq screener file with market cap $1-5B and price >= $5
        (short_interest.py's universe filter) that has a usable Cboe chain.
Shift   excess 12M return e = 0.0085 x z (latest walk-forward coefficient, $1-5B; FM 0.73%/sd t 3.5, OOS R^2 0.067% t 3.5,
        t 2.1 since 2015), log_shift = ln(1 + e): about +/-1.7% at the caps. Not scaled by conviction (conviction.py), like
        the other tilts. Only names with market cap $1-5B on the refresh date; 0 above $5B (nothing there: $5-20B OOS R^2
        0.008% t 0.6, $20B+ negative), below $1B (untested) and for special situations (combine.py skips them).

Usage: python3 cw_spread.py [D] [--force] [--names SYM ...]
  Refreshes data/cw_spread.json (+ archive data/cw_spread/D.json) when the file is older than MAX_AGE_DAYS or --force;
  otherwise exits (cheap; safe to call every night). --names only prints those names' spreads from fresh chains (no write).
Takes ~2-4 minutes (about 900 chains + dividend lookups, 12 at a time)."""
import datetime as dt, json, math, os, re, sys, time
from concurrent.futures import ThreadPoolExecutor
HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, 'data', 'cw_spread.json'); ARCH = os.path.join(HERE, 'data', 'cw_spread')
SCREENER = 'https://api.nasdaq.com/api/screener/stocks?tableonly=true&limit=25&download=true'
UA = 'Mozilla/5.0 (research script)'  # generic; no personal details in any request
CAP_LO, CAP_HI, MIN_PRICE = 1e9, 5e9, 5.0
COEF = 0.0085  # excess 12M return per sd of the spread (report.md section 5: latest walk-forward coefficient for $1-5B)
Z_CAP = 2.0; WINS = 0.01
REFRESH_DAYS = 6  # weekly refresh (the backtest formed the signal monthly); combine.py stops using a file older than its CW_MAX_AGE_DAYS
BAD_NAME = re.compile(r'\b(Preferred|Depositary|Notes|Warrants?|Limited Partner(ship)?|Units?|Rights)\b')
N = lambda x: 0.5 * (1 + math.erf(x / math.sqrt(2)))


def _get(url, timeout=30, tries=3):
    """JSON GET with requests (the client live_scores.py uses for the same screener file); None on 403/404 (no listed options)."""
    import requests
    for i in range(tries):
        try:
            r = requests.get(url, headers={'User-Agent': UA}, timeout=timeout)
            if r.status_code in (403, 404): return None
            r.raise_for_status(); return r.json()
        except Exception:
            if i == tries - 1: raise
        time.sleep(2 * (i + 1))


def num(x):
    try: return float(str(x).replace('$', '').replace(',', ''))
    except Exception: return 0.0


def bsm(S, K, T, r, q, v, call):
    sq = v * math.sqrt(T); d1 = (math.log(S / K) + (r - q + 0.5 * v * v) * T) / sq; d2 = d1 - sq
    if call: return S * math.exp(-q * T) * N(d1) - K * math.exp(-r * T) * N(d2), math.exp(-q * T) * N(d1), S * math.exp(-q * T) * math.sqrt(T) * math.exp(-d1 * d1 / 2)
    return K * math.exp(-r * T) * N(-d2) - S * math.exp(-q * T) * N(-d1), -math.exp(-q * T) * N(-d1), S * math.exp(-q * T) * math.sqrt(T) * math.exp(-d1 * d1 / 2)


def implied(px, S, K, T, r, q, call):
    lo, hi = 1e-3, 5.0
    if not (bsm(S, K, T, r, q, lo, call)[0] < px < bsm(S, K, T, r, q, hi, call)[0]): return None
    for _ in range(60):
        m = (lo + hi) / 2
        if bsm(S, K, T, r, q, m, call)[0] < px: lo = m
        else: hi = m
    return (lo + hi) / 2


def iv_at_delta(pts, target, band=(0.25, 0.75)):
    """pts [(delta, iv, weight)]: weighted straight line of iv on delta over |delta| in band, read at target; needs
    quotes on both sides of the target."""
    p = [x for x in pts if band[0] <= abs(x[0]) <= band[1]]
    if not (any(d < target for d, _, _ in p) and any(d > target for d, _, _ in p)): return None
    sw = sum(w for _, _, w in p); mx = sum(w * d for d, _, w in p) / sw; my = sum(w * v for _, v, w in p) / sw
    sxx = sum(w * (d - mx) ** 2 for d, _, w in p)
    b = sum(w * (d - mx) * (v - my) for d, v, w in p) / sxx if sxx > 0 else 0.0
    return my + b * (target - mx)


def cw_from_chain(raw, S, r, q, t0, relaxed=False):
    """CW spread at 30 days from a Cboe chain dict (data field). Returns dict(cw, iv_c50, iv_p50, expiries) or dict(reason).
    Thin chains ($2.50/$5 strikes on a low-priced name: one strike each side of the money) get a second pass that reads
    |delta| 0.10-0.90 and allows wider quotes (spread up to the mid or $0.20); such results carry relaxed=True."""
    by_exp = {}
    for o in raw.get('options') or []:
        m = re.match(r'^[A-Z.]+(\d{6})([CP])(\d{8})$', o['option'])  # skips adjusted roots (digit in root), as market_dist.py does
        if not m: continue
        b, a = o.get('bid') or 0, o.get('ask') or 0
        if not (b > 0 and a >= b and (a - b) <= (max((a + b) / 2, 0.20) if relaxed else max(0.75 * (a + b) / 2, 0.10))): continue
        by_exp.setdefault(m.group(1), []).append((m.group(2) == 'C', int(m.group(3)) / 1000, (a + b) / 2, a - b))
    rows = []
    for e, opts in sorted(by_exp.items()):
        T = (dt.datetime.strptime(e, '%y%m%d').replace(hour=16) - t0).total_seconds() / (365 * 86400); days = T * 365
        if not 7 <= days <= 75: continue
        side = {True: [], False: []}
        for call, K, mid, spr in opts:
            if not 0.6 < K / S < 1.6: continue
            v = implied(mid, S, K, T, r, q, call)
            if not v: continue
            _, dl, vega = bsm(S, K, T, r, q, v, call)
            side[call].append((dl, v, vega / max(spr, 0.01)))
        bd = (0.10, 0.90) if relaxed else (0.25, 0.75)
        c, p = iv_at_delta(side[True], 0.5, bd), iv_at_delta(side[False], -0.5, bd)
        if c and p: rows.append((days, c, p, dt.datetime.strptime(e, '%y%m%d').date().isoformat()))
    if not rows:
        return cw_from_chain(raw, S, r, q, t0, True) if not relaxed else dict(reason='no expiry 7-75 days with quotes either side of 50 delta')
    lo = [x for x in rows if x[0] <= 30]; hi = [x for x in rows if x[0] > 30]
    if lo and hi:
        (da, ca, pa, ea), (db, cb, pb, eb) = lo[-1], hi[0]; w = (30 - da) / (db - da)
        c, p, used = ca + w * (cb - ca), pa + w * (pb - pa), [ea, eb]
    else:
        x = min((x for x in rows if x[0] <= 60), key=lambda x: abs(x[0] - 30), default=None)
        if not x: return cw_from_chain(raw, S, r, q, t0, True) if not relaxed else dict(reason='no usable expiry within 60 days')
        c, p, used = x[1], x[2], [x[3]]
    return dict(cw=round(c - p, 5), iv_c50=round(c, 4), iv_p50=round(p, 4), expiries=used, **({'relaxed': True} if relaxed else {}))


def risk_free():
    sys.path.insert(0, HERE)
    import market_dist
    return market_dist.risk_free()[0]


def div_yield(sym, S):
    try:
        d = _get(f'https://query1.finance.yahoo.com/v8/finance/chart/{sym}?range=1y&interval=1d&events=div', timeout=20)
        res = d['chart']['result'][0]; cut = time.time() - 365 * 86400
        dps = sum(v['amount'] for v in ((res.get('events') or {}).get('dividends') or {}).values() if v['date'] >= cut)
        return math.log1p(dps / S) if S else 0.0, round(dps, 4)
    except Exception:
        return 0.0, None


def one(sym, r):
    raw = _get(f'https://cdn.cboe.com/api/global/delayed_quotes/options/{sym.replace(".", "/")}.json', timeout=60)
    if not raw or not raw.get('data'): return dict(reason='no Cboe chain')
    d = raw['data']; S = d.get('close') or d.get('current_price')
    if not S: return dict(reason='no spot in chain')
    ld = (d.get('last_trade_time') or dt.date.today().isoformat())[:10]
    q, dps = div_yield(sym, S)
    out = cw_from_chain(d, S, r, q, dt.datetime.fromisoformat(ld + 'T16:00'))
    return dict(out, spot=S, chain_date=ld, div_yield=round(q, 5), dps=dps)


def _safe(s, r):
    try: return one(s, r)
    except Exception as e: return dict(reason=f'fetch failed: {type(e).__name__}')


def wins_stats(vals):
    v = sorted(vals); n = len(v)
    pick = lambda p: v[min(n - 1, max(0, int(round(p * (n - 1)))))]
    lo, hi = pick(WINS), pick(1 - WINS); w = [min(max(x, lo), hi) for x in v]
    m = sum(w) / n; sd = math.sqrt(sum((x - m) ** 2 for x in w) / (n - 1))
    return lo, hi, m, sd


def tilt(z):
    e = COEF * z; return e, math.log1p(e)


def project_symbols(D):
    """Researched names (active_names.universe, archived ones too), the book and the bench: labeled in names{} with their reason."""
    syms = set()
    try:
        sys.path.insert(0, HERE); import active_names; syms |= set(active_names.universe(D))
    except Exception: pass
    try: syms |= set(json.load(open(os.path.join(HERE, 'data', 'bench.json'))).get('names') or [])
    except Exception: pass
    ds = sorted(d for d in os.listdir(os.path.join(HERE, 'priors')) if d[:2] == '20')
    if ds: syms |= {os.path.basename(f)[:-5] for f in os.listdir(os.path.join(HERE, 'priors', ds[-1])) if f.endswith('.json')}
    try:
        sys.path.insert(0, HERE); import book; syms |= {p['symbol'] for p in book.load()['positions']}
    except Exception: pass
    return syms


def main():
    args = sys.argv[1:]; force = '--force' in args
    D = next((a for a in args if re.match(r'^\d{4}-\d{2}-\d{2}$', a)), dt.date.today().isoformat())
    r = risk_free()
    if '--names' in args:
        for s in args[args.index('--names') + 1:]:
            print(s.upper(), one(s.upper(), r))
        return
    old = json.load(open(OUT)) if os.path.exists(OUT) else {}
    if old.get('as_of') and not force and (dt.date.fromisoformat(D) - dt.date.fromisoformat(old['as_of'])).days < REFRESH_DAYS:
        print(f"cw_spread: data/cw_spread.json from {old['as_of']} is under {REFRESH_DAYS} days old; nothing to do"); return
    scr = (_get(SCREENER, timeout=90) or {}).get('data', {}).get('rows')
    if not scr: raise SystemExit('cw_spread: Nasdaq screener file not available; keeping the old file')
    caps, band = {}, []
    for x in scr:
        s = x['symbol'].strip(); cap, px = num(x.get('marketCap')), num(x.get('lastsale'))
        ok = px >= MIN_PRICE and x.get('country') == 'United States' and '^' not in s and '/' not in s and not BAD_NAME.search(x.get('name') or '')
        caps[s] = dict(cap=cap, price=px, us_common=ok)
        if ok and CAP_LO <= cap < CAP_HI: band.append(s)
    if len(band) < 300: raise SystemExit(f'cw_spread: only {len(band)} $1-5B names in the screener file; download looks incomplete')
    proj = project_symbols(D)
    todo = sorted(set(band) | {s for s in proj if CAP_LO <= (caps.get(s) or {}).get('cap', 0) < CAP_HI})
    with ThreadPoolExecutor(12) as ex: got = dict(zip(todo, ex.map(lambda s: _safe(s, r), todo)))
    ref = {s: g['cw'] for s, g in got.items() if s in band and g.get('cw') is not None}
    if len(ref) < 200: raise SystemExit(f'cw_spread: only {len(ref)} usable $1-5B chains; Cboe fetch looks incomplete, keeping the old file')
    lo, hi, m, sd = wins_stats(list(ref.values()))
    names = {}
    for s in sorted(set(todo) | proj):
        g = got.get(s) or {}; c = caps.get(s) or {}
        row = dict(cap=c.get('cap'), **{k: v for k, v in g.items()})
        in_band = s in band
        if not c: row.update(log_shift=0.0, reason='not in the Nasdaq screener file')
        elif not in_band: row.update(log_shift=0.0, reason=('above $5B: no tilt' if c.get('cap', 0) >= CAP_HI else 'below $1B: no tilt (untested)') if c.get('us_common') else 'not US common stock / price under $5')
        elif g.get('cw') is None: row.update(log_shift=0.0, reason=g.get('reason') or 'no spread')
        else:
            z = max(-Z_CAP, min(Z_CAP, (min(max(g['cw'], lo), hi) - m) / sd)); e, ls = tilt(z)
            row.update(z=round(z, 3), excess_12m=round(e, 5), log_shift=round(ls, 5), in_band=True)
        names[s] = row
    out = dict(as_of=D, fetched=dt.datetime.now(dt.timezone.utc).strftime('%Y-%m-%dT%H:%MZ'),
               source='Cboe delayed option chains (https://cdn.cboe.com/api/global/delayed_quotes/options/SYM.json); market caps from the Nasdaq screener file; dividends from the Yahoo chart feed',
               definition='30-day IV of the 50-delta call minus the -50-delta put (BSM IV on mids at the T-bill rate and trailing dividend yield)',
               band='US common stock, market cap $1-5B, price >= $5 (Nasdaq screener)', n_band=len(band), n_reference=len(ref),
               cross_section=dict(wins_lo=round(lo, 5), wins_hi=round(hi, 5), mean=round(m, 5), sd=round(sd, 5),
                                  raw_mean=round(sum(ref.values()) / len(ref), 5)),
               mapping=dict(formula='z = (winsorized cw - mean) / sd, capped at +/-2; excess_12m = 0.0085 z; log_shift = ln(1 + excess_12m)',
                            coef=COEF, z_cap=Z_CAP, evidence='adhoc/skew_vrp_1111/report.md sections 4-5 (OptionMetrics 2000-2025, walk-forward from 2005)',
                            sizing='not scaled by conviction (conviction.py), like the other tilts'),
               unusable_reasons=dict(__import__('collections').Counter(re.sub(r': .*', '', g.get('reason') or '?') for s, g in got.items() if s in band and g.get('cw') is None)),
               reference={s: ref[s] for s in sorted(ref)}, names={s: names[s] for s in sorted(proj) if s in names},
               band_names={s: names[s] for s in sorted(band) if s in names and names[s].get('in_band')})
    os.makedirs(ARCH, exist_ok=True)
    tmp = OUT + '.tmp'; json.dump(out, open(tmp, 'w'), indent=1); os.replace(tmp, OUT)
    json.dump(out, open(os.path.join(ARCH, f'{D}.json'), 'w'), indent=1)
    inb = [s for s in proj if (names.get(s) or {}).get('in_band')]
    print(f"cw_spread {D}: {len(ref)} of {len(band)} $1-5B names usable; winsorized mean {m:+.4f} sd {sd:.4f}; project names in band: "
          + (', '.join(f"{s} z {names[s]['z']:+.2f} ({names[s]['excess_12m']*100:+.2f}%)" for s in sorted(inb)) or 'none'))


if __name__ == '__main__':
    main()
