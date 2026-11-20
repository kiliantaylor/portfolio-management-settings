"""Insider-sale warning on held names (Kilian 2026-10-19 card: "Show warning"). Display only: nothing here feeds sizing,
conviction or allocate.

usage: python3 insider_flags.py [AS_OF]   -> data/insider_flags.json   (AS_OF defaults to today, YYYY-MM-DD)

For every name held per data/trades.json it reads the issuer's recent Form 4 filings from SEC EDGAR (submissions JSON on
data.sec.gov, then each filing's XML) and keeps the filings that match the backtest's "discretionary sale" definition
(../insider_backtest/code: extract.py, analyze_insiders.py; result in ../insider_backtest/results/sales_10b5_check.csv):
  - form type 4 (amendments 4/A are skipped, as in the backtest's SEC dataset filter DOCUMENT_TYPE == '4');
  - at least one non-derivative line with transaction code S (open-market or private sale; F tax withholding, M option
    exercise and G gifts are not sales); shares and value summed over the filing's S lines, price = value-weighted average;
  - the filing-level 10b5-1 box (aff10b5One, on every Form 4 since April 2023) is NOT checked; a filing that omits the
    element is skipped as unknown (the backtest's non-plan group is flag 0/false only);
  - role as in analyze_insiders.load(): CEO/CFO if the officer title matches chief executive|ceo|chief financial|cfo, else
    "other officer" if the title or relationship says officer, else Director if the owner is a director. Only CEO/CFO and
    Director are flagged (the card's "CEO/CFO/director"); other officers and 10% owners are counted but not flagged;
  - trade-to-filing lag 0 to 60 days (the backtest's filter); no minimum size (the backtest had none);
  - flag window: filed in the last 90 calendar days up to AS_OF, matching the backtest's 3-month (63 trading day) horizon.
Each kept filing is one entry (the backtest's event unit is insider x ticker x filing date).

Requests: data.sec.gov with secfacts.UA (cached 24 h by secfacts); filing XML from www.sec.gov, which refuses a User-Agent
without a contact address, so SEC_UA (env, or data/sec_contact.txt) is used there; with neither, the script stops and keeps the last output.
Never put a personal name or email in it (privacy rule). At most ~7 requests a second (0.15 s pause); filing XML is cached in
/tmp/insider_cache (filings never change)."""
import datetime as dt, json, os, re, subprocess, sys, time
import xml.etree.ElementTree as ET
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import secfacts

WINDOW_DAYS = 90
MAX_LAG = 60
# www.sec.gov needs a real contact in the User-Agent. No placeholder address (2026-10-19: a made-up contact misdeclares the
# tool to SEC). Set env SEC_UA or put one line in data/sec_contact.txt (a non-personal project mailbox, never Kilian's
# own name or email). Without one the run stops before writing, so the last insider_flags.json and its as-of date stay.
_cf = os.path.join(HERE, 'data', 'sec_contact.txt')
SEC_UA = os.environ.get('SEC_UA') or (f"{secfacts.UA} {open(_cf).read().strip()}" if os.path.exists(_cf) else '')
CACHE = '/tmp/insider_cache'
os.makedirs(CACHE, exist_ok=True)


def held_names():
    T = json.load(open(f'{HERE}/data/trades.json'))
    T = T.get('trades', T) if isinstance(T, dict) else T
    pos = {}
    for t in T:
        pos[t['symbol']] = pos.get(t['symbol'], 0) + (t['shares'] if t.get('side', 'buy') == 'buy' else -t['shares'])
    return sorted(s for s, n in pos.items() if n > 0)


def fetch_xml(url, acc):
    path = f'{CACHE}/{acc}.xml'
    if os.path.exists(path) and os.path.getsize(path) > 200:
        return open(path, 'rb').read()
    for attempt in range(3):
        r = subprocess.run(['curl', '-sS', '-A', SEC_UA, '-o', path + '.tmp', '-w', '%{http_code}', url], capture_output=True, text=True)
        time.sleep(0.15)
        code = r.stdout.strip()
        if code == '200':
            os.replace(path + '.tmp', path); return open(path, 'rb').read()
        if code in ('429', '503'): time.sleep(2 + 3 * attempt); continue
        break
    if os.path.exists(path + '.tmp'): os.remove(path + '.tmp')
    raise RuntimeError(f'{url} -> HTTP {code or r.stderr.strip()}')


def txt(e, path):
    x = e.find(path)
    return x.text.strip() if x is not None and x.text and x.text.strip() else None


def num(e, path):
    v = txt(e, path)
    try: return float(v.replace(',', '')) if v is not None else None
    except ValueError: return None


def truthy(v):
    return None if v is None else v.strip().lower() in ('1', 'true')


def role_of(title, rel):
    """analyze_insiders.load() role buckets, in its order."""
    t = (title or '').lower()
    if re.search(r'chief executive|\bceo\b|chief financial|\bcfo\b', t): grp = 'CEO/CFO'
    elif re.search(r'president|chief|\bcoo\b|\bcto\b|officer|evp|svp|vice president|\bvp\b|general counsel|treasurer|controller', t) or 'Officer' in rel: grp = 'Other officer'
    elif 'Director' in rel: grp = 'Director'
    elif 'TenPercentOwner' in rel: grp = '10% owner'
    else: grp = 'Other'
    if grp == 'CEO/CFO':
        ceo, cfo = re.search(r'chief executive|\bceo\b', t), re.search(r'chief financial|\bcfo\b', t)
        label = 'CEO and CFO' if ceo and cfo else 'CEO' if ceo else 'CFO'
    else:
        label = {'Director': 'Director', 'Other officer': title or 'Officer', '10% owner': '10% owner'}.get(grp, 'Other')
    return grp, label


def parse(xml):
    root = ET.fromstring(xml)
    owners = root.findall('reportingOwner')
    rel = set()
    for o in owners:
        r = o.find('reportingOwnerRelationship')
        if r is None: continue
        for tag, name in (('isDirector', 'Director'), ('isOfficer', 'Officer'), ('isTenPercentOwner', 'TenPercentOwner'), ('isOther', 'Other')):
            if truthy(txt(r, tag)): rel.add(name)
    o0 = owners[0] if owners else root
    title = txt(o0, 'reportingOwnerRelationship/officerTitle')
    name = txt(o0, 'reportingOwnerId/rptOwnerName')
    foot = {f.get('id'): ''.join(f.itertext()).strip() for f in root.findall('footnotes/footnote')}
    lines = []
    for t in root.findall('nonDerivativeTable/nonDerivativeTransaction'):
        if txt(t, 'transactionCoding/transactionCode') != 'S': continue
        sh, px = num(t, 'transactionAmounts/transactionShares/value'), num(t, 'transactionAmounts/transactionPricePerShare/value')
        fids = [x.get('id') for x in t.iter('footnoteId')]
        lines.append(dict(date=txt(t, 'transactionDate/value'), shares=sh, price=px, notes=' '.join(foot.get(i, '') for i in fids)))
    return dict(plan=truthy(txt(root, 'aff10b5One')), issuer_cik=txt(root, 'issuer/issuerCik'), issuer_sym=txt(root, 'issuer/issuerTradingSymbol'),
                owner=name, title=title, rel='|'.join(sorted(rel)), n_owners=len(owners), sales=lines)


def pretty(name):
    """'HUANG JEN HSUN' -> 'Jen Hsun Huang': Form 4 owner names are 'Last First Middle'; entities are left in order."""
    if not name: return ''
    p = name.split()
    if len(p) >= 2 and not re.search(r'\b(llc|lp|l\.p\.|inc|trust|fund|ltd|corp|partners|holdings|foundation)\b', name, re.I):
        p = p[1:] + p[:1]
    return ' '.join(w.capitalize() if w.isupper() and len(w) > 1 else w for w in p)


def scan(sym, cik, as_of):
    start = as_of - dt.timedelta(days=WINDOW_DAYS)
    rec = secfacts.submissions(cik)['filings']['recent']
    out, cnt = [], dict(form4_filed=0, with_sales=0, plan_sales=0, plan_unknown=0, other_roles=0, late=0, flagged=0)
    for i, form in enumerate(rec['form']):
        if form != '4': continue
        filed = dt.date.fromisoformat(rec['filingDate'][i])
        if not (start < filed <= as_of): continue
        cnt['form4_filed'] += 1
        acc, doc = rec['accessionNumber'][i], rec['primaryDocument'][i]
        base = f'https://www.sec.gov/Archives/edgar/data/{cik}/{acc.replace("-", "")}/'
        f = parse(fetch_xml(base + doc.split('/')[-1], acc))
        if not f['sales']: continue
        cnt['with_sales'] += 1
        if f['plan'] is None: cnt['plan_unknown'] += 1; continue
        if f['plan']: cnt['plan_sales'] += 1; continue
        grp, label = role_of(f['title'], f['rel'])
        if grp not in ('CEO/CFO', 'Director'): cnt['other_roles'] += 1; continue
        dates = sorted(l['date'][:10] for l in f['sales'] if l['date'])
        lag = (filed - dt.date.fromisoformat(dates[0])).days if dates else None
        if lag is None or not 0 <= lag <= MAX_LAG: cnt['late'] += 1; continue
        shares = sum(l['shares'] or 0 for l in f['sales'])
        value = sum((l['shares'] or 0) * (l['price'] or 0) for l in f['sales'])
        priced = sum(l['shares'] or 0 for l in f['sales'] if l['price'])
        notes = ' '.join(l['notes'] for l in f['sales'])
        out.append(dict(filed=filed.isoformat(), trade_date=dates[0], trade_date_last=dates[-1], insider=f['owner'], insider_display=pretty(f['owner']),
                        role=label, role_group=grp, officer_title=f['title'], relationship=f['rel'], shares=round(shares, 4),
                        value_usd=round(value, 2) if priced else None, price=round(value / priced, 4) if priced else None,
                        accession=acc, accession_url=base + doc, index_url=base,
                        footnote_mentions_10b5_1=bool(re.search(r'10b5-?1', notes, re.I))))
        cnt['flagged'] += 1
    out.sort(key=lambda e: (e['filed'], e['trade_date']), reverse=True)
    return out, cnt


def line(sym, e):
    """The one-line display text, shared by news_page.py and the dashboard."""
    v = f"${e['value_usd']:,.0f}" if e.get('value_usd') else f"{e['shares']:,.0f} shares"
    when = e['trade_date'] if e['trade_date'] == e['trade_date_last'] else f"{e['trade_date']} to {e['trade_date_last']}"
    return f"Insider sale (not under a 10b5-1 plan): {e['role']} {e['insider_display']} sold {v} on {when}"


def main():
    if not SEC_UA:
        print('insider_flags: no SEC contact set (env SEC_UA or data/sec_contact.txt); kept the previous insider_flags.json'); return
    as_of = dt.date.fromisoformat(sys.argv[1]) if len(sys.argv) > 1 else dt.date.today()
    cmap = json.load(open(f'{HERE}/data/cik_map.json'))
    res = {}
    checked, errors = {}, {}
    for s in held_names():
        try:
            cik = cmap[s]['cik'] if s in cmap else secfacts.cik_of(s)[1]
            res[s], checked[s] = scan(s, cik, as_of)
        except Exception as ex:  # one name failing must not stop the others
            res[s] = []; errors[s] = str(ex)[:300]
        print(s, checked.get(s, errors.get(s)), flush=True)
    res.update(as_of=as_of.isoformat(), window_days=WINDOW_DAYS, generated=dt.datetime.now(dt.timezone.utc).strftime('%Y-%m-%dT%H:%MZ'),
               checked=checked, errors=errors,
               _note=('Display only (Kilian 2026-10-19 card "Show warning"): Form 4 sales (code S) filed in the last 90 days by a CEO, CFO '
                      'or director with the 10b5-1 box unchecked, per the insider backtest\'s definitions; see insider_flags.py and METHOD.md. '
                      'Does not feed sizing, conviction or allocate.'))
    tmp = f'{HERE}/data/insider_flags.json.tmp'
    json.dump(res, open(tmp, 'w'), indent=1); os.replace(tmp, f'{HERE}/data/insider_flags.json')
    print('insider_flags.json', {s: len(v) for s, v in res.items() if isinstance(v, list)}, 'errors' if errors else '', errors or '')


if __name__ == '__main__':
    main()
