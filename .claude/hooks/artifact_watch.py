#!/usr/bin/env python3
"""PostToolUse hook on Artifact: keep comment watches on the shared pages with one owner thread.

Every Artifact publish leaves the publishing session holding a durable comment watch. On the shared
pages that meant one comment sent to Claude woke ~45 finished threads (Kilian, 2026-11-09). Rule:
only the owner thread (Daily routines) watches the shared pages. Any other session that publishes
one is told to drop its watch right away. Prints nothing (no effect) for the owner or other pages.
"""
import json, os, re, sys

OWNER = '01JyCFfTD4vhsPLj8t6K57Au'  # Daily routines session since 2026-11-11 (was 014feK2CECVJVEWGcpuYtfF1) (session_/cse_ prefix stripped)
SHARED = {
    '9w9Hwe7BLfJyJLmC18Yps5': 'Price Targets',
    'MDSp6x8D7xVUaVkhNGvYNs': "Kilian's Holdings dashboard",
    '8hDNZX3DBAXU5C2rxQJ5VN': 'Portfolio Math Reference',
    '59bT8J6KpmduVYwcQdhWA6': 'Fill Log',
    'Mty2EU96kk2w8qmjdWDWav': 'Catalyst Calendar',
}

def main():
    try: ev = json.load(sys.stdin)
    except ValueError: return
    inp = ev.get('tool_input') or {}
    if inp.get('action') not in (None, 'publish') or inp.get('asset'): return
    me = re.sub(r'^(cse_|session_)', '', os.environ.get('CLAUDE_CODE_REMOTE_SESSION_ID', ''))
    if me == OWNER: return
    blob = json.dumps(inp) + ' ' + json.dumps(ev.get('tool_response'))
    hit = [(i, n) for i, n in SHARED.items() if i in blob]
    if not hit: return
    calls = '; '.join('ArtifactComments action "watch", on false, url "https://claude.ai/artifact/%s" (%s)' % (i, n) for i, n in hit)
    msg = ('PROJECT RULE (Kilian 2026-11-09): only the Daily routines thread watches the shared pages for comments. '
           'This session just published a shared page, which made it a watcher. Drop that watch now, before anything else: '
           + calls + '. Your other own pages: drop their watches too when this thread finishes.')
    print(json.dumps({'hookSpecificOutput': {'hookEventName': 'PostToolUse', 'additionalContext': msg}}))

if __name__ == '__main__':
    main()
