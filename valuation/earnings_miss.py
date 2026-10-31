"""Big-miss flag (Kilian 2026-10-19 15:21Z: "earnings misses beyond the usual range should be flagged and investigated but not
hard block a name"; MODE = 'flag', RULE = 'usual'). Was the big-miss add block (card "Add flag"); MODE = 'block' restores it.

What it does. For each name it covers, it reads reported quarterly EPS and the consensus forecast for the last four reports
from Nasdaq.com's earnings-surprise table (Zacks consensus; free, no key, the same kind of public JSON feed the project
already reads from Yahoo and Cboe), keeps every report it has ever seen in data/earnings_history.json (Nasdaq shows only the
last four, so the history grows by one report a quarter), and writes data/earnings_miss.json:
    {SYM: {last_report_date, fiscal_quarter, eps, consensus, surprise, surprise_pct_price, usual_range, big_miss,
           block_from, block_until, blocked, reason, source, next_report, history}}  plus a "_meta" record.
allocate.py (new buys, staged new names) and review.py (adds to held names, staged second tranches, next-share add levels)
read it through blocks(D): a blocked name gets no adds. Trims, exits and stops are unchanged.

Definition (same scaling as the evidence code, wrds/code/surprise_ibes.py and usual_beat.py):
    s = (reported EPS - consensus EPS) / close at the month-end before the report month       (split-adjusted closes)
    big miss  <=>  s <= -0.0005, i.e. EPS below consensus by at least 0.05% of the share price.
-0.0005 is the bottom-fifth cut-off of s among $1B+ reporters, the "miss" group of surprise_ibes.py: the median monthly
20th percentile was -0.00044 in 2000-2012, -0.00050 in 2013-2020 and -0.00052 in 2021-2025 (I/B/E/S, survivor-free CRSP).
With this fixed cut-off the 2013-2025 miss group did -1.30% vs all reporters over the next 6 months (t -2.13, 17,126 reports,
21% of reports); 2000-2012 +0.27% (t 0.36), no effect. (wrds/code/big_miss_rule.py -> wrds/results/big_miss_rule.csv.)
The name's usual surprise range is reported next to it, computed as usual_beat.py does: usual = median s over the name's
previous 8 reports (at least 4 needed), range = [min, max] of those s. It is information, not the trigger (RULE = 'price'),
because a per-name rule ("below the lowest of its last 8") carried no reliable drift in the same test: -0.67% (t -0.92).
RULE = 'usual' switches the trigger to that per-name rule (s < 0 and s below the range's low, >= 4 previous reports).

Block. A big miss reported on date R blocks adds from R until R + 6 calendar months (block_until, the first day adds are
allowed again), so blocked at market date D  <=>  R <= D < block_until. Any big miss in the 6 months before D counts, not
only the latest report. Reports dated after D are ignored (re-runs for old dates).

Live data never comes from WRDS: the WRDS work only fixed the cut-off (an aggregate) and the evidence.

Usage: python3 earnings_miss.py D [SYM ...] [--active] [--offline] [--rule price|usual]
  D          market date (YYYY-MM-DD) the block is evaluated at
  SYM ...    names to cover; default: held names (book.py) + staged_entries + limit_orders + memo_extra
             + the newest portfolio.json's committed buys, Sharpe-test candidates and suggested buys (allocation shares > 0)
  --active   also every active name (active_names.py D)
  --offline  no network: recompute from data/earnings_history.json only
Library: blocks(D) -> {SYM: record} for the names blocked at D (empty when the file is missing or unreadable).
"""
import calendar, concurrent.futures as cf, csv, datetime as dt, glob, json, os, statistics, sys, time, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__)).replace('/mnt/attach/project-files', '/mnt/project-files')
OUT = os.path.join(HERE, 'data', 'earnings_miss.json')
HIST = os.path.join(HERE, 'data', 'earnings_history.json')
CUTOFF = -0.0005          # s <= CUTOFF is a big miss (bottom fifth of $1B+ reporters' price-scaled surprise)
BLOCK_MONTHS = 6
USUAL_N, USUAL_MIN = 8, 4  # usual_beat.py: previous 8 reports, at least 4
RULE = 'usual'             # Kilian 2026-10-19 15:21Z: misses beyond the name's usual range (was 'price', the backtested rule)
MODE = 'flag'              # Kilian 2026-10-19 15:21Z: 'flagged and investigated but not hard block'; 'block' restores the add block
FLAG_DAYS = 120            # a flag stays up until the next report or this many days, whichever comes first
UA = {'User-Agent': 'Mozilla/5.0', 'Accept': 'application/json'}
SRC_URL = 'https://api.nasdaq.com/api/company/{sym}/earnings-surprise'
DATE_URL = 'https://api.nasdaq.com/api/analyst/{sym}/earnings-date'
SOURCE = 'Nasdaq.com earnings surprise table (reported EPS vs Zacks consensus)'


def add_months(d, n):
    y, m = divmod(d.month - 1 + n, 12); y += d.year; m += 1
    return dt.date(y, m, min(d.day, calendar.monthrange(y, m)[1]))


def jload(p, default):
    try:
        with open(p) as f: return json.load(f)
    except Exception: return default


def get_json(url, tries=3):
    for k in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=30) as r: return json.load(r)
        except Exception as e:
            err = e; time.sleep(1.5 * (k + 1))
    raise err


def fnum(x):
    try:
        v = float(str(x).replace('$', '').replace(',', '')); return v if v == v else None
    except (TypeError, ValueError): return None


def mdy(x):
    try: return dt.datetime.strptime(x.strip(), '%m/%d/%Y').date().isoformat()
    except Exception: return None


def fetch(sym):
    """Last four reports from Nasdaq plus the next expected report date. Returns (rows, next_report, error)."""
    rows, nxt, err = [], None, None
    try:
        d = get_json(SRC_URL.format(sym=sym))
        for r in (((d.get('data') or {}).get('earningsSurpriseTable') or {}).get('rows') or []):
            rd, eps, con = mdy(r.get('dateReported') or ''), fnum(r.get('eps')), fnum(r.get('consensusForecast'))
            if rd and eps is not None and con is not None:
                rows.append(dict(fiscal_quarter=r.get('fiscalQtrEnd'), date_reported=rd, eps=eps, consensus=con,
                                 source=SOURCE, url=SRC_URL.format(sym=sym)))
        if not rows: err = 'no rows in the Nasdaq table'
    except Exception as e: err = f'Nasdaq fetch failed: {e}'
    try:
        t = ((get_json(DATE_URL.format(sym=sym)).get('data') or {}).get('reportText') or '')
        import re
        m = re.search(r'report earnings on\s+(\d{1,2}/\d{1,2}/\d{4})(?:\s+(after market close|before market open))?', t)
        if m: nxt = dict(date=mdy(m.group(1)), timing=m.group(2), text=' '.join(t.split()))
        elif t: nxt = dict(date=None, timing=None, text=' '.join(t.split()))
    except Exception: pass
    return rows, nxt, err


def month_end_close(sym, report_date):
    """Last close in the month before the report month (the evidence code's price: prior month-end, split-adjusted)."""
    first = report_date[:8] + '01'
    for d in (os.path.join(HERE, 'data'), os.path.join(HERE, '..', 'portfolio', 'data')):
        p = os.path.join(d, f'daily_{sym}.csv')
        if not os.path.exists(p): continue
        best = None
        with open(p) as f:
            for r in csv.DictReader(f):
                if r['date'] < first and r.get('close'): best = (r['date'], float(r['close']))
        if best: return best
    return None


def default_names(D):
    sys.path.insert(0, HERE)
    cfg = jload(os.path.join(HERE, 'data', 'review_settings.json'), {})
    names = set()
    try:
        import book; names |= {p['symbol'] for p in book.load()['positions'] if p.get('shares')}
    except Exception as e: print('book.load failed, held names from trades.json skipped:', e, file=sys.stderr)
    names |= {k for k, v in (cfg.get('staged_entries') or {}).items() if isinstance(v, dict)}
    names |= set(cfg.get('limit_orders') or {}) | set(cfg.get('memo_extra') or [])
    runs = sorted(d for d in os.listdir(os.path.join(HERE, 'runs')) if len(d) == 10 and d <= D and os.path.exists(os.path.join(HERE, 'runs', d, 'portfolio.json')))
    if runs:
        pf = jload(os.path.join(HERE, 'runs', runs[-1], 'portfolio.json'), {})
        sc = pf.get('sharpe_combo') or {}
        names |= set(sc.get('committed') or {}) | set(sc.get('candidates') or {}) | {k for k, v in (pf.get('shares') or {}).items() if v}
    idx = (cfg.get('index_fund') or {}).get('symbol')
    return sorted(n for n in names if n and n != idx)


def evaluate(sym, hist, D, rule=None):
    """Record for one name from its stored reports (dated <= D)."""
    rule = rule or RULE
    rows = sorted((r for r in hist.values() if r['date_reported'] <= D), key=lambda r: r['date_reported'])
    if not rows: return dict(symbol=sym, big_miss=False, blocked=False, reason='no reported EPS vs consensus on file', source=SOURCE)
    ev = []
    for r in rows:
        px = month_end_close(sym, r['date_reported'])
        s = (r['eps'] - r['consensus']) / px[1] if px and px[1] > 0 else None
        ev.append(dict(r, price_date=px and px[0], price=px and round(px[1], 4), s=None if s is None else round(s, 6)))
    for i, e in enumerate(ev):
        prev = [x['s'] for x in ev[max(0, i - USUAL_N):i] if x['s'] is not None]
        e['n_prev'] = len(prev)
        e['usual'] = round(statistics.median(prev), 6) if len(prev) >= USUAL_MIN else None
        e['range_low'] = round(min(prev), 6) if len(prev) >= USUAL_MIN else None
        e['range_high'] = round(max(prev), 6) if len(prev) >= USUAL_MIN else None
        e['big_miss_price_rule'] = e['s'] is not None and e['s'] <= CUTOFF
        e['below_usual_range'] = (e['s'] < 0 and e['s'] < e['range_low']) if (e['s'] is not None and e['range_low'] is not None) else None
        e['big_miss'] = e['big_miss_price_rule'] if rule == 'price' else bool(e['below_usual_range'])
        e['block_until'] = add_months(dt.date.fromisoformat(e['date_reported']), BLOCK_MONTHS).isoformat() if e['big_miss'] else None
    last = ev[-1]
    active = [e for e in ev if e['big_miss'] and e['date_reported'] <= D < e['block_until']]
    blk = active[-1] if active else None
    def desc(e):
        return (f"{e['fiscal_quarter']} EPS ${e['eps']:.2f} vs ${e['consensus']:.2f} consensus, "
                f"{e['s'] * 100:+.2f}% of the ${e['price']:.2f} share price; big = at least 0.05% below")
    def why(e): return f"big earnings miss on {e['date_reported']} ({desc(e)}): adds paused until {e['block_until']}"
    # flag mode: the latest report is a miss beyond the usual range, reported within FLAG_DAYS of D
    fl = last if (last['big_miss'] and (dt.date.fromisoformat(D) - dt.date.fromisoformat(last['date_reported'])).days <= FLAG_DAYS) else None
    if MODE == 'flag': blk = None
    prev_miss = ([e for e in ev if e['big_miss']] or [None])[-1]
    out = dict(symbol=sym, as_of=D, rule=rule, cutoff=CUTOFF,
               last_report_date=last['date_reported'], fiscal_quarter=last['fiscal_quarter'], eps=last['eps'], consensus=last['consensus'],
               surprise=round(last['eps'] - last['consensus'], 4),
               surprise_pct_consensus=round((last['eps'] - last['consensus']) / abs(last['consensus']), 4) if last['consensus'] else None,
               surprise_pct_price=last['s'], price_basis=dict(date=last['price_date'], close=last['price']),
               usual_range=dict(usual=last['usual'], low=last['range_low'], high=last['range_high'], n_prev=last['n_prev'],
                                note=f'median and [min, max] of s over the previous {USUAL_N} reports on file ({USUAL_MIN} needed); usual_beat.py definition; information only' if rule == 'price' else 'trigger'),
               below_usual_range=last['below_usual_range'],
               big_miss=last['big_miss'], block_until=blk['block_until'] if blk else last['block_until'],
               block_from=blk['date_reported'] if blk else None, blocked=bool(blk), mode=MODE,
               flagged=bool(fl) if MODE == 'flag' else False,
               investigate=bool(fl) and (dt.date.fromisoformat(D) - dt.date.fromisoformat(fl['date_reported'])).days <= 7 if MODE == 'flag' else False,
               reason=why(blk) if blk else (f"earnings miss beyond its usual range on {fl['date_reported']} ({desc(fl).split('; big')[0]}; usual range {fl['range_low'] * 100:+.2f}% to {fl['range_high'] * 100:+.2f}%): flagged for investigation, adds not blocked" if (MODE == 'flag' and fl) else (
                   f"no add block at {D}: last report {last['date_reported']} "
                   + (f"{last['s'] * 100:+.2f}% of price" if last['s'] is not None else '(no price on file)')
                   + (f"; last big miss on {prev_miss['date_reported']} ({desc(prev_miss)})" + (f", block ended {prev_miss['block_until']}" if MODE == 'block' else '') if prev_miss else ''))),
               source=last.get('source', SOURCE), url=last.get('url'),
               history=[{k: e[k] for k in ('fiscal_quarter', 'date_reported', 'eps', 'consensus', 's', 'big_miss', 'source')} for e in ev[-USUAL_N:]])
    return out


def blocks(D):
    """Names whose adds are blocked at market date D, from data/earnings_miss.json: {SYM: record}. Never raises.
    Empty in flag mode (MODE = 'flag', Kilian 2026-10-19): a miss is flagged for investigation, not blocked.
    Plus expected-miss holds (expected_miss.py, Kilian 2026-10-30 card 'Hold adds before expected misses'): adds wait until after a print
    our model expects to miss the Street by 5% or more."""
    try:
        import expected_miss; xm = expected_miss.blocks(D)
    except Exception as e: xm = {}; print('expected_miss.blocks failed:', e, file=sys.stderr)
    if MODE != 'block': return xm
    try:
        d = jload(OUT, {})
        out = {}
        for s, r in d.items():
            if s.startswith('_') or not isinstance(r, dict): continue
            if r.get('block_from') and r.get('block_until') and r['block_from'] <= D < r['block_until']: out[s] = r
        return {**xm, **out}
    except Exception as e:
        print('earnings_miss.blocks failed, no big-miss blocks applied:', e, file=sys.stderr); return {}


def flags(D):
    """Names flagged at D (flag mode): {SYM: record}. Never raises."""
    try: return {s: r for s, r in jload(OUT, {}).items() if not s.startswith('_') and isinstance(r, dict) and r.get('flagged')}
    except Exception: return {}


def main(a):
    rule = RULE
    if '--rule' in a: i = a.index('--rule'); rule = a[i + 1]; a = a[:i] + a[i + 2:]
    offline, active = '--offline' in a, '--active' in a
    pos = [x for x in a if not x.startswith('--')]
    if not pos: raise SystemExit(__doc__.split('Usage:')[1].split('Library:')[0])
    D = pos[0]; dt.date.fromisoformat(D)
    names = [s.upper() for s in pos[1:]] or default_names(D)
    if active:
        sys.path.insert(0, HERE); import active_names
        names = sorted(set(names) | set(active_names.active_names(D)))
    hist = jload(HIST, {}); old = jload(OUT, {})
    fetched, nxt, errs = {}, {}, {}
    if not offline:
        with cf.ThreadPoolExecutor(6) as ex:
            for s, (rows, n, e) in zip(names, ex.map(fetch, names)):
                fetched[s], nxt[s] = rows, n
                if e: errs[s] = e
    now = dt.datetime.now(dt.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    for s in names:
        h = hist.setdefault(s, {})
        for r in fetched.get(s, []):
            k = r['date_reported']
            if k not in h: h[k] = dict(r, first_seen=now)
            elif (h[k]['eps'], h[k]['consensus']) != (r['eps'], r['consensus']): h[k] = dict(r, first_seen=h[k].get('first_seen'), revised=now)
    out = {k: v for k, v in old.items() if not k.startswith('_') and k not in names}  # names not covered today keep their record
    for s in names:
        rec = evaluate(s, hist.get(s, {}), D, rule)
        rec['next_report'] = nxt.get(s) or (old.get(s) or {}).get('next_report')
        rec['fetched_utc'] = now if fetched.get(s) else (old.get(s) or {}).get('fetched_utc')
        if errs.get(s): rec['fetch_error'] = errs[s]
        out[s] = rec
    for s, r in out.items():  # re-evaluate the block window of carried-over records at D
        if s not in names and r.get('block_from') and r.get('block_until'): r['blocked'] = r['block_from'] <= D < r['block_until']
    meta = dict(as_of=D, written_utc=now, rule=rule, mode=MODE, flag_days=FLAG_DAYS, cutoff=CUTOFF, block_months=BLOCK_MONTHS, names=names,
                blocked=sorted(s for s, r in out.items() if r.get('blocked')), fetch_errors=errs,
                definition='big miss: (reported EPS - consensus) / prior month-end close <= -0.0005 (EPS below consensus by at least '
                           '0.05% of the share price; bottom fifth of $1B+ reporters). Adds (new buys, adds to held names, staged '
                           'tranches) pause for 6 months from the report date; trims, exits and stops unchanged. Kilian 2026-10-19 card "Add flag".',
                source=SOURCE)
    tmp = OUT + '.tmp'; json.dump(dict(_meta=meta, **{s: out[s] for s in sorted(out)}), open(tmp, 'w'), indent=1); os.replace(tmp, OUT)
    tmp = HIST + '.tmp'; json.dump(hist, open(tmp, 'w'), indent=1); os.replace(tmp, HIST)
    for s in names:
        r = out[s]
        print(f"{s:6} {'BLOCKED' if r.get('blocked') else 'ok     '} last {r.get('last_report_date')} {r.get('fiscal_quarter') or ''} "
              f"EPS {r.get('eps')} vs {r.get('consensus')} s={r.get('surprise_pct_price')} usual={(r.get('usual_range') or {}).get('usual')} "
              f"n_prev={(r.get('usual_range') or {}).get('n_prev')} next={((r.get('next_report') or {}).get('date'))}"
              + (f"  [{errs[s]}]" if s in errs else ''))
    print('blocked at', D, ':', meta['blocked'] or 'none')
    if errs: print('fetch errors (kept the stored history):', errs)


if __name__ == '__main__':
    main(sys.argv[1:])
