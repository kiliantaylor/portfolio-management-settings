#!/usr/bin/env python3
"""Tests for live/contract.py: a rename in valuation/ must fail the check (python3 live/test_contract.py)."""
import os, shutil, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__))); import contract

LIVE = '''import sys
def f():
    import combine, book
    combine.model_leg(1)
    getattr(combine, 'iv30', None)
    book.load()
    run('review.py')
'''
VAL = {'combine.py': 'import os\ndef model_leg(x): pass\ndef iv30(): pass\n', 'book.py': 'def load(): pass\n', 'review.py': ''}


def tree(val):
    t = tempfile.mkdtemp(); os.makedirs(f'{t}/valuation'); os.makedirs(f'{t}/live')
    open(f'{t}/live/x.py', 'w').write(LIVE)
    for k, v in val.items(): open(f'{t}/valuation/{k}', 'w').write(v)
    return t


def run(val):
    t = tree(val)
    try: return contract.check(f'{t}/valuation', f'{t}/live')
    finally: shutil.rmtree(t)


assert run(VAL) == [], run(VAL)
bad = run({**VAL, 'combine.py': 'def standard_model(x): pass\ndef iv30(): pass\n'})
assert len(bad) == 1 and 'combine.model_leg' in bad[0], bad
bad = run({**VAL, 'combine.py': 'def model_leg(x): pass\n'})
assert len(bad) == 1 and 'combine.iv30' in bad[0], bad  # getattr(module, 'name') counts
bad = run({k: v for k, v in VAL.items() if k != 'review.py'})
assert len(bad) == 1 and 'review.py' in bad[0], bad
print('test_contract ok')
