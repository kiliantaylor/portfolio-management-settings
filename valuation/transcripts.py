"""Earnings-call transcripts by script (Kilian 2026-11-05: "could you automate downloads of the page").

  python3 transcripts.py get SYM [PRINT_DATE]   fetch the call transcript for the print on PRINT_DATE (default: SYM's latest print in
                                                data/earnings_reruns.json); prints one JSON line; exit 0 saved / 2 not out yet
  python3 transcripts.py due D                  try every print from the last 10 days with no transcript saved (only names with a
                                                driver model, name_update.driver_model; dates from earnings_reruns.json, earnings_miss.json, EDGAR 8-K 2.02;
                                                NIGHTLY 4e1); prints JSON {saved, waiting, ask_kilian, errors}
  python3 transcripts.py add SYM PRINT_DATE PDF  save a transcript PDF Kilian downloaded (text via pdftotext) as the file on record

Source: The Motley Fool transcript pages (fool.com/earnings/call-transcripts). Its robots.txt allows these pages for every user agent
and its content signal says ai-input=yes, so a plain scripted read is within its stated terms; no browser disguise, no bot-check
workaround. Yahoo Finance transcripts are NOT used: Yahoo blocks automated reads, and we do not get around bot checks.
Lag: morning calls are usually up the same afternoon, evening calls by the next day; a few have taken up to ~5 days (NVDA, SNOW
Aug-Sep 2026). Not covered in Jul-Nov 2026: APP, DY. Kilian 11-05 ("too slow, tell me what to download"): when fool.com does not
have it on the first night the print is out, `ask_kilian` lists it once (state data/transcripts_asked.json) and the nightly tells him
which call to download; his PDF goes in with `add`.

Saved to notes/transcripts/SYM_<print date>.md with the source URL and Fool's publish time at the top (cite the URL as the source).
Retention: the last 4 transcripts per name are kept; older ones move to _trash_<today>/ in project files (prune.py rule).
Fetch cost: one sitemap (~200 KB) per month searched plus one page per hit, curl only (no WebFetch budget), UA 'valuation-research script'."""
import datetime as dt, html, json, os, re, shutil, subprocess, sys
VAL = os.path.dirname(os.path.abspath(__file__)).replace('/mnt/attach/project-files', '/mnt/project-files')
OUT = os.path.join(VAL, 'notes', 'transcripts')
ST = os.path.join(VAL, 'data', 'earnings_reruns.json')
EM = os.path.join(VAL, 'data', 'earnings_miss.json')
ASKED = os.path.join(VAL, 'data', 'transcripts_asked.json')
DM = os.path.join(VAL, 'driver_models')
UA = 'valuation-research script'
KEEP = 4
DUE_DAYS = 10

def curl(url):
    r = subprocess.run(['curl', '-sS', '-m', '40', '--retry', '2', '-A', UA, '-w', '\n%{http_code}', url], capture_output=True, text=True)
    body, _, code = r.stdout.rpartition('\n')
    if code != '200':
        raise RuntimeError(f'{url}: HTTP {code or "?"} {r.stderr.strip()[:200]}')
    return body

def months(d):
    """Sitemap months to search: the print's month and the next one (a late-month print can post in the next month)."""
    nxt = (d.replace(day=1) + dt.timedelta(days=32)).replace(day=1)
    return [(d.year, d.month), (nxt.year, nxt.month)]

def find(sym, d):
    """Fool transcript URLs for SYM dated from the day before the print to 14 days after, with Fool's lastmod."""
    pat = re.compile(r'<loc>(https://www\.fool\.com/earnings/call-transcripts/(\d{4})/(\d{2})/(\d{2})/[^<]*-' + re.escape(sym.lower()) +
                     r'-q\d-\d{4}-earnings-call-transcript/?)</loc>(?:<lastmod>([^<]*)</lastmod>)?')
    hits = []
    for y, m in months(d):
        if dt.date(y, m, 1) > dt.date.today():
            continue
        for u, yy, mm, dd, lm in pat.findall(curl(f'https://www.fool.com/sitemap/{y}/{m:02d}')):
            pd_ = dt.date(int(yy), int(mm), int(dd))
            if d - dt.timedelta(days=1) <= pd_ <= d + dt.timedelta(days=14):
                hits.append((pd_, u, lm))
    return sorted(hits)

def text(page):
    """Transcript body: from the 'Full Conference Call Transcript' heading to the first promo box after it, as speaker paragraphs."""
    i = page.find('id="full-conference-call-transcript"')
    if i < 0:
        raise RuntimeError('no transcript heading on the page (layout changed?)')
    j = page.find('article-body-promobox', i)
    j = page.rfind('<', i, j) if j > 0 else None
    s = page[page.find('>', i) + 1: j]
    s = s.split('</h2>', 1)[-1]
    s = re.sub(r'<strong>(.*?)</strong>', r'**\1**', s, flags=re.S)
    s = re.sub(r'</p>|<br\s*/?>|</h\d>|</li>', '\n\n', s)
    s = re.sub(r'<h\d[^>]*>', '## ', s)
    s = html.unescape(re.sub(r'<[^>]+>', '', s))
    s = re.sub(r'\n{3,}', '\n\n', re.sub(r'[ \t]+', ' ', s)).strip()
    if len(s) < 5000 or 'Operator' not in s[:3000] and '**' not in s[:500]:
        raise RuntimeError(f'transcript text looks wrong ({len(s)} chars)')
    return s

def et(z):
    try:
        t = dt.datetime.fromisoformat(z.replace('Z', '+00:00'))
    except Exception:
        return z or '?'
    off = -4 if dt.date(t.year, 3, 8) <= t.date() < dt.date(t.year, 11, 1) else -5   # rough DST window, display only
    return (t + dt.timedelta(hours=off)).strftime('%Y-%m-%d %-I:%M%p ET').replace('AM', 'am').replace('PM', 'pm')

def prune(sym):
    files = sorted(f for f in os.listdir(OUT) if re.fullmatch(re.escape(sym) + r'_\d{4}-\d{2}-\d{2}\.md', f))
    files_pdf = {f[:-3]: f[:-3] + '.pdf' for f in files if os.path.exists(os.path.join(OUT, f[:-3] + '.pdf'))}
    trash = os.path.join(os.path.dirname(VAL), f'_trash_{dt.date.today()}', 'valuation', 'notes', 'transcripts')
    for f in files[:-KEEP]:
        os.makedirs(trash, exist_ok=True)
        shutil.move(os.path.join(OUT, f), os.path.join(trash, f))
        if f[:-3] in files_pdf:
            shutil.move(os.path.join(OUT, files_pdf[f[:-3]]), os.path.join(trash, files_pdf[f[:-3]]))

def path(sym, d):
    return os.path.join(OUT, f'{sym}_{d}.md')

def get(sym, d):
    sym = sym.upper()
    p = path(sym, d)
    if os.path.exists(p):
        return dict(sym=sym, print=str(d), status='saved', file=p, note='already on file')
    hits = find(sym, d)
    if not hits:
        return dict(sym=sym, print=str(d), status='waiting', note='not on fool.com yet (or not covered): ask Kilian to download it')
    pd_, url, lm = hits[0]
    body = text(curl(url))
    os.makedirs(OUT, exist_ok=True)
    head = (f'# {sym} earnings call transcript (print {d})\n\nSource: {url}\nPublished on fool.com: {et(lm)}  \n'
            f'Fetched: {et(dt.datetime.now(dt.timezone.utc).isoformat())} by valuation/transcripts.py\n\n')
    with open(p + '.tmp', 'w') as f:
        f.write(head + body + '\n')
    os.replace(p + '.tmp', p)
    prune(sym)
    return dict(sym=sym, print=str(d), status='saved', file=p, url=url, chars=len(body))

def prints():
    st = json.load(open(ST))
    out = {}
    for s, r in st.get('reruns', {}).items():
        if r.get('report_date'):
            out[s] = r['report_date']
    for s, w in st.get('watch', {}).items():   # a print already out but not yet moved to reruns
        if w.get('date') and w['date'] <= str(dt.date.today()) and w['date'] > out.get(s, ''):
            out[s] = w['date']
    miss = []
    try:
        em = json.load(open(EM))
        for s in os.listdir(DM):
            d = (em.get(s) or {}).get('last_report_date') if os.path.isdir(os.path.join(DM, s)) and s.isupper() else None
            if d and d > out.get(s, ''):
                out[s] = d
            if os.path.isdir(os.path.join(DM, s)) and s.isupper():
                miss.append(s)   # also the newest 8-K Item 2.02 from EDGAR (the feed lacks some names or lags; staged_dates.last_202)
    except Exception:
        pass
    if miss:
        sys.path.insert(0, VAL)
        import staged_dates
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(5) as ex:
            for s, r in zip(miss, ex.map(lambda x: _try(staged_dates.last_202, x), miss)):
                if r and r[0] and r[0] > out.get(s, ''):
                    out[s] = r[0]
    return out

def _try(f, x):
    try:
        return f(x)
    except Exception:
        return None

def due(D):
    D = dt.date.fromisoformat(D)
    res = dict(saved=[], waiting=[], ask_kilian=[], errors=[])
    asked = json.load(open(ASKED)) if os.path.exists(ASKED) else {}
    sys.path.insert(0, VAL)
    import name_update   # Kilian 2026-11-05: transcripts only for names that already have a driver model
    for s, d in sorted(prints().items()):
        d = dt.date.fromisoformat(d)
        if not _try(name_update.driver_model, s):
            continue
        if not (D - dt.timedelta(days=DUE_DAYS) <= d <= D) or os.path.exists(path(s, d)):
            continue
        try:
            r = get(s, d)
            (res['saved'] if r['status'] == 'saved' else res['waiting']).append(dict(r, days_since_print=(D - d).days))
        except Exception as e:
            res['errors'].append(dict(sym=s, print=str(d), error=str(e)[:300]))
    for w in res['waiting']:
        k = f"{w['sym']}_{w['print']}"
        if k not in asked:
            asked[k] = dt.datetime.now(dt.timezone.utc).strftime('%Y-%m-%dT%H:%MZ')
            res['ask_kilian'].append(dict(sym=w['sym'], print=w['print'],
                ask=f"download the {w['sym']} call transcript for its {w['print']} print as a PDF and upload it"))
    cut = str(D - dt.timedelta(days=60))
    asked = {k: v for k, v in asked.items() if k.split('_')[-1] >= cut}
    with open(ASKED + '.tmp', 'w') as f:
        json.dump(asked, f, indent=1)
    os.replace(ASKED + '.tmp', ASKED)
    return res

def add(sym, d, pdf):
    sym = sym.upper()
    t = subprocess.run(['pdftotext', '-layout', pdf, '-'], capture_output=True, text=True, check=True).stdout
    t = re.sub(r'\n{3,}', '\n\n', t).strip()
    if len(t) < 5000:
        sys.exit(f'{pdf}: only {len(t)} chars of text; not a transcript?')
    os.makedirs(OUT, exist_ok=True)
    p = path(sym, d)
    keep = os.path.join(OUT, f'{sym}_{d}.pdf')
    shutil.copyfile(pdf, keep)
    with open(p + '.tmp', 'w') as f:
        f.write(f'# {sym} earnings call transcript (print {d})\n\nSource: PDF Kilian downloaded and uploaded, kept at {keep} '
                f'(cite this file)\nSaved: {et(dt.datetime.now(dt.timezone.utc).isoformat())} by valuation/transcripts.py add\n\n{t}\n')
    os.replace(p + '.tmp', p)
    prune(sym)
    return dict(sym=sym, print=str(d), status='saved', file=p, pdf=keep, chars=len(t))

if __name__ == '__main__':
    a = sys.argv[1:]
    if len(a) >= 2 and a[0] == 'get':
        d = dt.date.fromisoformat(a[2]) if len(a) > 2 else dt.date.fromisoformat(prints().get(a[1].upper()) or sys.exit(f'{a[1]}: no print on file; pass PRINT_DATE'))
        r = get(a[1], d)
        print(json.dumps(r))
        sys.exit(0 if r['status'] == 'saved' else 2)
    elif len(a) == 4 and a[0] == 'add':
        print(json.dumps(add(a[1], dt.date.fromisoformat(a[2]), a[3])))
    elif len(a) == 2 and a[0] == 'due':
        print(json.dumps(due(a[1])))
    else:
        sys.exit(__doc__)
