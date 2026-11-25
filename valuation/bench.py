"""The bench (Kilian 2026-11-11, threads "Pick the 6-name bench" and "Add AI correlation rule and bench").

The bench is the short list of unheld names ready to buy when one comes within reach; it is the second tab on Price Targets
(report_template.html benchPanel; build_report.py reads data/bench.json). You pick its names by hand.

Upkeep rule (Kilian card 2026-11-11 19:05Z "Replace-weakest only"): the bench holds at most max_size (8 since 2026-11-14; 6 before) names. While it has
room a name joins as is. When it is full, a new name joins only by replacing the weakest bench name. There is no automatic
drop and no archiving: a name leaves only when you remove it or when it is the weakest and a new name replaces it.

Weakest = farthest above its Entry below level (gap = last / Entry below - 1, largest gap), i.e. the price would have to
fall the most before a buy could pass the buy floor. Entry below = (target + 12-month dividends) / (1 + beta-adjusted return)
from the name's newest mark (result.json target, allocation.json beta_bar, divs.dps), the same level the page shows.
A name with no mark ranks weakest. A name joins only with a driver model (no buys without one, last_print.driver_model).

Auto-add of sold names (Kilian 2026-11-14, thread "Auto-bench formerly held names": "formerly held names go to the bench
automatically unless i say otherwise"): a name whose position in data/trades.json goes to zero shares on or after
bench.json auto.from (2026-11-10) joins the bench by the same add() rule (replaces the weakest when full; driver model
required, else it waits and is retried each night). Skipped: names in auto.exclude (AMZN, Kilian 11-14 "leave amzn off for
now"), and a name taken off (remove) or replaced out after its last full sale, until it is bought and fully sold again.
nightly.py runs auto_add(D) first thing each full night; trade booking needs no hook because the nightly reads trades.json.

    python3 bench.py status [D]               the bench ranked by gap, weakest last
    python3 bench.py add SYM [D] --why TEXT   add SYM; when full it replaces the weakest (printed, logged)
    python3 bench.py remove SYM --why TEXT    take SYM off by hand
    python3 bench.py auto [D] [--dry]         add the fully sold names (the nightly runs this)
"""
import datetime as dt, glob, json, os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
PATH = os.path.join(HERE, 'data', 'bench.json')
MAX_SIZE = 8  # Kilian 2026-11-14 'the bench can have 8 spots' (was 6, card 11-11)


def load():
    try: b = json.load(open(PATH))
    except (OSError, ValueError): b = {}
    b.setdefault('names', []); b.setdefault('max_size', MAX_SIZE); b.setdefault('log', [])
    return b


def save(b):
    tmp = PATH + '.tmp'; json.dump(b, open(tmp, 'w'), indent=1); os.replace(tmp, PATH)


def _newest(s, D, f):
    for d in sorted((os.path.basename(os.path.dirname(os.path.dirname(p))) for p in glob.glob(os.path.join(HERE, 'runs', '2*', s, f))), reverse=True):
        if d <= D:
            try: return json.load(open(os.path.join(HERE, 'runs', d, s, f))), d
            except (OSError, ValueError): pass
    return None, None


def mark(s, D):
    """{spot, target, entry_below, gap, as_of} from the newest mark on or before D, or None."""
    r, d = _newest(s, D, 'result.json')
    if not r or not r.get('spot') or not r.get('target'): return None
    a = (json.load(open(os.path.join(HERE, 'runs', d, s, 'allocation.json'))) if os.path.exists(os.path.join(HERE, 'runs', d, s, 'allocation.json')) else {}) or {}
    bb = a.get('beta_bar')
    if bb is None: return dict(spot=r['spot'], target=r['target'], entry_below=None, gap=None, as_of=d, research_as_of=r.get('research_as_of'))
    sys.path.insert(0, HERE)
    try: import divs; dv = float(divs.dps(s) or 0)
    except Exception: dv = 0.0
    E = (r['target'] + dv) / (1 + bb)
    return dict(spot=r['spot'], target=r['target'], entry_below=round(E, 2), gap=round(r['spot'] / E - 1, 4), as_of=d, research_as_of=r.get('research_as_of'))


def ranked(names, D):
    """[(sym, mark or None)] strongest first: smallest gap first, no mark last."""
    m = {s: mark(s, D) for s in names}
    return sorted(m.items(), key=lambda kv: (kv[1] is None or kv[1]['gap'] is None, (kv[1] or {}).get('gap') or 0))


def weakest(names, D):
    return ranked(names, D)[-1][0] if names else None


def _today():
    return dt.date.today().isoformat()


def add(sym, D, why):
    b = load(); sym = sym.upper()
    if sym in b['names']: return f'{sym} is already on the bench'
    sys.path.insert(0, HERE); import last_print
    m = mark(sym, D)
    if not last_print.driver_model(sym, (m or {}).get('research_as_of')): raise SystemExit(f'{sym}: no driver model; a name joins the bench only with one')
    out = None
    if len(b['names']) >= b['max_size']:
        out = weakest(b['names'], D); b['names'].remove(out)
    b['names'].append(sym)
    b['log'].append(dict(date=_today(), add=sym, replaced=out, why=why, gap=(m or {}).get('gap')))
    save(b)
    return f'{sym} added' + (f', replacing the weakest, {out}' if out else '') + f'; bench {len(b["names"])}/{b["max_size"]}: ' + ' '.join(b['names'])


def exits():
    """{sym: date the position last went to zero} for names held at some point and holding zero shares now."""
    T = json.load(open(os.path.join(HERE, 'data', 'trades.json')))['trades']
    pos, out = {}, {}
    for t in sorted(T, key=lambda x: x['date']):
        s = t['symbol']; pos[s] = pos.get(s, 0) + (t['shares'] if t['side'] == 'buy' else -t['shares'])
        if abs(pos[s]) < 1e-9: out[s] = t['date']
        else: out.pop(s, None)
    return out


def auto_add(D=None, dry=False):
    """Add each fully sold name (sold out on or after auto.from) not on the bench, not excluded, not taken off since. Returns notes."""
    D = D or _today(); b = load(); a = b.get('auto') or {}
    if not a.get('on', True): return []
    frm, excl, notes = a.get('from', '2026-11-10'), a.get('exclude') or {}, []
    for s, d in sorted(exits().items()):
        if d < frm or s in b['names']: continue
        if s in excl: notes.append(f'{s}: sold out {d}, excluded ({excl[s]})'); continue
        if any(e.get('date', '') >= d and s in (e.get('remove'), e.get('replaced')) for e in b['log']):
            notes.append(f'{s}: sold out {d}, taken off the bench since; not re-added'); continue
        if dry: notes.append(f'{s}: sold out {d}, would be added'); continue
        try: notes.append('auto: ' + add(s, D, f'auto: fully sold {d} (rule Kilian 2026-11-14)')); b = load()
        except SystemExit as e: notes.append(f'{s}: sold out {d}, waits: {e}')
    return notes


def remove(sym, why):
    b = load(); sym = sym.upper()
    if sym not in b['names']: return f'{sym} is not on the bench'
    b['names'].remove(sym); b['log'].append(dict(date=_today(), remove=sym, why=why)); save(b)
    return f'{sym} removed; bench {len(b["names"])}/{b["max_size"]}: ' + ' '.join(b['names'])


if __name__ == '__main__':
    a = sys.argv[1:]
    why = None
    if '--why' in a: i = a.index('--why'); why = a[i + 1]; del a[i:i + 2]
    if not a: print(__doc__); sys.exit(1)
    if a[0] == 'status':
        D = a[1] if len(a) > 1 else _today(); b = load()
        print(f'bench {len(b["names"])}/{b["max_size"]} (marks on or before {D}); strongest first, weakest last')
        for s, m in ranked(b['names'], D):
            print(f"  {s:6}" + (f" last {m['spot']:.2f}  Entry below {m['entry_below']:.2f}  gap {m['gap']:+.1%}  (mark {m['as_of']})" if m and m['gap'] is not None else '  no mark'))
    elif a[0] == 'auto':
        dry = '--dry' in a; a = [x for x in a if x != '--dry']
        for n in auto_add(a[1] if len(a) > 1 else _today(), dry) or ['nothing to add']: print(n)
    elif a[0] in ('add', 'remove'):
        if len(a) < 2 or not why: print('needs SYM and --why'); sys.exit(1)
        print(add(a[1], a[2] if len(a) > 2 else _today(), why) if a[0] == 'add' else remove(a[1], why))
    else: print(__doc__); sys.exit(1)
