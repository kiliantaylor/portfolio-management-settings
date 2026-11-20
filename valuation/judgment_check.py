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
  src id that resolves to a URL in supervisor.json `sources`, or to a project-file path for an upload with no public link;
  `src N` = sources[N-1] only, never a neighbor). Bare string options are refused.

Plausibility screen (Kilian 2026-11-18 22:11Z, NVDA supply floor at the history ratio, FY28 ~$1.6T: "why is this even
suggested if it's not remotely plausible"; line 2.0x picked on his card 22:19Z): `size` also writes each alternative's
`plausibility` {distance, metric, fy, value, anchors, over}: its revenue / EPS per forecast year against the band of the
company guide, the latest consensus, the booked build and any `anchors` the item names ([{metric, fy, value, source, what}],
e.g. a buyer build). distance > 2.0x against all of them (and at least one outside anchor) = implausible: it must sit in
`rejected_alternatives` with `rejected` (>= 40 chars) and is never carded. A rejection the screen does not confirm (inside
2.0x, or no outside anchor) is refused: an evidence-based alternative is never cut only because it is large (rule 2).

Small flagged paths (RULES.md 26, Kilian 2026-11-18 22:21Z "Skip under 0.5%, note it"): `check` refuses a draft card on a
gate-flagged row (gate_rN_row) whose options all move the target < 0.5% (rule small-flag-card): small_flag.py runs the
feeds-forward check, then the row is noted in the reply and closed on the standing card instead.

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
    python3 judgment_check.py card-gate D SYM  the card_check hook's own checks, run by hand (gate status of runs/D/SYM + the form of each
                                               card's message); `cards` prints the same to stderr and exits 1 when it fails
                                               (Kilian 2026-11-18, NVDA: cards went out ungated and unexplained where hooks did not load)
    python3 judgment_check.py screen D SYM [PATCH_JSON] [--item ITEM]
                                               the plausibility screen (Kilian 2026-11-18, line PLAUSIBLE_X = 2.0x): every
                                               alternative's revenue / EPS against its anchors, or one candidate patch (a gate
                                               checker's missing_alternative before it is listed); IMPLAUSIBLE = record under
                                               rejected_alternatives with a reason, never card
    python3 judgment_check.py statement D SYM  the one sentence the update reply uses: what was sized and the largest effect

Wired into name_update.py gate (refuses to start the gate) and finish (refuses); override only with --why there (logged).
"""
import copy, datetime as dt, json, os, re, sys
HERE = os.path.dirname(os.path.abspath(__file__)).replace('/mnt/attach/project-files', '/mnt/project-files')
RUNS = os.environ.get('VAL_RUN_ROOT', os.path.join(HERE, 'runs'))
sys.path.insert(0, HERE)
CARD_PCT = 5.0      # Kilian's line: any evidence-based alternative moving the research target (12m) by more than 5%
CTX_MIN, REASON_MIN = 120, 40  # Kilian 2026-11-10: every valuation card gives full context and each option's reasoning + linked sources
SMALL_FLAG_PCT = 0.5  # RULES.md 26: a gate-flagged path whose options all move the target less than this is noted, not carded (small_flag.py)
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


def _value(mi, as_of, sym, spot, beta, out=None):
    """Research target (12m) of a set. out (a dict): also filled with the key outputs the plausibility screen reads."""
    import model_engine as ME
    m, _ = ME.apply_dcf_policy(copy.deepcopy(mi), sym, spot, beta)
    v = ME.value(dict(m, _allow_long=True), as_of); hv = v['horizon_value']
    if out is not None: out.update(_key_outputs(v))
    ev = (m.get('deal_events') or [None])[0]  # 2026-11-13: deal events, target = p x deal value + (1 - p) x standalone (model_engine.deal_mix)
    if not ev: return hv
    D = ME._deal_odds().deal_value(ev, v['horizon'])  # 2026-11-16: stock / mixed = cash x df + ratio x acquirer research target (12m)
    return ev['probability'] * D + (1 - ev['probability']) * hv


# ---- Plausibility screen (Kilian 2026-11-18 22:11Z, NVDA: "why is this even suggested if it's not remotely plausible") ----
# A supply-floor history ratio put NVDA FY28 revenue at ~$1.6T (EPS $39.0 vs consensus $15.63, booked $16.62) and reached a
# card through the >5% rule. An alternative whose revenue or EPS sits more than PLAUSIBLE_X outside the band of EVERY
# independent anchor for a forecast year (company guide, latest consensus, the set's own booked build, and any buyer build the
# item names in `anchors`) goes under rejected_alternatives with a written `rejected` reason and is never carded. The screen
# never cuts an alternative only because it is large (rule 2): a rejection inside PLAUSIBLE_X, or with no outside anchor for
# the year, is refused. Threshold from the 11-18 survey of 1,736 past alternatives (notes/plausibility_screen_1118/survey.md):
# above 2.0x only the two NVDA ratio options; next SPCX's signed-book floor at 1.82x (evidence-based, stays carded).
PLAUSIBLE_X = 2.0
REJECT_MIN = 40      # a rejection gives its reason: which anchors, how far outside, why the build does not fit
_REV = re.compile(r'(total |net )?(revenue|sales|net sales)( \(\$?m\))?$', re.I)
_EPS = re.compile(r'(adj\.? |adjusted |non-gaap |diluted |gaap )?eps\b', re.I)
_FY = re.compile(r'(?:FY|CY)\s?(\d{4})$', re.I)


def _key_outputs(v):
    """Revenue by fiscal year (row end year) for the first three forecast years, and EPS by fiscal year where the set has
    all four quarters of that year."""
    rows = [r for r in v.get('rows') or [] if not str(r.get('label', '')).startswith('fade')][:3]
    rev = {int(str(r['end'])[:4]): r['revenue'] for r in rows if r.get('end') and isinstance(r.get('revenue'), (int, float))}
    lab = {r.get('label'): int(str(r['end'])[:4]) for r in rows if r.get('end')}; q = {}
    for x in v.get('quarters') or []:
        y = lab.get(x.get('fy'))
        if y is not None and isinstance(x.get('eps'), (int, float)): q.setdefault(y, []).append(x['eps'])
    return dict(revenue=rev, eps={y: sum(e) for y, e in q.items() if len(e) == 4})


def _metric(m):
    m = str(m or '').strip()
    return 'revenue' if _REV.match(m) else 'eps' if _EPS.match(m) and 'whisper' not in m.lower() else None


def anchors(d, base, it=None):
    """{metric: {year: [(name, value)]}} of independent anchors: latest consensus per year (reference.consensus,
    guidance_check consensus as a level), the company's annual guide (a level, or a growth guide on the booked prior year),
    the booked build itself, and the item's own `anchors` [{metric, fy, value, source, what}] (e.g. a buyer build).
    Anchors only widen the band, so naming more can never screen more out."""
    RI = (d.get('model') or {}).get('reconciled_inputs') or {}; A = {'revenue': {}, 'eps': {}}; best = {}
    for c in (RI.get('reference') or {}).get('consensus') or []:
        if not isinstance(c, dict) or not isinstance(c.get('value'), (int, float)): continue
        k = _metric(c.get('metric')); y = _FY.match(str(c.get('fy') or '').strip())
        if not k or not y: continue
        key = (k, int(y.group(1))); a = str(c.get('as_of') or '')
        if key not in best or a >= best[key][0]: best[key] = (a, c['value'])
    for (k, y), (_, v) in best.items(): A[k].setdefault(y, []).append(('consensus', v))
    for g in RI.get('guidance_check') or []:
        if not isinstance(g, dict): continue
        y = _FY.match(str(g.get('fy') or '').strip()); lo, hi = g.get('guide_low'), g.get('guide_high')
        if not y or not isinstance(lo, (int, float)) or not isinstance(hi, (int, float)): continue
        y = int(y.group(1)); mid = (lo + hi) / 2; m = str(g.get('metric') or '').strip().lower(); k = _metric(m)
        if k == 'revenue' and mid > 100: A['revenue'].setdefault(y, []).append(('guide', mid))
        elif k == 'eps' and abs(mid) < 1000: A['eps'].setdefault(y, []).append(('guide', mid))
        elif re.fullmatch(r'(total |net )?(revenue|sales) growth', m) and abs(mid) < 3 and (y - 1) in (base.get('revenue') or {}):
            A['revenue'].setdefault(y, []).append(('guide', base['revenue'][y - 1] * (1 + mid)))
        c = g.get('consensus')
        if isinstance(c, (int, float)) and k == 'revenue' and c > 100 and ('revenue', y) not in best: A['revenue'].setdefault(y, []).append(('consensus', c))
    for x in (it or {}).get('anchors') or []:
        if not isinstance(x, dict) or not isinstance(x.get('value'), (int, float)) or not CITE.search(str(x.get('source') or '')): continue
        k = _metric(x.get('metric')); y = _FY.match(str(x.get('fy') or '').strip())
        if k and y: A[k].setdefault(int(y.group(1)), []).append((str(x.get('what') or 'named anchor')[:40], x['value']))
    return A


def plausibility(out, base, A):
    """How far an alternative's key outputs sit outside its anchor band. Per metric and forecast year that has an OUTSIDE
    anchor (not only the booked build): 1.0 inside [lowest, highest]; value / highest above; lowest / value below.
    Returns the worst year: {distance, metric, fy, value, anchors, over}; distance None when nothing outside anchors it."""
    worst = None
    for k in ('revenue', 'eps'):
        for y, v in (out.get(k) or {}).items():
            outside = [(n, a) for n, a in A[k].get(y, []) if a and a > 0]
            if not outside or not isinstance(v, (int, float)): continue
            band = outside + ([('booked', base[k][y])] if (base.get(k) or {}).get(y, 0) > 0 else [])
            lo, hi = min(a for _, a in band), max(a for _, a in band)
            dist = float('inf') if v <= 0 else v / hi if v > hi else lo / v if v < lo else 1.0
            if worst is None or dist > worst['distance']:
                worst = dict(distance=round(dist, 3) if dist != float('inf') else 999.0, metric=k, fy=y, value=round(v, 2),
                             anchors=[[n, round(a, 2)] for n, a in band])
    if worst is None: return dict(distance=None, over=False, why='no guide, consensus or named anchor for any forecast year: the screen does not apply')
    worst['over'] = worst['distance'] > PLAUSIBLE_X
    return worst


def _ctx(D, s):
    import model_engine as ME
    p = _path((D, s)); d = _j(p, {}) or {}
    RI = (d.get('model') or {}).get('reconciled_inputs')
    if not isinstance(RI, dict): sys.exit(f'{s} {D}: no model.reconciled_inputs in {p}')
    spot, beta = ME.prior_spot_beta(D, s)
    return p, d, RI, d.get('as_of') or D, spot, beta, ME.set_hash(p)


def size(D, s):
    p, d, RI, as_of, spot, beta, h = _ctx(D, s)
    bo = {}; base = _value(RI, as_of, s, spot, beta, bo); utc = dt.datetime.now(dt.timezone.utc).strftime('%Y-%m-%dT%H:%MZ'); n = 0; errs = []
    for it in d.get('judgment_alternatives') or []:
        A = anchors(d, bo, it)
        for kind in ('alternatives', 'rejected_alternatives'):  # rejected ones are sized too: the screen must confirm them
            for a in it.get(kind) or []:
                for k in ('research_target', 'fv_effect_pct', 'sized', 'size_error', 'plausibility'): a.pop(k, None)
                if not a.get('patch'):
                    a['size_error'] = 'no patch'
                    if kind == 'alternatives': errs.append(f"{it.get('item')}: {a.get('build', '')[:60]}: no patch")
                    continue
                t = copy.deepcopy(RI); o = {}
                try:
                    for pt in a['patch']: _apply(t, pt)
                    v = _value(t, as_of, s, spot, beta, o)
                except Exception as e:
                    a['size_error'] = str(e)[:300]; errs.append(f"{it.get('item')}: {a.get('build', '')[:60]}: {str(e)[:200]}"); continue
                a.update(research_target=round(v, 2), fv_effect_pct=round((v / base - 1) * 100, 2), plausibility=plausibility(o, bo, A),
                         sized=dict(by='judgment_check.py', set_hash=h, base_target=round(base, 2), utc=utc)); n += 1
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


def _small_bigger(D, s, d, RI, item, row):
    """small_flag.bigger for one card row (RULES.md 26): same-period alternatives of `item` moving >= 0.5%; [] when none or
    when the row names no period."""
    import model_engine as ME, small_flag as SF
    it = _items(d).get(str(item)) or {}; al = it.get('alternatives') or []
    if not al or SF.row_periods(RI, row) is None: return []
    spot, beta = ME.prior_spot_beta(D, s); ctx = (d.get('as_of') or D, s, spot, beta)
    return SF.bigger(RI, al, set(), row, ctx, None) or []


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
            pl = a.get('plausibility') or {}
            if pl.get('over'):
                V.append(dict(rule='implausible-listed', where=f'{k}: {b}', why=f"{pl['metric']} FY{pl['fy']} {pl['value']} sits {pl['distance']:.2f}x outside every anchor "
                              f"({', '.join(f'{n} {v}' for n, v in pl['anchors'])}), past the {PLAUSIBLE_X}x line (Kilian 2026-11-18): move it to "
                              f"rejected_alternatives with a `rejected` reason; it is never carded"))
                continue
            if abs(a['fv_effect_pct']) > CARD_PCT: over.setdefault(k, []).append(a)
        for a in it.get('rejected_alternatives') or []:  # rule 2: never cut an evidence-based alternative only because it is large
            b = (a.get('build') or '')[:70]; pl = a.get('plausibility') or {}; sz = a.get('sized') or {}
            if len((a.get('rejected') or '').strip()) < REJECT_MIN:
                V.append(dict(rule='rejected-no-reason', where=f'{k}: {b}', why=f'`rejected` missing or under {REJECT_MIN} chars: name the anchors, how far outside, and why the build does not fit'))
            if sz.get('by') != 'judgment_check.py' or sz.get('set_hash') != h:
                V.append(dict(rule='rejected-unscreened', where=f'{k}: {b}', why=f'not screened on this set (needs a patch): run python3 {HERE}/judgment_check.py size {D} {s}')); continue
            if not pl.get('over'):
                d_ = pl.get('distance')
                V.append(dict(rule='rejected-plausible', where=f'{k}: {b}', why=(f'{d_:.2f}x outside its anchors, inside the {PLAUSIBLE_X}x line' if d_ else pl.get('why', 'no outside anchor'))
                              + ': an evidence-based alternative cannot be rejected for its size (rule 2); list it under alternatives and size it'))
    cards = d.get('judgment_calls_for_kilian') or []
    for i, c in enumerate(cards):
        k = str(c.get('item') or ''); q = (c.get('question') or '')[:70]
        if k not in I: V.append(dict(rule='card-no-item', where=f'card {i}: {q}', why='card has no `item` matching a judgment_alternatives entry')); continue
        eff = [a['fv_effect_pct'] for a in I[k].get('alternatives') or [] if isinstance(a.get('fv_effect_pct'), (int, float))]
        fe = c.get('fv_effect_pct')
        if not isinstance(fe, (int, float)) or not any(abs(fe - e) <= MATCH_PCT for e in eff):
            V.append(dict(rule='card-not-sized', where=f'card {i}: {q}', why=f'fv_effect_pct {fe} is not one of the engine-sized effects for {k}: {eff}'))
    for i, c in enumerate(cards): V += card_problems(i, c, d)
    for i, c in enumerate(cards):  # RULES.md 26 (Kilian 2026-11-18 "Skip under 0.5%, note it"): no card for a tiny gate-flagged path
        row = next((v for k, v in c.items() if re.fullmatch(r'gate_r\d+_row', str(k))), None)
        fe = [o.get('fv_effect_pct') for o in c.get('options') or [] if isinstance(o, dict)]
        if row and fe and not str(c.get('status') or '').lower().startswith('decided') and all(isinstance(e, (int, float)) and abs(e) < SMALL_FLAG_PCT for e in fe):
            # 11-19 (AVGO "non_ai_rev FY28-FY29"): if an alternative for the row's own period moves >= 0.5% (small_flag.bigger, same
            # test the note check uses), the card is the right place, but it must carry that option; else it is a note, no card
            B = _small_bigger(D, s, d, RI, c.get('item'), row)
            if B:
                V.append(dict(rule='small-flag-card-missing-option', where=f"card {i}: {(c.get('question') or '')[:70]}",
                              why='options all move < 0.5%, but alternatives for this period move more: ' + '; '.join(f'alt {j} {why}' for j, e, k_, why in B)
                                  + ': add them to the card options (rule 2), or source the path'))
                continue
            V.append(dict(rule='small-flag-card', where=f"card {i}: {(c.get('question') or '')[:70]}",
                          why=f'every option moves the target < {SMALL_FLAG_PCT}% on a gate-flagged row ({str(row)[:80]}): run python3 {HERE}/small_flag.py '
                              f'suggest {D} {s}; fix the model if it does not feed forward, else note it (small_flag_notes + small_flag.py accept), no card'))
    carded = {str(c.get('item')) for c in cards}
    for k, al in over.items():
        if k not in carded:
            V.append(dict(rule='uncarded-over-5', where=k, why='; '.join(f"{a.get('build', '')[:60]} -> {a['fv_effect_pct']:+.1f}% ({a['research_target']})" for a in al) + ': card it in judgment_calls_for_kilian'))
    for w, t in _texts(d):
        m = LOOSE.search(t)
        if m: V.append(dict(rule='loose-claim', where=w, why=f'"{m.group(0)[:160]}": the claim must be about the sized evidence-based alternatives (use `judgment_check.py statement`)'))
    return V


SRC_IDS = re.compile(r'\bsrcs?\.?\s*#?((?:\d+\s*(?:[-\u2013]\s*\d+)?\s*(?:,\s*|and\s+)?)+)', re.I)  # as run_digest.SRC_REF


def _src_ids(r):
    """`src 13`, `src 1, 12-16`, `srcs 4 and 7` -> [13], [1, 12, 13, 14, 15, 16], [4, 7]."""
    out = []
    for g in SRC_IDS.findall(r):
        for a, b in re.findall(r'(\d+)(?:\s*[-\u2013]\s*(\d+))?', g):
            a = int(a); out += list(range(a, int(b) + 1)) if b and int(b) >= a else [a]
    return out


def _resolve(refs, d):
    """Each source behind an option: ('url', link), ('file', project-file path, no public link) or ('bad', why).
    `src N` is 1-based (src 1 = sources[0], as run_digest numbers them) and resolves to sources[N-1] ONLY. Before 2026-11-16 a
    src whose entry had no http URL fell through to sources[N] and returned the next source's link (SPCX 11-15: src 13, the
    uploaded Morgan Stanley digest, showed the Hund-Monk-Tice paper)."""
    S = d.get('sources') or []; out = []
    for r in refs if isinstance(refs, list) else [refs]:
        ids = [r] if isinstance(r, int) and not isinstance(r, bool) else None
        r = str(r or '')
        out += [('url', u) for u in re.findall(r'https?://\S+', r)] if ids is None else []
        for j in ids if ids is not None else _src_ids(r):
            x = S[j - 1] if 0 < j <= len(S) else None
            if not isinstance(x, dict): out.append(('bad', f'src {j}: no such entry in `sources` ({len(S)} listed)')); continue
            u = str(x.get('url') or x.get('path') or '').strip()
            if u.startswith('file://'): u = u[len('file://'):]
            if u.startswith(('http://', 'https://')): out.append(('url', u))
            elif u and not re.match(r'\w+://', u):
                fp = u if u.startswith('/') else '/mnt/project-files/' + u.lstrip('./')
                out.append(('file', f"{fp} (src {j}, {str(x.get('title') or 'project file')[:80]}: no public link)"))
            else: out.append(('bad', f"src {j} ({str(x.get('title') or '')[:60]}): no URL or project-file path in `sources`"))
    return out


def _urls(refs, d):
    """Links behind an option's `sources`: URLs as given, `src N` resolved to sources[N-1] only, and project-file paths
    (uploads with no public link) marked as such. Never a neighboring source's link."""
    return [v for k, v in _resolve(refs, d) if k != 'bad']


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
        R = _resolve(o.get('sources'), d)
        for k, why in R:
            if k == 'bad': V.append(dict(rule='card-option-bad-src', where=f'{w} option {j} {lb}', why=why + ': fix the id or the `sources` entry'))
        if not any(k != 'bad' for k, _ in R):
            V.append(dict(rule='card-option-no-link', where=f'{w} option {j} {lb}', why='no source link: give a URL, a src id that resolves to one in `sources`, or a project-file path for an upload'))
    return V


def cards(D, s, gate=True):
    """Markdown for each card, posted in the thread beside the decision card (the card itself holds one line per option).
    gate: also print the card gate (card_gate) to stderr first; main() exits 1 when it is not OK."""
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
    CARD_MDS[(D, s)] = list(out)
    if gate and out:
        ok, G = card_gate(D, s, list(out))
        CARDS_GATE[(D, s)] = ok
        print('\n'.join(G), file=sys.stderr)
    import forecast_lint  # Kilian 2026-11-17: any use of the guide (or guide x usual beat) is flagged to him with its explanation
    if forecast_lint.round_guide_uses(D, s): out.append(forecast_lint.guide_uses_md(D, s))
    return '\n\n'.join(out) or f'No cards for {s} {D}.'


CARDS_GATE, CARD_MDS = {}, {}


def _card_check():
    """The settings repo's card_check.py (the PreToolUse hook on request_decision), loaded by path."""
    import importlib.util
    for p in [os.environ.get('CARD_CHECK_PATH', ''), '/home/user/.claude/hooks/card_check.py',
              '/home/user/portfolio-management-settings/.claude/hooks/card_check.py']:
        if p and os.path.exists(p):
            sp = importlib.util.spec_from_file_location('card_check_hook', p); m = importlib.util.module_from_spec(sp); sp.loader.exec_module(m)
            return m
    return None


def card_gate(D, s, md=None):
    """(ok, lines): the same checks the card_check hook runs on a decision card, run here so a thread sees them before it posts
    (Kilian 2026-11-18, NVDA: cards went out before the gate checked the new rows and without their reasoning laid out, in a
    session whose hooks never loaded). Gate status of runs/D/SYM, and the form of each card's message (context, a section per
    option with reasoning, a link and a sized number)."""
    cc = _card_check()
    if cc is None: return False, ['CARD GATE: card_check.py not found (settings repo .claude/hooks): do not post cards until it is']
    st, det = cc.gate_status(('run', D, s))
    L = [f'CARD GATE: {st.upper()}: {det}']
    ok = st == 'clean'
    if not ok: L.append('  -> do NOT post these cards: the citation gate has not cleared this round (RULES.md 2, card_check.py gate rule)')
    d = _j(_path((D, s)), {}) or {}
    for i, c in enumerate(d.get('judgment_calls_for_kilian') or []):
        labels = [o.get('label', '') for o in c.get('options') or [] if isinstance(o, dict)]
        P = cc.problems(md[i] if md and i < len(md) else '', labels, valuation=True) if labels else ['no option labels']
        if P:
            ok = False; L.append(f'CARD {i + 1} FORM: ' + '; '.join(P))
    if ok: L.append(f'CARD GATE OK: post each card\'s message below in the thread FIRST, then the card in a later step (labels exactly as written)')
    return ok, L


def statement(D, s):
    d = _j(_path((D, s)), {}) or {}; I = _items(d)
    al = [(k, a) for k, it in I.items() for a in it.get('alternatives') or [] if isinstance(a.get('fv_effect_pct'), (int, float))]
    if not al: return f'No evidence-based alternatives sized for {s} {D}.'
    k, a = max(al, key=lambda x: abs(x[1]['fv_effect_pct']))
    big = [x for x in al if abs(x[1]['fv_effect_pct']) > CARD_PCT]
    head = (f"{len(big)} evidence-based alternative(s) move the target >5%, all carded" if big else
            "None of the evidence-based alternatives moves the target >5%")
    import forecast_lint
    G = forecast_lint.round_guide_uses(D, s)
    gu = (f" Guide used as a last resort on {len(G)} item(s), flagged with why nothing better exists: "
          + '; '.join(g['where'] for g in G) + '.') if G else ''
    return (f"{head} ({len(al)} alternatives sized by the engine on {len(I)} judgment items; largest {a['fv_effect_pct']:+.1f}% "
            f"on {k}: {a.get('build', '')[:90]}).") + gu


def screen(D, s, patch=None, item=None):
    """The plausibility screen on its own. With patch (a JSON list of {path, value|scale|add}): one candidate build, e.g. a
    gate checker's missing_alternative before it is listed. Without: every listed and rejected alternative of the set."""
    p, d, RI, as_of, spot, beta, h = _ctx(D, s)
    bo = {}; base = _value(RI, as_of, s, spot, beta, bo); I = _items(d)
    def one(pts, it):
        t = copy.deepcopy(RI); o = {}
        for pt in pts: _apply(t, pt)
        v = _value(t, as_of, s, spot, beta, o); pl = plausibility(o, bo, anchors(d, bo, it))
        return dict(research_target=round(v, 2), fv_effect_pct=round((v / base - 1) * 100, 2), plausibility=pl,
                    verdict=('IMPLAUSIBLE: record under rejected_alternatives with a reason, do not card' if pl.get('over') else
                             'PLAUSIBLE: an evidence-based alternative; list and size it (card if over 5%)'))
    if patch is not None:
        pts = json.loads(patch); pts = pts if isinstance(pts, list) else [pts]
        return dict(symbol=s, round=D, line=PLAUSIBLE_X, base_target=round(base, 2), **one(pts, I.get(item) if item else None))
    out = []
    for k, it in I.items():
        for kind in ('alternatives', 'rejected_alternatives'):
            for a in it.get(kind) or []:
                if not a.get('patch'): continue
                try: r = one(a['patch'], it)
                except Exception as e: r = dict(error=str(e)[:200])
                out.append(dict(item=k, list=kind, build=(a.get('build') or '')[:100], **r))
    return dict(symbol=s, round=D, line=PLAUSIBLE_X, base_target=round(base, 2), alternatives=out)


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
    elif a[0] == 'screen':
        it = a[a.index('--item') + 1] if '--item' in a else None; rest = [x for x in a[3:] if x not in ('--item', it)]
        r = screen(D, s, rest[0] if rest else None, it); print(json.dumps(r, indent=1))
    elif a[0] == 'cards':
        print(cards(D, s)); sys.exit(0 if CARDS_GATE.get((D, s), True) else 1)
    elif a[0] == 'card-gate':
        cards(D, s, gate=False); ok, G = card_gate(D, s, CARD_MDS.get((D, s))); print('\n'.join(G)); sys.exit(0 if ok else 1)
    else: print(__doc__); sys.exit(1)
