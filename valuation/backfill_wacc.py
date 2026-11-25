"""Backfilled model sets (agents/model_backfill.md) whose run never stated a WACC get one consistent filled-in rate
instead of each translator's own proxy (Kilian 2026-10-14 backfill): cost of equity = the price target's own market
leg, T-bill + the measured beta-decile premium (premium in priors/DATE/SYM.json; 7.69% x (0.768 + 0.232 x beta) from 2026-10-14)
(priors/DATE/SYM.json, which the agents may not open), weighted with after-tax debt on the set's
own current EV. Value is unchanged where dcf_weight is 0; the DCF leg, sensitivity and reverse DCF read sensibly.

python3 backfill_wacc.py DATE SYM   -> rewrites WACC in runs/DATE/SYM/model_backfill/*.json where it was filled in
"""
import glob, json, os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
date, sym = sys.argv[1], sys.argv[2].upper()
pr = json.load(open(f'{HERE}/priors/{date}/{sym}.json'))
rf, beta, erp, spot = pr['rf'], pr['beta'], pr.get('erp', 0.05), pr['spot']
prem = pr.get('premium', beta * erp)
ke = rf + prem
for f in sorted(glob.glob(f'{HERE}/runs/{date}/{sym}/model_backfill/*.json')):
    d = json.load(open(f))
    mi = d.get('model_inputs') or d['model']['reconciled_inputs']
    dr = mi['discount_rate']
    rows = [r for r in mi.get('inputs_detail', []) if 'wacc' in r.get('input', '').lower() or 'discount' in r.get('input', '').lower()]
    if not any('filled in' in s.lower() or 'no discount rate' in s.lower() or 'no wacc' in s.lower() for s in [str(dr.get('basis', ''))] + [r.get('derivation', '') for r in rows]): print(os.path.basename(f), 'stated WACC kept', dr['wacc']); continue
    E = spot * mi['shares']['diluted_now']; D = max(mi['balance']['net_debt'] + mi['balance'].get('minorities', 0), 0)
    kd = mi['balance'].get('interest_rate', 0.05); t = mi['years'][0].get('tax_rate', 0.21)
    w = (E * ke + D * kd * (1 - t)) / (E + D)
    why = (f"filled in (not stated in this set's original work): cost of equity {ke:.4f} = T-bill {rf:.4f} + premium {prem:.4f} (beta {beta:.2f} on the "
           f"{erp:.2%} market premium, the price target's market leg, {date}); after-tax debt {kd:.4f} x (1 - {t:.3f}) on net debt and "
           f"minorities {D:,.0f} vs equity {E:,.0f} ({mi['shares']['diluted_now']:,.2f}M x ${spot:.2f}) -> WACC {w:.4f}")
    old = dr['wacc']; dr['wacc'] = round(w, 4); dr['basis'] = why
    for r in rows:
            r.update(value=round(w, 4), basis='estimate', derivation=why)
    json.dump(d, open(f, 'w'), indent=1)
    print(os.path.basename(f), f'WACC {old:.4f} -> {w:.4f}')
