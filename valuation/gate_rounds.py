"""Citation-gate round cap and changed-rows-only re-gates (RULES.md 24; Kilian 2026-11-18 card "Yes, rule and code" after ABT
ran 13 gate rounds: "running 13 gate rounds is too many", "these aren't reasons for doing a new round").

- name_update.py gate: round 4 of a round folder is refused outright (no override). Rounds 2-3 need --why, and the reason
  must say why a re-check beats carding the rows or Kilian's acceptance (gate_accept.py), not just list what changed.
- run_digest.py gate-update (round >= 2): only rows whose text or value changed since the last merged round are checked.
  Shard URL groups whose figures are all unchanged carry the earlier shard results; the text agent reviews only the changed
  parts of supervisor.json, and earlier passed text results on unchanged parts carry. Earlier FAILURES never carry: they
  go back to their checker (Kilian 2026-11-23, CRDO round 3 re-merged four round-2 fixes). Each round saves gate/rows_rN.json (what it
  checked) and gate-merge saves gate/merged_rN.json (tagged results), so the next round can compare.
- run_digest.py gate-merge (round >= 2): unsupported / trend_only / back_solved rows are listed in gate/must_card.json and
  in brief_fix.md as must-card items: they go to Kilian as cards now, not to another round.
- must-card rows whose sized options all move the target < 0.5% are noted, not carded (RULES.md 26, small_flag.py), once the
  feeds-forward check passes.
- sweep: figures a fix replaced (supervisor.json citation_audit.corrections `from`) that still appear in the set's prose.

- every round 2-3 --why reads "trigger: <the change or finding that triggered it, naming the changed rows>; reason: <why a
  re-check beats carding / gate_accept>" (Kilian 2026-11-18: "if you're not providing reasons for why you need extra rounds the
  gate is not working as intended"). The trigger must name a part of supervisor.json that changed since the last round (checked
  against gate/rows_rN.json); nothing changed = refused. update.json gate_rounds logs trigger, reason and the changed parts;
  every refusal and `status` print that history. Each round also saves gate/values_rN.json for carry_trace.py.

  python3 gate_rounds.py status U SYM        round history (trigger, reason, changed parts), count, cap, carry basis, must-card
  python3 gate_rounds.py sweep U SYM [--all] stale figures still in the prose (corrections since the last gate round)
"""
import hashlib, json, os, re, sys

HERE = os.path.dirname(os.path.abspath(__file__)); RUNS = os.path.join(HERE, 'runs')
MAX_ROUNDS = 3
MUST_CARD = ('unsupported', 'trend_only', 'back_solved')
SKIP_TOP = {'sources', 'citation_audit', 'judgment_calls_history', 'key_drivers_text_before', 'judgment_alternatives_sized',
            'model', 'judgment_calls_for_kilian', 'judgment_alternatives'}
SWEEP_LOGS = {'set_review', 'gate', 'changes', 'corrections'}  # logs of past states, anywhere in the file
SWEEP_SKIP = {'citation_audit', 'judgment_calls_history', 'key_drivers_text_before', 'changes', 'sources'}


def J(p): return json.load(open(p))


def _h(x): return hashlib.sha1(json.dumps(x, sort_keys=True, default=str).encode()).hexdigest()[:16]


def item_fp(it):
    """A checklist item's text and value as the agents see them."""
    return _h([it.get('origin'), it.get('item'), it.get('value'), it.get('basis'), sorted(it.get('urls') or []), it.get('derivation')])


def round_no(rd):
    """The round being gated now = gate_rounds logged in update.json (name_update.py gate logs a round before gate-update)."""
    p = os.path.join(rd, 'update.json')
    try: return len(J(p).get('gate_rounds') or [])
    except (OSError, ValueError): return 0


# ---------------------------------------------------------------------------------------------------------------- sections
def sections(S):
    """supervisor.json split into checkable parts: key -> (label, names to find it by in a result, fingerprint)."""
    out = {}
    def put(key, label, names, val): out[key] = dict(label=label, names=[n.lower() for n in names if n and len(n) >= 2], fp=_h(val))
    for k, v in S.items():
        if k not in SKIP_TOP: put(f'S.{k}', f'supervisor.json {k}', [k], v)
    RI = (S.get('model') or {}).get('reconciled_inputs') or {}
    for k, v in (S.get('model') or {}).items():
        if k != 'reconciled_inputs': put(f'M.{k}', f'model.{k}', [k], v)
    for k, v in RI.items():
        if k not in ('inputs_detail', 'drivers', 'guidance_check'): put(f'RI.{k}', f'model.reconciled_inputs.{k}', [k], v)
    for i, r in enumerate(RI.get('inputs_detail') or []):
        n = str(r.get('input') or i); put(f'row:{n}', f'inputs_detail row "{n}"', [n, n.split(' (')[0]], r)
    for d in RI.get('drivers') or []:
        n = str(d.get('id')); put(f'drv:{n}', f'driver {n} ({d.get("label", "")})', [n, f'driver:{n}', d.get('label')], d)
    for g in RI.get('guidance_check') or []:
        n = f"{g.get('metric')} {g.get('fy')}"; put(f'gc:{n}', f'guidance_check {n}', [n, str(g.get('metric') or '')], g)
    for i, c in enumerate(S.get('judgment_calls_for_kilian') or []):
        n = str(c.get('item') or c.get('id') or i); put(f'jc:{i}:{n}', f'judgment_calls_for_kilian #{i} ({n})', [n], c)
    ja = S.get('judgment_alternatives') or []
    for i, a in enumerate(ja.values() if isinstance(ja, dict) else ja):
        n = str((a or {}).get('item') or i) if isinstance(a, dict) else str(i); put(f'ja:{i}:{n}', f'judgment_alternatives {n}', [n], a)
    return out


def _word(n, t):
    return re.search(r'(?<![a-z0-9_])' + re.escape(n) + r'(?![a-z0-9_])', t) is not None


def place(r, secs):
    """Section keys a text-agent result refers to (by origin, item and value text); empty = could not place it."""
    t = ' '.join(str(r.get(k) or '') for k in ('origin', 'item', 'value')).lower()
    hit = {k for k, s in secs.items() if any(_word(n, t) for n in s['names'])}
    fam = {'guidance': 'gc:', 'judgment_calls_for_kilian': 'jc:', 'judgment_alternatives': 'ja:'}
    for word, pre in fam.items():  # "supervisor guidance" with no metric named -> every entry of that family
        if word in t and not any(k.startswith(pre) for k in hit): hit |= {k for k in secs if k.startswith(pre)}
    return hit


# ---------------------------------------------------------------------------------------------------------------- carry
def prior(gd, rn):
    """Newest earlier round r < rn with both gate/rows_r{r}.json and gate/merged_r{r}.json -> (r, rows, merged) or None."""
    for r in range(rn - 1, 0, -1):
        a, b = os.path.join(gd, f'rows_r{r}.json'), os.path.join(gd, f'merged_r{r}.json')
        if os.path.exists(a) and os.path.exists(b): return r, J(a), J(b)
    return None


def plan(rd, rn, groups, arith, claims, norm, S):
    """Re-gate scope for round rn >= 2. Returns dict(groups, arith, claims, norm, carried, scope_text, basis, rows) where
    groups/arith/claims/norm are what still goes to agents, carried the earlier results kept, rows what to save."""
    gd = os.path.join(rd, 'gate'); secs = sections(S) if S else {}
    rows = dict(round=rn, sections={k: v['fp'] for k, v in secs.items()},
                urls={u: sorted(item_fp(it) for it in its) for u, its in groups.items()},
                arith=sorted(item_fp(it) for it in arith), n_corrections=len(((S or {}).get('citation_audit') or {}).get('corrections') or []))
    out = dict(groups=groups, arith=arith, claims=claims, norm=norm, carried=[], scope_text='', basis=None, rows=rows)
    pr = prior(gd, rn) if rn >= 2 else None
    if not pr:
        if rn >= 2: out['basis'] = 'no earlier merged round with saved rows (round folder from before 2026-11-18): full scope this round'
        return out
    r0, R0, M0 = pr
    old_s = R0.get('sections') or {}
    changed = {k for k in secs if old_s.get(k) != secs[k]['fp']} | {k for k in old_s if k not in secs}
    keep_urls = {u for u, fps in rows['urls'].items() if (R0.get('urls') or {}).get(u) == fps}
    res0 = M0.get('results') or []
    shard_by_url = {}
    for x in res0:
        if x.get('_src') == 'shard': shard_by_url.setdefault(x.get('source_url'), []).append(x)
    # Kilian 2026-11-23 (CRDO round 3 re-merged four failures round 2 had fixed): only passed results carry. An earlier failure
    # goes back to its checker, because the fix that cleared it often sits where the fingerprints do not look (a source
    # entry's date, a card or alternative the script cannot place). A URL with any earlier failure is re-opened, and
    # gate_carry.carry keeps its passed figures, so only the failed ones are checked again.
    keep_urls = {u for u in keep_urls if shard_by_url.get(u) and all(x.get('result') == 'ok' for x in shard_by_url[u])}
    carried = [dict(x, carried_from_round=x.get('carried_from_round') or r0) for u in keep_urls for x in shard_by_url[u]]
    recheck = []
    for x in res0:
        if x.get('_src') != 'text': continue
        if x.get('result') != 'ok': recheck.append(x); continue
        if not (place(x, secs) & changed): carried.append(dict(x, carried_from_round=x.get('carried_from_round') or r0))
    old_a = set(R0.get('arith') or [])
    arith2 = [it for it in arith if item_fp(it) not in old_a]
    def in_scope(r):  # a part that changed, or one the script cannot place, stays with the text agent
        h = place(r, secs); return not h or bool(h & changed)
    claims2 = [c for c in claims if in_scope(dict(origin=c.get('origin'), value=c.get('text')))]
    norm2 = [n for n in norm if in_scope(dict(origin=n.get('origin'), item=n.get('item')))]
    groups2 = {u: v for u, v in groups.items() if u not in keep_urls}
    lab = [secs[k]['label'] for k in sorted(changed) if k in secs] + [f'(removed) {k}' for k in sorted(changed) if k not in secs]
    rc = '\n'.join(f"- [{x.get('origin')}] {str(x.get('item'))[:200]} = {str(x.get('value'))[:200]}: was {x.get('result')} ({str(x.get('page_shows') or x.get('note') or '')[:200]})" for x in recheck)
    out.update(groups=groups2, arith=arith2, claims=claims2, norm=norm2, carried=carried, recheck=recheck,
               text_empty=not (changed or recheck or arith2 or claims2 or norm2),
               basis=f'round {rn} re-gate against merged round {r0}: {len(changed)} of {len(secs)} parts of supervisor.json changed; '
                     f'{len(keep_urls)} of {len(groups)} source URLs unchanged (results carried); {len(carried)} earlier results carried',
               scope_text=(f"RE-GATE SCOPE (round {rn}; RULES.md 24, Kilian 2026-11-18: re-gates check only what changed). In steps 1 and 5 "
                           f"check ONLY the parts of supervisor.json listed here; round {r0}'s results on every other part carry by script. "
                           f"Changed since round {r0}:\n" + ('\n'.join(f'- {l}' for l in lab) or '- (nothing in supervisor.json changed)') +
                           ("\nAlso re-check these earlier failures against the set as it stands now (a fix may have cleared them; failures never carry by script):\n" + rc if rc else '')))
    return out


def save_rows(gd, rows):
    json.dump(rows, open(os.path.join(gd, f"rows_r{rows['round']}.json"), 'w'), indent=1)


# ---------------------------------------------------------------------------------------------------------------- merge side
def must_card(rd, fails):
    """Round >= 2: unsupported / trend_only / back_solved failures go to Kilian as cards now (RULES.md 24 + rule 4)."""
    rn = round_no(rd); gd = os.path.join(rd, 'gate'); p = os.path.join(gd, 'must_card.json')
    if rn < 2:
        if os.path.exists(p): os.remove(p)
        return rn, []
    m = [dict(origin=f.get('origin'), item=f.get('item'), value=f.get('value'), result=f.get('result'),
              note=f.get('note') or f.get('page_shows') or '') for f in fails if f.get('result') in MUST_CARD]
    json.dump(dict(round=rn, items=m, rule='RULES.md 24: after round 2, unsourced / trend_only rows go to Kilian as cards now'), open(p, 'w'), indent=1)
    return rn, m


def fix_note(rn, must):
    """Text for brief_fix.md: which round this is, and the must-card list."""
    t = [f'GATE ROUND {rn} of at most {MAX_ROUNDS} (RULES.md 24, Kilian 2026-11-18). ' +
         ('This is the last round: no re-gate follows. Whatever stays open goes to Kilian as cards or his gate_accept.'
          if rn >= MAX_ROUNDS else 'A re-gate re-opens only figures whose value, claim text, source or derivation you change (gate_carry.py), so leave unchanged figures and sentences alone.')]
    if must:
        t.append(f'MUST-CARD ROWS ({len(must)}; round >= 2): do NOT try to source these again. For each, write a '
                 '`judgment_calls_for_kilian` entry now (context, options each with reasoning, a source link and an engine-sized '
                 'fv_effect_pct; booked value as one option; do not pick), and leave the row in open_failures with fix '
                 '"carded: <item>". They close when Kilian picks (gate_accept.py), not in another round: his picks never trigger a round (RULES.md 24, Kilian 2026-11-22). EXCEPTION, RULES.md 26 '
                 '(Kilian 2026-11-18 "Skip under 0.5%, note it"): after sizing, run `python3 small_flag.py suggest U SYM`. A row whose '
                 'alternatives all move the target < 0.5% fails first if it does not feed forward (no_feed_forward / no_effect): fix '
                 'the model so the next period jumps off it and re-size; if it passes, write a `small_flag_notes` entry (item, row, '
                 'alternatives, searched) instead of a card and run `small_flag.py accept U SYM`; its one-line note goes in the reply:')
        t += [f"- [{m['origin']}] {str(m['item'])[:200]} = {str(m['value'])[:160]} ({m['result']})" for m in must]
    return '\n'.join(t)


# ---------------------------------------------------------------------------------------------------------------- gate side
def history(rd):
    """The round log of update.json gate_rounds, one line per round (what triggered it and why), for refusals and status."""
    try: gr = J(os.path.join(rd, 'update.json')).get('gate_rounds') or []
    except (OSError, ValueError): gr = []
    L = []
    for i, g in enumerate(gr, 1):
        t = g.get('trigger') or ''; r = g.get('reason') or ''
        if not (t or r): r = str(g.get('why') or '')
        ch = g.get('changed') or []
        L.append(f"  round {i} ({g.get('utc')}): " + (f'trigger: {t[:300]}; ' if t else '') + f'reason: {r[:400]}' +
                 (f" [changed since round {i - 1}: {len(ch)} part(s): {', '.join(ch[:8])}{' ...' if len(ch) > 8 else ''}]" if ch else ''))
    return '\n'.join(L) or '  (no rounds logged)'


def changed_parts(rd, n):
    """Parts of supervisor.json changed since round n-1's prep (gate/rows_r{n-1}.json) -> (labels, {names}) or (None, None)."""
    rf = os.path.join(rd, 'gate', f'rows_r{n - 1}.json')
    try: old = J(rf).get('sections') or {}; S = J(os.path.join(rd, 'supervisor.json'))
    except (OSError, ValueError): return None, None
    secs = sections(S)
    ch = sorted(k for k in secs if old.get(k) != secs[k]['fp']) + sorted(k for k in old if k not in secs)
    names = set()
    for k in ch:
        if k in secs: names |= set(secs[k]['names'])
        else: names.add(k.split(':', 1)[-1].split('.', 1)[-1].lower())
    return [secs[k]['label'] if k in secs else f'(removed) {k}' for k in ch], {x for x in names if len(x) >= 3}


WHY_FORM = ('--why "trigger: <the change or finding that triggered this round, naming the rows it changed>; reason: <why a '
            're-check beats carding those rows or Kilian\'s gate_accept>"')


# Kilian's own picks are never a round's trigger (Kilian 2026-11-22, NVDA: "why does this need a gate round")
PICKS = re.compile(r"\b(?:kilian'?s?|his|owner'?s?|card)\s+(?:own\s+)?(?:card\s+)?(?:pick|answer|choice|decision|tap)(?:s|ed)?\b|\b(?:picked|answered|chose)\s+(?:by|on)\s+(?:kilian|his card|the card)", re.I)


def parse_why(why):
    """'trigger: ...; reason: ...' -> (trigger, reason); either may be ''."""
    w = (why or '').strip()
    t = re.search(r'\btrigger\s*:\s*(.+?)(?=\breason\s*:|$)', w, re.I | re.S)
    r = re.search(r'\breason\s*:\s*(.+)$', w, re.I | re.S)
    return (t.group(1).strip(' ;') if t else ''), (r.group(1).strip(' ;') if r else '')


def check_round(U, s, why):
    """name_update.py gate: refuse round 4+ outright; rounds 2-3 need --why naming the change that triggered the round
    (Kilian 2026-11-18: "if you're not providing reasons for why you need extra rounds the gate is not working as intended")
    and a reason that weighs a re-check against carding / accepting. Every refusal prints the round history."""
    rd = os.path.join(RUNS, U, s); n = round_no(rd) + 1
    if n > MAX_ROUNDS:
        sys.exit(f'REFUSED (gate-round-cap, RULES.md 24): {s} {U} already had {n - 1} citation-gate rounds; the cap is {MAX_ROUNDS} '
                 'and there is no override (Kilian 2026-11-18). Card every open row to Kilian (rule 2/4) or ask him to accept '
                 f'the fixes himself: python3 gate_accept.py accept {U} {s} --msg <his message id> --quote "<his words>" --items N\n'
                 f'Round history (what triggered each round and why):\n{history(rd)}')
    if n >= 2:
        t, r = parse_why(why); labs, names = changed_parts(rd, n)
        bad = []
        if not t: bad.append('no "trigger:" part: name the change or gate finding that triggered this round')
        if not r or len(r) < 40 or not re.search(r'card|accept', r, re.I):
            bad.append('the "reason:" part (>= 40 chars) must say why a re-check beats carding the open rows or Kilian\'s gate_accept (name both)')
        if t and PICKS.search(t):  # Kilian 2026-11-22 (NVDA): "fix this so it doesn't automatically happen again"
            bad.append('the trigger is Kilian\'s picks: applying his card / message picks never needs a gate round (RULES.md 24). Carry '
                       'them through, re-size (judgment_check.py size), sweep, close the carded rows with gate_accept.py quoting his '
                       'card or message, then finish. A round is only for a change that is not his pick; name that change instead')
        if labs is not None and not labs:
            bad.append(f'nothing in supervisor.json changed since round {n - 1}: a re-gate would re-check nothing; card the open rows or ask for gate_accept')
        elif t and names and not any(_word(x, t.lower()) for x in names):
            bad.append(f'the trigger names none of the {len(labs)} part(s) that changed since round {n - 1}: ' + '; '.join(labs[:12]) +
                       (' ...' if len(labs) > 12 else ''))
        if bad:
            sys.exit(f'REFUSED (gate-round-reason, RULES.md 24): round {n} of {MAX_ROUNDS} for {s} {U}:\n- ' + '\n- '.join(bad) +
                     f'\nForm: {WHY_FORM}. The usual valid reason is the one independent re-check of a fix agent\'s own fixes (round 2).\n'
                     f'Round history:\n{history(rd)}')
    return n


def entry(U, s, n, why):
    """Fields name_update.py gate logs for round n beside utc/why: trigger, reason and the parts changed since round n-1."""
    if n < 2: return {}
    rd = os.path.join(RUNS, U, s); t, r = parse_why(why); labs, _ = changed_parts(rd, n)
    return dict(trigger=t, reason=r, changed=labs if labs is not None else ['(no rows_r file for the earlier round: not computed)'])


def snapshot(U, s, n, utc=''):
    """gate/values_rN.json for carry_trace.py: series values and quoting text as round n is gated (later rounds compare)."""
    import carry_trace
    rd = os.path.join(RUNS, U, s)
    try: RI = (J(os.path.join(rd, 'supervisor.json')).get('model') or {}).get('reconciled_inputs')
    except (OSError, ValueError): RI = None
    if isinstance(RI, dict): carry_trace.snapshot(rd, n, RI, utc)


def _figs(t):  # figures distinctive enough to search for: with % or $, or at least 3 digits
    ms = {m.group(0).strip() for m in re.finditer(r'-?\$?\d[\d,]*\.\d+%?|-?\$?\d[\d,]*%|\$\d[\d,]*(?:\.\d+)?\s?[BMK]\b', str(t or ''))}
    return {m for m in ms if '%' in m or '$' in m or sum(c.isdigit() for c in m) >= 3}


def sweep(U, s, all_=False):
    """Figures a fix replaced that still appear in the set's prose -> [(correction item, figure, json path, snippet)]."""
    rd = os.path.join(RUNS, U, s); S = J(os.path.join(rd, 'supervisor.json'))
    C = (S.get('citation_audit') or {}).get('corrections') or []
    start = 0
    if not all_:  # corrections written since the newest gate round's prep (None: round folder from before 2026-11-18, basis unknown)
        gd = os.path.join(rd, 'gate'); rn = round_no(rd)
        rf = os.path.join(gd, f'rows_r{rn}.json')
        start = (J(rf).get('n_corrections') or 0) if os.path.exists(rf) else None
    strings = []
    def walk(x, p):
        if isinstance(x, dict):
            for k, v in x.items():
                if not ((p == '' and k in SWEEP_SKIP) or k in SWEEP_LOGS or 'history' in k or k.endswith('_before')): walk(v, f'{p}.{k}' if p else k)
        elif isinstance(x, list):
            for i, v in enumerate(x): walk(v, f'{p}[{i}]')
        elif isinstance(x, str): strings.append((p, x))
    walk(S, '')
    hits = []
    for c in C[start or 0:]:
        gone = _figs(c.get('from')) - _figs(c.get('to'))
        for f in gone:
            for p, t in strings:
                i = t.find(f)
                if i >= 0 and re.match(r'(?![\d.,])', t[i + len(f):i + len(f) + 1] or ' ') and (i == 0 or not t[i - 1].isdigit()):
                    hits.append((c.get('item'), f, p, t[max(0, i - 60):i + len(f) + 60].replace('\n', ' ')))
    return start, len(C), hits


def main(a):
    if not a or a[0] not in ('status', 'sweep'): print(__doc__); sys.exit(2)
    U, s = a[1], a[2].upper(); rd = os.path.join(RUNS, U, s)
    if a[0] == 'sweep':
        st, n, hits = sweep(U, s, '--all' in a)
        print(f'{s} {U}: corrections {st if st is not None else "0 (older folder: all)"}..{n}: {len(hits)} replaced figure(s) still in the prose')
        for it, f, p, snip in hits: print(f'- {f} (corrected in "{str(it)[:80]}") at {p}: ...{snip}...')
        sys.exit(1 if hits else 0)
    rn = round_no(rd); gd = os.path.join(rd, 'gate')
    mc = J(os.path.join(gd, 'must_card.json')) if os.path.exists(os.path.join(gd, 'must_card.json')) else None
    print(f'Round history:\n{history(rd)}')
    print(json.dumps(dict(symbol=s, round=U, gate_rounds=rn, cap=MAX_ROUNDS, next_allowed=rn < MAX_ROUNDS,
                          carry_basis=[f for f in sorted(os.listdir(gd)) if re.match(r'(rows|merged)_r\d+\.json', f)] if os.path.isdir(gd) else [],
                          must_card=len((mc or {}).get('items') or [])), indent=1))


if __name__ == '__main__': main(sys.argv[1:])
