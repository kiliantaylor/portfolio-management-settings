"""Staged-tranche dates from the print itself, never from a typed guess (Kilian 2026-11-04: "you have the wrong date for bkng q3";
"vst earnings date on tranche plan is wrong as well").

python3 staged_dates.py [D]      (nightly.py step 3b0s, after earnings_miss.py and before review.py; also safe to run any time)

Why: review_settings.json staged_entries[SYM].after was typed by hand when a plan was written ("Q3 results (likely Nov 3-5)",
after 2026-11-06) and nothing checked it. BKNG printed Nov 3 after the close, so its sell-all waited on a review that had
already happened; NVDA's plan said ~Nov 18 when Nvidia had announced Nov 17.

What it does, for every staged entry tied to a print:
  1. Print already out (primary source): the newest SEC 8-K with Item 2.02 (results of operations) filed in the 45 days before
     the staged date. The tranche date becomes the next NYSE session after that filing date.
  2. Print still ahead: the next report date in data/earnings_miss.json (Nasdaq/Zacks). Only a company-confirmed date
     (Nasdaq text "expected*") moves the tranche date (next session after it); an algorithm estimate ("estimated") is left
     as is and listed under `unconfirmed`, for the nightly thread to look up at the company's IR page and set by hand with
     event_source (a hand-set event_source + event_date is never overwritten by the feed, only by an 8-K).
Each entry gets event_date, event_timing, event_source, and its event text loses any typed guess in parentheses (the page
shows the real date from event_date). Prints JSON: changed, confirmed, unconfirmed, errors.
SEC requests use the User-Agent 'valuation-research script' (memory: SEC FETCHES)."""
import json, os, re, sys, time, urllib.request, datetime as dt
HERE = os.path.dirname(os.path.abspath(__file__))
CFG = os.path.join(HERE, 'data', 'review_settings.json')
UA = 'valuation-research script'
WINDOW_DAYS = 45
# NYSE full-day closures (nyse.com holidays page); extend each December
HOLIDAYS = {'2026-11-26', '2026-12-25', '2027-01-01', '2027-01-18', '2027-02-15', '2027-03-26', '2027-05-31', '2027-06-18',
            '2027-07-05', '2027-09-06', '2027-11-25', '2027-12-24'}
CIK = {}  # filled from data/cik_map.json once per run

def act_session(d, timing):
    """Kilian 2026-11-04 ("change vst date to nov 6 instead of 9"): a before-the-open print acts the same session (the
    post-print update lands before the close); otherwise the session after the print."""
    d0 = dt.date.fromisoformat(d)
    if 'before' in str(timing or '').lower() and d0.weekday() < 5 and d not in HOLIDAYS: return d
    return next_session(d)

def next_session(d):
    d = dt.date.fromisoformat(d)
    while True:
        d += dt.timedelta(days=1)
        if d.weekday() < 5 and d.isoformat() not in HOLIDAYS: return d.isoformat()

def get(url):
    req = urllib.request.Request(url, headers={'User-Agent': UA, 'Accept-Encoding': 'identity'})
    with urllib.request.urlopen(req, timeout=30) as r: return json.loads(r.read().decode())

def cik(sym):  # data/cik_map.json (www.sec.gov/files/company_tickers.json answers 403 through the proxy)
    if not CIK: CIK.update({k: int(v['cik']) for k, v in json.load(open(os.path.join(HERE, 'data', 'cik_map.json'))).items() if isinstance(v, dict) and v.get('cik')})
    return CIK.get(sym.upper())

def last_202(sym):
    """Newest 8-K Item 2.02: filing date, URL and timing vs the session, from SEC EDGAR submissions."""
    c = cik(sym)
    if not c: return None, None, None
    r = get(f'https://data.sec.gov/submissions/CIK{c:010d}.json')['filings']['recent']
    for i, f in enumerate(r['form']):
        if f == '8-K' and '2.02' in (r['items'][i] or ''):
            acc = r['accessionNumber'][i]
            url = f"https://www.sec.gov/Archives/edgar/data/{c}/{acc.replace('-', '')}/{r['primaryDocument'][i]}"
            t = dt.datetime.fromisoformat(r['acceptanceDateTime'][i].replace('Z', '+00:00')) - dt.timedelta(hours=5 if r['filingDate'][i] >= '2026-11-01' or r['filingDate'][i] < '2026-03-08' else 4)
            tm = 'after market close' if t.hour >= 16 else 'before market open' if (t.hour, t.minute) < (9, 30) else 'during market hours'
            return r['filingDate'][i], url, tm  # ET from the EDGAR acceptance time (DST approximated for 2026; display only)
    return None, None, None

def main(D):
    cfg = json.load(open(CFG)); st = cfg.get('staged_entries') or {}
    try: EM = json.load(open(os.path.join(HERE, 'data', 'earnings_miss.json')))
    except Exception: EM = {}
    out = dict(as_of=D, changed=[], confirmed=[], unconfirmed=[], errors=[])
    for s, e in st.items():
        if s.startswith('_') or not isinstance(e, dict) or not e.get('after'): continue
        ev = str(e.get('event') or '')
        if not re.search(r'\b(Q[1-4]|results|print|earnings|call)\b', ev, re.I): continue  # not tied to a print
        old = dict(after=e['after'], event=ev, event_date=e.get('event_date'))
        try: fd, url, tm = last_202(s); time.sleep(0.2)
        except Exception as x: fd, url, tm = None, None, None; out['errors'].append(f'{s}: SEC lookup failed ({x})')
        lo = (dt.date.fromisoformat(e['after']) - dt.timedelta(days=WINDOW_DAYS)).isoformat()
        if fd and lo <= fd < e['after'] or (fd and e.get('event_date') and fd >= e['event_date']):
            e.update(event_date=fd, event_timing=tm,
                     event_source=f'SEC 8-K Item 2.02 filed {fd}: {url}', after=act_session(fd, tm), event_reported=True)
        elif e.get('event_reported') and e.get('event_date') and e['event_date'] < e['after']:
            pass  # already linked to its 8-K
        elif not (e.get('event_source') or '').startswith('http'):  # a hand-set company source wins over the feed
            nr = (EM.get(s) or {}).get('next_report') or {}
            if nr.get('date') and 'expected*' in (nr.get('text') or ''):
                e.update(event_date=nr['date'], event_timing=nr.get('timing'), after=act_session(nr['date'], nr.get('timing')),
                         event_source='Nasdaq/Zacks company-confirmed date (expected*)')
            else:
                out['unconfirmed'].append(dict(symbol=s, after=e['after'], event=ev, feed=nr.get('date'),
                                               note='no company-confirmed date: look it up at the IR page, set event_date, event_timing and event_source (URL) by hand'))
        else:
            e['after'] = act_session(e['event_date'], e.get('event_timing'))  # hand-set date from the company: keep the tranche on the session after it
        e['event'] = re.sub(r'\s*\(.*$', '', ev).strip() or ev  # typed guesses like "(likely Nov 3-5)" go; the page shows event_date
        new = dict(after=e['after'], event=e['event'], event_date=e.get('event_date'))
        (out['changed'] if new != old else out['confirmed']).append(dict(symbol=s, old=old, new=new, source=e.get('event_source')))
    if out['changed']: json.dump(cfg, open(CFG, 'w'), indent=1)
    print(json.dumps(out, indent=1))

if __name__ == '__main__':
    main(sys.argv[1] if len(sys.argv) > 1 else dt.date.today().isoformat())
