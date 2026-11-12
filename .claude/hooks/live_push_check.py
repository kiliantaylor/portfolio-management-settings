#!/usr/bin/env python3
"""PostToolUse hook on Artifact: after a Price Targets publish, make sure the live job knows which build is live.

The page only applies a live patch whose base key matches its own data (as_of|generated|live.time_utc). Every
rebuild has a new key, so a publish without `live/nightly.sh push` leaves the page on the prior close until
someone pushes (2026-11-04, 2026-11-11: 3:18pm ET publish, page stuck until 4:50pm ET). Kilian 2026-11-11:
"do it in code instead". This hook:
  1. reads the published file's key (tool_input.file_path, else valuation/report.html),
  2. reads live-data's slice.json base,
  3. if they differ and the published file matches report.html on disk, runs publish_check.push_live() (the
     same push + verify as `publish_check.py D --stamp`); if the published file is not report.html's build it
     cannot push the right slice, so it tells the session to fix that before doing anything else.
Prints nothing when the keys already match (e.g. publish_check --stamp ran first) or for other pages.
"""
import contextlib, io, json, os, subprocess, sys, tempfile

PAGE = '9w9Hwe7BLfJyJLmC18Yps5'  # Price Targets
VAL = os.environ.get('PT_VALUATION_DIR', '/mnt/project-files/valuation')
REPO = 'https://github.com/kiliantaylor/portfolio-management-settings'


def load_publish_check():
    # exec-load by path: a bare import can pick up a same-named module elsewhere (11-10 'import review' incident)
    p = os.path.join(VAL, 'publish_check.py')
    g = {'__name__': 'publish_check', '__file__': p}
    exec(compile(open(p).read(), p, 'exec'), g)
    return g


def live_data_base():
    w = tempfile.mkdtemp()
    try:
        r = subprocess.run(['git', 'clone', '-q', '--depth', '1', '-b', 'live-data', REPO, w + '/s'],
                           capture_output=True, text=True, timeout=90)
        if r.returncode: return None, (r.stderr or '')[-300:]
        return json.load(open(w + '/s/slice.json')).get('base'), ''
    except Exception as e:
        return None, repr(e)[-300:]
    finally:
        subprocess.run(['rm', '-rf', w])


def say(msg):
    print(json.dumps({'hookSpecificOutput': {'hookEventName': 'PostToolUse', 'additionalContext': msg}}))


def main():
    try: ev = json.load(sys.stdin)
    except ValueError: return
    inp = ev.get('tool_input') or {}
    if inp.get('action') not in (None, 'publish') or inp.get('asset'): return
    if PAGE not in json.dumps(inp) + ' ' + json.dumps(ev.get('tool_response')): return
    resp = json.dumps(ev.get('tool_response')).lower()
    if any(w in resp for w in ('refused', 'rejected', 'conflict')) and 'published' not in resp: return
    try:
        pc = load_publish_check()
        disk = os.path.join(VAL, 'report.html')
        fp = inp.get('file_path') or disk
        if not os.path.isabs(fp): fp = os.path.join(os.environ.get('CLAUDE_PROJECT_DIR', os.getcwd()), fp)
        want, disk_key = pc['page_key'](fp), pc['page_key'](disk)
    except Exception as e:
        say('LIVE PUSH CHECK could not read the published Price Targets key (%r). Run `python3 publish_check.py D '
            '--stamp` in valuation/ now, or the page stays on the prior close.' % e)
        return
    base, err = live_data_base()
    if base == want: return
    if want != disk_key:
        say('LIVE PUSH CHECK: the Price Targets page you just published (key %s) is not the build in '
            'valuation/report.html (key %s), so live-data cannot be pushed for it and the page will stay on the '
            'prior close. Republish valuation/report.html itself, or rebuild report.html to match, then run '
            '`python3 publish_check.py D --stamp`. Do this before anything else.' % (want, disk_key))
        return
    try:
        with contextlib.redirect_stdout(io.StringIO()):  # hook stdout must stay one JSON object
            pc['push_live']()  # clones, runs live/nightly.sh push, verifies base; exits 1 on failure
        say('LIVE PUSH CHECK: live-data had base %s, the page has %s. The hook pushed the new build (verified), '
            'so the next live run re-marks the page. Research added with `live/nightly.sh research SYM` after this '
            'build was dropped by the push: rerun it for those names.' % (base, want))
    except SystemExit:
        say('LIVE PUSH CHECK FAILED: live-data still has base %s, the page has %s (%s). Run `python3 '
            'publish_check.py D --stamp` in valuation/ now; until it succeeds the page stays on the prior close.'
            % (base, want, err))


if __name__ == '__main__':
    main()
