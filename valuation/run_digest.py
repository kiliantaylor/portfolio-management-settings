"""Compact views of a research round, so the supervisor, the revision round and the citation gate stop re-reading whole
files every turn (token cut, Kilian 2026-10-13). Nothing here changes a number: it only selects and formats.

Usage:
  python3 run_digest.py sup DATE SYM         -> runs/DATE/SYM/digest.md (all agents side by side, for the supervisor)
  python3 run_digest.py revise DATE SYM      -> runs/DATE/SYM/round1/feedback_agent_N.json (each agent's slice of the
                                                round-1 supervisor file; revise_briefs.py calls this)
  python3 run_digest.py gate DATE SYM        -> runs/DATE/SYM/gate/: checklist.json (every cited figure, deduped and
                                                grouped by source URL), auto.json (figures checked by script against the
                                                SEC fact pack), brief_shard_N.md (one per ~18 URLs) and brief_text.md
  python3 run_digest.py gate-update DATE SYM -> the same for a name_update.py round: the reconciled set's changed rows only
  python3 run_digest.py gate-merge DATE SYM  -> merges gate/shard_*.json; writes citation_audit.json when nothing failed,
                                                else gate/brief_fix.md for one fix agent"""
import glob, json, os, re, sys, collections, urllib.parse
HERE = os.path.dirname(os.path.abspath(__file__))
RUNS = os.environ.get('VAL_RUN_ROOT', f'{HERE}/runs')  # a scratch copy can be tested without touching the real run folder
J = lambda p: json.load(open(p))

def agents(rd):
    return {os.path.basename(f)[:-5]: J(f) for f in sorted(glob.glob(f'{rd}/agent_*.json'))}

def short(t, n=300):
    t = re.sub(r'\s+', ' ', str(t or '')).strip()
    return t if len(t) <= n else t[:n - 1] + '…'

def dom(u):
    try: return urllib.parse.urlparse(u).netloc.replace('www.', '')
    except Exception: return ''

def ladder_row(l): return ' '.join(f"{x['K']:g}:{x['p']:.2f}" for x in l or [])

def guidance_rows(gc):  # Kilian 2026-10-21 18:38Z: guidance is one checked input (agents/model_inputs.md guidance_check)
    out = []
    for g in gc or []:
        tr = g.get('track_record') or {}
        out.append(f"Guidance {short(g.get('metric'), 40)} {g.get('fy')}: guide {g.get('guide_low')}-{g.get('guide_high')} ({short(g.get('guide_basis'), 40)}, {g.get('guide_as_of')}) src {g.get('guide_source_ids')}; "
                   f"record {tr.get('beat')} beat / {tr.get('inside')} inside / {tr.get('miss')} miss over {len(tr.get('periods') or [])} {tr.get('granularity')} periods, median vs mid {tr.get('median_vs_mid_pct')}; "
                   f"record-adjusted {g.get('record_adjusted_guide')}; consensus {g.get('consensus')}; drivers {g.get('drivers_imply')}; "
                   f"ours {g.get('our_value')} = {g.get('position')} ({g.get('gap_to_mid_pct')} vs mid): {short(g.get('why'), 300)}")
    return out

# ---------------------------------------------------------------- supervisor digest
def sup_digest(date, sym):
    rd = f'{RUNS}/{date}/{sym}'; A = agents(rd)
    L = [f'# {sym} research digest ({date}), {len(A)} forecasters',
         'Built by run_digest.py from the agent files (nothing summarised away from the numbers: every method, input,',
         'comp and source is listed). Open an agent file only for a specific passage, and print just that part',
         "(e.g. python3 -c \"import json;print(json.load(open('agent_2.json'))['comps'][3])\"), never the whole file.", '']
    ov = f'{rd}/source_overlap.json'
    if os.path.exists(ov):
        o = J(ov); fin = o.get('final', o)
        L.append('Source overlap: ' + short(json.dumps({k: v for k, v in fin.items() if k != 'agents'}), 600))
        L.append('')
    fvs = {a: d.get('fair_value', {}).get('value') for a, d in A.items()}
    L += ['| agent | lane | research target (12m) | downside (P10) | base | upside (P90) | thesis break | SBC basis |', '|---|---|---|---|---|---|---|---|']
    for a, d in A.items():
        sc = d.get('scenarios', {})
        L.append(f"| {a} | {d.get('lane')} | {fvs[a]} | " + ' | '.join(
            str((sc.get(k) or sc.get(k2) or {}).get('price')) for k, k2 in (('downside', 'bear'), ('base', 'base'), ('upside', 'bull')))
            + f" | {d.get('thesis_break_price')} | {short((d.get('sbc_treatment') or {}).get('basis'), 40)} |")
    for a, d in A.items():
        srcs = d.get('sources', [])
        L += ['', f'## {a} ({d.get("lane")})', f"Research target (12m) {fvs[a]}: {short(d.get('fair_value', {}).get('method'), 400)}"]
        for m in d.get('fair_value', {}).get('methods', []):
            L.append(f"- **{m.get('name')}** {m.get('value')} x weight {m.get('weight')}: {short(m.get('inputs'), 400)}")
            for i in m.get('inputs_detail', []):
                L.append(f"  - {short(i.get('input'), 80)} = {short(i.get('value'), 80)} [{i.get('basis')}] src {i.get('source_ids')}"
                         + (f" facts {i['fact_ids']}" if i.get('fact_ids') else '')
                         + (f" | {short(i.get('derivation'), 220)}" if i.get('derivation') else ''))
        cs = d.get('comps_summary') or {}
        L.append(f"Comps ({len(d.get('comps', []))}): median fwd P/E {cs.get('peer_median_fwd_pe')}, EV/EBITDA {cs.get('peer_median_ev_ebitda')}, "
                 f"range {short(cs.get('peer_range'), 80)}; ours: {short(cs.get('our_multiple_vs_peers'), 200)}; "
                 f"why: {short(cs.get('premium_discount_justification'), 300)}")
        L.append('  ' + '; '.join(f"{c.get('symbol')} {c.get('fwd_pe')}/{c.get('ev_ebitda_fwd')} {short(c.get('growth_next_yr'), 20)} s{c.get('source_ids')}"
                                  for c in d.get('comps', [])))
        for r in d.get('long_dated_risks', []):
            L.append(f"Long-dated: {short(r.get('risk'), 120)} p={r.get('probability')} when {r.get('when')} effect {r.get('value_effect_per_share')}/sh via {r.get('how_applied')}")
        mi = d.get('model_inputs')
        if mi:
            ys = mi.get('years', [])
            L.append(f"Model set: base {mi.get('base')}; years " + '; '.join(
                f"{y.get('label')} g{y.get('revenue_growth')} m{y.get('ebit_margin')}" for y in ys)
                + f"; terminal {mi.get('terminal')}; WACC {(mi.get('discount_rate') or {}).get('wacc')}; net debt {(mi.get('balance') or {}).get('net_debt')}"
                + f"; shares {mi.get('shares')}; multiple {mi.get('horizon_multiple')}; segments {len(mi.get('segments') or [])}, "
                f"adjustments {len(mi.get('adjustments') or [])}, custom {[c.get('name') for c in mi.get('custom') or []]}; "
                f"notes {short(mi.get('structure_note'), 120)} {short(mi.get('gap_note'), 120)} (python3 model_engine.py run values every set)")
        L += guidance_rows((mi or {}).get('guidance_check')) or ([f'Guidance check: MISSING (agent gave no guidance_check)'] if mi else [])
        L.append(f"Reverse DCF: {short(d.get('reverse_dcf_check'), 300)}")
        L.append(f"Thesis break {d.get('thesis_break_price')}: {short(d.get('thesis_break_reason'), 250)}")
        L.append('Uncertainties: ' + '; '.join(short(u, 120) for u in d.get('key_uncertainties', [])))
        L.append('Catalysts: ' + '; '.join(f"{c.get('date')} {short(c.get('event'), 60)} {c.get('direction')}" for c in d.get('catalysts', [])))
        L.append(f"Reasoning: {short(d.get('reasoning'), 2500)}")
        L.append(f"1M: {short(d.get('one_month_reasoning'), 300)}")
        L.append(f"Ladder: {ladder_row(d.get('ladder'))}")
        L.append(f"Ladder 1M: {ladder_row(d.get('ladder_1m'))}")
        for r in d.get('revisions', []):
            L.append(f"Revision: {short(r.get('item'), 60)} {short(r.get('from'), 40)} -> {short(r.get('to'), 40)}: {short(r.get('why'), 160)}")
        L.append('Sources: ' + ' | '.join(f"{i}. {short(s.get('title'), 70)} ({dom(s.get('url'))}, {s.get('date')}, {s.get('type')}{'' if s.get('in_lane', True) else ', off-lane'}) {s.get('url')}"
                                         for i, s in enumerate(srcs, 1)))
    out = f'{rd}/digest.md'; open(out, 'w').write('\n'.join(L) + '\n')
    print(sym, 'digest', len('\n'.join(L)) // 1000, 'k chars vs agent files',
          sum(os.path.getsize(f) for f in glob.glob(f'{rd}/agent_*.json')) // 1000, 'k ->', out)

# ---------------------------------------------------------------- revision slices
def revise_slices(date, sym):
    rd = f'{RUNS}/{date}/{sym}'; r1 = f'{rd}/round1'
    S = J(f'{r1}/supervisor.json')
    for f in sorted(glob.glob(f'{r1}/agent_*.json')):
        a = os.path.basename(f)[:-5]
        sr = S.get('source_review', {})
        sl = dict(agent=a, note='Your slice of the round-1 supervisor file (run_digest.py); the full file is supervisor.json beside it.',
                  feedback=[x for x in S.get('feedback_for_agents', []) if x.get('agent') == a],
                  summary=S.get('summary'), key_drivers=S.get('key_drivers'), fair_value=S.get('fair_value'),
                  thesis_break=dict(price=S.get('thesis_break_price'), reason=S.get('thesis_break_reason')),
                  disagreements=S.get('disagreements'), changes=S.get('changes'), comps_review=S.get('comps_review'),
                  long_dated_review=S.get('long_dated_review'),
                  source_review=dict(quality=[q for q in sr.get('quality', []) if q.get('agent') == a],
                                     repeated_claims=[q for q in sr.get('repeated_claims', []) if a in (q.get('agents') or [])],
                                     discounted=sr.get('discounted')),
                  supervisor_sources=S.get('sources'))
        json.dump(sl, open(f'{r1}/feedback_{a}.json', 'w'), indent=1)
    print(sym, 'revision slices written')

# ---------------------------------------------------------------- citation gate
NUM = re.compile(r'-?\d[\d,]*\.?\d*')
def nums(t):
    out = []
    for m in NUM.findall(str(t)):
        try: out.append(float(m.replace(',', '')))
        except ValueError: pass
    return out

def matches_fact(text, fact):
    v = fact.get('value')
    if v is None: return False
    for x in nums(text):
        for scale in (1, 1e3, 1e6, 1e9):
            if v and abs(x * scale - v) <= 0.005 * abs(v): return True
    return False

def gate_prep(date, sym, per_shard=10, update=False):
    """update=True (gate-update, name_update.py 2026-10-21): only the supervisor's reconciled set, and in update mode only the rows
    named in update.json changed_inputs plus the whole guidance_check; the base round's clean audit covers the rest."""
    rd = f'{RUNS}/{date}/{sym}'; gd = f'{rd}/gate'; os.makedirs(gd, exist_ok=True)
    A = {} if update else agents(rd); facts = {}
    U = J(f'{rd}/update.json') if update else {}
    keep = None if not update or U.get('mode') == 'build' or '*' in (U.get('changed_inputs') or []) else set(U.get('changed_inputs') or [])
    if update and U.get('xbrl_actuals'):  # Kilian card 2026-10-28 "Script checks XBRL rows": rows the script verifies skip the agents
        try:
            import xbrl_actuals; xc = xbrl_actuals.verify(date, sym)
        except Exception as e: xc = dict(passed=[], to_agents=[], error=str(e)[:200])
        json.dump(xc, open(f'{gd}/xbrl_check.json', 'w'), indent=1)
        if keep is not None: keep -= {r['input'] for r in xc['passed']}
        print(sym, f"XBRL rows checked by script: {len(xc['passed'])} passed, {len(xc['to_agents'])} to agents")
    if os.path.exists(f'{rd}/facts.json'):
        facts = {f['id']: f for f in J(f'{rd}/facts.json')['facts']}
    if os.path.exists(f'{rd}/peers.json'):  # peers.py table (2026-10-14): ids like FAST.ev_ebitda_ntm, checked by script
        for r in J(f'{rd}/peers.json').get('peers', []):
            for f in r.get('facts', []): facts[f['id']] = f
    items = []  # every structured cited figure
    def add(origin, item, value, basis, urls, fact_ids=None, derivation=''):
        items.append(dict(origin=origin, item=short(item, 160), value=short(value, 200), basis=basis or '',
                          urls=[u for u in urls if u], fact_ids=fact_ids or [], derivation=short(derivation, 1500)))
    def guidance_items(origin, gc, u):  # Kilian 2026-10-21 18:38Z: guides, past guides vs actuals and consensus are cited figures
        for g in gc or []:
            m = f"{g.get('metric')} {g.get('fy')}"
            if g.get('guide_low') is not None or g.get('guide_high') is not None:
                add(origin, f"{m} guide as of {g.get('guide_as_of')} ({g.get('guide_basis')})", f"{g.get('guide_low')}-{g.get('guide_high')}",
                    'guidance', u(g.get('guide_source_ids')), g.get('guide_fact_ids'))
            tr = g.get('track_record') or {}
            for pr in tr.get('periods') or []:
                add(origin, f"{g.get('metric')} {pr.get('period')}: first guide ({pr.get('first_guide_date')}) / reported actual",
                    f"{pr.get('first_guide_low')}-{pr.get('first_guide_high')} / {pr.get('actual')}", 'guidance', u(pr.get('source_ids')), pr.get('fact_ids'))
            if g.get('consensus') is not None:
                add(origin, f"{m} consensus as of {g.get('consensus_as_of')}", g.get('consensus'), 'consensus', u(g.get('consensus_source_ids')))
            per = '; '.join(f"{pr.get('period')} {pr.get('first_guide_low')}-{pr.get('first_guide_high')} -> {pr.get('actual')} ({pr.get('actual_vs_mid_pct')})"
                            for pr in tr.get('periods') or [])
            add(origin, f"{m} guidance arithmetic: actual vs midpoint, record counts, record-adjusted guide, gap to midpoint, position",
                f"{tr.get('beat')}/{tr.get('inside')}/{tr.get('miss')} beat/inside/miss, median {tr.get('median_vs_mid_pct')}, adjusted {g.get('record_adjusted_guide')}, "
                f"ours {g.get('our_value')} {g.get('position')} {g.get('gap_to_mid_pct')}", 'estimate', [],
                derivation=f"periods: {per}; guide {g.get('guide_low')}-{g.get('guide_high')}; actual_vs_mid = actual / midpoint - 1; "
                           f"beat > high, inside within, miss < low; adjusted = midpoint x (1 + median); gap = ours / midpoint - 1; position vs the range")
    for a, d in A.items():
        src = {i: s.get('url') for i, s in enumerate(d.get('sources', []), 1)}
        u = lambda ids: [src.get(i) for i in (ids or []) if isinstance(i, int)]
        for m in d.get('fair_value', {}).get('methods', []):
            for i in m.get('inputs_detail', []):
                add(f'{a} {m.get("name")}', i.get('input'), i.get('value'), i.get('basis'), u(i.get('source_ids')), i.get('fact_ids'), i.get('derivation'))
        for c in d.get('comps', []):
            add(f'{a} comps', f"{c.get('symbol')} fwd P/E / fwd EV/EBITDA / growth", f"{c.get('fwd_pe')} / {c.get('ev_ebitda_fwd')} / {c.get('growth_next_yr')}",
                c.get('basis') or 'reported', u(c.get('source_ids')), c.get('fact_ids'))
            items[-1]['comp_fields'] = [c.get('fwd_pe'), c.get('ev_ebitda_fwd'), c.get('growth_next_yr')]
        for i in (d.get('model_inputs') or {}).get('inputs_detail', []):
            add(f'{a} model_inputs', i.get('input'), i.get('value'), i.get('basis'), u(i.get('source_ids')), i.get('fact_ids'), i.get('derivation'))
        guidance_items(f'{a} guidance', (d.get('model_inputs') or {}).get('guidance_check'), u)
        sb = d.get('sbc_treatment') or {}
        if sb.get('sbc_last_fy_musd'): add(f'{a} sbc', 'SBC last fiscal year ($M)', sb.get('sbc_last_fy_musd'), 'reported', u(sb.get('sources')), sb.get('fact_ids'))
    if os.path.exists(f'{rd}/supervisor.json'):
        S = J(f'{rd}/supervisor.json'); ssrc = {i: x.get('url') for i, x in enumerate(S.get('sources', []), 1)}
        guidance_items('supervisor guidance', ((S.get('model') or {}).get('reconciled_inputs') or {}).get('guidance_check'),
                       lambda ids: [ssrc.get(k) for k in (ids or []) if isinstance(k, int)])
        for i in ((S.get('model') or {}).get('reconciled_inputs') or {}).get('inputs_detail', []):
            if keep is not None and i.get('input') not in keep: continue
            add('supervisor model', i.get('input'), i.get('value'), i.get('basis'),
                [ssrc.get(k) for k in (i.get('source_ids') or []) if isinstance(k, int)], i.get('fact_ids'), i.get('derivation'))
    if os.path.exists(f'{rd}/model_assumptions.json') and not update:
        M = J(f'{rd}/model_assumptions.json')
        for x in M.get('assumptions', []):
            rc = x.get('reconciled') or {}
            add(f"model {x.get('id')} reconciled", x.get('name'), f"{rc.get('value')} {x.get('unit', '')}", 'judgment', rc.get('source_urls') or [],
                derivation=f"supervisor: {rc.get('reasoning')} | sources {', '.join(rc.get('source_urls') or [])}")
            for a, v in (x.get('agents') or {}).items():
                for s in v.get('added_sources') or []:
                    add(f"model {x.get('id')} {a} (supervisor-added source)", x.get('name'), f"{v.get('value')} {x.get('unit', '')}", v.get('basis'), [s.get('url')])
    auto, todo, arith = [], [], []
    for it in items:
        cf = it.pop('comp_fields', None)
        if cf is not None and it['fact_ids'] and all(f in facts for f in it['fact_ids']):
            # a comps row: every figure it gives must match one of its cited table/fact ids (growth may be % or fraction)
            def fits(x):
                if x in (None, '', 'n/a'): return True
                xs = nums(x)
                def near(v, fct):  # table values are unrounded; agents round to 0.1x or 0.1pp
                    t = fct.get('value')
                    if t is None: return False
                    if fct.get('unit') == 'fraction': return abs(v - t) <= 0.0006 or abs(v / 100 - t) <= 0.0006
                    return abs(v - t) <= max(0.01 * abs(t), 0.051)
                return bool(xs) and any(near(v, facts[f]) for v in xs for f in it['fact_ids'])
            if all(fits(x) for x in cf):
                it.update(result='ok', checked_by='peers.py / secfacts.py table (script)', source_url=facts[it['fact_ids'][0]].get('source') or facts[it['fact_ids'][0]].get('url'))
                auto.append(it); continue
        if cf is not None:  # comps not fully covered by table ids: a checker opens the cited page whatever the basis
            (todo if it['urls'] else arith).append(it); continue
        if it['fact_ids'] and all(f in facts for f in it['fact_ids']) and all(matches_fact(it['value'], facts[f]) for f in it['fact_ids']):
            it.update(result='ok', checked_by='secfacts.py (SEC XBRL companyfacts)', source_url=facts[it['fact_ids'][0]].get('url'))
            auto.append(it); continue
        if it['basis'] in ('estimate', 'judgment') and not it['urls']:
            arith.append(it); continue
        if it['basis'] in ('estimate', 'judgment'):
            arith.append(it)  # arithmetic/label check; its cited facts are checked where they appear as inputs
            continue
        todo.append(it)
    # dedupe by (url set, value), group by first URL
    seen = set(); groups = collections.OrderedDict()
    for it in todo:
        key = (tuple(sorted(it['urls'])), it['value'].lower())
        if key in seen: continue
        seen.add(key)
        groups.setdefault(it['urls'][0] if it['urls'] else '(no source cited)', []).append(it)
    urls = list(groups)
    shards = [urls[i:i + per_shard] for i in range(0, len(urls), per_shard)]
    json.dump(dict(items=len(items), auto=len(auto), arithmetic=len(arith), to_open=sum(len(v) for v in groups.values()),
                   urls=len(urls), shards=len(shards), groups=groups, arith=arith), open(f'{gd}/checklist.json', 'w'), indent=1)
    json.dump(auto, open(f'{gd}/auto.json', 'w'), indent=1)
    tpl = open(f'{HERE}/agents/gate_shard.md').read().split('\n---\n', 1)[1]
    for n, us in enumerate(shards, 1):
        block = []
        for u in us:
            block.append(f'### {u}')
            block += [f"- [{it['origin']}] {it['item']} = {it['value']} ({it['basis']})" for it in groups[u]]
        b = tpl.replace('{{SYMBOL}}', sym).replace('{{DATE}}', date).replace('{{DIR}}', rd).replace('{{VAL_DIR}}', HERE).replace('{{N}}', str(n)) \
               .replace('{{OUT}}', f'{gd}/shard_{n}.json').replace('{{CHECKLIST}}', '\n'.join(block))
        open(f'{gd}/brief_shard_{n}.md', 'w').write(b)
    t = open(f'{HERE}/agents/gate_text.md').read().split('\n---\n', 1)[1]
    ar = '\n'.join(f"- [{it['origin']}] {it['item']} = {it['value']} ({it['basis']}): {it['derivation'] or 'NO DERIVATION GIVEN'}" for it in arith)
    open(f'{gd}/brief_text.md', 'w').write(t.replace('{{SYMBOL}}', sym).replace('{{DATE}}', date).replace('{{DIR}}', rd).replace('{{VAL_DIR}}', HERE)
                                          .replace('{{OUT}}', f'{gd}/shard_text.json').replace('{{ARITH}}', ar or '(none)'))
    print(sym, f'{len(items)} cited figures: {len(auto)} checked by script, {len(arith)} arithmetic/label, '
               f'{sum(len(v) for v in groups.values())} to open across {len(urls)} URLs -> {len(shards)} shard briefs + brief_text.md in {gd}')

def gate_merge(date, sym):
    rd = f'{RUNS}/{date}/{sym}'; gd = f'{rd}/gate'
    res = J(f'{gd}/auto.json'); missing = []
    C = J(f'{gd}/checklist.json')
    for n in range(1, C['shards'] + 1):
        p = f'{gd}/shard_{n}.json'
        if os.path.exists(p): res += J(p).get('results', [])
        else: missing.append(p)
    p = f'{gd}/shard_text.json'
    if os.path.exists(p): res += J(p).get('results', [])
    else: missing.append(p)
    if missing: print('missing shard files:', missing); sys.exit(1)
    if os.path.exists(f'{gd}/xbrl_check.json'):  # script-verified XBRL actuals rows (gate_prep, update rounds)
        res += [dict(origin='xbrl_actuals.py verify', item=r['input'], value=r['value'], basis='filing', source_url=(r['filings'] or [''])[-1],
                     result='ok', note=r['note']) for r in J(f'{gd}/xbrl_check.json').get('passed', [])]
    fails = [r for r in res if r.get('result') not in ('ok',)]
    ok = [r for r in res if r.get('result') == 'ok']
    json.dump(dict(results=res, failures=fails), open(f'{gd}/merged.json', 'w'), indent=1)
    if not fails:
        audit = dict(date=date, symbol=sym, clean=True, checked=len(res), ok=len(ok), failures=[], open_failures=[],
                     method='split gate (run_digest.py gate): script check against SEC XBRL + shard agents by source URL',
                     verified=[dict(item=r.get('item'), value=r.get('value'), basis=r.get('basis'), source_url=r.get('source_url'),
                                    checked=date, feeds=r.get('origin')) for r in ok])
        if os.path.exists(f'{rd}/update.json'):  # name_update.py round: this audit covers the changed rows; the base round's covers the rest
            u = J(f'{rd}/update.json')
            audit.update(scope=f"model update ({u.get('mode')}): changed rows of the reconciled set and its guidance_check",
                         base_audit=f"runs/{u.get('base')}/{sym}/citation_audit.json")
        json.dump(audit, open(f'{rd}/citation_audit.json', 'w'), indent=1)
        print(sym, f'clean: {len(res)} checked, all ok -> citation_audit.json')
    else:
        t = open(f'{HERE}/agents/gate_fix.md').read().split('\n---\n', 1)[1]
        open(f'{gd}/brief_fix.md', 'w').write(t.replace('{{SYMBOL}}', sym).replace('{{DATE}}', date).replace('{{DIR}}', rd).replace('{{VAL_DIR}}', HERE)
                                             .replace('{{N_FAIL}}', str(len(fails))).replace('{{N_ALL}}', str(len(res))))
        print(sym, f'{len(fails)} of {len(res)} failed -> run one agent on {gd}/brief_fix.md')

if __name__ == '__main__':
    cmd, date, syms = sys.argv[1], sys.argv[2], [s.upper() for s in sys.argv[3:]]
    for s in syms:
        if cmd == 'gate-update': gate_prep(date, s, update=True); continue
        {'sup': sup_digest, 'revise': revise_slices, 'gate': gate_prep, 'gate-merge': gate_merge}[cmd](date, s)
