"""Inputs one name's model sets and other names' models use (Kilian 2026-11-23, CRDO update thread: "this needs to be handled
automatically"). NVDA's Nov 17 round lowered the Big-4 capex path (hs_capex) and CRDO, COHR, KEYS and AMZN sat on older paths for
a week: hs_capex is a row of NVDA's own set, not a driver_models/shared/ parameter file, so shared_drivers.py and finish never
saw it. This file closes that loop; it adds no gate and no size threshold.

Registry: driver_models/shared_inputs.json. Each entry: id, owner (the name whose gate-clean set defines it), row (the owner's
driver id), labels (calendar years for the owner's `years` list), values (the owner's last finished round), round, history,
consumers {SYM: rows (where the name uses it), how, taken_round, taken_values, requested_for}.

Automatic, nothing to run by hand:
  - name_update.py finish OWNER: `publish` reads the row from the finished set; when a value moved, the registry takes the new
    path and every consumer on an older path is listed in the finish flags.
  - routines/driver_news.py check D (nightly 4e0): `stale` consumers that have not been asked for this owner round become
    update requests (no news needed, no 5% trigger), or carry lines when an update for that name is already open.
  - name_update.py start SYM: `brief` appends the current path and the rows that use it to the update brief.
  - name_update.py finish SYM (consumer): `take` records the path the finished set uses (reconciled_inputs.shared_inputs.<id>
    .round, else the owner's values found in the set's text). Still on an older path: one flag line, nothing refused.

    python3 shared_inputs.py show                  registry, with each consumer's state
    python3 shared_inputs.py stale                 consumers on an older path than the owner's last finished round
    python3 shared_inputs.py publish U SYM         (finish does this) owner round U -> registry
    python3 shared_inputs.py take U SYM            (finish does this) consumer round U -> taken
    python3 shared_inputs.py brief SYM             the brief section for SYM's next update
    python3 shared_inputs.py add ID OWNER ROW LABEL1,LABEL2,..   register a new shared input (then add consumers in the file)
"""
import datetime as dt, json, os, re, sys
HERE = os.path.dirname(os.path.abspath(__file__)).replace('/mnt/attach/project-files', '/mnt/project-files')
REG = os.path.join(HERE, 'driver_models', 'shared_inputs.json')
RUNS = os.path.join(HERE, 'runs')
DP = 2  # values compare at the precision the sets quote them (750.70 / 1,009.45); no size threshold


def now(): return dt.datetime.now(dt.timezone.utc).strftime('%Y-%m-%dT%H:%MZ')


def load():
    try: return json.load(open(REG))
    except (OSError, ValueError): return dict(entries={})


def save(R): json.dump(R, open(REG, 'w'), indent=1)


def _set(U, s):
    p = os.path.join(RUNS, U, s, 'supervisor.json')
    S = json.load(open(p)); return S, ((S.get('model') or {}).get('reconciled_inputs') or {}), p


def owner_values(U, s, e):
    """The owner's row values in round U, keyed by e['labels'] (None when the set has no such row)."""
    _, ri, _ = _set(U, s)
    x = next((d for d in ri.get('drivers') or [] if d.get('id') == e['row']), None)
    if not x or not x.get(e.get('field', 'years')): return None
    v = x[e.get('field', 'years')]
    return {l: round(float(a), DP) for l, a in zip(e['labels'], v)}


def _same(a, b):
    return bool(a) and bool(b) and set(a) == set(b) and all(round(float(a[k]), DP) == round(float(b[k]), DP) for k in a)


def publish(U, s):
    """Owner finished round U: take its path into the registry. Returns flag lines (consumers now on an older path)."""
    R = load(); out = []
    for i, e in R['entries'].items():
        if e['owner'] != s: continue
        v = owner_values(U, s, e)
        if v is None: out.append(f"shared input {i}: {s} {U} has no row '{e['row']}'; registry kept at {e['round']}"); continue
        if _same(v, e.get('values')):
            e['round'] = U if U > (e.get('round') or '') else e['round']
            for c in (e.get('consumers') or {}).values():  # a consumer on the unchanged path stays current
                if _same(c.get('taken_values'), v): c['taken_round'] = e['round']
            continue
        e.setdefault('history', []).append(dict(round=e.get('round'), values=e.get('values'), replaced_utc=now()))
        e['history'] = e['history'][-20:]
        e.update(values=v, round=U, published_utc=now())
        old = [c for c, x in (e.get('consumers') or {}).items() if not _same(x.get('taken_values'), v)]
        out.append(f"shared input {i} moved ({s} {U}: {', '.join(f'{k} {v[k]:,.2f}' for k in e['labels'])}): "
                   f"{', '.join(old) or 'no consumer'} on an older path; tonight's driver_news check requests their updates")
    if out: save(R)
    return out


def _in_text(t, v):
    """Every value of the path appears in the set's text (as 1,009.45 / 1009.45 / 1,009.5 / 1009.5)."""
    def forms(x):
        return {f'{x:,.2f}', f'{x:.2f}', f'{x:,.1f}', f'{x:.1f}'}
    return all(any(f in t for f in forms(float(x))) for x in v.values())


def take(U, s):
    """Consumer finished round U: record which path its set uses. Returns flag lines."""
    R = load(); out = []; ch = False
    _, ri, p = _set(U, s); t = open(p).read(); decl = ri.get('shared_inputs') or {}
    for i, e in R['entries'].items():
        c = (e.get('consumers') or {}).get(s)
        if c is None: continue
        d = decl.get(i) or {}
        if d.get('round') == e['round'] or _in_text(t, e['values']):
            c.update(taken_round=e['round'], taken_values=dict(e['values']), taken_by=U, taken_utc=now()); ch = True
        else:
            path = ', '.join(f"{k} {e['values'][k]:,.2f}" for k in e['labels'])
            out.append(f"shared input {i}: {s} {U} does not use {e['owner']}'s {e['round']} path ({path}); still on {c.get('taken_round')}")
    if ch: save(R)
    return out


def stale():
    """[{id, symbol, owner, round, taken_round, rows, how, values, requested_for}] for consumers on an older path."""
    R = load(); out = []
    for i, e in R['entries'].items():
        for s, c in (e.get('consumers') or {}).items():
            if not _same(c.get('taken_values'), e.get('values')):
                out.append(dict(id=i, symbol=s, owner=e['owner'], round=e['round'], taken_round=c.get('taken_round'),
                                taken_values=c.get('taken_values'), values=e['values'], rows=c.get('rows'), how=c.get('how'),
                                requested_for=c.get('requested_for')))
    return out


def mark_requested(i, s, rnd):
    R = load(); R['entries'][i]['consumers'][s]['requested_for'] = rnd; save(R)


def brief(s):
    """Markdown section for SYM's update brief ('' when it uses no shared input)."""
    R = load(); ls = []
    for i, e in R['entries'].items():
        c = (e.get('consumers') or {}).get(s)
        if c is None: continue
        path = ', '.join(f"{k} {e['values'][k]:,.2f}" for k in e['labels'])
        st = ('current' if _same(c.get('taken_values'), e['values']) else
              f"OLDER: the base uses {', '.join(f'{k} {v:,.2f}' for k, v in (c.get('taken_values') or {}).items()) or 'an unrecorded path'} "
              f"({c.get('taken_round') or 'round unknown'})")
        ls.append(f"- `{i}` ({e.get('label', i)}), owner {e['owner']} gate-clean round {e['round']}: {path}. Used in this set by "
                  f"{', '.join(c.get('rows') or []) or 'see how'}: {c.get('how') or ''}. Base: {st}.")
    if not ls: return ''
    return ('\n\n## Shared inputs from other names\' models (shared_inputs.py; Kilian 2026-11-23: picked up automatically)\n\n'
            'These paths come from another name\'s gate-clean model. Use the owner\'s current path below wherever this set uses it '
            '(re-derive every figure built on it; cite the owner\'s round as runs/<round>/<owner>/supervisor.json) and write '
            '`reconciled_inputs.shared_inputs.<id> = {"owner": ..., "round": ...}` in the set. Do not re-research the path itself: '
            'a change to it belongs in the owner\'s model.\n\n' + '\n'.join(ls) + '\n')


def add(i, owner, row, labels):
    R = load()
    if i in R['entries']: sys.exit(f'{i}: already registered')
    R['entries'][i] = dict(id=i, owner=owner, row=row, field='years', labels=labels, values=None, round=None, consumers={})
    save(R); print(f'{i}: registered; publish runs at {owner}\'s next finish (or `publish U {owner}` on its last finished round)')


if __name__ == '__main__':
    a = sys.argv[1:]
    if a[:1] == ['show']: print(json.dumps(load(), indent=1))
    elif a[:1] == ['stale']: print(json.dumps(stale(), indent=1))
    elif a[:1] == ['publish'] and len(a) == 3: print(json.dumps(publish(a[1], a[2].upper()), indent=1))
    elif a[:1] == ['take'] and len(a) == 3: print(json.dumps(take(a[1], a[2].upper()), indent=1))
    elif a[:1] == ['brief'] and len(a) == 2: print(brief(a[1].upper()) or '(no shared inputs)')
    elif a[:1] == ['add'] and len(a) == 5: add(a[1], a[2].upper(), a[3], a[4].split(','))
    else: print(__doc__); sys.exit(1)
