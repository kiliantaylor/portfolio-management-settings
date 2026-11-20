"""Checks for timeliness.py (RULES.md 23). Run: python3 test_timeliness.py"""
import copy, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import timeliness as t

G = '2026-11-17'
CC = {'title': 'ConstructConnect, U.S. put-in-place construction forecast, Spring 2026: Private Office +14.8% 2027',
      'url': 'https://news.constructconnect.com/spring-2026', 'date': '2026-11-17'}
GS = {'title': "Goldman Sachs (reported 2026-11-07/08): US data center construction spending forecast raised to $80B",
      'url': 'https://www.tokenpost.com/news/business/51961', 'date': '2026-11-08'}
fails = 0
def ok(cond, msg):
    global fails
    print(('ok   ' if cond else 'FAIL ') + msg); fails += not cond

# 1. the DY case: access date 2026-11-17 must not hide a Spring 2026 edition
f = t.screen(CC, G)
ok(f and f['result'] == 'stale_forecast' and f['published'] == '2026-03-01', 'Spring 2026 edition read from the title, stale in November')
ok(t.screen(CC, G, agent={'published': '2026-04-08'})['published_from'] == 'gate checker (page)', 'checker page date wins')
# 2. kinds
ok(t.kind_of(GS)[0] == 'sell_side', 'Goldman note = sell_side')
ok(t.kind_of({'title': 'Dycom Q2 FY2027 earnings release (8-K Ex.99.1): FY27 outlook', 'url': 'https://www.sec.gov/x'})[0] == 'not_forecast', 'company release not screened')
ok(t.kind_of({'title': 'Nasdaq.com/Zacks DY earnings forecast', 'url': 'https://api.nasdaq.com/api/analyst/DY/earnings-forecast'})[0] == 'consensus', 'aggregator = consensus')
ok(t.kind_of({'title': 'Yahoo Finance chart feed, NVDA last close', 'url': 'https://query1.finance.yahoo.com/x'})[0] == 'not_forecast', 'price feed not screened')
ok(t.kind_of(dict(CC, kind='not_forecast'))[0] == 'not_forecast', 'explicit kind wins')
# 3. fresh sell-side note passes
ok(t.screen(GS, G) is None, 'Goldman 2026-11-07 fresh at 10 days')
# 4. overtaken by actuals even inside the window
fresh = dict(CC, title='X Research construction forecast', published='2026-10-01',
             actuals={'series': 'Census private data center VIP', 'through': '2026-09', 'released': '2026-11-03'})
ok(t.screen(fresh, G)['result'] == 'stale_forecast', 'inside window but actuals released after publication -> stale')
# 5. clearance: newer forecast cited and fresh
srcs = [CC, GS]
c = dict(CC, timeliness={'newer_forecast': [2]})
ok(t.screen(c, G, srcs) is None, 'newer fresh forecast cited -> cleared')
c2 = dict(CC, timeliness={'newer_forecast': [1]})
ok(t.screen(c2, G, [dict(CC), GS]) is not None, 'newer_forecast pointing at a stale/not newer source -> refused')
# 6. clearance: none newer, search shown
nn = {'searched': '2026-11-17', 'queries': ['ConstructConnect fall 2026 forecast', 'Dodge 2027 data center outlook'],
      'checked': ['ConstructConnect Fall 2026 (not out)', 'Dodge Outlook 2027 (Nov 2026: no data center split)']}
ok(t.screen(dict(CC, timeliness={'none_newer': nn}), G) is None, 'none newer with search shown -> cleared (no actuals to reconcile)')
ok(t.screen(dict(CC, timeliness={'none_newer': dict(nn, queries=['one'])}), G) is not None, 'one query is not a search')
ok(t.screen(dict(CC, timeliness={'none_newer': dict(nn, searched='2026-11-01')}), G) is not None, 'old search refused')
ov = dict(fresh, timeliness={'none_newer': nn})
ok(t.screen(ov, G) is not None, 'overtaken + none newer needs a reconciliation')
ok(t.screen(dict(ov, timeliness={'none_newer': nn, 'reconciled': 'Sep 2026 SAAR 41.2B is 8% above its 2027 path; set uses 41.2B x 1.10'}), G) is None,
   'overtaken + none newer + numeric reconciliation -> cleared')
ok(t.screen(dict(CC, timeliness={'none_newer': nn}), G, agent={'newer_found': 'Fall 2026 edition'}) is not None, 'checker found a newer edition -> refused')
# 7. undated
und = {'title': 'Some Research market forecast', 'url': 'https://x.example/y'}
ok(t.screen(und, G)['result'] == 'no_pub_date', 'undated forecast refused')
ok(t.screen(dict(und, date='2026-05-01'), G)['result'] == 'stale_forecast', 'opened > window ago -> stale without a date')
# 8. check_set scope: sources used only in a card are screened; consensus only on rows in scope
S = {'sources': [CC, {'title': 'StockAnalysis DY forecast', 'url': 'https://stockanalysis.com/stocks/dy/forecast/', 'date': '2026-10-09'}],
     'judgment_calls_for_kilian': [{'options': [{'sources': ['https://news.constructconnect.com/spring-2026']}]}],
     'model': {'reconciled_inputs': {'inputs_detail': [{'source_ids': [2]}]}}}
fl = t.check_set(S, S['sources'], G, scope_urls=set())
ok([x['src'] for x in fl] == [1], 'card-only forecast screened; consensus out of scope skipped')
fl = t.check_set(S, S['sources'], G, scope_urls={'https://stockanalysis.com/stocks/dy/forecast/'})
ok(sorted(x['src'] for x in fl) == [1, 2], 'consensus on a row in scope screened (39 days > 21)')
print('all passed' if not fails else f'{fails} failed'); sys.exit(1 if fails else 0)
