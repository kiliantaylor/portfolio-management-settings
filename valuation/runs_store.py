"""Read a run file whether it is still loose in runs/ or packed into the monthly archive (Kilian card 2026-11-14
"45-day window + monthly archive"; packer: routines/runs_archive.py).

Run days older than 45 days are packed into valuation/runs_archive/YYYY-MM.zip, one member per original file at its
original relative path (e.g. 2026-09-25/ABT/result.json), byte for byte. Each name's newest copy of every file, its newest
run folder and its recent research rounds stay loose, so most scripts never need this. Scripts that look further back
(e.g. scoring/scorecard.py reading a year-old forecast's result.json) read through here.

    import runs_store            # load by path in Actions: exec(open('.../valuation/runs_store.py').read(), g)
    runs_store.read_bytes('2026-09-25', 'ABT', 'result.json')   -> bytes or None
    runs_store.load_json('2026-09-25', 'ABT', 'result.json')    -> parsed JSON or None
    runs_store.exists('2026-09-25', 'ABT', 'result.json')
    runs_store.dates_with('ABT', 'result.json')                 -> sorted dates, loose and archived
"""
import glob, json, os, zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
RUNS = os.path.join(HERE, 'runs')
ARCH = os.path.join(HERE, 'runs_archive')
_ZIPS = {}


def _zip(month):
    if month not in _ZIPS:
        p = os.path.join(ARCH, f'{month}.zip')
        try: z = zipfile.ZipFile(p)
        except (OSError, zipfile.BadZipFile): z = None
        _ZIPS[month] = (z, set(z.namelist()) if z else set())
    return _ZIPS[month]


def _rel(date, sym, name):
    return '/'.join(x for x in (date, sym, name) if x)


def read_bytes(date, sym, name):
    """sym may be '' for a day-level file (runs/DATE/portfolio.json)."""
    p = os.path.join(RUNS, *[x for x in (date, sym, name) if x])
    try:
        with open(p, 'rb') as f: return f.read()
    except OSError: pass
    z, names = _zip(date[:7])
    r = _rel(date, sym, name)
    return z.read(r) if z and r in names else None


def load_json(date, sym, name):
    b = read_bytes(date, sym, name)
    if b is None: return None
    try: return json.loads(b)
    except ValueError: return None


def exists(date, sym, name):
    if os.path.exists(os.path.join(RUNS, *[x for x in (date, sym, name) if x])): return True
    return _rel(date, sym, name) in _zip(date[:7])[1]


def dates_with(sym, name):
    out = {f.split(os.sep)[-3] for f in glob.glob(os.path.join(RUNS, '*', sym, name))}
    for p in glob.glob(os.path.join(ARCH, '*.zip')):
        _, names = _zip(os.path.basename(p)[:-4])
        out |= {n.split('/')[0] for n in names if n.split('/', 1)[-1] == f'{sym}/{name}'}
    return sorted(out)


PRI = os.path.join(HERE, 'priors')
PARCH = os.path.join(HERE, 'priors_archive')
_PZ = {}


def prior_json(date, sym, sub=''):
    """priors/DATE/[SUB/]SYM.json whether loose or packed into priors_archive/YYYY-MM.zip (2026-11-24, runs_archive.py)."""
    rel = '/'.join(x for x in (date, sub, f'{sym}.json') if x)
    p = os.path.join(PRI, *rel.split('/'))
    try: return json.load(open(p))
    except OSError: pass
    m = date[:7]
    if m not in _PZ:
        try: z = zipfile.ZipFile(os.path.join(PARCH, f'{m}.zip')); _PZ[m] = (z, set(z.namelist()))
        except (OSError, zipfile.BadZipFile): _PZ[m] = (None, set())
    z, names = _PZ[m]
    return json.loads(z.read(rel)) if z and rel in names else None
