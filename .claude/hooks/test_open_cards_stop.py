#!/usr/bin/env python3
"""Tests for open_cards_stop.py and card_check.py's re-post exemption (run: python3 .claude/hooks/test_open_cards_stop.py)."""
import json, os, subprocess, sys, tempfile

D = os.path.dirname(os.path.abspath(__file__))
STOP, CARDCHECK = os.path.join(D, 'open_cards_stop.py'), os.path.join(D, 'card_check.py')
HOME = tempfile.mkdtemp(prefix='ocs_home_')
VAL = tempfile.mkdtemp(prefix='ocs_val_')
N = [0]


def use(name, inp, uid):
    return {'type': 'assistant', 'message': {'content': [{'type': 'tool_use', 'id': uid, 'name': name, 'input': inp}]}}


def res(uid, text='ok', err=False):
    return {'type': 'user', 'message': {'content': [{'type': 'tool_result', 'tool_use_id': uid,
                                                     'content': [{'type': 'text', 'text': text}], 'is_error': err}]}}


def prompt(text):
    return {'type': 'user', 'message': {'content': text}}


H = 'mcp__hearthbot__'
CARD_IN = {'question': 'Choose META ad revenue path', 'options': [{'label': 'Market growth', 'consequence': 'x'},
                                                                  {'label': 'Share gain', 'consequence': 'y'}],
           'recommended': 0}


def card(uid, cid):
    return [use(H + 'request_decision', CARD_IN, uid), res(uid, json.dumps({'message_id': cid}))]


def call(name, uid, inp=None):
    return [use(H + name, inp or {'text': 'x'}, uid), res(uid)]


def write(entries):
    N[0] += 1
    p = os.path.join(HOME, f't{N[0]}.jsonl')
    with open(p, 'w') as f:
        for e in entries: f.write(json.dumps(e) + '\n')
    return p


def run_stop(entries, session=None):
    p = write(entries)
    ev = {'session_id': session or f's{N[0]}', 'transcript_path': p, 'hook_event_name': 'Stop'}
    out = subprocess.run([sys.executable, STOP], input=json.dumps(ev), capture_output=True, text=True,
                         env={**os.environ, 'HOME': HOME}).stdout.strip()
    return json.loads(out) if out else None


def run_card(entries, inp):
    p = write(entries)
    ev = {'tool_name': H + 'request_decision', 'tool_input': inp, 'transcript_path': p}
    out = subprocess.run([sys.executable, CARDCHECK], input=json.dumps(ev), capture_output=True, text=True,
                         env={**os.environ, 'CARD_CHECK_VAL_DIR': VAL}).stdout.strip()
    return json.loads(out) if out else None


C1, C2 = 'cmsg_01AAAAAAAAAAAAAAAAAAAAAAAA', 'cmsg_01BBBBBBBBBBBBBBBBBBBBBBBB'
fails = 0


def check(name, cond):
    global fails
    print(('ok   ' if cond else 'FAIL ') + name)
    fails += 0 if cond else 1


base = [prompt('<wake reason="mention">do it</wake>')] + call('post_message', 'm1')

# 1. META 11-18: cards, then a checklist -> refused, names the card
r = run_stop(base + card('c1', C1) + call('update_progress_checklist', 'k1'))
check('cards then checklist -> block', r and r.get('decision') == 'block' and C1 in r.get('reason', ''))

# 2. cards are the last call -> allowed
check('cards last -> allow', run_stop(base + call('update_progress_checklist', 'k1') + card('c1', C1)) is None)

# 3. no cards -> allowed
check('no cards -> allow', run_stop(base + call('update_progress_checklist', 'k1')) is None)

# 4. card closed with close_decision, then a reply -> allowed
check('closed card -> allow', run_stop(base + card('c1', C1) + call('close_decision', 'x1', {'message_id': C1, 'chose': 0})
                                       + call('post_message', 'm2')) is None)

# 5. card answered by a tap (later wake carries its id), reply after -> allowed
check('tapped card -> allow', run_stop(base + card('c1', C1) + [prompt(f'<wake reason="decision"><message id="{C1}">'
                                                                      'chose 0</message></wake>')]
                                       + call('post_message', 'm2')) is None)

# 6. later turn answers a question while a card from an earlier turn is open -> refused
r = run_stop(base + card('c1', C1) + [prompt('<wake reason="mention">quick question</wake>')] + call('post_message', 'm2'))
check('later turn reply with open card -> block', r and r.get('decision') == 'block')

# 7. two cards, one closed -> refusal lists only the open one
r = run_stop(base + card('c1', C1) + card('c2', C2) + call('close_decision', 'x1', {'message_id': C1}) +
             call('post_message', 'm2'))
check('lists only open card', r and C2 in r['reason'] and C1 not in r['reason'])

# 8. our own refusal text (names the ids) does not count as an answer; cap of 2 refusals per turn
ents = base + card('c1', C1) + call('post_message', 'm2')
a = run_stop(ents, 'cap')
ents2 = ents + [{'type': 'user', 'message': {'content': a['reason']}}] + call('post_message', 'm3')
b = run_stop(ents2, 'cap')
c = run_stop(ents2 + call('post_message', 'm4'), 'cap')
check('refusal text not an answer', b and b.get('decision') == 'block')
check('cap 2 refusals per turn', c is None)

# 9. subagent stop is ignored
p = write(base + card('c1', C1) + call('post_message', 'm2'))
out = subprocess.run([sys.executable, STOP], input=json.dumps({'session_id': 'sa', 'transcript_path': p, 'agent_id': 'a1'}),
                     capture_output=True, text=True, env={**os.environ, 'HOME': HOME}).stdout.strip()
check('subagent ignored', out == '')

# 10. failed card call does not count
check('errored card ignored', run_stop(base + [use(H + 'request_decision', CARD_IN, 'c1'), res('c1', 'boom', True)]
                                       + call('post_message', 'm2')) is None)

# 11. card_check: identical re-post of a withdrawn card goes through without a new context message
ents = base + card('c1', C1) + call('post_message', 'm2') + call('close_decision', 'x1', {'message_id': C1})
check('re-post of withdrawn card allowed', run_card(ents, CARD_IN) is None)

# 12. card_check: same card NOT withdrawn first -> still checked (refused: no context message since the card)
ents = base + card('c1', C1) + call('update_progress_checklist', 'k1')
r = run_card(ents, CARD_IN)
check('re-post without withdraw refused', r and r['hookSpecificOutput']['permissionDecision'] == 'deny')

# 13. card_check: a different card after a withdraw is still checked
other = dict(CARD_IN, question='Something else')
ents = base + card('c1', C1) + call('close_decision', 'x1', {'message_id': C1})
r = run_card(ents, other)
check('different card after withdraw refused', r and r['hookSpecificOutput']['permissionDecision'] == 'deny')

print(f'{13 + 1 - fails} of 14 passed' if fails else 'all 14 passed')
sys.exit(1 if fails else 0)
