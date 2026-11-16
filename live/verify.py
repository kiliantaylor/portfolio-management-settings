#!/usr/bin/env python3
"""Check that the published Price Targets page is actually taking the live patches, instead of assuming it.

    python3 live/verify.py [--root /mnt/project-files] [--since ISO_UTC] [--wait SEC] [--base-only] [SYM ...]

The page applies a patch only when the patch's base equals its own key, as_of|generated|live.time_utc of the data
embedded in the build it shows (report_template.html applyLive). A publish without `live/nightly.sh push` leaves
live-data on the old key, every live run then diffs against the old build, and the page silently ignores every patch
(2026-11-04, 2026-11-11, 2026-11-16: a 7:04am ET republish was never pushed, so AVGO's intraday research push at
11:02am ET was computed, written to the Sheet, and skipped by the page). This checks, in order:
  1. live-data slice.json base == the key of valuation/report.html (the build that is published),
  2. a live run finished after --since (waits up to --wait seconds for it; the push starts one within ~10 s),
  3. that run's patch (branch live-out, the same JSON it wrote to the Sheet's live tab) has the same base,
  4. each SYM (default: every name in slice.json research) is re-marked in that patch, on its pushed research round
     when that round postdates the slice date.
Prints one JSON line; exit 0 when all hold, 1 (with the reason and the fix) when any fails. Run by live/nightly.sh after
every push, research and tape step; anyone can run it to answer "is the page live?".
"""
import json, os, re, subprocess, sys, tempfile, time

REPO = os.environ.get('REPO', 'https://github.com/kiliantaylor/portfolio-management-settings')
ARGS = sys.argv[1:]


def opt(name, default=None):
    if name in ARGS:
        i = ARGS.index(name); v = ARGS[i + 1]; del ARGS[i:i + 2]; return v
    return default


ROOT = opt('--root', '/mnt/project-files')
SINCE = opt('--since', '')
WAIT = int(opt('--wait', '300'))
BASE_ONLY = '--base-only' in ARGS
SYMS = [a.upper() for a in ARGS if a != '--base-only']


def page_key(path):  # same key as publish_check.page_key and live/slice.py base_key
    m = re.search(r'<script id="data" type="application/json">(.*?)</script>', open(path).read(), re.S)
    d = json.loads(m.group(1).replace('<\\/', '</'))
    return f"{d.get('as_of')}|{d.get('generated')}|{(d.get('live') or {}).get('time_utc')}"


def branch_file(branch, name):
    w = tempfile.mkdtemp()
    try:
        r = subprocess.run(['git', 'clone', '-q', '--depth', '1', '-b', branch, REPO, w + '/b'], capture_output=True, text=True, timeout=120)
        if r.returncode: return None
        return json.load(open(f'{w}/b/{name}'))
    except Exception:
        return None
    finally:
        subprocess.run(['rm', '-rf', w])


def fail(why, fix, **kw):
    print(json.dumps(dict(live_verify='FAIL', why=why, fix=fix, **kw)))
    sys.exit(1)


def main():
    want = page_key(os.path.join(ROOT, 'valuation', 'report.html'))
    sl = branch_file('live-data', 'slice.json') or {}
    if sl.get('base') != want:
        fail(f"live-data has base {sl.get('base')}, the published build has {want}: the page ignores every live patch",
             'run live/nightly.sh push (or python3 publish_check.py D --stamp in valuation/), then this check again',
             page_key=want, slice_base=sl.get('base'))
    if BASE_ONLY:
        print(json.dumps(dict(live_verify='ok', checked='base only (no live run started)', page_key=want))); return
    research = sl.get('research') or {}
    syms = SYMS or sorted(research)
    t0, patch = time.time(), None
    while True:
        patch = branch_file('live-out', 'patch.json') or {}
        if patch.get('version', '') >= SINCE and patch.get('base') == want: break
        if time.time() - t0 > WAIT: break
        time.sleep(20)
    if patch.get('version', '') < SINCE:
        fail(f"no live run finished after {SINCE} within {WAIT}s (newest patch {patch.get('version')})",
             'check the live workflow runs (Actions tab, workflow "live"); a red run names the failing step',
             page_key=want)
    if patch.get('base') != want:
        fail(f"the newest live patch ({patch.get('version')}) has base {patch.get('base')}, the page has {want}",
             'run live/nightly.sh push, then this check again', page_key=want)
    rem, miss = patch.get('remarked') or {}, []
    for s in syms:
        r = rem.get(s)
        rd = research.get(s)
        if r is None: miss.append(f'{s}: not re-marked in the patch')
        elif rd and rd > sl.get('date', '') and r.get('research') != rd:
            miss.append(f"{s}: re-marked on research {r.get('research')}, pushed {rd}")
    if miss:
        fail('; '.join(miss), 'read the live run log for these names (live/remark.py prints why a name failed)',
             page_key=want, version=patch.get('version'))
    print(json.dumps(dict(live_verify='ok', page_key=want, version=patch.get('version'),
                          names={s: {k: rem[s].get(k) for k in ('target', 'research') if rem[s].get(k) is not None} for s in syms})))


if __name__ == '__main__':
    main()
