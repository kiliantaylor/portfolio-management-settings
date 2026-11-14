"""Fill the forecaster and supervisor briefs for each ticker from its option-market prior and price history.

Usage: python3 prep_briefs.py DATE SYM [SYM ...] [--agents 5]
Needs priors/DATE/SYM.json (market_dist.py). Price facts come from valuation/data/daily_SYM.csv or
portfolio/data/daily_SYM.csv (date,open,high,low,close,volume); without one they read "n/a".
Writes runs/DATE/SYM/brief_agent_<i>.md and brief_supervisor.md."""
import csv, json, math, os, sys, datetime as dt
import broker_digests  # Morgan Stanley digests (Kilian 2026-10-03): industry lane and supervisor only
def capex_section(sym):  # Kilian 2026-10-05: one standing, all-customer AI-capex view for AI-capex-driven names (data/ai_capex.json)
    try: c = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', 'ai_capex.json')))
    except FileNotFoundError: return ''
    if sym.upper() not in c.get('names', []) or not os.path.exists(c['file']): return ''
    return ('\n\n## AI capex input (standing view, read it)\n'
            f"For any assumption about AI data-center capex, start from {c['file']} (as of {c['as_of']}; machine-readable {c['json']}). "
            'It covers every customer group (Big-4 hyperscalers, Oracle, neoclouds, AI labs, sovereigns, enterprise), low/base/high '
            'for 2026-2028, and the 2026 revision history (guides only went up this year; year-ahead consensus drifted up every year '
            '2024-26, but the drift is pro-cycle). Do not anchor on a single-source Big-4 consensus growth figure. If you deviate from '
            'its base case, say why and cite newer primary evidence (a company capex guide or filing dated after the file).')
HERE = os.path.dirname(os.path.abspath(__file__))

def playbook(sym):
    """Sector playbook for the name (Kilian 2026-10-19 20:31Z: sector-specific deep modeling, not multiples on consensus).
    data/playbook_map.json: an explicit symbol map first, then the GICS sector in data/sectors.json; returns (name, text)
    or (None, '') when no playbook fits (the general model_inputs rules then apply)."""
    try: pm = json.load(open(os.path.join(HERE, 'data', 'playbook_map.json')))
    except FileNotFoundError: return None, ''
    try: gics = json.load(open(os.path.join(HERE, 'data', 'sectors.json'))).get(sym)
    except FileNotFoundError: gics = None
    nm = pm.get('symbols', {}).get(sym) or pm.get('gics_fallback', {}).get(gics or '')
    f = os.path.join(HERE, 'agents', 'playbooks', f'{nm}.md') if nm else None
    if not f or not os.path.exists(f): return None, ''
    return nm, ('\n\n## Sector playbook: ' + nm + ' (read it; build your model the way it says)\n'
                + body(f).replace('{{VAL_DIR}}', HERE).replace('{{SYMBOL}}', sym))

def prices(s):
    for d in (os.path.join(HERE, 'data'), os.path.join(HERE, '..', 'portfolio', 'data')):
        p = f'{d}/daily_{s}.csv'
        if os.path.exists(p):
            return [(r['date'], float(r['high']), float(r['low']), float(r['close'])) for r in csv.DictReader(open(p))]
    return None

LANES = [  # one per forecaster (Kilian 2026-09-29: dedupe sources, allow non-traditional research)
    ('primary', 'Primary documents: 10-K/10-Q/8-K and proxy filings, earnings-call transcripts, investor-day decks, '
     'company releases, and management commentary at conferences. Read them yourself rather than articles about them; '
     'dig for what the recaps skip (segment notes, footnotes, guidance wording, capex and share-count detail).'),
    ('industry', 'Industry and value chain: competitors\', suppliers\' and customers\' results and commentary, trade press, '
     'industry associations and datasets, pricing and volume trackers, regulatory dockets. What does the rest of the '
     'chain say about this company\'s demand, pricing and share?'),
    ('skeptic', 'The downside case: short-seller reports, critical long-form journalism, downgrades and their reasoning, credit '
     'and debt documents (ratings, spreads, covenants), litigation, insider selling, accounting red flags. Your job is to '
     'find what would make the consensus wrong on the downside, and then weigh it honestly.'),
    ('alternative', 'Non-traditional sources: X/Twitter posts by credible industry or company watchers, Substack and '
     'independent newsletters, podcasts, Reddit and specialist forums, and alternative data (web traffic, app rankings, '
     'job postings, Google Trends, product reviews, shipping or pricing trackers). Look for signals the sell side has not '
     'priced yet, and verify any factual claim with a second source.'),
    ('base-rates', 'Base rates, history and events: how this stock and close peers behaved after similar setups (guidance '
     'cuts, turnarounds, multiple re-ratings, cycles), long-run valuation ranges, and event odds from prediction markets '
     '(Polymarket, Kalshi, Manifold) and economic data for the macro, policy, rate, tariff or legal outcomes that move the '
     'name. Anchor the distribution on what usually happens.'),
]


def fill(t, kv):
    for k, v in kv.items(): t = t.replace('{{' + k + '}}', str(v))
    return t

def body(path):
    t = open(path).read()
    return t.split('\n---\n', 1)[1].strip() + '\n'

if __name__ == '__main__':
    a = sys.argv[1:]; M = 5
    if '--agents' in a: i = a.index('--agents'); M = int(a[i+1]); del a[i:i+2]
    date, syms = a[0], [s.upper() for s in a[1:]]
    for s in syms:
        rd = os.path.join(HERE, 'runs', date, s); os.makedirs(rd, exist_ok=True)
        m = json.load(open(os.path.join(HERE, 'priors', date, f'{s}.json')))
        px = prices(s); facts = dict(LOW_52W='n/a', HIGH_52W='n/a', RET_1Y='n/a', RV_1Y='n/a')
        if px:
            last = px[-253:]
            facts = dict(LOW_52W=f'{min(x[2] for x in last):.2f}', HIGH_52W=f'{max(x[1] for x in last):.2f}',
                         RET_1Y=f'{(last[-1][3]/last[0][3]-1)*100:+.1f}% (price only)',
                         RV_1Y=f"{m.get('realized_vol_1y') and m['realized_vol_1y']*100:.0f}%")
            if len(px) < 240:  # recent listing: say so instead of implying a full year of history
                facts['_SHORT'] = (f'- Listed on {px[0][0]}, so there are only {len(px)} trading days of history. Range since listing: '
                                   f"${facts['LOW_52W']} to ${facts['HIGH_52W']}; return since first close: {facts['RET_1Y']}; "
                                   f"realized volatility since listing: {facts['RV_1Y']} (often inflated right after an IPO)")
        ladder = ', '.join(f"{l['K']:.2f}" if l['K'] < 20 else f"{l['K']:.0f}" for l in m['ladder'])
        p1 = os.path.join(HERE, 'priors', m['market_date'], '1m', f'{s}.json')  # 1-month horizon (Kilian 2026-09-29)
        m1 = json.load(open(p1)) if os.path.exists(p1) else None
        if m1: ladder_1m = ', '.join(f"{l['K']:.2f}" for l in m1['ladder']); h1m = m1['horizon']
        else:  # no 1-month chain read: same multiples of spot, 30 days out
            ladder_1m = ', '.join(f"{m['spot']*x:.2f}" for x in (0.8, 0.85, 0.9, 0.93, 0.96, 1.0, 1.04, 1.07, 1.1, 1.15, 1.2))
            h1m = (dt.date.fromisoformat(m['market_date']) + dt.timedelta(days=30)).isoformat()
        try:  # SEC XBRL fact pack (token cut, Kilian 2026-10-13): agents cite these instead of fetching them five times
            import secfacts; fp = secfacts.build(s)
            json.dump(fp, open(f'{rd}/facts.json', 'w'), indent=1); open(f'{rd}/facts.md', 'w').write(secfacts.to_md(fp))
            fact_pack = (f"`{rd}/facts.md` lists {len(fp['facts'])} figures from the company's SEC XBRL filings (revenue, operating "
                         'income, net income, diluted EPS and shares, D&A, SBC, operating cash flow, capex, buybacks, dividends, cash, '
                         'debt, shares outstanding; last fiscal years, latest quarter and TTM), each with its filing link, plus the '
                         'recent filings list. Use these figures instead of fetching them again: put the ids in the input\'s '
                         '`fact_ids` (e.g. ["F20"]) and the filing URL in `sources`; the citation gate checks them by script. Tags can '
                         'miss items (leases, preferred stock, segment detail, company-specific debt tags): read the filing for those. '
                         f'For peers, `python3 {HERE}/secfacts.py peers SYM SYM ...` prints filing-based trailing EV/EBITDA, EV/sales, '
                         'P/E and FCF yield with filing ids (cite e.g. VZ.ev_ebitda_ttm in a comp\'s `fact_ids`); forward multiples '
                         'still need NTM consensus from a consensus page. A symbol it does not know can be given as SYM:CIK.')
        except Exception as e:
            fact_pack = f'No SEC fact pack for this name ({str(e)[:120]}); read the filings directly.'
        if os.path.exists(f'{rd}/peers.md'):  # peers.py table (Kilian 2026-10-14: stop peer multiples failing the gate)
            fact_pack += (f' PEER TABLE: `{rd}/peers.md` is a gate-ready comps table built before you started (trailing from '
                          'filings, NTM from a saved consensus snapshot). Draw your 10 to 12 comparables from it where they fit, '
                          'copy its figures and put their ids in each comp\'s `fact_ids` (e.g. ["FAST.pe_ntm", "FAST.ev_ebitda_ntm", '
                          '"FAST.eps_growth_fwd"]) and set the comp\'s `basis` to "estimate"; the gate then checks them by script. '
                          'Any peer or figure the table lacks you source yourself, labelled "estimate" with the arithmetic when you '
                          'derived it; never cite a page for a figure it does not show.')
        _mf = json.load(open(f'{rd}/method_fit.json')) if os.path.exists(f'{rd}/method_fit.json') else None  # method_fit.py (Kilian 2026-10-14 20:37Z)
        _mf_md = open(f'{rd}/method_fit.md').read() if os.path.exists(f'{rd}/method_fit.md') else ''
        if _mf and _mf.get('verdict') == 'watch':
            fact_pack += (' METHOD FIT: one test says standard methods may not carry this name (' + '; '.join(_mf['flags']) + '). '
                          'Use the standard methods, but say whether that flag weakens any of them and add a non-multiple method if it does.')
        pb_name, pb_text = playbook(s)  # 2026-10-19: marks the round as a deep-modeling round for combine.py's research leg
        json.dump(dict(symbol=s, playbook=pb_name, deep_model=True, written=dt.datetime.now(dt.timezone.utc).strftime('%Y-%m-%dT%H:%MZ'),
                       note='briefs written under the deep-modeling rules (Kilian 2026-10-19 20:31Z); combine.py uses the '
                            'reconciled standard model as the research leg once the run is gate-clean'),
                  open(f'{rd}/playbook.json', 'w'), indent=1)
        kv = dict(SYMBOL=s, FACT_PACK=fact_pack, NAME_IF_KNOWN='look up the company name', HORIZON=m['horizon'], AS_OF=m['market_date'],
                  SPOT=m['spot'], TODAY=dt.date.today().isoformat(), LADDER=ladder, LADDER_1M=ladder_1m, HORIZON_1M=h1m,
                  M=M, RUN_DIR=rd, **facts)
        short = kv.pop('_SHORT', None)
        for i in range(1, M+1):
            ln, lt = LANES[(i - 1) % len(LANES)]
            b = fill(body(f'{HERE}/agents/forecaster.md'), dict(kv, AGENT_ID=f'agent_{i}', OUT_PATH=f'{rd}/agent_{i}.json',
                                                                   LANE_NAME=ln, LANE=f'**{ln}**: {lt}'))
            if short: b = '\n'.join(short if l.startswith('- 52-week range:') else l for l in b.split('\n'))
            if ln == 'industry': b += broker_digests.section(s, m['market_date'], 'industry')  # '' when no digest
            b += capex_section(s)
            b += '\n' + body(f'{HERE}/agents/model_inputs.md').replace('{{VAL_DIR}}', HERE)  # standard model set (Option 1, 2026-10-13)
            b += pb_text  # sector playbook (2026-10-19)
            b += _mf_md  # non-standard name: non-multiple methods (method_fit.py)
            open(f'{rd}/brief_agent_{i}.md', 'w').write(b)
        sup = fill(body(f'{HERE}/agents/supervisor.md'), kv)
        import book; H = book.load()
        if s not in {p['symbol'] for p in H['positions']}:  # candidate buy: ask the supervisor for a sizing view
            pv = H['totals']['account_total']
            sup += '\n' + fill(body(f'{HERE}/agents/sizing.md'), dict(CASH=f"{H.get('cash_to_deploy_external', 0):,.0f}",
                PORT_VALUE=f"{pv + H.get('cash_to_deploy_external', 0):,.0f}",
                HOLDINGS=', '.join(f"{p['symbol']} ${p['market_value']:,.0f}" for p in H['positions']) + f", cash ${H['cash_in_account'] + H.get('cash_to_deploy_external', 0):,.0f}"))
        sup += broker_digests.section(s, m['market_date'], 'supervisor')  # '' when no digest
        sup += capex_section(s)
        sup += '\n' + body(f'{HERE}/agents/model_supervisor.md').replace('{{VAL_DIR}}', HERE).replace('{{RUN_DIR}}', rd).replace('{{AS_OF}}', m['market_date']).replace('{{SYMBOL}}', s)
        sup += pb_text  # sector playbook (2026-10-19): the supervisor reviews each set against it
        sup += _mf_md
        open(f'{rd}/brief_supervisor.md', 'w').write(sup)
        print(s, 'briefs written:', M, '+ supervisor', '| playbook', pb_name or 'none (general rules)', '| ladder', ladder)
