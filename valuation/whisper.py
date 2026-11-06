"""Whisper EPS: the Street consensus plus the beat the market expects.

Method (Kilian's whisper rule; estimator chosen by the 2026-11-02 study, wrds/code/whisper_test*.py, results wrds/work/whisper/):
    beat s of a past report = (reported EPS - consensus EPS) / close at the month-end before the report month   (share of price)
    own    = median s over the name's last 4 usable reports (needs 3; with fewer, own is left out and the sector beat stands alone)
    sector = median s over every report of the names we track in the same GICS sector, reported in the 4 calendar quarters before
             today's quarter (needs 12 reports, else every tracked name is pooled)
    beat   = 1/2 own + 1/2 sector                       (own only when it exists; else sector)
    whisper EPS = Street + beat x current share price
    range  = whisper + z x spread x price, spread = mean |s - own| over the same 4 reports (floor 0.05% of price);
             z = -0.895 / +0.810 (50% range), -2.061 / +2.080 (80% range), fitted 2005-2014 on I/B/E/S
Usable = not on another basis (EPS more than 50% off a positive consensus) and not listed as a one-off in data/whisper_exclusions.json.
Why: out of sample (I/B/E/S 2013-2025, our names) this cut the whisper's error vs reported EPS 16% vs the Street alone and 32% vs
the old mean of the last 4 % beats, and beat the old method in all 13 years. Beats in % of Street blow up when the consensus is near
zero, a mean lets one odd quarter dominate, and a name's own 4 beats are noisy, so half the weight goes to the sector's usual beat.

Data: data/earnings_history.json (Nasdaq.com earnings surprise table, Zacks consensus; earnings_miss.py keeps it, Nasdaq shows the
last four reports) and data/gics_sector.json. `--refresh` pulls Nasdaq for names whose history is missing or stale.

  python3 whisper.py SYM [SYM ...] [--street EPS] [--spot PX] [--date D] [--refresh]    prints the whisper with its parts
Library: whisper(sym, street, spot, D=None) -> dict (None fields when Street or spot is missing).
"""
import datetime as dt, json, os, statistics, sys, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
HIST = os.path.join(HERE, 'data', 'earnings_history.json')
GICS = os.path.join(HERE, 'data', 'gics_sector.json')
EXCL = os.path.join(HERE, 'data', 'whisper_exclusions.json')
W_OWN, N_OWN, N_OWN_MIN, N_SECTOR_MIN = 0.5, 4, 3, 12
SPREAD_FLOOR = 0.0005
Z50, Z80 = (-0.895, 0.810), (-2.061, 2.080)
UA = {'User-Agent': 'Mozilla/5.0'}
_px = {}


def jl(p, d):
    try:
        with open(p) as f: return json.load(f)
    except Exception: return d


def off_basis(r):
    c = r.get('consensus') or 0
    return c > 0 and abs(r.get('eps', 0) / c - 1) > 0.5


def month_end_close(sym, date):
    """Close at the last trading day of the month before the report month: local daily file, else Yahoo daily chart."""
    import earnings_miss
    x = earnings_miss.month_end_close(sym, date)
    if x: return x[1]
    first = dt.date.fromisoformat(date[:8] + '01')
    if sym not in _px:
        try:
            p2 = int(dt.datetime.now(dt.timezone.utc).timestamp()); p1 = p2 - 86400 * 365 * 4
            u = f'https://query1.finance.yahoo.com/v8/finance/chart/{sym}?period1={p1}&period2={p2}&interval=1d'
            r = json.load(urllib.request.urlopen(urllib.request.Request(u, headers=UA), timeout=30))['chart']['result'][0]
            closes = r['indicators'].get('adjclose', [{}])[0].get('adjclose') or r['indicators']['quote'][0]['close']
            _px[sym] = sorted((dt.datetime.fromtimestamp(t, dt.timezone.utc).date(), c) for t, c in zip(r['timestamp'], closes) if c)
        except Exception: _px[sym] = []
    prev = [c for d, c in _px[sym] if d < first]
    return prev[-1] if prev else None


def reports(sym, hist=None, excl=None):
    """Past reports of sym, oldest first, each with s (beat as share of price) and why it is unusable (None when usable)."""
    hist = jl(HIST, {}) if hist is None else hist
    excl = jl(EXCL, {}) if excl is None else excl
    out = []
    for d, r in sorted((hist.get(sym) or {}).items()):
        if r.get('eps') is None or r.get('consensus') is None: continue
        px = month_end_close(sym, r['date_reported'])
        why = (excl.get(sym) or {}).get(r['date_reported'])
        why = f'one-off: {why}' if why else ('other EPS basis (>50% off consensus)' if off_basis(r) else None)
        if not px: why = why or 'no month-end price'
        out.append(dict(date=r['date_reported'], fq=r.get('fiscal_quarter'), eps=r['eps'], consensus=r['consensus'],
                        s=(r['eps'] - r['consensus']) / px if px else None, unusable=why))
    return out


def qidx(d):
    d = dt.date.fromisoformat(d); return d.year * 4 + (d.month - 1) // 3


def sector_beat(sym, D, hist=None, excl=None):
    gics = jl(GICS, {}); sec = gics.get(sym); q = qidx(D)
    hist = jl(HIST, {}) if hist is None else hist
    pool_sec, pool_all = [], []
    for n in hist:
        if n.startswith('_'): continue
        for r in reports(n, hist, excl):
            if r['unusable'] or r['s'] is None or not (q - 4 <= qidx(r['date']) <= q - 1): continue
            pool_all.append(r['s'])
            if sec and gics.get(n) == sec: pool_sec.append(r['s'])
    names = jl(GICS, {}).get('_meta', {}).get('sectors', {})
    if len(pool_sec) >= N_SECTOR_MIN:
        return statistics.median(pool_sec), len(pool_sec), names.get(sec, sec)
    if len(pool_all) >= N_SECTOR_MIN:
        return statistics.median(pool_all), len(pool_all), 'all tracked names' + (f' ({names.get(sec, sec)} has {len(pool_sec)} reports)' if sec else '')
    return None, len(pool_all), None


def whisper(sym, street, spot, D=None, hist=None, excl=None):
    D = D or dt.date.today().isoformat()
    R = [r for r in reports(sym, hist, excl) if r['date'] < D]
    use = [r for r in R if not r['unusable']][-N_OWN:]
    own = statistics.median(r['s'] for r in use) if len(use) >= N_OWN_MIN else None
    sec, n_sec, sec_name = sector_beat(sym, D, hist, excl)
    if own is not None and sec is not None: beat, basis = W_OWN * own + (1 - W_OWN) * sec, f'1/2 own median of {len(use)} + 1/2 {sec_name} beat'
    elif sec is not None: beat, basis = sec, f'{sec_name} beat only (own needs {N_OWN_MIN} usable reports, has {len(use)})'
    elif own is not None: beat, basis = own, 'own median only (no sector pool)'
    else: beat, basis = None, 'no beat history: Street stands in'
    spread = max(statistics.mean(abs(r['s'] - own) for r in use), SPREAD_FLOOR) if own is not None else None
    out = dict(sym=sym, date=D, street_eps=street, spot=spot, own_beat=own, own_reports=[(r['date'], round(r['s'], 5)) for r in use],
               dropped=[(r['date'], r['unusable']) for r in R[-(N_OWN + 4):] if r['unusable']],
               sector_beat=sec, sector_reports=n_sec, sector=sec_name, beat=beat, basis=basis, spread=spread,
               whisper_eps=None, beat_pct_of_street=None, range50=None, range80=None)
    if street is None or spot is None: return out
    if beat is None:
        out['whisper_eps'] = street; return out
    w = street + beat * spot
    out.update(whisper_eps=round(w, 4), beat_pct_of_street=round(100 * (w / street - 1), 2) if street else None)
    sc = (spread if spread is not None else SPREAD_FLOOR * 2) * spot
    out['range50'] = (round(w + Z50[0] * sc, 4), round(w + Z50[1] * sc, 4))
    out['range80'] = (round(w + Z80[0] * sc, 4), round(w + Z80[1] * sc, 4))
    return out


def refresh(syms, max_age_days=95):
    """Pull the Nasdaq table (8 at a time) for names with no stored report in the last max_age_days and merge it into
    earnings_history.json. The nightly pre-print log calls it for every tracked name so the sector pools stay current."""
    import earnings_miss, concurrent.futures as cf
    hist = jl(HIST, {}); now = dt.datetime.now(dt.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    stale = [s for s in dict.fromkeys(syms) if not hist.get(s) or (dt.date.today() - dt.date.fromisoformat(max(hist[s]))).days > max_age_days]
    if not stale: return hist
    with cf.ThreadPoolExecutor(8) as ex: got = dict(zip(stale, ex.map(earnings_miss.fetch, stale)))
    hist = jl(HIST, {}); changed = False                      # re-read: another job may have written meanwhile
    for s, (rows, _, err) in got.items():
        for r in rows:
            if r['date_reported'] not in (hist.get(s) or {}):
                hist.setdefault(s, {})[r['date_reported']] = dict(r, first_seen=now); changed = True
        if err: print(f'{s}: {err}', file=sys.stderr)
    if changed:
        tmp = HIST + '.tmp'
        with open(tmp, 'w') as f: json.dump(hist, f, indent=1)
        os.replace(tmp, HIST)
    return hist


def main(a):
    def opt(k):
        if k in a:
            i = a.index(k); v = a[i + 1]; del a[i:i + 2]; return v
    street, spot, D = opt('--street'), opt('--spot'), opt('--date')
    do_refresh = '--refresh' in a; a = [x for x in a if x != '--refresh']
    if do_refresh: refresh(a)
    for s in a:
        print(json.dumps(whisper(s, float(street) if street else None, float(spot) if spot else None, D), indent=1))


if __name__ == '__main__':
    sys.path.insert(0, HERE); main(sys.argv[1:])
