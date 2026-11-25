"""Pre-gate diff (Kilian 2026-11-20, after the VST non-op update spent three gate rounds on old figures nobody carried:
"implement all three for all work going forward and make sure no future claudes can bypass this without telling me what
they are doing").

Before every citation-gate round, `name_update.py gate` runs this check on runs/U/SYM/supervisor.json:
  moved   every number in supervisor.json that differs from the update's base round (update.json `base`) or from the
          set as an earlier gate round of this update saw it (gate/supervisor_rN.json, saved by `gate`): path, old, new.
          Numeric strings count (inputs_detail "value": "17,586").
  stale   every row and prose line that still shows an OLD value of a moved number (as written, x100 as a percent, or
          x1000 / /1000 for M vs B), where the same row does not also hold a current number that displays the same way.
Each stale hit must be fixed (edit the row / prose) or explained:
  python3 pregate_diff.py explain U SYM ID[,ID...] --why "<why this figure is right as written>"
`gate` refuses while a stale hit is neither fixed nor explained. There is no bypass: --why-pregate was removed
(Kilian 2026-11-25: "there is absolutely no bypass permitted"); each hit is fixed or explained by ID. Builds on gate_rounds.py (sweep: corrections' `from` figures) and carry_trace.py (unchanged values, changed text).

  python3 pregate_diff.py U SYM [--moved] [--no-drop]   stale hits (--moved: the moved list); drops history clauses first
                                            unless --no-drop; exit 1 on an unexplained hit
  python3 pregate_diff.py explain U SYM IDS --why TEXT
Writes runs/U/SYM/gate/pregate_diff.json (moved + stale + explained + dropped) on every run.

Only figures Kilian sees (Kilian 2026-11-23, COHR: "actuals and project figures should not be flagged and you can just drop
older ones to show what changed, figures that i don't ultimately see don't matter (put this in code)"):
  seen     prose is checked only where it reaches him: the Sheet (inputs_detail basis/derivation, horizon_multiples peer_basis,
           segments/adjustments/sotp notes, custom explanations, scenario stories, segment driver notes, discount-rate basis),
           the page (summary, key_drivers, fair_value.reason) and the cards (judgment_calls_for_kilian question, context,
           why, recommendation, option labels and reasoning). Internal text (drivers[].derivation, long_dated_review,
           constants_note, guidance_check, disagreements, feedback_for_agents, judgment_alternatives builds) is not checked.
           Old values come only from numbers he sees too: moved judgment_alternatives sizes and other internal numbers
           are not "old figures".
  actual   a figure next to a reported period (Q1 FY27, FY25, bare Q1 of the current year, up to the last reported quarter)
           or a filing word (reported, filed, actual, 10-Q, 10-K, 8-K, Ex.99), or equal to a reported quarter value, or a sourced
           outside figure (src N, a URL, secfacts, Zacks, consensus, the call / transcript within 40 characters): not flagged.
  project  project-rule figures (cost of equity, T-bill, the 7.69% premium and its 0.768 / 0.232 beta line): not flagged.
  current  a figure a current number in the same series still shows (a capture path whose values shifted quarter): not flagged.
  history  an old figure kept to show what changed ("base round ... value 309.64", "(was ...)", "older ... view", BEFORE
           sections, the old side of "280.86 -> 277.01" with its arrow) is DROPPED
           from the text by `gate` (the clause or parenthetical is removed; the original text is kept in pregate_diff.json
           `dropped`), not flagged.
"""
import datetime as dt, hashlib, json, os, re, sys

HERE = os.path.dirname(os.path.abspath(__file__)).replace('/mnt/attach/project-files', '/mnt/project-files')
RUNS = os.environ.get('VAL_RUN_ROOT') or os.path.join(HERE, 'runs')
SKIP_TOP = {'citation_audit', 'judgment_calls_history', 'key_drivers_text_before', 'changes', 'sources', 'judgment_alternatives_sized'}
LOGS = {'set_review', 'gate', 'changes', 'corrections'}  # logs of past states, anywhere in the file
ROW_KEYS = ('id', 'input', 'item', 'name', 'key')
SCALES = (1.0, 100.0, 1000.0, 0.001)
NUM_STR = re.compile(r'^\s*\(?-?\$?\d[\d,]*(?:\.\d+)?%?\)?\s*$')
TOK = re.compile(r'(?<![\w.])(-?\$?)(\d{1,3}(?:,\d{3})+|\d+)(\.\d+)?(%|\s?(?:bn|B|M|K|x)\b)?')
SEEN = [re.compile(x) for x in (  # text Kilian sees (Kilian 2026-11-23); everything else is internal
    r'^summary$', r'^key_drivers\[[^\]]*\]$', r'^fair_value\.reason$',
    r'^model\.reconciled_inputs\.inputs_detail\[[^\]]*\]\.(derivation|basis)$',
    r'^model\.reconciled_inputs\.horizon_multiples?(\[[^\]]*\])?\.peer_basis$',
    r'^model\.reconciled_inputs\.(segments|adjustments)\[[^\]]*\]\.note$',
    r'^model\.reconciled_inputs\.sotp\.parts\[[^\]]*\]\.note$',
    r'^model\.reconciled_inputs\.custom\[[^\]]*\]\.explanation$',
    r'^model\.reconciled_inputs\.scenarios\.[^.]+\.story$',
    r'^model\.reconciled_inputs\.segment_build(\.segments)?\[[^\]]*\]\.driver_note$',
    r'^model\.reconciled_inputs\.discount_rate\.basis$',
    r'^judgment_calls_for_kilian\[[^\]]*\]\.(question|context|why|recommendation)$',
    r'^judgment_calls_for_kilian\[[^\]]*\]\.options\[[^\]]*\]\.(label|reasoning)$')]
UNSEEN_NUM = ('judgment_alternatives', 'disagreements', 'feedback_for_agents', 'long_dated_review', 'source_review', 'model.set_review',
              'dcf_check', 'comps_review')  # numbers Kilian never sees: their old values are not old figures
PERIOD = re.compile(r"\b(?:Q([1-4])\s?(?:FY\s?'?(\d{2,4}))?|FY\s?'?(\d{2,4}))\b")
ACTUAL_W = re.compile(r'\b(?:reported|filed|actuals?|restated|10-Q|10-K|8-K|Ex\.\s?99(?:\.\d)?|press release)\b', re.I)
SOURCED_W = re.compile(r'\bsrc\s?\d|\bsources?\s\d|https?://|\bsecfacts\b|\bZacks\b|\bconsensus\b|\btranscript\b|\bon the call\b|\bfrom the call\b', re.I)
ARROW = re.compile(r'\s*(?:->|→)\s*')
PROJECT_W = re.compile(r'project(?:\'s)? (?:rule|cost of equity)|cost of equity|T-bill|\bERP\b', re.I)
PROJECT_FIG = {'7.69%', '0.768', '0.232'}
HISTORY_W = re.compile(r'(?-i:\bBEFORE\b)|\bbase round\b|\bwas\b|\bpreviously\b|\bprevious\b|\bprior round\b|\bearlier round\b|\bolder\b|\bsuperseded\b|\bbefore this update\b', re.I)
HISTORY_WHY = re.compile(r'\bhistor(y|ic|ical)\b|\bdated (comparison|history)\b|\bkept to show\b|\bto show what changed\b|\bwhat (it|the \w+) (replaces|changed)\b'
                         r'|\bthe figure it replaces\b|\bold (value|figure)s? (kept|shown|retained)\b|\bbefore (kilian|the pick|this update)\b', re.I)
DATE = re.compile(r'\b\d{4}-\d{2}-\d{2}\b|\b(?:FY|Q[1-4]\s?|CY|H[12]\s?)\'?\d{2,4}\b|\b(?:19|20)\d{2}\b')


def J(p, d=None):
    try: return json.load(open(p))
    except (OSError, ValueError): return d


def _now(): return dt.datetime.now(dt.timezone.utc).strftime('%Y-%m-%dT%H:%MZ')


def _skip(p, k):
    return (p == '' and k in SKIP_TOP) or k in LOGS or 'history' in str(k) or str(k).endswith('_before')


def _num_str(s):
    if not isinstance(s, str) or not NUM_STR.match(s): return None
    neg = s.strip().startswith('(') or '-' in s
    try: v = float(re.sub(r'[^\d.]', '', s))
    except ValueError: return None
    return (-v if neg else v) * (0.01 if '%' in s else 1)


def _idlike(p):
    k = re.sub(r'\[[^\]]*\]$', '', p).split('.')[-1].lower()
    if k == 'fv_effect_pct': return True  # card sizes move on every re-size; x100 coincidences with prose percents
    return k in ('id', 'src', 'source', 'sources', 'page', 'pages', 'slide') or k.endswith('_ids') or k.endswith('_id') or k in ('source_ids', 'fact_ids')


def leaves(S, loc=None):
    """{path: number} and [(path, text, row_path)] for supervisor.json; list items keyed by their id/input/item when they have one."""
    nums, texts = {}, []
    def key_of(x, i):
        if isinstance(x, dict):
            for k in ROW_KEYS:
                if isinstance(x.get(k), (str, int)) and str(x.get(k)).strip(): return f'[{k}={x[k]}]'
            if x.get('metric') is not None: return f"[metric={x.get('metric')} {x.get('fy', '')}]"
        return f'[{i}]'
    def walk(x, p, row):
        if isinstance(x, dict):
            for k, v in x.items():
                if not _skip(p, k): walk(v, f'{p}.{k}' if p else k, row)
        elif isinstance(x, list):
            seen = set()
            for i, v in enumerate(x):
                k = key_of(v, i)
                if k in seen: k = f'[{i}]'
                seen.add(k); q = f'{p}{k}'
                walk(v, q, q if isinstance(v, dict) else row)
        elif isinstance(x, bool) or x is None: return
        elif _idlike(p): return
        elif isinstance(x, (int, float)): nums[p] = float(x)
        elif isinstance(x, str):
            v = _num_str(x)
            if v is not None: nums[p] = v
            elif len(x) > 3: texts.append((p, x, row or p.split('.')[0]))
    def walk2(x, p, row):  # records (container, key) per path so `gate` can drop history clauses in place
        if isinstance(x, dict):
            for k, v in list(x.items()):
                if _skip(p, k): continue
                q = f'{p}.{k}' if p else k
                if isinstance(v, str) and loc is not None: loc[q] = (x, k)
                walk2(v, q, row)
        elif isinstance(x, list):
            seen_ = set()
            for i, v in enumerate(x):
                k = key_of(v, i)
                if k in seen_: k = f'[{i}]'
                seen_.add(k); q = f'{p}{k}'
                if isinstance(v, str) and loc is not None: loc[q] = (x, i)
                walk2(v, q, q)
    walk(S, '', '')
    if loc is not None: walk2(S, '', '')
    return nums, texts


def tokens(t):
    """Figures in prose distinctive enough to trace: with %, $, a B/M unit or a decimal point, or at least 3 digits."""
    t = DATE.sub(lambda m: ' ' * len(m.group(0)), t)
    out = []
    for m in TOK.finditer(t):
        sign, ip, fp, suf = m.group(1), m.group(2), m.group(3) or '', (m.group(4) or '').strip()
        digits = sum(c.isdigit() for c in ip + fp)
        if not ('$' in sign or suf or fp or digits >= 3): continue
        if suf == 'x' and not fp: continue
        v = float(ip.replace(',', '') + fp) * (-1 if '-' in sign else 1)
        out.append(dict(text=m.group(0).strip(), v=v, d=len(fp) - 1 if fp else 0, pct=suf == '%', unit=suf, start=m.start()))
    return out


def _scales(tok):
    if tok['pct']: return (100.0, 1.0)
    if tok['unit'] in ('B', 'bn'): return (0.001, 1.0)
    if tok['unit'] == 'K': return (1000.0, 1.0)
    return (1.0,)


def _shows(x, tok):
    """True when number x, written at the token's precision and in the token's unit (% = x100, B = M/1000), is the token."""
    for s in _scales(tok):
        if abs(round(x * s, tok['d']) - tok['v']) < 10 ** -(tok['d'] + 3) and (x != 0 or tok['v'] == 0): return True
    return False


def _sig(tok): return len(re.sub(r'\D', '', tok['text']).lstrip('0'))


def _words(path):
    """Words naming a number: its key and the row key it sits under (tax_rate -> tax, rate; [input=Net debt ...] -> net, debt)."""
    w = set()
    for part in re.findall(r'\[[^=\]]*=([^\]]*)\]|([A-Za-z][A-Za-z_]*)', path.split('.')[-1] + ' ' + (re.findall(r'\[[^\]]*\]', path) or [''])[-1]):
        for x in part:
            w |= {y.lower() for y in re.split(r'[^A-Za-z]+', x) if len(y) >= 3}
    return w - {'value', 'values', 'years', 'base', 'input', 'item', 'name', 'the', 'and', 'quarters'}


def seen(p): return any(r.match(p) for r in SEEN)


def _series(p):
    """The series a number belongs to: its path without the last list index (drivers[id=x].quarters[13] -> drivers[id=x].quarters)."""
    return re.sub(r'\[\d+\]$', '', p)


def _fy(x): x = int(x); return x + 2000 if x < 100 else x


def last_reported(S, rd):
    """(fiscal year, quarter) of the last reported period: driver_build.quarters.reported labels, else facts.json periods."""
    best = None
    rep = (((((S.get('model') or {}).get('reconciled_inputs') or {}).get('driver_build') or {}).get('quarters') or {}).get('reported') or {})
    for lab in rep if isinstance(rep, dict) else []:
        m = re.search(r'Q([1-4])\s?FY\s?(\d{2,4})', lab)
        if m: best = max(best or (0, 0), (_fy(m.group(2)), int(m.group(1))))
    for f in (J(os.path.join(rd, 'facts.json'), {}) or {}).get('facts') or []:
        m = re.search(r'_(?:q([1-4])_?)?fy(\d{4})$', str(f.get('key', '')).lower())
        if m and f.get('basis', 'reported') == 'reported': best = max(best or (0, 0), (int(m.group(2)), int(m.group(1) or 4)))
    return best


def reported_values(S):
    """Numbers in the reported quarters (driver_build.quarters.reported): a prose figure equal to one is an actual."""
    rep = (((((S.get('model') or {}).get('reconciled_inputs') or {}).get('driver_build') or {}).get('quarters') or {}).get('reported') or {})
    out = []
    def walk(x):
        if isinstance(x, dict): [walk(v) for v in x.values()]
        elif isinstance(x, list): [walk(v) for v in x]
        elif isinstance(x, (int, float)) and not isinstance(x, bool): out.append(float(x))
    walk(rep)
    return out


def _clause(t, i):
    a, b = _sentence(t, i)
    return a, b


def is_actual(t, tok, last, repvals):
    """A reported figure: next to a reported period label, a filing word, or equal to a reported quarter value."""
    if any(_shows(v, tok) for v in repvals): return True
    a, b = _clause(t, tok['start']); e = tok['start'] + len(tok['text'])
    if ACTUAL_W.search(t[max(a, tok['start'] - 40):min(b, e + 40)]): return True
    if SOURCED_W.search(t[max(a, tok['start'] - 40):min(b, e + 40)]): return True  # Kilian 2026-11-23 "yes": sourced outside figures count as actuals
    if not last: return False
    sa = max(t.rfind('. ', 0, tok['start']), t.rfind('\n', 0, tok['start'])) + 1  # labels may sit a ';' clause back ("(FY26) ...; other income 9.0 / 12.7")
    before = list(PERIOD.finditer(t, max(sa, tok['start'] - 120), tok['start']))
    after = [m for m in PERIOD.finditer(t, e, min(b, e + 20))]
    m = before[-1] if before else (after[0] if after else None)
    if not m: return False
    if m.group(1): per = (_fy(m.group(2)) if m.group(2) else last[0], int(m.group(1)))
    else: per = (_fy(m.group(3)), 4)
    return per <= last


def is_project(t, tok):
    if tok['text'].lstrip('$') in PROJECT_FIG: return True
    a, b = _clause(t, tok['start'])
    return bool(PROJECT_W.search(t[a:b]))


def history_span(t, tok):
    """(start, end) of the clause or parenthetical that keeps an old figure to show what changed, or None."""
    i = tok['start']; e = i + len(tok['text'])
    m = ARROW.match(t, e)
    if m and m.end() < len(t):  # "280.86 -> 277.01": the old side and its arrow go (Kilian 2026-11-23 "yes")
        nx = next((x for x in tokens(t[m.end():m.end() + 40]) if x['start'] == 0), None)
        if nx and ('$' in nx['text']) == ('$' in tok['text']) and nx['unit'] == tok['unit'] and tok['v'] and 0.5 <= nx['v'] / tok['v'] <= 2:
            return i, m.end()  # same kind of figure on both sides: a change, not "k 1.2773 -> $274.09" (a step to a result)
    o = t.rfind('(', 0, i); c = t.find(')', i)
    if o >= 0 and c >= 0 and ')' not in t[o:i] and HISTORY_W.search(t[o:c]):
        return (o - 1 if o > 0 and t[o - 1] == ' ' else o), c + 1
    a, b = _sentence(t, i)
    if not HISTORY_W.search(t[max(a, i - 60):i]): return None  # the label sits just before the old figure; a far-off "was" in a long clause is not enough
    if a == 0 and b >= len(t): return None  # the whole text: leave it to the writer
    return a, min(b + 1, len(t))


def refs(U, s):
    """[(label, supervisor dict)] to diff against: the base round, then each earlier gate round's saved set."""
    rd = os.path.join(RUNS, U, s); u = J(os.path.join(rd, 'update.json'), {}) or {}
    out = []
    b = u.get('base')
    if b:
        S0 = J(os.path.join(RUNS, b, s, 'supervisor.json'))
        if S0: out.append((f'base {b}', S0))
    gd = os.path.join(rd, 'gate')
    if os.path.isdir(gd):
        for f in sorted(os.listdir(gd), key=lambda x: int(re.sub(r'\D', '', x) or 0)):
            m = re.fullmatch(r'supervisor_r(\d+)\.json', f)
            if m:
                S0 = J(os.path.join(gd, f))
                if S0: out.append((f'gate round {m.group(1)}', S0))
    return out


def hid(path, fig): return hashlib.sha1(f'{path}|{fig}'.encode()).hexdigest()[:8]


def diff(U, s, S=None):
    rd = os.path.join(RUNS, U, s); S = S if S is not None else J(os.path.join(rd, 'supervisor.json'))
    if S is None: raise SystemExit(f'{s}: no {rd}/supervisor.json')
    cur, texts = leaves(S)
    last, repvals = last_reported(S, rd), reported_values(S)
    moved = {}
    for lab, S0 in refs(U, s):
        old, _ = leaves(S0)
        for p, o in old.items():
            if p.startswith(UNSEEN_NUM): continue  # a number Kilian never sees has no old figure he could see
            n = cur.get(p)
            if n is None or abs(n - o) <= 1e-9 * max(abs(n), abs(o), 1): continue
            moved.setdefault(p, dict(path=p, new=n, old=[]))
            if o not in [x['value'] for x in moved[p]['old']]: moved[p]['old'].append(dict(value=o, ref=lab))
    # current numbers by row (a figure that a current number in the same row also shows is not stale)
    by_row = {}
    by_ser = {}
    for p, v in cur.items(): by_row.setdefault(_row_of(p), []).append(v); by_ser.setdefault(_series(p), []).append(v)
    olds = [(o['value'], m) for m in moved.values() for o in m['old']]
    ex = (J(os.path.join(rd, 'gate', 'pregate_diff.json'), {}) or {}).get('explained') or {}
    stale = []
    for p, t, row in texts:
        if not seen(p): continue  # Kilian 2026-11-23: figures he does not see do not matter
        rowvals = by_row.get(_row_of(p), [])
        for tok in tokens(t):
            hits = [m for o, m in olds if _shows(o, tok) and not _shows(m['new'], tok)]
            if not hits: continue
            if any(_shows(v, tok) for v in rowvals): continue  # a current number in the same row shows this figure
            if is_actual(t, tok, last, repvals) or is_project(t, tok): continue  # Kilian 2026-11-23: actuals, project figures
            hits = [m for m in hits if not any(_shows(v, tok) for v in by_ser.get(_series(m['path']), []))]  # still in its series
            if not hits: continue
            a, b = _sentence(t, tok['start'])
            sent, ctx = t[a:b], t[max(0, tok['start'] - 60):tok['start'] + len(tok['text']) + 60].lower()
            keep = []
            for m in hits:
                if any(_shows(m['new'], x) for x in tokens(sent)): continue  # before -> after in one sentence ("1,488 -> 488", "was ...")
                if _sig(tok) < 3 and not any(w in ctx for w in _words(m['path'])): continue  # a short figure needs its subject nearby
                keep.append(m)
            if not keep: continue
            i = hid(p, tok['text'])
            if any(h['id'] == i for h in stale): continue
            snip = t[max(0, tok['start'] - 70):tok['start'] + len(tok['text']) + 70].replace('\n', ' ')
            hs = history_span(t, tok)
            stale.append(dict(id=i, path=p, figure=tok['text'], snippet=snip, kind='history' if hs else 'stale',
                              drop=t[hs[0]:hs[1]] if hs else None,
                              old_of=[dict(path=m['path'], old=[o['value'] for o in m['old']], new=m['new']) for m in keep[:4]],
                              explained=ex.get(i)))
    prev = J(os.path.join(rd, 'gate', 'pregate_diff.json'), {}) or {}
    out = dict(symbol=s, update=U, utc=_now(), refs=[l for l, _ in refs(U, s)], moved=list(moved.values()), stale=stale, explained=ex,
               dropped=prev.get('dropped') or [])
    os.makedirs(os.path.join(rd, 'gate'), exist_ok=True)
    json.dump(out, open(os.path.join(rd, 'gate', 'pregate_diff.json'), 'w'), indent=1)
    return out


def _sentence(t, i):
    """Bounds of the sentence (or ';' clause) holding position i."""
    a = max(t.rfind('. ', 0, i), t.rfind('; ', 0, i), t.rfind('\n', 0, i)); a = 0 if a < 0 else a + 1
    bs = [x for x in (t.find('. ', i), t.find('; ', i), t.find('\n', i)) if x >= 0]
    return a, (min(bs) if bs else len(t))


def _row_of(p):
    """The row a path belongs to: up to its last keyed or indexed list item, else its top-level key."""
    m = list(re.finditer(r'\[[^\]]*\]', p))
    return p[:m[-1].end()] if m else p.split('.')[0]


def drop_history(U, s):
    """Kilian 2026-11-23: old figures kept to show what changed are dropped from the text, not flagged. Removes each history
    hit's clause or parenthetical from supervisor.json; the original text is kept in pregate_diff.json `dropped`."""
    rd = os.path.join(RUNS, U, s); f = os.path.join(rd, 'supervisor.json')
    S = J(f); d = diff(U, s, S)
    hist = [h for h in d['stale'] if h['kind'] == 'history' and not h['explained']]
    if not hist: return []
    loc = {}; leaves(S, loc); done = []
    for h in hist:
        c, k = loc.get(h['path'], (None, None))
        if c is None or not h['drop'] or h['drop'] not in c[k]: continue
        before = c[k]; after = re.sub(r'\s{2,}', ' ', before.replace(h['drop'], '', 1)).strip()
        after = re.sub(r'\s+([.;,)])', r'\1', after)
        c[k] = after; done.append(dict(id=h['id'], path=h['path'], figure=h['figure'], dropped=h['drop'].strip(), utc=_now()))
    if done:
        json.dump(S, open(f, 'w'), indent=1)
        p = os.path.join(rd, 'gate', 'pregate_diff.json'); x = J(p, {}) or {}
        x['dropped'] = (x.get('dropped') or []) + done; json.dump(x, open(p, 'w'), indent=1)
    return done


def open_hits(U, s, drop=True):
    dropped = drop_history(U, s) if drop else []
    d = diff(U, s); d['dropped_now'] = dropped
    return d, [h for h in d['stale'] if not h['explained']]


def explain(U, s, ids, why):
    if not (why or '').strip(): raise SystemExit('explain needs --why "<why this figure is right as written>"')
    if HISTORY_WHY.search(why):  # Kilian 2026-11-24 ("why did round-1 not work as intended this is supposed to be strictly enforced")
        raise SystemExit('refused: an old figure kept to show what changed is dropped, not explained (Kilian 2026-11-23; INTC 2026-11-23 '
                         "explained 22.4x and $0.285 as 'dated history' and both failed gate round 2). Delete the old figure, or write it "
                         "as '(was X)' / 'X -> Y' so gate drops it. Explain only a figure that is current as written.")
    d = diff(U, s); have = {h['id']: h for h in d['stale']}
    bad = [i for i in ids if i not in have]
    if bad: raise SystemExit(f'not open stale hits: {bad} (run `python3 pregate_diff.py {U} {s}` for the ids)')
    p = os.path.join(RUNS, U, s, 'gate', 'pregate_diff.json'); x = J(p, {})
    for i in ids: x.setdefault('explained', {})[i] = dict(why=why, utc=_now(), figure=have[i]['figure'], path=have[i]['path'])
    json.dump(x, open(p, 'w'), indent=1)
    print(f'{len(ids)} hit(s) explained')


def report(d, show_moved=False, limit=40):
    L = [f"  dropped old figure {x['figure']} at {x['path']}: \"{x['dropped'][:160]}\"" for x in d.get('dropped_now') or []]
    L = [f"{d['symbol']} {d['update']} pre-gate diff vs {', '.join(d['refs']) or '(no base found)'}: {len(d['moved'])} number(s) moved; "
         f"{len(d['stale'])} stale old value(s) in text Kilian sees, {sum(1 for h in d['stale'] if not h['explained'])} not fixed or explained"
         + (f"; {len(d['dropped_now'])} old figure(s) kept to show what changed dropped from the text" if d.get('dropped_now') else '')] + L
    if show_moved:
        L += [f"  moved {m['path']}: {', '.join(f'{o['value']:g} ({o['ref']})' for o in m['old'])} -> {m['new']:g}" for m in d['moved']]
    op = [h for h in d['stale'] if not h['explained']]
    for h in op[:limit]:
        o = h['old_of'][0]
        L.append(f"  [{h['id']}] {h['figure']} at {h['path']} (old value of {o['path']}: {o['old'][0]:g} -> now {o['new']:g}): ...{h['snippet']}...")
    if len(op) > limit: L.append(f'  ... {len(op) - limit} more in gate/pregate_diff.json')
    if op: L.append('Fix each row/prose line, or explain it: python3 pregate_diff.py explain U SYM ID[,ID] --why "<why it is right as written>"')
    return '\n'.join(L)


def main(a):
    if a and a[0] == 'explain':
        if '--why' not in a: raise SystemExit(__doc__)
        i = a.index('--why'); why = a[i + 1]; del a[i:i + 2]
        explain(a[1], a[2].upper(), [x for x in a[3].split(',') if x], why); return
    if len(a) < 2: print(__doc__); sys.exit(2)
    d, op = open_hits(a[0], a[1].upper(), drop='--no-drop' not in a)
    print(report(d, '--moved' in a, 10 ** 6 if '--all' in a else 40))
    sys.exit(1 if op else 0)


if __name__ == '__main__': main(sys.argv[1:])
