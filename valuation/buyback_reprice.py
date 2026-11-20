"""Nightly re-pricing of research buybacks at the close (Kilian 2026-11-05: buybacks should follow the stock price, "but only
when it moves target by more than say 1%"; thread "Re-price buybacks when spot moves target").

Usage: python3 buyback_reprice.py D [SYM ...] [--dry-run] [--threshold 0.01]
A set opts in with model_inputs.shares.buyback {ref_price, retired: [{from, to, shares, ref_price?}], basis} (model_engine.py
docstring): the shares its research retires at the price it assumed. For each live driver-model set that has one (the set the
name's research leg used on the previous re-mark, as stake_marks.py finds it; skipped while a name update is in progress), this
values the set with the buybacks re-priced at D's close and compares it with the target as it stands (the stored reprice, or
none). Only when the research target (12m) moves by more than the threshold (1%) does it write
shares.buyback.reprice {price, date, by, target_before, target_after} into the set and revalue it (model_engine.py run U SYM),
so combine.py (step 3) reads it. Below the threshold the set is left alone, so small moves never churn it. The research's
own path and ref_price are never changed. Writes runs/D/buyback_reprice.json. A few seconds (in-process engine, names in parallel)."""
import concurrent.futures as cf, copy, json, os, subprocess, sys
HERE = os.path.dirname(os.path.abspath(__file__)).replace('/mnt/attach/project-files', '/mnt/project-files')
sys.path.insert(0, HERE)
import model_engine as me
from stake_marks import close_on, set_file

THRESHOLD = 0.01


def target(d, U, sym):
    """The research target (12m) the way model_engine.py run values the supervisor set."""
    spot, beta = me.prior_spot_beta(U, sym)
    mi, note = me.apply_dcf_policy(me.extract(d), sym, spot, beta)
    v2 = me.dcf_policy().get('mode') == 'v2'
    cap = me.MAX_YEARS_NO_DCF if v2 or not (mi.get('dcf_weight', 0.0) or 0) > 0 else me.MAX_YEARS
    if me.n_forward(mi) > cap and (v2 or note or len(mi['years']) <= me.MAX_YEARS): mi = dict(mi, _allow_long=True)
    return me.run_set(mi, d.get('as_of', U), spot, sym=sym)['median']


def one(sym, D, dry, thr):
    sf, why = set_file(sym, D)
    if not sf: return dict(skipped=why)
    raw = open(sf).read(); d = json.loads(raw); sh = d['model']['reconciled_inputs'].get('shares') or {}
    bb = sh.get('buyback')
    if not isinstance(bb, dict) or 'retired' not in bb: return None  # no spec (SNOW keeps buyback notes as text)
    U = sf.split('/')[-3]; old = bb.get('reprice') or {}
    try: px, src = close_on(sym, D)
    except Exception as e: return dict(set=os.path.relpath(sf, HERE), error=f'no close: {str(e)[:200]}')
    rep = dict(set=os.path.relpath(sf, HERE), close=px, ref_price=bb['ref_price'], stored_price=old.get('price'))
    try:
        cur = target(d, U, sym); d2 = copy.deepcopy(d)
        d2['model']['reconciled_inputs']['shares']['buyback']['reprice'] = dict(price=px, date=D, price_source=src, by='buyback_reprice.py')
        new = target(d2, U, sym)
    except me.ModelError as e: return dict(rep, error=f'model: {str(e)[:300]}')
    mv = new / cur - 1; rep.update(target_now=round(cur, 2), target_at_close=round(new, 2), move=round(mv, 4), changed=abs(mv) > thr)
    if rep['changed'] and not dry:
        d2['model']['reconciled_inputs']['shares']['buyback']['reprice'].update(target_before=round(cur, 2), target_after=round(new, 2))
        ind = len(raw.split('\n', 2)[1]) - len(raw.split('\n', 2)[1].lstrip()) if raw.count('\n') > 1 else 1
        json.dump(d2, open(sf, 'w'), indent=ind or 1)
        r = subprocess.run([sys.executable, 'model_engine.py', 'run', U, sym], cwd=HERE, capture_output=True, text=True)
        rep['model_engine'] = 'ok' if r.returncode == 0 else f'failed: {(r.stdout + r.stderr)[-300:]}'
        try: rep['model_target'] = round(json.load(open(os.path.join(os.path.dirname(sf), 'model_engine.json')))['model_target'], 2)
        except Exception: pass
    return rep


def main(D, dry, thr, only=None):
    syms = only or sorted(s for s in os.listdir(os.path.join(HERE, 'driver_models')) if s.isupper() and os.path.isdir(os.path.join(HERE, 'driver_models', s)))
    with cf.ThreadPoolExecutor(8) as ex: res = dict(zip(syms, ex.map(lambda s: one(s, D, dry, thr), syms)))
    out = dict(date=D, threshold=thr, names={s: r for s, r in res.items() if r is not None})
    if not dry:
        os.makedirs(os.path.join(HERE, 'runs', D), exist_ok=True)
        json.dump(out, open(os.path.join(HERE, 'runs', D, 'buyback_reprice.json'), 'w'), indent=1)
    print(json.dumps(out, indent=1))


if __name__ == '__main__':
    a = sys.argv[1:]
    if not a: print(__doc__); sys.exit(1)
    thr = float(a[a.index('--threshold') + 1]) if '--threshold' in a else THRESHOLD
    main(a[0], '--dry-run' in a, thr, [x for x in a[1:] if x.isupper()] or None)
