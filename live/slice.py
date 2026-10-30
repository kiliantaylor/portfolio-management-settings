"""Copy the inputs the live run needs from the project files into a folder that becomes the `live-data` branch.

    python3 live/slice.py OUT [--root /mnt/project-files] [--date D]

OUT may already hold yesterday's slice (a clone of live-data): run folders of past dates are kept when the
same file is already there with the same size, so a nightly run only reads what changed from the slow mount.
Writes OUT/base.json, the DATA embedded in the published valuation/report.html, which the live patch is measured from."""
import concurrent.futures as cf, glob, json, os, re, shutil, sys

ARGS = sys.argv[1:]
OUT = os.path.abspath(ARGS[0])
ROOT = ARGS[ARGS.index('--root') + 1] if '--root' in ARGS else '/mnt/project-files'
V = f'{ROOT}/valuation'
D = ARGS[ARGS.index('--date') + 1] if '--date' in ARGS else sorted(p.split('/')[-2] for p in glob.glob(f'{V}/runs/*/review_summary.json'))[-1]
SKIP = re.compile(r'\.bak|\.tmp$|/__pycache__/|/gate/|/locks/')

def walk(base, keep=lambda p: True):
    for dp, dn, fn in os.walk(base):
        for f in fn:
            p = os.path.join(dp, f)
            if not SKIP.search(p) and keep(p): yield p

TRACER = r"""import builtins, io, json, os, sys, runpy
root, out, tmp, script = sys.argv[1:5]; seen = set()
_open, _isfile, _real = builtins.open, os.path.isfile, os.path.realpath
def under(p):
    try: return _real(p).startswith(root + '/')
    except TypeError: return False
def note(p):
    q = _real(p)
    if q.startswith(root + '/') and _isfile(q): seen.add(q)
def mirror(p): return os.path.join(tmp, 'w', _real(p)[len(root) + 1:])
def topen(p, mode='r', *a, **k):  # the run reads the project files and writes into a scratch mirror, never the mount
    if isinstance(p, (str, os.PathLike)) and under(p):
        m = mirror(p)
        if any(c in mode for c in 'wax+'):
            os.makedirs(os.path.dirname(m), exist_ok=True); return _open(m, mode, *a, **k)
        if _isfile(m): return _open(m, mode, *a, **k)  # read back what the run wrote
        if str(p).endswith('data/live_quotes.json') and os.environ.get('TRACE_QUOTES'): note(p); return _open(os.environ['TRACE_QUOTES'], mode, *a, **k)
        note(p)
    return _open(p, mode, *a, **k)
builtins.open = io.open = topen
def logged(f):
    def g(p, *a, **k):
        if under(p) and _isfile(mirror(p)): return f(mirror(p), *a, **k)
        r = f(p, *a, **k)
        if under(p): note(p)
        return r
    return g
for n in ('exists', 'isfile', 'getsize', 'getmtime'): setattr(os.path, n, logged(getattr(os.path, n)))
def blocked(f):
    def g(*a, **k):
        if any(under(x) for x in a if isinstance(x, (str, os.PathLike))):
            if f.__name__ in ('replace', 'rename'): return f(*(mirror(x) if under(x) else x for x in a))
            return None
        return f(*a, **k)
    return g
for n in ('replace', 'rename', 'remove', 'unlink', 'chmod', 'utime', 'makedirs', 'mkdir'): setattr(os, n, blocked(getattr(os, n)))
def hook(ev, args):
    if ev != 'open' or not isinstance(args[0], str) or not under(args[0]): return
    if (args[1] and any(c in str(args[1]) for c in 'wax+')) or (args[2] or 0) & (os.O_WRONLY | os.O_RDWR | os.O_CREAT):
        raise PermissionError('slice trace: no writes under ' + root)
sys.addaudithook(hook)
os.chdir(os.path.dirname(script)); sys.path.insert(0, os.path.dirname(script)); sys.argv = [script] + sys.argv[5:]
try: runpy.run_path(script, run_name='__main__')
finally: _open(out, 'w').write('\n'.join(sorted(seen)))
"""

def traced(script, *args, env=None):
    """Every file under ROOT that `script` reads or checks for, run against the project files read-only (writes go to /tmp)."""
    import subprocess, tempfile
    with tempfile.TemporaryDirectory() as tmp:
        open(f'{tmp}/t.py', 'w').write(TRACER)
        real = os.path.realpath(ROOT)
        r = subprocess.run(['python3', '-I', f'{tmp}/t.py', real, f'{tmp}/seen', tmp, f'{V}/{script}', *args],
                           capture_output=True, text=True, env={**os.environ, **(env or {})})
        if r.returncode: sys.exit(f'{script} trace failed:\n' + r.stderr[-2000:])
        return [ROOT + p[len(real):] for p in open(f'{tmp}/seen').read().split('\n') if p and '__pycache__' not in p]

def traced_review():  # review.py at live prices, with tonight's quotes dated tomorrow so the live repricing path runs
    import tempfile
    q = json.load(open(f'{V}/data/live_quotes.json'))
    for x in (q.get('quotes') or {}).values(): x['date'] = '9999-12-31'
    with tempfile.NamedTemporaryFile('w', suffix='.json', delete=False) as f: json.dump(q, f)
    try: return traced('review.py', D, env={'LIVE_QUOTES': '1', 'TRACE_QUOTES': f.name})
    finally: os.unlink(f.name)

def wanted():
    yield from glob.glob(f'{V}/*.py')
    yield f'{ROOT}/adhoc/page_search.py'
    yield f'{ROOT}/risk/riskcalc.py'
    yield from glob.glob(f'{ROOT}/risk/model/*.json')
    yield from (p for p in glob.glob(f'{V}/data/*.json') if not SKIP.search(p))
    trades = json.load(open(f'{V}/data/trades.json'))
    held = {t['symbol'] for t in (trades['trades'] if isinstance(trades, dict) else trades)}
    yield from (p for p in glob.glob(f'{V}/data/daily_*.csv') if p[len(V) + 12:-4] in held)
    yield from walk(f'{ROOT}/portfolio/data')
    yield from walk(f'{V}/priors/{D}')
    yield from traced('build_report.py', D, '--out', '/tmp/slice_trace_page.html')
    marked = sorted(s for s in held if os.path.exists(f'{V}/priors/{D}/{s}.json'))
    if marked: yield from traced('combine.py', D, *marked, '--dry-run')  # what live/remark.py needs to re-mark a held name
    # live/remark.py re-marks, and live/swaps.py scores, any unheld gate-clean name that falls under its Entry below
    yield f'{ROOT}/market_color/beats_check.py'
    cands = sorted(s for s in (p.split('/')[-2] for p in glob.glob(f'{V}/runs/{D}/*/result.json'))
                   if os.path.exists(f'{V}/priors/{D}/{s}.json')
                   and (json.load(open(f'{V}/runs/{D}/{s}/result.json')).get('citation_gate') or {}).get('status'))
    if cands:
        yield from traced('combine.py', D, *cands, '--dry-run')
        sys.path.insert(0, V); import book  # allocate.py sizes unheld names only, as the nightly does
        now = {p['symbol'] for p in book.load()['positions'] if p.get('shares')}
        yield from traced('allocate.py', D, *[s for s in cands if s not in now])  # the inputs of beats_check.py's probe (allocate.py's own code up to its Sharpe test)
    yield from traced_review()

def base_key(data):  # the page computes the same key from its embedded DATA (applyLive in report_template.html)
    return f"{data.get('as_of')}|{data.get('generated')}|{(data.get('live') or {}).get('time_utc')}"

def copy(src):
    rel = os.path.relpath(src, ROOT); dst = os.path.join(OUT, rel)
    fresh = '/runs/' in rel and f'/runs/{D}/' not in rel  # past runs don't change: reuse yesterday's copy
    if fresh and os.path.exists(dst) and os.path.getsize(dst) == os.path.getsize(src): return 0
    os.makedirs(os.path.dirname(dst), exist_ok=True); shutil.copyfile(src, dst); return 1

if __name__ == '__main__':
    files = sorted(set(wanted()))
    with cf.ThreadPoolExecutor(16) as ex: n = sum(ex.map(copy, files))
    keep = {os.path.relpath(p, ROOT) for p in files}
    gone = 0
    for p in walk(OUT):
        rel = os.path.relpath(p, OUT)
        if rel not in keep and rel not in ('base.json', 'slice.json') and not rel.startswith('.git/'): os.remove(p); gone += 1
    h = open(f'{V}/report.html').read()
    m = re.search(r'<script id="data" type="application/json">(.*?)</script>', h, re.S)
    base = json.loads(m.group(1).replace('<\\/', '</'))
    json.dump(base, open(f'{OUT}/base.json', 'w'))
    key = base_key(base)
    json.dump(dict(date=D, base=key), open(f'{OUT}/slice.json', 'w'))
    print(json.dumps(dict(date=D, files=len(files), copied=n, removed=gone, base=key)))
