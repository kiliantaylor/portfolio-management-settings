"""Tests for update_speed_check.py (RULES.md 28b/28c). Run: python3 .claude/hooks/test_update_speed_check.py"""
import json, os, subprocess, sys, tempfile
H = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'update_speed_check.py')
T = tempfile.mkdtemp(); os.makedirs(f'{T}/data'); os.makedirs(f'{T}/runs/2026-11-20/VST')
URL = 'https://www.sec.gov/Archives/edgar/data/1692819/000169281926000025/vistra-20260930.htm'


def run(tool, inp):
    r = subprocess.run([sys.executable, H], input=json.dumps(dict(tool_name=tool, tool_input=inp)), capture_output=True, text=True,
                       env=dict(os.environ, SPEED_CHECK_VAL_DIR=T))
    return 'deny' in r.stdout


fails = 0
def check(name, got, want):
    global fails
    if got != want: fails += 1; print('FAIL', name, got, want)

check('webfetch filing doc refused', run('WebFetch', dict(url=URL, prompt='x')), True)
check('ix viewer refused', run('WebFetch', dict(url='https://www.sec.gov/ix?doc=/Archives/edgar/data/1692819/000169281926000025/vistra.htm')), True)
check('chrome navigate refused', run('mcp__claude-in-chrome__navigate', dict(url=URL, tabId=1)), True)
check('curl refused', run('Bash', dict(command=f'curl -s -A x "{URL}" | head')), True)
check('data.sec.gov passes', run('WebFetch', dict(url='https://data.sec.gov/api/xbrl/companyfacts/CIK0001692819.json')), False)
check('efts passes', run('WebFetch', dict(url='https://efts.sec.gov/LATEST/search-index?q=x')), False)
check('secfacts untagged passes', run('Bash', dict(command=f'python3 /mnt/project-files/valuation/secfacts.py untagged VST "{URL}" --item "x"')), False)
check('non-fetch bash passes', run('Bash', dict(command=f'echo {URL}')), False)
open(f'{T}/data/sec_text_fetches.jsonl', 'w').write(json.dumps(dict(filing='1692819/000169281926000025')) + '\n')
check('registered filing passes', run('WebFetch', dict(url=URL)), False)
check('registered filing, other doc passes', run('WebFetch', dict(url=URL.replace('vistra-20260930.htm', 'ex99.htm'))), False)
check('other filing still refused', run('WebFetch', dict(url=URL.replace('000169281926000025', '000169281926000019'))), True)
sheet = dict(title='VST driver model (2026-11-20)', parentId='x', contentMimeType='application/vnd.google-apps.spreadsheet')
check('sheet no route passes', run('mcp__Google_Drive__create_file', sheet), False)
json.dump(dict(route='delta', base_id='abc'), open(f'{T}/runs/2026-11-20/VST/gsheet_route.json', 'w'))
check('sheet delta route refused', run('mcp__Google_Drive__create_file', sheet), True)
check('copy_file passes', run('mcp__Google_Drive__copy_file', dict(fileId='abc', title='VST driver model (2026-11-20)')), False)
json.dump(dict(route='full'), open(f'{T}/runs/2026-11-20/VST/gsheet_route.json', 'w'))
check('sheet full route passes', run('mcp__Google_Drive__create_file', sheet), False)
check('other doc passes', run('mcp__Google_Drive__create_file', dict(title='notes', contentMimeType='text/plain')), False)
print('ok' if not fails else f'{fails} failed'); sys.exit(1 if fails else 0)
