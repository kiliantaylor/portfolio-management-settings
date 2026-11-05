"""Copy the inputs the live run needs from the project files into a folder that becomes the `live-data` branch.

    python3 live/slice.py OUT [--root /mnt/project-files] [--date D]
    python3 live/slice.py OUT --research SYM [SYM ...] [--root /mnt/project-files]

OUT may already hold yesterday's slice (a clone of live-data): run folders of past dates are kept when the
same file is already there with the same size and the source has not been modified since that slice was taken
(slice.json 'time'), so a nightly run only reads what changed from the slow mount.
Writes OUT/base.json, the DATA embedded in the published valuation/report.html, which the live patch is measured from.

--research (Kilian 2026-11-05, "valuation changes go live at once"): OUT is the live-data clone of the published slice.
Adds each named symbol's newest finished research round dated after the slice date D (a model update done today, which
the nightly can only pick up after the 4:15pm ET close bar) and lists it in slice.json 'research' {SYM: RDATE}; live/remark.py
then re-marks that name with combine.py --research RDATE on every live run, so the page's target, take-profit, stop and
the held names' sizing move intraday. Finished = runs/RDATE/SYM has supervisor.json, result.json and a clean
citation_audit.json, a model_engine.json that values the current supervisor.json (combine.standard_model), and
data/name_updates.json does not hold the name pending. D, base.json and every other slice file
stay as they are (only files the slice lacks are added), so the page's patch key and the close-bar guard are untouched.
The next nightly slice (D = RDATE) drops the list: the nightly re-mark picks the round up itself."""
import concurrent.futures as cf, glob, json, os, re, shutil, sys, time

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

def research(syms):
    meta = json.load(open(f'{OUT}/slice.json')); D0 = meta['date']
    today = __import__('datetime').datetime.now(__import__('zoneinfo').ZoneInfo('America/New_York')).date().isoformat()
    try: pend = json.load(open(f'{V}/data/name_updates.json')).get('names') or {}
    except FileNotFoundError: pend = {}
    got, why = dict(meta.get('research') or {}), {}
    for s in syms:
        rs = sorted(p.split('/')[-3] for p in glob.glob(f'{V}/runs/*/{s}/supervisor.json'))
        rs = [r for r in rs if D0 < r <= today]
        if not rs: why[s] = f'no research round after the slice date {D0}'; continue
        R = rs[-1]; rd = f'{V}/runs/{R}/{s}'
        try: ca = json.load(open(f'{rd}/citation_audit.json'))
        except FileNotFoundError: ca = {}
        if ca.get('clean') is not True: why[s] = f'runs/{R}/{s}/citation_audit.json missing or not clean'; continue
        if not os.path.exists(f'{rd}/result.json'): why[s] = f'runs/{R}/{s} has no result.json (round not finished)'; continue
        if os.path.exists(f'{rd}/model_engine.json'):  # the model must value this exact set, as the nightly's combine will see it
            sys.path.insert(0, V); import combine
            sm, sm_why = combine.standard_model(R, s, 'clean')
            if not sm: why[s] = f'runs/{R}/{s}: standard model not usable ({sm_why})'; continue
        e = pend.get(s) or {}
        if e.get('status') == 'pending': why[s] = f'data/name_updates.json holds {s} pending: run name_update.py finish first'; continue
        files = set(walk(rd))
        for f in (f'{V}/priors/{R}/{s}.json', f'{V}/priors/{R}/1m/{s}.json'):
            if os.path.exists(f): files.add(f)
        # what combine.py reads to re-mark at D on this round; the slice already holds the nightly's copy of anything shared
        own = (f'{V}/runs/{R}/{s}/', f'{V}/priors/{R}/')
        for f in traced('combine.py', D0, s, '--research', R, '--dry-run'):
            if f.startswith(own) or not os.path.exists(os.path.join(OUT, os.path.relpath(f, ROOT))): files.add(f)
        for f in files:
            dst = os.path.join(OUT, os.path.relpath(f, ROOT)); os.makedirs(os.path.dirname(dst), exist_ok=True); shutil.copyfile(f, dst)
        got[s] = R
    meta['research'] = got
    json.dump(meta, open(f'{OUT}/slice.json', 'w'))
    print(json.dumps(dict(date=D0, base=meta['base'], research=got, refused=why)))
    return [x for x in syms if x in got and x not in why]

NOW = time.time()
try: PREV = float(json.load(open(f'{OUT}/slice.json')).get('time') or 0)
except Exception: PREV = 0.0  # no stamp: recopy everything once
MARGIN = 600  # network-mount clocks can drift; anything touched near the last slice is recopied

def copy(src):
    rel = os.path.relpath(src, ROOT); dst = os.path.join(OUT, rel)
    # past runs rarely change: reuse the last copy unless the source was edited after that slice (a same-size
    # edit of runs/2026-10-28/GOOGL/supervisor.json slipped through a size-only check on 2026-11-02)
    fresh = '/runs/' in rel and f'/runs/{D}/' not in rel
    if fresh and os.path.exists(dst):
        st = os.stat(src)
        if os.path.getsize(dst) == st.st_size and st.st_mtime < PREV - MARGIN: return 0
    os.makedirs(os.path.dirname(dst), exist_ok=True); shutil.copyfile(src, dst); return 1

if __name__ == '__main__':
    if '--research' in ARGS:
        rest = ARGS[ARGS.index('--research') + 1:]
        syms = [a.upper() for a in rest[:next((i for i, a in enumerate(rest) if a.startswith('--')), len(rest))]]
        if not syms: sys.exit('--research needs at least one symbol')
        sys.exit(0 if research(syms) else 2)  # 2: every symbol was refused (refusals print either way)
    files = sorted(set(wanted()))
    with cf.ThreadPoolExecutor(16) as ex: n = sum(ex.map(copy, files))
    keep = {os.path.relpath(p, ROOT) for p in files}
    gone = 0
    for p in walk(OUT):
        rel = os.path.relpath(p, OUT)
        if rel not in keep and rel not in ('base.json', 'slice.json') and rel.split(os.sep)[0] != '.git': os.remove(p)  # .git is a folder in a clone, a file in a worktree; gone += 1
    h = open(f'{V}/report.html').read()
    m = re.search(r'<script id="data" type="application/json">(.*?)</script>', h, re.S)
    base = json.loads(m.group(1).replace('<\\/', '</'))
    json.dump(base, open(f'{OUT}/base.json', 'w'))
    key = base_key(base)
    json.dump(dict(date=D, base=key, time=NOW), open(f'{OUT}/slice.json', 'w'))
    print(json.dumps(dict(date=D, files=len(files), copied=n, removed=gone, base=key)))
