#!/usr/bin/env python3
"""Stop hook: a thread with open decision cards ends its turn as waiting on Kilian, never as idle or ready for review.

Kilian 2026-11-18: "if a thread has open cards why are they marked idle instead of waiting on me" ... "code preferably".

Why it happens: the project marks a thread by the LAST thing it showed. A turn whose last visible call is
request_decision reads as "waiting on you" (blocked); one that posts the cards and then a checklist or a reply reads as
"ready for review" (e.g. META 11-18: three cards, then a checklist update -> review_ready). A later turn that only
answers a question while cards are still open flips it the same way.

What this hook does at the end of every turn (main loop only; subagents are not checked):
  1. Reads the session transcript for cards that went out (request_decision with a cmsg_ id in its result).
  2. A card is closed when close_decision ran on its id, or a later wake/user message carries its id (a tap on it).
  3. If any card is still open and the last status-setting call (post_message, update_progress_checklist,
     post_widget, no_reply_needed, set_thread_resolved) came after the last card, the stop is refused with the list
     of open cards and what to do:
       - a card Kilian already answered (tap or words): close_decision with chose=<option index>;
       - a card still open: withdraw the old copy (close_decision without chose) and post the SAME card again
         (same question and option labels) as the last call of the turn. card_check.py lets an identical re-post of a
         withdrawn card through without a new context message, since its reasoning is already in the thread.
  At most MAX_BLOCKS refusals per turn, so a session can never be trapped.

Tests: python3 .claude/hooks/test_open_cards_stop.py
"""
import json, os, re, sys, time

CARD = 'mcp__hearthbot__request_decision'
CLOSE = 'mcp__hearthbot__close_decision'
STATUS = {'mcp__hearthbot__' + n for n in ('post_message', 'update_progress_checklist', 'post_widget',
                                           'no_reply_needed', 'set_thread_resolved')}
CMSG = re.compile(r'cmsg_[A-Za-z0-9]{10,}')
MAX_BLOCKS = 2
MARK = 'OPEN CARDS (project rule'
STATE_DIR = os.path.join(os.environ.get('HOME') or '/tmp', '.open-cards-stop')


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
    """-> (open_cards [(id, question, labels)], last_status_tool_name or None, last_status_after_last_card, prompts)."""
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
    ok = lambda uid: uid not in results or not results[uid].get('is_error')   # pending = this turn's last call
    cards, closed, last_status, last_card_pos, last_status_pos = {}, set(), None, -1, -1
    for pos, (kind, v) in enumerate(order):
        if kind == 'user':
            for cid in CMSG.findall(v):
                if cid in cards: closed.add(cid)
            continue
        u = uses[v]; n = u.get('name'); inp = u.get('input') or {}
        if not ok(v): continue
        if n == CARD:
            last_card_pos = pos
            for cid in CMSG.findall(_text((results.get(v) or {}).get('content'))):
                cards[cid] = (str(inp.get('question', '')), [str(o.get('label', '')) for o in inp.get('options') or []
                                                             if isinstance(o, dict)])
                break
        elif n == CLOSE and inp.get('message_id'):
            closed.add(inp['message_id'])
        elif n in STATUS:
            last_status, last_status_pos = n, pos
    open_cards = [(cid, q, l) for cid, (q, l) in cards.items() if cid not in closed]
    return open_cards, last_status, last_status_pos > last_card_pos, prompts


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


def reason(open_cards, last_status):
    short = last_status.replace('mcp__hearthbot__', '') if last_status else '?'
    lines = '\n'.join(f'- {cid}: "{q[:120]}" (options: {" | ".join(l)})' for cid, q, l in open_cards)
    return (MARK + ', Kilian 11-18: a thread with open cards must read as waiting on him, not idle). '
            f'Your last visible call ({short}) came after these cards, so the thread now shows as ready for review:\n'
            f'{lines}\n'
            'Fix before ending the turn:\n'
            '1. Any card Kilian already answered (a tap or in words): close_decision with message_id and chose=<option '
            'index>. If unsure, read_messages_by_id on the card first.\n'
            '2. Each card still open: close_decision with only message_id (withdraws the old copy), then post the SAME '
            'card again with request_decision (same question and option labels; card_check lets an identical re-post '
            'through). Make the request_decision calls the LAST calls of the turn: no checklist or message after them.\n'
            'Next time: update the checklist and post the message BEFORE the cards, so the cards are the last thing.')


def hook():
    try: ev = json.load(sys.stdin)
    except ValueError: return
    if ev.get('agent_id'): return
    path = ev.get('transcript_path') or ''
    if not path: return
    _wait_stable(path)
    open_cards, last_status, after, prompts = scan(path)
    if not open_cards or not after: return
    session = re.sub(r'[^A-Za-z0-9_.-]', '', str(ev.get('session_id') or 'x'))[:100] or 'x'
    n = _blocks_so_far(session, prompts)
    if n >= MAX_BLOCKS: return
    _save(session, prompts, n + 1)
    print(json.dumps({'decision': 'block', 'reason': reason(open_cards, last_status)}))


if __name__ == '__main__':
    try:
        if len(sys.argv) > 2 and sys.argv[1] == 'status':
            oc, ls, after, _ = scan(sys.argv[2])
            print(json.dumps({'open_cards': oc, 'last_status': ls, 'status_after_last_card': after}, indent=1))
        else:
            hook()
    except Exception as e:  # never break a turn on a hook bug
        sys.stderr.write(f'open_cards_stop error: {type(e).__name__}\n')
    sys.exit(0)
