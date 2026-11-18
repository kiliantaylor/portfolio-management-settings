#!/usr/bin/env python3
"""PreToolUse hook on mcp__hearthbot__request_decision: no decision card without its reasoning laid out first.

Rule (Kilian 2026-11-10, "when you give me cards relevant to valuations i need you to give full context as well as the
sources + reasoning supporting each"; 2026-11-13 "enforce the every card needs reasoning laid out rule in code"):
every decision card follows a message in the thread that gives the full context, and for EACH option its reasoning and
linked sources (and, for a valuation card, its engine-sized effect). A card holds one short line per option, so the
message is where the reasoning lives.

What this hook refuses (the card is never posted; the session gets the list of problems):
  1. no post_message since the last card that went out (the context message must come FIRST, in an earlier step, not
     in the same parallel batch as the card),
  2. a context part (text before the first option) shorter than CTX_MIN characters,
  3. an option of the card with no section of its own in the message. A section starts on its own line with the
     option's label, e.g. "**Keep 0.33**: ...", "1. Keep 0.33 - ...", "### Option A: Keep 0.33", "- Keep 0.33: ...",
  4. a section whose reasoning (prose without links) is shorter than REASON_MIN characters,
  5. a section with no source link (http/https URL, or a Markdown link to a project file). Only when no outside source
     can exist (e.g. a pure workflow choice) may the section say "No source: <why>" with a reason of NOSRC_MIN+ chars,
  6. on a valuation card (VALUATION words in the card or message), a section with no sized number (a % or a price).
The text is checked for form only; whether the reasoning is right and the links show what it says is still on the
session (and the gate text agent for model cards).

GATE RULE (Kilian 2026-11-15, SPCX: five cards went out while the citation gate ran, then the gate failed an invented
claim, wrong numbers and missing options in them): no card while the round it is about has a citation gate that is not
clean. The rounds a session works on are found in its transcript (runs/DATE/SYM paths or "script.py ... DATE SYM", and
driver_models/SYM[/quarterly] paths or "drv.py/qdrv.py ... SYM"). Checked: the newest round per symbol named in the card
or its context message (if the card names none and is a valuation card: the round touched last).
  round runs/DATE/SYM (has supervisor.json): clean = citation_audit.json with clean true and no open_failures, written
     after gate/merged.json and gate/checklist.json (a later merge or re-prep means the gate is running again);
  driver set driver_models/SYM[/quarterly]: clean = gate.json with clean true, written after set.json.
Exception (RULES.md 24, Kilian 2026-11-18): after gate round 2, when every row still open is a must-card row
(gate/must_card.json: unsupported / trend_only / back_solved), the round counts as clean for cards: those rows are carded now.
Open (no gate yet or still running) or failed -> refused. A card that is not gated (e.g. a driver-set approval before
the build) needs a logged override:
    python3 .claude/hooks/card_check.py allow-ungated --why "<what the card is and why no gate applies>"
which lets exactly the next card past the gate check (the reasoning check still applies).
Status of a round: python3 .claude/hooks/card_check.py gate-status DATE SYM | drv SYM [quarterly]

Override (only when Kilian asked in words for a bare card): run
    python3 .claude/hooks/card_check.py allow-next --why "<Kilian's words and when>"
in this session; it logs to valuation/data/rule_overrides.jsonl and lets exactly the next card through.

Test a draft before posting:
    python3 .claude/hooks/card_check.py draft MSG.md "Label 1" "Label 2" [--valuation]
Model cards from supervisor.json: `python3 valuation/judgment_check.py cards D SYM` prints a message in this form.
Tests: python3 .claude/hooks/test_card_check.py
"""
import datetime as dt, json, os, re, sys

CARD_TOOL = 'mcp__hearthbot__request_decision'
MSG_TOOLS = ('mcp__hearthbot__post_message', 'mcp__hearthbot__edit_message')
CTX_MIN = 120      # same floor as judgment_check.py CTX_MIN
REASON_MIN = 80    # prose per option, links stripped
NOSRC_MIN = 30     # reason after "No source:"
VALUATION = re.compile(r'research target|price target|\btarget\b|valuation|driver|multiple|forecast|\beps\b|margin|'
                       r'revenue|capex|guid(?:e|ance)|exit|weight|sizing|position|\bRT\b', re.I)
SIZED = re.compile(r'[+\-−]?\d+(?:\.\d+)?\s?%|\$\s?\d|\b\d+\.\d+\b|\bbp\b|\b\d+\s?bps?\b', re.I)
URL = re.compile(r'https?://[^\s)\]>]+')
MDLINK = re.compile(r'\[[^\]]+\]\(([^)\s]+)\)')
NOSRC = re.compile(r'no (?:outside )?sources?\s*[:\-–—]\s*(.+)', re.I)
ALLOW_CMD = re.compile(r'card_check\.py\s+allow-next\b')
UNGATED_CMD = re.compile(r'card_check\.py\s+allow-ungated\b')
VAL = os.environ.get('CARD_CHECK_VAL_DIR', '/mnt/project-files/valuation')
SYM = r'[A-Z][A-Z0-9]{0,6}(?:\.[A-Z])?'
ROUND_REF = re.compile(r'\b(20\d\d-\d\d-\d\d)(?:/|[ \t]+)(' + SYM + r')\b')
DRV_REF = re.compile(r'driver_models/(' + SYM + r')(/quarterly)?\b|\b(q?)drv\.py[ \t]+[\w-]+[ \t]+(' + SYM + r')\b')
LOG = os.environ.get('CARD_CHECK_LOG', '/mnt/project-files/valuation/data/rule_overrides.jsonl')
# a line that opens an option section: optional quote/bullet/number/heading/bold/"Option X:", then the label
PREFIX = (r'^[ \t>]*(?:[-*+•]|\d+[.)])?[ \t]*(?:#{1,6}[ \t]*)?(?:\*\*|__|\*)?[ \t]*'
          r'(?:option[ \t]*\w{0,2}[ \t]*[:.)\-–—]?[ \t]*(?:\*\*|__)?[ \t]*)?[(\[\"“\']?[ \t]*')


def _label_re(label):
    words = re.findall(r'\w+|[^\w\s]', label)
    words = [re.escape(w) for w in words if re.match(r'\w', w)] or [re.escape(label.strip())]
    return re.compile(PREFIX + r'[\W_]*'.join(words), re.I | re.M)


def problems(text, labels, valuation=None):
    """Form problems of a context message for a card with these option labels ([] = fine)."""
    text = text or ''
    P = []
    starts = []
    for i, lab in enumerate(labels):
        m = _label_re(lab).search(text)
        if not m:
            P.append(f'option {i + 1} "{lab}" has no section of its own: start a line with the label, '
                     f'e.g. "**{lab}**: <reasoning> <source link>"')
        else:
            starts.append((m.start(), m.end(), i))
    starts.sort()
    first = starts[0][0] if starts else len(text)
    ctx = URL.sub('', text[:first]).strip()
    if len(ctx) < CTX_MIN:
        P.append(f'context before the first option is {len(ctx)} chars (need {CTX_MIN}+): say what the item is, how it '
                 f'enters the model or the work, what is booked now and on what evidence, and why it is a call')
    if valuation is None:
        valuation = bool(VALUATION.search(text))
    for k, (s, e, i) in enumerate(starts):
        end = starts[k + 1][0] if k + 1 < len(starts) else len(text)
        sec, lab = text[e:end], labels[i]
        prose = MDLINK.sub(lambda m: m.group(0).split('](')[0], sec)
        prose = re.sub(r'\s+', ' ', URL.sub('', prose)).strip()
        nos = NOSRC.search(sec)
        if len(prose) < REASON_MIN:
            P.append(f'option {i + 1} "{lab}": reasoning is {len(prose)} chars (need {REASON_MIN}+): why for and against it')
        if not (URL.search(sec) or MDLINK.search(sec)):
            if not nos or len(nos.group(1).strip()) < NOSRC_MIN:
                P.append(f'option {i + 1} "{lab}": no linked source (a URL, or "No source: <why none can exist>")')
        if valuation and not SIZED.search(sec):
            P.append(f'option {i + 1} "{lab}": valuation card but no sized number (its research target (12m) or % effect)')
    return P


# ---------- transcript ----------

def _entries(path):
    try:
        for line in open(path, encoding='utf-8'):
            try: yield json.loads(line)
            except ValueError: continue
    except OSError:
        return


def _blocks(e):
    c = (e.get('message') or {}).get('content')
    return c if isinstance(c, list) else []


def _why(cmd):
    m = re.search(r'--why[ =](["\'])(.+?)\1', cmd, re.S)
    return m.group(2) if m else ''


def scan(path):
    """Since the last card that went out: the last context message text and whether an allow-next ran (and its why)."""
    uses, results = [], {}
    for e in _entries(path):
        for b in _blocks(e):
            if b.get('type') == 'tool_use':
                uses.append(b)
            elif b.get('type') == 'tool_result':
                results[b.get('tool_use_id')] = b
    ok = lambda u: u.get('id') in results and not results[u['id']].get('is_error')
    # the card being checked has no result yet; earlier cards count only if they went out
    last_card = max((k for k, u in enumerate(uses) if u.get('name') == CARD_TOOL and ok(u)), default=-1)
    msg, allow = None, None
    for u in uses[last_card + 1:]:
        n, inp = u.get('name'), u.get('input') or {}
        if n in MSG_TOOLS and (u.get('id') not in results or ok(u)) and inp.get('text'):
            msg = inp['text']
        elif n == 'Bash' and ALLOW_CMD.search(inp.get('command', '')) and ok(u):
            allow = _why(inp.get('command', ''))
    return msg, allow


def scan_gate(path):
    """Rounds this session touched, in order of last mention: [('run', DATE, SYM) | ('drv', SYM, quarterly)], and the
    why of an allow-ungated that ran since the last card that went out (None if none)."""
    uses, results = [], {}
    for e in _entries(path):
        for b in _blocks(e):
            if b.get('type') == 'tool_use': uses.append(b)
            elif b.get('type') == 'tool_result': results[b.get('tool_use_id')] = b
    ok = lambda u: u.get('id') in results and not results[u['id']].get('is_error')
    last_card = max((k for k, u in enumerate(uses) if u.get('name') == CARD_TOOL and ok(u)), default=-1)
    seen, allow = {}, None
    for k, u in enumerate(uses):
        if u.get('name') == CARD_TOOL: continue
        t = json.dumps(u.get('input') or {}).replace('\\/', '/')
        for m in ROUND_REF.finditer(t):
            key = ('run', m.group(1), m.group(2))
            if os.path.exists(f'{VAL}/runs/{key[1]}/{key[2]}/supervisor.json'):
                seen.pop(key, None); seen[key] = k
        for m in DRV_REF.finditer(t):
            key = ('drv', m.group(1), bool(m.group(2))) if m.group(1) else ('drv', m.group(4), m.group(3) == 'q')
            if os.path.isdir(f'{VAL}/driver_models/{key[1]}'):
                seen.pop(key, None); seen[key] = k
        if k > last_card and u.get('name') == 'Bash' and UNGATED_CMD.search((u.get('input') or {}).get('command', '')) and ok(u):
            allow = _why(u['input']['command'])
    return list(seen), allow


def _mt(p):
    try: return os.path.getmtime(p)
    except OSError: return None


def _load(p):
    try: return json.load(open(p))
    except (OSError, ValueError): return None


def gate_status(key):
    """(state, detail) for one round: state is 'clean', 'open' or 'failed'."""
    if key[0] == 'run':
        _, d, s = key
        rd = f'{VAL}/runs/{d}/{s}'; gd = f'{rd}/gate'; name = f'runs/{d}/{s}'
        a, ta = _load(f'{rd}/citation_audit.json'), _mt(f'{rd}/citation_audit.json')
        merged, tm, tc = _load(f'{gd}/merged.json'), _mt(f'{gd}/merged.json'), _mt(f'{gd}/checklist.json')
        if a and a.get('clean') is True and not a.get('open_failures') and all(t is None or ta >= t for t in (tm, tc)):
            return 'clean', f'{name}: citation_audit.json clean ({a.get("checked")} checked)'
        # RULES.md 24 (Kilian 2026-11-18): after gate round 2, unsupported / trend_only / back_solved rows are must-card items
        # (gate/must_card.json). They are the blockers, so a card for them goes out now instead of waiting for a clean gate.
        mc = _load(f'{gd}/must_card.json'); MUST = ('unsupported', 'trend_only', 'back_solved')
        if mc and mc.get('items') and merged is not None and (tc is None or tm >= tc):
            if a and ta and (tm is None or ta >= tm): left = [f for f in (a.get('open_failures') or []) if f.get('status') != 'accepted_by_kilian']
            else: left = merged.get('failures') or []
            if left and all(f.get('result') in MUST for f in left):
                return 'clean', f'{name}: only must-card rows open ({len(left)}, gate round {mc.get("round")}; RULES.md 24): card them now'
        if a and ta and (a.get('clean') is False or a.get('open_failures')) and (tm is None or ta >= tm):
            return 'failed', f'{name}: citation_audit.json not clean ({len(a.get("open_failures") or a.get("failures") or [])} open failures)'
        if merged is not None and (ta is None or tm > ta):
            nf = len(merged.get('failures') or [])
            if nf: return 'failed', (f'{name}: gate merged with {nf} failures and no clean citation_audit.json since '
                                     f'(fix agent on gate/brief_fix.md, then a clean citation_audit.json)')
            return 'open', f'{name}: gate merged clean but citation_audit.json was not written'
        if tc is not None:
            return 'open', f'{name}: gate is running (gate/checklist.json prepared, not merged yet)'
        return 'open', f'{name}: no citation gate has run on this round'
    _, s, q = key
    base = f'{VAL}/driver_models/{s}' + ('/quarterly' if q else ''); name = base.split('valuation/')[-1]
    g, tg, ts = _load(f'{base}/gate.json'), _mt(f'{base}/gate.json'), _mt(f'{base}/set.json')
    if ts is None and g is None:
        return 'clean', f'{name}: no set.json (nothing to gate)'
    if g is None:
        return 'open', f'{name}: set.json has no gate.json (driver-set gate not run)'
    if ts is not None and ts > tg:
        return 'open', f'{name}: set.json changed after gate.json (re-run the driver-set gate)'
    if g.get('clean') is not True:
        return 'failed', f'{name}: gate.json clean={g.get("clean")}, open: {"; ".join(map(str, g.get("open") or []))[:300]}'
    return 'clean', f'{name}: gate.json clean'


def gate_problems(rounds, text, valuation):
    """Rounds the card is about whose gate is not clean -> list of details ([] = fine)."""
    syms = {k[2] if k[0] == 'run' else k[1] for k in rounds}
    named = {s for s in syms if re.search(r'(?<![A-Za-z0-9])' + re.escape(s) + r'(?![A-Za-z0-9])', text)}
    pick = [k for k in rounds if (k[2] if k[0] == 'run' else k[1]) in named]
    if not pick and valuation and rounds:
        last = rounds[-1]; ls = last[2] if last[0] == 'run' else last[1]
        pick = [k for k in rounds if (k[2] if k[0] == 'run' else k[1]) == ls]
    newest = {}
    for k in pick:  # newest round per symbol; driver sets checked as well
        if k[0] == 'run':
            if k[1] >= newest.get(k[2], ('run', '', ''))[1]: newest[k[2]] = k
    check = list(newest.values()) + [k for k in pick if k[0] == 'drv']
    return [d for st, d in map(gate_status, check) if st != 'clean']


CLOSE_TOOL = 'mcp__hearthbot__close_decision'
CMSG = re.compile(r'cmsg_[A-Za-z0-9]{10,}')


def _card_key(inp):
    return (str(inp.get('question', '')).strip(),
            tuple(str(o.get('label', '')).strip() for o in inp.get('options') or [] if isinstance(o, dict)))


def is_repost(path, inp):
    """True when this card repeats (same question and labels) a card that already went out and whose old copy was
    withdrawn with close_decision (no `chose`): open_cards_stop.py asks for that re-post so the thread ends as waiting
    on Kilian. The reasoning and gate checks ran on the original."""
    uses, results = [], {}
    for e in _entries(path):
        for b in _blocks(e):
            if b.get('type') == 'tool_use': uses.append(b)
            elif b.get('type') == 'tool_result': results[b.get('tool_use_id')] = b
    ok = lambda u: u.get('id') in results and not results[u['id']].get('is_error')
    key, ids, withdrawn = _card_key(inp), set(), set()
    for u in uses:
        i = u.get('input') or {}
        if u.get('name') == CARD_TOOL and ok(u) and _card_key(i) == key:
            c = results[u['id']].get('content')
            ids.update(CMSG.findall(c if isinstance(c, str) else json.dumps(c)))
        elif u.get('name') == CLOSE_TOOL and ok(u) and i.get('chose') is None and i.get('message_id'):
            withdrawn.add(i['message_id'])
    return bool(ids & withdrawn)


def deny(reason):
    print(json.dumps({'hookSpecificOutput': {'hookEventName': 'PreToolUse', 'permissionDecision': 'deny',
                                             'permissionDecisionReason': reason}}))


def hook():
    try: ev = json.load(sys.stdin)
    except ValueError: return
    if ev.get('tool_name') != CARD_TOOL: return
    inp = ev.get('tool_input') or {}
    labels = [str(o.get('label', '')) for o in inp.get('options') or [] if isinstance(o, dict)]
    if is_repost(ev.get('transcript_path') or '', inp):
        return  # identical re-post of a withdrawn open card (open_cards_stop.py); checked when first posted
    msg, allow = scan(ev.get('transcript_path') or '')
    rounds, ungated = scan_gate(ev.get('transcript_path') or '')
    card_text = ' '.join([str(inp.get('question', '')), str(inp.get('reason', ''))] + labels +
                         [str(o.get('consequence', '')) for o in inp.get('options') or [] if isinstance(o, dict)])
    G = [] if ungated is not None and len(ungated.strip()) >= 20 else \
        gate_problems(rounds, card_text + ' ' + (msg or ''), bool(VALUATION.search(card_text + ' ' + (msg or ''))))
    gate_txt = ('' if not G else
                'CARD REFUSED (project rule, Kilian 11-15: no card while its round\'s citation gate is open or flagged). '
                'Not clean:\n- ' + '\n- '.join(G) + '\nFix: wait for the gate, run the fix agent until citation_audit.json '
                '(or driver_models gate.json) is clean, re-check the card\'s numbers and options against the fixed set, '
                'then post the context message and the card. Only for a card no gate covers (e.g. a driver-set approval '
                'before the build): python3 .claude/hooks/card_check.py allow-ungated --why "<what the card is, why no gate>"\n')
    if allow is not None and len(allow.strip()) >= 20:
        if gate_txt: deny(gate_txt)
        return  # logged when allow-next ran
    head = ('CARD REFUSED (project rule, Kilian 11-10/11-13: every card comes with its reasoning laid out). ')
    how = ('\nFix: post a message FIRST (its own step, not in the same batch as the card) with the context, then one '
           'section per option starting on its own line with the option label, each with its reasoning, linked '
           'sources and, for a valuation card, its sized research target (12m) / % effect. Then post the card again. '
           'Check a draft with: python3 .claude/hooks/card_check.py draft MSG.md "' + '" "'.join(labels) + '"')
    if msg is None:
        deny(gate_txt + head + 'No context message was posted since the last card.' + how)
        return
    P = problems(msg, labels, valuation=bool(VALUATION.search(card_text + ' ' + msg)))
    if P:
        deny(gate_txt + head + 'The last message posted has these problems:\n- ' + '\n- '.join(P) + how)
    elif gate_txt:
        deny(gate_txt)


# ---------- CLI ----------

def allow_next(why, rule='card-reasoning'):
    if len(why.strip()) < 20:
        sys.exit('REFUSED: --why must say ' + ('what Kilian asked for and when' if rule == 'card-reasoning' else
                                              'what the card is and why no citation gate covers it') + ' (20+ chars).')
    row = dict(utc=dt.datetime.now(dt.timezone.utc).strftime('%Y-%m-%dT%H:%MZ'), rule=rule, why=why,
               session=os.environ.get('CLAUDE_CODE_REMOTE_SESSION_ID', ''))
    try:
        with open(LOG, 'a') as f: f.write(json.dumps(row) + '\n')
    except OSError as e:
        sys.exit(f'REFUSED: could not log the override to {LOG} ({e}).')
    print('Logged. The next decision card in this session goes through without the ' +
          ('reasoning check' if rule == 'card-reasoning' else 'citation-gate check') + '; the one after is checked again.')


def main(a):
    if not a:
        return hook()
    if a[0] == 'allow-next':
        why = a[a.index('--why') + 1] if '--why' in a and a.index('--why') + 1 < len(a) else ''
        return allow_next(why)
    if a[0] == 'allow-ungated':
        why = a[a.index('--why') + 1] if '--why' in a and a.index('--why') + 1 < len(a) else ''
        return allow_next(why, 'card-gate')
    if a[0] == 'gate-status' and len(a) >= 3:
        key = ('drv', a[2].upper(), len(a) > 3 and a[3] == 'quarterly') if a[1] == 'drv' else ('run', a[1], a[2].upper())
        st, d = gate_status(key); print(st.upper() + ': ' + d); sys.exit(0 if st == 'clean' else 1)
    if a[0] == 'draft' and len(a) >= 3:
        val = '--valuation' in a
        labels = [x for x in a[2:] if x != '--valuation']
        P = problems(open(a[1], encoding='utf-8').read(), labels, valuation=True if val else None)
        print('OK: the card can go out after this message.' if not P else 'PROBLEMS:\n- ' + '\n- '.join(P))
        sys.exit(1 if P else 0)
    sys.exit(__doc__)


if __name__ == '__main__':
    main(sys.argv[1:])
