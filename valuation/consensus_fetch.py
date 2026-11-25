"""Consensus feeds the gate checkers can open (Kilian 2026-11-24: "the checker needs to be able to access nasdaq api figure it
out", then "i approve the fetch script" after being told the feed's robots rule refuses automated fetching). WebFetch refuses
api.nasdaq.com (robots_disallowed) and auto mode refuses curl, so INTC and COHR round 1 (2026-11-23) lost 15 rows to
"not checked" consensus cites and INTC round 3 one more. A plain Python fetch (as nasdaq_est.py does) reaches both feeds.

Feeds: https://api.nasdaq.com/api/analyst/SYM/earnings-forecast (Zacks consensus EPS) and
       https://www.zacks.com/stock/quote/SYM/detailed-earning-estimates (Zacks EPS + sales tables).
Every fetch is archived to data/estimates/feeds/SYM_<feed>_<YYYYMMDDTHHMM>.json, so a cite can be checked against the feed as
of the day it was taken (consensus moves daily; the feeds carry no as-of date, so the fetch time is the as-of date).

  python3 consensus_fetch.py URL [URL ...]      print the feed (checkers: use this, never WebFetch, for these URLs)
  python3 consensus_fetch.py check URL "VALUE"  ok / no-match against the live feed and archived fetches

run_digest.py gate / gate-update calls auto(): consensus rows whose every figure is on the feed pass by script (published =
the fetch date, source_kind consensus); the rest go to a checker with the feed printed in the brief.
"""
import datetime as dt, glob, json, os, re, sys, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__)).replace('/mnt/attach/project-files', '/mnt/project-files')
ARCH = os.path.join(HERE, 'data', 'estimates', 'feeds')
UA = {'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/124 Safari/537.36', 'Accept': '*/*'}
FEEDS = [('nasdaq', re.compile(r'https?://api\.nasdaq\.com/api/analyst/([A-Za-z.\-]+)/earnings-forecast', re.I)),
         ('zacks', re.compile(r'https?://(?:www\.)?zacks\.com/stock/quote/([A-Za-z.\-]+)/detailed-earning-estimates', re.I))]
NUM = re.compile(r'(?<![\w.])-?\$?(\d{1,3}(?:,\d{3})+|\d+)(\.\d+)?\s*(%|[BMK]\b|bn\b)?')
_cache = {}


def feed_of(url):
    for name, rx in FEEDS:
        m = rx.search(url or '')
        if m: return name, m.group(1).upper()
    return None


def _get(u):
    return urllib.request.urlopen(urllib.request.Request(u, headers=UA), timeout=30).read().decode('utf-8', 'replace')


def _zacks_tables(html):
    out = []
    for h in re.findall(r'<h1>([^<]*Estimates)</h1>', html):
        if h == 'Detailed Estimates': continue
        i = html.find(f'<h1>{h}</h1>'); tbl = html[i:html.find('</table>', i)]
        heads = [re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', ' ', x)).strip() for x in re.findall(r'<th[^>]*>(.*?)</th>', tbl, re.S)]
        rows = []
        for tr in re.findall(r'<tr>(.*?)</tr>', tbl, re.S):
            cells = [re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', ' ', c)).strip() for c in re.findall(r'<td[^>]*>(.*?)</td>', tr, re.S)]
            if cells: rows.append(cells)
        if rows: out.append(dict(table=h, heads=heads, rows=rows))
    return out


def fetch(url):
    """Live feed -> {feed, symbol, url, fetched_utc, lines: [str]}; archived. Cached per process."""
    f = feed_of(url)
    if not f: raise ValueError(f'not a consensus feed URL: {url}')
    if url in _cache: return _cache[url]
    name, sym = f
    now = dt.datetime.now(dt.timezone.utc)
    lines = []
    if name == 'nasdaq':
        d = json.loads(_get(f'https://api.nasdaq.com/api/analyst/{sym}/earnings-forecast'))['data'] or {}
        for key in ('yearlyForecast', 'quarterlyForecast'):
            for r in (d.get(key) or {}).get('rows') or []:
                lines.append(f"{key} {r.get('fiscalEnd')}: consensus EPS {r.get('consensusEPSForecast')}, high {r.get('highEPSForecast')}, "
                             f"low {r.get('lowEPSForecast')}, estimates {r.get('noOfEstimates')}, up {r.get('up')}, down {r.get('down')}")
    else:
        for t in _zacks_tables(_get(f'https://www.zacks.com/stock/quote/{sym}/detailed-earning-estimates')):
            lines.append(f"{t['table']} columns: {' | '.join(t['heads'][1:] if len(t['heads']) > 1 else t['heads'])}")
            lines += [f"  {r[0]}: {' | '.join(r[1:])}" for r in t['rows']]
    rec = dict(feed=name, symbol=sym, url=url, fetched_utc=now.strftime('%Y-%m-%dT%H:%M:%SZ'), lines=lines,
               note='the feed states no as-of date; the fetch time is its as-of date')
    os.makedirs(ARCH, exist_ok=True)
    json.dump(rec, open(os.path.join(ARCH, f"{sym}_{name}_{now.strftime('%Y%m%dT%H%M')}.json"), 'w'), indent=1)
    _cache[url] = rec
    return rec


def archived(url):
    """Earlier fetches of this feed (newest first), plus data/estimates/SYM.json (nasdaq_est.py) as a dated fetch."""
    name, sym = feed_of(url)
    out = [json.load(open(p)) for p in sorted(glob.glob(os.path.join(ARCH, f'{sym}_{name}_*.json')), reverse=True)]
    snap = os.path.join(HERE, 'data', 'estimates', f'{sym}.json')
    if os.path.exists(snap):
        s = json.load(open(snap)); ls = []
        for r in s.get('records') or []:
            raw = r.get('raw_row') or {}
            if name == 'nasdaq' and raw:
                ls.append(f"{r.get('horizon')} {raw.get('fiscalEnd')}: consensus EPS {raw.get('consensusEPSForecast')}, high {raw.get('highEPSForecast')}, "
                          f"low {raw.get('lowEPSForecast')}, estimates {raw.get('noOfEstimates')}")
            if name == 'zacks' and r.get('revenue_estimate_average'):
                ls.append(f"{r.get('horizon')} {r.get('date')}: sales {r.get('revenue_estimate_average')}, high {r.get('revenue_estimate_high')}, "
                          f"low {r.get('revenue_estimate_low')}, estimates {r.get('revenue_estimate_analyst_count')}")
        if ls: out.append(dict(feed=name, symbol=sym, url=url, fetched_utc=s.get('retrieved_utc'), lines=ls, note='data/estimates snapshot (nasdaq_est.py)'))
    return out


def _feed_vals(lines):
    vals = []
    for ln in lines:
        for m in NUM.finditer(ln.split(':', 1)[-1]):
            try: vals.append(float((m.group(1) + (m.group(2) or '')).replace(',', '')))
            except ValueError: pass
    return vals


def _cited(value):
    """Figures to match: numbers in the cited value, without years, dates, periods and src ids."""
    v = re.sub(r'\b\d{4}-\d{2}-\d{2}\b|\b(?:FY|CY|Q[1-4])\s?\'?\d{2,4}\b|\bsrc\s?\d+\b|\b(?:19|20)\d{2}\b|\b\d{1,2}/\d{4}\b', ' ', value or '', flags=re.I)
    out = []
    for m in NUM.finditer(v):
        s = m.group(1).replace(',', ''); dec = m.group(2) or ''
        out.append((float(s + dec), len(dec) - 1 if dec else 0, (m.group(3) or '').strip()))
    return out


def _hit(x, nd, unit, vals):
    scales = {'B': (1e9,), 'bn': (1e9,), 'M': (1e6,), 'K': (1e3,), '%': (1,)}.get(unit, (1, 1e9, 1e6))
    for f in vals:
        for sc in scales:
            if round(f / sc, nd) == round(x, nd): return True
            if unit in ('B', 'bn', 'M') and abs(f / sc - x) <= 0.5 * 10 ** -nd: return True
    return False


def check(url, value):
    """(ok, fetch record used, unmatched figures). ok only when the value cites at least one figure and all are on one fetch."""
    cited = _cited(value)
    if not cited: return False, None, []
    recs = []
    try: recs.append(fetch(url))
    except Exception as e: print(f'consensus_fetch: live fetch failed for {url}: {str(e)[:120]}', file=sys.stderr)
    recs += archived(url)
    best = None
    for r in recs:
        vals = _feed_vals(r['lines']); miss = [x for x, nd, u in cited if not _hit(x, nd, u, vals)]
        if not miss: return True, r, []
        if best is None or len(miss) < len(best[1]): best = (r, miss)
    return (False, best[0], best[1]) if best else (False, None, [x for x, _, _ in cited])


def auto(groups):
    """run_digest gate prep: resolve consensus-feed URL groups by script. Returns (groups left, auto rows, {url: feed text})."""
    rows, shown, left = [], {}, {}
    for u, its in groups.items():
        if not feed_of(u): left[u] = its; continue
        try: live = fetch(u)
        except Exception as e:
            left[u] = its; shown[u] = f'    (live fetch failed: {str(e)[:120]}; run python3 {HERE}/consensus_fetch.py {u})'; continue
        pub = live['fetched_utc'][:10]; rest = []
        for it in its:
            if it.get('basis') == 'timeliness':  # the feed has no as-of date: its figures are those of the fetch
                rows.append(dict(it, result='ok', source_url=u, checked_by='consensus_fetch.py (live feed)', published=pub,
                                 source_kind='consensus', newer_found='', page_shows=f"live feed fetched {live['fetched_utc']}; the feed states no as-of date"))
                continue
            ok, r, _ = check(u, it.get('value', ''))
            if ok:
                rows.append(dict(it, result='ok', source_url=u, checked_by='consensus_fetch.py (live feed / dated fetch)', published=(r['fetched_utc'] or pub)[:10],
                                 source_kind='consensus', newer_found='', page_shows=f"every cited figure is on the feed as fetched {r['fetched_utc']}"))
            else:
                rest.append(it)
        if rest:
            left[u] = rest
            shown[u] = '\n'.join(f'    {ln}' for ln in live['lines'][:60]) + f"\n    (fetched {live['fetched_utc']}; earlier fetches: python3 {HERE}/consensus_fetch.py {u})"
    return left, rows, shown


if __name__ == '__main__':
    a = sys.argv[1:]
    if not a: print(__doc__); sys.exit(2)
    if a[0] == 'check':
        ok, r, miss = check(a[1], a[2])
        print('ok' if ok else 'no-match', f"(feed fetched {r['fetched_utc']})" if r else '', '' if ok else f'unmatched: {miss}')
        sys.exit(0 if ok else 1)
    for u in a:
        r = fetch(u)
        print(f"# {u}\n# fetched {r['fetched_utc']} ({r['note']})"); print('\n'.join(r['lines']))
        for old in archived(u)[1:4]:
            print(f"# earlier: {old['fetched_utc']} ({old.get('note', 'archived fetch')}):"); print('\n'.join(f'  {x}' for x in old['lines'][:20]))
