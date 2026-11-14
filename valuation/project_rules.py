"""The project's standing forecasting rules, put in front of every modeler and gate agent (Kilian 2026-11-09, after the VST
FY2027 back-solve passed the gate: "but clearly the gate checker needs to see it"). The agents only see their briefs, so the
rules that live in project memory are copied into each brief when it is written:

  {{PROJECT_RULES}} in agents/gate_shard.md, gate_text.md, gate_fix.md and name_update.md is filled by run_digest.py /
  name_update.py with text(); briefs an agent reads directly (driver_models/BRIEF.md, QBRIEF.md, GATE.md, GATE_Q.md,
  agents/model_inputs.md) tell it to read agents/project_rules.md first.

text() rebuilds agents/project_rules.md from the memory index (the "Always-on rules" block of MEMORY.md) and the forecasting
topic files in TOPICS whenever the memory folder is readable, and falls back to the saved file when it is not (a fresh
container without the memory mount). Edit the rules in memory, not in agents/project_rules.md.

    python3 project_rules.py          rebuild agents/project_rules.md and print its size
    python3 project_rules.py show     print it"""
import os, re, sys
HERE = os.path.dirname(os.path.abspath(__file__)).replace('/mnt/attach/project-files', '/mnt/project-files')
OUT = os.path.join(HERE, 'agents', 'project_rules.md')
MEM = [os.environ.get('PROJECT_MEMORY_DIR', ''), '/tmp/claude/memory/team/silo']
TOPICS = ['no-solving-drivers-to-target', 'forecast-paths-need-real-world-case', 'no-carried-driver-paths', 'guidance-check',
          'drivers-economic-not-segments', 'seasonal-shapes-multiyear', 'every-assumption-sourced', 'primary-sources-not-relays',
          'peer-comps-rule', 'judgment-calls-vs-evidence-alternatives', 'valuation-cards-full-context']
HEAD = """# Project rules (from project memory; Kilian's standing rules)

You are bound by these as much as by your brief. A forecast that breaks one is wrong even when every figure in it is cited.
Gate agents: a year or driver that breaks rule 4 (pure extrapolation) is `trend_only`; one that breaks rule 5 (set to the
guide or the guide x a usual beat, or a driver back-solved to reach any target) is `back_solved`. Both are failures.
forecast_lint.py also checks these by script and refuses the gate and finish; it cannot catch every wording, so you must.
Judgment calls are sized against the evidence-based alternatives, never the guide (rule 2; judgment_check.py refuses the gate
and finish): a cited source that supports a build of a driver not in `judgment_alternatives` is `missing_alternative`.
"""


def _mem_dir():
    for d in MEM:
        if d and os.path.exists(os.path.join(d, 'MEMORY.md')): return d


def _body(p, cap=2600):
    t = open(p).read()
    t = re.sub(r'\A---\n.*?\n---\n', '', t, flags=re.S).strip()
    return t if len(t) <= cap else t[:cap].rsplit(' ', 1)[0] + ' ...'


def build():
    d = _mem_dir()
    if not d: return None
    m = open(os.path.join(d, 'MEMORY.md')).read()
    a = re.search(r'## Always-on rules[^\n]*\n(.*?)(?=\n## )', m, re.S)
    parts = [HEAD, '## Always-on rules\n' + (a.group(1).strip() if a else '(not found in the memory index)')]
    for n in TOPICS:
        p = os.path.join(d, n + '.md')
        if os.path.exists(p): parts.append(f'## {n}\n' + _body(p))
    t = '\n\n'.join(parts) + '\n'
    try: open(OUT, 'w').write(t)
    except OSError: pass
    return t


def text(short=False):
    """short=True (citation shard agents): the header and the always-on rules only, not the topic files."""
    t = build()
    if not t:
        try: t = open(OUT).read()
        except OSError: return HEAD + '\n(agents/project_rules.md missing: ask the thread to run project_rules.py)\n'
    return t.split('\n\n## ', 2)[0] + '\n\n## ' + t.split('\n\n## ', 2)[1] + '\n' if short and t.count('\n\n## ') >= 2 else t


if __name__ == '__main__':
    if sys.argv[1:2] == ['show']: print(text())
    else:
        t = build(); print(f'{OUT}: {len(t)} chars' if t else 'memory folder not readable; agents/project_rules.md left as it is')
