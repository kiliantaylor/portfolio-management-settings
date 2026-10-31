"""Live spreadsheet of the standard model (Option 1, model_engine.py) for one name: the supervisor's reconciled
assumption set as editable inputs, and every step of the engine (projection, DCF to the horizon, horizon multiples,
sum of the parts, custom legs, net-debt bridge, scenarios, lognormal mixture and its quantiles) as live formulas.
Kilian 2026-10-13 15:03Z: "run the updated model for $tjx and have that be where the 'model in google sheets' button
links to". Upload the .xlsx to Drive as a Google Sheet; the page's Sheets button reads data/model_gsheets.json.
A set with segment_build (2026-10-21) gets segment revenue / EBIT rows on Model and on each Calc tab (growth and margin by
segment and year, scenario deltas on every segment), net sales and EBIT as their sums plus years[].corporate_ebit, the
segment base revenues on Valuation; sets without it build exactly as before.

Usage: python3 model_gsheet.py DATE SYM  -> runs/DATE/SYM/std_model_SYM.xlsx
The sheet must reproduce model_engine.json (supervisor median and research target (12m), data key fair_value); check after LibreOffice recalculation
with `python3 model_gsheet.py verify DATE SYM` (needs libreoffice-calc).
"""
import json, os, sys, re, datetime as dt, subprocess, shutil, tempfile, glob
import openpyxl
from openpyxl.styles import Font, Alignment, PatternFill
from openpyxl.utils import get_column_letter as L
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import model_engine as ME

BLUE = Font(color='0000FF'); BOLD = Font(bold=True); TITLE = Font(bold=True, size=13); GREY = Font(color='666666', italic=True)
HEAD = PatternFill('solid', fgColor='E8EEF7')
F_M, F_PS, F_PCT, F_D, F_X = '#,##0;(#,##0)', '0.00', '0.0%', 'yyyy-mm-dd', '0.00"x"'
DRIVERS = [('revenue_growth', 'Revenue growth', F_PCT), ('ebit_margin', 'EBIT margin', F_PCT), ('tax_rate', 'Tax rate', F_PCT),
           ('da_pct_rev', 'D&A % revenue', F_PCT), ('capex_pct_rev', 'Capex % revenue', F_PCT),
           ('nwc_pct_incr_rev', 'Working capital % of revenue increase', F_PCT), ('sbc_pct_rev', 'Stock comp % revenue', F_PCT),
           ('shareholder_returns', 'Shareholder returns ($)', F_M), ('shareholder_returns_pct_rev', 'Shareholder returns % revenue', F_PCT),
           ('other_uses', 'Other uses of cash ($: M&A, investments)', F_M), ('adj_ebitda_addback_pct_rev', 'Adj. EBITDA addback % revenue', F_PCT)]
REQUIRED = {'revenue_growth', 'ebit_margin', 'tax_rate'}

def d(s): return dt.date.fromisoformat(s[:10])

def put(ws, ref, v, fmt=None, font=None, wrap=False, fill=None):
    c = ws[ref]; c.value = v
    if fmt: c.number_format = fmt
    if font: c.font = font
    if wrap: c.alignment = Alignment(wrap_text=True, vertical='top')
    if fill: c.fill = fill
    return c

def header(ws, row, labels, col=1):
    for j, t in enumerate(labels):
        c = put(ws, f'{L(col + j)}{row}', t, font=BOLD); c.fill = HEAD

def section(ws, row, text, ncols):
    for j in range(ncols): ws[f'{L(1 + j)}{row}'].fill = SECT
    put(ws, f'A{row}', text, font=BOLD)

def num(x):
    return None if x is None or x == '' else x

GREEN = Font(color='008000'); SECT = PatternFill('solid', fgColor='D9E2F0'); ITAL = Font(italic=True, color='444444')
F_BP = '0" bp";-0" bp"'; F_EPS = '$0.00'

def build(date, sym, out=None):
    """Sell-side layout (Kilian 2026-10-13 16:43Z: "format more similarly to [public sell-side models] (but better)"):
    Summary (cover box, key estimates, legs, scenarios, checks) | Model (annual P&L and cash flow, actuals beside
    estimates, blue drivers, vs guidance and consensus) | Valuation (target derivation, 12-month EPS basis, DCF build,
    assumptions) | Sensitivity (three grids) | Distribution | Calc tabs (engine machinery per scenario) | Agents | Sources."""
    rd = f'{HERE}/runs/{date}/{sym}'
    sup = ME.load(f'{rd}/supervisor.json')
    mi = ME.resolve_drivers(ME.extract(sup)); as_of = sup.get('as_of', date)
    eng = json.load(open(f'{rd}/model_engine.json')) if os.path.exists(f'{rd}/model_engine.json') else {}
    spot = eng.get('spot')
    if spot is None:
        try: spot = json.load(open(f'{HERE}/priors/{date}/{sym}.json'))['spot']
        except Exception: spot = None
    # Fixed DCF (Kilian card 2026-10-21 08:05Z): the sheet takes the engine's script-set rate, weight, fade and terminal
    V2 = ME.dcf_policy().get('mode') == 'v2'; ctx = None
    if V2:
        _sp, _be = ME.prior_spot_beta(date, sym)
        mi_v2, _ = ME.apply_dcf_policy(mi, sym, spot if spot is not None else _sp, _be); ctx = mi_v2.get('_dcf')
        if len(mi_v2.get('years') or []) > ME.MAX_YEARS_NO_DCF: mi_v2 = dict(mi_v2, _allow_long=True)
        if ctx and len(mi['years']) >= ctx['fade_to_year']: ctx = None  # nothing to fade (legacy 10-year sets)
        ME.run_set(mi_v2, as_of)
    else: ME.run_set(mi, as_of)  # refuse to build a sheet for a set the engine rejects
    # 2026-10-26 (researched-name Sheets): what the formulas below cannot rebuild comes from the engine run of each tab's
    # deltas: driver-led scenario shifts (model_drivers resolves a scenario's driver deltas into line items), explicit
    # sum-of-the-parts parts that are not metric x multiple, rNPV / NAV / REIT legs and equity stakes.
    mi_e = mi_v2 if V2 else mi
    raw = ME.extract(sup)
    def eng_out(deltas):
        import copy as _c
        return ME.value(mi_e, as_of, None, _c.deepcopy(deltas) if deltas else None)
    def drv_shift(dd):
        """Line-item changes a scenario's driver deltas cause, keyed as the Calc tab uses them; unhandled paths listed."""
        import copy as _c
        if not dd or not raw.get('drivers'): return {}, []
        b0 = ME.resolve_drivers(_c.deepcopy(raw)); b1 = ME.resolve_drivers(_c.deepcopy(raw), _c.deepcopy(dd)); out, bad = {}, []
        def walk(x0, x1, path):
            if isinstance(x1, dict):
                for k_, v_ in x1.items():
                    if k_ in ('inputs_detail', 'reference', 'guidance_check', 'drivers', 'driver_build', 'scenarios', 'key_drivers', '_quarters', 'quarters'): continue
                    walk(x0.get(k_) if isinstance(x0, dict) else None, v_, path + (k_,))
            elif isinstance(x1, list):
                for i_, v_ in enumerate(x1): walk(x0[i_] if isinstance(x0, list) and i_ < len(x0) else None, v_, path + (i_,))
            elif isinstance(x1, (int, float)) and not isinstance(x1, bool):
                dv = x1 - (x0 if isinstance(x0, (int, float)) and not isinstance(x0, bool) else 0)
                if abs(dv) < 1e-12: return
                if path[0] == 'years' and len(path) == 3: out[('y', path[1], path[2])] = dv
                elif path[0] == 'segment_build' and path[-3:-2] == ('years',) or (path[0] == 'segment_build' and 'years' in path):
                    k_ = path[path.index('years') - 1]; out[('sg', k_, path[path.index('years') + 1], path[-1])] = dv
                elif path[0] == 'segment_build' and path[-1] == 'base_revenue': out[('sbase', path[-2])] = dv
                elif path[0] == 'horizon_multiples' and path[-1] in ('eps', 'fcf_per_share', 'value'): out[('hm', path[1], path[-1])] = dv
                elif path[0] == 'sotp' and path[1] == 'parts' and path[-1] in ('metric_value', 'value', 'multiple'): out[('sp', path[2], path[-1])] = dv
                elif path[0] == 'adjustments' and path[-1] == 'value': out[('adj', path[1])] = dv
                elif path[0] == 'segments' and path[-1] in ('ntm_metric', 'multiple'): out[('seg', path[1], path[-1])] = dv
                elif path == ('balance', 'net_debt_horizon'): out[('ndh',)] = dv
                else: bad.append('.'.join(map(str, path)))
        walk(b0, b1, ())
        return out, bad
    sop = (mi.get('sotp') or {}).get('parts') or []
    ENG_LEGS = [k for k in eng_out(None)['legs'] if k in ('rnpv', 'nav', 'p_affo', 'reit_nav')]
    fx = {}
    if os.path.exists(f'{rd}/facts.json'):
        _fp = json.load(open(f'{rd}/facts.json'))
        # 2026-10-26 (CCJ): a non-USD filer's XBRL actuals (CAD) must not sit beside USD projections; no history columns then
        if (_fp.get('currency') or 'USD') == 'USD' or mi.get('history_currency') == _fp.get('currency'):
            for x in _fp.get('facts', []): fx[x['key']] = x
    hy = sorted({int(k[-4:]) for k in fx if re.fullmatch(r'revenue_fy\d{4}', k)})[-3:]
    nh = len(hy); yrs = mi['years']; N = len(yrs); unit = mi.get('unit', '$M')
    scale = 1e6 if unit.upper() in ('$M', 'USD M', 'M') else (1e9 if unit.upper() in ('$B', 'B') else 1.0)
    rs = json.load(open(f'{rd}/result.json')) if os.path.exists(f'{rd}/result.json') else {}
    # downside (P10) and upside (P90) cases; the base case is the reconciled set itself (Kilian 2026-10-30), so no base Calc tab
    sc = {ME.SCEN_ALIAS.get(k, k): v for k, v in (mi.get('scenarios') or {}).items() if ME.SCEN_ALIAS.get(k, k) != 'base'}
    sc = {k: sc[k] for k in ('downside', 'upside') if k in sc}
    wb = openpyxl.Workbook(); S = wb.active; S.title = 'Summary'
    M = wb.create_sheet('Model'); V = wb.create_sheet('Valuation'); SE = wb.create_sheet('Sensitivity')
    D = wb.create_sheet('Distribution')
    CFN = 'Calc - research target (12m)'; CF = f"'{CFN}'"
    calc_names = {k: 'Calc - ' + re.sub(r'[^A-Za-z0-9 ]', '', k).strip().lower()[:16] for k in sc}
    for nm in [CFN] + list(calc_names.values()): wb.create_sheet(nm)
    G = wb.create_sheet('Agents'); R_ = wb.create_sheet('Sources')
    A = {}; IN = 'Valuation'

    # ================= Valuation: assumptions (inputs, blue) =================
    b, bal, sh, tm, dr = mi['base'], mi.get('balance', {}), mi.get('shares', {}), mi.get('terminal', {}) or {}, mi.get('discount_rate') or {}
    w_dcf_ = (mi.get('dcf_weight', 0.0) or 0.0) if (ME.dcf_allowed(sym) and not V2) else 0.0  # Kilian 2026-10-21: no DCF unless allowed (dcf_policy)
    if ctx:  # v2: the script's rate and weight, a steady-state terminal at 3%
        w_dcf_ = ctx['weight']; dr = dict(wacc=ctx['wacc'], basis='set by model_engine.py (fixed DCF, Kilian 2026-10-21 08:05Z): ' + ctx['basis'])
        tm = dict(method='steady_state', growth=ctx['g'])
    NODCF = not ctx and not w_dcf_  # Kilian 2026-10-30 17:58Z 'skip dcf if weight 0': no DCF build, grid or DCF checks on the sheet
    if dr.get('wacc') is None: dr = dict(dr, wacc=0.10, basis='not used: no DCF leg (Kilian 2026-10-21); a placeholder so the DCF block still computes')
    bal_asof = bal.get('as_of', as_of); sh_asof = sh.get('as_of', bal_asof)
    hms = mi.get('horizon_multiples') or ([mi['horizon_multiple']] if mi.get('horizon_multiple') else [])
    segs, adjs, cus = mi.get('segments') or [], mi.get('adjustments') or [], mi.get('custom') or []
    # segment driver build (model_engine.seg_build, 2026-10-19): per-segment growth and margin; revenue and EBIT are the sums
    # plus years[].corporate_ebit. Empty for sets without segment_build, which keep the layout below unchanged.
    sbl = mi.get('segment_build') or []
    sbl = (sbl.get('segments') or []) if isinstance(sbl, dict) else sbl
    n_legs = 1 + len(hms) + (1 if segs else 0) + len(cus) + (1 if sop else 0) + len(ENG_LEGS)
    v0 = 4 + n_legs + 2 + 6 + 3 + 22 + 3  # rows taken by the derivation, EPS basis and DCF blocks above the assumptions
    scal = [('as_of', 'Run date (valuation as of)', d(as_of), F_D, True, ''),
            ('H', 'Target date (run date + 365 days)', f'=B{v0 + 1}+365', F_D, False, '12-month horizon'),
            ('unit', 'Money unit', unit, None, True, ''),
            ('ebit_basis', 'EBIT basis (gaap / adjusted)', (mi.get('ebit_basis') or 'gaap').split(' ')[0], None, True, 'gaap = after stock comp'),
            ('sbc_t', 'Stock comp treatment (cost / dilution)', mi.get('sbc_treatment', 'cost'), None, True, 'used only when EBIT is adjusted'),
            ('fy_end', 'Last reported fiscal year end', d(b['fy_end']), F_D, True, ''),
            ('rev0', f'Net sales, last reported year ({unit})', b['revenue'], F_M, True, ''),
            ('wacc', 'WACC', dr['wacc'], F_PCT, True, dr.get('basis', '')),
            ('tmeth', 'Terminal method (gordon / exit_multiple / steady_state)', tm.get('method', 'gordon'), None, not ctx, 'steady state set by the engine (fixed DCF)' if ctx else ''),
            ('g', 'Terminal growth', tm.get('growth', 0.02), F_PCT, True, ''),
            ('exit', 'Exit EV/EBITDA (exit_multiple only)', num(tm.get('exit_ev_ebitda')), F_X, True, ''),
            ('bal_asof', 'Balance-sheet date', d(bal_asof), F_D, True, ''),
            ('nd', f'Net debt / (net cash) at balance date ({unit})', bal['net_debt'], F_M, True, 'excludes operating leases'),
            ('minor', 'Minority interests + preferred stock', bal.get('minorities', 0) + bal.get('preferred', 0), F_M, True,
             f"minorities {bal.get('minorities', 0):,.0f} + preferred {bal.get('preferred', 0):,.0f}" if bal.get('preferred') else ''),
            ('ir', 'Interest rate on net debt / cash', bal.get('interest_rate', 0.05), F_PCT, True, ''),
            ('ndh', 'Net debt at target date (override; blank = bridge)', num(bal.get('net_debt_horizon')), F_M, True, ''),
            ('sh0', 'Diluted shares (M)', sh.get('diluted_now'), '#,##0.0', True, ''),
            ('sh_asof', 'Share count date', d(sh_asof), F_D, True, ''),
            ('shchg', 'Annual change in diluted shares', sh.get('annual_change', 0), F_PCT, True, 'net of buybacks and stock-comp dilution'),
            ('shh', 'Shares at target date (override; blank = from annual change)', num(sh.get('horizon')), '#,##0.0', True, ''),
            ('w_dcf', 'DCF weight', w_dcf_, F_PCT, True, (f"fixed DCF weight ({ctx['weight_why']}); the other methods share the rest in their own proportions" if ctx else 'Kilian 2026-10-21: 0 unless the name has a DCF leg (dcf_policy)')),
            ('w_sotp', 'Sum-of-the-parts weight', mi.get('sotp_weight', 0), F_PCT, True, ''),
            ('sv', 'Spread on a side with no downside/upside case (lognormal vol)', mi.get('scenario_vol', 0.15), F_PCT, True, ''),
            ('spot', 'Share price at the run', spot, F_PS, True, f'close used by the run ({date})')]
    fin_t = ME.financing_expected(mi.get('financing_events'), str(dt.date.fromisoformat(str(as_of)[:10]) + dt.timedelta(days=365)))[0] if mi.get('financing_events') else None
    if fin_t:  # probable financing events at expected value (model_engine.financing_expected, 2026-10-29); probability-weighted totals
        fn = '; '.join(f"{e.get('name')} p{e.get('probability', 0):.0%}" for e in mi.get('financing_events') or [])
        scal += [('fin_sh', 'Expected financing: shares added by target date (M)', fin_t['shares'], '#,##0.0', True, fn),
                 ('fin_nd', f'Expected financing: change in net debt at target date ({unit})', fin_t['net_debt'], F_M, True, 'probability x each event'),
                 ('fin_pf', f'Expected financing: preferred added ({unit})', fin_t['preferred'], F_M, True, ''),
                 ('fin_c', f'Expected financing: annual after-tax cost to common ({unit})', fin_t['annual_cost'], F_M, True, 'preferred dividends, coupon above the set rate')]
    if ctx: scal += [('ronic', 'Return on new capital after the fade (RONIC; floored at the WACC)', ctx['ronic'], F_PCT, True, 'tested default 15% unless the set argues another figure'),
                     ('fadeT', 'DCF fades the build to year', ctx['fade_to_year'], None, False, 'growth to the terminal rate by this year; capex to the steady state')]
    section(V, v0 - 1, 'Assumptions (blue = input; sources on the Sources tab)', 9)
    header(V, v0, ['Input', 'Value', 'Basis / note'])
    for k, (key, lab, v, fmt, inp, note) in enumerate(scal):
        r = v0 + 1 + k; put(V, f'A{r}', lab); put(V, f'B{r}', v, fmt, BLUE if inp else None); put(V, f'C{r}', note)
        A[key] = f'{IN}!$B${r}'
    r = v0 + 1 + len(scal) + 1
    header(V, r, ['Target multiple', 'Multiple', 'Weight', 'Basis (ntm / ltm)', 'EBITDA basis', 'Stated EPS', 'Stated FCF/share', 'Levered', 'Peer basis'])
    A['hm_rows'] = []
    for j, hm in enumerate(hms):
        rr = r + 1 + j; A['hm_rows'].append(rr)
        w = hm.get('weight'); w = w if w is not None else f'=(1-{A["w_dcf"]})/{len(hms)}'
        vals = [hm['metric'], hm['value'], w, hm.get('basis', 'ntm'), hm.get('ebitda', 'gaap'), num(hm.get('eps')),
                num(hm.get('fcf_per_share')), bool(hm.get('levered', True)), hm.get('peer_basis', '')]
        fm = [None, F_X if hm['metric'] != 'FCF yield' else F_PCT, F_PCT, None, None, F_EPS, F_EPS, None, None]
        for c, (v, f) in enumerate(zip(vals, fm)): put(V, f'{L(1 + c)}{rr}', v, f, BLUE if c < 8 else None)
    r = r + max(len(hms), 1) + 2
    header(V, r, ['Segment (sum of the parts)', 'NTM metric', 'Metric', 'Multiple', 'Note']); A['seg_rows'] = []
    for j, s in enumerate(segs):
        rr = r + 1 + j; A['seg_rows'].append(rr)
        for c, (v, f) in enumerate(zip([s.get('name', ''), s['ntm_metric'], s.get('metric', ''), s['multiple'], s.get('note', '')], [None, F_M, None, F_X, None])):
            put(V, f'{L(1 + c)}{rr}', v, f, BLUE if c < 4 else None)
    if not segs: put(V, f'A{r + 1}', 'none', font=ITAL)
    r = r + max(len(segs), 1) + 2
    header(V, r, ['Adjustment (equity level)', 'Value', 'Probability', 'Note']); A['adj_rows'] = []
    for j, s in enumerate(adjs):
        rr = r + 1 + j; A['adj_rows'].append(rr)
        for c, (v, f) in enumerate(zip([s.get('name', ''), s.get('value', 0), s.get('probability', 1), s.get('note', '')], [None, F_M, F_PCT, None])):
            put(V, f'{L(1 + c)}{rr}', v, f, BLUE if c < 3 else None)
    if not adjs: put(V, f'A{r + 1}', 'none', font=ITAL)
    r = r + max(len(adjs), 1) + 2
    header(V, r, ['Custom method', 'Value per share', 'Weight', 'Explanation']); A['cus_rows'] = []
    for j, s in enumerate(cus):
        rr = r + 1 + j; A['cus_rows'].append(rr)
        for c, (v, f) in enumerate(zip([s['name'], s['value_per_share'], s.get('weight', 0), s.get('explanation', '')], [None, F_EPS, F_PCT, None])):
            put(V, f'{L(1 + c)}{rr}', v, f, BLUE if c < 3 else None)
    if not cus: put(V, f'A{r + 1}', 'none', font=ITAL)
    r = r + max(len(cus), 1) + 2
    A['sp_rows'] = []
    if sop:  # explicit sum of the parts (model_engine sotp): metric x multiple parts are live; other methods show the engine's value
        so_ = mi['sotp']; b0_ = eng_out(None)['legs'].get('sotp_parts', {}).get('parts', {})
        header(V, r, ['Sum of the parts (explicit parts)', 'Method', 'Level (ev / equity)', 'Metric', 'Multiple', 'Ownership',
                      'Value (engine, other methods)', 'Note'])
        for j, p_ in enumerate(sop):
            rr = r + 1 + j; A['sp_rows'].append(rr); mth = p_.get('method', 'multiple')
            ev_ = (b0_.get(p_['name']) or {}).get('value')
            vals = [p_['name'], mth, p_.get('level', 'ev'), num(p_.get('metric_value')), num(p_.get('multiple')), p_.get('ownership', 1.0),
                    (p_.get('value') if mth == 'value' else (ev_ / p_.get('ownership', 1.0) if ev_ is not None and mth != 'multiple' else None)),
                    (p_.get('note') or '')[:400]]
            fm = [None, None, None, F_M, F_X, F_PCT, F_M, None]
            for c, (v, f) in enumerate(zip(vals, fm)): put(V, f'{L(1 + c)}{rr}', v, f, BLUE if c in (3, 4, 5) or (c == 6 and mth == 'value') else None)
        rr = r + 1 + len(sop); put(V, f'A{rr}', 'Holding-company discount'); put(V, f'B{rr}', so_.get('holdco_discount', 0.0), F_PCT, BLUE); A['sp_hd'] = f'{IN}!$B${rr}'
        put(V, f'A{rr + 1}', 'Sum-of-the-parts weight'); put(V, f'B{rr + 1}', so_.get('weight', 0), F_PCT, BLUE); A['sp_w'] = f'{IN}!$B${rr + 1}'
        put(V, f'C{rr}', 'Cash-flow, DCF, rNPV and NAV parts are valued by model_engine.py for each scenario (Calc tabs show the scenario value); the rest are live.', font=GREY)
        r = rr + 3
    DK = [('revenue_growth', 'Growth Δ', F_PCT), ('ebit_margin', 'Margin Δ', F_PCT), ('multiple', 'Multiple Δ (turns)', '0.0'),
          ('wacc', 'WACC Δ', F_PCT), ('multiple_pct', 'Multiple %Δ (FCF yield, segments)', F_PCT), ('eps_pct', 'Stated EPS/FCF %Δ', F_PCT),
          ('custom_pct', 'Custom methods %Δ', F_PCT)]
    header(V, r, ['Case', 'Percentile'] + [x[1] for x in DK] + ['Story']); A['sc_rows'] = {}
    for j, (k, s) in enumerate(sc.items()):
        rr = r + 1 + j; A['sc_rows'][k] = rr; put(V, f'A{rr}', k)
        put(V, f'B{rr}', ME.SCEN_PCTL[k] / 100, F_PCT)
        for c, (dk, _, f) in enumerate(DK): put(V, f'{L(3 + c)}{rr}', (s.get('deltas') or {}).get(dk), f, BLUE)
        put(V, f'{L(3 + len(DK))}{rr}', s.get('story', ''), wrap=True)
        if (s.get('deltas') or {}).get('drivers'):  # driver-led set: the scenario moves its drivers (Calc tab shows the resolved line-item shifts)
            put(V, f'{L(4 + len(DK))}{rr}', 'driver deltas: ' + '; '.join(f'{k_} {"+" if (v_.get("pct", v_.get("add", 0)) or 0) >= 0 else ""}{v_.get("pct", v_.get("add"))}{" %" if "pct" in v_ else ""}' for k_, v_ in s['deltas']['drivers'].items() if isinstance(v_, dict)), wrap=True)
    A['sb_rows'] = []
    if sbl:  # segment build: base revenue per segment (growth and margin by year are on the Model tab)
        r = r + max(len(sc), 1) + 2
        header(V, r, ['Segment build (segment_build)', f'Base revenue ({unit})', 'Driver note (growth and EBIT margin by year: Model tab)'])
        for j, s in enumerate(sbl):
            rr = r + 1 + j; A['sb_rows'].append(rr)
            put(V, f'A{rr}', s.get('name', f'Segment {j + 1}')); put(V, f'B{rr}', s['base_revenue'], F_M, BLUE); put(V, f'C{rr}', s.get('driver_note', ''))
        rr = r + 1 + len(sbl); A['sb_sum'] = f'{IN}!$B${rr}'
        put(V, f'A{rr}', 'Sum of segment base revenues', font=BOLD); put(V, f'B{rr}', f'=SUM(B{r + 1}:B{rr - 1})', F_M, BOLD)
        put(V, f'C{rr}', f'=IF(ABS(B{rr}/{A["rev0"]}-1)<=0.02,"ties to the last reported net sales (within 2%)","FLAG: does not tie to the last reported net sales")')
    V.column_dimensions['A'].width = 52; V.column_dimensions['B'].width = 14; V.column_dimensions['C'].width = 14
    for c in range(4, 12): V.column_dimensions[L(c)].width = 13
    V.column_dimensions['J'].width = 60

    # ================= Model: annual P&L and cash flow =================
    PC0 = 2 + nh; lastP = L(PC0 + N - 1); SRC = L(PC0 + N); hcol = lambda j: L(2 + j); pcol = lambda i: L(PC0 + i)
    seg_plan = []
    if sbl:
        seg_plan = [('h_seg', 'sec', 'Segment build (blue = input: revenue growth and EBIT margin by segment and year)')]
        for k, s in enumerate(sbl):
            seg_plan += [(f'seg{k}_rev', 'line', s.get('name', f'Segment {k + 1}')), (f'seg{k}_g', 'pct', '  Revenue growth'),
                         (f'seg{k}_ebit', 'line', '  Segment EBIT'), (f'seg{k}_m', 'pct', '  EBIT margin')]
        seg_plan += [('seg_rev', 'line', 'Total segment revenue (= net sales)'), ('seg_ebit', 'line', 'Total segment EBIT'),
                     ('corp', 'line', 'Corporate / unallocated EBIT (blue = input)')]
    plan = seg_plan + [('h_sales', 'sec', 'Sales'), ('sales', 'line', 'Net sales'), ('sales_yoy', 'pct', '  % YoY'),
            ('h_op', 'sec', 'Operating profit'), ('ebit', 'line', 'EBIT (operating income)'), ('ebit_m', 'pct', '  EBIT margin'),
            ('da', 'line', 'D&A'), ('ebitda', 'line', 'EBITDA'), ('ebitda_m', 'pct', '  EBITDA margin'),
            ('netint', 'line', 'Net interest income / (expense)'), ('pretax', 'line', 'Pretax income'), ('pretax_m', 'pct', '  Pretax margin'),
            ('tax', 'line', 'Income tax'), ('tax_r', 'pct', '  Effective tax rate'), ('ni', 'line', 'Net income'), ('ni_yoy', 'pct', '  % YoY'),
            ('shares', 'line', 'Diluted shares (M, average)'), ('shares_yoy', 'pct', '  % YoY'), ('eps', 'eps', 'Diluted EPS'), ('eps_yoy', 'pct', '  % YoY'),
            ('h_cf', 'sec', 'Cash flow'), ('nopat', 'line', 'NOPAT (EBIT x (1 - tax))'), ('da2', 'line', '+ D&A'), ('capex', 'line', '- Capex'),
            ('capex_s', 'pct', '  Capex % of sales'), ('dnwc', 'line', '- Working capital increase'), ('sbc_c', 'line', '- Stock comp (adjusted EBIT only)'),
            ('fcff', 'line', 'Free cash flow to firm'), ('cfo', 'line', 'Operating cash flow (reported)'), ('fcf_h', 'line', 'FCF (operating cash flow - capex)'),
            ('sbc', 'line', 'Stock comp (memo)'), ('returns', 'line', 'Buybacks + dividends + other uses'), ('returns_s', 'pct', '  % of sales'),
            ('nd', 'line', 'Net debt / (net cash), year end (memo)'),
            ('h_drv', 'sec', 'Drivers (blue = input; a year left black carries the prior year)')]
    plan += [('drv_' + k, 'drv', lab) for k, lab, _ in DRIVERS]
    plan += [('h_ref', 'sec', 'Versus company guidance and street consensus')]
    R = {}; r = 9
    for key, kind, lab in plan:
        if kind == 'sec': r += 1
        R[key] = r; r += 1
    ref_row0 = r
    put(M, 'A1', f'{sym}: annual model, base case', font=TITLE)
    put(M, 'A2', f'({unit[1:] if unit.startswith("$") else unit} of US dollars except per share; fiscal years named by the calendar year they end in)', font=GREY)
    put(M, 'A3', 'Blue = input · black = formula · green = link to another tab · actuals from SEC filings (fact ids in the last column, links on Sources)', font=GREY)
    header(M, 5, ['Fiscal year'] + [f'FY{y}A' for y in hy] + [f'{y.get("label", f"Y{i + 1}")}E' for i, y in enumerate(yrs)] + ['Source / note'])
    put(M, 'A6', 'Period end'); put(M, 'A7', 'Projection year')
    for j, y in enumerate(hy):
        e = (fx.get(f'revenue_fy{y}') or {}).get('end'); put(M, f'{hcol(j)}6', d(e) if e else None, F_D); put(M, f'{hcol(j)}7', j - nh + 1)
    for i in range(N):
        c = pcol(i); put(M, f'{c}6', f'=IF(DAY({A["fy_end"]}+1)=1,EOMONTH({A["fy_end"]},12*{c}7),EDATE({A["fy_end"]},12*{c}7))', F_D); put(M, f'{c}7', i + 1)
    for key, kind, lab in plan:
        rr = R[key]
        if kind == 'sec': section(M, rr, lab, PC0 + N); continue
        put(M, f'A{rr}', lab, font=ITAL if kind == 'pct' else (BOLD if key in ('sales', 'pretax', 'ni', 'eps', 'fcff') else None))
    # history values (SEC facts)
    hfacts = {'sales': 'revenue', 'da': 'd_and_a', 'pretax': 'pretax_income', 'tax': 'income_tax', 'ni': 'net_income', 'shares': 'diluted_shares_wavg',
              'eps': 'eps_diluted', 'capex': 'capex', 'cfo': 'cfo', 'sbc': 'sbc'}
    for key, fk in hfacts.items():
        ids = []
        for j, y in enumerate(hy):
            f = fx.get(f'{fk}_fy{y}')
            if not f: continue
            v = f['value'] if key == 'eps' else f['value'] / scale
            put(M, f'{hcol(j)}{R[key]}', v, F_EPS if key == 'eps' else F_M); ids.append(f['id'])
        if key == 'da': [put(M, f'{hcol(j)}{R["da2"]}', f'={hcol(j)}{R["da"]}', F_M) for j in range(nh)]
        if ids: put(M, f'{SRC}{R[key]}', 'SEC XBRL ' + ', '.join(ids), font=GREY)
    rets = []
    for j, y in enumerate(hy):
        bb, dv_ = fx.get(f'buybacks_fy{y}'), fx.get(f'dividends_paid_fy{y}')
        if bb and dv_: put(M, f'{hcol(j)}{R["returns"]}', (bb['value'] + dv_['value']) / scale, F_M); rets += [bb['id'], dv_['id']]
    if rets: put(M, f'{SRC}{R["returns"]}', 'SEC XBRL ' + ', '.join(rets), font=GREY)
    for j in range(nh):
        c, p = hcol(j), (hcol(j - 1) if j else None)
        if p: put(M, f'{c}{R["sales_yoy"]}', f'=IF(OR({c}{R["sales"]}="",{p}{R["sales"]}="",{p}{R["sales"]}=0),"",{c}{R["sales"]}/{p}{R["sales"]}-1)', F_PCT, ITAL)
        put(M, f'{c}{R["pretax_m"]}', f'={c}{R["pretax"]}/{c}{R["sales"]}', F_PCT, ITAL)
        put(M, f'{c}{R["tax_r"]}', f'={c}{R["tax"]}/{c}{R["pretax"]}', F_PCT, ITAL)
        if p: put(M, f'{c}{R["ni_yoy"]}', f'=IF(OR({c}{R["ni"]}="",{p}{R["ni"]}="",{p}{R["ni"]}=0),"",{c}{R["ni"]}/{p}{R["ni"]}-1)', F_PCT, ITAL)
        if p: put(M, f'{c}{R["shares_yoy"]}', f'=IF(OR({c}{R["shares"]}="",{p}{R["shares"]}="",{p}{R["shares"]}=0),"",{c}{R["shares"]}/{p}{R["shares"]}-1)', F_PCT, ITAL)
        if p: put(M, f'{c}{R["eps_yoy"]}', f'=IF(OR({c}{R["eps"]}="",{p}{R["eps"]}="",{p}{R["eps"]}=0),"",{c}{R["eps"]}/{p}{R["eps"]}-1)', F_PCT, ITAL)
        put(M, f'{c}{R["capex_s"]}', f'={c}{R["capex"]}/{c}{R["sales"]}', F_PCT, ITAL)
        put(M, f'{c}{R["fcf_h"]}', f'={c}{R["cfo"]}-{c}{R["capex"]}', F_M)
        put(M, f'{c}{R["returns_s"]}', f'={c}{R["returns"]}/{c}{R["sales"]}', F_PCT, ITAL)
        # actual ratios on the driver rows, for comparison
        if p: put(M, f'{c}{R["drv_revenue_growth"]}', f'={c}{R["sales_yoy"]}', F_PCT)
        put(M, f'{c}{R["drv_tax_rate"]}', f'={c}{R["tax_r"]}', F_PCT)
        put(M, f'{c}{R["drv_da_pct_rev"]}', f'={c}{R["da"]}/{c}{R["sales"]}', F_PCT)
        put(M, f'{c}{R["drv_capex_pct_rev"]}', f'={c}{R["capex_s"]}', F_PCT)
        put(M, f'{c}{R["drv_sbc_pct_rev"]}', f'={c}{R["sbc"]}/{c}{R["sales"]}', F_PCT)
        put(M, f'{c}{R["drv_shareholder_returns_pct_rev"]}', f'={c}{R["returns_s"]}', F_PCT)
    # drivers (blue where stated, carried otherwise)
    for key, lab, fmt in DRIVERS:
        rr = R['drv_' + key]
        for i, y in enumerate(yrs):
            c = pcol(i)
            if key in y: put(M, f'{c}{rr}', y[key], fmt, BLUE)
            elif i > 0: put(M, f'{c}{rr}', f'={pcol(i - 1)}{rr}', fmt)
            else: put(M, f'{c}{rr}', 0, fmt, BLUE)
    dvm = lambda key, i: f'Model!{pcol(i)}{R["drv_" + key]}'
    tsh = f'(({A["sh_asof"]}-{A["fy_end"]})/365.25)'
    for i in range(N):
        c = pcol(i); p = pcol(i - 1) if i else None
        prev_s = f'{p}{R["sales"]}' if p else A['rev0']; prev_nd = f'{p}{R["nd"]}' if p else A['nd']
        g = f'{c}{R["drv_revenue_growth"]}'
        F = {'sales': f'={prev_s}*(1+{g})', 'sales_yoy': f'={c}{R["sales"]}/{prev_s}-1',
             'ebit': f'={c}{R["sales"]}*{c}{R["drv_ebit_margin"]}', 'ebit_m': f'={c}{R["ebit"]}/{c}{R["sales"]}',
             'da': f'={c}{R["sales"]}*{c}{R["drv_da_pct_rev"]}', 'ebitda': f'={c}{R["ebit"]}+{c}{R["da"]}', 'ebitda_m': f'={c}{R["ebitda"]}/{c}{R["sales"]}',
             'netint': f'=-{A["ir"]}*{prev_nd}', 'pretax': f'={c}{R["ebit"]}+{c}{R["netint"]}', 'pretax_m': f'={c}{R["pretax"]}/{c}{R["sales"]}',
             'tax': f'={c}{R["pretax"]}*{c}{R["drv_tax_rate"]}', 'tax_r': f'={c}{R["tax"]}/{c}{R["pretax"]}',
             'ni': f'={c}{R["pretax"]}-{c}{R["tax"]}', 'ni_yoy': f'=IF(OR({(p or hcol(nh - 1))}{R["ni"]}="",{(p or hcol(nh - 1))}{R["ni"]}=0),"",{c}{R["ni"]}/{(p or hcol(nh - 1))}{R["ni"]}-1)',
             'shares': f'={A["sh0"]}*(1+{A["shchg"]})^({c}7-0.5-{tsh})', 'shares_yoy': f'=IF(OR({(p or hcol(nh - 1))}{R["shares"]}="",{(p or hcol(nh - 1))}{R["shares"]}=0),"",{c}{R["shares"]}/{(p or hcol(nh - 1))}{R["shares"]}-1)',
             'eps': f'={c}{R["ni"]}/{c}{R["shares"]}', 'eps_yoy': f'=IF(OR({(p or hcol(nh - 1))}{R["eps"]}="",{(p or hcol(nh - 1))}{R["eps"]}=0),"",{c}{R["eps"]}/{(p or hcol(nh - 1))}{R["eps"]}-1)',
             'nopat': f'={c}{R["ebit"]}*(1-{c}{R["drv_tax_rate"]})', 'da2': f'={c}{R["da"]}',
             'capex': f'={c}{R["sales"]}*{c}{R["drv_capex_pct_rev"]}', 'capex_s': f'={c}{R["capex"]}/{c}{R["sales"]}',
             'dnwc': f'={c}{R["drv_nwc_pct_incr_rev"]}*({c}{R["sales"]}-{prev_s})', 'sbc': f'={c}{R["sales"]}*{c}{R["drv_sbc_pct_rev"]}',
             'sbc_c': f'=IF(AND({A["ebit_basis"]}="adjusted",{A["sbc_t"]}="cost"),{c}{R["sbc"]}*(1-{c}{R["drv_tax_rate"]}),0)',
             'fcff': f'={c}{R["nopat"]}+{c}{R["da2"]}-{c}{R["capex"]}-{c}{R["dnwc"]}-{c}{R["sbc_c"]}',
             'returns': f'={c}{R["sales"]}*{c}{R["drv_shareholder_returns_pct_rev"]}+{c}{R["drv_shareholder_returns"]}+{c}{R["drv_other_uses"]}',
             'returns_s': f'={c}{R["returns"]}/{c}{R["sales"]}',
             'nd': f'={prev_nd}-{c}{R["fcff"]}-{c}{R["netint"]}*(1-{c}{R["drv_tax_rate"]})+{c}{R["returns"]}'}
        if sbl: F['sales'] = f'={c}{R["seg_rev"]}'; F['ebit'] = f'={c}{R["seg_ebit"]}+{c}{R["corp"]}'
        if nh == 0:
            for k in ('ni_yoy', 'shares_yoy', 'eps_yoy'):
                if i == 0: F.pop(k)
        for k, f in F.items():
            fmt = F_PCT if k.endswith(('_yoy', '_m', '_r', '_s')) else (F_EPS if k == 'eps' else ('#,##0.0' if k == 'shares' else F_M))
            put(M, f'{c}{R[k]}', f, fmt, ITAL if fmt == F_PCT else (BOLD if k in ('sales', 'pretax', 'ni', 'eps', 'fcff') else None))
    if sbl:  # segment build (model_engine.seg_build): segment revenue = prior x (1 + growth), EBIT = revenue x margin
        lh = hcol(nh - 1) if nh else None
        for k, s in enumerate(sbl):
            ys = s.get('years') or []; base_ = f'{IN}!$B${A["sb_rows"][k]}'
            if lh: put(M, f'{lh}{R[f"seg{k}_rev"]}', f'={base_}', F_M, GREEN)
            for i in range(N):
                c = pcol(i); p = pcol(i - 1) if i else None; y = ys[i] if i < len(ys) else {}
                for key, rk in (('revenue_growth', 'g'), ('ebit_margin', 'm')):  # a year inherits what it leaves out
                    rr = R[f'seg{k}_{rk}']
                    if key in y: put(M, f'{c}{rr}', y[key], F_PCT, BLUE)
                    elif i > 0: put(M, f'{c}{rr}', f'={p}{rr}', F_PCT)
                    else: put(M, f'{c}{rr}', 0, F_PCT, BLUE)
                put(M, f'{c}{R[f"seg{k}_rev"]}', f'={(p + str(R[f"seg{k}_rev"])) if p else base_}*(1+{c}{R[f"seg{k}_g"]})', F_M)
                put(M, f'{c}{R[f"seg{k}_ebit"]}', f'={c}{R[f"seg{k}_rev"]}*{c}{R[f"seg{k}_m"]}', F_M)
        for c in ([lh] if lh else []) + [pcol(i) for i in range(N)]:
            put(M, f'{c}{R["seg_rev"]}', '=' + '+'.join(f'{c}{R[f"seg{k}_rev"]}' for k in range(len(sbl))), F_M, BOLD)
        for i in range(N):
            c = pcol(i); y = yrs[i]
            put(M, f'{c}{R["seg_ebit"]}', '=' + '+'.join(f'{c}{R[f"seg{k}_ebit"]}' for k in range(len(sbl))), F_M, BOLD)
            if 'corporate_ebit' in y: put(M, f'{c}{R["corp"]}', y['corporate_ebit'], F_M, BLUE)
            elif i > 0: put(M, f'{c}{R["corp"]}', f'={pcol(i - 1)}{R["corp"]}', F_M)
            else: put(M, f'{c}{R["corp"]}', 0, F_M, BLUE)
            put(M, f'{c}{R["drv_revenue_growth"]}', f'={c}{R["sales_yoy"]}', F_PCT)  # implied by the build, not an input
            put(M, f'{c}{R["drv_ebit_margin"]}', f'={c}{R["ebit_m"]}', F_PCT)
        put(M, f'{SRC}{R["seg_rev"]}', 'base revenues on Valuation (segment build); net sales = this total', font=GREY)
        put(M, f'{SRC}{R["corp"]}', 'years[].corporate_ebit; EBIT = total segment EBIT + corporate', font=GREY)
        put(M, f'{SRC}{R["drv_revenue_growth"]}', 'implied by the segment build (edit the segment rows)', font=GREY)
        put(M, f'{SRC}{R["drv_ebit_margin"]}', 'implied by the segment build and corporate EBIT', font=GREY)
    put(M, f'{SRC}{R["netint"]}', 'rate x prior year-end net debt (memo roll)', font=GREY)
    put(M, f'{SRC}{R["shares"]}', 'diluted count rolled at the annual change to mid-year', font=GREY)
    put(M, f'{SRC}{R["nd"]}', 'rolled from the balance-date figure; feeds interest only', font=GREY)
    put(M, f'{SRC}{R["fcff"]}', 'what the DCF discounts (Calc tabs)', font=GREY)
    # vs guidance and consensus
    ref = mi.get('reference') or {}
    labs = {f'FY{y}': hcol(j) for j, y in enumerate(hy)}; labs.update({y.get('label', f'Y{i + 1}'): pcol(i) for i, y in enumerate(yrs)})
    mrow = {'eps diluted': 'eps', 'eps': 'eps', 'revenue': 'sales', 'revenue growth': 'sales_yoy', 'pretax margin': 'pretax_m',
            'tax rate': 'tax_r', 'ebit margin': 'ebit_m'}
    srcs = sup.get('sources') or []
    rr = ref_row0
    for kind in ('guidance', 'consensus'):
        for e in ref.get(kind) or []:
            col = labs.get(e.get('fy')); met = (e.get('metric') or '').lower(); mk = mrow.get(met)
            if not col: continue
            israte = met in ('revenue growth', 'pretax margin', 'tax rate', 'ebit margin', 'comparable sales growth')
            fmt = F_PCT if israte else (F_EPS if 'eps' in met else F_M)
            if kind == 'guidance':
                lo, hi = e.get('low'), e.get('high')
                put(M, f'A{rr}', f'Guidance: {e.get("metric")} ({e.get("basis", "")}), midpoint')
                put(M, f'{col}{rr}', f'=AVERAGE({lo},{hi})' if lo is not None and hi is not None else (lo if lo is not None else hi), fmt, Font(color='7030A0'))
                rng = f'{lo:.4g} to {hi:.4g}' if lo is not None and hi is not None else ''
            else:
                put(M, f'A{rr}', f'Consensus: {e.get("metric")} ({e.get("basis", "")})')
                put(M, f'{col}{rr}', e.get('value'), fmt, Font(color='C55A11')); rng = ''
            sid = ', '.join(str(i) for i in e.get('source_ids') or [])
            put(M, f'{SRC}{rr}', f'{rng + "; " if rng else ""}as of {e.get("as_of", "")}; source {sid}' + (f' ({srcs[e["source_ids"][0] - 1].get("title", "")[:60]})' if e.get('source_ids') and len(srcs) >= e['source_ids'][0] else ''), font=GREY)
            if mk:
                put(M, f'A{rr + 1}', '  Model vs ' + ('guidance midpoint' if kind == 'guidance' else 'consensus'), font=ITAL)
                put(M, f'{col}{rr + 1}', f'=({col}{R[mk]}-{col}{rr})*10000' if israte else f'={col}{R[mk]}/{col}{rr}-1', F_BP if israte else F_PCT, ITAL)
                if kind == 'guidance' and e.get('low') is not None and e.get('high') is not None:
                    put(M, f'{SRC}{rr + 1}', f'=IF({col}{R[mk]}<{e["low"]},"below the guided range",IF({col}{R[mk]}>{e["high"]},"above the guided range","inside the guided range"))', font=GREY)
                rr += 2
            else: rr += 1
    if rr == ref_row0: put(M, f'A{rr}', 'no guidance or consensus figures in this run\'s reference block', font=ITAL); rr += 1
    M.column_dimensions['A'].width = 46
    for c in range(2, PC0 + N): M.column_dimensions[L(c)].width = 12
    M.column_dimensions[SRC].width = 60; M.freeze_panes = 'B8'

    # ================= Calc tabs (engine machinery) =================
    lastc = L(2 + N)
    def calc(name, title, drefs, deltas=None):
        ws = wb[name]; C = {}
        shift, bad = drv_shift((deltas or {}).get('drivers'))
        if bad: print(f'WARNING {name}: driver deltas move inputs the sheet does not shift: {bad[:8]}')
        eo = eng_out(deltas); srow = [250]
        if shift: put(ws, 'A249', 'Driver scenario shifts: line-item changes model_drivers.py resolves from this scenario\'s driver deltas (added to the Model tab inputs)', font=BOLD)
        def sf(key, label):
            v = shift.get(key, 0.0)
            if abs(v) < 1e-12: return ''
            r__ = srow[0]; srow[0] += 1; put(ws, f'A{r__}', label); put(ws, f'B{r__}', v, '0.0000', BLUE)
            return f'+$B${r__}'
        put(ws, 'A1', f'{sym} engine calculation: {title}', font=TITLE)
        put(ws, 'A2', 'Machinery behind the Valuation and Distribution tabs; drivers come from Model, assumptions from Valuation.', font=GREY)
        labs_ = ['Growth Δ', 'Margin Δ', 'Multiple Δ (turns)', 'WACC Δ', 'Multiple %Δ', 'Stated EPS/FCF %Δ', 'Custom %Δ']
        for j, (lab, ref_) in enumerate(zip(labs_, drefs)):
            put(ws, f'A{4 + j}', 'Scenario ' + lab); put(ws, f'B{4 + j}', ref_, F_PCT if j != 2 else '0.0', GREEN if isinstance(ref_, str) else None)
        put(ws, 'A12', 'WACC'); put(ws, 'B12', f'={A["wacc"]}+B7', F_PCT)
        put(ws, 'A13', 'Years from last FY end to target date'); put(ws, 'B13', f'=({A["H"]}-{A["fy_end"]})/365.25', '0.000')
        put(ws, 'A14', 'Years from last FY end to balance date'); put(ws, 'B14', f'=({A["bal_asof"]}-{A["fy_end"]})/365.25', '0.000')
        put(ws, 'A15', 'Years from last FY end to share count date'); put(ws, 'B15', f'=({A["sh_asof"]}-{A["fy_end"]})/365.25', '0.000')
        put(ws, 'A16', 'Projected years'); put(ws, 'B16', N)
        header(ws, 18, ['Projection', 'Base'] + [f'=Model!{pcol(i)}5' for i in range(N)])
        rows = ['Year index', 'Period end', 'Revenue growth', 'Revenue', 'EBIT margin', 'EBIT', 'Tax rate', 'NOPAT', 'D&A', 'Capex',
                'Working capital increase', 'Stock comp', 'Free cash flow to firm', 'EBITDA', 'Adj. EBITDA',
                'Shareholder returns + other uses', 'Share of year after target date', 'PV at target date of FCFF',
                'Overlap: balance date to target date', 'NTM weight at target date', 'LTM weight at target date']
        for j, t in enumerate(rows): put(ws, f'A{19 + j}', t)
        put(ws, 'B19', 0); put(ws, 'B22', f'={A["rev0"]}', F_M)
        put(ws, 'B39', '=MAX(0,MIN(0,$B$13)-MAX(-1,$B$13-1))', '0.000')
        for i in range(N):
            c, p = L(3 + i), L(2 + i)
            put(ws, f'{c}19', i + 1)
            put(ws, f'{c}20', f'={A["fy_end"]}+ROUND(365.25*{c}19,0)', F_D)
            put(ws, f'{c}21', f'={dvm("revenue_growth", i)}+$B$4' + sf(('y', i, 'revenue_growth'), f'Revenue growth, year {i + 1}'), F_PCT)
            put(ws, f'{c}22', f'={p}22*(1+{c}21)', F_M)
            put(ws, f'{c}23', f'={dvm("ebit_margin", i)}+$B$5' + sf(('y', i, 'ebit_margin'), f'EBIT margin, year {i + 1}'), F_PCT)
            put(ws, f'{c}24', f'={c}22*{c}23', F_M)
            put(ws, f'{c}25', f'={dvm("tax_rate", i)}' + sf(('y', i, 'tax_rate'), f'Tax rate, year {i + 1}'), F_PCT)
            put(ws, f'{c}26', f'={c}24*(1-{c}25)', F_M)
            put(ws, f'{c}27', f'={c}22*({dvm("da_pct_rev", i)}{sf(("y", i, "da_pct_rev"), f"D&A % revenue, year {i + 1}")})', F_M)
            put(ws, f'{c}28', f'={c}22*({dvm("capex_pct_rev", i)}{sf(("y", i, "capex_pct_rev"), f"Capex % revenue, year {i + 1}")})', F_M)
            put(ws, f'{c}29', f'=({dvm("nwc_pct_incr_rev", i)}{sf(("y", i, "nwc_pct_incr_rev"), f"Working capital % of revenue increase, year {i + 1}")})*({c}22-{p}22)', F_M)
            put(ws, f'{c}30', f'={c}22*({dvm("sbc_pct_rev", i)}{sf(("y", i, "sbc_pct_rev"), f"Stock comp % revenue, year {i + 1}")})', F_M)
            put(ws, f'{c}31', f'={c}26+{c}27-{c}28-{c}29-IF(AND({A["ebit_basis"]}="adjusted",{A["sbc_t"]}="cost"),{c}30*(1-{c}25),0)', F_M)
            put(ws, f'{c}32', f'={c}24+{c}27', F_M)
            put(ws, f'{c}33', f'={c}24+{c}27+{c}22*({dvm("adj_ebitda_addback_pct_rev", i)}{sf(("y", i, "adj_ebitda_addback_pct_rev"), f"Adj. EBITDA addback % revenue, year {i + 1}")})', F_M)
            put(ws, f'{c}34', f'={c}22*{dvm("shareholder_returns_pct_rev", i)}+{dvm("shareholder_returns", i)}+{dvm("other_uses", i)}', F_M)
            put(ws, f'{c}35', f'=IF({c}19<=$B$13,0,{c}19-MAX({c}19-1,$B$13))', '0.000')
            put(ws, f'{c}36', f'=IF({c}35=0,0,{c}31*{c}35/(1+$B$12)^({c}19-$B$13))', F_M)
            put(ws, f'{c}37', f'=MAX(0,MIN({c}19,$B$13)-MAX({c}19-1,$B$14))', '0.000')
            put(ws, f'{c}38', f'=MAX(0,MIN({c}19,$B$13+1)-MAX({c}19-1,$B$13))', '0.000')
            put(ws, f'{c}39', f'=MAX(0,MIN({c}19,$B$13)-MAX({c}19-1,$B$13-1))', '0.000')
        if sbl:  # segment build (model_engine.seg_build): scenario growth / margin deltas reach every segment-year
            put(ws, 'A129', 'Segment build (model_engine.seg_build): revenue and EBIT are the segment sums plus corporate EBIT', font=BOLD)
            header(ws, 130, ['Segment', 'Base'] + [f'=Model!{pcol(i)}5' for i in range(N)])
            for k, s in enumerate(sbl):
                g0 = 131 + 4 * k; nm_ = s.get('name', f'Segment {k + 1}')
                for j, t in enumerate(['revenue growth', 'revenue', 'EBIT margin', 'EBIT']): put(ws, f'A{g0 + j}', f'{nm_[:40]}: {t}')
                put(ws, f'B{g0 + 1}', f'={IN}!$B${A["sb_rows"][k]}' + sf(('sbase', k), f'{nm_[:40]}: base revenue'), F_M, GREEN)
                for i in range(N):
                    c, p = L(3 + i), L(2 + i)
                    put(ws, f'{c}{g0}', f'=Model!{pcol(i)}{R[f"seg{k}_g"]}+$B$4' + sf(('sg', k, i, 'revenue_growth'), f'{nm_[:40]}: revenue growth, year {i + 1}'), F_PCT)
                    put(ws, f'{c}{g0 + 1}', f'={p}{g0 + 1}*(1+{c}{g0})', F_M)
                    put(ws, f'{c}{g0 + 2}', f'=Model!{pcol(i)}{R[f"seg{k}_m"]}+$B$5' + sf(('sg', k, i, 'ebit_margin'), f'{nm_[:40]}: EBIT margin, year {i + 1}'), F_PCT)
                    put(ws, f'{c}{g0 + 3}', f'={c}{g0 + 1}*{c}{g0 + 2}', F_M)
            t0 = 131 + 4 * len(sbl)
            for j, t in enumerate(['Total segment revenue', 'Total segment EBIT', 'Corporate / unallocated EBIT', 'EBIT (segments + corporate)']):
                put(ws, f'A{t0 + j}', t, font=BOLD if j in (0, 3) else None)
            for i in range(N):
                c, p = L(3 + i), L(2 + i)
                put(ws, f'{c}{t0}', '=' + '+'.join(f'{c}{132 + 4 * k}' for k in range(len(sbl))), F_M)
                put(ws, f'{c}{t0 + 1}', '=' + '+'.join(f'{c}{134 + 4 * k}' for k in range(len(sbl))), F_M)
                put(ws, f'{c}{t0 + 2}', f'=Model!{pcol(i)}{R["corp"]}' + sf(('y', i, 'corporate_ebit'), f'Corporate EBIT, year {i + 1}'), F_M, GREEN)
                put(ws, f'{c}{t0 + 3}', f'={c}{t0 + 1}+{c}{t0 + 2}', F_M)
                put(ws, f'{c}21', f'={c}22/{p}22-1', F_PCT); put(ws, f'{c}22', f'={c}{t0}', F_M)
                put(ws, f'{c}23', f'={c}24/{c}22', F_PCT); put(ws, f'{c}24', f'={c}{t0 + 3}', F_M)
        rg = lambda row: f'$C${row}:${lastc}${row}'
        if ctx:  # fixed DCF: the engine's fade from year N to year T (model_engine.extend_rows)
            T = ctx['fade_to_year']; nf = T - N; flc = L(2 + nf); prevc = L(1 + N); g_ = A['g']
            put(ws, 'A100', 'Fixed DCF: fade from the last projected year to year ' + str(T) + ' (model_engine.extend_rows)', font=BOLD)
            for j, t in enumerate(['Year index', 'Fade fraction', 'Revenue growth', 'Revenue', 'EBIT', 'NOPAT', 'D&A', 'Capex', 'Working capital increase',
                                   'Stock comp', 'Free cash flow to firm', 'Share of year after target date', 'PV at target date of FCFF',
                                   'FCFF with capex held at the last year\'s ratio (sensitivity)', 'Revenue x fade fraction (sensitivity)']):
                put(ws, f'A{101 + j}', t)
            H_ = [('Last-year EBIT margin', f'={lastc}24/{lastc}22', F_PCT), ('Last-year tax rate', f'={lastc}25', F_PCT),
                  ('Last-year D&A % revenue', f'={lastc}27/{lastc}22', F_PCT), ('Last-year capex % revenue', f'={lastc}28/{lastc}22', F_PCT),
                  ('Last-year stock comp % revenue', f'={lastc}30/{lastc}22', F_PCT),
                  ('Last-year working capital % of revenue increase', f'=IF(ABS({lastc}22-{prevc}22)>1E-9,{lastc}29/({lastc}22-{prevc}22),0)', F_PCT),
                  ('Stock comp is a cost (adjusted EBIT)', f'=AND({A["ebit_basis"]}="adjusted",{A["sbc_t"]}="cost")', None),
                  ('RONIC used (floored at the WACC)', f'=MAX({A["ronic"]},$B$12)', F_PCT),
                  ('NOPAT margin after stock comp', '=B117*(1-B118)-IF(B123,B121*(1-B118),0)', F_PCT),
                  ('Steady-state capex % revenue', f'=B119+{g_}/B124*B125-B122*{g_}/(1+{g_})', F_PCT),
                  ('Last-year revenue growth', f'={lastc}21', F_PCT)]
            for j, (lab, f, fm) in enumerate(H_): put(ws, f'D{117 + j}', lab); put(ws, f'B{117 + j}', f, fm)
            for j in range(nf):
                c, p = L(3 + j), (lastc if j == 0 else L(2 + j))
                put(ws, f'{c}101', N + 1 + j); put(ws, f'{c}102', f'=({c}101-$B$16)/({T}-$B$16)', '0.000')
                put(ws, f'{c}103', f'=$B$127+({g_}-$B$127)*{c}102', F_PCT)
                put(ws, f'{c}104', f'={p}{22 if j == 0 else 104}*(1+{c}103)', F_M)
                put(ws, f'{c}105', f'={c}104*$B$117', F_M); put(ws, f'{c}106', f'={c}105*(1-$B$118)', F_M)
                put(ws, f'{c}107', f'={c}104*$B$119', F_M); put(ws, f'{c}108', f'={c}104*($B$120+($B$126-$B$120)*{c}102)', F_M)
                put(ws, f'{c}109', f'=$B$122*({c}104-{p}{22 if j == 0 else 104})', F_M); put(ws, f'{c}110', f'={c}104*$B$121', F_M)
                put(ws, f'{c}111', f'={c}106+{c}107-{c}108-{c}109-IF($B$123,{c}110*(1-$B$118),0)', F_M)
                put(ws, f'{c}112', f'=IF({c}101<=$B$13,0,{c}101-MAX({c}101-1,$B$13))', '0.000')
                put(ws, f'{c}113', f'=IF({c}112=0,0,{c}111*{c}112/(1+$B$12)^({c}101-$B$13))', F_M)
                put(ws, f'{c}114', f'={c}106+{c}107-{c}104*$B$120-{c}109-IF($B$123,{c}110*(1-$B$118),0)', F_M)
                put(ws, f'{c}115', f'={c}104*{c}102', F_M)
            C['fade'] = dict(T=T, flc=flc)
        Vv = [('PV of free cash flow after the target date', f'=SUM({rg(36)})', F_M),
              ('Terminal value at final year', f'=IF({A["tmeth"]}="exit_multiple",{A["exit"]}*{lastc}32,{lastc}31*(1+{A["g"]})/($B$12-{A["g"]}))', F_M),
              ('PV of terminal value at target date', '=B43/(1+$B$12)^($B$16-$B$13)', F_M),
              ('DCF enterprise value at target date', '=B42+B44', F_M),
              ('Terminal value share of DCF', '=B44/B45', F_PCT),
              ('Free cash flow earned, balance date to target date', f'=SUMPRODUCT({rg(37)},{rg(31)})', F_M),
              ('Shareholder returns + other uses paid', f'=SUMPRODUCT({rg(37)},{rg(34)})', F_M),
              ('Interest drag on net debt', f'={A["ir"]}*(1-$C$25)*MAX({A["nd"]},0)*MAX($B$13-$B$14,0)', F_M),
              ('Net debt at target date', f'=IF({A["ndh"]}="",{A["nd"]}-B47+B49+B48,{A["ndh"]}{sf(("ndh",), "Net debt at target date (override)")})' + (f'+{A["fin_nd"]}' if 'fin_nd' in A else ''), F_M),
              ('Minority interests', f'={A["minor"]}' + (f'+{A["fin_pf"]}' if 'fin_pf' in A else ''), F_M),
              ('Adjustments (probability-weighted)', '=' + '+'.join([f'({IN}!B{x}{sf(("adj", j_), f"Adjustment {j_ + 1} value")})*{IN}!C{x}' for j_, x in enumerate(A['adj_rows'])]
                                                                   + [f'{x_:.4f}' for x_ in [((eo.get('deep') or {}).get('stakes') or {}).get('total', 0.0),
                                                                      ((eo.get('deep') or {}).get('rnpv') or {}).get('total', 0.0) if (mi.get('rnpv') or {}).get('mode', 'leg') == 'adjustment' else 0.0] if abs(x_) > 1e-9] + ['0']), F_M),
              ('Diluted shares at target date (M)', f'=IF({A["shh"]}="",{A["sh0"]}*(1+{A["shchg"]})^MAX($B$13-$B$15,0),{A["shh"]})' + (f'+{A["fin_sh"]}' if 'fin_sh' in A else ''), '#,##0.0'),
              ('After-tax interest on target-date net debt' + (' (net cash earns it: quarterly set)' if eo.get('quarters') else ''),
               f'={A["ir"]}*(1-$C$25)*' + ('B50' if eo.get('quarters') else 'MAX(B50,0)') + (f'+{A["fin_c"]}' if 'fin_c' in A else ''), F_M),
              ('Check: WACC vs terminal growth', f'=IF(AND({A["tmeth"]}<>"exit_multiple",$B$12<={A["g"]}+0.01),"WACC must exceed terminal growth by 1 point","ok")', None),
              ('Check: projection length', '=IF(OR($B$13>=$B$16-1,$B$13+1>$B$16),"add projected years past the target date","ok")', None)]
        if ctx:
            T, flc = C['fade']['T'], C['fade']['flc']
            Vv[0] = ('PV of free cash flow after the target date (projection + fade)', f'=SUM({rg(36)})+SUM(C113:{flc}113)', F_M)
            Vv[1] = (f'Terminal value at year {T} (steady state)', f'=({flc}106-IF(B123,{flc}110*(1-B118),0))*(1+{A["g"]})*(1-{A["g"]}/B124)/($B$12-{A["g"]})', F_M)
            Vv[2] = ('PV of terminal value at target date', f'=B43/(1+$B$12)^({T}-$B$13)', F_M)
        QW = {}
        if eo.get('quarters'):  # quarterly set (model_drivers, 2026-10-29): per-year weights the engine's quarter rows give each metric
            qr = eo['quarters']; yr = {r_['label']: r_ for r_ in eo['rows']}; Dd = dt.date.fromisoformat; Hd = Dd(eo['horizon'])
            wins = dict(ntm=(Hd, Hd + dt.timedelta(days=365)), ltm=(Hd - dt.timedelta(days=365), Hd), roll=(Dd(bal_asof[:10]), Hd))
            covered = lambda a, b: a >= Dd(qr[0]['start']) - dt.timedelta(days=3) and b <= Dd(qr[-1]['end']) + dt.timedelta(days=3)
            put(ws, 'A180', 'Quarterly set: weight of each projected year in the metric at the target date, from the engine\'s quarter rows (model_engine.quarter_rows; replaces the straight-line weights in rows 37-39)', font=BOLD)
            rq = 181
            for wk, keys in (('ntm', (22, 24, 32, 33, 26, 31)), ('ltm', (22, 24, 32, 33, 26, 31)), ('roll', (31, 34))):
                a_, b_ = wins[wk]
                if not covered(a_, b_) or (wk == 'roll' and a_ >= b_): continue
                for row in keys:
                    key = {22: 'revenue', 24: 'ebit', 32: 'ebitda', 33: 'adj_ebitda', 26: 'nopat', 31: 'fcff', 34: 'returns'}[row]
                    put(ws, f'A{rq}', f'{wk.upper()} weight by year: {key}')
                    for i in range(N):
                        ylab = eo['rows'][i]['label']; tot = yr[ylab][key]; ct = 0.0
                        for q_ in qr:
                            if q_['fy'] != ylab: continue
                            s_, e_ = Dd(q_['start']), Dd(q_['end']); ov = (min(e_, b_) - max(s_, a_)).days
                            if ov > 0: ct += q_[key] * ov / (e_ - s_).days
                        put(ws, f'{L(3 + i)}{rq}', ct / tot if abs(tot) > 1e-9 else 0.0, '0.0000', GREEN)
                    QW[(wk, row)] = rq; rq += 1
        if (('roll', 31) in QW):
            Vv[5] = ('Free cash flow earned, balance date to target date', f'=SUMPRODUCT({rg(QW[("roll", 31)])},{rg(31)})', F_M)
            Vv[6] = ('Shareholder returns + other uses paid', f'=SUMPRODUCT({rg(QW[("roll", 34)])},{rg(34)})', F_M)
        put(ws, 'A41', 'Value at the target date', font=BOLD)
        for j, (lab, f, fm) in enumerate(Vv): put(ws, f'A{42 + j}', lab); put(ws, f'B{42 + j}', f, fm)
        header(ws, 58, ['Metric at target date', 'Next 12 months', 'Last 12 months'])
        MET = [('Revenue', 22), ('EBIT', 24), ('EBITDA', 32), ('Adj. EBITDA', 33), ('NOPAT', 26), ('Free cash flow to firm', 31)]
        for j, (lab, row) in enumerate(MET):
            rr_ = 59 + j; put(ws, f'A{rr_}', lab)
            put(ws, f'B{rr_}', f'=SUMPRODUCT({rg(QW.get(("ntm", row), 38))},{rg(row)})', F_M)
            put(ws, f'C{rr_}', f'=SUMPRODUCT({rg(QW[("ltm", row)])},{rg(row)})' if ('ltm', row) in QW else
                f'=SUMPRODUCT({rg(39)},{rg(row)})' + ('+$B$39*$B$22' if row == 22 else ''), F_M)
        header(ws, 66, ['Valuation method', 'Metric', 'Multiple used', 'Basis', 'Metric at target date', 'Value per share', 'Weight', 'Metric key', 'Metric shown (stated per-share figure when used)'])
        per = lambda ev: f'(({ev})-$B$50-$B$51+$B$52)/$B$53'
        put(ws, 'A67', 'DCF (cash flow to target date + terminal)'); put(ws, 'F67', '=' + per('B45'), F_PS); put(ws, 'G67', f'={A["w_dcf"]}', F_PCT)
        r_ = 68; raw_w = []
        for j, ir_ in enumerate(A['hm_rows']):
            m, bs = f'{IN}!A{ir_}', f'{IN}!D{ir_}'
            put(ws, f'A{r_}', f'="Multiple: "&{m}&" ("&{bs}&")"')
            put(ws, f'B{r_}', f'={m}')
            mv_ = f'({IN}!B{ir_}{sf(("hm", j, "value"), f"Target multiple {j + 1}")})'; ep_ = f'({IN}!F{ir_}{sf(("hm", j, "eps"), f"Stated EPS, multiple {j + 1}")})'
            fp_ = f'({IN}!G{ir_}{sf(("hm", j, "fcf_per_share"), f"Stated FCF/share, multiple {j + 1}")})'
            if 'fin_sh' in A:  # stated EPS / FCF per share are before the expected financing (model_engine.financing_expected)
                fe_ = f'({A["ir"]}*(1-$C$25)*{A["fin_nd"]}+{A["fin_c"]})'
                ep_ = f'({ep_}*($B$53-{A["fin_sh"]})-{fe_})/$B$53'; fp_ = f'({fp_}*($B$53-{A["fin_sh"]})-{fe_})/$B$53'
            put(ws, f'C{r_}', f'=IF(B{r_}="FCF yield",{mv_}*(1-$B$8),{mv_}+IF(B{r_}="EV/sales",0.1,1)*$B$6)', '0.00')
            put(ws, f'D{r_}', f'={bs}')
            put(ws, f'H{r_}', f'=IF(B{r_}="EV/sales",1,IF(B{r_}="EV/EBIT",2,IF(B{r_}="EV/EBITDA",IF({IN}!E{ir_}="adjusted",4,3),IF(B{r_}="P/E",5,6))))')
            put(ws, f'E{r_}', f'=IF(D{r_}="ltm",CHOOSE(H{r_},$C$59,$C$60,$C$61,$C$62,$C$63,$C$64),CHOOSE(H{r_},$B$59,$B$60,$B$61,$B$62,$B$63,$B$64))', F_M)
            put(ws, f'F{r_}', f'=IF(OR(B{r_}="EV/EBITDA",B{r_}="EV/sales",B{r_}="EV/EBIT"),{per(f"C{r_}*E{r_}")},'
                               f'IF(B{r_}="P/E",IF({IN}!F{ir_}<>"",C{r_}*{ep_}*(1+$B$9),C{r_}*(E{r_}-$B$54)/$B$53),'
                               f'IF({IN}!G{ir_}<>"",{fp_}*(1+$B$9)/C{r_},(E{r_}-IF({IN}!H{ir_},$B$54,0))/C{r_}/$B$53))+$B$52/$B$53)', F_PS)
            put(ws, f'G{r_}', f'={IN}!C{ir_}' + ('*$B$98' if ctx else ''), F_PCT); raw_w.append(f'{IN}!C{ir_}')
            put(ws, f'I{r_}', f'=IF(AND(B{r_}="P/E",{IN}!F{ir_}<>""),{ep_}*(1+$B$9),IF(AND(B{r_}="FCF yield",{IN}!G{ir_}<>""),{fp_}*(1+$B$9),E{r_}))', '#,##0.00'); r_ += 1
        if A['seg_rows']:
            ev = '+'.join(f'({IN}!B{x}{sf(("seg", j_, "ntm_metric"), f"Segment {j_ + 1} NTM metric")})*({IN}!D{x}{sf(("seg", j_, "multiple"), f"Segment {j_ + 1} multiple")})' for j_, x in enumerate(A['seg_rows']))
            put(ws, f'A{r_}', 'Sum of the parts'); put(ws, f'F{r_}', '=' + per(f'({ev})*(1+$B$8)'), F_PS); put(ws, f'G{r_}', f'={A["w_sotp"]}' + ('*$B$98' if ctx else ''), F_PCT); raw_w.append(A['w_sotp']); r_ += 1
        for x in A['cus_rows']:
            put(ws, f'A{r_}', f'="Custom: "&{IN}!A{x}'); put(ws, f'F{r_}', f'={IN}!B{x}*(1+$B$10)', F_PS); put(ws, f'G{r_}', f'={IN}!C{x}' + ('*$B$98' if ctx else ''), F_PCT); raw_w.append(f'{IN}!C{x}'); r_ += 1
        if sop:  # explicit parts block (rows 230+): live metric x multiple, engine value for the other methods
            put(ws, 'A229', 'Sum of the parts (explicit parts): value of each part after ownership', font=BOLD)
            pv_ = (eo['legs'].get('sotp_parts') or {}).get('parts', {})
            for j, (p_, x) in enumerate(zip(sop, A['sp_rows'])):
                rr_ = 230 + j; mth = p_.get('method', 'multiple'); put(ws, f'A{rr_}', f'={IN}!A{x}'); put(ws, f'C{rr_}', f'={IN}!C{x}')
                if mth == 'multiple': put(ws, f'B{rr_}', f'=({IN}!D{x}{sf(("sp", j, "metric_value"), f"Part {j + 1} metric")})*({IN}!E{x}{sf(("sp", j, "multiple"), f"Part {j + 1} multiple")})*(1+$B$8)*{IN}!F{x}', F_M)
                elif mth == 'value': put(ws, f'B{rr_}', f'={IN}!G{x}' + ('*(1+$B$8)' if p_.get('scale_with_multiple') else '') + f'*{IN}!F{x}', F_M)
                else: put(ws, f'B{rr_}', (pv_.get(p_['name']) or {}).get('value', 0.0), F_M); put(ws, f'D{rr_}', f'{mth}: valued by model_engine.py for this scenario', font=GREY)
            z_ = 229 + len(sop)
            put(ws, f'A{r_}', 'Sum of the parts (explicit parts)')
            put(ws, f'F{r_}', f'=(SUMIF($C$230:$C${z_},"ev",$B$230:$B${z_})*(1-{A["sp_hd"]})-$B$50-$B$51+$B$52)/$B$53+SUMIF($C$230:$C${z_},"equity",$B$230:$B${z_})*(1-{A["sp_hd"]})/$B$53', F_PS)
            put(ws, f'G{r_}', f'={A["sp_w"]}' + ('*$B$98' if ctx else ''), F_PCT); raw_w.append(A['sp_w']); r_ += 1
        for k_ in ENG_LEGS:  # rNPV / NAV / REIT legs: the engine's value for this scenario
            lg = eo['legs'].get(k_) or {}
            put(ws, f'A{r_}', f'Engine-computed: {k_}'); put(ws, f'F{r_}', lg.get('value', 0.0), F_PS); put(ws, f'G{r_}', lg.get('weight', 0.0), F_PCT)
            put(ws, f'I{r_}', 'valued by model_engine.py for this scenario', font=GREY); raw_w.append(str(lg.get('weight', 0.0))); r_ += 1
        if ctx:  # the DCF takes its weight; the other methods share the rest in their own proportions (model_engine.value)
            put(ws, 'A98', 'Scale on the other methods\' weights (fixed DCF)'); put(ws, 'B98', f'=IF(SUM({",".join(raw_w) or 0})>0,(1-{A["w_dcf"]})/SUM({",".join(raw_w) or 0}),0)', '0.0000')
        put(ws, f'A{r_}', 'Value per share at target date (weighted methods)', font=BOLD)
        put(ws, f'F{r_}', f'=SUMPRODUCT(F67:F{r_ - 1},G67:G{r_ - 1})/SUM(G67:G{r_ - 1})', F_PS, BOLD)
        C['hv'] = f"'{name}'!$F${r_}"; C['legs'] = (67, r_ - 1); C['hv_row'] = r_
        ws.column_dimensions['A'].width = 46
        for c in range(2, 3 + N): ws.column_dimensions[L(c)].width = 13
        ws.freeze_panes = 'B19'
        return C
    CFc = calc(CFN, 'research target (12m) (reconciled set, no scenario deltas)', [0] * 7)
    SC = {k: calc(calc_names[k], f'{k} scenario', [f'=N({IN}!{L(3 + c)}{rr})' for c in range(7)], sc[k].get('deltas')) for k, rr in A['sc_rows'].items()}

    # ================= Valuation: target derivation (top of the tab) =================
    put(V, 'A1', f'{sym}: valuation and price target derivation', font=TITLE)
    put(V, 'A2', 'Value per share at the target date = weighted methods below; the base case is the 12-month median, with the downside and upside cases as its 10th and 90th percentiles (Distribution tab).', font=GREY)
    header(V, 3, ['Method', 'Multiple', 'Metric at target date', 'Value per share', 'Weight', 'Contribution'])
    a_, z_ = CFc['legs']
    off = 1 if NODCF else 0  # no DCF row when the DCF weight is 0 (the DCF is the first leg, Calc row 67)
    assert not off or a_ == 67
    for k in range(a_ + off, z_ + 1):
        rr = 4 + (k - a_ - off)
        put(V, f'A{rr}', f'={CF}!A{k}', font=GREEN); put(V, f'B{rr}', f'=IF({CF}!C{k}="","",{CF}!C{k})', '0.00', GREEN)
        put(V, f'C{rr}', f'=IF({CF}!I{k}="","",{CF}!I{k})', '#,##0.00', GREEN); put(V, f'D{rr}', f'={CF}!F{k}', F_PS, GREEN)
        put(V, f'E{rr}', f'={CF}!G{k}', F_PCT, GREEN); put(V, f'F{rr}', f'=D{rr}*E{rr}/SUM($E$4:$E${3 + n_legs})', F_PS)
    rr = 4 + n_legs; put(V, f'A{rr}', 'Research target (12m) per share', font=BOLD); put(V, f'D{rr}', f'={CFc["hv"]}', F_PS, BOLD); put(V, f'F{rr}', f'=SUM(F4:F{rr - 1})', F_PS, BOLD)
    FVR = f'Valuation!$D${rr}'
    r0 = rr + 2; section(V, r0, '12-month EPS basis (what the P/E is applied to)', 9)
    header(V, r0 + 1, ['Fiscal year'] + [f'=Model!{pcol(i)}5' for i in range(N)])
    put(V, f'A{r0 + 2}', 'Share of the 12 months after the target date'); put(V, f'A{r0 + 3}', 'Model EPS (Model tab)')
    for i in range(N):
        put(V, f'{L(2 + i)}{r0 + 2}', f'={CF}!{L(3 + i)}38', F_PCT, GREEN); put(V, f'{L(2 + i)}{r0 + 3}', f'=Model!{pcol(i)}{R["eps"]}', F_EPS, GREEN)
    put(V, f'A{r0 + 4}', 'Model EPS, next 12 months after the target date'); put(V, f'B{r0 + 4}', f'=SUMPRODUCT(B{r0 + 2}:{L(1 + N)}{r0 + 2},B{r0 + 3}:{L(1 + N)}{r0 + 3})', F_EPS, BOLD)
    pe_rows = [x for x, hm in zip(A['hm_rows'], hms) if hm['metric'] == 'P/E' and hm.get('eps') is not None]
    if pe_rows:
        put(V, f'A{r0 + 5}', 'Stated next-12-month EPS used by the P/E method (consensus basis, cited)'); put(V, f'B{r0 + 5}', f'={IN}!F{pe_rows[0]}', F_EPS)
        put(V, f'C{r0 + 5}', f'=B{r0 + 5}/B{r0 + 4}-1', F_PCT, ITAL); put(V, f'D{r0 + 5}', 'vs model EPS', font=GREY)
    r1 = r0 + 7
    if NODCF: put(V, f'A{r1}', 'No DCF leg for this name (DCF weight 0), so no DCF build.', font=GREY)
    else:
     section(V, r1, 'DCF build (free cash flow to firm, discounted to the target date)', 9)
     header(V, r1 + 1, ['Fiscal year'] + [f'=Model!{pcol(i)}5' for i in range(N)])
     for j, (lab, crow, fmt) in enumerate([('Free cash flow to firm', 31, F_M), ('Share of the year after the target date', 35, F_PCT), ('PV at target date', 36, F_M)]):
         put(V, f'A{r1 + 2 + j}', lab)
         for i in range(N): put(V, f'{L(2 + i)}{r1 + 2 + j}', f'={CF}!{L(3 + i)}{crow}', fmt, GREEN)
     rows_ = [('PV of free cash flow after the target date', 'B42', F_M), ('Terminal value (final year)', 'B43', F_M), ('PV of terminal value', 'B44', F_M),
              ('Enterprise value at target date', 'B45', F_M), ('Terminal value share of DCF', 'B46', F_PCT), ('Net debt at target date', 'B50', F_M),
              ('Minority interests', 'B51', F_M), ('Adjustments', 'B52', F_M), ('Diluted shares at target date (M)', 'B53', '#,##0.0'),
              ('DCF value per share', 'F67', F_PS), ('WACC', 'B12', F_PCT)]
     for j, (lab, cref, fmt) in enumerate(rows_):
         put(V, f'A{r1 + 6 + j}', lab); put(V, f'B{r1 + 6 + j}', f'={CF}!{cref}', fmt, GREEN)
     iw = (eng.get('sets', {}).get('supervisor') or {}).get('implied_wacc_for_multiples')
     if iw is not None:
         put(V, f'A{r1 + 6 + len(rows_)}', 'WACC at which the DCF equals the multiples (reverse DCF, engine run; not live)'); put(V, f'B{r1 + 6 + len(rows_)}', iw, F_PCT)
     assert r1 + 7 + len(rows_) < v0 - 1, 'valuation blocks overlap the assumptions'

    # ================= Distribution =================
    put(D, 'A1', f'{sym}: 12-month distribution (base case = median; downside = 10th, upside = 90th percentile; a lognormal with its own spread on each side)', font=TITLE)
    header(D, 3, ['Case', 'Percentile', 'Value at target date', 'Story'])
    put(D, 'A4', 'downside'); put(D, 'B4', 0.1, F_PCT); put(D, 'A5', 'base'); put(D, 'B5', 0.5, F_PCT); put(D, 'A6', 'upside'); put(D, 'B6', 0.9, F_PCT)
    put(D, 'C5', f'={CFc["hv"]}', F_PS, GREEN); put(D, 'D5', 'the reconciled set (research target (12m))')
    for k, rr_d in (('downside', 4), ('upside', 6)):
        if k in SC:
            put(D, f'C{rr_d}', f'={SC[k]["hv"]}', F_PS, GREEN); put(D, f'D{rr_d}', f'={IN}!{L(3 + len(DK))}{A["sc_rows"][k]}')
        else: put(D, f'D{rr_d}', 'no case: this side uses the default spread')
    rS = 8
    put(D, f'A{rS}', 'Downside spread (log vol)'); put(D, f'B{rS}', f'=IF(ISNUMBER(C4),LN(C5/C4)/NORMSINV(0.9),{A["sv"]})', F_PCT)
    put(D, f'A{rS + 1}', 'Upside spread (log vol)'); put(D, f'B{rS + 1}', f'=IF(ISNUMBER(C6),LN(C6/C5)/NORMSINV(0.9),{A["sv"]})', F_PCT)
    put(D, f'A{rS + 2}', 'Mean'); put(D, f'B{rS + 2}', f'=C5*(EXP(B{rS}^2/2)*NORMSDIST(-B{rS})+EXP(B{rS + 1}^2/2)*NORMSDIST(B{rS + 1}))', F_PS)
    put(D, f'C{rS}', f'=IF(B{rS}<0.02,"FLAG: downside case at or above base","")'); put(D, f'C{rS + 1}', f'=IF(B{rS + 1}<0.02,"FLAG: upside case at or below base","")')
    SD_, SU_ = f'MAX(Distribution!$B${rS},0.02)', f'MAX(Distribution!$B${rS + 1},0.02)'
    PkX = lambda K: f'1-NORMSDIST(LN({K}/Distribution!$C$5)/IF({K}<Distribution!$C$5,{SD_},{SU_}))'
    QS = [('Median (model target)', 0.5), ('10th percentile', 0.1), ('25th percentile', 0.25), ('75th percentile', 0.75), ('90th percentile', 0.9)]
    q0 = rS + 4; header(D, q0, ['Quantile', 'Price'])
    Q = {}
    for j, (lab, q) in enumerate(QS):
        put(D, f'A{q0 + 1 + j}', lab); put(D, f'B{q0 + 1 + j}', f'=$C$5*EXP(NORMSINV({q})*' + (SD_ if q < 0.5 else SU_) + ')', F_PS)
        Q[q] = f'Distribution!$B${q0 + 1 + j}'
    D.column_dimensions['A'].width = 26; D.column_dimensions['D'].width = 60
    for c in range(2, 16): D.column_dimensions[L(c)].width = max(D.column_dimensions[L(c)].width or 0, 12)

    # ================= Sensitivity =================
    put(SE, 'A1', f'{sym}: sensitivity tables (bold = current inputs; every cell recalculates from the live model)', font=TITLE)
    rg = lambda row: f"{CF}!$C${row}:${lastc}${row}"
    # 1. DCF value per share: WACC x terminal growth
    exit_tv = tm.get('method') == 'exit_multiple'  # exit-multiple terminal: growth does not move the DCF, so flex the exit multiple
    if NODCF: put(SE, 'A3', 'No DCF leg for this name (DCF weight 0), so no DCF sensitivity.', font=GREY)
    if ctx:  # fixed DCF: WACC x RONIC (growth is the engine's 3%)
        T, flc = CFc['fade']['T'], CFc['fade']['flc']; g_ = A['g']
        put(SE, 'A3', 'DCF value per share: WACC (down) x return on new capital after the fade (across)', font=BOLD)
        put(SE, 'A4', 'WACC \\ RONIC', font=BOLD); rsteps = [-0.05, -0.025, 0, 0.05, 0.10]; steps = [-0.01, -0.005, 0, 0.005, 0.01]
        for j, s_ in enumerate(rsteps): put(SE, f'{L(2 + j)}4', f'={A["ronic"]}+({s_})', F_PCT, BOLD)
        for i_, s_ in enumerate(steps):
            rr = 5 + i_; put(SE, f'A{rr}', f'={A["wacc"]}+({s_})', F_PCT, BOLD)
            for j in range(5):
                c = L(2 + j); w, RN_ = f'$A{rr}', f'MAX({c}$4,$A{rr})'
                st = f'({CF}!$B$119+{g_}/{RN_}*{CF}!$B$125-{CF}!$B$122*{g_}/(1+{g_}))'
                ev = (f'SUMPRODUCT({rg(31)},{rg(35)},(1+{w})^(-({rg(19)}-{CF}!$B$13)))'
                      f'+SUMPRODUCT({CF}!$C$114:${flc}$114-{CF}!$C$115:${flc}$115*({st}-{CF}!$B$120),{CF}!$C$112:${flc}$112,(1+{w})^(-({CF}!$C$101:${flc}$101-{CF}!$B$13)))'
                      f'+({CF}!${flc}$106-IF({CF}!$B$123,{CF}!${flc}$110*(1-{CF}!$B$118),0))*(1+{g_})*(1-{g_}/{RN_})/({w}-{g_})/(1+{w})^({T}-{CF}!$B$13)')
                put(SE, f'{c}{rr}', f'=IF({w}-{g_}<=0.01,"n/a",(({ev})-{CF}!$B$50-{CF}!$B$51+{CF}!$B$52)/{CF}!$B$53)', F_PS, BOLD if i_ == 2 and j == 2 else None)
    if not ctx and not NODCF: put(SE, 'A3', 'DCF value per share: WACC (down) x ' + ('exit EV/EBITDA multiple' if exit_tv else 'terminal growth') + ' (across)', font=BOLD)
    steps = [-0.01, -0.005, 0, 0.005, 0.01]; xsteps = [-2, -1, 0, 1, 2]
    if not ctx and not NODCF: put(SE, 'A4', 'WACC \\ exit multiple' if exit_tv else 'WACC \\ growth', font=BOLD)
    for j, s_ in enumerate(steps if not ctx and not NODCF else []):
        if exit_tv: put(SE, f'{L(2 + j)}4', f'={A["exit"]}+({xsteps[j]})', '0.0"x"', BOLD)
        else: put(SE, f'{L(2 + j)}4', f'={A["g"]}+({s_})', F_PCT, BOLD)
    for i_, s_ in enumerate(steps if not ctx and not NODCF else []):
        rr = 5 + i_; put(SE, f'A{rr}', f'={A["wacc"]}+({s_})', F_PCT, BOLD)
        for j in range(5):
            c = L(2 + j); w, gg = f'$A{rr}', f'{c}$4'
            ev = (f'SUMPRODUCT({rg(31)},{rg(35)},(1+{w})^(-({rg(19)}-{CF}!$B$13)))'
                  + (f'+{gg}*{CF}!${lastc}$32' if exit_tv else f'+{CF}!${lastc}$31*(1+{gg})/({w}-{gg})') + f'/(1+{w})^({CF}!$B$16-{CF}!$B$13)')
            put(SE, f'{c}{rr}', (f'=(({ev})-{CF}!$B$50-{CF}!$B$51+{CF}!$B$52)/{CF}!$B$53' if exit_tv else
                                 f'=IF({w}-{gg}<=0.01,"n/a",(({ev})-{CF}!$B$50-{CF}!$B$51+{CF}!$B$52)/{CF}!$B$53)'), F_PS, BOLD if i_ == 2 and j == 2 else None)
    # 2. Research target (12m): first multiple x its metric
    if A['hm_rows']:
        ir_ = A['hm_rows'][0]; hm0 = hms[0]; lr = 68
        put(SE, 'A11', f'Research target (12m) per share: {hm0["metric"]} multiple (down) x ' + ('stated EPS' if hm0['metric'] == 'P/E' and hm0.get('eps') is not None else 'metric at target date') + ' (across)', font=BOLD)
        put(SE, 'A12', 'Multiple \\ metric', font=BOLD)
        msteps = [-3, -2, -1, 0, 1, 2, 3] if hm0['metric'] != 'FCF yield' else [-0.015, -0.01, -0.005, 0, 0.005, 0.01, 0.015]
        ssteps = [0.9, 0.95, 1.0, 1.05, 1.1]
        stated = f'{IN}!F{ir_}' if hm0['metric'] == 'P/E' else f'{IN}!G{ir_}'
        for j, s_ in enumerate(ssteps):
            put(SE, f'{L(2 + j)}12', (f'={stated}*{s_}' if (hm0['metric'] in ('P/E', 'FCF yield') and (hm0.get('eps') if hm0['metric'] == 'P/E' else hm0.get('fcf_per_share')) is not None) else f'={CF}!$E${lr}*{s_}'),
                F_EPS if hm0['metric'] in ('P/E', 'FCF yield') and (hm0.get('eps') or hm0.get('fcf_per_share')) is not None else F_M, BOLD)
        others = f'(SUMPRODUCT({CF}!$F${a_}:$F${z_},{CF}!$G${a_}:$G${z_})-{CF}!$F${lr}*{CF}!$G${lr})'
        per = lambda ev: f'(({ev})-{CF}!$B$50-{CF}!$B$51+{CF}!$B$52)/{CF}!$B$53'
        for i_, s_ in enumerate(msteps):
            rr = 13 + i_; put(SE, f'A{rr}', f'={IN}!$B${ir_}+({s_})', '0.0"x"' if hm0['metric'] != 'FCF yield' else F_PCT, BOLD)
            for j in range(5):
                c = L(2 + j); m, X = f'$A{rr}', f'{c}$12'
                met = hm0['metric']
                if met in ('EV/EBITDA', 'EV/sales', 'EV/EBIT'): lv = per(f'{m}*{X}')
                elif met == 'P/E': lv = (f'{m}*{X}' if hm0.get('eps') is not None else f'{m}*({X}-{CF}!$B$54)/{CF}!$B$53') + f'+{CF}!$B$52/{CF}!$B$53'
                else: lv = (f'{X}/{m}' if hm0.get('fcf_per_share') is not None else f'({X}-IF({IN}!H{ir_},{CF}!$B$54,0))/{m}/{CF}!$B$53') + f'+{CF}!$B$52/{CF}!$B$53'
                put(SE, f'{c}{rr}', f'=({others}+{CF}!$G${lr}*({lv}))/SUM({CF}!$G${a_}:$G${z_})', F_PS, BOLD if i_ == 3 and j == 2 else None)
    # 3. EPS in the second projected year: growth delta x margin delta
    k2 = min(1, N - 1); yl = yrs[k2].get('label', f'Y{k2 + 1}')
    put(SE, 'A22', f'{yl}E diluted EPS: revenue growth change in every year (down) x EBIT margin change (across)', font=BOLD)
    put(SE, 'A23', 'Growth Δ \\ margin Δ', font=BOLD)
    dms = [-0.01, -0.005, 0, 0.005, 0.01]; dgs = [-0.02, -0.01, 0, 0.01, 0.02]
    for j, s_ in enumerate(dms): put(SE, f'{L(2 + j)}23', s_, '+0.0%;-0.0%;0.0%', BOLD)
    for i_, s_ in enumerate(dgs):
        rr = 24 + i_; put(SE, f'A{rr}', s_, '+0.0%;-0.0%;0.0%', BOLD)
        for j in range(5):
            c = L(2 + j); gprod = '*'.join(f'(1+Model!{pcol(t)}{R["drv_revenue_growth"]}+$A{rr})' for t in range(k2 + 1))
            col = pcol(k2)
            ebit_ = f'{A["rev0"]}*{gprod}*(Model!{col}{R["drv_ebit_margin"]}+{c}$23)'
            if sbl:  # segment build: the deltas move every segment's growth and margin, as the engine's scenarios do
                ebit_ = '+'.join(f'{IN}!$B${A["sb_rows"][s]}*' + '*'.join(f'(1+Model!{pcol(t)}{R[f"seg{s}_g"]}+$A{rr})' for t in range(k2 + 1))
                                 + f'*(Model!{col}{R[f"seg{s}_m"]}+{c}$23)' for s in range(len(sbl))) + f'+Model!{col}{R["corp"]}'
            put(SE, f'{c}{rr}', f'=({ebit_}+Model!{col}{R["netint"]})*(1-Model!{col}{R["drv_tax_rate"]})/Model!{col}{R["shares"]}',
                F_EPS, BOLD if i_ == 2 and j == 2 else None)
    put(SE, 'A30', 'Net interest and the share count are held at the base case in table 3.', font=GREY)
    SE.column_dimensions['A'].width = 30
    for c in range(2, 8): SE.column_dimensions[L(c)].width = 13

    # ================= Summary (cover) =================
    fv_sup = sup.get('fair_value'); fv_sup = fv_sup.get('value') if isinstance(fv_sup, dict) else fv_sup
    put(S, 'A1', f'{sym} standard model (trial) · research run {date}', font=TITLE)
    put(S, 'A2', 'The supervisor\'s reconciled assumption set as a live model. Trial: shown beside the blended page target, not used for '
                 'trade levels until its weight is decided. Edit blue cells on Model (drivers) and Valuation (assumptions).', font=GREY)
    box = [('Live price', f'=IFERROR(GOOGLEFINANCE("{sym}","price"),{A["spot"]})', F_PS, None),
           ('Price as of', '=NOW()', 'yyyy-mm-dd hh:mm', None),
           ('Model target (12-month median)', f'={Q[0.5]}', F_PS, BOLD),
           ('Upside to model target from live price', '=B6/B4-1', F_PCT, BOLD),
           ('Research target (12m) (no scenario deltas)', f'={FVR}', F_PS, None),
           ('Mean', f'=Distribution!$B${rS + 2}', F_PS, None),
           ('10th percentile', f'={Q[0.1]}', F_PS, None), ('25th percentile', f'={Q[0.25]}', F_PS, None),
           ('75th percentile', f'={Q[0.75]}', F_PS, None), ('90th percentile', f'={Q[0.9]}', F_PS, None),
           ('Target date', f'={A["H"]}', F_D, None), ('Price at the run', f'={A["spot"]}', F_PS, None)]
    header(S, 3, ['Price target box', 'Value'])
    for j, (lab, f, fm, ft) in enumerate(box): put(S, f'A{4 + j}', lab); put(S, f'B{4 + j}', f, fm, ft or GREEN if '!' in str(f) else ft)
    r = 4 + len(box)
    extra = [('Blended page target (options 67% + agents 33%, not live)', rs.get('target'), F_PS), ('Supervisor research target (12m) (its own methods)', fv_sup, F_PS),
             ('Thesis break price (supervisor)', sup.get('thesis_break_price'), F_PS), ('Supervisor confidence', sup.get('confidence'), None)]
    for lab, v, fm in extra:
        if v is not None: put(S, f'A{r}', lab); put(S, f'B{r}', v, fm); r += 1
    r += 1; header(S, r, ['Key estimates', f'FY{hy[-1]}A' if hy else ''] + [f'=Model!{pcol(i)}5' for i in range(min(2, N))]); r += 1
    for lab, key, fm in [('Net sales', 'sales', F_M), ('  % YoY', 'sales_yoy', F_PCT), ('Pretax margin', 'pretax_m', F_PCT), ('Diluted EPS (model)', 'eps', F_EPS),
                         ('  % YoY', 'eps_yoy', F_PCT), ('Free cash flow to firm', 'fcff', F_M)]:
        put(S, f'A{r}', lab, font=ITAL if lab.startswith('  ') else None)
        if hy: put(S, f'B{r}', f'=IF(Model!{hcol(nh - 1)}{R[key]}="","",Model!{hcol(nh - 1)}{R[key]})', fm, GREEN)
        for i in range(min(2, N)): put(S, f'{L(3 + i)}{r}', f'=Model!{pcol(i)}{R[key]}', fm, GREEN)
        r += 1
    for kind in ('guidance', 'consensus'):
        for e in (ref.get(kind) or []):
            if 'eps' not in (e.get('metric') or '').lower(): continue
            col = [i for i, y in enumerate(yrs[:2]) if y.get('label') == e.get('fy')]
            if not col: continue
            v = (e.get('low') + e.get('high')) / 2 if kind == 'guidance' and e.get('low') is not None and e.get('high') is not None else e.get('value')
            put(S, f'A{r}', f'{"Guidance midpoint" if kind == "guidance" else "Consensus"}: EPS ({e.get("basis", "")[:40]})', font=Font(color='7030A0' if kind == 'guidance' else 'C55A11'))
            put(S, f'{L(3 + col[0])}{r}', v, F_EPS, Font(color='7030A0' if kind == 'guidance' else 'C55A11')); r += 1
    r += 1; header(S, r, ['Valuation method', 'Value per share', 'Weight']); r += 1
    for k in range(n_legs - (1 if NODCF else 0)):
        put(S, f'A{r}', f'=Valuation!A{4 + k}', font=GREEN); put(S, f'B{r}', f'=Valuation!D{4 + k}', F_PS, GREEN); put(S, f'C{r}', f'=Valuation!E{4 + k}', F_PCT, GREEN); r += 1
    r += 1; header(S, r, ['Case', 'Percentile', 'Value at target date', 'What has to happen']); r += 1
    for j in range(3):
        put(S, f'A{r}', f'=Distribution!A{4 + j}', font=GREEN); put(S, f'B{r}', f'=Distribution!B{4 + j}', F_PCT, GREEN)
        put(S, f'C{r}', f'=Distribution!C{4 + j}', F_PS, GREEN); put(S, f'D{r}', f'=Distribution!D{4 + j}', font=GREEN, wrap=True); r += 1
    r += 1; header(S, r, ['Price at target date', 'Probability above']); r += 1
    for mult in (0.7, 0.8, 0.9, 1.0, 1.1, 1.2, 1.3, 1.5):
        put(S, f'A{r}', f'=ROUND({A["spot"]}*{mult},0)', F_PS); put(S, f'B{r}', '=' + PkX(f'A{r}'), F_PCT); r += 1
    # checks
    r += 1; header(S, r, ['Model checks', 'Status']); r += 1; c0r = r
    eng_s = (eng.get('sets', {}).get('supervisor') or {})
    checks = [('Downside below base and upside above base', f'=IF(AND(Distribution!C{rS}="",Distribution!C{rS + 1}=""),"ok","FLAG")'),
              ('WACC exceeds terminal growth by 1 point or more', f'=IF({CF}!B55="ok","ok","FLAG")'),
              ('Projection runs a full year past the target date', f'=IF({CF}!B56="ok","ok","FLAG")'),
              ('Method weights add up to more than zero', f'=IF(SUM(Valuation!E4:E{3 + n_legs})>0,"ok","FLAG")'),
              ('Terminal value under 85% of the DCF', f'=IF({CF}!B46<0.85,"ok","note: "&TEXT({CF}!B46,"0%")&" of the DCF is terminal value")')]
    if NODCF: checks = [c for c in checks if 'WACC' not in c[0] and 'DCF' not in c[0]]
    if sbl: checks.append(('Segment base revenues tie to the last reported net sales (within 2%)', f'=IF(ABS({A["sb_sum"]}/{A["rev0"]}-1)<=0.02,"ok","FLAG")'))
    if eng_s.get('median'):
        checks.append(('Matches the engine run at publication (target, research target (12m))',
                       f'=IF(AND(ABS({Q[0.5]}-{eng_s["median"]:.4f})<0.01,ABS({FVR}-{eng_s["base"]["horizon_value"]:.4f})<0.01),"ok","inputs edited since publication")'))
    for lab, f in checks: put(S, f'A{r}', lab); put(S, f'B{r}', f); r += 1
    put(S, f'A{r}', 'All checks', font=BOLD); put(S, f'B{r}', f'=IF(COUNTIF(B{c0r}:B{r - 1},"ok")={len(checks)},"all ok",{len(checks)}-COUNTIF(B{c0r}:B{r - 1},"ok")&" to look at")', font=BOLD)
    S.column_dimensions['A'].width = 58; S.column_dimensions['B'].width = 16; S.column_dimensions['C'].width = 16; S.column_dimensions['D'].width = 70

    # ================= Agents (static) =================
    put(G, 'A1', f'{sym}: each forecaster\'s own assumption set through the same engine (from model_engine.json, not live)', font=TITLE)
    header(G, 3, ['Set', 'Research target (12m)', 'Median', '25th pct', '75th pct', 'WACC', 'Y1 growth', 'Y1 margin', 'Terminal growth', 'DCF weight', 'Years', 'Multiples', 'Supervisor\'s note'])
    review = {x.get('agent'): x for x in ((sup.get('model') or {}).get('set_review') or [])}
    r = 4
    for nm in sorted(eng.get('sets', {}), key=lambda x: (x == 'supervisor', x)):
        s = eng['sets'][nm]; f = f'{rd}/{nm}.json'
        try: smi = ME.resolve_drivers(ME.extract(ME.load(f)))
        except Exception: smi = {}
        y1 = (smi.get('years') or [{}])[0]
        hm = smi.get('horizon_multiples') or ([smi['horizon_multiple']] if smi.get('horizon_multiple') else [])
        vals = [nm.replace('_', ' '), s['base']['horizon_value'], s['median'], s['quantiles']['q25'], s['quantiles']['q75'],
                s['base']['wacc'], y1.get('revenue_growth'), y1.get('ebit_margin'), (smi.get('terminal') or {}).get('growth'),
                ((s['base']['legs'].get('dcf') or {}).get('weight', 0.0) if V2 else (smi.get('dcf_weight', 0.0) if ME.dcf_allowed(sym) else 0.0)), len(smi.get('years') or []), '; '.join(f"{x['metric']} {x['value']} ({x.get('basis', 'ntm')})" for x in hm),
                (review.get(nm) or {}).get('main_issue', '') + (f" [{eng['warnings'][nm]}]" if nm in (eng.get('warnings') or {}) else '')]
        fm = [None, F_PS, F_PS, F_PS, F_PS, F_PCT, F_PCT, F_PCT, F_PCT, F_PCT, None, None, None]
        for c, (v, fmt) in enumerate(zip(vals, fm)): put(G, f'{L(1 + c)}{r}', v, fmt, wrap=c == 12)
        r += 1
    for nm, e in (eng.get('errors') or {}).items(): put(G, f'A{r}', nm); put(G, f'M{r}', 'engine error: ' + e); r += 1
    G.column_dimensions['A'].width = 14; G.column_dimensions['L'].width = 34; G.column_dimensions['M'].width = 70

    # ================= Sources =================
    put(R_, 'A1', f'{sym}: where each input comes from (source numbers point to the list below; F ids to the SEC fact table)', font=TITLE)
    header(R_, 3, ['Input', 'Value', 'Basis', 'Sources', 'SEC fact ids', 'Derivation'])
    r = 4
    for x in mi.get('inputs_detail') or []:
        vals = [x.get('input', ''), str(x.get('value', '')), x.get('basis', ''), ', '.join(str(i) for i in x.get('source_ids') or []),
                ', '.join(str(i) for i in x.get('fact_ids') or []), x.get('derivation', '')]
        for c, v in enumerate(vals): put(R_, f'{L(1 + c)}{r}', v, wrap=c in (0, 5))
        r += 1
    r += 1; header(R_, r, ['#', 'Source', 'Date', 'Link']); r += 1
    for i, s in enumerate(srcs, 1):
        put(R_, f'A{r}', i); put(R_, f'B{r}', s.get('title', ''), wrap=True); put(R_, f'C{r}', s.get('date', ''))
        u = s.get('url', '')
        if u: c = put(R_, f'D{r}', u); c.hyperlink = u
        r += 1
    if fx:
        r += 1; header(R_, r, ['Fact id', 'Figure', 'Value', 'Filing']); r += 1
        for x in fx.values():
            put(R_, f'A{r}', x.get('id')); put(R_, f'B{r}', x.get('label')); put(R_, f'C{r}', x.get('value'), '#,##0.00' if isinstance(x.get('value'), (int, float)) else None)
            u = x.get('url') or ''
            if u: c = put(R_, f'D{r}', u); c.hyperlink = u
            r += 1
    R_.column_dimensions['A'].width = 34; R_.column_dimensions['B'].width = 50; R_.column_dimensions['D'].width = 40; R_.column_dimensions['F'].width = 70
    e0 = eng_out(None)
    if e0.get('quarters'):  # quarterly set (model_drivers, 2026-10-29): the quarter build the fiscal years add up from
        Qt = wb.create_sheet('Quarters', index=wb.sheetnames.index('Model') + 1); qr = e0['quarters']; QS = mi.get('quarters') or {}
        put(Qt, 'A1', f'{sym} quarter build (engine values: model_engine.quarter_rows; fiscal years on Model are the sums)', font=TITLE)
        put(Qt, 'A2', 'Actual quarters are as reported (filings / XBRL); forecast quarters come from the drivers below through driver_build.quarters.', font=GREY)
        header(Qt, 4, ['Line item'] + [q_['label'] for q_ in qr])
        for j, q_ in enumerate(qr): put(Qt, f'{L(2 + j)}5', 'actual' if q_['actual'] else 'forecast', font=GREY if q_['actual'] else BLUE)
        put(Qt, 'A5', 'Reported / forecast'); put(Qt, 'A6', 'Quarter end')
        for j, q_ in enumerate(qr): put(Qt, f'{L(2 + j)}6', d(q_['end']), F_D)
        r__ = 7; segn = list((qr[0].get('segments') or {}).keys())
        for nm_ in segn:
            for kk, t in (('revenue', 'revenue'), ('ebit', 'EBIT')):
                put(Qt, f'A{r__}', f'{nm_[:44]}: {t}')
                for j, q_ in enumerate(qr): put(Qt, f'{L(2 + j)}{r__}', (q_.get('segments') or {}).get(nm_, {}).get(kk), F_M)
                r__ += 1
        for kk, t, f_ in (('revenue', 'Revenue', F_M), ('ebit', 'EBIT', F_M), (None, 'EBIT margin', F_PCT), ('da', 'D&A', F_M), ('capex', 'Capex', F_M),
                          ('sbc', 'Stock comp', F_M), ('fcff', 'Free cash flow to firm', F_M), ('eps', 'Model EPS (EBIT - interest, model tax and shares)', F_EPS)):
            put(Qt, f'A{r__}', t, font=BOLD if kk in ('revenue', 'ebit', 'eps') else None)
            for j, q_ in enumerate(qr):
                put(Qt, f'{L(2 + j)}{r__}', q_[kk] if kk else (q_['ebit'] / q_['revenue'] if q_['revenue'] else None), f_)
            r__ += 1
        r__ += 1; put(Qt, f'A{r__}', 'Drivers by quarter', font=BOLD); r__ += 1
        lab_all = QS.get('labels') or []; off = 4  # the base year's quarters come first in the set
        for x in raw.get('drivers') or []:
            if not x.get('quarters'): continue
            put(Qt, f'A{r__}', f"{x.get('label', x['id'])[:40]} ({x.get('unit', '')}, {x.get('agg', 'mean')})")
            for j, q_ in enumerate(qr):
                k_ = lab_all.index(q_['label']) if q_['label'] in lab_all else j + off
                put(Qt, f'{L(2 + j)}{r__}', x['quarters'][k_], '#,##0.0000' if abs(x['quarters'][k_]) < 10 else F_M, BLUE)
            r__ += 1
        Qt.column_dimensions['A'].width = 50
        for j in range(len(qr)): Qt.column_dimensions[L(2 + j)].width = 12
        Qt.freeze_panes = 'B5'
    for ws in wb.worksheets: ws.sheet_view.showGridLines = False
    out = out or f'{rd}/std_model_{sym}.xlsx'
    wb.save(out); print('wrote', out)
    return out


def verify(date, sym, path=None):
    """Recalculate the workbook in LibreOffice and compare with model_engine.json."""
    rd = f'{HERE}/runs/{date}/{sym}'; path = path or f'{rd}/std_model_{sym}.xlsx'
    eng = json.load(open(f'{rd}/model_engine.json'))['sets']['supervisor']
    tmp = tempfile.mkdtemp(); shutil.copy(path, tmp); fn = os.path.join(tmp, os.path.basename(path))
    env = dict(os.environ, HOME=tmp)
    subprocess.run(['soffice', '--headless', '--norestore', '--convert-to', 'xlsx', '--outdir', tmp + '/out', fn], env=env,
                   capture_output=True, timeout=300)
    wb = openpyxl.load_workbook(tmp + '/out/' + os.path.basename(path), data_only=True); S = wb['Summary']
    got = {S.cell(r, 1).value: S.cell(r, 2).value for r in range(3, 26)}
    pairs = [('Model target (12-month median)', eng['median']), ('Research target (12m) (no scenario deltas)', eng['base']['horizon_value']),
             ('25th percentile', eng['quantiles']['q25']), ('75th percentile', eng['quantiles']['q75'])]
    ok = True
    for k, want in pairs:
        g = got.get(k); bad = not isinstance(g, (int, float)) or abs(g - want) > 0.01 * max(1, abs(want)) * 0.05
        ok &= not bad; print(f'{k}: sheet {g} vs engine {want:.4f}' + ('  MISMATCH' if bad else ''))
    for k, s in (eng.get('scenarios') or {}).items(): print(f'scenario {k}: engine {s["value"]:.4f}')
    errs = [f'{w.title}!{c.coordinate}={c.value}' for w in wb for row in w.iter_rows() for c in row
            if isinstance(c.value, str) and c.value.startswith(('#VALUE', '#NAME', '#REF', '#DIV', '#N/A', 'Err:', '#NUM'))]
    if errs: ok = False; print('ERROR CELLS', len(errs), errs[:30])
    print('VERIFY', 'OK' if ok else 'FAILED'); return ok

def export(date, sym, path=None, outdir=None):
    """Google Sheets payloads (the Drive connector cannot take a binary upload reliably): per tab, a values grid for
    spreadsheets.values.update (USER_ENTERED) and one batchUpdate list for tabs, number formats, fonts and widths.
    Writes runs/DATE/SYM/gsheet/{tabs.json, values_<n>.json, format.json}."""
    rd = f'{HERE}/runs/{date}/{sym}'; path = path or f'{rd}/std_model_{sym}.xlsx'; outdir = outdir or f'{rd}/gsheet'
    os.makedirs(outdir, exist_ok=True)
    wb = openpyxl.load_workbook(path); tabs = []
    fmt_reqs, widths = [], []
    def nf(f):
        if not f or f == 'General': return None
        if '%' in f: return {'type': 'PERCENT', 'pattern': f}
        if 'yy' in f: return {'type': 'DATE', 'pattern': f}
        return {'type': 'NUMBER', 'pattern': f}
    for sid, ws in enumerate(wb.worksheets):
        tabs.append(dict(sheetId=sid, title=ws.title))
        R, C = ws.max_row, ws.max_column
        grid = [['' for _ in range(C)] for _ in range(R)]
        runs = {}
        for row in ws.iter_rows():
            for c in row:
                v = c.value
                if v is None: continue
                if c.hyperlink and isinstance(v, str) and v.startswith('http'):
                    v = '=HYPERLINK("' + v.replace('"', '%22') + '","' + v.replace('"', "'")[:200] + '")'
                elif isinstance(v, (dt.datetime, dt.date)): v = f'=DATE({v.year},{v.month},{v.day})'
                elif isinstance(v, str) and not v.startswith('=') and (v[:1] in '+-@' or re.fullmatch(r'[\d.,%$ ()-]+', v)): v = "'" + v
                grid[c.row - 1][c.column - 1] = v
                key = (json.dumps(nf(c.number_format)), bool(c.font and c.font.b), (c.font.color.rgb if c.font and c.font.color and isinstance(c.font.color.rgb, str) else None),
                       bool(c.font and c.font.i), int(c.font.sz) if c.font and c.font.sz and c.font.sz != 11 else None,
                       c.fill.fgColor.rgb if c.fill and c.fill.fill_type == 'solid' else None, bool(c.alignment and c.alignment.wrap_text))
                if key == ('null', False, None, False, None, None, False) or key[2] in ('FF000000',) and key[:2] == ('null', False) and not any(key[3:]): continue
                runs.setdefault(key, set()).add((c.row - 1, c.column - 1))
        # values in blocks of non-blank rows, each anchored at its own start row (2026-10-26: workers miscounted long runs
        # of blank rows in one grid and shifted the Calc blocks; a block breaks at 3+ blank rows or 400 rows)
        filled = [any(v != '' for v in row) for row in grid]; blocks, i = [], 0
        while i < R:
            if not filled[i]: i += 1; continue
            j = i; last = i
            while j < R and j - i < 400 and (filled[j] or j - last < 3):
                if filled[j]: last = j
                j += 1
            blocks.append((i, last + 1)); i = last + 1
        for k_, (a_, b_) in enumerate(blocks):
            json.dump(dict(range=f"'{ws.title}'!A{a_ + 1}", values=grid[a_:b_]), open(f'{outdir}/values_{sid}_{k_}.json', 'w'), separators=(',', ':'), ensure_ascii=False)
        for key, cells in runs.items():
            nfm, bold, color, ital, size, fill, wrap = key; nfm = json.loads(nfm)
            fmt, fields = {}, []
            if nfm: fmt['numberFormat'] = nfm; fields.append('userEnteredFormat.numberFormat')
            tf = {}
            if bold: tf['bold'] = True
            if ital: tf['italic'] = True
            if size: tf['fontSize'] = size
            if color and color not in ('FF000000', '00000000'):
                h = color[-6:]; tf['foregroundColor'] = {k: int(h[i:i + 2], 16) / 255 for k, i in (('red', 0), ('green', 2), ('blue', 4))}
            if tf: fmt['textFormat'] = tf; fields += ['userEnteredFormat.textFormat.' + k for k in tf]
            if fill: h = fill[-6:]; fmt['backgroundColor'] = {k: int(h[i:i + 2], 16) / 255 for k, i in (('red', 0), ('green', 2), ('blue', 4))}; fields.append('userEnteredFormat.backgroundColor')
            if wrap: fmt['wrapStrategy'] = 'WRAP'; fields.append('userEnteredFormat.wrapStrategy')
            if not fields: continue
            # rectangles: contiguous column runs per row, then merge identical runs on consecutive rows
            byrow = {}
            for r_, c_ in cells: byrow.setdefault(r_, []).append(c_)
            spans = {}
            for r_, cs in byrow.items():
                cs.sort(); a = b = cs[0]
                for c_ in cs[1:] + [None]:
                    if c_ is not None and c_ == b + 1: b = c_; continue
                    spans.setdefault((a, b), []).append(r_)
                    if c_ is not None: a = b = c_
            for (a, b), rows in spans.items():
                rows.sort(); s0 = p0 = rows[0]
                for r_ in rows[1:] + [None]:
                    if r_ is not None and r_ == p0 + 1: p0 = r_; continue
                    fmt_reqs.append({'repeatCell': {'range': {'sheetId': sid, 'startRowIndex': s0, 'endRowIndex': p0 + 1, 'startColumnIndex': a, 'endColumnIndex': b + 1},
                                                    'cell': {'userEnteredFormat': fmt}, 'fields': ','.join(fields)}})
                    if r_ is not None: s0 = p0 = r_
        for col, d_ in ws.column_dimensions.items():
            if d_.width:
                ci = openpyxl.utils.column_index_from_string(col) - 1
                widths.append({'updateDimensionProperties': {'range': {'sheetId': sid, 'dimension': 'COLUMNS', 'startIndex': ci, 'endIndex': ci + 1},
                                                             'properties': {'pixelSize': int(d_.width * 7)}, 'fields': 'pixelSize'}})
        if ws.freeze_panes:
            from openpyxl.utils.cell import coordinate_from_string
            col, row = coordinate_from_string(ws.freeze_panes)
            fmt_reqs.append({'updateSheetProperties': {'properties': {'sheetId': sid, 'gridProperties': {'frozenRowCount': row - 1, 'frozenColumnCount': openpyxl.utils.column_index_from_string(col) - 1}},
                                                       'fields': 'gridProperties.frozenRowCount,gridProperties.frozenColumnCount'}})
    setup = [{'updateSheetProperties': {'properties': {'sheetId': 0, 'title': tabs[0]['title'], 'gridProperties': {'hideGridlines': True}}, 'fields': 'title,gridProperties.hideGridlines'}}] + \
            [{'addSheet': {'properties': {'sheetId': t['sheetId'], 'title': t['title'], 'gridProperties': {'rowCount': max(wb.worksheets[t['sheetId']].max_row + 20, 100), 'columnCount': max(wb.worksheets[t['sheetId']].max_column + 2, 26), 'hideGridlines': True}}}} for t in tabs[1:]]
    json.dump(setup, open(f'{outdir}/setup.json', 'w'), separators=(',', ':'))
    allf = fmt_reqs + widths
    for k in range(0, len(allf), 120): json.dump(allf[k:k + 120], open(f'{outdir}/format_{k // 120}.json', 'w'), separators=(',', ':'))
    json.dump(tabs, open(f'{outdir}/tabs.json', 'w'))
    for f in sorted(glob.glob(f'{outdir}/*.json')): print(os.path.basename(f), os.path.getsize(f))
    print(len(fmt_reqs + widths), 'format requests')

if __name__ == '__main__':
    a = sys.argv[1:]
    if a and a[0] == 'verify': sys.exit(0 if verify(a[1], a[2].upper(), a[3] if len(a) > 3 else None) else 1)
    elif a and a[0] == 'export': export(a[1], a[2].upper())
    elif len(a) >= 2: build(a[0], a[1].upper(), a[2] if len(a) > 2 else None)
    else: print(__doc__); sys.exit(1)
