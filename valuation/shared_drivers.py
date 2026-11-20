"""Drivers shared by several names live in ONE parameter file, driver_models/shared/<id>.json, and every name's set takes its
quarter values from it (Kilian 2026-11-05, after AVGO and NVDA each re-set dc_mor three times and drifted apart). Change the
parameter file, then `fill` each name; name_update.py finish refuses a round whose shared driver differs from the file.

    python3 shared_drivers.py check [D]          every name in a shared file: does its current research round match? (D: the
                                                 runs/D result.json research_as_of; default: the name's newest round with the driver)
    python3 shared_drivers.py fill U SYM         write the file's quarter values into runs/U/SYM/supervisor.json (backup kept)

Self-funded carve-out (Kilian 2026-11-16 11:11Z: "leave the estimated portion from self-funding hyperscalers untouched and then
apply dc_mor on the remaining share of revenue"): a name's `name_overrides.SYM.self_funded` is the estimated share of its
exposed revenue from self-funding hyperscalers (Microsoft, Amazon, Alphabet, Meta). dc_mor's quarters stay the gross haircut;
the name's set carries a flat driver `hs_self_funded` with that value and applies (1 - hs_self_funded) x dc_mor wherever it
used dc_mor. `fill` writes (or creates) the hs_self_funded driver; `diff` refuses a set whose hs_self_funded differs from the
file or whose build uses dc_mor without hs_self_funded. A value waiting for its round to finish sits in
`self_funded_pending` (not checked). self_funded is a number (flat) or {"FY2028": v, "default": v} by fiscal year and moves to `self_funded` right before `name_update.py finish` (as with pending_names).
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
    return dict(sp, **{k: v for k, v in o.items() if k in ('us_share', 'early_stage', 'self_funded', 'self_funded_source')})


HS = 'hs_self_funded'


def sf_path(sf, labels):
    """self_funded per quarter: a number (flat) or {"FY2028": v, ..., "default": v} keyed by the label's fiscal year;
    a key equal to a full quarter label (e.g. "Q2 FY2027") wins over its fiscal-year key (CRDO 2026-11-16: a path
    that falls 1pt a quarter)."""
    if not isinstance(sf, dict): return [round(float(sf), 5)] * len(labels)
    return [round(float(sf.get(l, sf.get(l.split()[-1], sf.get('default')))), 5) for l in labels]


def _hs_driver(sp, labels):
    q = sf_path(sp['self_funded'], labels); v = q[-1]; n = len(labels)
    fy = {}
    for l, x in zip(labels, q): fy.setdefault(l.split()[-1], x)
    return dict(id=HS, label='Share of exposed revenue from self-funding hyperscalers (Microsoft, Amazon, Alphabet, Meta), left untouched by dc_mor',
                unit='fraction', base=q[0], years=list(fy.values())[-3:], basis='estimate', agg='mean', quarters=q,
                source=sp.get('self_funded_source') or '', shared_file='driver_models/shared/dc_mor.json name_overrides',
                why="Kilian 2026-11-16: revenue from self-funding hyperscalers is left untouched by the moratorium haircut; dc_mor applies to the rest.")


def _uses_without_hs(ri):
    """Formula strings in the build that use dc_mor without hs_self_funded."""
    bad = []
    def walk(o):
        if isinstance(o, dict): [walk(v) for k, v in o.items() if k not in ('reported', 'note', 'notes', 'source', 'derivation')]
        elif isinstance(o, list): [walk(v) for v in o]
        elif isinstance(o, str) and 'dc_mor' in o and HS not in o and any(c in o for c in '*+-('): bad.append(o[:80])
    walk(ri.get('driver_build') or {}); return bad


def diff(path, sym, sid=None):
    """[(driver id, worst quarter gap, quarter label)] for `sym`'s shared drivers in the set at `path` that differ from their file."""
    S, ri = _set(path); bad = []
    for x in ri.get('drivers') or []:
        sp = _spec(sym, x.get('id'))
        if not sp or (sid and x['id'] != sid) or not x.get('quarters'): continue
        Q = ri.get('quarters') or {}; want = quarters(sp, Q['ends'])
        g = max(((abs(a - b), Q['labels'][i]) for i, (a, b) in enumerate(zip(x['quarters'], want))), default=(0, None))
        if g[0] > TOL or len(want) != len(x['quarters']): bad.append((x['id'], round(g[0], 5), g[1]))
        if sp.get('self_funded') is not None:
            h = next((y for y in ri.get('drivers') or [] if y.get('id') == HS), None)
            want_h = sf_path(sp['self_funded'], Q.get('labels') or [])
            if not h: bad.append((HS, 'missing', f"file self_funded {sp['self_funded']}"))
            else:
                gh = max((abs(a - b) for a, b in zip(h.get('quarters') or [], want_h)), default=1)
                if gh > TOL or len(h.get('quarters') or []) != len(want_h): bad.append((HS, round(gh, 5), 'vs file self_funded'))
            if _uses_without_hs(ri): bad.append((HS, 'not applied', _uses_without_hs(ri)[0]))
    return bad


def fill(U, s):
    p = os.path.join(HERE, 'runs', U, s, 'supervisor.json'); S, ri = _set(p); done = []
    for x in ri.get('drivers') or []:
        sp = _spec(s, x.get('id'))
        if sp:
            x['quarters'] = quarters(sp, ri['quarters']['ends']); done.append(x['id'])
            x['shared_file'] = f"driver_models/shared/{x['id']}.json (as_of {sp.get('as_of')})"
            if sp.get('self_funded') is not None:
                h = _hs_driver(sp, ri['quarters']['labels'])
                ds = ri['drivers']; i = next((k for k, y in enumerate(ds) if y.get('id') == HS), None)
                if i is None: ds.append(h)
                else: ds[i] = dict(ds[i], **{k: h[k] for k in ('base', 'years', 'quarters', 'shared_file')})
                done.append(HS)
    if done:
        shutil.copy(p, p + '.bak-shared'); json.dump(S, open(p, 'w'), indent=1)
    print(json.dumps(dict(symbol=s, round=U, filled=done)))


def current_round(s, D=None):
    if D:
        # 2026-11-17: a name not re-marked on D yet (an --after-targets publish before the nightly, or stale quotes) stays on
        # the page from its latest run before D, so check the research round of that run, not "none found". Still refuses
        # when no run on or before D has a result.json, or its round's supervisor.json is gone.
        for d in sorted((x for x in os.listdir(os.path.join(HERE, 'runs')) if x[:1].isdigit() and x[:10] <= D), reverse=True):
            try: return json.load(open(os.path.join(HERE, 'runs', d, s, 'result.json'))).get('research_as_of')
            except OSError: continue
        return None
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
