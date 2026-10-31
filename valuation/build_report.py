"""Build report.html from runs/DATE/*/result.json (plus agent catalysts and names).
Usage: python3 build_report.py DATE [SYM ...] [--archive] [--out PATH]
  no SYMs: the active names (active_names.py: held, planned, candidates, orders, research <= 21 days) -> report.html
  --archive: every other researched name, same layout -> archive.html (2026-10-19: the page was 13.7MB of a 16MB cap)
  explicit SYMs: exactly those names -> report.html (or archive.html with --archive); --out writes elsewhere"""
import glob, json, os, re, sys, datetime as dt
import fscache; fscache.install()  # 2026-10-21: read-once cache for the shared network folder (fscache.py; same output)
sys.path.insert(0, '/mnt/project-files/adhoc'); from page_search import inject as add_search  # ticker/company search box (Kilian 2026-10-19)
_tr = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', 'trades.json')))
_HELD = {t['symbol'] for t in (_tr.get('trades', []) if isinstance(_tr, dict) else _tr)}
HERE = os.path.dirname(os.path.abspath(__file__))
_ARGV = sys.argv[1:]; ARCHIVE = '--archive' in _ARGV; OUT = None
if ARCHIVE: _ARGV.remove('--archive')
if '--out' in _ARGV: _i = _ARGV.index('--out'); OUT = _ARGV[_i + 1]; del _ARGV[_i:_i + 2]
date = _ARGV[0]
_EXPLICIT = [s.upper() for s in _ARGV[1:]]
syms = _EXPLICIT or sorted(os.path.basename(os.path.dirname(p)) for p in glob.glob(f'{HERE}/runs/{date}/*/result.json'))
_REMOVED = set(json.load(open(f'{HERE}/data/review_settings.json')).get('removed_names') or [])  # Kilian 2026-10-07: defense names off the page
syms = [s for s in syms if s not in _REMOVED]
# Kilian 2026-10-14 20:24Z "update price targets then, please make this automatic": a full run (supervisor.json + result.json)
# finished after DATE joins the page at once, without waiting for the nightly re-mark: a new name is added, and a name not in
# trades.json shows its newest full run. Held names keep DATE's result (their levels come from review.py at DATE).
_RD = {}
if not _EXPLICIT:
    # only run dates after DATE can qualify, so list just those (the runs folder is on a slow network mount)
    for _d2 in sorted(d for d in os.listdir(f'{HERE}/runs') if date < d <= dt.date.today().isoformat() and os.path.isdir(f'{HERE}/runs/{d}')):
        for _p in sorted(glob.glob(f'{HERE}/runs/{_d2}/*/supervisor.json')):
            _s2 = _p.split('/')[-2]
            if os.path.exists(os.path.dirname(_p) + '/result.json') and _s2 not in _REMOVED and (_s2 not in syms or _s2 not in _HELD):
                _RD[_s2] = _d2
    syms = sorted(set(syms) | set(_RD))
    # 2026-10-19: a held name whose quotes were stale tonight (no result.json for DATE) stays on the page from its latest
    # earlier run, flagged stale, instead of dropping off (MOD on 2026-10-19: Cboe delayed feed still on 10-16).
    try:
        sys.path.insert(0, HERE); import book as _bk
        _NOW = {p['symbol'] for p in _bk.load()['positions'] if p.get('shares')}  # live book from trades.json
    except Exception: _NOW = set()
    _STALE = {}
    _prev = sorted((d for d in os.listdir(f'{HERE}/runs') if d < date and os.path.isdir(f'{HERE}/runs/{d}')), reverse=True)[:10]
    for _s3 in sorted(_NOW - set(syms) - _REMOVED):
        for _d3 in _prev:
            if os.path.exists(f'{HERE}/runs/{_d3}/{_s3}/result.json'):
                _RD[_s3] = _STALE[_s3] = _d3; break
    syms = sorted(set(syms) | set(_STALE))
    # 2026-10-19: the main page carries the active names only; --archive builds the rest on archive.html (same layout)
    sys.path.insert(0, HERE); import active_names as _an
    _ACT = set(_an.active_names(date)) | set(_RD)
    _ARCH_N = sum(1 for s in syms if s not in _ACT)
    syms = [s for s in syms if (s not in _ACT) == ARCHIVE]
else: _ARCH_N = None
KNOWN = {'AAPL': 'Apple Inc.', 'EL': 'The Estée Lauder Companies', 'GOOGL': 'Alphabet Inc.', 'GPRO': 'GoPro, Inc.', 'META': 'Meta Platforms, Inc.', 'TSLA': 'Tesla, Inc.', 'SPCX': 'SpaceX', 'CRDO': 'Credo Technology Group', 'UUUU': 'Energy Fuels Inc.', 'BE': 'Bloom Energy Corporation', 'AVGO': 'Broadcom Inc.', 'MCHP': 'Microchip Technology Inc.', 'ERO': 'Ero Copper Corp.', 'VST': 'Vistra Corp.', 'VRT': 'Vertiv Holdings Co'}
results, cats, names, agents = [], {}, {}, {}

def memo_md(m):
    """Markdown version of a pitch memo, for the page's download button."""
    src = {x.get('id'): x for x in m.get('sources') or []}
    ref = lambda ids: ''.join(f' [{i}]' for i in (ids or []) if i in src)
    v = m.get('valuation') or {}; t = m.get('trade') or {}
    ttl = re.sub(rf"^{m['symbol']}:\s*", '', m.get('title', ''))
    L = [f"# {m['symbol']}: {ttl}", f"*As of the {m.get('as_of')} close. {m.get('call', '')}: {m.get('call_reason', '')}*", '',
         m.get('one_liner', ''), '', '## Thesis']
    L += [f"{i}. **{x.get('point', '')}** {x.get('evidence', '')}{ref(x.get('source_ids'))}" for i, x in enumerate(m.get('thesis') or [], 1)]
    L += ['', '## Variant view', m.get('variant_view', ''), '', '## Valuation',
          f"Price ${v.get('price')}, target ${v.get('target')} ({v.get('upside_to_target_pct')}%), research target (12m) ${v.get('fair_value')} ({v.get('upside_to_fair_value_pct')}%). {v.get('methods', '')}",
          '', '## Catalysts', '| Date | Event | Why it moves | Dir | Size | Watch |', '|---|---|---|---|---|---|']
    L += [f"| {c.get('date')}{' (est.)' if c.get('date_status') == 'estimated' else ''} | {c.get('event', '')}{ref(c.get('source_ids'))} | {c.get('why_it_moves', '')} | { {'?': 'either'}.get(c.get('direction', ''), c.get('direction', ''))} | {c.get('magnitude', '')} | {c.get('watch', '')} |".replace('\n', ' ')
          for c in m.get('catalysts') or []]
    L += ['', '## Risks'] + [f"- **{x.get('risk', '')}** Signpost: {x.get('signpost', '')}{ref(x.get('source_ids'))}" for x in m.get('risks') or []]
    L += ['', '## Trade', f"- Position: {t.get('position', '')}", f"- Entry: {t.get('entry', '')}", f"- Stop ${t.get('stop')}, take-profit ${t.get('take_profit')}",
          f"- Exit or add: {t.get('exit_or_add', '')}", '', '## What changes our mind'] + [f'- {x}' for x in m.get('what_changes_our_mind') or []]
    L += ['', '## Sources'] + [f"{x.get('id')}. " + (f"[{x.get('title', '')}]({x.get('url', '')})" if str(x.get('url', '')).startswith('http') else x.get('title', '')) + f" {x.get('date', '')}" for x in m.get('sources') or []]
    return '\n'.join(L) + '\n'

_SECTORS = json.load(open(f'{HERE}/data/sectors.json'))
def _lots(sym):  # open FIFO lots from trades.json (Kilian 2026-10-05: entry price on Positions; trims take the oldest lots first, as the tax model does)
    lots = []
    for t in sorted((t for t in (_tr.get('trades', []) if isinstance(_tr, dict) else _tr) if t['symbol'] == sym), key=lambda t: t.get('date', '')):
        if t.get('side', 'buy') == 'buy': lots.append(dict(date=t['date'], shares=t['shares'], price=t['price'], fees=t.get('fees', 0)))
        else:
            q = t['shares']
            while q > 1e-9 and lots:
                take = min(q, lots[0]['shares']); lots[0]['fees'] *= (lots[0]['shares'] - take) / lots[0]['shares']; lots[0]['shares'] -= take; q -= take
                if lots[0]['shares'] <= 1e-9: lots.pop(0)
    n = sum(l['shares'] for l in lots)
    if not n: return None
    px = sum(l['shares']*l['price'] for l in lots) / n
    return dict(shares=n, avg_price=round(px, 4), avg_cost_with_fees=round((sum(l['shares']*l['price'] + l['fees'] for l in lots)) / n, 4), first=lots[0]['date'], lots=lots)
_NOFEE = [  # Kilian 2026-10-13 23:47Z "stop referencing fees": broker-fee wording in older memos is dropped on the page
    (r' ?\(\+\$\d+(?:\.\d+)? fee, cost basis \$[\d,.]+, ', ' ('),
    (r'~\$2,467 after tax and the \$6 fee \(derived: 2,519\.46 - 46\.66 - 6; \$6 fee per decider\.json\)', '~$2,473 after tax (derived: 2,519.46 - 46.66)'),
    (r';? ?\$\d+ fee per trade', ''),
]
def _nofee(o):
    if isinstance(o, dict): return {k: _nofee(v) for k, v in o.items()}
    if isinstance(o, list): return [_nofee(v) for v in o]
    if isinstance(o, str):
        for p, q in _NOFEE: o = re.sub(p, q, o)
    return o
_MEMO_EXTRA = set(json.load(open(f'{HERE}/data/review_settings.json')).get('memo_extra', []))  # unbought names with a memo/model on request
print('no sector for:', [x for x in syms if x not in _SECTORS] or 'none')
sys.path.insert(0, HERE); import book as _bookmod
import combine as _cb
_BOOK_POS = {p['symbol']: p for p in _bookmod.load()['positions'] if p.get('shares')}
_BOOK_NOW = list(_BOOK_POS)
_RM = []
def _live_overlap(s):
    """Correlation of s to each name held today, from the option-calibrated risk model (riskcal.py) at this date."""
    if not _RM:
        import riskcal; _RM.append(riskcal.load(date))
    M = _RM[0]; others = [h for h in _BOOK_NOW if h != s]
    if not M.covered(s): return 'Not in the risk model, so no correlation to the book is shown.'
    rows = sorted(((round(M.corr(s, h), 2), h) for h in others if M.covered(h)), reverse=True)
    if not rows: return 'No other names held.'
    return (f"Correlation to the book held on {date} (risk model): " + ', '.join(f"{h} {c:.2f}" for c, h in rows[:4])
            + (f"; the other {len(rows) - 4} are lower" if len(rows) > 4 else '') + '.')
try: _LIVE = json.load(open(f'{HERE}/data/live_quotes.json')).get('quotes', {})
except FileNotFoundError: _LIVE = {}
for s in syms:
    rd = f'{HERE}/runs/{_RD.get(s, date)}/{s}'
    r = json.load(open(f'{rd}/result.json'))
    if s in globals().get('_STALE', {}): r['stale_as_of'] = _STALE[s]  # shown as a 'stale' tag on the page
    _lq = _LIVE.get(s)  # Kilian 2026-10-08 "it should also be updating last price from the intraday yahoo pulls" (live_quotes.py)
    if _lq and _lq.get('date') and _lq['date'] > date and _lq.get('price'):
        r['close_price'], r['close_date'] = r['spot'], date
        _S = r['spot'] = _lq['price']; r['spot_time_utc'] = _lq['time_utc']
        # Kilian 2026-10-08 "the other columns need to be fixed with it too": everything measured from the price is re-measured
        # from the live Last on the close's distribution (combine.py's own formulas); targets and levels stay from the close
        _lad = r.get('ladder') or []
        if _lad:
            _Ks = [l['K'] for l in _lad]; _sf = _cb.survival_fn(_Ks, [l['final'] for l in _lad])
            r['p_gain'] = round(_sf(_S), 4)
            if 'market' in _lad[0] and r.get('market_only'): r['market_only']['p_gain'] = round(_cb.survival_fn(_Ks, [l['market'] for l in _lad])(_S), 4)
            try: _T = json.load(open(f'{HERE}/priors/{date}/{s}.json'))['horizon_years']
            except Exception: _T = 1.0
            r['p_touch_tp'] = round(_cb.touch_prob(_S, r['take_profit'], _T, _sf, r['target']), 3) if r['take_profit'] > _S else 1.0
            r['p_touch_sl'] = round(_cb.touch_prob(_S, r['stop_loss'], _T, _sf, r['target']), 3) if r['stop_loss'] < _S else 1.0
        _tq = r.get('take_profit_q75') or r['take_profit']  # reward/risk stays on the 75th percentile (review.py keeps it for the rubric)
        r['reward_risk'] = round((_tq - _S) / (_S - r['stop_loss']), 2) if r['stop_loss'] < _S else None
        if r.get('fair_value_median'): r['fair_value_gap'] = round(r['fair_value_median'] / _S - 1, 4)
    r.pop('ladder', None); results.append(r)
    _bp = _BOOK_POS.get(s)  # Kilian 2026-10-07 "move panw back to researched and add amzn": the Holdings tab follows today's fills, not the book at combine time
    if not _bp: r.pop('position', None)
    else:
        _S = r['spot']; _n = _bp['shares']
        r['position'] = dict(shares=_n, cost_basis=round(_bp['cost_basis'], 2), market_value=round(_n*_S, 2), unrealized=round(_n*_S - _bp['cost_basis'], 2),
                             to_target=round(_n*(r['target'] - _S), 2), to_take_profit=round(_n*(r['take_profit'] - _S), 2), to_stop=round(_n*(r['stop_loss'] - _S), 2))
    r['sector'] = _SECTORS.get(s, 'Other')  # data/sectors.json, groups the Researched tab
    try:  # entry level (Kilian 2026-10-21 22:10Z "just discount using the adjusted-capm formula"): required 12-month price return =
        # T-bill + 7.69% x (0.768 + 0.232 beta) (market_dist.premium, French beta deciles) less the trailing dividend yield
        _pr = json.load(open(f'{HERE}/priors/{date}/{s}.json')); import divs
        # 2026-10-27: dividend yield taken at the entry price itself, E = (target + dividends) / (1 + T-bill + premium), the same level
        # review.py's first add uses (VST: the page showed 129.26 with the yield at spot while the add sat at 129.33)
        _b = _pr['rf'] + _pr['premium']; _E = (r['target'] + divs.dps(s)) / (1 + _b)
        r['req_return'] = round(_b - divs.dy(s, _E), 6); r['req_beta'] = _pr.get('beta')
        # 2026-10-29 (Kilian: "how the agents got there" is outdated): the market leg's inputs for "How the target was built"
        r['market_leg'] = {k: _pr.get(k) for k in ('market_date', 'atm_iv_1y', 'vol_mode', 'realized_vol_1y', 'rf', 'beta', 'premium', 'low_confidence')}
    except Exception: pass
    _rl = r.get('research_leg') or {}
    if _rl.get('source') == 'standard_model' and os.path.exists(f"{HERE}/{_rl.get('file', '')}"):  # the driver model behind the research leg
        try:
            _m = json.load(open(f"{HERE}/{_rl['file']}")); _st = _m['sets']['supervisor']; _bs = _st['base']
            _fin = ((_bs.get('deep') or {}).get('financing') or {}).get('detail') or []
            r['model_how'] = dict(
                quarterly=bool(_bs.get('quarters')), horizon=_bs.get('horizon'), value=_bs.get('horizon_value'),
                legs=[dict(name={'sotp': 'Sum of the parts', 'sotp_parts': 'Sum of the parts', 'dcf': 'DCF'}.get(k, re.sub(r'^(multiple|custom): ', '', k)),
                           value=v.get('value'), weight=(v.get('weight') or 0) / (sum((x.get('weight') or 0) for x in _bs['legs'].values()) or 1),
                           multiple=v.get('multiple')) for k, v in (_bs.get('legs') or {}).items()],  # weights as the engine applies them (sum to 1)
                scenarios=[dict(name={'bear': 'downside', 'bull': 'upside'}.get(k, k), prob=v.get('prob'), pctl=v.get('percentile'), value=v.get('value'), story=(lambda x: x if len(x) <= 260 else x[:257].rstrip() + '…')(re.sub(r'^(\[[^\]]*\]\s*)+', '', v.get('story') or '')))
                           for k, v in sorted((_st.get('scenarios') or {}).items(), key=lambda kv: kv[1].get('percentile', 50))],
                mix_median=_m.get('model_target'), mix_mean=_st.get('mean'),
                financing=[{k: e.get(k) for k in ('name', 'kind', 'probability', 'by_horizon', 'expected_shares_added', 'expected_net_debt_change')} for e in _fin],
                update_round=os.path.exists(f"{HERE}/runs/{r.get('research_as_of')}/{s}/update.json"))
        except Exception as _e: print(f'{s}: model_how skipped ({str(_e)[:80]})')
    if os.path.exists(f'{rd}/review.json'): r['review'] = json.load(open(f'{rd}/review.json'))  # trim / hold / add (review.py)
    if os.path.exists(f'{rd}/allocation.json'): r['allocation'] = json.load(open(f'{rd}/allocation.json'))  # names not held
    if os.path.exists(f'{rd}/decider.json'):  # price-only trim/add levels (review.py)
        r['decider'] = {k: v for k, v in json.load(open(f'{rd}/decider.json')).items() if k != 'schedule'}
        for _x in ((r['decider'].get('marginal') or {}).get('trim') or []) + ((r['decider'].get('marginal') or {}).get('add') or []):
            _x['pct_from_last'] = round(_x['price'] / r['spot'] - 1, 4)  # measured from the live Last when there is one
    try:  # weighted analyst consensus firm list (analyst_weights/)
        aw = json.load(open(f'{HERE}/data/analyst_weighted.json')).get(s)
        clean = lambda d: {k: (None if isinstance(v, float) and v != v else v) for k, v in d.items()}  # NaN is not valid JSON
        if aw: r['analyst_firms'] = dict(clean({k: v for k, v in aw.items() if k != 'firms'}), firms=[clean(f) for f in aw.get('firms') or []])
    except FileNotFoundError: pass
#    if os.path.exists(f'{rd}/news.json'): r['news'] = json.load(open(f'{rd}/news.json'))  # daily news check, held names (agents/news.md)
    _sp = f"{HERE}/runs/{r.get('research_as_of') or date}/{s}/supervisor.json"  # Kilian 2026-10-06: "everything must be cited accurately"
    if os.path.exists(_sp): r['citation_audit'] = json.load(open(_sp)).get('citation_audit')
    _me = f"{HERE}/runs/{r.get('research_as_of') or date}/{s}/model_engine.json"  # standard model, trial (Option 1, Kilian 2026-10-13)
    if os.path.exists(_me):
        _m = json.load(open(_me))
        if _m.get('model_target'):
            r['model_trial'] = dict(target=_m['model_target'], q25=_m['model_quantiles']['q25'], q75=_m['model_quantiles']['q75'],
                                    base_value=_m['model_fair_value'],
                                    agent_values=[v['base']['horizon_value'] for k, v in sorted(_m['sets'].items()) if k.startswith('agent_')])
    if os.path.exists(f'{rd}/explain.json'): r['explain'] = json.load(open(f'{rd}/explain.json'))  # how the agents got there
    if os.path.exists(f'{rd}/model_{s}.xlsx'):  # Excel model (model_xlsx.py), embedded so the page can offer it via the downloads capability
        import base64
        r['model'] = f'model_{s}.xlsx'
        # page cap is 16MB: embed the workbook only for held names and names not marked low confidence;
        # the rest stay in valuation/models/ and the button says so
        if True:  # every name (Kilian 2026-10-26: models for researched names too); the page is ~4MB of its 16MB cap
            r['model_b64'] = base64.b64encode(open(f'{rd}/model_{s}.xlsx', 'rb').read()).decode()
    if s in _HELD: r['entry'] = _lots(s)
    _cfg = json.load(open(f'{HERE}/data/review_settings.json'))  # planned buys (Kilian 2026-10-05): open limit orders and staged new names
    _lo = (_cfg.get('limit_orders') or {}).get(s); _st = (_cfg.get('staged_entries') or {}).get(s)
    if _lo and s not in _HELD: r['plan'] = dict(kind='limit', shares=_lo['shares'], price=_lo['limit'], when=f"{_lo.get('type', 'limit')}, placed {_lo.get('placed', '')}", condition='fills only if the price touches the limit')
    elif isinstance(_st, dict) and _st.get('new_name') and s not in _HELD: r['plan'] = dict(kind='staged', shares=('model' if _st.get('model_sized') else _st['full_shares']), price=None, when=f"after {_st.get('event', '')}", condition=_st.get('condition', ''))
    _ss = (r.get('allocation') or {}).get('supervisor_sizing')
    if isinstance(_ss, dict):  # Kilian 2026-10-07 "the overlaps are wrong/outdated": the supervisor wrote them against the book of its day
        _ss['overlap'] = _live_overlap(s)
        if s in _BOOK_NOW:  # its tranche plan was written for a new buy
            _ss['entry'] = (f"Held. Staged add after {_st.get('event', 'the next print')}, sized then by the model on the $24k book; condition: {_st.get('condition', '')}"
                            if isinstance(_st, dict) and _st.get('full_shares') else "Held, no staged add. Trims and adds follow the levels in the Holdings tab.")
    _show = (r.get('allocation') or {}).get('shares') or s in _HELD or s in _MEMO_EXTRA  # bought or held names, plus names Kilian asked memos for
    # pitch memo (agents/memo.md); looked up only for names that show one (each glob costs ~1s on the network mount)
    mems = sorted(f for f in glob.glob(f'{HERE}/runs/*/{s}/memo.json') if f.split('/')[-3] <= date) if _show else []
    # a memo still in its citation audit stays off the page: MEMO_HOLD=SYM,SYM (no unverified claims, Kilian 2026-10-06)
    if s in (os.environ.get('MEMO_HOLD') or '').upper().split(','): mems = [f for f in mems if f.split('/')[-3] != date]
    if mems and _show:  # memos only for names the book buys (or asked for)
        r['memo'] = json.load(open(mems[-1]))
        r['memo'] = _nofee(r['memo'])
        _t = r['memo'].get('trade')
        if s in _HELD and isinstance(_t, dict) and r.get('take_profit_basis'):  # Kilian 2026-10-06: one upside exit, the sizing rule's
            _t['take_profit'] = round(r['take_profit'], 2); _t['take_profit_note'] = r['take_profit_basis'] + f" (as of {date})"
            if r['take_profit_basis'].startswith(('sizing', 'expected value')) and _t.get('take_profit_q75_memo') is None:
                _t['exit_or_add'] = (_t.get('exit_or_add') or '') + f" [Updated {date}: the one upside exit is now ${r['take_profit']:,.2f}, where the 12-month expected value no longer beats the 4% cash sleeve and the model sells every share; any other take-profit figure in this memo is superseded.]"
        # Kilian 2026-10-07: no .md download on the page; the memo's Google Doc and the model's Google Sheet sit side by side
        try: r['memo_gdoc'] = json.load(open(f'{HERE}/data/memo_gdocs.json')).get(mems[-1].split('/')[-3], {}).get(s)  # Google Docs copy
        except FileNotFoundError: pass
    if True:  # Google Sheets model for every name on the page (Kilian 2026-10-26 12:29Z "add the models for researched names"; was bought names only)
        try: gs = json.load(open(f'{HERE}/data/model_gsheets.json')); ks = [k for k in sorted(gs) if k <= max(date, dt.date.today().isoformat()) and s in gs[k]]  # a Sheet published today shows on today's page even before tonight's re-mark
        except FileNotFoundError: ks = []
        if ks:
            r['model_gsheet'] = gs[ks[-1]][s]
            if os.path.exists(f'{HERE}/runs/{ks[-1]}/{s}/std_model_{s}.xlsx'): r['model_gsheet_std'] = ks[-1]  # Kilian 2026-10-13: the Sheet is the standard model (model_gsheet.py)
    ad = f"{HERE}/runs/{r.get('research_as_of') or date}/{s}"  # agents' research may predate a daily re-mark
    ag = [json.load(open(f)) for f in sorted(glob.glob(f'{ad}/agent_*.json'))]
    seen, cl = set(), []
    for a in ag:
        for c in a.get('catalysts', []):
            k = str(c.get('date', ''))[:10] or str(c.get('event', ''))[:30].lower()
            if k not in seen: seen.add(k); cl.append(c)
    cats[s] = sorted(cl, key=lambda c: str(c.get('date', '')))[:8]
    nm = [a.get('company') or a.get('name') for a in ag if a.get('company') or a.get('name')]
    names[s] = nm[0] if nm else KNOWN.get(s, '')
def _priced():
    """Every ticker with a result.json in any run folder (same set as globbing runs/*/*/result.json, which takes ~70s on the
    network mount): list each run date, and stat result.json only for tickers not already known to have one."""
    have = {r['symbol'] for r in results}
    for d in sorted((d for d in os.listdir(f'{HERE}/runs') if os.path.isdir(f'{HERE}/runs/{d}')), reverse=True):
        for x in os.listdir(f'{HERE}/runs/{d}'):
            if x not in have and os.path.exists(f'{HERE}/runs/{d}/{x}/result.json'): have.add(x)
    return sorted(have)
# Friday idea round: the newest ideas/<date>/ on or before DATE (screen always; scouts' themes and the ranker's list once written)
idea = None
for d in sorted(glob.glob(f'{HERE}/ideas/*/screen.json'), reverse=True):
    idir = os.path.dirname(d)
    if os.path.basename(idir) > date: continue
    sc = json.load(open(d)); idea = dict(as_of=sc['as_of'], universe=sc.get('priced'), screen={}, themes={})
    keep = ('symbol', 'name', 'market_cap', 'last', 'score', 'mom_12_1', 'mom_3m', 'off_high', 'vol_1y', 'vol_surge')
    for sec, v in sc['sectors'].items():
        idea['screen'][sec] = {k: [{x: n.get(x) for x in keep} for n in v[k]] for k in ('leaders', 'pullbacks')}
    for f in glob.glob(f'{idir}/scout_*.json'):
        try: j = json.load(open(f)); idea['themes'][j.get('sector') or os.path.basename(f)[6:-5]] = j.get('themes', [])
        except Exception: pass
    if os.path.exists(f'{idir}/ideas.json'): idea.update(json.load(open(f'{idir}/ideas.json')))
    idea['priced'] = _priced()
    break
data = dict(score=(json.load(open(f'{HERE}/runs/{date}/score.json')) if os.path.exists(f'{HERE}/runs/{date}/score.json') else None), portfolio=(json.load(open(f'{HERE}/runs/{date}/portfolio.json')) if os.path.exists(f'{HERE}/runs/{date}/portfolio.json') else None), review_summary=(json.load(open(f'{HERE}/runs/{date}/review_summary.json')) if os.path.exists(f'{HERE}/runs/{date}/review_summary.json') else None), ideas=idea, as_of=date,  # the run's close date; a name re-marked intraday carries today's date in its own as_of (2026-10-30)
            research=sorted({r.get('research_as_of') or date for r in results}), horizon=results[0]['horizon'] if results else None, generated=dt.date.today().isoformat(),
            results=results, catalysts=cats, names=names, agents=agents)
def _scrub(o):  # Kilian 2026-10-07 20:58Z "rtx is still on price targets, remove please": removed names leave every table, not just the ticker list
    if isinstance(o, dict): return {k: _scrub(v) for k, v in o.items() if k not in _REMOVED}
    if isinstance(o, list): return [_scrub(v) for v in o if not (isinstance(v, str) and v in _REMOVED) and not (isinstance(v, dict) and v.get('symbol') in _REMOVED)]
    return o
def _jl(p):
    try: return json.load(open(p))
    except Exception: return None
# 2026-10-19 "Waiting on you": open questions for Kilian (data/decisions.json, filled by the project's sessions); hidden when empty
_dec = _jl(f'{HERE}/data/decisions.json')
_dec = _dec.get('decisions', []) if isinstance(_dec, dict) else (_dec or [])
data['decisions'] = [d for d in _dec if isinstance(d, dict) and d.get('status') == 'open'] if not ARCHIVE else []
# 2026-10-19 Scorecard (scoring/scorecard.py): forecast scores, the real book vs holding / SPY / equal weight; the daily
# series and the method text stay in the file
_sc = _jl(f'{HERE}/scoring/scorecard.json')
if isinstance(_sc, dict) and not ARCHIVE:
    _sc = {k: v for k, v in _sc.items() if k != 'method'}
    if isinstance(_sc.get('live_book'), dict): _sc['live_book'] = {k: v for k, v in _sc['live_book'].items() if k != 'daily'}
    data['scorecard'] = _sc
_em = _jl(f'{HERE}/data/earnings_miss.json')  # big-miss add block (earnings_miss.py, Kilian 2026-10-19 card "Add flag")
if isinstance(_em, dict) and not ARCHIVE:
    data['earnings_miss'] = dict(written_utc=(_em.get('_meta') or {}).get('written_utc'),
                                 blocked=[dict(symbol=k, reason=v.get('reason')) for k, v in sorted(_em.items()) if isinstance(v, dict) and v.get('blocked')],
                                 flagged=[dict(symbol=k, reason=v.get('reason')) for k, v in sorted(_em.items()) if isinstance(v, dict) and v.get('flagged')],
                                 mode=(_em.get('_meta') or {}).get('mode') or 'flag')  # Kilian 2026-10-19 15:21Z: flag and investigate, no block
# 2026-10-20 (Kilian: "can you make the tranches clearer on price targets?"): one row per staged entry plan, with the
# research plan it came from (supervisor sizing.entry), what is bought so far (fills in trades.json), when the next
# tranche is due and on what condition; the page adds the model's size for it at today's price
if not ARCHIVE:
    _cfgT = _jl(f'{HERE}/data/review_settings.json') or {}
    _trs = _tr.get('trades', []) if isinstance(_tr, dict) else _tr
    _resT = {r['symbol']: r for r in results}
    _tl = []
    for _s, _e in (_cfgT.get('staged_entries') or {}).items():
        if _s.startswith('_') or not isinstance(_e, dict): continue
        _buys = [dict(date=t['date'], shares=t['shares'], price=t['price']) for t in _trs if t['symbol'] == _s and t['side'] == 'buy']
        _net = sum(t['shares'] * (1 if t['side'] == 'buy' else -1) for t in _trs if t['symbol'] == _s)
        _ra = (_resT.get(_s) or {}).get('research_as_of'); _plan = None
        for _f in ([f'{HERE}/runs/{_ra}/{_s}/supervisor.json'] if _ra else []) + sorted(glob.glob(f'{HERE}/runs/*/{_s}/supervisor.json'), reverse=True):
            _sv = _jl(_f)
            if isinstance(_sv, dict) and (_sv.get('sizing') or {}).get('entry'): _plan = dict(text=_sv['sizing']['entry'], run=_f.split('/runs/')[1].split('/')[0]); break
        _tl.append(dict(symbol=_s, held=_net, buys=_buys, after=_e.get('after'), event=_e.get('event'), condition=_e.get('condition'),
                        new_name=bool(_e.get('new_name')), fixed_shares=_e.get('full_shares') if _e.get('new_name') and not _e.get('model_sized') else None, plan=_plan))
    data['tranches'] = sorted(_tl, key=lambda x: x['after'] or '')
_si = _jl(f'{HERE}/data/short_interest.json')  # universe size for the days-to-cover line (short_interest.py)
if isinstance(_si, dict) and _si.get('n_universe'): data['short_interest_n'] = _si['n_universe']
if ARCHIVE: data['archive'] = True
elif _ARCH_N is not None: data['archive_info'] = dict(n=_ARCH_N, url=(_jl(f'{HERE}/data/pages.json') or {}).get('archive_url'))
_lv = [r for r in results if r.get('close_date')]
if _lv: data['live'] = dict(n=len(_lv), time_utc=max(r['spot_time_utc'] for r in _lv))
data = _scrub(data)
html = open(f'{HERE}/report_template.html').read().replace('__DATA__', json.dumps(data).replace('</', '<\\/'))
if ARCHIVE: html = html.replace('<title>Price Targets</title>', '<title>Price Targets Archive</title>', 1)
OUT = OUT or f"{HERE}/{'archive' if ARCHIVE else 'report'}.html"
html = add_search(html); open(OUT, 'w').write(html)
print(os.path.basename(OUT), len(html.encode()), 'bytes,', len(results), 'tickers' + (f', {_ARCH_N} more on archive.html' if _ARCH_N else ''))
