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
            m = re.search(r'--why[ =](["\'])(.+?)\1', inp.get('command', ''), re.S)
            allow = m.group(2) if m else ''
    return msg, allow


def deny(reason):
    print(json.dumps({'hookSpecificOutput': {'hookEventName': 'PreToolUse', 'permissionDecision': 'deny',
                                             'permissionDecisionReason': reason}}))


def hook():
    try: ev = json.load(sys.stdin)
    except ValueError: return
    if ev.get('tool_name') != CARD_TOOL: return
    inp = ev.get('tool_input') or {}
    labels = [str(o.get('label', '')) for o in inp.get('options') or [] if isinstance(o, dict)]
    msg, allow = scan(ev.get('transcript_path') or '')
    if allow is not None and len(allow.strip()) >= 20:
        return  # logged when allow-next ran
    head = ('CARD REFUSED (project rule, Kilian 11-10/11-13: every card comes with its reasoning laid out). ')
    how = ('\nFix: post a message FIRST (its own step, not in the same batch as the card) with the context, then one '
           'section per option starting on its own line with the option label, each with its reasoning, linked '
           'sources and, for a valuation card, its sized research target (12m) / % effect. Then post the card again. '
           'Check a draft with: python3 .claude/hooks/card_check.py draft MSG.md "' + '" "'.join(labels) + '"')
    if msg is None:
        deny(head + 'No context message was posted since the last card.' + how)
        return
    card_text = ' '.join([str(inp.get('question', '')), str(inp.get('reason', ''))] +
                         [str(o.get('consequence', '')) for o in inp.get('options') or [] if isinstance(o, dict)])
    P = problems(msg, labels, valuation=bool(VALUATION.search(card_text + ' ' + msg)))
    if P:
        deny(head + 'The last message posted has these problems:\n- ' + '\n- '.join(P) + how)


# ---------- CLI ----------

def allow_next(why):
    if len(why.strip()) < 20:
        sys.exit('REFUSED: --why must quote what Kilian asked for and when (20+ chars).')
    row = dict(utc=dt.datetime.now(dt.timezone.utc).strftime('%Y-%m-%dT%H:%MZ'), rule='card-reasoning', why=why,
               session=os.environ.get('CLAUDE_CODE_REMOTE_SESSION_ID', ''))
    try:
        with open(LOG, 'a') as f: f.write(json.dumps(row) + '\n')
    except OSError as e:
        sys.exit(f'REFUSED: could not log the override to {LOG} ({e}).')
    print('Logged. The next decision card in this session goes through without the reasoning check; the one after is checked again.')


def main(a):
    if not a:
        return hook()
    if a[0] == 'allow-next':
        why = a[a.index('--why') + 1] if '--why' in a and a.index('--why') + 1 < len(a) else ''
        return allow_next(why)
    if a[0] == 'draft' and len(a) >= 3:
        val = '--valuation' in a
        labels = [x for x in a[2:] if x != '--valuation']
        P = problems(open(a[1], encoding='utf-8').read(), labels, valuation=True if val else None)
        print('OK: the card can go out after this message.' if not P else 'PROBLEMS:\n- ' + '\n- '.join(P))
        sys.exit(1 if P else 0)
    sys.exit(__doc__)


if __name__ == '__main__':
    main(sys.argv[1:])
