#!/usr/bin/env python3
"""Tests for open_cards_stop.py and card_check.py's re-post refusal (rule 2a) (run: python3 .claude/hooks/test_open_cards_stop.py)."""
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


def run_guard(entries, tool='post_message', session=None, event='PreToolUse'):
    p = write(entries)
    ev = {'session_id': session or f's{N[0]}', 'transcript_path': p, 'hook_event_name': event,
          'tool_name': H + tool, 'tool_input': {'text': 'x'}}
    out = subprocess.run([sys.executable, STOP], input=json.dumps(ev), capture_output=True, text=True,
                         env={**os.environ, 'HOME': HOME}).stdout.strip()
    return json.loads(out) if out else None


def denied(r):
    return bool(r) and r['hookSpecificOutput']['permissionDecision'] == 'deny'


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
tap = lambda cid: [prompt(f'<wake reason="decision"><message id="{cid}">chose 0</message></wake>')]
C3 = 'cmsg_01CCCCCCCCCCCCCCCCCCCCCCCC'
three = base + card('c1', C1) + card('c2', C2) + card('c3', C3)

# 1. SPCX 11-19: three cards out, Kilian taps one -> a "got it" reply is refused (wait for all), never asks for a re-post
r = run_guard(three + tap(C1))
check('tap with others open -> ack refused', denied(r) and C2 in r['hookSpecificOutput']['permissionDecisionReason'])
txt = r['hookSpecificOutput']['permissionDecisionReason'] if r else ''
check('refusal says do not re-post', 'do not withdraw or re-post' in txt and 'no_reply_needed' in txt)
check('checklist also refused on a tap turn', denied(run_guard(three + tap(C1), 'update_progress_checklist')))

# 2. second tap, one card still open -> still refused
check('second tap, one open -> refused', denied(run_guard(three + tap(C1) + call('no_reply_needed', 'n1') + tap(C2))))

# 3. last card tapped -> no card open -> reply allowed (apply all in one go)
check('last tap -> allowed', run_guard(three + tap(C1) + tap(C2) + tap(C3)) is None)

# 4. cards posted this turn, then a checklist -> refused, and it says not to re-post
r = run_guard(base + card('c1', C1), 'update_progress_checklist')
check('cards then checklist same turn -> refused', denied(r) and 'Do NOT withdraw and re-post' in
      r['hookSpecificOutput']['permissionDecisionReason'])

# 5. checklist and message before the cards -> allowed
check('status before cards -> allowed', run_guard(base) is None)

# 6. later turn: Kilian asks a question while cards are open -> answer allowed
check('question while cards open -> allowed', run_guard(base + card('c1', C1) + [prompt('<wake>quick question</wake>')]) is None)

# 7. no cards -> allowed; closed card -> allowed
check('no cards -> allowed', run_guard(base) is None)
check('closed card -> allowed', run_guard(base + card('c1', C1) + call('close_decision', 'x1', {'message_id': C1, 'chose': 0})
                                          + [prompt('<wake>next</wake>')]) is None)

# 8. tools the guard does not cover (no_reply_needed, close_decision) are never refused
check('no_reply_needed allowed', run_guard(three + tap(C1), 'no_reply_needed') is None)

# 9. cap: 2 refusals per turn, then it steps aside; refusal text is not counted as a tap or a new turn
ents = three + tap(C1)
a1 = run_guard(ents, session='cap')
ents2 = ents + [{'type': 'user', 'message': {'content': a1['hookSpecificOutput']['permissionDecisionReason']}}]
a2 = run_guard(ents2, session='cap')
a3 = run_guard(ents2, session='cap')
check('refusal text not a new turn', denied(a2))
check('cap 2 refusals per turn', a3 is None)

# 10. Stop event and subagents are ignored (no withdraw/re-post demand at the end of a turn any more)
check('Stop is a no-op', run_guard(base + card('c1', C1) + call('post_message', 'm2'), event='Stop') is None)
p = write(three + tap(C1))
out = subprocess.run([sys.executable, STOP], input=json.dumps({'session_id': 'sa', 'transcript_path': p, 'agent_id': 'a1',
                     'tool_name': H + 'post_message'}), capture_output=True, text=True, env={**os.environ, 'HOME': HOME}).stdout.strip()
check('subagent ignored', out == '')

# 10b. failed card call does not count
check('errored card ignored', run_guard(base + [use(H + 'request_decision', CARD_IN, 'c1'), res('c1', 'boom', True)]) is None)

# 11. card_check: identical re-post of a withdrawn card is refused (rule 2a, Kilian 11-19)
ents = base + card('c1', C1) + call('post_message', 'm2') + call('close_decision', 'x1', {'message_id': C1})
r = run_card(ents, CARD_IN)
check('re-post of withdrawn card refused', r and 'rule 2a' in r['hookSpecificOutput']['permissionDecisionReason'])

# 12. card_check: same card NOT withdrawn first -> still checked (refused: no context message since the card)
ents = base + card('c1', C1) + call('update_progress_checklist', 'k1')
r = run_card(ents, CARD_IN)
check('re-post without withdraw refused', r and r['hookSpecificOutput']['permissionDecision'] == 'deny')

# 13. card_check: a different card after a withdraw is still checked
other = dict(CARD_IN, question='Something else')
ents = base + card('c1', C1) + call('close_decision', 'x1', {'message_id': C1})
r = run_card(ents, other)
check('different card after withdraw refused', r and r['hookSpecificOutput']['permissionDecision'] == 'deny')

print(f'{fails} failed' if fails else 'all passed')
sys.exit(1 if fails else 0)
