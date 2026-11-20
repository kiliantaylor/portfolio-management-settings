"""Hard stop on two forecast failures (Kilian 2026-11-09, VST FY2027 = guide midpoint 8,100 x 1.019 with gen_price back-solved,
and retail_twh / ercot_dc_gw set from trend fits, all passed by the gate: "how is this still happening ... how do I hard stop
this from happening?"). Memory and brief text alone did not stop it, so the script checks every reconciled set:

  guide-anchor   a projected guidance_check year whose our_value / drivers_imply sits within TOL (0.3%) of the guide midpoint,
                 the record-adjusted guide, or midpoint x (1 + a past period's beat, the median or the mean beat): the year was
                 set to the guide (or guide x usual beat), not built bottom-up. A closed period (actual in the track record)
                 is skipped.
  back-solve     derivation / guidance why wording that sets a driver to reach a total: "back-solved", "set so the build",
                 "to hit", "absorbs the gap", "calibrated to the guide", "solved for", "plug", "midpoint x 1.0x" ...
                 (actual quarters reconciled to filed totals are fine and are skipped).
  trend-only     a sentence that uses a past trend or fit as the path ("extend the observed trend", "trend is extended",
                 "least-squares slope", "trend fit", "/yr fit", "extrapolate"), cited or not: the source shows the past, not the
                 forecast year. A past-years "CAGR" fails only with no cited source, target, guide or consensus beside it.
                 Filling a past quarter between observations ("interpolated", "back-extrapolated") is skipped.
  carry-forward  (Kilian 2026-11-17, AMZN Q4 FY2026 capex = Q3 $77.3B x 1.013, "the lowest Q4/Q3 ratio of 2022-2025": "you
                 must project economic reality, not just blindly project forward past data ... if you need code to force you
                 to stop doing this write it"). Wording that sets a forecast period from a past period: last quarter x a
                 historical ratio ("Q3 x 1.013", "the lowest Q4/Q3 ratio", "average seasonal uplift"), a level held at or
                 rolled from a past period ("held at Q3's 58.7%", "held at the FY2028 level", "at the Q3 level", "rolled
                 forward"), a past step or growth rate reused ("the live +$0.9B q/q step", "repeat last year's growth").
                 Numbers too (carry-num): a forecast quarter of a driver or driver_build constant whose q/q ratio equals a
                 past same-quarter ratio in the set (or their min / max / mean / median), whose y/y ratio equals the last
                 actual y/y or the mean past y/y, or whose whole forecast is the last actual held flat.
                 Splitting a year built from its own drivers into quarters by a multi-year shape (rule 9) is not a carry.
  carry-step / carry-held / carry-base  (Kilian 2026-11-18, AMZN Q4 FY2026 AWS revenue $49,663M = the 10-29 "Q3 x 1.08" view,
                 passed by gate rounds 1-2 while aws_ppe moved and aws_turn was re-solved to hold the product): carry_trace.py.
                 The quarter lines the engine builds (segment revenue / EBIT, totals, D&A, capex) are traced too: a first
                 forecast quarter = last actual x a round factor (carry-step); a built line held exactly at its base-round or
                 earlier-gate-round value while its inputs moved (carry-held); a value unchanged from those rounds whose quoting
                 text now gives a different reason (carry-base). Earlier gate rounds = gate/values_rN.json (name_update.py gate).
  guide-residual wording that takes a period as the guide less what is already reported ("$220B guide - 9M", "plan less H1"):
                 a guide use, as guide-anchor.
Negated or superseded wording ("not back-solved", "never extend the trend", "was $78.3B on a Q4/Q3 ratio", "why not Q3's run
rate") is skipped, and so is a derivation's history after "BEFORE:" / "PREVIOUS:" (it describes a replaced path).

GUIDE USE, LAST RESORT (Kilian 2026-11-17: "you are allowed to use guidance or guidance * usual beat if you don't have anything
better but it must be flagged to me with a satisfactory explanation"). A guide-anchor or guide-residual hit passes only when
the guidance_check row (guide-anchor) or the driver / constant (guide-residual) carries a complete declaration:
  "guide_use": {"kind": "guide" | "guide_x_beat",          # must match what the value sits on
                "why_nothing_better": ">= 150 chars: why no bottom-up build from real drivers is possible for this period",
                "tried": [{"build": "the evidence-based build tried (backlog, capacity, customers, peers, outside forecasts)",
                           "why_not": ">= 40 chars: why it cannot set the level"}],      # at least one
                "source": "src N | https://... (the guide itself)"}
An incomplete declaration is a failure (guide-use-unexplained). A complete one is not a failure but a FLAG: every use is
listed by `forecast_lint.py guide-uses D SYM`, by judgment_check.py cards / statement (posted beside the decision cards and in
the update reply), in name_update.py finish flags, and in the pitch memo (agents/memo.md).

A failure can only be passed with a logged reason, one key at a time:
  python3 forecast_lint.py check SET.json            print failures (exit 1 if any are not overridden)
  python3 forecast_lint.py round D SYM               the same for runs/D/SYM/supervisor.json, with that round's overrides
  python3 forecast_lint.py override D SYM KEY --why "<reason>"
                                                     writes runs/D/SYM/lint_overrides.json and logs to data/rule_overrides.jsonl
  python3 forecast_lint.py guide-uses D SYM | runs/D/SYM   Markdown list of the round's declared guide uses (what Kilian sees)
  python3 forecast_lint.py all [D]                   every name's current research set (newest result.json research_as_of
                                                     on or before D) -> data/forecast_lint.json and a summary
Wired into: name_update.py gate (refuses to start the gate) and finish (refuses), run_digest.py gate-merge (each failure is a
gate failure, so no clean citation_audit.json), rules_check.py (no due buy / add / swap-in on a failing set)."""
import glob, json, os, re, sys
HERE = os.path.dirname(os.path.abspath(__file__)).replace('/mnt/attach/project-files', '/mnt/project-files')
RUNS = os.environ.get('VAL_RUN_ROOT', os.path.join(HERE, 'runs'))
sys.path.insert(0, HERE)
TOL = 0.003

NEG = re.compile(r"\b(not|never|no|nor|without|instead of|rather than|isn't|wasn't|don't|avoid\w*|against|would|need|needs)\b[^.;]{0,40}$", re.I)
BACK = [
    r'back[- ]?solv\w*', r'reverse[- ]engineer\w*', r'\bsolv(?:e|ed|es|ing) (?:for|to|so)\b',
    r'\bset so (?:that )?(?:the )?(?:build|total|year|sum|ebitda|revenue|eps|segment|fy)', r'\bso (?:that )?the (?:build|total) (?:hits|reaches|matches|lands)',
    r'\bto hit\b', r'\babsorbs? (?:the )?(?:gap|residual|difference|rest|balance|remainder)', r'\bcalibrated to (?:the )?(?:guide|guidance|midpoint|range)',
    r'\bto (?:reach|match|land on|tie to|tie out to|equal) (?:the )?(?:guide|guidance|midpoint|mid\b|record[- ]adjusted)',
    r'\bplug(?:ged)?\b', r'\b(?:guide |guided |narrowed |the )?mid(?:point)?\s*(?:[\d,.]+\s*)?(?:x|×|\*|times)\s*(?:\(1\s*\+|1\.\d)',
    r'\bimplied by (?:the )?(?:guide|guidance|midpoint)\b',
]
TREND = [  # strong: fail even when the sentence cites the history it was fitted to (the source must be for the forecast, not the past)
    r'\bextend(?:s|ed|ing)? (?:the |its |a )?(?:observed |past |historical |recent |reported |pre-\w+ |\d+[- ]\w+ )*(?:trend|slope|pace|run[- ]rate|decline|growth rate)',
    r'\btrend (?:is |was |gets )?extended', r'\bobserved (?:[\w-]+ ){0,3}trend', r'\btrend[- ](?:fit|line|extrapolation)',
    r'\bfitted (?:trend|slope|line|path|growth|rate|decline)', r'\b(?:linear|log|exponential|ols) (?:fit|regression|trend)', r'\bleast[- ]squares\b',
    r'/\s*(?:yr|year) fit\b', r'\bfit (?:to|of|on) (?:the )?(?:\d+|past|history|reported|actuals|observations)',
    r'(?<!back-)\bextrapolat(?!\w*\s+(?:back|method))\w*', r'\bcontinu(?:e|es|ing) (?:the |its )?(?:observed |past |historical |recent )?(?:trend|pace|decline|slope)',
    r'\bregression (?:line|fit|slope)', r'\bslope of (?:the )?(?:\w+ ){0,4}(?:observations|actuals|history|points)',
]
_PAST = r"(?:Q[1-4](?:\s*(?:FY)?\s*'?\d{2,4})?|last quarter|prior quarter|previous quarter|latest quarter|the prior year|last year|prior[- ]year|TTM|LTM)"
CARRY = [  # Kilian 2026-11-17 (AMZN Q4 capex = Q3 x the lowest past Q4/Q3 ratio): a forecast period set from a past period
    r'\bQ[1-4]\s*/\s*Q[1-4]\s+(?:ratios?|steps?|uplifts?|multiples?|factors?|of (?:FY)?\d{4}|in (?:FY)?\d{4})',  # a seasonal quarter-to-quarter ratio ("Q2/Q3 carry" = both quarters, skipped)
    r'\b(?:lowest|highest|smallest|largest|median|average|mean|typical|usual|normal|historical|past|prior|trailing|\d{4}\s*[-–]\s*\d{4})\s+(?:[\w/-]+\s+){0,3}?(?:seasonal\w*\s+)?(?:ratio|uplift|step[- ]?up|multiplier|factor|q/q|sequential (?:step|change|growth|increase)|y/y|yoy)\b',
    r'\bseasonal (?:ratio|uplift|factor|multiplier|step)',
    r"\b(?:the\s+)?(?:FY\s?20\d\d(?:\s*[-–]\s*\d\d)?|20\d\d|Q[1-4]|last|prior|previous|latest|trailing|TTM|LTM)(?:'s)?(?:\s+\w+)?\s+(?:rate|growth|margin|ratio|step|pace|level|mix|share)s?\s+(?:is\s+|was\s+)?(?:held|kept|carried|repeated|continued)\b",  # "the FY2028 rate held"
    r"\b(?:held|kept|keeps?|hold|holds|holding|flat|unchanged|carried(?: forward)?|rolled(?: forward)?|repeated)\s+(?:at|from|on|with)\s+(?:the\s+)?(?:Q[1-4]\b|FY\s?20\d\d|FY\d\d\b|20\d\d\b|last\b|latest\b|prior\b|previous\b|current\b|trailing\b|TTM\b|LTM\b|reported\b|actual\b|recent\b|year[- ]end\b|base (?:year|quarter)\b|its (?:last|latest|current|reported))",
    r"\bat (?:the )?(?:Q[1-4]|TTM|LTM|last|latest|prior|previous|trailing|current|last actual)(?:'s)? (?:[\w-]+ ){0,2}?(?:level|run[- ]rate|rate|pace|value)\b",
    r"\bat Q[1-4]'s\b",
    r"\b(?:trailing|current|latest|last|TTM|LTM|Q[1-4]'s) run[- ]rate\b",
    r'\broll(?:s|ed|ing)? (?:it |them |the \w+ )?forward\b', r'\bcarr(?:y|ies|ied) (?:it |them |the \w+ )?forward\b',
    r'\b(?:live|old|prior|previous|same|last|recent)\b[^.;]{0,25}\b(?:q/q|y/y|sequential|quarterly|yearly|annual)\s+(?:step|increase|rise|growth|change|add)',
    r"\b(?:repeat\w*|carr\w*|appl\w*|keep\w*|kept|reus\w*|uses?|used)\s+(?:the\s+)?(?:last|prior|previous|latest|trailing|FY\s?20\d\d|20\d\d|Q[1-4])(?:'s| year's| quarter's)?\s+(?:[\w-]+\s+){0,2}(?:growth|margin|ratio|rate|share|mix|step|seasonal\w*|pattern|shape)\b",
    r'\bsame (?:as|growth as|rate as|ratio as) (?:the )?(?:last|prior|previous) (?:year|quarter)',
]
CARRY_RE = [re.compile(p, re.I) for p in CARRY]
# a past period x a multiplier ("Q3 $77.3B x 1.013") is a carry when the sentence shows the multiplier is history (a past ratio,
# pace, q/q or y/y); a past quarter x a growth rate built from its own drivers is not
CARRY_MULT = re.compile(_PAST + r"(?:'s)?\s*(?:actual\s*)?(?:\(?\$?[\d,.]+\s*[BMK]?\)?\s*)?(?:x|×|\*|times)\s*\(?(?:1\s*\+\s*)?\d*\.\d+", re.I)
MULT_HIST = re.compile(r"\b(pace|ratio|run[- ]rate|q/q|y/y|yoy|seasonal\w*|past|historical|observed|average|mean|median|lowest|highest|typical|usual|same as|last year's|trailing)\b", re.I)
SKIP_ALT = re.compile(r'^\s*(?:variants?|alternatives?|options?)\b|\b(?:judgment_alternatives|evidence-based alternatives?|cross-check only|alternatives? (?:sized|considered))\b', re.I)
ROLL_LABEL = re.compile(r'\(?\broll forward \d{4}-\d\d-\d\d[^)]*\)?:?', re.I)
GUIDE_XCHECK = re.compile(r'\b(?:sits|inside|within|above|below|versus|vs\.?|compared?|cross-check\w*|top of|bottom of|low end|high end)\b', re.I)
CARRY_NEG = re.compile(r"\b(was|were|replac\w*|dropped|superseded|retired|no longer|why not|old|before|previously|live)\b(?:[^.;]|(?<=\d)\.(?=\d)){0,60}$", re.I)


def _quoted(pre):
    """True when the match sits inside a quotation (an odd count of quote marks before it): a quote is evidence, not our method."""
    return (pre.count("'") - len(re.findall(r"\w'\w|s'\s", pre))) % 2 == 1 or pre.count('"') % 2 == 1 or pre.count('\u2018') > pre.count('\u2019')
GUIDE_RESID = re.compile(r"\b(?:guide|guidance|outlook|guided)\b[^.;]{0,30}?(?:\s[-−–]\s|\sminus\s|\sless\s)\s*(?:the\s+)?(?:\$?[\d,.]+\s*[BMK]?\s*)?\(?(?:9M|H1|1H|YTD|year[- ]to[- ]date|first (?:three|two|nine|3|2|9)|Q1\s*[-–]\s*Q3|Q1\s*[-–]\s*Q2|9 months|nine months|reported)\b", re.I)
HISTORY = re.compile(r'(?:\|\s*|\b)(?:BEFORE|PREVIOUS|PRIOR VERSION)\s*:')
GU_WHY_MIN, GU_TRY_MIN = 150, 40
SOFT = [r'\bCAGR\b']  # soft: a past-years CAGR used as the path; fine beside a target, guide, consensus or cited outside source
BACK_RE = [re.compile(p, re.I) for p in BACK]; TREND_RE = [re.compile(p, re.I) for p in TREND]; SOFT_RE = [re.compile(p) for p in SOFT]
SOFT_OK = re.compile(r'\b(target|guide|guidance|framework|outlook|consensus|plan|deck|slide|management|market (?:report|data|forecast))\b', re.I)
HIST_FILL = re.compile(r'(back-extrapolat|extrapolated back|interpolat)', re.I)
QUARTER_SPLIT = re.compile(r'\bquarter', re.I)
YEAR_TOTAL = re.compile(r"\b(?:so the year|so the years|each year's total|the live year|the year's total|equal the (?:live )?year)", re.I)
NOT_USED = re.compile(r'\b(not used|shown only|cross-check only|but not used|for reference only)\b', re.I)
ACTUAL_OK = re.compile(r'\b(actual|reported|filed|10-[KQ]|reconcil\w*|base quarters|base year)\b', re.I)
FWD = re.compile(r'\b(guide|guidance|midpoint|forecast|FY20(?:2[6-9]|3\d)E?|projected|target)\b', re.I)
CITE = re.compile(r'\bsrcs?\.?\s*#?\d|https?://|\bsource_ids?\b', re.I)


def _j(p, d=None):
    try: return json.load(open(p))
    except (OSError, ValueError): return d


def _sentences(t):
    import run_digest
    return run_digest.sentences(t)


def _hit(rx, s):
    for r in rx:
        for m in r.finditer(s):
            if not NEG.search(s[:m.start()]): return m.group(0)


def _num(x): return x if isinstance(x, (int, float)) and not isinstance(x, bool) else None


def _pct_unit(tr):
    """Units of a track record's actual_vs_mid_pct / median_vs_mid_pct: 'pct' (3.4 = +3.4%), 'frac' (0.034) or None (unclear).
    Sets are mixed (AMAT writes percent, MOD and SPCX fractions), so read it off periods whose actual and first guide give the
    beat directly; with none, a value over 1 in size is percent (a >100% beat is not plausible); else unclear."""
    votes = {'pct': 0, 'frac': 0}; vals = []
    for p in tr.get('periods') or []:
        b, a = _num(p.get('actual_vs_mid_pct')), _num(p.get('actual'))
        lo, hi = _num(p.get('first_guide_low')), _num(p.get('first_guide_high'))
        if b is None: continue
        vals.append(b)
        if a is None or lo is None or hi is None or not (lo + hi): continue
        d = a / ((lo + hi) / 2) - 1
        if abs(d) < 1e-6: continue
        ep, ef = abs(b / 100 - d), abs(b - d)
        if min(ep, ef) <= 0.1 * abs(d) + 1e-4: votes['pct' if ep < ef else 'frac'] += 1
    if votes['pct'] != votes['frac']: return 'pct' if votes['pct'] > votes['frac'] else 'frac'
    m = _num(tr.get('median_vs_mid_pct'))
    if any(abs(v) > 1 for v in vals + ([m] if m is not None else [])): return 'pct'
    return None


def _beats(tr):
    """[(label, beat as a fraction)] for each past period, the stored median and the mean; both readings when units are unclear."""
    u = _pct_unit(tr)
    scale = {'pct': [(100, '')], 'frac': [(1, '')], None: [(100, ' if percent'), (1, ' if fraction')]}[u]
    per = [(p.get('period'), _num(p.get('actual_vs_mid_pct'))) for p in tr.get('periods') or []]
    per = [(k, b) for k, b in per if b is not None]
    out = []
    for k, sfx in scale:
        out += [(f"{per_k} beat{sfx}", b / k) for per_k, b in per]
        m = _num(tr.get('median_vs_mid_pct'))
        if m is not None: out.append((f"median beat{sfx}", m / k))
        if len(per) > 1: out.append((f"mean beat{sfx}", sum(b for _, b in per) / len(per) / k))
    return out


def guide_anchor(RI):
    out = []
    for g in RI.get('guidance_check') or []:
        lo, hi = _num(g.get('guide_low')), _num(g.get('guide_high'))
        if lo is None or hi is None or (lo == 0 and hi == 0): continue
        fy = str(g.get('fy') or ''); tr = g.get('track_record') or {}
        per = tr.get('periods') or []
        if any(str(p.get('period')) == fy and _num(p.get('actual')) is not None for p in per): continue  # closed period
        mid = (lo + hi) / 2
        anchors = [('guide midpoint', mid)]
        if _num(g.get('record_adjusted_guide')): anchors.append(('record-adjusted guide', g['record_adjusted_guide']))
        for name, b in _beats(tr): anchors.append((f"midpoint x (1 + {name} {b:+.2%})", mid * (1 + b)))
        seen = set()
        for k in ('our_value', 'drivers_imply'):
            v = _num(g.get(k))
            if v is None or v in seen: continue
            seen.add(v)
            for name, a in anchors:
                if a and abs(v / a - 1) <= TOL:
                    key = f"guide-anchor:{g.get('metric')}:{fy}"
                    kind = 'guide_x_beat' if name.startswith(('midpoint x', 'record-adjusted')) else 'guide'
                    bad = guide_use_problems(g.get('guide_use'), kind)
                    if not bad:  # Kilian 2026-11-17: last resort, allowed when flagged with a satisfactory explanation
                        out.append(dict(rule='guide-use', key=f"guide-use:{g.get('metric')}:{fy}", where=f"guidance_check {g.get('metric')} {fy}",
                                        value=v, anchor=name, kind=kind, decl=g['guide_use'], flag_only=True,
                                        why=f"{k} {v:,.6g} sits on the {name} {a:,.6g}: declared guide use ({kind}), flagged to Kilian"))
                        break
                    if g.get('guide_use') is not None:
                        out.append(dict(rule='guide-use-unexplained', key=f"guide-use-unexplained:{g.get('metric')}:{fy}", where=f"guidance_check {g.get('metric')} {fy}",
                                        value=v, why=f"{k} {v:,.6g} sits on the {name} {a:,.6g} and the guide_use declaration is incomplete: " + '; '.join(bad)))
                        break
                    out.append(dict(rule='guide-anchor', key=key, where=f"guidance_check {g.get('metric')} {fy}", value=v,
                                    why=f"{k} {v:,.6g} is within {TOL:.1%} of the {name} {a:,.6g} (guide {lo:,.6g}-{hi:,.6g}): "
                                        'build the year from its drivers; if nothing better exists, declare guidance_check guide_use '
                                        '(kind, why_nothing_better, tried, source) and it is flagged to Kilian instead (Kilian 2026-11-17)'))
                    break
            else: continue
            break
    return out


def guide_use_problems(gu, kind=None):
    """What a guide_use declaration lacks ([] = complete). kind: what the value sits on ('guide' / 'guide_x_beat'), or None."""
    if not isinstance(gu, dict): return ['no guide_use declaration']
    P = []
    if gu.get('kind') not in ('guide', 'guide_x_beat'): P.append('kind must be "guide" or "guide_x_beat"')
    elif kind and gu['kind'] != kind: P.append(f'kind is {gu["kind"]} but the value sits on the {kind.replace("_", " x ")}')
    w = str(gu.get('why_nothing_better') or '').strip()
    if len(w) < GU_WHY_MIN: P.append(f'why_nothing_better under {GU_WHY_MIN} chars: say why no bottom-up build from real drivers can set this period')
    elif _hit(TREND_RE, w) or _hit(CARRY_RE, w): P.append('why_nothing_better itself rests on a past trend or carry-forward')
    tr = [x for x in gu.get('tried') or [] if isinstance(x, dict) and len(str(x.get('build') or '').strip()) >= 20
          and len(str(x.get('why_not') or '').strip()) >= GU_TRY_MIN]
    if not tr: P.append(f'tried needs at least one {{build, why_not (>= {GU_TRY_MIN} chars)}}: the evidence-based build tried and why it cannot set the level')
    if not CITE.search(str(gu.get('source') or '')): P.append('source (src N or URL of the guide) missing')
    return P


def _dbq(RI, key):
    """driver_build.quarters[key] (or driver_build[key]) as a dict, {} when absent or not a dict."""
    db = RI.get('driver_build') if isinstance(RI.get('driver_build'), dict) else {}
    q = db.get('quarters') if isinstance(db.get('quarters'), dict) else db
    v = q.get(key)
    return v if isinstance(v, dict) else {}


def _current(x):
    """A derivation's current text: what precedes its history ("BEFORE:" / "PREVIOUS:" describe replaced paths)."""
    m = HISTORY.search(x) if isinstance(x, str) else None
    return x[:m.start()] if m else x


def _texts(RI, owners=False):
    t = []
    for g in RI.get('guidance_check') or []:
        t += [(f"guidance_check {g.get('metric')} {g.get('fy')} {k}", g.get(k)) for k in ('why', 'drivers_note')]
    ds = RI.get('drivers') or []
    for d in (ds.values() if isinstance(ds, dict) else ds):
        if not isinstance(d, dict): continue
        for k in ('derivation', 'quarter_derivation', 'variant', 'flag'):
            if k in ('variant', 'flag'): continue  # variants describe alternatives; flags describe weaknesses
            t.append((f"driver {d.get('id')} {k}", d.get(k)))
    for i in RI.get('inputs_detail') or []:
        if isinstance(i, dict) and not re.search(r'scenario[ _]vol', str(i.get('input')), re.I): t.append((f"inputs_detail {i.get('input')} derivation", i.get('derivation')))
    for k, n in _dbq(RI, 'constants_note').items():  # 2026-11-17: AMZN capex_q lived here, unchecked
        if isinstance(n, dict): t.append((f"driver_build constant {k} derivation", n.get('derivation')))
    return [(w, _current(x)) for w, x in t if isinstance(x, str) and x.strip()]


def _owner_decl(RI, where):
    """The guide_use declaration of the driver / constant / guidance row a text belongs to."""
    m = re.match(r'driver (\S+) ', where)
    if m:
        ds = RI.get('drivers') or []
        for d in (ds.values() if isinstance(ds, dict) else ds):
            if isinstance(d, dict) and str(d.get('id')) == m.group(1): return d.get('guide_use')
    m = re.match(r'driver_build constant (\S+) ', where)
    if m:
        n = _dbq(RI, 'constants_note').get(m.group(1))
        return n.get('guide_use') if isinstance(n, dict) else None
    for g in RI.get('guidance_check') or []:
        if where.startswith(f"guidance_check {g.get('metric')} {g.get('fy')} "): return g.get('guide_use')


def wording(RI):
    out = []
    for where, t in _texts(RI):
        for n, s in enumerate(_sentences(t)):
            b = _hit(BACK_RE, s)
            if b and ACTUAL_OK.search(s) and not FWD.search(s): b = None  # actual quarters reconciled to filed totals
            if b and QUARTER_SPLIT.search(s) and YEAR_TOTAL.search(s): b = None  # quarters shaped to sum to their own year
            if b and NOT_USED.search(s): b = None  # a record-adjusted figure shown as a cross-check
            if b:
                out.append(dict(rule='back-solve', key=f'back-solve:{where}:{n}', where=where, phrase=b, sentence=s[:400],
                                why='a driver or total set so it reaches a target (the guide, the guide x usual beat, an old value); build it from its own evidence'))
            tr = _hit(TREND_RE, s)
            if tr and HIST_FILL.search(s) and not FWD.search(s): tr = None  # filling a past quarter between observations
            if not tr:
                tr = _hit(SOFT_RE, s)
                if tr and (CITE.search(s) or SOFT_OK.search(s)): tr = None
            if tr:
                out.append(dict(rule='trend-only', key=f'trend-only:{where}:{n}', where=where, phrase=tr, sentence=s[:400],
                                why='a past trend or fit used as the forecast path; build the real-world case (market, share, capacity, contracts) with its own source'))
            c = _carry_hit(s)
            if c:
                out.append(dict(rule='carry-forward', key=f'carry-forward:{where}:{n}', where=where, phrase=c, sentence=s[:400],
                                why='a forecast period set from a past period (last quarter x a past ratio, a level held or rolled from a past period, '
                                    'a past step reused): project the economic drivers (orders, capacity, contracts, customers, prices, outside forecasts) '
                                    'with their own sources (Kilian 2026-11-17)'))
            gr = GUIDE_RESID.search(s)
            if gr and (NEG.search(s[:gr.start()]) or GUIDE_XCHECK.search(s)): gr = None  # a cross-check against the guide is fine
            if gr:
                decl = _owner_decl(RI, where); bad = guide_use_problems(decl)
                if not bad:
                    out.append(dict(rule='guide-use', key=f'guide-use:{where}:{n}', where=where, phrase=gr.group(0), sentence=s[:400], kind=decl.get('kind'),
                                    decl=decl, flag_only=True, why='the period is the guide less what is reported: declared guide use, flagged to Kilian'))
                else:
                    out.append(dict(rule='guide-residual', key=f'guide-residual:{where}:{n}', where=where, phrase=gr.group(0), sentence=s[:400],
                                    why='the period is the guide less what is already reported (a guide use): build it from its drivers, or declare guide_use '
                                        'on this driver / constant (' + '; '.join(bad) + ')'))
    return out


def _carry_hit(s):
    s = ROLL_LABEL.sub(' ', s)  # the "(roll forward 2026-11-02)" horizon-extension label is not itself a method
    if SKIP_ALT.search(s): return None  # alternatives / variants describe options, not the booked path
    if ACTUAL_OK.search(s) and not FWD.search(s) and not re.search(r'\b(?:view|est\w*|projected|next)\b', s, re.I): return None  # reported periods
    if QUARTER_SPLIT.search(s) and YEAR_TOTAL.search(s): return None  # a year built from its drivers split by a multi-year shape (rule 9)
    if HIST_FILL.search(s) and not FWD.search(s): return None
    for r in CARRY_RE:
        for m in r.finditer(s):
            pre = s[:m.start()]
            if NEG.search(pre) or CARRY_NEG.search(pre) or _quoted(pre): continue
            return m.group(0)
    for m in CARRY_MULT.finditer(s):
        pre = s[:m.start()]
        if NEG.search(pre) or CARRY_NEG.search(pre) or _quoted(pre): continue
        if MULT_HIST.search(s): return m.group(0)


def _qseries(RI):
    """[(where, quarters list, n actual)] for drivers and driver_build constants on the set's quarter grid."""
    Q = RI.get('quarters') or {}; A = Q.get('actual'); L = Q.get('labels') or []
    if not isinstance(A, int) or not L: return []
    out = []
    ds = RI.get('drivers') or []
    for d in (ds.values() if isinstance(ds, dict) else ds):
        if isinstance(d, dict) and (d.get('basis') or 'estimate') != 'reported' and isinstance(d.get('quarters'), list):
            out.append((f"driver {d.get('id')} quarters", d['quarters'], A))
    for k, v in _dbq(RI, 'constants').items():
        if isinstance(v, list): out.append((f"driver_build constant {k}", v, A))
    return [(w, q, a) for w, q, a in out if len(q) == len(L) and a >= 2 and len(q) > a and all(_num(x) is not None for x in q)]


def numeric_carry(RI):
    """carry-num: forecast quarters that repeat a past ratio of the set's own history (q/q same quarter, y/y) or hold the last
    actual flat. One failure per series (the first hit)."""
    import statistics as st
    L = (RI.get('quarters') or {}).get('labels') or []; out = []
    def same(r, x): return x and abs(r / x - 1) <= 2e-4 and abs(r - 1) > 1e-6
    for where, q, A in _qseries(RI):
        h, f = q[:A], q[A:]
        hit = None
        if len(set(round(x, 12) for x in f)) == 1 and abs(f[0] - h[-1]) <= 1e-9 * max(1, abs(h[-1])) and len(set(round(x, 9) for x in h)) > 1:
            hit = f'every forecast quarter = the last actual {h[-1]:.6g} held flat'
        for i in range(A, len(q)):
            if hit: break
            if i == A and q[i - 1]:  # the next quarter off the last actual (later quarters repeat q/q under any uniform y/y split)
                R = [q[j] / q[j - 1] for j in range(1, A) if (j - i) % 4 == 0 and q[j - 1]]
                r = q[i] / q[i - 1]
                if R:
                    for nm, x in [*((f'{L[j]} q/q' , q[j] / q[j - 1]) for j in range(1, A) if (j - i) % 4 == 0 and q[j - 1]),
                                  ('min past same-quarter q/q', min(R)), ('max past same-quarter q/q', max(R)),
                                  ('mean past same-quarter q/q', sum(R) / len(R)), ('median past same-quarter q/q', st.median(R))]:
                        if same(r, x): hit = f'{L[i]} = {L[i - 1]} x {r:.4f}, the {nm}'; break
            if not hit and i >= 4 and q[i - 4]:
                Y = [q[j] / q[j - 4] for j in range(4, A) if q[j - 4]]
                r = q[i] / q[i - 4]
                if Y:
                    for nm, x in (('last actual y/y', Y[-1]), ('mean past y/y', sum(Y) / len(Y))):
                        if same(r, x): hit = f'{L[i]} = {L[i - 4]} x {r:.4f}, the {nm}'; break
        if hit:
            out.append(dict(rule='carry-num', key=f'carry-num:{where}', where=where, value=hit,
                            why=f'{hit}: the forecast repeats the past in numbers; set it from its economic drivers with their own sources (Kilian 2026-11-17)'))
    return out


def guide_uses(RI):
    """Declared guide uses (flag-only rows) of a set: what Kilian is shown."""
    return [f for f in guide_anchor(RI) + wording(RI) if f.get('flag_only')]


def lint(RI): return [f for f in guide_anchor(RI) + wording(RI) + numeric_carry(RI) if not f.get('flag_only')]


def overrides(D, s): return (_j(os.path.join(RUNS, D, s, 'lint_overrides.json'), {}) or {}).get('overrides') or {}


NEW_1117 = ('carry-forward', 'carry-num', 'guide-residual', 'guide-use-unexplained')
# Kilian 2026-11-18 (AMZN Q4 AWS $49,663M carried through gate rounds 1-2 while its reason changed): carry_trace.py hits.
# Gate/finish enforce now; buys only on rounds dated on or after BUY_BLOCK_FROM_1118 (same grandfather as 11-17).
NEW_1118 = ('carry-step', 'carry-held', 'carry-base')
BUY_BLOCK_FROM_1118 = '2026-11-19'
# Buys on rounds gated before the 2026-11-17 rules: the new carry checks block gate/finish of every update from today, and
# block buys only on rounds dated on or after BUY_BLOCK_FROM (older rounds get them at their next update; Kilian's card).
BUY_BLOCK_FROM = '2026-11-18'


def round_fail(D, s, all_=False, for_buys=False):
    """Failures for runs/D/SYM/supervisor.json not passed by a logged override (all_=True: every failure, override noted).
    for_buys (rules_check.py): the 2026-11-17 rules count only on rounds dated on or after BUY_BLOCK_FROM."""
    sup = _j(os.path.join(RUNS, D, s, 'supervisor.json'), {}) or {}
    RI = (sup.get('model') or {}).get('reconciled_inputs')
    if not isinstance(RI, dict): return []
    ov = overrides(D, s); out = []
    import carry_trace  # numbers the model books traced to past periods and to the base / earlier gate rounds (2026-11-18)
    try: ct = carry_trace.trace(D, s, RI)
    except Exception as e: ct = [dict(rule='carry-base', key='carry-trace-error', where='carry_trace.py', value=str(e)[:200],
                                      why=f'carry_trace.py could not read this set ({type(e).__name__}: {str(e)[:160]}); fix the set or the script')]
    for f in lint(RI) + ct:
        if for_buys and f['rule'] in NEW_1117 and str(D) < BUY_BLOCK_FROM: continue
        if for_buys and f['rule'] in NEW_1118 and str(D) < BUY_BLOCK_FROM_1118: continue
        if f['key'] in ov:
            if all_: out.append(dict(f, overridden=ov[f['key']]))
        else: out.append(f)
    return out


def round_guide_uses(D, s):
    sup = _j(os.path.join(RUNS, D, s, 'supervisor.json'), {}) or {}
    RI = (sup.get('model') or {}).get('reconciled_inputs')
    return guide_uses(RI) if isinstance(RI, dict) else []


def guide_uses_md(D, s):
    """Markdown for Kilian: each declared guide use with its explanation (cards, update reply, finish flags, memo)."""
    G = round_guide_uses(D, s)
    if not G: return f'No guide used as a level in {s} {D}.'
    L = [f'**{s}: guide used as a last resort ({len(G)})** (Kilian 2026-11-17: allowed only when nothing better exists, flagged)']
    for g in G:
        d = g.get('decl') or {}
        L.append(f"- {g['where']} ({'guide x usual beat' if d.get('kind') == 'guide_x_beat' else 'guide'}): {d.get('why_nothing_better', '')}")
        L += [f"  - tried: {x.get('build')}: {x.get('why_not')}" for x in d.get('tried') or [] if isinstance(x, dict)]
        L.append(f"  - guide source: {d.get('source')}")
    return '\n'.join(L)


def flag_lines(D, s):
    """One line per declared guide use for name_update.py finish flags."""
    return [f"GUIDE USED (last resort, {(g.get('decl') or {}).get('kind')}) {g['where']}: {str((g.get('decl') or {}).get('why_nothing_better', ''))[:220]}"
            for g in round_guide_uses(D, s)]


def gate_rows(D, s):
    """Rows for run_digest.py gate-merge (result back_solved / trend_only fail the gate)."""
    res = {'guide-anchor': 'back_solved', 'back-solve': 'back_solved', 'trend-only': 'trend_only', 'guide-residual': 'back_solved',
           'guide-use-unexplained': 'back_solved', 'carry-forward': 'trend_only', 'carry-num': 'trend_only',
           'carry-step': 'trend_only', 'carry-held': 'back_solved', 'carry-base': 'trend_only'}
    return [dict(origin='forecast_lint.py (script)', item=f['where'], value=f.get('value', f.get('phrase')), basis='', source_url='',
                 result=res[f['rule']], page_shows='', note=f"{f['why']} | key {f['key']}" + (f" | {f['sentence']}" if f.get('sentence') else ''),
                 checked_by='forecast_lint.py (script)') for f in round_fail(D, s)]


def refuse(D, s, step):
    f = round_fail(D, s)
    if not f: return
    lines = '\n'.join(f"  [{x['rule']}] {x['where']}: {x['why']}" + (f"\n      \"{x['sentence'][:220]}\"" if x.get('sentence') else '') + f"\n      key {x['key']}" for x in f)
    sys.exit(f"REFUSED (forecast-lint, {step} {s} {D}): {len(f)} forecast(s) set to the guide without a declared guide_use, back-solved, "
             f"trend-only or carried forward from past periods (Kilian 2026-11-09, 2026-11-17).\n{lines}\n"
             'Rebuild those periods from their economic drivers (orders, capacity, contracts, customers, prices, outside forecasts). '
             'The guide or guide x usual beat is a last resort: declare guide_use (see the docstring) and it is flagged to Kilian. '
             'Only if a hit is wrong (a bottom-up build that happens to land on the guide, quoted wording, a carry with its own sourced '
             f'economic case): python3 {HERE}/forecast_lint.py override {D} {s} KEY --why "<reason>" (logged; a carry key needs a src id or URL in the reason).')


def override(D, s, key, why):
    import guards
    keys = {f['key'] for f in round_fail(D, s, all_=True)}
    if key not in keys: sys.exit(f'no such failure key for {s} {D}: {key}')
    if key.startswith('carry') and not (CITE.search(why or '') and len((why or '').strip()) >= 60):
        sys.exit('a carry-forward override needs the economic case in --why (>= 60 chars) with its src id or URL (Kilian 2026-11-17)')
    guards.need_why('forecast-lint', why, f'{s} {D} {key}', symbol=s, round=D, key=key)
    p = os.path.join(RUNS, D, s, 'lint_overrides.json'); o = _j(p, {}) or {}
    import datetime as dt
    o.setdefault('overrides', {})[key] = dict(why=why, utc=dt.datetime.now(dt.timezone.utc).strftime('%Y-%m-%dT%H:%MZ'))
    json.dump(o, open(p, 'w'), indent=1); print(f'override logged: {s} {D} {key}')


def current_sets(D=None):
    """{SYM: research_as_of} from the newest run folder on or before D that has the name's result.json."""
    import datetime as dt
    D = D or dt.date.today().isoformat()
    dates = sorted(x for x in os.listdir(RUNS) if len(x) == 10 and x[4] == '-' and x <= D)[-15:]
    out = {}
    for d in dates:
        for f in glob.glob(os.path.join(RUNS, d, '*', 'result.json')):
            ra = (_j(f, {}) or {}).get('research_as_of')
            if ra: out[f.split('/')[-2]] = ra
    for d in dates:  # research folders newer than the last re-mark (an update round not re-marked yet)
        for f in glob.glob(os.path.join(RUNS, d, '*', 'update.json')):
            s = f.split('/')[-2]
            if os.path.exists(os.path.join(RUNS, d, s, 'citation_audit.json')) and d > out.get(s, ''): out[s] = d
    return out


def all_(D=None):
    import concurrent.futures as cf
    cs = current_sets(D)
    def one(it):
        s, ra = it
        sup = _j(os.path.join(RUNS, ra, s, 'supervisor.json'), {}) or {}
        RI = (sup.get('model') or {}).get('reconciled_inputs')
        if not isinstance(RI, dict) or not RI.get('drivers'): return s, ra, None
        return s, ra, round_fail(ra, s, all_=True)
    with cf.ThreadPoolExecutor(16) as ex: rows = list(ex.map(one, sorted(cs.items())))
    rep = {s: dict(round=ra, failures=f) for s, ra, f in rows if f is not None}
    json.dump(rep, open(os.path.join(HERE, 'data', 'forecast_lint.json'), 'w'), indent=1)
    for s, r in rep.items():
        c = {}
        for f in r['failures']:
            if not f.get('overridden'): c[f['rule']] = c.get(f['rule'], 0) + 1
        print(f"{s:6} {r['round']}  " + (', '.join(f'{k} {v}' for k, v in sorted(c.items())) or 'clean'))
    return rep


if __name__ == '__main__':
    a = sys.argv[1:]
    def opt(k):
        if k in a: i = a.index(k); v = a[i + 1]; del a[i:i + 2]; return v
    why = opt('--why')
    if not a: print(__doc__); sys.exit(1)
    if a[0] == 'check':
        RI = (_j(a[1], {}).get('model') or {}).get('reconciled_inputs') or _j(a[1], {})
        f = lint(RI); print(json.dumps(f, indent=1)); sys.exit(1 if f else 0)
    elif a[0] == 'round':
        f = round_fail(a[1], a[2].upper(), all_=True); print(json.dumps(f, indent=1)); sys.exit(1 if any(not x.get('overridden') for x in f) else 0)
    elif a[0] == 'override': override(a[1], a[2].upper(), a[3], why or '')
    elif a[0] == 'guide-uses':
        if len(a) == 2: a = [a[0], *a[1].rstrip('/').split('/')[-2:]]  # runs/D/SYM form (memo brief)
        print(guide_uses_md(a[1], a[2].upper()))
    elif a[0] == 'all': all_(a[1] if len(a) > 1 else None)
    else: print(__doc__); sys.exit(1)
