"""One EPS path (Kilian 2026-11-17: "why would they disagree at all this is the same as the model disagreeing with what you put on
the sheet there should be no disagreement whatsoever").

Every quarter EPS we score, log or act on (routines/preprint_log.our_quarter, so the pre-print log and expected_miss.py, and
scoring/consensus_calls.py Q+1, Q+2, FY1, FY2) is

    ours = the engine's own quarter EPS (model_engine.value quarters[i]['eps'], the figure the model Sheet shows)
         + the Zacks bridge for that quarter (bridge() below, the ONLY place the basis changes to Zacks'; logged to data/eps_bridge.json)

and nothing else. There is no second build. Where the engine's EPS is wrong (a tax rate, interest, shares), it is fixed in the model,
so the Sheet changes too (rule 16; a change that moves a live target needs Kilian's OK). The filings build in preprint_log
(quarter_build) is kept only as a cross-check on the model's inputs (ours_detail.filings_check), never as our number.

Zacks bridge = an explicit conversion, per name, from the model's EPS basis to the EPS measure Zacks reports (Kilian 2026-11-19 on the
Self-scoring doc: "this is not how you should do it; i suspect zacks is reporting adj. eps for some companies, you need to go find out
what non-gaap measure they are using"). The median of past gaps is gone. data/zacks_basis.json holds, per name:
  zacks        the measure Zacks reports, found by matching Zacks' reported EPS to GAAP diluted EPS (SEC XBRL) and to the company's own
               non-GAAP EPS (earnings release) quarter by quarter, with the sources (e.g. MRVL: company non-GAAP less stock comp after tax)
  items        the named conversion items, each computed from the engine's OWN forecast quarter (the model Sheet's lines):
               sbc  -stock comp x (1 - tax) / shares   (tax = the quarter's own rate, or a number with its filed source)
               da   +D&A x (1 - tax) / shares          (AMBQ: Ambiq's non-GAAP, and so Zacks, adds back D&A)
               per_share basic_if_loss: a loss quarter on basic shares (diluted / basic from the set's own share fields)
               no items: Zacks' measure is the model's basis (one-offs, marks and other items the model does not forecast are left out
               by both), the bridge is 0 and ours = the engine's EPS
  quarters     the filed evidence per reported quarter (Zacks, GAAP, company non-GAAP, the items, the check arithmetic)
Reported quarters are checked, never averaged: Zacks - (the engine formula at the quarter's own filed tax + the items, filed SBC/D&A where
the basis file has them) is a MODEL gap (an item the engine leaves out: interest income on net cash, other income, share timing).
It is shown per quarter and flagged (model_gap when every checked quarter sits on the same side by more than 3% of Zacks), never added
(rules 4/5: no past quarter's gap carried forward; the model is fixed instead). Flags: basis_open (the basis file names an unexplained
part, e.g. AVGO), perimeter (MOD: reported quarters are whole-company), marks (GOOGL/AMZN/VST: score against Zacks ex marks).
A name missing from data/zacks_basis.json gets no number (gap None): its Zacks measure has to be found first.
Research only: nothing here changes valuation."""
import copy, datetime as dt, json, os, statistics, sys
HERE = os.path.dirname(os.path.abspath(__file__)).replace('/mnt/attach/project-files', '/mnt/project-files')
sys.path.insert(0, HERE); sys.path.insert(0, os.path.join(os.path.dirname(HERE), 'routines'))
LOG = os.environ.get('EPS_BRIDGE_LOG', os.path.join(HERE, 'data', 'eps_bridge.json'))  # one entry per name (latest), overwritten: no growth
BASIS_FILE = os.path.join(HERE, 'data', 'zacks_basis.json')
CHECK_N, GAP_TOL, OFF = 4, 0.03, 0.5   # reported quarters checked; model_gap when all sit > GAP_TOL x |Zacks| on one side; off-basis report
PERIMETER = {'MOD': 'Performance Technologies spin closed 2026-10-01; reported quarters and FY1 consensus are whole-company, the model is Climate only'}
# Replaced 2026-11-19 by data/zacks_basis.json: MARKS_ONLY (GOOGL AMZN VST) and NOISE_ZERO (NFLX META T INTC) were zero bridges set by
# hand; their Zacks measures are now in the basis file with the filed evidence (no conversion items), so the same 0 comes from there.


def basis(sym):
    try: return (json.load(open(BASIS_FILE)) or {}).get(sym)
    except Exception: return None


class PathError(Exception): pass


def _d(s): return dt.date.fromisoformat(str(s)[:10])
def _near(a, b, tol=20): return abs((_d(a) - _d(b)).days) <= tol


def _fq_end(fq):
    """'Dec 2026' -> the month's last day (iso)."""
    import calendar
    m = dt.datetime.strptime(fq.strip(), '%b %Y'); return dt.date(m.year, m.month, calendar.monthrange(m.year, m.month)[1]).isoformat()


class Path:
    """The engine's quarters for one model set plus the Zacks bridge. Build with path(sym, R, mi, hist)."""
    def __init__(self, sym, R, mi, val, hist):
        import model_engine as me, preprint_log as pl
        self.sym, self.R, self.mi, self.val = sym, R, mi, val
        self.Q = val.get('quarters') or []
        bal = mi.get('balance') or {}; sh = mi.get('shares') or {}
        self.rate, self.nd = bal.get('interest_rate', 0.05), bal.get('net_debt') or 0.0
        self.sh0, self.chg = sh.get('diluted_now'), sh.get('annual_change', 0)
        self.t0 = sh.get('as_of', bal.get('as_of', R))
        if not self.sh0: raise PathError('model has no diluted share count')
        self._yf = me.yfrac
        self.adjusted = pl.is_adjusted(sym, mi)
        # forecast quarters: our EPS is the engine's own quarters[i]['eps'] (exactly the Sheet's), never recomputed here; the
        # formula below is used only on reported quarters to measure the bridge (the engine's shares can differ in forecast
        # quarters, e.g. buyback_reprice.py re-pricing APP's retired shares, so they are read off the engine's EPS where needed)
        self._sh_fwd = {}
        for q in self.Q:
            if q.get('actual'): continue
            num = (q['ebit'] - self.rate * max(self.nd, 0) / 4) * (1 - q['tax'])
            if abs(q.get('eps') or 0) > 1e-9 and abs(num) > 1e-9: self._sh_fwd[q['end']] = num / q['eps']
        self.rep = {}
        for h in hist or []:
            if h.get('fiscal_quarter') and h.get('eps') is not None:
                try: self.rep[_fq_end(h['fiscal_quarter'])] = h
                except Exception: pass
        aq = [q for q in self.Q if q.get('actual')]
        self.aq, self.restated = pl.sbc_restate(sym, mi, aq)
        self.pairs = sorted(((self.rep[e], q) for q in self.aq for e in self.rep if _near(e, q['end'])), key=lambda p: p[1]['end'], reverse=True)[:CHECK_N]
        self.B = basis(sym)
        self.perimeter = PERIMETER.get(sym)
        self._basic_ratio = None
        if self.B and self.B.get('per_share') == 'basic_if_loss':
            bs = (mi.get('shares') or {}).get(self.B.get('basic_key') or '')
            if bs: self._basic_ratio = self.sh0 / bs
        self._cache = {}

    def shares(self, end):
        """Diluted shares at a quarter end as the engine has them: forecast quarters read off the engine's EPS, else diluted now x
        (1 + annual change)^years after the share date (reported quarters: diluted now)."""
        if end in self._sh_fwd: return self._sh_fwd[end]
        return self.sh0 * (1 + self.chg) ** max(self._yf(self.t0, end), 0)

    def formula(self, q, tax=None):
        """The engine's quarter EPS (model_engine.value, quarters 'eps'): (EBIT - interest on positive net debt) x (1 - tax) / shares."""
        t = q['tax'] if tax is None else tax
        return (q['ebit'] - self.rate * max(self.nd, 0) / 4) * (1 - t) / self.shares(q['end'])

    def _use(self, h, m):
        off = (h.get('consensus') or 0) > 0 and abs(h['eps'] / h['consensus'] - 1) > OFF
        return h['consensus'] if off and abs(m - h['consensus']) < abs(m - h['eps']) else h['eps']

    def force_zero(self, reason):
        """A perimeter change (e.g. MOD's spin): reported quarters are not on the model's perimeter; they are not checked."""
        self.perimeter = reason; self._cache = {}

    def conversion(self, q, eps, filed=None):
        """(gap per share, [item rows]) converting the model-basis EPS `eps` of quarter q to Zacks' measure with the basis file's named
        items. `filed` = the basis file's row for a reported quarter (its filed SBC / D&A replace the engine's where given)."""
        rows, adj = [], 0.0
        sh = self.shares(q['end'])
        for it in (self.B or {}).get('items') or []:
            k = it['item']; t = q['tax'] if it.get('tax', 'quarter') == 'quarter' else float(it['tax'])
            raw = (filed or {}).get(k); src_ = 'filed' if raw is not None else 'model'
            if raw is None: raw = q.get(k) or 0.0
            sign = -1 if k == 'sbc' else 1
            v = sign * raw * (1 - t) / sh
            rows.append(dict(item=k, amount=round(raw, 2), tax=t, per_share=round(v, 4), source=src_,
                             how=f"{'-' if sign < 0 else '+'}{k} {raw:,.1f} x (1 - {t:.1%}) / {sh:,.1f} = {v:+.3f}")); adj += v
        if self._basic_ratio and eps + adj < 0:
            z = (eps + adj) * self._basic_ratio; v = z - (eps + adj)
            rows.append(dict(item='basic_shares', per_share=round(v, 4), how=f"loss on basic shares: x {self._basic_ratio:.3f} (diluted / basic) = {v:+.3f}")); adj += v
        return adj, rows

    def checks(self):
        """Per reported quarter: Zacks vs the engine formula (at the quarter's own tax) + the named items. The residual is a model gap,
        shown, never added."""
        out = []
        filed = {r['end']: r for r in (self.B or {}).get('quarters') or []}
        for h, q in self.pairs:
            m = self.formula(q); fr = next((filed[e] for e in filed if _near(e, q['end'])), None)
            adj, items = self.conversion(q, m, fr)
            z = self._use(h, m + adj)
            out.append(dict(end=q['end'], zacks=z, zacks_reported=h['eps'], engine_formula=round(m, 4), conversion=round(adj, 4),
                            residual=round(z - m - adj, 4), gap=round(z - m - adj, 4), ebit=round(q['ebit'], 2), tax=q['tax'], items=items,
                            **({k: fr[k] for k in ('gaap', 'company', 'check') if fr and k in fr}),
                            **({'ebit_as_reported': q['ebit_as_reported']} if 'ebit_as_reported' in q else {})))
        return out

    def bridge(self, q):
        """dict(gap, items, quarters, flags, how, zacks_measure) for forecast quarter q (a quarter dict; a bare tax rate picks the first
        forecast quarter at that rate, for old callers). gap None when the name's Zacks measure is not in the basis file."""
        if not isinstance(q, dict):
            q = next((x for x in self.Q if not x.get('actual') and abs(x['tax'] - q) < 1e-9), None) or next((x for x in self.Q if not x.get('actual')), None)
            if q is None: return dict(gap=None, items=[], quarters=[], flags=['no_forecast_quarter'], how='no forecast quarter')
        key = q['end']
        if key in self._cache: return self._cache[key]
        if not self.B:
            b = dict(gap=None, items=[], quarters=[], flags=['basis_unchecked'], zacks_measure=None,
                     how=f'Zacks EPS measure for {self.sym} not checked yet (data/zacks_basis.json): no Zacks-basis number')
            self._cache[key] = b; return b
        gap, items = self.conversion(q, q['eps'])
        rows = [] if self.perimeter else self.checks()
        flags = []
        if self.perimeter: flags.append('perimeter')
        if self.B.get('marks'): flags.append('marks')
        if self.B.get('open'): flags.append('basis_open')
        rs = [r for r in rows if r['zacks'] is not None]
        if len(rs) >= 2 and all(abs(r['residual']) > max(GAP_TOL * abs(r['zacks']), 0.01) for r in rs) and len({r['residual'] > 0 for r in rs}) == 1:
            flags.append('model_gap')
        how = (f"Zacks measure: {self.B['zacks']} (model: {self.B.get('model')}); "
               + ('conversion ' + ', '.join(i['how'] for i in items) + f' = {gap:+.3f}' if items else 'no conversion items: Zacks measure = model basis, bridge 0')
               + (f"; reported check (Zacks - formula - items): " + ', '.join(f"{r['end'][:7]} {r['residual']:+.3f}" for r in rows) if rows else '')
               + (f"; {self.perimeter}" if self.perimeter else '') + (f"; open: {self.B['open']}" if self.B.get('open') else '')
               + (f" [{', '.join(flags)}]" if flags else ''))
        b = dict(gap=round(gap, 4), items=items, quarters=rows, flags=flags, how=how, zacks_measure=self.B['zacks'])
        self._cache[key] = b
        return b

    def quarter(self, end):
        """Our EPS for the model quarter ending near `end`: dict(eps, engine_eps, bridge, tax, label, how, flags), or None."""
        q = next((q for q in self.Q if not q.get('actual') and _near(q['end'], end)), None)
        if q is None: return None
        b = self.bridge(q)
        if b['gap'] is None: return dict(eps=None, engine_eps=q['eps'], bridge=b, tax=q['tax'], label=q['label'], end=q['end'], how=b['how'], flags=b['flags'])
        eps = round(q['eps'] + b['gap'], 4)
        return dict(eps=eps, engine_eps=q['eps'], ebit=q['ebit'], bridge=b, tax=q['tax'], label=q['label'], end=q['end'], flags=b['flags'],
                    how=f"engine {q['label']} EPS {q['eps']:.3f} (the model Sheet's; EBIT {q['ebit']:,.0f}, tax {q['tax']:.1%}) + {b['how']} -> {eps:.3f}")

    def log(self):
        """Writes this name's bridge (at each forecast quarter's tax) to data/eps_bridge.json, one entry per name, replaced each run."""
        try:
            try: L = json.load(open(LOG)) if os.path.exists(LOG) else {}
            except ValueError:  # 2026-11-17: two writers sharing one .tmp left trailing bytes; keep the first whole document
                L, _ = json.JSONDecoder().raw_decode(open(LOG).read())
            ent = dict(model_date=self.R, logged_utc=dt.datetime.now(dt.timezone.utc).strftime('%Y-%m-%dT%H:%MZ'), restated=self.restated, zacks_measure=(self.B or {}).get('zacks'),
                       quarters={q['end']: dict(label=q['label'], engine_eps=q['eps'], tax=q['tax'], **{k: v for k, v in self.bridge(q).items() if k not in ('how', 'zacks_measure')})
                                 for q in self.Q if not q.get('actual')})
            L[self.sym] = ent
            import threading; tmp = f'{LOG}.{os.getpid()}.{threading.get_ident()}.tmp'  # own tmp per writer (shared one corrupted the log 11-17)
            with open(tmp, 'w') as f: json.dump(L, f, indent=1, default=str)
            os.replace(tmp, LOG)
        except Exception as e: print(f'eps_path: bridge log not written for {self.sym}: {str(e)[:100]}', file=sys.stderr)


def path(sym, hist, ms=None, log=True):
    """Path for sym's newest reconciled driver-model set (preprint_log.model_set), or raises PathError."""
    import preprint_log as pl, model_engine as me
    ms = ms or pl.model_set(sym)
    if not ms: raise PathError('no reconciled driver-model set')
    R, src, mi = ms
    try: val = me.value(dict(copy.deepcopy(mi), _allow_long=True), R)
    except Exception as e: raise PathError(f'model {R} did not value: {str(e)[:80]}')
    if not val.get('quarters'): raise PathError(f'model {R} has no quarters (annual set): no engine quarter EPS')
    p = Path(sym, R, mi, val, hist); p.src = src
    if log: p.log()
    return p
