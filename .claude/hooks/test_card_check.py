#!/usr/bin/env python3
"""Tests for card_check.py (run: python3 .claude/hooks/test_card_check.py). Builds fake transcripts and runs the hook."""
import json, os, subprocess, sys, tempfile

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


def run(blocks, pending):
    with tempfile.NamedTemporaryFile('w', suffix='.jsonl', delete=False) as f:
        for b in blocks + [pending]:
            role = 'user' if b['type'] == 'tool_result' else 'assistant'
            f.write(json.dumps({'type': role, 'message': {'role': role, 'content': [b]}}) + '\n')
    ev = {'tool_name': pending['name'], 'tool_input': pending['input'], 'transcript_path': f.name}
    out = subprocess.run([sys.executable, HOOK], input=json.dumps(ev), capture_output=True, text=True).stdout
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
    print(f'{sum(R)}/{len(R)} passed')
    sys.exit(0 if all(R) else 1)


if __name__ == '__main__':
    main()
