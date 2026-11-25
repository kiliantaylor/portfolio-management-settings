"""Transcript speaker check for the citation gate (RULES.md 25; Kilian 2026-11-18, NVDA: "that coreweave mistake cannot happen").

What went wrong: on CoreWeave's Q2 2026 call, Evercore analyst Irvin Liu asked about '>3 GW active by end of next year'. The
NVDA set's source title read "CoreWeave Q2 2026 corrected transcript (2026-08-11): Q2 capex $9.4B; >3 GW active targeted by
end-2027" with kind company_guide, so the analyst's figure became a CoreWeave target. It set the ACIE FY29 step and reached a
card Kilian picked on. Nothing recorded WHO said it, so no check could tell.

The rule (RULES.md 25): every figure or quote taken from an earnings-call transcript, conference or interview names its
speaker and role. Only company speakers make company statements. An analyst's question, estimate or framing counts as
company evidence only when an executive restates it in their own words and that answer is quoted.

Source field (supervisor.json `sources`, forecaster `sources`, driver-set `sources`) on every transcript source:
  "speakers": [{"says": "the figure or quote as the set uses it", "speaker": "Irvin Liu", "role": "analyst",
                "firm": "Evercore ISI",                      # required for an analyst
                "restated": {"speaker": "Nitin Agrawal", "role": "CFO", "quote": "the executive's own words"}}]   # optional
  role: a company role (CEO, CFO, COO, CTO, President, Chair, EVP/SVP/VP ..., IR, "management"), or analyst (with firm), or
  other (operator, moderator, host, journalist).
A transcript source is refused when:
  speaker_missing     it has no `speakers`, an entry has no speaker or an unrecognized role (an analyst with no firm too),
                      or a figure in its title is not covered by any entry's `says`;
  analyst_as_company  an analyst/other figure (no valid `restated`) appears in the source title or in a sentence of the set
                      that cites the source, and that text does not say it is the analyst's (the word "analyst", or the
                      speaker's name with "asked"/"question"). A `restated` clears it only with a company role and a quote of
                      15+ characters that contains the figure.
At the gate, shard checkers record `speaker` / `speaker_role` / `speaker_firm` on every row for a transcript URL
(run_digest.py marks them `[TRANSCRIPT ...]`); gate-merge adds:
  speaker_missing     a checker row for a transcript URL with no speaker_role,
  analyst_as_company  the checker found the figure in an analyst's or other non-company speaker's words and the row does
                      not label it as theirs,
  speaker_mismatch    the set's `speakers` entry for the figure names a company role, the checker found an analyst (or the reverse).
name_update.py finish refuses a gated round (gate/speaker.json) with any of these. The only override is --why that QUOTES the
executive's own words ("..." of 15+ characters) and names a company role; it is logged (data/rule_overrides.jsonl).

Usage:
  python3 speaker.py check SET_OR_SUPERVISOR.json [SUPERVISOR.json]   flags for one set (driver-set gates: GATE.md, GATE_Q.md)
  python3 speaker.py scan                                              every live driver-model set -> data/speaker_scan.json
Tests: python3 test_speaker.py
"""
import datetime, json, os, re, sys
HERE = os.path.dirname(os.path.abspath(__file__)).replace('/mnt/attach/project-files', '/mnt/project-files')
RUNS = os.environ.get('VAL_RUN_ROOT', f'{HERE}/runs')
sys.path.insert(0, HERE)

TRANSCRIPT_TITLE = re.compile(
    r'transcript|\bearnings[- ]calls?\b|\bconference[- ]calls?\b|\bcall highlights\b|\bcall recap\b|'
    r'\b(?:q[1-4]|quarter|quarterly|results|fy\s?\d{2,4})\b[^|:;]{0,40}\bcall\b|'
    r'\bfireside\b|\binterview\b|\binvestor day\b|\banalyst day\b|\bcapital markets day\b|\bpodcast\b|\bkeynote\b|'
    r'\bq&a\b|\blive[- ]blog\b|\blive updates\b', re.I)
TRANSCRIPT_URL = re.compile(r'transcript|call-transcripts|/live/[^/]*earnings', re.I)
NOT_TRANSCRIPT = re.compile(r'\b(10-[KQ]|8-K|20-F|6-K|ex\.?\s*99|press release|news release|shareholder letter|prepared remarks)\b', re.I)

COMPANY_ROLE = re.compile(r'\b(ceo|cfo|coo|cto|cro|cso|cmo|cco|chief\b.*|president|chair(?:man|woman|person)?|co-?founder|founder|'
                          r'evp|svp|vp|vice president|head of .*|investor relations|ir|treasurer|controller|general counsel|'
                          r'executive|management|company|director)\b', re.I)
ANALYST_ROLE = re.compile(r'\b(analyst|research|sell[- ]side|strategist|equity research)\b', re.I)
OTHER_ROLE = re.compile(r'\b(operator|moderator|host|journalist|reporter|interviewer|anchor|panelist|investor|shareholder|other)\b', re.I)
LABELLED = re.compile(r"\banalyst'?s?\b|\banalysts'\b", re.I)
RESTATED_MIN = 15

UNIT = r'(?:\s?(?:%|percent|bps?|bp|x\b|gw|mw|kw|twh|gwh|mwh|b\b|bn|billion|m\b|mm|million|k\b|thousand|t\b|trillion))?'
NUM = re.compile(r'(?<![A-Za-z\d.])(?:\$\s?)?(\d+(?:,\d{3})*(?:\.\d+)?)' + f'({UNIT})', re.I)
NOT_FIGURE_BEFORE = re.compile(r'(?:\bq|\bfy|\bcy|\bh|\bgen|\bv|\bno\.?|\bsrc|\bsrcs|\bphase|\btpu|\bmi|\bitem|\bnote)\s?$', re.I)


def role_class(r):
    r = (r or '').strip()
    if not r: return 'missing'
    if re.search(r'investor relations|\bir\b', r, re.I): return 'company'
    if ANALYST_ROLE.search(r): return 'analyst'
    if OTHER_ROLE.search(r): return 'other'
    if COMPANY_ROLE.search(r): return 'company'
    return 'unknown'


def figures(text):
    """{(number, unit)} figures in a text: years, dates, quarter / fiscal labels and src ids left out."""
    out, t = set(), text or ''
    t = re.sub(r'\b(?:19|20)\d\d-\d\d(?:-\d\d)?\b', ' ', t)
    for m in NUM.finditer(t):
        n, u = m.group(1).replace(',', ''), re.sub(r'\s', '', (m.group(2) or '').lower())
        before = t[max(0, m.start() - 6):m.start()]
        if NOT_FIGURE_BEFORE.search(before): continue
        if re.fullmatch(r'(?:19|20)\d\d', n) and not u: continue  # a year
        if t[m.end():m.end() + 1].isalpha() and not u: continue  # 8t / 8i / 3x-style labels
        u = {'percent': '%', 'bps': 'bp', 'bn': 'b', 'billion': 'b', 'mm': 'm', 'million': 'm', 'thousand': 'k', 'trillion': 't'}.get(u, u)
        out.add((n.rstrip('0').rstrip('.') if '.' in n else n, u))
    return out


def _hit(fig, figs):
    """A figure is used in a text when the same number appears with the same unit (or, unitless, a number of 2+ digits)."""
    n, u = fig
    if u: return (n, u) in figs
    return len(n.replace('.', '')) >= 2 and any(x[0] == n for x in figs)


def is_transcript(src):
    t, u = src.get('title') or '', src.get('url') or ''
    if src.get('speakers'): return True
    if NOT_TRANSCRIPT.search(t) and not re.search(r'transcript', t, re.I): return False
    return bool(TRANSCRIPT_TITLE.search(t) or TRANSCRIPT_URL.search(u))


def _restated_ok(e):
    r = e.get('restated') or {}
    q = (r.get('quote') or '').strip()
    if role_class(r.get('role')) != 'company' or not (r.get('speaker') or '').strip() or len(q) < RESTATED_MIN: return False
    need = figures(e.get('says'))
    return not need or any(_hit(f, figures(q)) for f in need)


def _labels_speaker(text, e):
    """True when the text says the figure is the analyst's / non-company speaker's."""
    if LABELLED.search(text or ''): return True
    sp = (e.get('speaker') or '').strip()
    last = sp.split()[-1] if sp else ''
    return bool(last and re.search(re.escape(last), text or '', re.I) and re.search(r'\b(ask(?:ed|s)?|question|estimate[sd]?|said)\b', text or '', re.I))


def screen(src, i, sentences=()):
    """One transcript source -> list of flags. sentences: [(where, text)] of the set that cite this source."""
    base = dict(src=i, title=(src.get('title') or '')[:200], url=src.get('url'))
    S = src.get('speakers')
    if not isinstance(S, list) or not S:
        return [dict(base, result='speaker_missing', figure='', why='transcript source with no `speakers`: record who said each figure the set '
                     'takes from it (speaker, role; firm for an analyst), RULES.md 25')]
    out, covered = [], set()
    for e in S:
        if not isinstance(e, dict): out.append(dict(base, result='speaker_missing', figure=str(e)[:80], why='`speakers` entry is not {says, speaker, role}')); continue
        rc, says = role_class(e.get('role')), (e.get('says') or '').strip()
        figs = figures(says); covered |= figs
        tag = dict(base, figure=says[:160], speaker=e.get('speaker'), role=e.get('role'), role_class=rc)
        if not says or not (e.get('speaker') or '').strip() or rc in ('missing', 'unknown'):
            out.append(dict(tag, result='speaker_missing', why='entry needs `says`, the speaker\'s name and a recognized role (CEO, CFO, ... IR, analyst, other)')); continue
        if rc == 'analyst' and not (e.get('firm') or '').strip():
            out.append(dict(tag, result='speaker_missing', why='an analyst entry needs the analyst\'s `firm`')); continue
        if rc == 'company' or _restated_ok(e): continue
        if e.get('restated'):
            out.append(dict(tag, result='analyst_as_company', why='`restated` does not count: it needs a company role, the executive\'s name and their '
                            f'own words ({RESTATED_MIN}+ characters) containing the figure')); continue
        # a non-company figure: it may be used only labelled as that speaker's
        t = src.get('title') or ''
        tf = figures(t)
        if any(_hit(f, tf) for f in figs) and not _labels_speaker(t, e):
            out.append(dict(tag, result='analyst_as_company', where='source title',
                            why=f"the title gives the {rc}'s figure as the company's: say it is {e.get('speaker')}'s ({e.get('firm') or rc}) "
                                'question/estimate, or drop it; it can be company evidence only when an executive restates it (`restated`)'))
        for where, sen in sentences:
            sf = figures(sen)
            if any(_hit(f, sf) for f in figs) and not _labels_speaker(sen, e):
                out.append(dict(tag, result='analyst_as_company', where=where, sentence=sen[:400],
                                why=f"this sentence uses the {rc}'s figure as a company statement: label it as {e.get('speaker')}'s "
                                    f"({e.get('firm') or rc}) or rebuild the value on what the company said"))
    t = src.get('title') or ''
    for f in sorted(figures(t)):
        if f[1] == '' and len(f[0].replace('.', '')) < 2: continue
        if not any(_hit(f, {c}) or c == f for c in covered):
            out.append(dict(base, result='speaker_missing', figure=f'{f[0]}{f[1]}', where='source title',
                            why=f"figure {f[0]}{f[1]} in the title has no `speakers` entry: say who said it"))
    return out


# history, not use: what a correction changed FROM, the audit trail, card text Kilian already saw, pre-change copies
HISTORY = re.compile(r'(^|\.)(citation_audit|judgment_calls_resolved|changes|\w*_before\w*|\w*previous\w*)(\.|\[|$)|\.(from|old|was)$')


def _strings(x, path=''):
    if path and HISTORY.search(path): return
    if isinstance(x, dict):
        for k, v in x.items():
            if k in ('sources', 'speakers'): continue
            yield from _strings(v, f'{path}.{k}' if path else k)
    elif isinstance(x, list):
        for j, v in enumerate(x): yield from _strings(v, f'{path}[{j}]')
    elif isinstance(x, str): yield path, x


def cite_sentences(body, url_of):
    """{src id: [(where, sentence)]} for every sentence in the set (outside `sources`) that cites a source by id or URL."""
    import run_digest
    out = {}
    for where, s in _strings(body):
        if len(s) < 20: continue
        for sen in run_digest.sentences(s):
            ids = set(run_digest.src_ids(sen))
            ids |= {i for i, u in url_of.items() if u and u in sen}
            for i in ids: out.setdefault(i, []).append((where, sen))
    return out


def check_set(obj, sources):
    """Flags for every transcript source the set cites anywhere (rows, derivations, cards, options, corrections)."""
    import timeliness
    body = {k: v for k, v in obj.items() if k != 'sources'} if isinstance(obj, dict) else obj
    ids, txt = timeliness.cited_ids(body), json.dumps(body)
    url_of = {i: s.get('url') for i, s in enumerate(sources or [], 1)}
    sens = cite_sentences(body, url_of)
    out = []
    for i, s in enumerate(sources or [], 1):
        if not is_transcript(s): continue
        if not (i in ids or (s.get('url') and s['url'] in txt)): continue
        out += screen(s, i, sens.get(i, []))
    return out


def gate_rows(flags, origin='speaker.py (script)'):
    return [dict(origin=origin, item=f"src {f['src']} {f['title'][:110]}" + (f" | {f['where']}" if f.get('where') else ''),
                 value=f.get('figure') or '', basis='transcript', source_url=f['url'], result=f['result'], checked_by='speaker.py (script)',
                 page_shows=(f"speaker {f.get('speaker')} ({f.get('role')})" if f.get('speaker') else '') + (f" | {f['sentence']}" if f.get('sentence') else ''),
                 note=f['why']) for f in flags]


# ---------------------------------------------------------------- citation gate wiring
def gate_prep(rd, scope_urls):
    """run_digest.gate_prep: transcript URLs among the rows to open (shard briefs mark them) -> gate/speaker.json."""
    import timeliness
    tu = {}
    for lab, src in timeliness._source_lists(rd):
        for i, x in enumerate(src, 1):
            u = x.get('url')
            if u and u in scope_urls and u not in tu and is_transcript(x): tu[u] = dict(list=lab, src=i)
    os.makedirs(f'{rd}/gate', exist_ok=True)
    json.dump(dict(date=datetime.date.today().isoformat(), transcript_urls=tu), open(f'{rd}/gate/speaker.json', 'w'), indent=1)
    return set(tu)


def _entries_for(rd, url):
    import timeliness
    out = []
    for lab, src in timeliness._source_lists(rd):
        for x in src:
            if x.get('url') == url: out += [e for e in x.get('speakers') or [] if isinstance(e, dict)]
    return out


def _names_speaker(text, r):
    """True when the text names the checker-recorded speaker (surname) or their firm, e.g. 'Kuo survey' (Kilian 2026-11-23)."""
    sp = (r.get('speaker') or '').strip()
    last = sp.split()[-1] if sp else ''
    firm = re.split(r'\s*\(', (r.get('speaker_firm') or '').strip())[0].strip()
    return any(w and re.search(r'\b' + re.escape(w) + r'\b', text or '', re.I) for w in (last, firm))


def agent_rows_check(rd, agent_rows):
    """Gate rows from the checkers' speaker records on transcript URLs."""
    p = f'{rd}/gate/speaker.json'
    if not os.path.exists(p): return []
    tu = set((json.load(open(p)).get('transcript_urls') or {}))
    out = []
    for r in agent_rows or []:
        u = r.get('source_url')
        if u not in tu or r.get('_src') not in ('shard', None) or str(r.get('origin', '')).startswith('speaker.py'): continue
        if r.get('basis') == 'timeliness' or str(r.get('origin', '')).startswith('timeliness.py'): continue  # date-only rows carry no figure
        rc = role_class(r.get('speaker_role'))
        if rc == 'missing' and r.get('carried_from_round'): continue  # checked in an earlier round, before checkers recorded speakers
        row = dict(origin='speaker.py (gate checker record)', item=r.get('item'), value=r.get('value'), basis='transcript', source_url=u,
                   checked_by='speaker.py (script, from the shard record)', page_shows=f"speaker {r.get('speaker') or '?'} ({r.get('speaker_role') or '?'}"
                   + (f", {r.get('speaker_firm')}" if r.get('speaker_firm') else '') + ')')
        if rc in ('missing', 'unknown'):
            out.append(dict(row, result='speaker_missing', note='the checker did not record who said this figure on the transcript (speaker_role): re-check it'))
            continue
        label = ' '.join(str(r.get(k) or '') for k in ('item', 'value', 'origin'))
        figs = figures(str(r.get('value') or '') + ' ' + str(r.get('item') or ''))
        ents = [e for e in _entries_for(rd, u) if any(_hit(f, figures(e.get('says'))) for f in figs)]
        if rc in ('analyst', 'other') and not LABELLED.search(label) and not _names_speaker(label, r) and not any(_restated_ok(e) for e in ents):
            out.append(dict(row, result='analyst_as_company', note=f"the checker found this figure in a {rc}'s words, not a company speaker's (RULES.md 25): "
                            'label it as theirs, or rebuild the value on what the company said; an executive restating it counts only quoted (`restated`)'))
            continue
        for e in ents:
            ec = role_class(e.get('role'))
            if ec in ('company', 'analyst', 'other') and (ec == 'company') != (rc == 'company'):
                out.append(dict(row, result='speaker_mismatch', note=f"the set's `speakers` entry says {e.get('speaker')} ({e.get('role')}); the checker found "
                                f"{r.get('speaker')} ({r.get('speaker_role')}): fix the entry and every use of the figure"))
                break
    return out


def gate_check(rd, agent_rows=None):
    """gate-merge rows: script flags on the set's transcript sources + the checkers' speaker records. Re-reads supervisor.json."""
    if not os.path.exists(f'{rd}/gate/speaker.json'): return []
    rows = []
    if os.path.exists(f'{rd}/supervisor.json'):
        S = json.load(open(f'{rd}/supervisor.json'))
        rows += gate_rows(check_set(S, S.get('sources') or []))
    return rows + agent_rows_check(rd, agent_rows)


QUOTE = re.compile(r'["“\'‘]([^"”\'’]{%d,})["”\'’]' % RESTATED_MIN)


def refuse(U, s, step):
    """name_update.py finish: refuse a gated round with a speaker failure. No override (Kilian 2026-11-25: "there is absolutely no
    bypass permitted"; the old --why quoting the executive was removed): record `speakers` on the source instead."""
    rd = f'{RUNS}/{U}/{s}'
    if not os.path.exists(f'{rd}/gate/speaker.json'): return
    mp = f'{rd}/gate/merged.json'
    agent = json.load(open(mp)).get('results') if os.path.exists(mp) else []
    rows = gate_check(rd, [r for r in agent or [] if not str(r.get('origin', '')).startswith('speaker.py')])
    # Kilian 2026-11-25 card 'Fix the check and finish DY': a stored gate row whose claim text no longer appears anywhere in
    # supervisor.json was rewritten after the last gate round; it no longer reaches him, and refuse_set still checks the live set.
    sp = f'{rd}/supervisor.json'
    if rows and os.path.exists(sp):
        _S = json.load(open(sp)); _S.pop('citation_audit', None)  # the gate-fix log keeps old text as history, not live text
        live = json.dumps(_S, ensure_ascii=False)
        stale = [r for r in rows if len(str(r.get('value') or '')) >= 40 and str(r['value']) not in live]
        if stale:
            print(f"speaker.refuse: {len(stale)} stored gate row(s) skipped, text no longer in supervisor.json: "
                  + '; '.join(str(r['value'])[:80] for r in stale))
        rows = [r for r in rows if r not in stale]
    if not rows: return
    lines = '\n'.join(f"  [{r['result']}] {r['item']}: {r['value']} {r['page_shows']}" for r in rows)
    sys.exit(f"REFUSED (transcript-speaker): {s} {U}: {len(rows)} transcript figure(s) with a missing or non-company speaker (RULES.md 25, "
             f"Kilian 2026-11-18, CoreWeave '>3 GW' was an analyst's question).\n{lines}\nRecord `speakers` on the source, label an analyst's "
             "figure as theirs, or rebuild the value on what the company said. " + NO_BYPASS)


def refuse_set(U, s, step):
    """name_update.py gate (before any round) and finish: refuse while a transcript source the set cites has no `speakers`
    entry for a figure the set takes from it, or a non-company figure is used as the company's (check_set on supervisor.json).
    Kilian 2026-11-23 (MRVL src 14/63 reached a gate round with no speakers: "this shouldn't be allowed to happen").
    No override (Kilian 2026-11-25)."""
    rd = f'{RUNS}/{U}/{s}'
    if not os.path.exists(f'{rd}/supervisor.json'): return
    S = json.load(open(f'{rd}/supervisor.json'))
    fl = check_set(S, S.get('sources') or [])
    if not fl: return
    lines = '\n'.join(f"  [{f['result']}] src {f['src']} {f['title'][:100]} | {f.get('where') or ''} {f.get('figure') or ''}: {f['why']}" for f in fl)
    sys.exit(f"REFUSED (transcript-speaker, {step}): {s} {U}: {len(fl)} transcript problem(s) in supervisor.json before {step} (RULES.md 25: "
             f"every figure taken from a transcript names its speaker and role).\n{lines}\nAdd `speakers` [{{says, speaker, role, firm for an "
             f"analyst}}] to each source (check: python3 {HERE}/speaker.py check {rd}/supervisor.json). " + NO_BYPASS)


NO_BYPASS = 'No override exists (Kilian 2026-11-25: "there is absolutely no bypass permitted"); if a fix seems impossible, tell Kilian the reason and ask.'


# ---------------------------------------------------------------- saved transcripts (transcripts.py): who said each figure
TDIR = f'{HERE}/notes/transcripts'
PARA = re.compile(r'^\*\*([^*:]{2,60}):\*\*\s*(.*)$', re.M)
FROM_LINE = re.compile(r'(?:from the line of|comes from|question is from|go to|from)\s+([A-Z][\w.\'-]+(?:\s+[A-Z][\w.\'-]+){0,3})\s+(?:with|from|of|at)\s+'
                       r'([A-Z][\w&.\'-]*(?:\s+(?:[A-Z][\w&.\'-]*|&|and|de|of))*)', re.M)


def saved_transcript(url):
    """Path of the saved transcript whose Source line is this URL, or None."""
    if not url or not os.path.isdir(TDIR): return None
    for f in sorted(os.listdir(TDIR)):
        if f.endswith('.md'):
            try: head = open(f'{TDIR}/{f}', encoding='utf-8').read(600)
            except OSError: continue
            if url.rstrip('/') in head: return f'{TDIR}/{f}'
    return None


def speakers_of(path):
    """[(speaker, role class, firm, paragraph)] from a saved transcript: analysts are the names the operator introduces 'with FIRM'."""
    t = open(path, encoding='utf-8').read()
    marks = list(PARA.finditer(t))  # a speaker's turn runs to the next bold label (several paragraphs)
    paras = [(m.group(1), t[m.start(2):(marks[k + 1].start() if k + 1 < len(marks) else len(t))]) for k, m in enumerate(marks)]
    analysts = {}
    for sp, body in paras:
        if sp.lower() == 'operator' or 'question' in body.lower()[:400]:
            for nm, firm in FROM_LINE.findall(body): analysts.setdefault(nm.strip(), re.split(r'\.\s|\.$', firm.strip())[0])
    out = []
    for sp, body in paras:
        sp = sp.strip()
        if sp.lower() in ('operator', 'unknown speaker', 'unidentified analyst'): rc, firm = ('other' if sp.lower() == 'operator' else 'unknown'), ''
        elif sp in analysts: rc, firm = 'analyst', analysts[sp]
        else: rc, firm = 'company', ''
        out.append((sp, rc, firm, body))
    return out


def attribute(src):
    """For a transcript source with a saved transcript: {figure: [(speaker, role class, firm)]} for each figure in its title."""
    p = saved_transcript(src.get('url'))
    if not p: return None
    S = speakers_of(p); res = {}
    for f in figures(src.get('title')):
        if f[1] == '' and len(f[0].replace('.', '')) < 2: continue
        res[f'{f[0]}{f[1]}'] = sorted({(sp, rc, firm) for sp, rc, firm, body in S if _hit(f, figures(body))})
    return res


def _load(p, sp=None):
    import timeliness
    return timeliness._load_set(p, sp)


def scan():
    import driver_names, timeliness
    today = datetime.date.today().isoformat()
    rows, missing, likely = [], [], []
    for sym, ra in driver_names.names(today).items():
        sp = timeliness.live_supervisor(sym, ra)
        if not sp: missing.append(f'{sym} ({ra})'); continue
        RI, src, _ = _load(sp)
        for f in check_set(RI, src): rows.append(dict(f, symbol=sym, round=ra))
        for i, x in enumerate(src, 1):  # figures in a transcript source's title said only by analysts / the operator (saved transcripts)
            if not is_transcript(x) or not any(r['symbol'] == sym and r['src'] == i for r in rows): continue
            A = attribute(x)
            for fig, who in (A or {}).items():
                if who and not any(rc == 'company' for _, rc, _ in who):
                    likely.append(dict(symbol=sym, round=ra, src=i, title=(x.get('title') or '')[:200], figure=fig,
                                       said_by=[f'{sp} ({rc}{", " + fm if fm else ""})' for sp, rc, fm in who]))
    ac = [r for r in rows if r['result'] == 'analyst_as_company']
    srcs = {(r['symbol'], r['src']) for r in rows}
    out = dict(date=today, rule='RULES.md 25 transcript speaker', flagged=rows, analyst_as_company=ac, title_figures_not_said_by_company=likely,
               transcript_sources_flagged=len(srcs), names_flagged=sorted({r['symbol'] for r in rows}), missing=missing,
               note='legacy sources carry no `speakers`, so every transcript source a set cites is speaker_missing until the name\'s next '
                    'update records them; analyst_as_company needs a `speakers` record to show')
    os.makedirs(f'{HERE}/data', exist_ok=True)
    json.dump(out, open(f'{HERE}/data/speaker_scan.json', 'w'), indent=1)
    return out


if __name__ == '__main__':
    a = sys.argv[1:]
    if a and a[0] == 'check' and len(a) > 1:
        RI, src, sp = _load(a[1], a[2] if len(a) > 2 else None)
        fl = check_set(RI, src)
        for f in fl:
            print(f"FAIL {f['result']} src {f['src']}: {f['title'][:100]} | {f.get('where') or ''} {f.get('figure') or ''} | {f['why']}")
        print(f"speaker: {len(fl)} transcript problem(s) (sources from {sp or a[1]})")
        sys.exit(1 if fl else 0)
    if a and a[0] == 'scan':
        o = scan()
        by = {}
        for r in o['flagged']: by.setdefault(r['symbol'], set()).add(r['src'])
        for s, ids in sorted(by.items()): print(f"{s:5} {len(ids)} transcript source(s) flagged: src {', '.join(map(str, sorted(ids)))}")
        for r in o['title_figures_not_said_by_company']:
            print(f"  NOT SAID BY THE COMPANY? {r['symbol']} src {r['src']} figure {r['figure']}: only {', '.join(r['said_by'])} | {r['title'][:120]}")
        print(f"{o['transcript_sources_flagged']} transcript sources across {len(o['names_flagged'])} names; analyst_as_company {len(o['analyst_as_company'])}")
        sys.exit(0)
    print(__doc__)
