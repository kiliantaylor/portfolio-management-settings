"""Which researched names are "active" (main Price Targets page, nightly re-mark) and which go to the archive.

Rule (2026-10-19, page over the 16MB cap: 13.7MB for 112 names; tightened 2026-10-21, Kilian 21:10Z "archive the ones that are
not close to being attractive entries"). A name is active if any of these hold on market date D:
  held        a position in the live book (book.load(), which follows data/trades.json)
  committed   runs/B/portfolio.json sharpe_combo.committed (planned buys, e.g. APP)
  candidate   allocation.json shares > 0 (a suggested buy), or review.json action 'add'
  in reach    the Sharpe swap check (market_color/beats_check/<date>.json, the newest file on or before D that scored the
              name) has it as eligible with a book-Sharpe change of at least -REACH (-0.005); names it gated out (target
              below the last price, excluded, ...) are not in reach
  order       an open limit order or a staged tranche (data/review_settings.json limit_orders / staged_entries),
              or a memo asked for by name (review_settings memo_extra)
  update      a model update in progress (data/name_updates.json status pending, name_update.py)
  research    the name's newest research (a runs/<date>/SYM folder with supervisor.json) is at most WINDOW_DAYS (7) before D
Before 2026-10-21 every sharpe_combo candidate and every medium/high rubric label counted, and research for 21 days, which
kept ~104 of 114 names active. Archived names are still re-marked on Fridays (nightly.py, all names), and that night's
swap check scores them again, so a name that comes back within reach returns to the main page the next weekday.
B is the newest run date on or before D with a portfolio.json (D itself once allocate.py has run). The research date is the
newest of result.json research_as_of at B and any research folder dated after B (a full run that has not been re-marked
yet). Every other name in the universe is archived. removed_names never appear anywhere.
Universe = names with a result.json in any run folder dated within UNIVERSE_DAYS (14) before B, up to B (so the last
Friday's full re-mark is always included, even when weekday runs re-mark the active names only), plus names with a finished
research folder (supervisor.json + result.json) after B. Each name's own files are read from its newest such folder.

Usage: python3 active_names.py D [--archive | --json]   (prints the active names, the archive names, or both with reasons)
Library: active_names(D) -> sorted list; active_names(D, detail=True) -> {SYM: [reasons]}; universe(D); archive_names(D).
The data folder is a slow network mount (~50ms per file), so reads run in a thread pool and results are cached per D."""
import concurrent.futures as cf, datetime as dt, json, os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
RUNS = os.path.join(HERE, 'runs')
WINDOW_DAYS = 7  # 21 until 2026-10-21
REACH = 0.005  # book-Sharpe change (beats_check delta) below which an unheld name is out of reach
UNIVERSE_DAYS = 14
_CACHE = {}


def _load(p):
    try:
        with open(p) as f: return json.load(f)
    except Exception: return None


def _dates():
    return sorted(d for d in os.listdir(RUNS) if len(d) == 10 and d[4] == '-' and os.path.isdir(os.path.join(RUNS, d)))


def _base(D, dates):
    """Newest run date <= D with portfolio.json (the last finished allocation), else the newest run date <= D."""
    for d in reversed([d for d in dates if d <= D]):
        if os.path.exists(os.path.join(RUNS, d, 'portfolio.json')): return d
    older = [d for d in dates if d <= D]
    return older[-1] if older else None


def _state(D):
    if D in _CACHE: return _CACHE[D]
    sys.path.insert(0, HERE)
    import book
    cfg = _load(os.path.join(HERE, 'data', 'review_settings.json')) or {}
    removed = set(cfg.get('removed_names') or [])
    dates = _dates(); B = _base(D, dates)
    last = {}  # SYM -> newest run date (within UNIVERSE_DAYS, up to B) holding its result.json
    if B:
        lo = (dt.date.fromisoformat(B) - dt.timedelta(days=UNIVERSE_DAYS)).isoformat()
        for d in sorted((d for d in dates if lo <= d <= B), reverse=True):
            for x in os.listdir(os.path.join(RUNS, d)):
                if x not in last and not x.endswith('.json') and os.path.exists(os.path.join(RUNS, d, x, 'result.json')): last[x] = d
    syms = sorted(last)
    files = {}
    with cf.ThreadPoolExecutor(16) as ex:
        jobs = {ex.submit(_load, os.path.join(RUNS, last[s], s, f)): (s, f) for s in syms for f in ('result.json', 'allocation.json', 'review.json')}
        later = [d for d in dates if B and B < d <= max(D, dt.date.today().isoformat())]  # research finished after the last re-mark
        lsyms = {d: [s for s in os.listdir(os.path.join(RUNS, d)) if os.path.isdir(os.path.join(RUNS, d, s))] for d in later}
        ljobs = {ex.submit(lambda d, s: os.path.exists(os.path.join(RUNS, d, s, 'supervisor.json')) and os.path.exists(os.path.join(RUNS, d, s, 'result.json')), d, s): (d, s)
                 for d in later for s in lsyms[d]}
        for j, k in jobs.items(): files[k] = j.result()
        new_research = {}
        for j, (d, s) in ljobs.items():
            if j.result() and d > new_research.get(s, ''): new_research[s] = d
    universe = sorted(({s for s in syms if files.get((s, 'result.json'))} | set(new_research)) - removed)
    port = _load(os.path.join(RUNS, B, 'portfolio.json')) if B else None
    combo = (port or {}).get('sharpe_combo') or {}
    why = {s: [] for s in universe}
    add = lambda s, r: why[s].append(r) if s in why and r not in why[s] else None
    for p in book.load().get('positions') or []:
        if p.get('shares'): add(p['symbol'], 'held')
    for s in (combo.get('committed') or {}): add(s, 'committed')
    for s, (dl, bd) in _reach(D, universe).items():
        if dl is not None and dl >= -REACH: add(s, f'in reach (Sharpe {dl:+.4f}, {bd})')
    try: upd = _load(os.path.join(HERE, 'data', 'name_updates.json')) or {}
    except Exception: upd = {}
    for s, u in (upd.get('names') or {}).items():
        if isinstance(u, dict) and u.get('status') == 'pending': add(s, 'update pending')
    for s in (cfg.get('limit_orders') or {}): add(s, 'limit order')
    for s, v in (cfg.get('staged_entries') or {}).items():
        if not s.startswith('_') and isinstance(v, dict): add(s, 'staged tranche')
    for s in cfg.get('memo_extra') or []: add(s, 'memo on request')
    cut = (dt.date.fromisoformat(D) - dt.timedelta(days=WINDOW_DAYS)).isoformat()
    research = {}
    for s in universe:
        a = files.get((s, 'allocation.json')) or {}
        if (a.get('shares') or 0) > 0: add(s, 'suggested buy')
        if (files.get((s, 'review.json')) or {}).get('action') == 'add': add(s, 'add')
        rd = max(filter(None, [(files.get((s, 'result.json')) or {}).get('research_as_of'), new_research.get(s)]), default=None)
        research[s] = rd
        if rd and cut <= rd <= D or s in new_research: add(s, f'research {rd}')
    _CACHE[D] = dict(base=B, universe=universe, why=why, research=research, last=last)
    return _CACHE[D]


def _reach(D, syms):
    """{SYM: (book-Sharpe delta or None if gated out, beats_check date)} from the newest swap-check file on or before D that
    scored each name (weekday checks score the active names only; Friday's scores every name)."""
    bd = os.path.join(os.path.dirname(HERE), 'market_color', 'beats_check')
    try: files = sorted((f for f in os.listdir(bd) if f.endswith('.json') and f[:-5] <= D), reverse=True)
    except FileNotFoundError: return {}
    out, want = {}, set(syms)
    for f in files:
        b = _load(os.path.join(bd, f)) or {}
        for r in b.get('candidates') or []:
            if r.get('sym') in want and r['sym'] not in out: out[r['sym']] = (r.get('delta'), f[:-5])
        for x in b.get('gated_out') or []:
            if x in want and x not in out: out[x] = (None, f[:-5])
        if want <= set(out): break
    return out


def universe(D):
    return list(_state(D)['universe'])


def active_names(D, detail=False):
    w = _state(D)['why']
    return {s: r for s, r in w.items() if r} if detail else sorted(s for s, r in w.items() if r)


def archive_names(D):
    w = _state(D)['why']
    return sorted(s for s, r in w.items() if not r)


if __name__ == '__main__':
    D = sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].startswith('-') else dt.date.today().isoformat()
    if '--json' in sys.argv:
        st = _state(D)
        print(json.dumps(dict(D=D, base=st['base'], active=active_names(D, detail=True), archive=archive_names(D)), indent=1))
    else:
        print(' '.join(archive_names(D) if '--archive' in sys.argv else active_names(D)))
