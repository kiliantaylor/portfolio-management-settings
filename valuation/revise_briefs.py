"""Set up the forecasters' revision round (round 2) after the round-1 supervisor has written supervisor.json.

Usage: python3 revise_briefs.py DATE SYM [SYM ...]
For each ticker: moves runs/DATE/SYM/{agent_*.json, supervisor.json} into runs/DATE/SYM/round1/ (combine.py ignores a
folder until the round-2 supervisor.json exists), then writes brief_revise_agent_<i>.md from agents/revise.md.
After the revised agent_<i>.json files are in, run the supervisor again on brief_supervisor.md (it now reconciles the
revised forecasts), then combine.py. Refuses to run twice on the same folder (round1/ already holds the files)."""
import glob, os, shutil, sys
HERE = os.path.dirname(os.path.abspath(__file__))

def body(path):
    return open(path).read().split('\n---\n', 1)[1].strip() + '\n'

if __name__ == '__main__':
    date, syms = sys.argv[1], [s.upper() for s in sys.argv[2:]]
    for s in syms:
        rd = os.path.join(HERE, 'runs', date, s); r1 = os.path.join(rd, 'round1')
        if os.path.exists(os.path.join(r1, 'supervisor.json')):
            print(s, 'skipped: round1/ already populated'); continue
        if not os.path.exists(os.path.join(rd, 'supervisor.json')):
            print(s, 'skipped: no round-1 supervisor.json yet'); continue
        os.makedirs(r1, exist_ok=True)
        agents = sorted(glob.glob(f'{rd}/agent_*.json'))
        for f in agents + [f'{rd}/supervisor.json']:
            shutil.move(f, r1)
        import run_digest; run_digest.revise_slices(date, s)  # each agent's slice of the supervisor file (token cut 2026-10-13)
        for f in agents:
            aid = os.path.basename(f)[:-5]
            kv = dict(AGENT_ID=aid, SYMBOL=s, R1_BRIEF=f'{rd}/brief_{aid}.md', R1_PATH=f'{r1}/{aid}.json',
                      SUP_PATH=f'{r1}/supervisor.json', FEEDBACK_PATH=f'{r1}/feedback_{aid}.json', OUT_PATH=f'{rd}/{aid}.json')
            t = body(os.path.join(HERE, 'agents', 'revise.md'))
            for k, v in kv.items(): t = t.replace('{{' + k + '}}', v)
            open(f'{rd}/brief_revise_{aid}.md', 'w').write(t)
        print(s, f'round 1 moved to round1/, {len(agents)} revision briefs written')
