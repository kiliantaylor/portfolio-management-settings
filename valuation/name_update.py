"""Marginal work on a researched name: one entry point for a model update instead of a full run (Kilian 2026-10-21 21:10Z:
"figure out an easier way to do marginal work on names that we have already researched").

Actuals: `start` runs xbrl_actuals.py roll, so the reported quarters of every XBRL-mapped driver land in the copied set by
script (with their filings) before the modeler starts; the modeler judges forward values and the unmapped drivers.

Triggers: a print (held names via routines/earnings_rerun_check.py; unheld names from `queue`), material news, or a pick from
the nightly swap check (market_color/beats_check.py). Each update runs in its own small, fresh thread.

  python3 name_update.py queue [D]                 researched unheld names with results filed since their research, with their
                                                   swap-check standing (beat / near miss / in reach / out of reach / gated)
  python3 name_update.py start SYM [U] [--why TEXT] [--mode update|build|full] [--thread ID]
                                                   sets up runs/U/SYM as an update round (U = newest priors date for SYM):
                                                   copies the base round's agent files and supervisor.json, writes a fresh SEC
                                                   fact pack, update.json and brief_update.md (agents/name_update.md) for ONE
                                                   modeler agent, and marks the name pending in data/name_updates.json
  python3 name_update.py gate U SYM                citation gate on the changed rows only (run_digest.py gate-update): then one
                                                   agent per gate/brief_shard_N.md + gate/brief_text.md, `run_digest.py
                                                   gate-merge U SYM`, and agents/gate_fix.md until clean
  python3 name_update.py finish U SYM              model_engine.py run + combine.py --research U, compares with the base value,
                                                   marks the update done and prints the remaining finish steps

Modes. update: the base round has a gate-clean standard model (combine.standard_model); the modeler changes only what the new
information moves and lists the changed rows, and the gate checks those rows plus guidance_check. build: no standard model
yet (most names researched before 2026-10-19); the modeler writes one deep reconciled set from the base round's cited work
plus the new quarter, and the gate checks the whole set. full: no gate-clean research, a non-reliance notice (8-K item 4.02)
since the base round, or the last full run is more than FULL_AFTER_DAYS old; `start` stops and says so (held names: the
post-earnings rule escalates to a full rerun; unheld names: Kilian picks full runs).
An update round is a research round like any other (combine.py picks the newest gate-clean one; its update.json marks it), so
the nightly re-mark uses it from then on. Its Platt shift is computed at U's spot, as for any new round. The forecasters'
distribution is the base round's, re-centred on the new model target (combine.py STD_MODEL_RECENTRE).
Escalation (post-earnings rule, Kilian 2026-10-21 14:55Z): research target (12m) moves more than ~10%, guidance withdrawn or far outside
its range, a move over 2x the implied one or near the thesis break, structural news. `finish` flags the first mechanically;
the modeler records the rest in update.json escalate."""
import datetime as dt, glob, json, os, shutil, subprocess, sys
HERE = os.path.dirname(os.path.abspath(__file__)).replace('/mnt/attach/project-files', '/mnt/project-files')
RUNS = os.path.join(HERE, 'runs'); ST = os.path.join(HERE, 'data', 'name_updates.json')
FULL_AFTER_DAYS = 90; ESCALATE_FV = 0.10
sys.path.insert(0, HERE)
J = lambda p: json.load(open(p))
now = lambda: dt.datetime.now(dt.timezone.utc).strftime('%Y-%m-%dT%H:%MZ')


def _state():
    st = J(ST) if os.path.exists(ST) else {}
    st.setdefault('names', {}); st.setdefault('history', []); return st


def _save(st): json.dump(st, open(ST, 'w'), indent=1)


def is_update(d, s): return os.path.exists(os.path.join(RUNS, d, s, 'update.json'))


def rounds(s, before=None):
    """Gate-clean research rounds (supervisor.json + clean citation audit), oldest first."""
    import combine
    ds = sorted(f.split('/')[-3] for f in glob.glob(os.path.join(RUNS, '*', s, 'supervisor.json')))
    return [d for d in ds if (before is None or d < before) and combine.citation_gate(d, s)]


def base_fv(d, s):
    """(research target (12m), source) of a round: the engine's reconciled value when the round has a standard model, else the supervisor's."""
    import combine
    sm, _ = combine.standard_model(d, s, combine.citation_gate(d, s))
    if sm: return sm['model_fair_value'], 'standard model'
    fv = (J(os.path.join(RUNS, d, s, 'supervisor.json')).get('fair_value') or {}).get('value')
    return fv, 'supervisor research target (12m)'


def filings_since(s, d):
    """Results and other material filings dated after D (SEC submissions feed): [{form, filed, items, url}]."""
    import secfacts
    try:
        _, cik = secfacts.cik_of(s); r = secfacts.submissions(cik)['filings']['recent']
    except Exception as e: return [dict(form='?', filed='', items='', url='', note=f'SEC feed failed: {str(e)[:100]}')]
    out = []
    for i, fm in enumerate(r['form']):
        if r['filingDate'][i] <= d: continue
        it = (r.get('items') or [''] * len(r['form']))[i] or ''
        if fm in ('10-Q', '10-K', '20-F', '40-F', '6-K') or (fm == '8-K' and any(x in it for x in ('2.02', '4.02', '5.02', '1.01', '2.01'))):
            out.append(dict(form=fm, filed=r['filingDate'][i], items=it, url=secfacts.folder(cik, r['accessionNumber'][i]) + r['primaryDocument'][i]))
    return sorted(out, key=lambda x: x['filed'])


def mode_for(s, U, fl):
    rs = rounds(s, before=U)
    if not rs: return 'full', 'no gate-clean research round before the update date'
    if any(f['form'] == '8-K' and '4.02' in f['items'] for f in fl): return 'full', 'non-reliance notice (8-K item 4.02) since the base round: the research is suspect'
    full = [d for d in rs if not is_update(d, s)]
    age = (dt.date.fromisoformat(U) - dt.date.fromisoformat(full[-1])).days if full else None
    if age is None or age > FULL_AFTER_DAYS: return 'full', f'last full run {full[-1] if full else None} is {age} days old (limit {FULL_AFTER_DAYS})'
    import combine
    sm, why = combine.standard_model(rs[-1], s, 'clean')
    return ('update', f'base {rs[-1]} has a gate-clean standard model') if sm else ('build', f'base {rs[-1]}: {why}')


def queue(D):
    import active_names as an, book
    held = {p['symbol'] for p in book.load().get('positions') or [] if p.get('shares')}
    reach = an._reach(D, an.universe(D)); st = _state()
    bc = {}
    for f in sorted(glob.glob(os.path.join(os.path.dirname(HERE), 'market_color', 'beats_check', '*.json')), reverse=True):
        if os.path.basename(f)[:-5] <= D: bc = J(f); break
    rows = {r['sym']: r for r in bc.get('candidates') or []}
    out = []
    for s in sorted(an.universe(D)):
        if s in held: continue
        rs = rounds(s)
        if not rs: continue
        fl = filings_since(s, rs[-1])
        err = [f['note'] for f in fl if f.get('note')]
        fl = [f for f in fl if not f.get('note') and (f['form'] != '8-K' or '2.02' in f['items'] or '4.02' in f['items'])]
        if not fl and not err: continue
        dl, bd = reach.get(s, (None, None)); r = rows.get(s) or {}
        tag = ('beat' if r.get('beats_all_planned') else 'near miss' if dl is not None and dl > 0 else 'in reach' if dl is not None and dl >= -an.REACH
               else 'gated' if s in reach and dl is None else 'out of reach')
        out.append(dict(symbol=s, research=rs[-1], filed=[f"{f['form']} {f['filed']}" + (f" items {f['items']}" if f['items'] else '') for f in fl],
                        sharpe_delta=dl, swap_check=bd, standing=tag, pending=(st['names'].get(s) or {}).get('status') == 'pending', **(dict(sec_error=err[0]) if err else {})))
    order = ['beat', 'near miss', 'in reach', 'gated', 'out of reach']
    out.sort(key=lambda x: (order.index(x['standing']), -(x['sharpe_delta'] if x['sharpe_delta'] is not None else -9)))
    print(json.dumps(dict(as_of=D, swap_check=bc.get('as_of'), note='contenders = beat + near miss (raises book Sharpe, but less than the planned adds)',
                          names=out), indent=1))


def start(s, U=None, why='', mode=None, thread=None):
    import secfacts, prep_briefs
    st = _state()
    if (st['names'].get(s) or {}).get('status') == 'pending': sys.exit(f"{s}: an update is already pending ({st['names'][s]})")
    if not U:
        ds = sorted(os.path.basename(os.path.dirname(f)) for f in glob.glob(os.path.join(HERE, 'priors', '*', f'{s}.json')))
        if not ds: sys.exit(f'{s}: no priors; run `python3 market_dist.py {s}` first')
        U = ds[-1]
    if not os.path.exists(os.path.join(HERE, 'priors', U, f'{s}.json')): sys.exit(f'{s}: no priors/{U}/{s}.json; run `python3 market_dist.py {s}` (writes the last close)')
    rs = rounds(s, before=U)
    if not rs: sys.exit(f'{s}: no gate-clean research before {U}: needs a full run')
    B = rs[-1]; fl = filings_since(s, B)
    m, reason = mode_for(s, U, fl)
    if mode: m, reason = mode, f'set by hand (computed: {m}, {reason})'
    if m == 'full': sys.exit(f'{s}: full run needed: {reason}. Held names: the post-earnings rule escalates; unheld names: Kilian picks full runs.')
    bd, rd = os.path.join(RUNS, B, s), os.path.join(RUNS, U, s); os.makedirs(rd, exist_ok=True)
    if os.path.exists(os.path.join(rd, 'supervisor.json')) and not is_update(U, s): sys.exit(f'{rd} already holds a research round')
    for f in glob.glob(os.path.join(bd, 'agent_*.json')) + [os.path.join(bd, x) for x in ('supervisor.json', 'playbook.json', 'peers.json', 'peers.md')]:
        if os.path.exists(f): shutil.copy(f, rd)
    if os.path.isdir(os.path.join(bd, 'model_backfill')): shutil.copytree(os.path.join(bd, 'model_backfill'), os.path.join(rd, 'base_backfill'), dirs_exist_ok=True)
    try:
        fp = secfacts.build(s); json.dump(fp, open(f'{rd}/facts.json', 'w'), indent=1); open(f'{rd}/facts.md', 'w').write(secfacts.to_md(fp))
    except Exception as e: print(f'{s}: no fact pack ({str(e)[:100]}); the modeler reads the filings directly')
    pb, _ = prep_briefs.playbook(s)
    fv, fvs = base_fv(B, s)
    u = dict(symbol=s, update_date=U, base=B, mode=m, mode_reason=reason, trigger=why or 'unspecified', playbook=pb, base_fair_value=fv,
             base_fair_value_source=fvs, filings_since_base=fl, created_utc=now(), changed_inputs=[], what_changed='', escalate=[])
    json.dump(u, open(f'{rd}/update.json', 'w'), indent=1)
    xa = None  # Kilian 2026-10-28: reported actuals for the drivers land by script (xbrl_actuals.py), the modeler only judges forward values
    try:
        import xbrl_actuals; xa = xbrl_actuals.roll(U, s)
    except Exception as e: print(f'{s}: XBRL actuals not pulled ({str(e)[:120]}); the modeler takes the actuals from the filings')
    xat = ('none: no driver set, no mapped driver, or the pull failed; take every actual from the filings' if not xa else
           f"actuals on {', '.join(xa['scripted']) or 'none'}; first projected year re-set from new quarters on {', '.join(xa['rolled']) or 'none'}")
    t = open(os.path.join(HERE, 'agents', 'name_update.md')).read().split('\n---\n', 1)[1]
    flt = '\n'.join(f"  - {f['form']} filed {f['filed']}" + (f" (items {f['items']})" if f['items'] else '') + f": {f['url']}" for f in fl) or '  - (none found in the SEC feed; check the company IR page)'
    kv = dict(SYMBOL=s, BASE=B, BASE_DIR=bd, DIR=rd, UPDATE_DATE=U, MODE=m, TRIGGER=why or 'unspecified', PLAYBOOK=pb or 'none (general model_inputs rules)',
              FILINGS=flt, VAL_DIR=HERE, XBRL_ACTUALS=xat, BASE_FV=f'{fv:.2f} ({fvs})' if isinstance(fv, (int, float)) else 'n/a')
    for k, v in kv.items(): t = t.replace('{{' + k + '}}', str(v))
    open(f'{rd}/brief_update.md', 'w').write(t)
    st['names'][s] = dict(status='pending', update_date=U, base=B, mode=m, trigger=why or 'unspecified', thread=thread, started_utc=now())
    _save(st)
    print(json.dumps(dict(symbol=s, update_date=U, base=B, mode=m, because=reason, playbook=pb, base_fair_value=fv, filings=len(fl),
                          next=[f'one agent (fresh context) on {rd}/brief_update.md',
                                f'python3 {HERE}/name_update.py gate {U} {s}', f'python3 {HERE}/name_update.py finish {U} {s}']), indent=1))


def gate(U, s):
    subprocess.run([sys.executable, os.path.join(HERE, 'run_digest.py'), 'gate-update', U, s], check=True, cwd=HERE)


def finish(U, s):
    import book
    rd = os.path.join(RUNS, U, s); u = J(f'{rd}/update.json')
    ca = f'{rd}/citation_audit.json'
    if not (os.path.exists(ca) and J(ca).get('clean') is True): sys.exit(f'{s}: {ca} missing or not clean: finish the gate first')
    if u['mode'] == 'update' and not u.get('changed_inputs'): sys.exit(f'{s}: update.json changed_inputs is empty')
    for cmd in (['model_engine.py', 'run', U, s], ['combine.py', U, s, '--research', U]):
        r = subprocess.run([sys.executable, *cmd], cwd=HERE, capture_output=True, text=True)
        print(r.stdout[-1500:], r.stderr[-800:], sep='')
        if r.returncode: sys.exit(f'{s}: {cmd[0]} failed')
    me, res = J(f'{rd}/model_engine.json'), J(f'{rd}/result.json')
    new, old = me.get('model_fair_value'), u.get('base_fair_value')
    mv = (new / old - 1) if isinstance(new, (int, float)) and isinstance(old, (int, float)) and old else None
    esc = list(u.get('escalate') or [])
    if mv is not None and abs(mv) > ESCALATE_FV: esc.append(f'research target (12m) moved {mv:+.1%} ({old:.2f} -> {new:.2f}), over {ESCALATE_FV:.0%}')
    rl = res.get('research_leg') or {}
    if rl.get('source') != 'standard_model' or rl.get('set_file') != f'runs/{U}/{s}/supervisor.json':
        esc.append(f"combine did not use this update's model ({rl.get('source')}: {rl.get('fallback_reason') or rl.get('set_file')})")
    held = any(p['symbol'] == s and p.get('shares') for p in book.load().get('positions') or [])
    st = _state(); e = st['names'].get(s) or {}
    e.update(status='done', done_utc=now(), update_date=U, model_fair_value=new, base_fair_value=old, fv_move=round(mv, 4) if mv is not None else None,
             target=res.get('target'), escalate=esc)
    st['names'][s] = e; st['history'].append(dict(symbol=s, **{k: e.get(k) for k in ('update_date', 'base', 'mode', 'trigger', 'model_fair_value', 'base_fair_value', 'target', 'escalate', 'done_utc')}))
    _save(st)
    steps = ([f'python3 {os.path.dirname(HERE)}/routines/earnings_rerun_check.py --done {s} {U}   (held name: releases the add/trim hold)'] if held else []) + [
        f'cd {HERE} && python3 nightly.py {U} --after-targets {s}   (Bash run_in_background; waits on data/nightly.lock)',
        f'python3 runlock.py take "publish {s}" -> token; publish report.html to Price Targets (read the artifact once; data-line-only diff -> republish the disk build, else merge); python3 runlock.py release TOKEN',
        'quote book Sharpe only from runs/D/portfolio.json sharpe_combo.base_sharpe']
    print(json.dumps(dict(symbol=s, update_date=U, mode=u['mode'], model_fair_value=new, base_fair_value=old, fv_move=mv, target=res.get('target'),
                          spot=res.get('spot'), stop_loss=res.get('stop_loss'), take_profit=res.get('take_profit'), escalate=esc,
                          judgment_calls=J(f'{rd}/supervisor.json').get('judgment_calls_for_kilian') or [],
                          held=held, next=steps,
                          escalation_note=('held name: escalate to a full rerun' if held else 'unheld: ask Kilian on a card whether to full-run') if esc else None), indent=1))


if __name__ == '__main__':
    a = sys.argv[1:]
    if not a: print(__doc__); sys.exit(1)
    def opt(k):
        if k in a: i = a.index(k); v = a[i + 1]; del a[i:i + 2]; return v
    why, md, th = opt('--why'), opt('--mode'), opt('--thread')
    if a[0] == 'queue': queue(a[1] if len(a) > 1 else dt.date.today().isoformat())
    elif a[0] == 'start': start(a[1].upper(), a[2] if len(a) > 2 else None, why or '', md, th)
    elif a[0] == 'gate': gate(a[1], a[2].upper())
    elif a[0] == 'finish': finish(a[1], a[2].upper())
    else: print(__doc__); sys.exit(1)
