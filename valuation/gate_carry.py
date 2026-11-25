"""Re-gate only figures that moved (Kilian 2026-11-23, after the AVGO TPU update's round 1 opened 130 figures because editing
one sentence of a row re-opened every old quote in it: "do you need to check all 130 then" / "if they are relatively current"
/ "yes"; RULES.md 24).

run_digest.py gate-update calls carry() after gate_rounds.plan(): every cited figure still headed for a checker is compared,
figure by figure, with the results that already passed ('ok') in
  - the base round's last gate (runs/<base>/SYM/gate/merged.json; it holds that round's own carried results too, so the
    chain of passed figures runs back through earlier updates), and
  - this round folder's earlier merged rounds (gate/merged_rN.json).
A figure carries (its earlier 'ok' result is kept, tagged carried_from_round) when the same item, the same value and the same
source URL passed before. Arithmetic / label items carry only when their derivation is also unchanged. Anything whose
value, claim text, source or derivation moved is opened as before. Failed, mislabelled or accepted results never carry.

Currency is not skipped: carried rows keep the checker's `published` / `source_kind` record, and timeliness.gate_check
(RULES.md 23) re-screens them on today's date at gate-merge, so a stale forecast still fails without its page being
re-opened. A forecast URL with no recorded publication date is still opened for its date.
"""
import glob, json, os, re

HERE = os.path.dirname(os.path.abspath(__file__)); RUNS = os.path.join(HERE, 'runs')


def J(p): return json.load(open(p))


def _n(s): return ' '.join(str(s or '').lower().split())


def _fp_arith(it): return (_n(it.get('item')), _n(it.get('value')), _n(it.get('derivation')))


def _pool_files(rd, rn):
    """(label, merged results file) for the base round's last gate and this folder's earlier merged rounds."""
    out = []
    try: U = J(os.path.join(rd, 'update.json'))
    except (OSError, ValueError): U = {}
    sym = os.path.basename(rd.rstrip('/'))
    if U.get('base'):
        p = os.path.join(RUNS, U['base'], sym, 'gate', 'merged.json')
        if os.path.exists(p): out.append((f"base {U['base']}", p))
    for r in range(1, rn):
        p = os.path.join(rd, 'gate', f'merged_r{r}.json')
        if os.path.exists(p): out.append((f'round {r}', p))
    return U, out


def _arith_seen(rd, U, rn):
    """Arithmetic items (item, value, derivation) sent to the text agent before, in the base round or this folder."""
    seen = set(); sym = os.path.basename(rd.rstrip('/'))
    dirs = ([os.path.join(RUNS, U['base'], sym, 'gate')] if U.get('base') else []) + ([os.path.join(rd, 'gate')] if rn >= 2 else [])
    for d in dirs:
        for p in [os.path.join(d, 'checklist.json')] + glob.glob(os.path.join(d, 'checklist_r*.json')):
            try: seen |= {_fp_arith(it) for it in J(p).get('arith') or []}
            except (OSError, ValueError): pass
    return seen


def carry(rd, rn, groups, arith):
    """-> (groups, arith, carried rows, basis text). groups: {url: [items]} still to open; arith: text-agent items."""
    U, files = _pool_files(rd, rn)
    ok_url, ok_text = {}, {}
    for lab, p in files:
        try: res = J(p).get('results') or []
        except (OSError, ValueError): continue
        for x in res:
            if x.get('result') != 'ok' or str(x.get('origin', '')).startswith(('timeliness.py', 'speaker.py', 'xbrl_actuals')): continue
            row = dict(x, carried_from_round=x.get('carried_from_round') or lab)
            k = (_n(x.get('item')), _n(x.get('value')))
            if x.get('source_url'): ok_url[k + (x['source_url'],)] = row
            if x.get('_src') == 'text': ok_text[k] = row
    carried, g2, n_fig = [], {}, 0
    for u, its in groups.items():
        left = []
        for it in its:
            if it.get('basis') == 'timeliness': left.append(it); continue  # a date-only check is decided below
            n_fig += 1
            k = (_n(it.get('item')), _n(it.get('value')))
            hit = next((ok_url[k + (v,)] for v in it.get('urls') or [] if k + (v,) in ok_url), None)
            # a checker's newer_found is a finding against the source, not a pass: it never carries. The page is re-opened, so
            # the fix's answer (timeliness.none_newer / newer_forecast) is re-checked independently (Kilian 2026-11-24, COHR
            # src 51: round 1's newer_found was carried into rounds 2 and 3 and overrode the fix's complete none_newer search)
            if hit and not hit.get('newer_found'): carried.append(hit)
            else: left.append(it)
        if left: g2[u] = left
    # a forecast URL whose figures all carried: its date comes from the carried record; no page opened for the date alone
    dated = {x.get('source_url') for x in carried if x.get('published')}
    for u in list(g2):
        if u in dated and all(it.get('basis') == 'timeliness' for it in g2[u]): del g2[u]
    seen = _arith_seen(rd, U, rn)
    a2 = []
    for it in arith:
        k = (_n(it.get('item')), _n(it.get('value')))
        if _fp_arith(it) in seen and k in ok_text: carried.append(ok_text[k])
        else: a2.append(it)
    # one result per (item, value, url): an earlier round may hold the same figure twice
    uniq, out = set(), []
    for x in carried:
        key = (_n(x.get('item')), _n(x.get('value')), x.get('source_url'))
        if key not in uniq: uniq.add(key); out.append(x)
    n_open = sum(len(v) for v in g2.values())
    basis = (f"figure-level carry (gate_carry.py, Kilian 2026-11-23): {len(out)} unchanged figure(s) carried from "
             f"{', '.join(l for l, _ in files) or 'no earlier gate'}; {n_open} to open, {len(a2)} arithmetic to the text agent")
    return g2, a2, out, basis


def save_checklist_copy(gd, rn):
    """Keep each round's checklist (gate-update overwrites checklist.json), so later rounds can match arithmetic items."""
    p = os.path.join(gd, 'checklist.json')
    if rn and os.path.exists(p):
        with open(p) as f: t = f.read()
        open(os.path.join(gd, f'checklist_r{rn}.json'), 'w').write(t)


def carry_claims(rd, claims, judgment_claims):
    """Uncited claim sentences for the text agent -> (still to check, carried rows). A sentence carries when the base round's
    set holds the same sentence and the base gate left no failure on it (the base audit closed clean or by Kilian's acceptance)."""
    try: U = J(os.path.join(rd, 'update.json'))
    except (OSError, ValueError): return claims, []
    sym = os.path.basename(rd.rstrip('/')); bd = os.path.join(RUNS, str(U.get('base')), sym)
    try: B = J(os.path.join(bd, 'supervisor.json'))
    except (OSError, ValueError): return claims, []
    RI = (B.get('model') or {}).get('reconciled_inputs') or {}
    base = {_n(sen) for _, sen, _ in judgment_claims(RI, B.get('judgment_calls_for_kilian'), None)}
    bad = set()
    for p in [os.path.join(bd, 'gate', 'merged.json')] + glob.glob(os.path.join(bd, 'gate', 'merged_r*.json')):
        try: bad |= {_n(x.get('value')) for x in J(p).get('results') or [] if x.get('result') != 'ok'}
        except (OSError, ValueError): pass
    left, out = [], []
    for c in claims:
        t = _n(c.get('text'))
        if t in base and t not in bad:
            out.append(dict(origin=c.get('origin'), item='uncited claim, unchanged since the base gate', value=c.get('text'), basis='claim',
                            source_url='', result='ok', checked_by='gate_carry.py (same sentence passed the base round gate)',
                            carried_from_round=f"base {U.get('base')}", _src='carry'))
        else: left.append(c)
    return left, out
