#!/usr/bin/env python3
"""Tests for wording_check.py (run: python3 .claude/hooks/test_wording_check.py)."""
import json, os, subprocess, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import wording_check as W
HOOK = W.__file__

BAD = {
    'research-target': ['Our fair value is 120.', 'FV moves to 98.'],
    'upside-downside': ['The bull case is 140.', 'bear scenario at 80', 'Bullish on AMAT'],
    'times-et': ['Ran at 14:05 UTC.', 'filed 13:28Z', 'by 9am UTC'],
    'no-lint': ['The lint flagged two rows.', 'it passed linting'],
    'no-fees': ['Trading fees eat the gain.', 'after fees the trade is worse', 'commission per trade'],
    'agent-views': ['Option B: 0x research weight', 'set research weight 0', 'drop the agent forecasts', 'market-only sizing'],
    'american': ['the licence', 'labelled rows', 'annualised vol', 'utilisation', 'centre of the range'],
}
GOOD = ['Research target (12m) 120, upside 140, downside 80.', 'Filed at 8:28am ET.', 'These risks bear on the multiple.',
        'Fees are not shown.', 'NFLX ad fees rise' , 'research weight 0.5-1x', 'Key `fair_value` and forecast_lint.py stay.',
        'See [doc](https://x.com/fair-value-bull) for analyses.', 'STD_MODEL_MEAN_CENTRE stays 0.75.', 'license, labeled, modeled']


def run(ev):
    r = subprocess.run([sys.executable, HOOK], input=json.dumps(ev), capture_output=True, text=True)
    return json.loads(r.stdout) if r.stdout.strip() else None


def main():
    n = 0
    for rule, xs in BAD.items():
        for x in xs:
            P = W.problems(x); assert any(p.startswith(rule) for p in P), (rule, x, P); n += 1
    for x in GOOD:
        assert not W.problems(x), (x, W.problems(x)); n += 1
    d = tempfile.mkdtemp(); tp = os.path.join(d, 't.jsonl'); open(tp, 'w').close()
    ev = {'tool_name': 'mcp__hearthbot__post_message', 'tool_input': {'text': 'fair value 120'}, 'transcript_path': tp}
    out = run(ev); assert out and out['hookSpecificOutput']['permissionDecision'] == 'deny'; n += 1
    assert run(dict(ev, tool_input={'text': 'research target (12m) 120'})) is None; n += 1
    assert run(dict(ev, tool_name='Bash', tool_input={'command': 'echo fair value'})) is None; n += 1
    doc = {'tool_name': 'mcp__Claude_Docs__batch', 'transcript_path': tp,
           'tool_input': {'container': {'kind': 'project', 'id': 'fair-value'}, 'batch': [{'op': 'x', 'markdown': 'bull case 3'}]}}
    assert run(doc); n += 1
    # allow-next in the transcript lets one call through
    with open(tp, 'w') as f:
        for e in [{'type': 'tool_use', 'id': 'b1', 'name': 'Bash', 'input': {'command': 'python3 .claude/hooks/wording_check.py allow-next --why "quoting x"'}},
                  {'type': 'tool_result', 'tool_use_id': 'b1', 'content': 'ok'}]:
            f.write(json.dumps({'message': {'content': [e]}}) + '\n')
    assert run(ev) is None; n += 1
    with open(tp, 'a') as f:
        for e in [{'type': 'tool_use', 'id': 'm1', 'name': 'mcp__hearthbot__post_message', 'input': {'text': 'fair value'}},
                  {'type': 'tool_result', 'tool_use_id': 'm1', 'content': 'ok'}]:
            f.write(json.dumps({'message': {'content': [e]}}) + '\n')
    assert run(ev); n += 1
    print(f'OK {n} checks')


if __name__ == '__main__':
    main()
