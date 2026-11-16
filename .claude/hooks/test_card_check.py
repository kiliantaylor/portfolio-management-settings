#!/usr/bin/env python3
"""Tests for card_check.py (run: python3 .claude/hooks/test_card_check.py). Builds fake transcripts and runs the hook."""
import json, os, shutil, subprocess, sys, tempfile, time

HOOK = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'card_check.py')

GOOD = """AMAT 2027 WFE growth is the main driver of semis-systems revenue: the set books +12% from the customer capex build \
(TSMC, Samsung, Micron capex guides), which is why it is a judgment call and why it moves the research target (12m) the most.

**Keep +12%**: built company by company from the three largest customers' stated 2027 capex plans; downside is that Samsung \
has not guided 2027 yet. Research target 426.37 (0.0%). Source: https://investor.tsmc.com/english/quarterly-results
**WFE >30%**: follows SEMI's Dec outlook and Lam's call comments on a 2027 step-up; it leans on one industry forecast that \
has overshot before. Research target 457.26 (+7.2%). Sources: https://www.semi.org/en/news and https://investor.lamresearch.com
"""
LABELS = ['Keep +12%', 'WFE >30%']


def card(labels=LABELS, q='Pick the 2027 WFE growth for AMAT', uid='c1'):
    return {'type': 'tool_use', 'id': uid, 'name': 'mcp__hearthbot__request_decision',
            'input': {'question': q, 'options': [{'label': l, 'consequence': 'x'} for l in labels], 'recommended': 0}}


def msg(text, uid='m1'):
    return {'type': 'tool_use', 'id': uid, 'name': 'mcp__hearthbot__post_message', 'input': {'text': text}}


def res(uid, err=False):
    return {'type': 'tool_result', 'tool_use_id': uid, 'content': 'ok', 'is_error': err}


VAL = tempfile.mkdtemp(prefix='cardcheck_val_')  # fake valuation folder: the hook never reads the real one in tests


def bash(cmd, uid):
    return {'type': 'tool_use', 'id': uid, 'name': 'Bash', 'input': {'command': cmd}}


def put(rel, obj, age=0):
    p = os.path.join(VAL, rel); os.makedirs(os.path.dirname(p), exist_ok=True)
    json.dump(obj, open(p, 'w'))
    t = time.time() - age; os.utime(p, (t, t))


def run(blocks, pending):
    with tempfile.NamedTemporaryFile('w', suffix='.jsonl', delete=False) as f:
        for b in blocks + [pending]:
            role = 'user' if b['type'] == 'tool_result' else 'assistant'
            f.write(json.dumps({'type': role, 'message': {'role': role, 'content': [b]}}) + '\n')
    ev = {'tool_name': pending['name'], 'tool_input': pending['input'], 'transcript_path': f.name}
    env = dict(os.environ, CARD_CHECK_VAL_DIR=VAL, CARD_CHECK_LOG=os.path.join(VAL, 'overrides.jsonl'))
    out = subprocess.run([sys.executable, HOOK], input=json.dumps(ev), capture_output=True, text=True, env=env).stdout
    os.unlink(f.name)
    return json.loads(out)['hookSpecificOutput']['permissionDecisionReason'] if out.strip() else None


def check(name, got_deny, want_deny, needle=None, reason=''):
    ok = (got_deny is not None) == want_deny and (needle is None or (reason and needle in reason))
    print(('PASS ' if ok else 'FAIL ') + name + ('' if ok else f'\n    {reason}'))
    return ok


def main():
    R = []
    r = run([msg(GOOD), res('m1')], card()); R.append(check('good message passes', r, False, reason=r))
    r = run([], card()); R.append(check('no message refused', r, True, 'No context message', r))
    r = run([msg(GOOD), res('m1'), card(uid='c0'), res('c0')], card())
    R.append(check('message used by an earlier card does not count', r, True, 'No context message', r))
    r = run([msg(GOOD), res('m1'), card(uid='c0'), res('c0', err=True)], card())
    R.append(check('a refused earlier card does not use up the message', r, False, reason=r))
    r = run([msg(GOOD.replace('https://investor.tsmc.com/english/quarterly-results', 'the TSMC deck')), res('m1')], card())
    R.append(check('option without link refused', r, True, 'option 1 "Keep +12%": no linked source', r))
    r = run([msg(GOOD.replace('**WFE >30%**', 'WFE above thirty')), res('m1')], card())
    R.append(check('option without its own section refused', r, True, 'option 2 "WFE >30%" has no section', r))
    r = run([msg(GOOD.split('\n\n', 1)[1]), res('m1')], card()); R.append(check('no context refused', r, True, 'context before', r))
    r = run([msg(GOOD.replace('Research target 426.37 (0.0%). ', '')), res('m1')], card())
    R.append(check('valuation option without sized number refused', r, True, 'no sized number', r))
    short = GOOD.split('**WFE')[0] + '**WFE >30%**: SEMI says so. https://www.semi.org +7.2%\n'
    r = run([msg(short), res('m1')], card()); R.append(check('thin reasoning refused', r, True, 'option 2 "WFE >30%": reasoning', r))
    wf = ('Kilian asked whether the AVGO share-gain read-through should be its own study or folded into the Nov 18 MOD update; '
          'the two touch the same hyperscaler orders so the choice is about thread size, not the model.\n\n'
          '1. Own thread - keeps this thread short per the project rule and lets the study run on its own schedule without '
          'blocking anything here. No source: a workflow choice with nothing outside to cite.\n'
          '2. Fold in - fewer threads to follow, but it makes the Nov 18 update longer and mixes two questions in one place. '
          'No source: a workflow choice with nothing outside to cite.\n')
    r = run([msg(wf), res('m1')], card(['Own thread', 'Fold in'], q='Run the study in its own thread?'))
    R.append(check('workflow card with "No source:" passes', r, False, reason=r))
    allow = {'type': 'tool_use', 'id': 'b1', 'name': 'Bash',
             'input': {'command': 'python3 .claude/hooks/card_check.py allow-next --why "Kilian 11-13 3:10am ET: just give me a bare yes/no card"'}}
    r = run([allow, res('b1')], card()); R.append(check('allow-next lets one card through', r, False, reason=r))
    r = run([allow, res('b1'), card(uid='c0'), res('c0')], card()); R.append(check('allow-next is used up by one card', r, True, 'No context', r))
    r = run([msg(GOOD)], card()); R.append(check('message without result yet (same batch) still counts', r, False, reason=r))
    R += gate_tests()
    shutil.rmtree(VAL, ignore_errors=True)
    print(f'{sum(R)}/{len(R)} passed')
    sys.exit(0 if all(R) else 1)


def gate_tests():
    R = []
    rd = 'runs/2026-11-13/AMAT'
    touch = [bash(f'cat /mnt/project-files/valuation/{rd}/supervisor.json | head', 'b0'), res('b0')]
    good = [msg(GOOD), res('m1')]
    put(f'{rd}/supervisor.json', {}, age=100)
    r = run(touch + good, card()); R.append(check('gate: round with no gate refused', r, True, 'no citation gate has run', r))
    put(f'{rd}/gate/checklist.json', {}, age=50)
    r = run(touch + good, card()); R.append(check('gate: running gate refused', r, True, 'gate is running', r))
    put(f'{rd}/gate/merged.json', {'failures': [{'item': 'x'}] * 3}, age=40)
    r = run(touch + good, card()); R.append(check('gate: failed merge refused', r, True, 'merged with 3 failures', r))
    r = run(touch, card()); R.append(check('gate: refusal lists gate and reasoning problems together', r, True, 'No context message', r)
                                     and 'merged with 3 failures' in r)
    put(f'{rd}/citation_audit.json', {'clean': True, 'checked': 9, 'open_failures': []}, age=30)
    r = run(touch + good, card()); R.append(check('gate: clean audit after the fix passes', r, False, reason=r))
    put(f'{rd}/gate/merged.json', {'failures': [{'item': 'y'}]}, age=10)
    r = run(touch + good, card()); R.append(check('gate: re-merge with failures after a clean audit refused', r, True, 'merged with 1 failures', r))
    put(f'{rd}/citation_audit.json', {'clean': True, 'checked': 9, 'open_failures': []}, age=5)
    r = run([bash('python3 name_update.py gate 2026-11-13 AMAT', 'b0'), res('b0')] + good, card())
    R.append(check('gate: "script DATE SYM" form found; clean passes', r, False, reason=r))
    put('runs/2026-11-15/AMAT/supervisor.json', {}, age=1)
    r = run(touch + [bash('ls runs/2026-11-15/AMAT/', 'b2'), res('b2')] + good, card())
    R.append(check('gate: newest round of the symbol is the one checked', r, True, 'runs/2026-11-15/AMAT', r))
    r = run(touch + [bash('ls runs/2026-11-15/AMAT/', 'b2'), res('b2')] + good,
            card(['Keep +12%', 'WFE >30%'], q='Pick the 2027 WFE growth for MOD'))
    R.append(check('gate: other symbol named in card -> AMAT still checked via message', r, True, 'runs/2026-11-15/AMAT', r))
    wf = ('Kilian asked whether the follow-up study should be its own thread or folded into this one; '
          'the choice is about thread size and scheduling only, nothing in it moves a number anywhere.\n\n'
          '1. Own thread - keeps this thread short per the project rule and lets the study run on its own schedule without '
          'blocking anything here. No source: a workflow choice with nothing outside to cite.\n'
          '2. Fold in - fewer threads to follow, but it makes this thread longer and mixes two questions in one place. '
          'No source: a workflow choice with nothing outside to cite.\n')
    r = run(touch + [bash('ls runs/2026-11-15/AMAT/', 'b2'), res('b2'), msg(wf), res('m1')],
            card(['Own thread', 'Fold in'], q='Run the study in its own thread?'))
    R.append(check('gate: workflow card naming no symbol is not gate-checked', r, False, reason=r))
    ung = bash('python3 .claude/hooks/card_check.py allow-ungated --why "AMAT driver-set approval before the build; no gate yet"', 'b3')
    base = touch + [bash('ls runs/2026-11-15/AMAT/', 'b2'), res('b2')]
    r = run(base + [ung, res('b3')] + good, card()); R.append(check('gate: allow-ungated lets one card past the gate', r, False, reason=r))
    r = run(base + [ung, res('b3'), card(uid='c0'), res('c0')] + [msg(GOOD, 'm2'), res('m2')], card())
    R.append(check('gate: allow-ungated is used up by one card', r, True, 'runs/2026-11-15/AMAT', r))
    r = run(base + [ung, res('b3')], card()); R.append(check('gate: allow-ungated does not skip the reasoning check', r, True, 'No context message', r))
    allow = bash('python3 .claude/hooks/card_check.py allow-next --why "Kilian 11-13 3:10am ET: just give me a bare yes/no card"', 'b4')
    r = run(base + [allow, res('b4')], card()); R.append(check('gate: allow-next does not skip the gate', r, True, 'runs/2026-11-15/AMAT', r))
    # driver sets
    put('driver_models/MOD/set.json', {}, age=20)
    dtouch = [bash('python3 drv.py run MOD --no-combine', 'd0'), res('d0')]
    q = 'Pick the MOD data-center margin path'
    r = run(dtouch + good, card(q=q)); R.append(check('gate: driver set without gate.json refused', r, True, 'no gate.json', r))
    put('driver_models/MOD/gate.json', {'clean': False, 'open': ['dc_margin FY28']}, age=10)
    r = run(dtouch + good, card(q=q)); R.append(check('gate: driver gate not clean refused', r, True, 'dc_margin FY28', r))
    put('driver_models/MOD/gate.json', {'clean': True, 'open': []}, age=10)
    r = run(dtouch + good, card(q=q)); R.append(check('gate: driver gate clean passes', r, False, reason=r))
    put('driver_models/MOD/set.json', {}, age=1)
    r = run(dtouch + good, card(q=q)); R.append(check('gate: set.json edited after gate refused', r, True, 'changed after gate.json', r))
    return R


if __name__ == '__main__':
    main()
