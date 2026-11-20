"""Small flagged paths are noted, not carded (RULES.md 26; Kilian 2026-11-18 22:21Z card "Skip under 0.5%, note it", standing
card cmsg_01HdhANNPxz1f5xqDwR22cfL6x7Uwm1s8RxKyE9kDsHU8Z).

What went wrong: the NVDA Edge Q4 FY27 card offered three options that all moved the research target (12m) 0.00%. Kilian:
"why is this a card if nothing moves the target either the model is wrong or it legitimately doesn't matter". Here the model
was wrong: Q4 FY27 edge_rev (and hs_capture) did not feed FY28, whose quarters were set on their own (Kilian 22:17Z: "this
should be informing the path ahead of it").

The rule in code:
1. Source the path first (rule 4). A gate-flagged row (unsupported / trend_only) that stays unsourced is sized with the engine
   as usual (judgment_check.py size).
2. Feeds-forward check, before anything else: an alternative that patches a forecast quarter or year must move a later period
   of the engine output. If its own period moves and nothing after it does, the path does not chain (no_feed_forward); if its
   own period does not move either, the model does not use the input (no_effect). Both are model errors: fix the model (make
   the next period jump off this one), re-size, and only then decide. A path in the last forecast period (last_period) or a
   non-period input (not_period: a multiple, a weight, a scalar) passes.
3. If every listed evidence-based alternative still moves the target by less than SKIP_PCT (0.5%), the row is booked with a
   one-line note in the reply (what was searched, the option range in %) instead of a card, and the gate row closes on the
   standing card (gate_accept.py --msg <standing card>), which accepts only rows a valid note here covers.

Schema (supervisor.json, top level):
  "small_flag_notes": [
    {"item": "edge_rev",                                  # judgment_alternatives item
     "row": "edge_rev Q4 FY2027 = 8,850",                 # the gate row as it reads in citation_audit / gate/must_card.json
     "alternatives": [5, 7],                              # indices into that item's judgment_alternatives[].alternatives
     "searched": "what was searched for a source and found (>= 40 chars)",
     "note": "optional extra words for the reply line",
     # written by this script, never by hand:
     "checked": {"by": "small_flag.py", "set_hash": "...", "range_pct": [-0.1, 0.0], "feeds_forward": [...], "utc": "..."},
     "card_withdrawn": {...}}]                            # the draft card it replaced, moved here by `accept`

    python3 small_flag.py suggest U SYM   flagged cards / must-card rows whose options all move < 0.5%, with the feeds-forward
                                          result and a draft small_flag_notes entry (fix the model first where it fails)
    python3 small_flag.py check U SYM     validate small_flag_notes (exit 1 on problems) and write `checked`
    python3 small_flag.py notes U SYM     the one-line reply notes
    python3 small_flag.py accept U SYM    check, then close each covered gate row on the standing card and withdraw its draft card
"""
import copy, datetime as dt, json, os, re, sys
HERE = os.path.dirname(os.path.abspath(__file__)).replace('/mnt/attach/project-files', '/mnt/project-files')
sys.path.insert(0, HERE)
SKIP_PCT = 0.5
STANDING_CARD = 'cmsg_01HdhANNPxz1f5xqDwR22cfL6x7Uwm1s8RxKyE9kDsHU8Z'
STANDING_QUOTE = 'Skip under 0.5%, note it'
FLAGGED = ('unsupported', 'trend_only')
SEARCH_MIN = 40
TOL = 1e-7          # relative change below this = the period did not move
OUT_KEYS = ('revenue', 'ebit', 'fcff', 'eps', 'nopat')


def _j(p, d=None):
    try: return json.load(open(p))
    except (OSError, ValueError): return d


def _norm(t): return re.sub(r'[^a-z0-9.%$]+', ' ', str(t or '').lower()).strip()


RUNS = os.environ.get('VAL_RUN_ROOT', os.path.join(HERE, 'runs'))


def _rd(U, s): return os.path.join(RUNS, U, s)


# ------------------------------------------------------------------------------------------------------- feeds-forward check
def _periods(RI, patch):
    """('q', last patched quarter index) / ('y', last patched year index) / None for a non-period input."""
    last = None
    for pt in patch or []:
        m = re.match(r'drivers\[id=[^\]]+\]\.(years|quarters)(?:\[(\d*)(:?)(\d*)\])?$', str(pt.get('path', '')))
        if not m: continue
        kind = 'q' if m.group(1) == 'quarters' else 'y'
        if m.group(2) is None:  # whole list: the last element
            n = len((RI.get('quarters') or {}).get('labels') or []) if kind == 'q' else None
            if n is None: continue
            hi = n - 1
        elif m.group(3):
            b = m.group(4)
            if b: hi = int(b) - 1
            else:
                L = (RI.get('quarters') or {}).get('labels') or []
                hi = len(L) - 1 if kind == 'q' else None
                if hi is None: continue
        else: hi = int(m.group(2))
        if last is None or (kind, hi) > last: last = (kind, hi)
    return last


def _series(v):
    """Ordered engine outputs: quarters by label, rows (forecast years, fades left out) by index."""
    q = [(x.get('label'), {k: x.get(k) for k in OUT_KEYS}) for x in v.get('quarters') or []]
    y = [(r.get('label'), {k: r.get(k) for k in OUT_KEYS}) for r in v.get('rows') or [] if not str(r.get('label', '')).startswith('fade')]
    return q, y


PL_KEYS = ('revenue', 'ebit', 'eps')  # the P&L lines; cash-only lines (fcff via working capital) count only for cash-only inputs


def _moved(a, b, keys=OUT_KEYS):
    for k in keys:
        x, z = a.get(k), b.get(k)
        if isinstance(x, (int, float)) and isinstance(z, (int, float)) and abs(x - z) > TOL * max(1.0, abs(x)): return True
    return False


def _engine(mi, as_of, s, spot, beta):
    import model_engine as ME
    m, _ = ME.apply_dcf_policy(copy.deepcopy(mi), s, spot, beta)
    return ME.value(dict(m, _allow_long=True), as_of)


def feeds_forward(RI, patch, ctx, base_v=None):
    """{status, period, next_moved, own_moved, why}. status: ok | last_period | not_period | no_feed_forward | no_effect | error."""
    import judgment_check as JC
    as_of, s, spot, beta = ctx
    P = _periods(RI, patch)
    if P is None: return dict(status='not_period', why='patch is not a forecast quarter or year of a driver')
    t = copy.deepcopy(RI)
    try:
        for pt in patch: JC._apply(t, pt)
        av = _engine(t, as_of, s, spot, beta); bv = base_v or _engine(RI, as_of, s, spot, beta)
    except Exception as e: return dict(status='error', why=str(e)[:200])
    (bq, by), (aq, ay) = _series(bv), _series(av)
    kind, hi = P
    if kind == 'q':
        L = (RI.get('quarters') or {}).get('labels') or []
        if hi >= len(L): return dict(status='error', why=f'quarter index {hi} past the set')
        lab = L[hi]; idx = {x[0]: i for i, x in enumerate(bq)}
        if lab not in idx: return dict(status='not_period', period=lab, why=f'{lab} is not a forecast quarter in the engine output')
        i = idx[lab]; K = PL_KEYS if _moved(bq[i][1], aq[i][1], PL_KEYS) else OUT_KEYS
        own = _moved(bq[i][1], aq[i][1], K); after = list(range(i + 1, len(bq)))
        later = [bq[j][0] for j in after if _moved(bq[j][1], aq[j][1], K)]
        # a later fiscal year counts too (year rows carry what the quarters do not)
        fy = next((x.get('fy') for x in bv.get('quarters') or [] if x.get('label') == lab), None)
        ys = [r[0] for r in by]; yi = ys.index(fy) if fy in ys else None
        if yi is not None: later += [by[j][0] for j in range(yi + 1, len(by)) if _moved(by[j][1], ay[j][1], K)]
        has_after = bool(after) or (yi is not None and yi + 1 < len(by))
    else:
        if hi >= len(by): return dict(status='error', why=f'year index {hi} past the forecast rows')
        lab = by[hi][0]; K = PL_KEYS if _moved(by[hi][1], ay[hi][1], PL_KEYS) else OUT_KEYS; own = _moved(by[hi][1], ay[hi][1], K)
        later = [by[j][0] for j in range(hi + 1, len(by)) if _moved(by[j][1], ay[j][1], K)]; has_after = hi + 1 < len(by)
    if not has_after: return dict(status='last_period', period=lab, own_moved=own, why='last forecast period: nothing after it to feed')
    if later: return dict(status='ok', period=lab, own_moved=own, next_moved=later[:3])
    if own: return dict(status='no_feed_forward', period=lab, own_moved=True,
                        why=f'{lab} moves but no later period does: the path after it does not jump off it (fix the model, rule 26)')
    return dict(status='no_effect', period=lab, own_moved=False,
                why=f'{lab} does not move at all: the model does not use this input (check the model, rule 26)')


FF_OK = ('ok', 'last_period', 'not_period')


# --------------------------------------------------------------------------------------------------------------- the set
def _ctx(U, s):
    import judgment_check as JC, model_engine as ME
    p, d, RI, as_of, spot, beta, h = JC._ctx(U, s)
    return p, d, RI, (as_of, s, spot, beta), h


def flagged_rows(U, s):
    """Gate rows flagged unsupported / trend_only: open or accepted in citation_audit.json, or listed in gate/must_card.json."""
    rd = _rd(U, s); A = _j(os.path.join(rd, 'citation_audit.json'), {}) or {}; out = []
    for k in ('open_failures', 'accepted_carried'):
        out += [dict(f, _where=k) for f in A.get(k) or [] if f.get('result') in FLAGGED]
    for f in (_j(os.path.join(rd, 'gate', 'must_card.json'), {}) or {}).get('items') or []:
        if f.get('result') in FLAGGED: out.append(dict(f, _where='must_card'))
    import glob
    for g in sorted(glob.glob(os.path.join(rd, 'gate', 'merged*.json'))):  # flagged in an earlier round of this folder
        out += [dict(f, _where=os.path.basename(g)) for f in (_j(g, {}) or {}).get('failures') or [] if isinstance(f, dict) and f.get('result') in FLAGGED]
    return out


def _row_match(row, f):
    a, b = _norm(row), _norm(f.get('item'))
    if not a or not b: return False
    v = _norm(f.get('value'))
    return a == b or a in b or b in a or (v and a == _norm(f"{f.get('item')} = {f.get('value')}"))


def row_period(RI, row):
    """('q', index) for a row naming one quarter ("Q4 FY2027"), ('y', index) for one fiscal year when the set is annual; else None."""
    t = str(row or ''); L = (RI.get('quarters') or {}).get('labels') or []
    m = re.search(r'\b(Q[1-4])\s*FY\s?(\d{2,4})\b', t, re.I)
    if m and L:
        y = m.group(2); y = y if len(y) == 4 else '20' + y; lab = f'{m.group(1).upper()} FY{y}'
        return ('q', L.index(lab)) if lab in L else None
    return None


def _fy(t): return t if len(t) == 4 else '20' + t


_PER = re.compile(r'\b(?:(Q[1-4])\s*)?FY\s?(\d{4}|\d{2})\b(?:\s*[-\u2013]\s*(?:(Q[1-4])\s*)?(?:FY\s?)?(\d{4}|\d{2})\b)?', re.I)


def row_periods(RI, row):
    """The flagged period of a gate row as ('q', frozenset of quarter indices) / ('y', frozenset of year indices), or None.
    Reads "Q4 FY2027", "FY29", "FY28-FY29", "Q4 FY26-FY29", "Q4 FY26-Q2 FY27" (first period named in the row). Quarter sets
    use quarters.labels; a set with no quarters uses the years labels (11-19: AVGO "sw_rev FY29", "non_ai_rev FY28-FY29")."""
    m = _PER.search(str(row or ''))
    if not m: return None
    q0, y0, q1, y1 = m.group(1), _fy(m.group(2)), m.group(3), m.group(4) and _fy(m.group(4))
    L = (RI.get('quarters') or {}).get('labels') or []
    if L:
        pos = {l: i for i, l in enumerate(L)}
        lo = pos.get(f'{q0.upper()} FY{y0}' if q0 else f'Q1 FY{y0}')
        if y1: hi = pos.get(f'{q1.upper()} FY{y1}' if q1 else f'Q4 FY{y1}')
        else: hi = lo if q0 else pos.get(f'Q4 FY{y0}')
        return ('q', frozenset(range(lo, hi + 1))) if lo is not None and hi is not None and hi >= lo else None
    if q0 or q1: return None
    Y = [str(r.get('label')) for r in RI.get('years') or []]
    ys = [i for i, l in enumerate(Y) if l == f'FY{y0}' or (y1 and f'FY{y0}' <= l <= f'FY{y1}')]
    return ('y', frozenset(ys)) if ys else None


def L_(RI, rp):
    if not rp: return str(rp)
    k, ix = rp; ix = sorted(ix) if not isinstance(ix, int) else [ix]
    L = ((RI.get('quarters') or {}).get('labels') or []) if k == 'q' else [str(r.get('label')) for r in RI.get('years') or []]
    return L[ix[0]] if len(ix) == 1 else f'{L[ix[0]]} to {L[ix[-1]]}'


def _span(RI, path, kind):
    """(path without its index, lo, hi exclusive) of a quarters / years list patch, else None. Covers drivers[id=x].quarters[a:b]
    and driver_build.quarters.constants.x[a:b] (11-19: AVGO constants), years likewise."""
    m = re.match(r'(.*?)(?:\[(\d*)(:?)(\d*)\])?$', str(path or ''))
    pre = m.group(1); key = 'quarters' if kind == 'q' else 'years'
    if not re.search(rf'(^|\.){key}(\.|\[id=|$)', pre) and not re.search(rf'\.{key}$', pre): return None
    n = len((RI.get('quarters') or {}).get('labels') or []) if kind == 'q' else len(RI.get('years') or [])
    if m.group(2) is None and not m.group(3): return pre, 0, n
    lo = int(m.group(2)) if m.group(2) else 0
    if not m.group(3): return pre, lo, lo + 1
    return pre, lo, int(m.group(4)) if m.group(4) else n


def row_part(RI, patch, rp):
    """How an alternative relates to the flagged period rp = ('q' | 'y', index or set of indices):
    ('own', None): it has an entry inside that period only (an option for it; its later entries are its own chain, so its
                   full sized effect counts);
    ('span', part): it changes the period only inside a longer span; part = the patch cut to the period alone;
    ('other', None): it never touches the period (another period of the driver)."""
    kind, P = rp; P = frozenset([P]) if isinstance(P, int) else frozenset(P); lo_p, hi_p = min(P), max(P) + 1
    part = []; own = False
    for pt in patch or []:
        q = _span(RI, pt.get('path'), kind)
        if not q or q[2] <= lo_p or q[1] >= hi_p: continue
        if lo_p <= q[1] and q[2] <= hi_p: own = True; continue
        a, b = max(q[1], lo_p), min(q[2], hi_p)
        sub = {k: v for k, v in pt.items() if k != 'path'}; sub['path'] = f'{q[0]}[{a}:{b}]'
        if isinstance(pt.get('value'), list): sub['value'] = pt['value'][a - q[1]:b - q[1]]
        elif 'value' in pt: sub['value'] = [pt['value']] * (b - a)
        part.append(sub)
    if own: return 'own', None
    return ('span', part) if part else ('other', None)


def bigger(RI, al, listed, row, ctx, base_v):
    """Sized alternatives of an item for the row's flagged period that move the target >= SKIP_PCT, other than `listed`:
    [(j, effect, 'own' | 'span' | 'last', why)]. None when the row names no period (callers fall back)."""
    rp = row_periods(RI, row)
    if rp is None: return None
    out = []
    for j, a in enumerate(al):
        if j in listed or not isinstance(a.get('fv_effect_pct'), (int, float)): continue
        kind, part = row_part(RI, a.get('patch'), rp); b = (a.get('build') or '')[:60]
        if kind == 'other': continue
        if kind == 'own': e = a['fv_effect_pct']
        else:
            try: e = _effect(RI, part, ctx, base_v)
            except Exception as ex: out.append((j, None, kind, f'{b}: its {L_(RI, rp)} part could not be sized ({str(ex)[:120]})')); continue
        if abs(e) >= SKIP_PCT:
            out.append((j, e, kind, f"{b} {'patches' if kind == 'own' else 'spans'} {L_(RI, rp)} and "
                                    f"{'moves' if kind == 'own' else 'its ' + L_(RI, rp) + ' part alone moves'} {e:+.2f}%: card the row"))
    return out


def _effect(RI, patch, ctx, base_v):
    """Research target (12m) effect in % of a patch, sized the way judgment_check.py size does."""
    import judgment_check as JC
    as_of, s, spot, beta = ctx; t = copy.deepcopy(RI)
    for pt in patch: JC._apply(t, pt)
    base = JC._value(RI, as_of, s, spot, beta); v = JC._value(t, as_of, s, spot, beta)
    return round((v / base - 1) * 100, 2)


def _card_row(c):
    for k, v in c.items():
        if re.fullmatch(r'gate_r\d+_row', str(k)): return v
    return c.get('gate_row')


def validate(U, s, write=False):
    """(problems, notes): every small_flag_notes entry checked; write=True records `checked` in supervisor.json."""
    p, d, RI, ctx, h = _ctx(U, s)
    I = {str(it.get('item')): it for it in d.get('judgment_alternatives') or [] if it.get('item')}
    rows = flagged_rows(U, s); V = []; base_v = _engine(RI, *ctx); now = dt.datetime.now(dt.timezone.utc).strftime('%Y-%m-%dT%H:%MZ')
    for n, x in enumerate(d.get('small_flag_notes') or []):
        w = f"note {n}: {x.get('item')} | {str(x.get('row'))[:80]}"; it = I.get(str(x.get('item')))
        if not it: V.append(dict(rule='no-item', where=w, why='item has no judgment_alternatives entry')); continue
        if not any(_row_match(x.get('row'), f) for f in rows):
            V.append(dict(rule='not-flagged', where=w, why='row is not a gate row flagged unsupported / trend_only (rule 26 covers only those)'))
        if len((x.get('searched') or '').strip()) < SEARCH_MIN:
            V.append(dict(rule='no-search', where=w, why=f'`searched` missing or under {SEARCH_MIN} chars: rule 4, source the path first and say what was searched'))
        al = it.get('alternatives') or []; ix = x.get('alternatives') or []; eff = []; ff = []
        if not ix: V.append(dict(rule='no-alternatives', where=w, why='list the evidence-based alternatives (indices) that were sized'))
        for i in ix:
            if not isinstance(i, int) or not 0 <= i < len(al): V.append(dict(rule='bad-index', where=w, why=f'alternative {i} not in {x.get("item")} (0..{len(al) - 1})')); continue
            a = al[i]; sz = a.get('sized') or {}; b = (a.get('build') or '')[:60]
            if sz.get('by') != 'judgment_check.py' or sz.get('set_hash') != h or not isinstance(a.get('fv_effect_pct'), (int, float)):
                V.append(dict(rule='unsized', where=f'{w} alt {i} {b}', why=f'not engine-sized on this set: python3 {HERE}/judgment_check.py size {U} {s}')); continue
            eff.append(a['fv_effect_pct'])
            if abs(a['fv_effect_pct']) >= SKIP_PCT:
                V.append(dict(rule='over-skip-line', where=f'{w} alt {i} {b}', why=f"{a['fv_effect_pct']:+.2f}% is not under {SKIP_PCT}%: this is a card"))
            r = feeds_forward(RI, a.get('patch'), ctx, base_v); ff.append(dict(r, alternative=i))
            if r['status'] not in FF_OK:
                V.append(dict(rule=f"feeds-forward-{r['status']}", where=f'{w} alt {i} {b}', why=r.get('why', '')))
        # every sized alternative of the item for the flagged period must be listed: no picking only the small ones. Only
        # alternatives that patch the row's own period count (11-19: NVDA Q4 FY27 notes were refused on FY28-29 terms because
        # every patch ran through the last quarter; AVGO "sw_rev FY29", "non_ai_rev FY28-FY29" the same on FY27-29 spans);
        # one that spans the period and others counts by its own measured effect on that period alone (row_part), so a
        # big change hidden in a long span is still caught.
        B = bigger(RI, al, set(ix), x.get('row'), ctx, base_v)
        if B is None:  # no period in the row: the old test, same last patched period
            lp = {_periods(RI, al[i].get('patch')) for i in ix if isinstance(i, int) and 0 <= i < len(al)}
            B = [(j, a['fv_effect_pct'], 'last', f"{(a.get('build') or '')[:60]} patches the same period and moves {a['fv_effect_pct']:+.2f}%: card the row")
                 for j, a in enumerate(al) if j not in ix and isinstance(a.get('fv_effect_pct'), (int, float))
                 and _periods(RI, a.get('patch')) in lp and abs(a['fv_effect_pct']) >= SKIP_PCT]
        for j, e, kind, why in B: V.append(dict(rule='bigger-alternative', where=f'{w} alt {j}', why=why))
        if write and eff:
            x['checked'] = dict(by='small_flag.py', set_hash=h, range_pct=[round(min(eff + [0.0]), 2), round(max(eff + [0.0]), 2)], feeds_forward=ff, utc=now)
    if write: json.dump(d, open(p, 'w'), indent=1)
    return V, d.get('small_flag_notes') or []


def covers(U, s, item_text):
    """True when a valid, current small_flag_notes entry covers this gate row (gate_accept.py asks before using the standing card)."""
    V, N = validate(U, s)
    bad = {v['where'].split(':')[0] for v in V}
    return any(f'note {n}' not in bad and _row_match(x.get('row'), dict(item=item_text)) for n, x in enumerate(N))


def note_line(x):
    c = x.get('checked') or {}; lo, hi = (c.get('range_pct') or [None, None])
    rng = f'{lo:+.2f}% to {hi:+.2f}%' if lo is not None else 'unsized'
    ff = ', '.join(sorted({f['status'] for f in c.get('feeds_forward') or []})) or 'not checked'
    return (f"{x.get('row')}: booked without a card (rule 26). Options move the target {rng}; feeds forward: {ff}. "
            f"Searched: {x.get('searched', '').strip()}" + (f" {x['note'].strip()}" if x.get('note') else ''))


# --------------------------------------------------------------------------------------------------------------- commands
def suggest(U, s):
    p, d, RI, ctx, h = _ctx(U, s); base_v = _engine(RI, *ctx)
    I = {str(it.get('item')): it for it in d.get('judgment_alternatives') or [] if it.get('item')}
    rows = flagged_rows(U, s); out = []
    for ci, c in enumerate(d.get('judgment_calls_for_kilian') or []):
        row = _card_row(c); it = I.get(str(c.get('item')))
        if not row or not it: continue
        fe = [o.get('fv_effect_pct') for o in c.get('options') or [] if isinstance(o, dict)]
        if not fe or not all(isinstance(e, (int, float)) and abs(e) < SKIP_PCT for e in fe): continue
        al = it.get('alternatives') or []; rp = row_period(RI, row)
        if rp is not None:  # the period the card's row is about: every alternative whose patch ends there, small or not
            cand = [j for j, a in enumerate(al) if _periods(RI, a.get('patch')) == rp]
        else:  # no period in the row: the alternatives whose effect matches a non-booked option
            cand = [j for j, a in enumerate(al) if isinstance(a.get('fv_effect_pct'), (int, float))
                    and any(abs(a['fv_effect_pct'] - e) <= 0.005 for o, e in zip(c.get('options') or [], fe) if '(booked)' not in str(o.get('label')))]
        ff = [dict(feeds_forward(RI, al[j].get('patch'), ctx, base_v), alternative=j) for j in cand]
        out.append(dict(card=ci, question=c.get('question'), item=c.get('item'), row=row, flagged=any(_row_match(row, f) for f in rows),
                        option_pct=fe, alternatives=[dict(i=j, fv_effect_pct=al[j].get('fv_effect_pct'), build=(al[j].get('build') or '')[:90]) for j in cand],
                        feeds_forward=ff, verdict=('FIX THE MODEL FIRST: ' + '; '.join(f"alt {f['alternative']}: {f.get('why', '')}" for f in ff if f['status'] not in FF_OK))
                        if any(f['status'] not in FF_OK for f in ff) else
                        ('CARD: an alternative on this period moves >= 0.5%' if any(abs(al[j].get('fv_effect_pct') or 0) >= SKIP_PCT for j in cand) else
                         'NOTE IT: add a small_flag_notes entry (item, row, alternatives, searched), then small_flag.py accept'),
                        draft=dict(item=c.get('item'), row=row, alternatives=cand, searched='<what was searched for a source and found>')))
    print(json.dumps(out, indent=1)); return out


def accept(U, s):
    V, N = validate(U, s, write=True)
    if V:
        for v in V: print(f"[{v['rule']}] {v['where']}: {v['why']}")
        sys.exit(f'REFUSED: {len(V)} problem(s) in small_flag_notes; nothing accepted')
    if not N: print('no small_flag_notes'); return
    p = os.path.join(_rd(U, s), 'supervisor.json'); d = _j(p); now = dt.datetime.now(dt.timezone.utc).strftime('%Y-%m-%dT%H:%MZ')
    ca = os.path.join(_rd(U, s), 'citation_audit.json'); A = _j(ca, {}) or {}; op = A.get('open_failures') or []; keep, moved = [], []
    for f in op:
        x = next((x for x in d.get('small_flag_notes') or [] if f.get('result') in FLAGGED and _row_match(x.get('row'), f)), None)
        if x is None: keep.append(f); continue
        lo, hi = x['checked']['range_pct']; q = f'{STANDING_QUOTE} (options {lo:+.2f}% to {hi:+.2f}%, rule 26)'
        moved.append(dict(f, status='accepted_by_kilian', fix=(f.get('fix') or '') + f' | rule 26 small flagged path, standing card {STANDING_CARD}: "{q}"',
                          owner_override=dict(msg=STANDING_CARD, card=STANDING_CARD, quote=q, utc=now, rule='RULES.md 26')))
    if A:
        A.setdefault('accepted_carried', []).extend(moved); A['open_failures'] = keep
        if not keep: A['clean'] = True
        A['note'] = (A.get('note') or '') + f' | {now}: {len(moved)} small flagged row(s) closed on standing card {STANDING_CARD} (RULES.md 26).'
        json.dump(A, open(ca, 'w'), indent=1)
    # withdraw the draft cards the notes replace
    C = d.get('judgment_calls_for_kilian') or []; left = []; wd = 0
    for c in C:
        x = next((x for x in d.get('small_flag_notes') or [] if str(x.get('item')) == str(c.get('item')) and _card_row(c) and _norm(_card_row(c)) == _norm(x.get('row'))), None)
        if x is not None and not (c.get('status') or '').lower().startswith('decided'): x['card_withdrawn'] = c; wd += 1
        else: left.append(c)
    d['judgment_calls_for_kilian'] = left; json.dump(d, open(p, 'w'), indent=1)
    with open(os.environ.get('VAL_OVERRIDES_LOG', os.path.join(HERE, 'data', 'rule_overrides.jsonl')), 'a') as fh:
        fh.write(json.dumps(dict(rule='small-flagged-path-noted', round=U, symbol=s, items=[m.get('item') for m in moved], msg=STANDING_CARD,
                                 quote=STANDING_QUOTE, utc=now)) + '\n')
    print(json.dumps(dict(symbol=s, round=U, gate_rows_closed=len(moved), cards_withdrawn=wd, open=len(keep), clean=A.get('clean'))))
    for x in d.get('small_flag_notes') or []: print('NOTE:', note_line(x))


def main(a):
    if len(a) < 3 or a[0] not in ('suggest', 'check', 'notes', 'accept'): sys.exit(__doc__)
    cmd, U, s = a[0], a[1], a[2].upper()
    if cmd == 'suggest': suggest(U, s)
    elif cmd == 'accept': accept(U, s)
    else:
        V, N = validate(U, s, write=cmd == 'check')
        if cmd == 'check':
            for v in V: print(f"[{v['rule']}] {v['where']}: {v['why']}")
            print(f'{len(N)} note(s), {len(V)} problem(s)'); sys.exit(1 if V else 0)
        for x in N: print('-', note_line(x))


if __name__ == '__main__': main(sys.argv[1:])
