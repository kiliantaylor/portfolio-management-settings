"""Nightly EDGAR deal-filing scan for held and bench names (special situations playbook, Kilian 2026-11-16, all
recommended options; doc https://claude.ai/artifact/Q5a8R1N1oMAHWpWa3X8gk9 "Detection" item 2).

Usage:  python3 deal_filings.py [D] [--also SYM ...] [--days N] [--dry-run]
  D        market date of the nightly run (default today); only used to stamp the file
  --also   extra names to scan (e.g. a deal target or acquirer we track that is neither held nor on the bench)
  watch    data/deal_filings.json "watch" {SYM: why} is scanned too: every name with a live deal_events entry that is
           neither held nor bench (e.g. LITE) and its acquirer (MRVL); add a name when its deal entry is written, remove it
           when the deal closes or breaks
  --days   look-back window in calendar days for filings never seen before (default 7; the seen list stops repeats)

What it reads: data.sec.gov/submissions/CIK##########.json for each name (the company's own filings AND filings by others
that name it as subject company, e.g. a bidder's SC TO-T or an activist's 13D). Forms that count:
  8-K with Item 1.01 (material definitive agreement) or Item 1.02 (termination of one, a T6 break signal)
  SC TO-T (third-party tender offer), SC 14D9 (target's response), PREM14A/DEFM14A (merger proxy), S-4 (stock deal
  registration), 425 (deal communications), 10-12B/10-12G (Form 10 spin-off), SC 13D / SCHEDULE 13D (5%+ active holder);
  amendments (/A) included.
For an 8-K 1.01/1.02, EDGAR full-text search (efts.sec.gov) checks the filing and its exhibits for deal language
("agreement and plan of merger", "tender offer", "separation and distribution" ...), because most 1.01s are credit
agreements; deal_language is True, False, or None (search failed or filed in the last day, so the index may lag; a None
8-K is re-checked next run and must be read by hand meanwhile). A hit is only a lead:
the model reads it, sets the playbook type (T1..T8) and starts the fast lane if it is a held name.

A fetch that fails is logged under `misses` and NEVER read as "nothing filed" (sec.gov blocks some automated fetches).
Generic User-Agent with no personal details (privacy rule), same as secfacts.py.

Output: data/deal_filings.json (rolling; no per-run file, so no retention rule is needed):
  as_of, scanned [SYM], misses [{sym, error}], hits [new hits this run], recent [hits of the last 30 days],
  seen {accession: filing date} (kept 120 days so a filing is reported once).
Prints {as_of, scanned, new_hits, misses} as JSON."""
import datetime as dt, hashlib, json, os, subprocess, sys, time, urllib.parse

HERE = os.path.dirname(os.path.abspath(__file__)).replace('/mnt/attach/project-files', '/mnt/project-files')
OUT = os.path.join(HERE, 'data', 'deal_filings.json')
UA = 'valuation-research script'  # sec.gov only; no personal details
CACHE = '/tmp/deal_filings_cache'

# form -> (what it is, likely playbook type when the filing is about this name)
FORMS = {
    'SC TO-T': ('third-party tender offer', 'T3/T4'), 'SC TO-T/A': ('tender offer amendment', 'T3/T4/T5'),
    'SC 14D9': ("target's response to a tender offer", 'T3/T4'), 'SC 14D9/A': ('tender response amendment', 'T3/T4/T5'),
    'PREM14A': ('preliminary merger proxy', 'T3/T4'), 'DEFM14A': ('definitive merger proxy', 'T3/T4'),
    'S-4': ('stock-deal registration', 'T4 (or T7 if our name is the buyer)'), 'S-4/A': ('stock-deal registration amendment', 'T4/T7'),
    '425': ('deal communication', 'T3/T4/T7'),
    '10-12B': ('Form 10 spin-off registration', 'T8'), '10-12B/A': ('Form 10 amendment', 'T8'),
    '10-12G': ('Form 10 spin-off registration', 'T8'), '10-12G/A': ('Form 10 amendment', 'T8'),
    'SC 13D': ('5%+ active holder (13D)', 'T1/T2 lead'), 'SC 13D/A': ('13D amendment', 'T1/T2 lead'),
    'SCHEDULE 13D': ('5%+ active holder (13D)', 'T1/T2 lead'), 'SCHEDULE 13D/A': ('13D amendment', 'T1/T2 lead'),
}
ITEMS_8K = {'1.01': ('material definitive agreement', 'T3/T4/T7 if a deal'), '1.02': ('termination of a material agreement', 'T6 if a deal')}
# strong phrases only: "tender offer", "spin-off", "business combination" and "all outstanding shares" are boilerplate in
# notes indentures and credit agreements (META and BKNG debt 8-Ks matched them on 2026-11-16 tests)
DEAL_WORDS = ['agreement and plan of merger', 'merger agreement', 'plan of merger', 'separation and distribution agreement',
              'acquire all of the outstanding', 'offer to purchase all', 'per share in cash', 'exchange ratio',
              'termination of the merger', 'terminate the merger']


def curl(url, path, max_age=6 * 3600):
    os.makedirs(CACHE, exist_ok=True)
    if os.path.exists(path) and time.time() - os.path.getmtime(path) < max_age: return open(path, 'rb').read()
    r = subprocess.run(['curl', '-sS', '--max-time', '40', '-A', UA, '-o', path + '.tmp', '-w', '%{http_code}', url], capture_output=True, text=True)
    time.sleep(0.15)  # well under sec.gov's 10 requests a second
    if r.stdout.strip() != '200':
        if os.path.exists(path + '.tmp'): os.remove(path + '.tmp')
        raise RuntimeError(f'HTTP {r.stdout.strip() or r.stderr.strip()[:200]}')
    os.replace(path + '.tmp', path)
    return open(path, 'rb').read()


def names(extra, watch=None):
    sys.path.insert(0, HERE); import book
    held = sorted(p['symbol'] for p in book.load().get('positions') or [] if p.get('shares'))
    try: bench = json.load(open(os.path.join(HERE, 'data', 'bench.json'))).get('names') or []
    except Exception: bench = []
    out, role = [], {}
    for s, r in [(s, 'held') for s in held] + [(s, 'bench') for s in bench] + [(s, 'watch') for s in sorted(watch or {})] + [(s.upper(), 'also') for s in extra]:
        if s not in role: out.append(s); role[s] = r
    return out, role


def efts_deal_accns(cik, since, until):
    """Accession numbers of this CIK's 8-Ks (main document or exhibits) that contain any deal phrase, from EDGAR full-text
    search (efts.sec.gov answers the generic User-Agent; www.sec.gov Archives returns 403 without a contact address, so the
    documents themselves are not read). Raises on a failed fetch."""
    q = urllib.parse.quote(' OR '.join(f'"{w}"' for w in DEAL_WORDS))
    url = f'https://efts.sec.gov/LATEST/search-index?q={q}&ciks={cik:010d}&forms=8-K&dateRange=custom&startdt={since}&enddt={until}'
    d = json.loads(curl(url, os.path.join(CACHE, f'efts_{cik:010d}_{since}_{until}_{hashlib.md5(q.encode()).hexdigest()[:8]}.json'), max_age=3 * 3600))
    return {h.get('_source', {}).get('adsh') or h.get('_id', '').split(':')[0] for h in d.get('hits', {}).get('hits') or []}


def scan(D, extra, days, dry):
    cmap = json.load(open(os.path.join(HERE, 'data', 'cik_map.json')))
    try: st = json.load(open(OUT))
    except Exception: st = {}
    seen = st.get('seen') or {}
    since = (dt.date.fromisoformat(D) - dt.timedelta(days=days)).isoformat()
    watch = st.get('watch') or {}
    syms, role = names(extra, watch)
    hits, misses, efts = [], [], {}
    lag = (dt.date.fromisoformat(D) - dt.timedelta(days=1)).isoformat()
    for s in syms:
        c = (cmap.get(s) or {}).get('cik')
        if not c: misses.append(dict(sym=s, error='no CIK in data/cik_map.json')); continue
        try: sub = json.loads(curl(f'https://data.sec.gov/submissions/CIK{c:010d}.json', os.path.join(CACHE, f'sub_{c:010d}.json')))
        except Exception as e: misses.append(dict(sym=s, error=f'submissions fetch failed: {e}')); continue
        r = sub.get('filings', {}).get('recent', {})
        for i, form in enumerate(r.get('form') or []):
            fd, accn = r['filingDate'][i], r['accessionNumber'][i]
            if fd < since or accn in seen: continue
            doc = r['primaryDocument'][i]; items = [x.strip() for x in (r.get('items') or [''] * (i + 1))[i].split(',') if x.strip()]
            base = f'https://www.sec.gov/Archives/edgar/data/{c}/{accn.replace("-", "")}/'
            h = None
            if form in FORMS:
                what, typ = FORMS[form]
                h = dict(sym=s, role=role[s], form=form, filed=fd, accession=accn, what=what, likely_type=typ, url=base + doc, index=base)
            elif form in ('8-K', '8-K/A') and any(it in ITEMS_8K for it in items):
                its = [it for it in items if it in ITEMS_8K]
                h = dict(sym=s, role=role[s], form=form, filed=fd, accession=accn, items=its,
                         what='; '.join(ITEMS_8K[it][0] for it in its), likely_type='; '.join(ITEMS_8K[it][1] for it in its),
                         url=base + doc, index=base)
                try:
                    if c not in efts: efts[c] = efts_deal_accns(c, since, D)
                    h['deal_language'] = accn in efts[c]
                except Exception as e:
                    h['deal_language'] = None; h['deal_language_note'] = f'full-text search failed ({e}): read it by hand, do not treat as no deal'
                if h['deal_language'] is False and fd >= lag:  # the full-text index can lag a day: re-check tomorrow before calling it no deal
                    h['deal_language'] = None; h['deal_language_note'] = 'filed in the last 2 days, full-text index may lag: re-checked next run'
                    hits.append(h); continue  # not marked seen
            if h:
                hits.append(h)
            seen[accn] = fd  # every filing in the window is marked seen, so the window can stay wide without repeats
    keep = (dt.date.fromisoformat(D) - dt.timedelta(days=120)).isoformat()
    seen = {a: d for a, d in seen.items() if d >= keep}
    r30 = (dt.date.fromisoformat(D) - dt.timedelta(days=30)).isoformat()
    recent = list({h['accession']: h for h in [h for h in (st.get('recent') or []) if h.get('filed', '') >= r30] + hits}.values())  # a lagged 8-K re-checked tomorrow replaces its row
    out = dict(as_of=D, watch=watch, scanned=syms, misses=misses, hits=hits, recent=recent, seen=seen,
               _why='Kilian 2026-11-16 special situations playbook (Detection 2): nightly EDGAR deal-filing scan; a miss is logged, never read as nothing filed')
    if not dry:
        with open(OUT + '.tmp', 'w') as f: json.dump(out, f, indent=1)
        os.replace(OUT + '.tmp', OUT)
    return out


def flag(h):
    """True when a hit needs the model to read it tonight: every non-8-K deal form, and 8-Ks with deal words or a failed read."""
    return h['form'] not in ('8-K', '8-K/A') or h.get('deal_language') is None or bool(h.get('deal_language'))


def watch_add(sym, acquirer=None, why=''):
    """Add a deal name (and its acquirer) to data/deal_filings.json `watch` so the nightly scan covers it (called by
    deal_odds.py when it writes an entry for a name that is neither held nor bench). Returns the watch dict."""
    try: st = json.load(open(OUT))
    except Exception: st = {}
    w = st.setdefault('watch', {})
    w[sym.upper()] = why or w.get(sym.upper()) or 'deal_events entry'
    if acquirer: w[acquirer.upper()] = w.get(acquirer.upper()) or f'acquirer of {sym.upper()}'
    with open(OUT + '.tmp', 'w') as f: json.dump(st, f, indent=1)
    os.replace(OUT + '.tmp', OUT)
    return w


def watch_remove(sym):
    """Drop a name from `watch` when its deal closes or breaks."""
    st = json.load(open(OUT)); st.get('watch', {}).pop(sym.upper(), None)
    with open(OUT + '.tmp', 'w') as f: json.dump(st, f, indent=1)
    os.replace(OUT + '.tmp', OUT)


def to_read(hits):
    """One line per name/form/day for the hits the model must read (425s come in batches of a dozen on a signing day)."""
    from collections import Counter
    n = Counter((h['sym'], h['form'], h['filed']) for h in hits if flag(h))
    return [f"{s} {f} {d}" + (f" x{k}" if k > 1 else '') for (s, f, d), k in sorted(n.items(), key=lambda x: (x[0][2], x[0][0]), reverse=True)]


if __name__ == '__main__':
    a = sys.argv[1:]; dry = '--dry-run' in a; a = [x for x in a if x != '--dry-run']
    days = 7
    if '--days' in a: i = a.index('--days'); days = int(a[i + 1]); del a[i:i + 2]
    extra = []
    if '--also' in a: i = a.index('--also'); extra = a[i + 1:]; a = a[:i]
    D = a[0] if a else dt.date.today().isoformat()
    o = scan(D, extra, days, dry)
    print(json.dumps(dict(as_of=o['as_of'], scanned=len(o['scanned']), new_hits=[{k: h.get(k) for k in ('sym', 'role', 'form', 'filed', 'what', 'likely_type', 'deal_language', 'url')} for h in o['hits']],
                          to_read=to_read(o['hits']), misses=o['misses']), indent=1))
