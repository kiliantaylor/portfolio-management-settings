"""Nightly re-mark of listed stakes inside a name's research set (Kilian 2026-10-30 03:29Z: "re-mark the research leg based on
their stock price at each close", for GOOGL, AMZN "and any companies we hold with similar structure").

Usage: python3 stake_marks.py D [--dry-run]
Spec: data/listed_stakes.json {HOLDER: {"stakes": [...], "overlay": {...}}}; a stake with "ticker": null is pending (not listed
yet, e.g. Anthropic before pricing) and is skipped. Per stake, at the ticker's close on D (data/daily_TICKER.csv, else Yahoo):
  gross = shares_m x close; after_lockup = gross x (1 - lockup); net = after_lockup - tax_rate x max(0, after_lockup - cost)
  adjustment value = haircut x net - counted       (counted = the part of the stake already in another line of the set)
The holder's overlay adjustment (a conglomerate discount on non-core parts) is -rate x (fixed_part + sum of haircut x net
over its stakes with "in_overlay": true). The adjustments are found by the "match" text in their name; the set is the one the
holder's research leg used on the previous re-mark (runs/<prev>/HOLDER/result.json research_leg.set_file, standard model only).
A changed set is revalued with model_engine.py run U HOLDER so combine.py (step 3) reads it. A holder whose set has a name
update in progress (data/name_updates.json not done) is skipped. Writes runs/D/stake_marks.json."""
import glob, json, os, subprocess, sys, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__)).replace('/mnt/attach/project-files', '/mnt/project-files')
J = lambda p: json.load(open(p))


def close_on(t, D):
    f = os.path.join(HERE, 'data', f'daily_{t}.csv')
    if os.path.exists(f):
        rows = [r.split(',') for r in open(f).read().strip().split('\n')[1:]]
        rows = [r for r in rows if r[0] <= D]
        if rows and rows[-1][0] == D: return float(rows[-1][4]), f'data/daily_{t}.csv'
    u = f'https://query1.finance.yahoo.com/v8/finance/chart/{t}?range=5d&interval=1d'
    r = json.load(urllib.request.urlopen(urllib.request.Request(u, headers={'User-Agent': 'Mozilla/5.0'}), timeout=30))['chart']['result'][0]
    import datetime as dt
    for ts, c in zip(r['timestamp'][::-1], r['indicators']['quote'][0]['close'][::-1]):
        if c is not None and dt.datetime.utcfromtimestamp(ts).strftime('%Y-%m-%d') == D: return float(c), 'Yahoo daily bar'
    raise ValueError(f'no {D} close for {t}')


def set_file(h, D):
    rs = [f for f in sorted(glob.glob(os.path.join(HERE, 'runs', '*', h, 'result.json'))) if f.split('/')[-3] < D]
    if not rs: return None, 'no earlier result.json'
    rl = J(rs[-1]).get('research_leg') or {}
    if rl.get('source') != 'standard_model' or not rl.get('set_file'): return None, f"research leg is {rl.get('source')}, not a standard model"
    st = (J(os.path.join(HERE, 'data', 'name_updates.json')).get('names') or {}).get(h) or {}
    if st.get('status') not in (None, 'done'): return None, f"name update {st.get('status')}"
    return os.path.join(HERE, rl['set_file']), None


def main(D, dry):
    spec = J(os.path.join(HERE, 'data', 'listed_stakes.json')); out = {}
    for h, sp in spec.items():
        if h.startswith('_'): continue
        live = [s for s in sp.get('stakes', []) if s.get('ticker')]
        if not live: out[h] = dict(skipped='no listed stake yet'); continue
        sf, why = set_file(h, D)
        if not sf: out[h] = dict(skipped=why); continue
        d = J(sf); adj = d['model']['reconciled_inputs']['adjustments']
        def find(m):
            hit = [a for a in adj if m in a['name']]
            if len(hit) != 1: raise SystemExit(f'{h}: {len(hit)} adjustments match {m!r}')
            return hit[0]
        rep, parts, changed = dict(set=os.path.relpath(sf, HERE), stakes=[]), 0.0, False
        for s in live:
            try: px, src = close_on(s['ticker'], D)
            except Exception as e: rep['stakes'].append(dict(id=s['id'], error=str(e)[:200])); continue
            gross = s['shares_m'] * px; al = gross * (1 - s.get('lockup', 0))
            net = al - s.get('tax_rate', 0) * max(0.0, al - s.get('cost', 0)); val = round(s.get('haircut', 1) * net - s.get('counted', 0), 1)
            if s.get('in_overlay'): parts += s.get('haircut', 1) * net
            a = find(s['match']); old = a['value']
            a['value'] = val
            a['mark'] = dict(date=D, ticker=s['ticker'], close=px, price_source=src, gross=round(gross, 1), net=round(net, 1), by='stake_marks.py')
            changed |= abs(val - old) >= 1
            rep['stakes'].append(dict(id=s['id'], ticker=s['ticker'], close=px, value=val, was=old))
        ov = sp.get('overlay')
        if ov and not any('error' in x for x in rep['stakes']):
            a = find(ov['match']); old = a['value']; a['value'] = round(-ov['rate'] * (ov['fixed_part'] + parts), 1)
            a['mark'] = dict(date=D, by='stake_marks.py', fixed_part=ov['fixed_part'], marked_parts=round(parts, 1))
            changed |= abs(a['value'] - old) >= 1; rep['overlay'] = dict(value=a['value'], was=old)
        if changed and not dry:
            json.dump(d, open(sf, 'w'), indent=1)
            U = sf.split('/')[-3]
            r = subprocess.run([sys.executable, 'model_engine.py', 'run', U, h], cwd=HERE, capture_output=True, text=True)
            rep['model_engine'] = 'ok' if r.returncode == 0 else f'failed: {(r.stdout + r.stderr)[-300:]}'
            try: rep['model_fair_value'] = J(os.path.join(os.path.dirname(sf), 'model_engine.json')).get('model_fair_value')
            except Exception: pass
        rep['changed'] = changed; out[h] = rep
    if not dry:
        os.makedirs(os.path.join(HERE, 'runs', D), exist_ok=True)
        json.dump(out, open(os.path.join(HERE, 'runs', D, 'stake_marks.json'), 'w'), indent=1)
    print(json.dumps(out, indent=1))


if __name__ == '__main__':
    a = sys.argv[1:]
    if not a: print(__doc__); sys.exit(1)
    main(a[0], '--dry-run' in a)
