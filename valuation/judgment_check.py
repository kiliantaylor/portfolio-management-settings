"""Judgment calls sized against the evidence-based alternatives (Kilian 2026-11-09, VST thread: "why was this not originally
flagged as a >5% decision" -> "yes make that rule change").

What went wrong: the old rule carded a judgment call only when its departure from the guide midpoint moved the research
target (12m) by more than 5%, sized against options the modeler listed itself. VST FY2027 sat at midpoint x 1.019 (2.9% off
the guide), so it was never carded, but building FY2027 from the hedge book moves the target about -7%; the 2028 hedge
prices alone span 131 to 158 a share.

The rule now: every forecast driver (and every reconciled input) whose level rests on judgment lists the evidence-based
ways to build it (filings, hedge books and forward curves, contracts, peer data, outside forecasts found by web search),
each with its source and a machine-readable patch; this script values each alternative with the engine; any alternative
that moves the research target (12m) by more than 5% must be a card in judgment_calls_for_kilian. Sized against the
evidence, never against the guide and never only against the modeler's own shortlist.

Schema (supervisor.json, top level, beside judgment_calls_for_kilian):
  "judgment_alternatives": [
    {"item": "gen_price",                       # a driver id, an inputs_detail `input`, or another named input
     "booked": "what the set uses now and its evidence",
     "alternatives": [
        {"build": "FY2027-28 from the Q3 hedge book: 2027 98% hedged at $X, 2028 at the deck's $Y",
         "source": "src 56 slide 9 | https://...",            # required: a src id or URL
         "patch": [{"path": "drivers[id=gen_price].years", "value": [61.2, 63.0, 64.1, 64.1]}],
         # written by this script (`size`), never by hand:
         "research_target": 141.9, "fv_effect_pct": -6.9, "sized": {"by": "judgment_check.py", "set_hash": "...", "utc": "..."}}],
     "why_none": ""}]                                         # only when no evidence-based alternative exists: say why
  A patch is {path, value} (replace), {path, scale} (x factor) or {path, add} (+ amount); scale/add work element-wise on
  a list or slice. Paths are dotted keys with [n] indices, [a:b] slices or [key=value] selectors, e.g.
  drivers[id=gen_price].quarters[8:12], horizon_multiples[0].multiple, balance.net_debt, adjustments[name=Moratorium].value.
  A quarterly driver is patched on its forecast quarters (its years are their sums; a .years patch or a reported quarter is
  refused). Each patch applies to a copy of model.reconciled_inputs; the set on disk is never changed.
  Each judgment_calls_for_kilian entry carries `item` (the same name) and an fv_effect_pct equal to one of that item's
  engine-sized alternatives (within 0.15 points), so every card's number is engine-sized.
  Card content (Kilian 2026-11-10: "give full context as well as the sources + reasoning supporting each"):
  {item, question, context, options: [{label, reasoning, sources: ["https://..." | "src N"], research_target, fv_effect_pct}],
   recommendation, why, fv_effect_pct}. `context` (>= 120 chars) says what the input is, what the set books and on what
  evidence, and why it is a call; each option has its own reasoning (>= 40 chars) and at least one source link (a URL, or a
  src id that resolves to a URL in supervisor.json `sources`). Bare string options are refused.

Which items must be listed: every driver in reconciled_inputs.drivers whose basis is not "reported", and every
inputs_detail row with basis "judgment". A driver pinned by a signed contract or the company's own stated plan may give
`why_none` instead of alternatives, but the reason must name that evidence.

    python3 judgment_check.py size D SYM       value every alternative of runs/D/SYM/supervisor.json with the engine and
                                               write research_target / fv_effect_pct / sized back into the file
    python3 judgment_check.py check D SYM      print problems (exit 1 if any): unlisted judgment items, alternatives without
                                               a source or patch, unsized or stale (set changed since sizing), an
                                               alternative over 5% with no card, a card whose fv_effect_pct is not engine-sized,
                                               a looser claim in the text ("no departure from the guide moves ... 5%"),
                                               a card without context, or an option without reasoning or a source link
    python3 judgment_check.py cards D SYM      each card as Markdown (context, options with reasoning and links, recommendation):
                                               the thread posts it beside the decision card, whose options hold one line each
    python3 judgment_check.py statement D SYM  the one sentence the update reply uses: what was sized and the largest effect

Wired into name_update.py gate (refuses to start the gate) and finish (refuses); override only with --why there (logged).
"""
import copy, datetime as dt, json, os, re, sys
HERE = os.path.dirname(os.path.abspath(__file__)).replace('/mnt/attach/project-files', '/mnt/project-files')
RUNS = os.environ.get('VAL_RUN_ROOT', os.path.join(HERE, 'runs'))
sys.path.insert(0, HERE)
CARD_PCT = 5.0      # Kilian's line: any evidence-based alternative moving the research target (12m) by more than 5%
CTX_MIN, REASON_MIN = 120, 40  # Kilian 2026-11-10: every valuation card gives full context and each option's reasoning + linked sources
MATCH_PCT = 0.15    # a card's fv_effect_pct must match an engine-sized alternative within this many points
CITE = re.compile(r'\bsrcs?\.?\s*#?\d|https?://|\bsource_ids?\b|\b10-[KQ]\b|\b8-K\b', re.I)
LOOSE = re.compile(r"(depart\w*|away|off|distance)\s+(?:\w+\s+){0,3}(?:from\s+)?(?:the\s+)?guid\w*(?:\s+midpoint)?[^.;]{0,120}?(?:5\s?%|5 percent|five percent)"
                   r"|(?:no|none of the)\s+judg\w*\s+calls?[^.;]{0,80}?(?:5\s?%|5 percent|five percent)", re.I)


def _j(p, d=None):
    try: return json.load(open(p))
    except (OSError, ValueError): return d


def _path(rd): return os.path.join(RUNS, rd[0], rd[1], 'supervisor.json')


def _seg(s):
    m = re.fullmatch(r'([^\[\]]+)((?:\[[^\]]+\])*)', s)
    if not m: raise ValueError(f'bad path segment {s!r}')
    return m.group(1), re.findall(r'\[([^\]]+)\]', m.group(2))


def _op(old, pt):
    """New value at a path: `value` (replace), `scale` (x factor) or `add` (+ amount), element-wise on lists."""
    if 'value' in pt: return pt['value']
    f = lambda v: v * pt['scale'] if 'scale' in pt else v + pt['add']
    if 'scale' not in pt and 'add' not in pt: raise ValueError(f"{pt.get('path')}: give value, scale or add")
    return [f(float(v)) for v in old] if isinstance(old, list) else f(float(old))


def _apply(RI, pt):
    """Apply one patch {path, value | scale | add} to RI. Path: dotted keys, [n] indices, [a:b] slices, [key=value] selectors."""
    path = pt['path']; parts = []
    for s in path.split('.'):
        k, sel = _seg(s); parts.append(('key', k)); parts += [('sel', x) for x in sel]
    m = re.match(r'drivers\[id=([^\]]+)\]\.(years|quarters)(?:\[(\d*):?(\d*)\])?', path)
    if m:  # guard the two traps: years of a quarterly driver (the quarters win) and reported quarters
        x = next((y for y in RI.get('drivers') or [] if str(y.get('id')) == m.group(1)), None)
        if x is not None and m.group(2) == 'years' and x.get('quarters') and RI.get('quarters'):
            raise ValueError(f'{path}: driver {m.group(1)} has quarters, so its years are their sums: patch .quarters[a:b] (forecast quarters)')
        if m.group(2) == 'quarters' and RI.get('quarters'):
            na = int(RI['quarters'].get('actual', 4)); lo = int(m.group(3)) if m.group(3) else 0
            if lo < na: raise ValueError(f'{path}: starts at quarter {lo}, but the first {na} quarters are reported; patch [{na}:...] or later')
    cur = RI
    for i, (t, x) in enumerate(parts):
        last = i == len(parts) - 1
        if t == 'key':
            if not isinstance(cur, dict) or (x not in cur and not last): raise ValueError(f'{path}: no key {x!r}')
            if last: cur[x] = _op(cur.get(x), pt); return
            cur = cur[x]
        else:
            if not isinstance(cur, list): raise ValueError(f'{path}: [{x}] on a non-list')
            if ':' in x:
                a, b = x.split(':', 1); a = int(a) if a else 0; b = int(b) if b else len(cur)
                if not last: raise ValueError(f'{path}: a slice must be last')
                nv = _op(cur[a:b], pt)
                if not isinstance(nv, list) or len(nv) != b - a: raise ValueError(f'{path}: slice of {b - a} needs {b - a} values')
                cur[a:b] = nv; return
            if '=' in x:
                k, v = x.split('=', 1); hit = [j for j, y in enumerate(cur) if isinstance(y, dict) and str(y.get(k)) == v]
                if len(hit) != 1: raise ValueError(f'{path}: [{x}] matches {len(hit)} entries')
                j = hit[0]
            else:
                j = int(x)
                if not -len(cur) <= j < len(cur): raise ValueError(f'{path}: index {j} out of range')
            if last: cur[j] = _op(cur[j], pt); return
            cur = cur[j]


def _value(mi, as_of, sym, spot, beta):
    import model_engine as ME
    m, _ = ME.apply_dcf_policy(copy.deepcopy(mi), sym, spot, beta)
    hv = ME.value(dict(m, _allow_long=True), as_of)['horizon_value']
    ev = (m.get('deal_events') or [None])[0]  # 2026-11-13: unsigned deal talks, target = p x deal price + (1 - p) x standalone (model_engine.deal_mix)
    return ev['probability'] * ev['price_per_share'] + (1 - ev['probability']) * hv if ev else hv


def _ctx(D, s):
    import model_engine as ME
    p = _path((D, s)); d = _j(p, {}) or {}
    RI = (d.get('model') or {}).get('reconciled_inputs')
    if not isinstance(RI, dict): sys.exit(f'{s} {D}: no model.reconciled_inputs in {p}')
    spot, beta = ME.prior_spot_beta(D, s)
    return p, d, RI, d.get('as_of') or D, spot, beta, ME.set_hash(p)


def size(D, s):
    p, d, RI, as_of, spot, beta, h = _ctx(D, s)
    base = _value(RI, as_of, s, spot, beta); utc = dt.datetime.now(dt.timezone.utc).strftime('%Y-%m-%dT%H:%MZ'); n = 0; errs = []
    for it in d.get('judgment_alternatives') or []:
        for a in it.get('alternatives') or []:
            for k in ('research_target', 'fv_effect_pct', 'sized', 'size_error'): a.pop(k, None)
            if not a.get('patch'): a['size_error'] = 'no patch'; errs.append(f"{it.get('item')}: {a.get('build', '')[:60]}: no patch"); continue
            t = copy.deepcopy(RI)
            try:
                for pt in a['patch']: _apply(t, pt)
                v = _value(t, as_of, s, spot, beta)
            except Exception as e:
                a['size_error'] = str(e)[:300]; errs.append(f"{it.get('item')}: {a.get('build', '')[:60]}: {str(e)[:200]}"); continue
            a.update(research_target=round(v, 2), fv_effect_pct=round((v / base - 1) * 100, 2), sized=dict(by='judgment_check.py', set_hash=h, base_target=round(base, 2), utc=utc)); n += 1
    d['judgment_alternatives_sized'] = dict(set_hash=h, base_target=round(base, 2), utc=utc, alternatives=n)
    json.dump(d, open(p, 'w'), indent=1)
    print(json.dumps(dict(symbol=s, round=D, base_target=round(base, 2), sized=n, errors=errs, statement=statement(D, s)), indent=1))
    return errs


def _items(d):
    return {str(it.get('item')): it for it in d.get('judgment_alternatives') or [] if it.get('item')}


def _texts(d):
    m = d.get('model') or {}; RI = m.get('reconciled_inputs') or {}
    out = [('model.reasoning', m.get('reasoning')), ('fair_value.reason', (d.get('fair_value') or {}).get('reason')),
           ('summary', d.get('summary'))]
    out += [(f"guidance_check {g.get('metric', '')} {g.get('period', '')}", g.get('why')) for g in RI.get('guidance_check') or [] if isinstance(g, dict)]
    out += [(f"judgment call {i}", c.get('why')) for i, c in enumerate(d.get('judgment_calls_for_kilian') or [])]
    return [(w, t) for w, t in out if isinstance(t, str)]


def check(D, s, d=None):
    import model_engine as ME
    p = _path((D, s)); d = d or _j(p, {}) or {}
    RI = (d.get('model') or {}).get('reconciled_inputs') or {}
    if not RI: return [dict(rule='no-set', where=p, why='no model.reconciled_inputs')]
    h = ME.set_hash(p); I = _items(d); V = []
    need = [x['id'] for x in RI.get('drivers') or [] if x.get('id') and (x.get('basis') or 'estimate') != 'reported']
    need += [r['input'] for r in RI.get('inputs_detail') or [] if r.get('input') and r.get('basis') == 'judgment']
    for k in need:
        if k not in I: V.append(dict(rule='unlisted', where=k, why='judgment-based driver/input with no judgment_alternatives entry (list the evidence-based ways to build it, or why_none)'))
    over = {}
    for k, it in I.items():
        al = it.get('alternatives') or []
        if not al:
            w = (it.get('why_none') or '').strip()
            if len(w) < 30: V.append(dict(rule='no-alternatives', where=k, why='no alternatives and no why_none naming the evidence that pins it'))
            continue
        for a in al:
            b = (a.get('build') or '')[:70]
            if not CITE.search(str(a.get('source') or '')): V.append(dict(rule='no-source', where=f'{k}: {b}', why='alternative has no src id or URL in `source`'))
            if a.get('size_error'): V.append(dict(rule='size-error', where=f'{k}: {b}', why=a['size_error'])); continue
            sz = a.get('sized') or {}
            if sz.get('by') != 'judgment_check.py' or not isinstance(a.get('fv_effect_pct'), (int, float)):
                V.append(dict(rule='unsized', where=f'{k}: {b}', why=f'not engine-sized: run python3 {HERE}/judgment_check.py size {D} {s}')); continue
            if sz.get('set_hash') != h:
                V.append(dict(rule='stale', where=f'{k}: {b}', why=f'set changed since sizing: re-run python3 {HERE}/judgment_check.py size {D} {s}')); continue
            if abs(a['fv_effect_pct']) > CARD_PCT: over.setdefault(k, []).append(a)
    cards = d.get('judgment_calls_for_kilian') or []
    for i, c in enumerate(cards):
        k = str(c.get('item') or ''); q = (c.get('question') or '')[:70]
        if k not in I: V.append(dict(rule='card-no-item', where=f'card {i}: {q}', why='card has no `item` matching a judgment_alternatives entry')); continue
        eff = [a['fv_effect_pct'] for a in I[k].get('alternatives') or [] if isinstance(a.get('fv_effect_pct'), (int, float))]
        fe = c.get('fv_effect_pct')
        if not isinstance(fe, (int, float)) or not any(abs(fe - e) <= MATCH_PCT for e in eff):
            V.append(dict(rule='card-not-sized', where=f'card {i}: {q}', why=f'fv_effect_pct {fe} is not one of the engine-sized effects for {k}: {eff}'))
    for i, c in enumerate(cards): V += card_problems(i, c, d)
    carded = {str(c.get('item')) for c in cards}
    for k, al in over.items():
        if k not in carded:
            V.append(dict(rule='uncarded-over-5', where=k, why='; '.join(f"{a.get('build', '')[:60]} -> {a['fv_effect_pct']:+.1f}% ({a['research_target']})" for a in al) + ': card it in judgment_calls_for_kilian'))
    for w, t in _texts(d):
        m = LOOSE.search(t)
        if m: V.append(dict(rule='loose-claim', where=w, why=f'"{m.group(0)[:160]}": the claim must be about the sized evidence-based alternatives (use `judgment_check.py statement`)'))
    return V


def _urls(refs, d):
    """Links behind an option's `sources`: URLs as given, and `src N` ids resolved through supervisor.json `sources`."""
    S = d.get('sources') or []; out = []
    for r in refs if isinstance(refs, list) else [refs]:
        r = str(r or '')
        out += re.findall(r'https?://\S+', r)
        for n in re.findall(r'\bsrcs?\.?\s*#?(\d+)', r, re.I):
            j = int(n)
            for x in (S[j - 1] if 0 < j <= len(S) else None, S[j] if 0 <= j < len(S) else None):
                if isinstance(x, dict) and str(x.get('url', '')).startswith('http'): out.append(x['url']); break
    return out


def card_problems(i, c, d):
    """Kilian 2026-11-10: "when you give me cards relevant to valuations i need you to give full context as well as the sources +
    reasoning supporting each". A card needs `context` (what the input is, what the set books and why it is a call) and options
    as {label, reasoning, sources}: each option's own case and at least one link (a URL, or a src id that resolves to one)."""
    q = (c.get('question') or '')[:70]; w = f'card {i}: {q}'; V = []
    if len((c.get('context') or '').strip()) < CTX_MIN:
        V.append(dict(rule='card-no-context', where=w, why=f'`context` missing or under {CTX_MIN} chars: say what the input is, what the set books and on what evidence, and why it is a call'))
    ops = c.get('options') or []
    if len(ops) < 2: V.append(dict(rule='card-options', where=w, why='a card needs at least two options'))
    for j, o in enumerate(ops):
        if not isinstance(o, dict):
            V.append(dict(rule='card-option-bare', where=f'{w} option {j}', why=f'bare option "{str(o)[:60]}": write {{label, reasoning, sources, research_target, fv_effect_pct}}')); continue
        lb = (o.get('label') or '')[:50]
        if len((o.get('reasoning') or '').strip()) < REASON_MIN:
            V.append(dict(rule='card-option-no-reasoning', where=f'{w} option {j} {lb}', why=f'`reasoning` missing or under {REASON_MIN} chars: the case for this option'))
        if not _urls(o.get('sources'), d):
            V.append(dict(rule='card-option-no-link', where=f'{w} option {j} {lb}', why='no source link: give a URL or a src id that resolves to one in `sources`'))
    return V


def cards(D, s):
    """Markdown for each card, posted in the thread beside the decision card (the card itself holds one line per option)."""
    d = _j(_path((D, s)), {}) or {}; out = []
    for i, c in enumerate(d.get('judgment_calls_for_kilian') or []):
        L = [f"**{s} card {i + 1}: {c.get('question', '')}**", '', f"Context: {c.get('context', '(missing)')}", '']
        for o in c.get('options') or []:
            if not isinstance(o, dict): L.append(f'- {o} (no reasoning or sources)'); continue
            v = (f" -> ${o['research_target']:.2f}" if isinstance(o.get('research_target'), (int, float)) else '') + \
                (f" ({o['fv_effect_pct']:+.1f}%)" if isinstance(o.get('fv_effect_pct'), (int, float)) else '')
            L.append(f"- **{o.get('label', '')}**{v}: {o.get('reasoning', '')}")
            L += [f'  - {u}' for u in dict.fromkeys(_urls(o.get('sources'), d))]
        L += ['', f"Recommendation: {c.get('recommendation', '')}. {c.get('why', '')}"]
        out.append('\n'.join(L))
    return '\n\n'.join(out) or f'No cards for {s} {D}.'


def statement(D, s):
    d = _j(_path((D, s)), {}) or {}; I = _items(d)
    al = [(k, a) for k, it in I.items() for a in it.get('alternatives') or [] if isinstance(a.get('fv_effect_pct'), (int, float))]
    if not al: return f'No evidence-based alternatives sized for {s} {D}.'
    k, a = max(al, key=lambda x: abs(x[1]['fv_effect_pct']))
    big = [x for x in al if abs(x[1]['fv_effect_pct']) > CARD_PCT]
    head = (f"{len(big)} evidence-based alternative(s) move the target >5%, all carded" if big else
            "None of the evidence-based alternatives moves the target >5%")
    return (f"{head} ({len(al)} alternatives sized by the engine on {len(I)} judgment items; largest {a['fv_effect_pct']:+.1f}% "
            f"on {k}: {a.get('build', '')[:90]}).")


def refuse(D, s, step, why=''):
    v = check(D, s)
    if not v: return
    lines = '\n'.join(f"  [{x['rule']}] {x['where']}: {x['why']}" for x in v)
    msg = (f"judgment-alternatives, {step} {s} {D}: {len(v)} problem(s) (Kilian 2026-11-09: size every judgment input against its "
           f"evidence-based alternatives; card any over 5%).\n{lines}\nAdd the alternatives to supervisor.json judgment_alternatives, "
           f"run python3 {HERE}/judgment_check.py size {D} {s}, card any over 5%. (name_update.py: the override flag is --why-judgment.)")
    import guards
    guards.need_why('judgment-alternatives', why, msg, symbol=s, update=D, step=step, problems=len(v))


if __name__ == '__main__':
    a = sys.argv[1:]
    if len(a) < 3: print(__doc__); sys.exit(1)
    D, s = a[1], a[2].upper()
    if a[0] == 'size': sys.exit(1 if size(D, s) else 0)
    elif a[0] == 'check':
        v = check(D, s); print(json.dumps(dict(ok=not v, problems=v), indent=1)); sys.exit(1 if v else 0)
    elif a[0] == 'statement': print(statement(D, s))
    elif a[0] == 'cards': print(cards(D, s))
    else: print(__doc__); sys.exit(1)
