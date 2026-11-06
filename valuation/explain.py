"""How each forecaster reached its valuation and what the supervisor changed, for the report (Kilian reviews these).

Usage: python3 explain.py DATE [SYM ...]   (default: every runs/DATE/<SYM>/result.json)
Reads the agent_*.json and supervisor.json of the research the result used (result.json research_as_of, else DATE) and
writes runs/DATE/SYM/explain.json:
  agents[]   : research target (12m), its methods (structured `fair_value.methods` when the agent gave them, else tags found in its
               method text, e.g. SOTP / DCF / P/E), scenarios, P(close >= spot), ladder median, thesis break, reasoning
  supervisor : confidence, whether its ladder replaced the mean, its `changes` list (or, for older runs, changes
               derived from its ladder vs the forecasters' mean and its thesis break vs theirs), disagreements settled
Speed (2026-10-21, same output): each agent file is read once (the supervisor comparison re-read every agent file at every
strike), and the command line explains 12 names at a time in threads, printing in the original order; on the shared
network folder each file lookup costs ~0.04s.
"""
import glob, json, math, os, re, sys
HERE = os.path.dirname(os.path.abspath(__file__))
TAGS = [('SOTP', r'sum[- ]of[- ](the[- ])?parts|\bSOTP\b'), ('DCF', r'\bDCF\b|discounted cash'),
        ('P/E', r'\bP/?E\b|forward multiple|earnings multiple|EPS[^;]{0,40}?\d\s*(x|×|times)\b|\d\s*x\s*(\d{4}\s*|NTM\s*|forward\s*|fwd\s*)*EPS'),
        ('EV/EBITDA', r'EV/?EBITDA|x\s*(\d{4}\s*|NTM\s*)*EBITDA'),
        ('EV/sales', r'EV/?(NTM\s*)?(sales|revenue)|\bP/S\b|x\s*(NTM\s*|\d{4}\s*)*(sales|revenue)|x an? ~?\$[\d.]+[BM] revenue'),
        ('FCF', r'\bFCF\b|free cash flow'), ('Merger terms', r'merger|deal terms|per current share|deal closes'),
        ('Scenario-weighted', r'probability[- ]weighted|scenario[- ]weighted')]

def median_from_ladder(lad):
    """K where P(close >= K) crosses 0.5, log-interpolated."""
    for a, b in zip(lad, lad[1:]):
        if a['p'] >= 0.5 >= b['p'] and a['p'] != b['p']:
            w = (a['p'] - 0.5) / (a['p'] - b['p']); return math.exp(math.log(a['K']) + w*(math.log(b['K']) - math.log(a['K'])))
    return None

def p_at(lad, S):
    for a, b in zip(lad, lad[1:]):
        if a['K'] <= S <= b['K']:
            w = (math.log(S) - math.log(a['K'])) / (math.log(b['K']) - math.log(a['K'])); return a['p'] + w*(b['p'] - a['p'])
    return None

def explain(date, s):
    res = json.load(open(os.path.join(HERE, 'runs', date, s, 'result.json'))); S = res['spot']
    rd = os.path.join(HERE, 'runs', res.get('research_as_of') or date, s)
    agents = []; loaded = {}
    _mec = {}
    def _me():  # model_engine.json of the research round (per-set medians), read once
        if 'v' not in _mec:
            try: _mec['v'] = json.load(open(f'{rd}/model_engine.json'))
            except Exception: _mec['v'] = {}
        return _mec['v']
    for p in sorted(glob.glob(f'{rd}/agent_*.json')):
        a = loaded[p] = json.load(open(p)); fv = a.get('fair_value', {}); txt = fv.get('method', '')
        methods = fv.get('methods') or [{'name': n, 'value': None, 'weight': None, 'inputs': None}
                                         for n, rx in TAGS if re.search(rx, txt, re.I)] or [{'name': 'other', 'value': None, 'weight': None, 'inputs': None}]
        lad = sorted(a.get('ladder') or [], key=lambda l: l['K'])  # 2026-10-30: no 12-month ladder from new rounds; median from the agent's model set
        _ms = ((_me().get('sets') or {}).get(os.path.basename(p)[:-5]) or {}) if not lad else {}
        agents.append(dict(agent=a.get('agent_id', os.path.basename(p)[:-5]), fair_value=fv.get('value'), method_text=txt,
                           methods=methods, structured=bool(fv.get('methods')),
                           scenarios={k: {'price': v.get('price'), 'prob': v.get('prob')} for k, v in a.get('scenarios', {}).items()},
                           p_gain=round(p_at(lad, S), 3) if lad else None,
                           median=round(median_from_ladder(lad), 2) if lad else (round(_ms['median'], 2) if isinstance(_ms.get('median'), (int, float)) else None),
                           thesis_break=a.get('thesis_break_price'), reasoning=a.get('reasoning')))
    out_r1 = None; r1 = f'{rd}/round1'  # revision round: round-1 files, the round-1 supervisor's feedback, and what each agent revised
    if os.path.isdir(r1):
        for a in agents:
            p1 = f"{r1}/{a['agent']}.json"; a2 = loaded.get(f"{rd}/{a['agent']}.json") or json.load(open(f"{rd}/{a['agent']}.json"))
            a['revisions'] = a2.get('revisions')
            if os.path.exists(p1):
                o = json.load(open(p1)); l1 = sorted(o.get('ladder') or [], key=lambda l: l['K'])
                a['round1'] = dict(fair_value=o.get('fair_value', {}).get('value'), p_gain=round(p_at(l1, S), 3) if l1 else None,
                                   median=round(median_from_ladder(l1), 2) if l1 else None, thesis_break=o.get('thesis_break_price'))
        if os.path.exists(f'{r1}/supervisor.json'):
            s1 = json.load(open(f'{r1}/supervisor.json'))
            out_r1 = dict(summary=s1.get('summary'), feedback=s1.get('feedback_for_agents'),
                          disagreements=[{k: d.get(k) for k in ('topic', 'resolution')} for d in s1.get('disagreements', [])])
    sup_p = f'{rd}/supervisor.json'; sup = json.load(open(sup_p)) if os.path.exists(sup_p) else None
    out = dict(symbol=s, as_of=date, research_as_of=os.path.basename(os.path.dirname(rd + '/')), spot=S, agents=agents, supervisor=None)
    if out_r1: out['supervisor_round1'] = out_r1
    if sup and not (sup.get('ladder') and all(loaded[p].get('ladder') for p in loaded)):  # 2026-10-30: ladderless round
        out['supervisor'] = dict(confidence=sup.get('confidence'), used=res.get('supervisor_used'),
                                 confidence_reason=sup.get('confidence_reason'), changes=sup.get('changes') or [], changes_derived=False,
                                 median_mean=None, median_supervisor=(_me().get('model_target') if _me() else None),
                                 thesis_break=sup.get('thesis_break_price'), summary=sup.get('summary'),
                                 fair_value=sup.get('fair_value') if isinstance(sup.get('fair_value'), dict) else None)
    elif sup:
        Ks = [l['K'] for l in sorted(sup['ladder'], key=lambda l: l['K'])]
        mean = {K: sum(next(l['p'] for l in loaded[p]['ladder'] if abs(l['K'] - K) < 1e-6 * max(K, 1) + 0.01)
                       for p in sorted(loaded)) / len(agents) for K in Ks}
        sl = {l['K']: l['p'] for l in sup['ladder']}
        changes = sup.get('changes')
        derived = not changes
        if derived:
            changes = []
            big = sorted(Ks, key=lambda K: -abs(sl[K] - mean[K]))[:3]
            for K in sorted(big):
                if abs(sl[K] - mean[K]) >= 0.005:
                    changes.append(dict(item=f'P(close >= {K:,.2f})', **{'from': round(mean[K], 3)}, to=round(sl[K], 3),
                                        why=sup.get('confidence_reason', '')))
            tbs = sorted(a['thesis_break'] for a in agents if a['thesis_break'])
            if tbs and sup.get('thesis_break_price') and abs(sup['thesis_break_price'] - tbs[len(tbs)//2]) > 0.005 * S:
                changes.append(dict(item='thesis break', **{'from': f'agents {tbs[0]:,.0f} to {tbs[-1]:,.0f}'}, to=sup['thesis_break_price'],
                                    why=sup.get('thesis_break_reason', '')))
            for d in sup.get('disagreements', []):
                changes.append(dict(item=d.get('topic'), **{'from': d.get('positions')}, to=d.get('resolution'), why=d.get('evidence')))
        out['supervisor'] = dict(confidence=sup.get('confidence'), used=res.get('supervisor_used'),
                                 confidence_reason=sup.get('confidence_reason'), changes=changes, changes_derived=derived,
                                 median_mean=round(median_from_ladder([{'K': K, 'p': mean[K]} for K in Ks]), 2),
                                 median_supervisor=round(median_from_ladder(sorted(sup['ladder'], key=lambda l: l['K'])), 2),
                                 thesis_break=sup.get('thesis_break_price'), summary=sup.get('summary'),
                                 fair_value=sup.get('fair_value') if isinstance(sup.get('fair_value'), dict) else None)
    json.dump(out, open(os.path.join(HERE, 'runs', date, s, 'explain.json'), 'w'), indent=1)
    return out

if __name__ == '__main__':
    date, syms = sys.argv[1], [x.upper() for x in sys.argv[2:]]
    syms = syms or [os.path.basename(os.path.dirname(p)) for p in sorted(glob.glob(os.path.join(HERE, 'runs', date, '*', 'result.json')))]
    import concurrent.futures as cf
    with cf.ThreadPoolExecutor(12) as ex:
        outs = list(ex.map(lambda s: explain(date, s), syms))
    for s, o in zip(syms, outs):
        sp = o['supervisor'] or {}
        print(f"{s:6} " + '  '.join(f"{a['agent']}: ${a['fair_value']} [{'+'.join(m['name'] for m in a['methods'])}]" for a in o['agents'])
              + f"  | sup {sp.get('confidence')} median {sp.get('median_mean')} -> {sp.get('median_supervisor')}, {len(sp.get('changes') or [])} changes")
