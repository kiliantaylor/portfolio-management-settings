"""Model price ladders for the held names, as one JSON doc for the Fill Log page.

The page turns the fills it already holds into shares held, then reads these ladders to show the next buy and sell level
for that exact share count, so a fill typed in the form changes the strip with no sync. Usage:
  python3 fill_plan.py D [out.json]     # D = newest runs/ date; prints the doc, or writes it to out.json
Write the result to the Fill Log page with ArtifactData set (collection "plan", doc_id "latest", file_path out.json).
"""
import json, os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
D = sys.argv[1]
names = {}
for s in sorted(json.load(open(f'{HERE}/runs/{D}/review_summary.json'))['sizing']['target_shares']):
    p = f'{HERE}/runs/{D}/{s}/decider.json'
    if not os.path.exists(p):
        continue
    d = json.load(open(p))
    names[s] = dict(spot=d['spot'], ladder=[[l['price'], l['shares']] for l in d['ladder']], stop=(d.get('marginal') or {}).get('stop'))
doc = dict(run_date=D, quotes_as_of=json.load(open(f'{HERE}/runs/{D}/review_summary.json')).get('live_quotes_as_of'), names=names)
if len(sys.argv) > 2:
    json.dump(doc, open(sys.argv[2], 'w'))
else:
    print(json.dumps(doc))
