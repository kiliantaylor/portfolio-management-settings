"""Drivers shared by several names live in ONE parameter file, driver_models/shared/<id>.json, and every name's set takes its
quarter values from it (Kilian 2026-11-05, after AVGO and NVDA each re-set dc_mor three times and drifted apart). Change the
parameter file, then `fill` each name; name_update.py finish refuses a round whose shared driver differs from the file.

    python3 shared_drivers.py check [D]          every name in a shared file: does its current research round match? (D: the
                                                 runs/D result.json research_as_of; default: the name's newest round with the driver)
    python3 shared_drivers.py fill U SYM         write the file's quarter values into runs/U/SYM/supervisor.json (backup kept)
"""
import glob, json, os, shutil, sys
HERE = os.path.dirname(os.path.abspath(__file__))
SHARED = os.path.join(HERE, 'driver_models', 'shared')
TOL = 5e-4  # the sets store 5 decimals; a real parameter change moves quarters by 1e-3 or more


def specs():
    return {json.load(open(f))['id']: json.load(open(f)) for f in sorted(glob.glob(os.path.join(SHARED, '*.json')))}


def _month(spec, ym):
    w = spec['scenarios']; v = 0.0
    for st in spec['states'].values():
        if st['from'] <= ym and (st.get('to') is None or ym <= st['to']): v += st['share'] * sum(w[k] for k in st['in'])
    return spec['us_share'] * spec['early_stage'] * v


def quarters(spec, ends):
    """One value per fiscal quarter: the mean of the three calendar months it covers (an end on day 1-14 closes the month before)."""
    out = []
    for e in ends:
        y, m, d = map(int, e.split('-'))
        if d < 15: y, m = (y, m - 1) if m > 1 else (y - 1, 12)
        ms = [((y * 12 + m - 1 - k) // 12, (y * 12 + m - 1 - k) % 12 + 1) for k in range(3)]
        out.append(round(sum(_month(spec, f'{a:04d}-{b:02d}') for a, b in ms) / 3, 5))
    return out


def _set(path):
    S = json.load(open(path)); return S, ((S.get('model') or {}).get('reconciled_inputs') or {})


def _spec(sym, did):
    sp = specs().get(did)
    if not sp or sym not in (sp.get('names') or []): return None  # another name's own driver may share the id
    o = (sp.get('name_overrides') or {}).get(sym) or {}  # a name's own exposure (MOD's Americas DC share; Kilian card 2026-11-06)
    return dict(sp, **{k: v for k, v in o.items() if k in ('us_share', 'early_stage')})


def diff(path, sym, sid=None):
    """[(driver id, worst quarter gap, quarter label)] for `sym`'s shared drivers in the set at `path` that differ from their file."""
    S, ri = _set(path); bad = []
    for x in ri.get('drivers') or []:
        sp = _spec(sym, x.get('id'))
        if not sp or (sid and x['id'] != sid) or not x.get('quarters'): continue
        Q = ri.get('quarters') or {}; want = quarters(sp, Q['ends'])
        g = max(((abs(a - b), Q['labels'][i]) for i, (a, b) in enumerate(zip(x['quarters'], want))), default=(0, None))
        if g[0] > TOL or len(want) != len(x['quarters']): bad.append((x['id'], round(g[0], 5), g[1]))
    return bad


def fill(U, s):
    p = os.path.join(HERE, 'runs', U, s, 'supervisor.json'); S, ri = _set(p); done = []
    for x in ri.get('drivers') or []:
        sp = _spec(s, x.get('id'))
        if sp:
            x['quarters'] = quarters(sp, ri['quarters']['ends']); done.append(x['id'])
            x['shared_file'] = f"driver_models/shared/{x['id']}.json (as_of {sp.get('as_of')})"
    if done:
        shutil.copy(p, p + '.bak-shared'); json.dump(S, open(p, 'w'), indent=1)
    print(json.dumps(dict(symbol=s, round=U, filled=done)))


def current_round(s, D=None):
    if D:
        try: return json.load(open(os.path.join(HERE, 'runs', D, s, 'result.json'))).get('research_as_of')
        except OSError: return None
    for f in sorted(glob.glob(os.path.join(HERE, 'runs', '*', s, 'supervisor.json')), reverse=True):
        if any(_spec(s, x.get('id')) for x in _set(f)[1].get('drivers') or []): return f.split('/')[-3]


def check(D=None):
    out = []
    for sid, sp in specs().items():
        for s in sp.get('names') or []:
            r = current_round(s, D)
            p = os.path.join(HERE, 'runs', r or '-', s, 'supervisor.json')
            if not r or not os.path.exists(p): out.append(dict(symbol=s, driver=sid, problem='no research round found')); continue
            ids = [x.get('id') for x in _set(p)[1].get('drivers') or []]
            if sid not in ids: out.append(dict(symbol=s, driver=sid, round=r, problem='driver missing from the set')); continue
            for d in diff(p, s, sid): out.append(dict(symbol=s, driver=sid, round=r, problem=f'differs from the shared file by {d[1]} ({d[2]})'))
    return out


if __name__ == '__main__':
    a = sys.argv[1:]
    if a[:1] == ['check']:
        r = check(a[1] if len(a) > 1 else None); print(json.dumps(dict(ok=not r, problems=r), indent=1)); sys.exit(1 if r else 0)
    elif a[:1] == ['fill'] and len(a) == 3: fill(a[1], a[2].upper())
    else: print(__doc__); sys.exit(1)
