#!/usr/bin/env python3
"""PreToolUse hook: Kilian's wording rules on everything he reads (replies, edits, cards, widgets, checklists, Claude Docs).

Moved from project memory into code (Kilian 2026-11-16: "put this and anything else in memory in code if you can to save
space"). Each rule below was a standing memory rule; the hook refuses the call and says what to write instead.

  research-target  "fair value" / "FV"            -> "research target (12m)"            (Kilian 10-22, 10-23)
  upside-downside  "bull"/"bear" (case, scenario)  -> "upside"/"downside"; the verb "bear on/the/out" is fine (10-30)
  times-et         a time in UTC / GMT / "Z"       -> ET, e.g. "2:25pm ET" (UTC-5 since Nov 1)             (10-29)
  no-lint          "lint"                          -> "the guide-anchor check" / "the forecast checks"      (11-09)
  no-fees          trading fees / commissions      -> never mention fees (fees are zero for sizing)
  agent-views      offering ~0x research weight, market-only sizing or dropping the agent forecasts      (11-09)
  american         British spellings (licence, labelled, annualised, ...)                                (11-09)

Not checked: URLs, Markdown link targets and `inline code` / ``` blocks (file and key names keep fair_value, bull/bear
aliases, forecast_lint.py). Quoting Kilian's own words or an outside title verbatim is the only reason for an override:
    python3 .claude/hooks/wording_check.py allow-next --why "<what is quoted and why it must stay verbatim>"
logs to valuation/data/rule_overrides.jsonl (rule wording) and lets exactly the next checked call through.
Check a draft: python3 .claude/hooks/wording_check.py draft FILE      Tests: python3 .claude/hooks/test_wording_check.py
"""
import datetime as dt, json, os, re, sys

TOOLS = re.compile(r'^mcp__hearthbot__(post_message|edit_message|request_decision|post_widget|update_progress_checklist|'
                   r'rename_thread)$|^mcp__Claude_Docs__(batch|update)$')
SKIP_KEYS = {'id', 'opId', 'ref', 'url', 'container', 'kind', 'message_id', 'thread_id', 'engine', 'version', 'object',
             'answering', 'attached_outputs', 'emoji', 'user', 'type'}
LOG = os.environ.get('CARD_CHECK_LOG', '/mnt/project-files/valuation/data/rule_overrides.jsonl')
ALLOW_CMD = re.compile(r'wording_check\.py\s+allow-next\b')
I = re.I
RULES = [
    ('research-target', re.compile(r'\bfair[ -]values?\b', I), 'say "research target (12m)", never fair value'),
    ('research-target', re.compile(r'\bFVs?\b'), 'say "research target (12m)" (or RT), never FV'),
    ('upside-downside', re.compile(r'\bbull(?:ish|s)?\b|\bbearish\b|\bbears?\b(?!\s+(?:on|in mind|the|out|with|down|fruit)\b)', I),
     'say upside/downside, never bull/bear'),
    ('times-et', re.compile(r'\b\d{1,2}:\d{2}(?::\d{2})?\s?(?:UTC|GMT|Z)\b|\b\d{1,2}(?::\d{2})?\s?(?:am|pm)?\s?(?:UTC|GMT)\b'
                            r'|\bT\d{2}:\d{2}(?::\d{2})?Z\b|\bUTC\b', I),
     'give times in ET, e.g. "2:25pm ET" (UTC-5 since Nov 1)'),
    ('no-lint', re.compile(r'\blint(?:s|ed|ing|er)?\b', I), 'never say "lint": say "the guide-anchor check", "the forecast '
     'checks", "flag", "logged exception"'),
    ('no-fees', re.compile(r'\b(?:trading|transaction|broker(?:age)?|per[- ]trade|round[- ]trip|execution)\s+(?:fees?|costs?|commissions?)\b'
                           r'|\bfees?\b(?=[^.\n]{0,40}\b(?:trade|trades|trading|sizing|order|orders|fill|fills|broker)\b)'
                           r'|\b(?:trade|trades|trading|sizing|order|orders|fill|fills|broker)\b[^.\n]{0,40}\bfees?\b'
                           r'|\bcommissions?\b(?=[^.\n]{0,40}\b(?:trade|trades|trading|broker|order|fill)\b)', I),
     'never mention fees (they are zero for sizing)'),
    ('agent-views', re.compile(r'\b0(?:\.0+)?\s?x\s+(?:research|alpha|conviction)\b|\bresearch weight\s+(?:of\s+)?(?:~\s?)?0(?:\.0+)?\b(?!\.\d)'
                               r'|\b(?:pure|market)[- ]only\s+(?:sizing|market|weights?)\b|\bpure[- ]market\s+(?:sizing|option|weights?)\b'
                               r'|\b(?:drop|cut|remove|strip|without|zero)\s+(?:out\s+)?(?:the\s+)?agents?(?:\'s|s\')?\s+'
                               r'(?:forecasts?|views?|legs?|research)\b', I),
     'never suggest cutting out the agent forecasts (no ~0x research weight / market-only sizing options); keep research '
     'weight inside 0.5-1x'),
    ('american', re.compile(r'\b(?:licences?|labell(?:ed|ing)|modell(?:ed|ing)|annualis(?:e|ed|es|ing)|'
                            r'utilis(?:e|ed|es|ing|ation)|organis(?:e|ed|es|ing|ation)|analys(?:e|ed|ing)|'
                            r'favour\w*|colour\w*|behaviour\w*|centre(?:d|s)?|neighbour\w*|recognis\w+|'
                            r'optimis(?:e|ed|es|ing|ation)|normalis\w+|capitalis\w+|amortis\w+|programme|'
                            r'defence|offence)\b', I),
     'use American spelling (license, labeled, modeled, annualized, utilization, center, favor, analyze ...)'),
]


def clean(t):
    t = re.sub(r'```.*?```', ' ', t, flags=re.S)
    t = re.sub(r'`[^`\n]*`', ' ', t)
    t = re.sub(r'\]\([^)\s]*\)', ']', t)
    t = re.sub(r'https?://\S+|/mnt/\S+|\b[\w.-]+\.(?:py|json|md|csv|html|jsonl|xlsx)\b', ' ', t)
    t = re.sub(r'\b\w*_\w*\b', ' ', t)  # identifiers such as fair_value, STD_MODEL_MEAN_CENTRE
    return t


def strings(x, key=None):
    if key in SKIP_KEYS: return
    if isinstance(x, str): yield x
    elif isinstance(x, dict):
        for k, v in x.items(): yield from strings(v, k)
    elif isinstance(x, list):
        for v in x: yield from strings(v, key)


def problems(text):
    t = clean(text or '')
    P, seen = [], set()
    for rule, rx, fix in RULES:
        hits = sorted({m.group(0).strip() for m in rx.finditer(t)})
        if hits and (rule, fix) not in seen:
            seen.add((rule, fix)); P.append(f'{rule}: found {", ".join(repr(h) for h in hits[:5])}; {fix}')
    return P


def _entries(path):
    try:
        for line in open(path, encoding='utf-8'):
            try: yield json.loads(line)
            except ValueError: continue
    except OSError: return


def allowed(path):
    """True when an allow-next ran (and succeeded) after the last call this hook let through."""
    uses, results = [], {}
    for e in _entries(path):
        c = (e.get('message') or {}).get('content')
        for b in c if isinstance(c, list) else []:
            if b.get('type') == 'tool_use': uses.append(b)
            elif b.get('type') == 'tool_result': results[b.get('tool_use_id')] = b
    ok = lambda u: u.get('id') in results and not results[u['id']].get('is_error')
    last = max((k for k, u in enumerate(uses) if TOOLS.match(u.get('name') or '') and ok(u)), default=-1)
    return any(u.get('name') == 'Bash' and ALLOW_CMD.search((u.get('input') or {}).get('command', '')) and ok(u)
               for u in uses[last + 1:])


def hook():
    try: ev = json.load(sys.stdin)
    except ValueError: return
    if not TOOLS.match(ev.get('tool_name') or ''): return
    P = problems('\n'.join(strings(ev.get('tool_input') or {})))
    if not P or allowed(ev.get('transcript_path') or ''): return
    print(json.dumps({'hookSpecificOutput': {'hookEventName': 'PreToolUse', 'permissionDecision': 'deny',
                                             'permissionDecisionReason':
        'REFUSED (Kilian\'s wording rules, moved from project memory into code 11-16):\n- ' + '\n- '.join(P) +
        '\nReword and send again. Only to quote Kilian or an outside title verbatim: python3 .claude/hooks/wording_check.py '
        'allow-next --why "<what is quoted>"'}}))


def main(a):
    if not a: return hook()
    if a[0] == 'allow-next':
        why = a[a.index('--why') + 1] if '--why' in a and a.index('--why') + 1 < len(a) else ''
        if len(why.strip()) < 20: sys.exit('REFUSED: --why must say what is quoted verbatim and why (20+ chars).')
        row = dict(utc=dt.datetime.now(dt.timezone.utc).strftime('%Y-%m-%dT%H:%MZ'), rule='wording', why=why,
                   session=os.environ.get('CLAUDE_CODE_REMOTE_SESSION_ID', ''))
        try:
            with open(LOG, 'a') as f: f.write(json.dumps(row) + '\n')
        except OSError as e: sys.exit(f'REFUSED: could not log the override to {LOG} ({e}).')
        return print('Logged. The next checked message, card or doc edit goes through without the wording check.')
    if a[0] == 'draft' and len(a) > 1:
        P = problems(open(a[1], encoding='utf-8').read())
        print('OK' if not P else 'PROBLEMS:\n- ' + '\n- '.join(P)); sys.exit(1 if P else 0)
    sys.exit(__doc__)


if __name__ == '__main__':
    main(sys.argv[1:])
