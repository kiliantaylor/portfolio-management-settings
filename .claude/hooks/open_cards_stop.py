#!/usr/bin/env python3
"""Open decision cards: post the set once, then wait for Kilian to answer ALL of them. Never withdraw and re-post.

Kilian 2026-11-19 (SPCX): "why are you closing and reopening every card" ... "if there are multiple cards just wait
until i respond to all of them" (RULES.md 2a). The 11-18 version of this file was a Stop hook that refused any turn
whose last visible call came after open cards and asked the thread to withdraw and re-post every unanswered card. A
one-line "got it" after each tap counted as that last call, so every open card was closed and reopened after every tap.

Now a PreToolUse guard on the visible status calls (post_message, update_progress_checklist, post_widget,
set_thread_resolved). With any card still open it refuses the call when:
  a. a card went out earlier in this same turn: the checklist and context message go BEFORE the cards, and the cards are
     the last calls of the turn (so the thread reads as waiting on Kilian, Kilian 11-18);
  b. this turn was started by Kilian's tap on one card (the wake carries its cmsg id) while other cards of the set are
     still open: record the tap silently and end the turn with no_reply_needed. When the LAST open card is answered no
     card is open any more, so the thread applies all the answers in one go and replies once.
A turn started by anything else (Kilian writes a question, a coordinator note) is not refused: answer it.
It never asks for a withdraw or a re-post, and card_check.py refuses an identical re-post of a withdrawn card.
At most MAX_BLOCKS refusals per turn, so a session can never be trapped (e.g. a real blocker after a tap).
The Stop entry point is kept as a no-op so an old settings.json that still registers it does nothing.

A card is closed when close_decision ran on its id, or a later wake/user message carries its id (a tap on it).

Tests: python3 .claude/hooks/test_open_cards_stop.py
Status: python3 .claude/hooks/open_cards_stop.py status TRANSCRIPT.jsonl
"""
import json, os, re, sys, time

CARD = 'mcp__hearthbot__request_decision'
CLOSE = 'mcp__hearthbot__close_decision'
STATUS = {'mcp__hearthbot__' + n for n in ('post_message', 'update_progress_checklist', 'post_widget',
                                           'set_thread_resolved')}
CMSG = re.compile(r'cmsg_[A-Za-z0-9]{10,}')
MAX_BLOCKS = 2
MARK = 'OPEN CARDS (project rule'
STATE_DIR = os.path.join(os.environ.get('HOME') or '/tmp', '.open-cards-guard')


def _entries(path):
    try:
        with open(path, encoding='utf-8') as f:
            for line in f:
                try: yield json.loads(line)
                except ValueError: continue
    except OSError:
        return


def _text(x):
    if isinstance(x, str): return x
    if isinstance(x, list): return ' '.join(_text(b.get('text', '') if isinstance(b, dict) else b) for b in x)
    if isinstance(x, dict): return _text(x.get('text', ''))
    return ''


def _wait_stable(path, limit=2.0):
    """The CLI appends transcript lines asynchronously; wait briefly until the file stops growing."""
    end, last = time.time() + limit, -1
    while time.time() < end:
        try: size = os.path.getsize(path)
        except OSError: return
        if size == last: return
        last = size; time.sleep(0.25)


def scan(path):
    """-> dict(open_cards [(id, question, labels)], card_this_turn, tap_ids (cards this turn's prompt answers), prompts)."""
    uses, results, order = {}, {}, []   # order: ('use', id) | ('user', text)
    prompts = 0
    for e in _entries(path):
        if e.get('isSidechain'): continue
        m = e.get('message') if isinstance(e.get('message'), dict) else {}
        c = m.get('content')
        if e.get('type') == 'user':
            if isinstance(c, list) and any(isinstance(b, dict) and b.get('type') == 'tool_result' for b in c):
                for b in c:
                    if isinstance(b, dict) and b.get('type') == 'tool_result': results[b.get('tool_use_id')] = b
            elif not e.get('isMeta') and MARK not in _text(c):   # our own refusal text names the ids: not an answer
                prompts += 1; order.append(('user', _text(c)))
        elif e.get('type') == 'assistant' and isinstance(c, list):
            for b in c:
                if isinstance(b, dict) and b.get('type') == 'tool_use':
                    uses[b.get('id')] = b; order.append(('use', b.get('id')))
    ok = lambda uid: uid not in results or not results[uid].get('is_error')
    cards, closed, card_this_turn, tap_ids = {}, set(), False, set()
    for kind, v in order:
        if kind == 'user':
            card_this_turn, tap_ids = False, set()      # a new turn starts
            for cid in CMSG.findall(v):
                if cid in cards: closed.add(cid); tap_ids.add(cid)
            continue
        u = uses[v]; n = u.get('name'); inp = u.get('input') or {}
        if not ok(v): continue
        if n == CARD:
            for cid in CMSG.findall(_text((results.get(v) or {}).get('content'))):
                cards[cid] = (str(inp.get('question', '')), [str(o.get('label', '')) for o in inp.get('options') or []
                                                             if isinstance(o, dict)])
                card_this_turn = True
                break
        elif n == CLOSE and inp.get('message_id'):
            closed.add(inp['message_id'])
    open_cards = [(cid, q, l) for cid, (q, l) in cards.items() if cid not in closed]
    return dict(open_cards=open_cards, card_this_turn=card_this_turn, tap_ids=sorted(tap_ids), prompts=prompts)


def verdict(st):
    """-> None (allow) or 'posted' / 'tap' (refuse)."""
    if not st['open_cards']: return None
    if st['card_this_turn']: return 'posted'
    if st['tap_ids']: return 'tap'
    return None


def _blocks_so_far(session, prompts):
    try: s = json.load(open(os.path.join(STATE_DIR, session + '.json')))
    except (OSError, ValueError): return 0
    return s.get('blocks', 0) if s.get('prompts') == prompts else 0


def _save(session, prompts, blocks):
    try:
        os.makedirs(STATE_DIR, exist_ok=True)
        with open(os.path.join(STATE_DIR, session + '.json'), 'w') as f: json.dump({'prompts': prompts, 'blocks': blocks}, f)
    except OSError:
        pass


def reason(why, st, tool):
    short = tool.replace('mcp__hearthbot__', '')
    lines = '\n'.join(f'- {cid}: "{q[:120]}" (options: {" | ".join(l)})' for cid, q, l in st['open_cards'])
    if why == 'posted':
        do = (f'{short} refused: cards went out earlier in this turn, and a visible call after them makes the thread read '
              'as ready for review instead of waiting on Kilian (Kilian 11-18). Post the checklist and the context '
              'message BEFORE the cards next time. Do NOT withdraw and re-post the cards (Kilian 11-19, rule 2a): end '
              'the turn now with no further visible call.')
    else:
        do = (f'{short} refused: Kilian answered {", ".join(st["tap_ids"])} but other cards of the set are still open. '
              'Kilian 11-19 (rule 2a): "if there are multiple cards just wait until i respond to all of them". Note the '
              'pick, post nothing, do not withdraw or re-post any card, do not act on this answer alone; end the turn '
              'with no_reply_needed. When the last card is answered, apply all the answers in one go and reply once.')
    return (MARK + ' 2a). ' + do + '\nStill open:\n' + lines +
            '\nOnly if this turn truly needs Kilian now (a blocker), make the same call again; the guard steps aside after '
            f'{MAX_BLOCKS} refusals in a turn.')


def hook():
    try: ev = json.load(sys.stdin)
    except ValueError: return
    if ev.get('agent_id') or ev.get('hook_event_name') == 'Stop': return   # Stop: no-op (11-19)
    tool = ev.get('tool_name') or ''
    if tool not in STATUS: return
    path = ev.get('transcript_path') or ''
    if not path: return
    _wait_stable(path)
    st = scan(path)
    why = verdict(st)
    if not why: return
    session = re.sub(r'[^A-Za-z0-9_.-]', '', str(ev.get('session_id') or 'x'))[:100] or 'x'
    n = _blocks_so_far(session, st['prompts'])
    if n >= MAX_BLOCKS: return
    _save(session, st['prompts'], n + 1)
    print(json.dumps({'hookSpecificOutput': {'hookEventName': 'PreToolUse', 'permissionDecision': 'deny',
                                             'permissionDecisionReason': reason(why, st, tool)}}))


if __name__ == '__main__':
    try:
        if len(sys.argv) > 2 and sys.argv[1] == 'status':
            st = scan(sys.argv[2]); st['verdict'] = verdict(st)
            print(json.dumps(st, indent=1))
        else:
            hook()
    except Exception as e:  # never break a turn on a hook bug
        sys.stderr.write(f'open_cards_stop error: {type(e).__name__}\n')
    sys.exit(0)
