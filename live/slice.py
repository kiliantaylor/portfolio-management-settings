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
OLD_RUN = re.compile(r'/runs/\d{4}-\d\d-\d\d/[A-Z.]+/((supervisor|agent_[1-5]|memo|model_engine|result|allocation|review)\.json|std_model_[A-Z.]+\.xlsx)$')

def walk(base, keep=lambda p: True):
    for dp, dn, fn in os.walk(base):
        for f in fn:
            p = os.path.join(dp, f)
            if not SKIP.search(p) and keep(p): yield p

def wanted():
    yield from glob.glob(f'{V}/*.py')
    yield f'{V}/report_template.html'
    yield f'{ROOT}/adhoc/page_search.py'
    yield f'{ROOT}/risk/riskcalc.py'
    yield from glob.glob(f'{ROOT}/risk/model/*.json')
    yield from (p for p in glob.glob(f'{V}/data/*') if os.path.isfile(p) and p.endswith(('.json', '.csv')) and not SKIP.search(p))
    yield from walk(f'{ROOT}/portfolio/data')
    yield from walk(f'{V}/priors/{D}')
    yield from glob.glob(f'{V}/ideas/*/*.json')
    yield from glob.glob(f'{V}/scoring/*.json')
    yield from sorted(glob.glob(f'{ROOT}/market_color/beats_check/*.json'))[-10:]  # active_names.py: in-reach check
    yield from walk(f'{V}/runs/{D}', lambda p: p.endswith(('.json', '.xlsx')))
    for d in glob.glob(f'{V}/runs/*/'):
        if os.path.basename(d.rstrip('/')) != D: yield from walk(d, lambda p: bool(OLD_RUN.search(p)))

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
        if rel.split('/')[0] in ('valuation', 'portfolio', 'risk', 'adhoc', 'market_color') and rel not in keep: os.remove(p); gone += 1
    h = open(f'{V}/report.html').read()
    m = re.search(r'<script id="data" type="application/json">(.*?)</script>', h, re.S)
    base = json.loads(m.group(1).replace('<\\/', '</'))
    json.dump(base, open(f'{OUT}/base.json', 'w'))
    key = base_key(base)
    json.dump(dict(date=D, base=key), open(f'{OUT}/slice.json', 'w'))
    print(json.dumps(dict(date=D, files=len(files), copied=n, removed=gone, base=key)))
