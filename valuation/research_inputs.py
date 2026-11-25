"""Research inputs the ensemble score (score.py) reads from a name's newest gated research round: the reconciled fair
value and the supervisor's open flags (unresolved fact-check failure, thesis-break, accounting or credit flag).
2026-11-09 (Kilian "get rid of conviction rubric"): split out of rubric.py when the rubric was removed; same file
look-ups and same values, without the conviction label, book dP or bars. rubric.py and book_dp.py are retired.
Usage: python3 research_inputs.py SYM ...
"""
import glob, json, os, sys
HERE = os.path.dirname(os.path.abspath(__file__))

_IDX, _NEW = {}, {}
def _newest(sym, name):
    """The last of sorted(glob(runs/*/*/name)) whose folder is sym, i.e. sym's newest copy of the file.
    2026-11-02 (Kilian: "everything to run as fast as possible"): checks runs/<date>/sym/name newest first and stops at
    the first hit (a few look-ups) instead of listing all ~2,500 runs/<date>/<sym> folders (~27s on the shared folder).
    Same answer: glob's '*' skips names starting with '.', and the candidates are tried in the same full-path order."""
    if sym.startswith('.') or glob.has_magic(sym) or os.sep in sym:  # not a plain folder name: the old full listing
        if name not in _IDX:
            _IDX[name] = {}
            for f in sorted(glob.glob(os.path.join(HERE, 'runs', '*', '*', name))): _IDX[name][f.split(os.sep)[-2]] = f
        return _IDX[name].get(sym)
    if (sym, name) not in _NEW:
        runs = os.path.join(HERE, 'runs')
        try: ds = [d for d in os.listdir(runs) if not d.startswith('.')]
        except OSError: ds = []
        _NEW[sym, name] = next((p for p in sorted((os.path.join(runs, d, sym, name) for d in ds), reverse=True) if os.path.lexists(p)), None)
    return _NEW[sym, name]

def warm(syms):
    """Read the files inputs() reads for these names, 12 at a time, when the read-once cache (fscache.py) is on, so the
    callers' one-name-at-a-time loops find them in memory (2026-10-21; labels unchanged)."""
    fc = sys.modules.get('fscache')
    if not (fc and fc._real): return
    import concurrent.futures as cf
    def one(s):
        try: inputs(s)
        except Exception: pass
    with cf.ThreadPoolExecutor(12) as ex: list(ex.map(one, syms))


def inputs(sym):
    rp = _newest(sym, 'result.json')
    g = (json.load(open(rp)).get('citation_gate') or {}) if rp else {}  # 2026-10-06: only research that passed the citation gate
    sp = os.path.join(HERE, 'runs', g['research'], sym, 'supervisor.json') if g.get('research') else _newest(sym, 'supervisor.json')
    if sp and not os.path.exists(sp): sp = None
    if not sp or not rp: return None
    sup, res = json.load(open(sp)), json.load(open(rp))
    sz = sup.get('sizing') or {}
    if not sz.get('confidence') and os.path.exists(os.path.join(os.path.dirname(sp), 'sizing_pass.json')):
        sz = json.load(open(os.path.join(os.path.dirname(sp), 'sizing_pass.json')))
    sfv = sup.get('fair_value'); sfv = sfv.get('value') if isinstance(sfv, dict) else sfv
    flags = [str(f) for f in (sz.get('open_flags') or [])]
    return dict(fair_value=res.get('fair_value_median') or sfv, open_flags=flags, research=sp.split('/')[-3], priced=rp.split('/')[-3])

if __name__ == '__main__':
    for s in sys.argv[1:]: print(s, inputs(s))
