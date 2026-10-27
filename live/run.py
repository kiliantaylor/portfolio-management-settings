"""Live refresh in GitHub Actions: new fills, live quotes, review at live prices and the Price Targets live patch.

    python3 live/run.py SLICE FILLS_DIR OUT_DIR [--force]

SLICE is a checkout of the live-data branch (live/slice.py), reached as /mnt/project-files so the valuation scripts'
own paths work. FILLS_DIR holds one JSON per fill entered on the page (fields as on the page's Fills tab: symbol, side,
shares, price, date, note, entered_at; the file name is the fill id). OUT_DIR is a checkout of the live-out branch;
it gets patch.json (live/patch.py format, plus fills, quotes_as_of) and state.json.

Runs the valuation step when the market is open or the set of fills changed since the last patch; otherwise exits.
Applying fills is idempotent: every fill not yet in the nightly trades.json is appended on each run (fill_intake.py),
and the nightly run folds them into the project's trades.json for good."""
import datetime as dt, glob, hashlib, json, os, subprocess, sys, tempfile
from zoneinfo import ZoneInfo
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__))); import patch

SLICE, FILLS, OUT = (os.path.abspath(a) for a in sys.argv[1:4]); FORCE = '--force' in sys.argv
V = f'{SLICE}/valuation'

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
    now = dt.datetime.now(dt.timezone.utc)
    meta = json.load(open(f'{SLICE}/slice.json')); D = meta['date']
    fills = sorted(glob.glob(f'{FILLS}/*.json'))
    fill_key = hashlib.sha1(b''.join(open(p, 'rb').read() for p in fills)).hexdigest()
    try: state = json.load(open(f'{OUT}/state.json'))
    except FileNotFoundError: state = {}
    changed = state.get('fill_key') != fill_key or state.get('base') != meta['base']
    if not (FORCE or changed or market_open(now)):
        print(json.dumps({'skipped': 'market closed, no new fills'})); sys.exit(0)

    with tempfile.TemporaryDirectory() as tmp:  # fill_intake.py reads page-style docs: {"id", "data": {..., "status": "new"}}
        docs = []
        for p in fills:
            fid = os.path.splitext(os.path.basename(p))[0]
            doc = json.load(open(p)); doc['status'] = 'new'
            q = f'{tmp}/{fid}.json'; json.dump({'id': fid, 'data': doc}, open(q, 'w')); docs.append(q)
        intake = json.loads(sh('python3', 'fill_intake.py', *docs)) if docs else {'logged': [], 'already_in_trades': [], 'rejected': []}

    sh('python3', 'live_quotes.py', D)
    sh('python3', 'review.py', D, env={'LIVE_QUOTES': '1'})
    sh('python3', 'build_report.py', D, '--out', '/tmp/live.html')
    live = page_data('/tmp/live.html'); base = json.load(open(f'{SLICE}/base.json'))
    p = patch.diff(base, live)
    quotes = json.load(open(f'{V}/data/live_quotes.json'))
    p.update(base=meta['base'], version=now.strftime('%Y-%m-%dT%H:%M:%SZ'), quotes_as_of=quotes.get('as_of'),
             fills=dict(applied=intake['logged'], already=intake['already_in_trades'], rejected=intake['rejected']))
    os.makedirs(OUT, exist_ok=True)
    json.dump(p, open(f'{OUT}/patch.json', 'w'), separators=(',', ':'))
    json.dump(dict(fill_key=fill_key, base=meta['base'], version=p['version']), open(f'{OUT}/state.json', 'w'))
    open(f'{OUT}/.updated', 'w').close()  # tells the workflow to push live-out
    print(json.dumps(dict(version=p['version'], bytes=os.path.getsize(f'{OUT}/patch.json'), sets=len(p['set']),
                          fills_applied=len(intake['logged']), rejected=intake['rejected'])))
