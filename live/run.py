"""Live refresh in GitHub Actions: new fills, live quotes, review at live prices and the Price Targets live patch.

    python3 live/run.py SLICE FILLS_DIR OUT_DIR [--force]

SLICE is a checkout of the live-data branch (live/slice.py), reached as /mnt/project-files so the valuation scripts'
own paths work. FILLS_DIR (main's fills/) holds one JSON per fill entered on the page (symbol, side, shares, price,
date, note, entered_at; the file name is the fill id). With SHEET_ID and GOOGLE_SA_JSON set, new rows on the Sheet's
fills tab are first written there (the workflow commits them), each row gets a status, and the patch goes to the
Sheet's live tab, where the page reads it. OUT_DIR is a checkout of the live-out branch; it gets patch.json
(live/patch.py format, plus fills and quotes_as_of) and state.json.

Held names that moved past their implied move since their last market leg are re-marked on a fresh option chain first
(live/remark.py; the legs it pulls are kept in OUT_DIR/remark for the rest of the session).
Runs the valuation step when the market is open or the set of fills changed since the last patch; otherwise exits.
Applying fills is idempotent: every fill not yet in the nightly trades.json is appended on each run (fill_intake.py),
and the nightly run folds them into the project's trades.json for good."""
import datetime as dt, glob, hashlib, json, os, subprocess, sys
from zoneinfo import ZoneInfo
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__))); import patch


def sh(*cmd, env=None):
    r = subprocess.run(cmd, cwd=V, capture_output=True, text=True, env={**os.environ, **(env or {})})
    if r.returncode: sys.exit(f'{cmd[1]} failed:\n{r.stderr[-3000:]}')
    return r.stdout

def market_open(now):
    ny = now.astimezone(ZoneInfo('America/New_York'))
    return ny.weekday() < 5 and dt.time(9, 25) <= ny.time() <= dt.time(16, 20)

def page_data(html):
    import re
    m = re.search(r'<script id="data" type="application/json">(.*?)</script>', open(html).read(), re.S)
    return json.loads(m.group(1).replace('<\\/', '</'))

if __name__ == '__main__':
    SLICE, FILLS, OUT = (os.path.abspath(a) for a in sys.argv[1:4]); FORCE = '--force' in sys.argv
    V = f'{SLICE}/valuation'
    now = dt.datetime.now(dt.timezone.utc); stamp = now.strftime('%Y-%m-%dT%H:%M:%SZ')
    meta = json.load(open(f'{SLICE}/slice.json')); D = meta['date']
    sheet = None
    if os.environ.get('SHEET_ID') and os.environ.get('GOOGLE_SA_JSON'):
        from sheet import Sheet, DONE
        sheet = Sheet(os.environ['SHEET_ID'], os.environ['GOOGLE_SA_JSON'])
        rows = sheet.fills()
        for _, fid, doc, status in rows:  # pending rows become fill files; done or rejected ones never do
            if status not in DONE and not status.startswith('rejected') and not os.path.exists(f'{FILLS}/{fid}.json'):
                keep = {k: doc.get(k) for k in ('symbol', 'side', 'shares', 'price', 'date', 'note', 'entered_at')}
                json.dump(keep, open(f'{FILLS}/{fid}.json', 'w'), indent=1)
        for _, fid, _, status in rows:  # a removed or rejected row drops its file (the nightly run never takes those)
            if (status == 'removed' or status.startswith('rejected')) and os.path.exists(f'{FILLS}/{fid}.json'): os.remove(f'{FILLS}/{fid}.json')
    fills = sorted(glob.glob(f'{FILLS}/*.json'))
    fill_key = hashlib.sha1(b''.join(open(p, 'rb').read() for p in fills)).hexdigest()
    try: state = json.load(open(f'{OUT}/state.json'))
    except FileNotFoundError: state = {}
    changed = state.get('fill_key') != fill_key or state.get('base') != meta['base']
    if not (FORCE or changed or market_open(now)):
        print(json.dumps({'skipped': 'market closed, no new fills'})); sys.exit(0)

    r = subprocess.run(['python3', os.path.join(os.path.dirname(os.path.abspath(__file__)), 'fold_fills.py'), FILLS, V],
                       capture_output=True, text=True)
    if r.returncode: sys.exit(f'fold_fills.py failed:\n{r.stderr[-3000:]}')
    intake = json.loads(r.stdout)

    sh('python3', 'live_quotes.py', D)
    # held names that moved past their implied move get a fresh option chain and a re-mark of their own (live/remark.py)
    r = subprocess.run(['python3', os.path.join(os.path.dirname(os.path.abspath(__file__)), 'remark.py'), SLICE, OUT],
                       capture_output=True, text=True)
    if r.returncode: sys.exit(f'remark.py failed:\n{r.stderr[-3000:]}')
    remark = json.loads(r.stdout.strip().splitlines()[-1])
    sh('python3', 'review.py', D, env={'LIVE_QUOTES': '1'})
    sh('python3', 'build_report.py', D, '--out', '/tmp/live.html')
    live = page_data('/tmp/live.html'); base = json.load(open(f'{SLICE}/base.json'))
    p = patch.diff(base, live)
    quotes = json.load(open(f'{V}/data/live_quotes.json'))
    p.update(base=meta['base'], version=stamp, quotes_as_of=quotes.get('as_of'),
             fills=dict(applied=intake['logged'], already=intake['already_in_trades'], rejected=intake['rejected']),
             remarked=remark['remarked'])
    os.makedirs(OUT, exist_ok=True)
    json.dump(p, open(f'{OUT}/patch.json', 'w'), separators=(',', ':'))
    json.dump(dict(fill_key=fill_key, base=meta['base'], version=p['version']), open(f'{OUT}/state.json', 'w'))
    open(f'{OUT}/.updated', 'w').close()  # tells the workflow to push live-out
    if sheet:
        sheet.write_patch(p)
        tj = json.load(open(f'{V}/data/trades.json')); nightly = {t.get('fill_id') for t in tj['trades']} - set(intake['logged'])
        why = {r['id']: r['why'] for r in intake['rejected']}
        upd = {}
        for row, fid, _, status in rows:
            new = ('in trades.json' if fid in nightly else 'live, in trades.json tonight' if fid in intake['logged']
                   else f'rejected: {why[fid]}' if fid in why else status)
            if new != status and status not in DONE: upd[row] = new
        sheet.set_status(upd, stamp)
    print(json.dumps(dict(version=p['version'], bytes=os.path.getsize(f'{OUT}/patch.json'), sets=len(p['set']),
                          fills_applied=len(intake['logged']), rejected=intake['rejected'], sheet=bool(sheet),
                          chains_pulled=remark['pulled'], remarked=remark['remarked'], remark_failed=remark['failed'])))
