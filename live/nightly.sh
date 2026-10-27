#!/bin/bash
# Nightly hand-off between the project files and the live workflow (run by the nightly routine, not by Actions).
#   live/nightly.sh fold   before the re-mark: append the page's fills (repo fills/) to valuation/data/trades.json
#   live/nightly.sh push   after Price Targets is published: push the live run's inputs to branch live-data and
#                          remove the folded fill files from main (the workflow then rebuilds the patch on the new page)
set -euo pipefail
REPO=https://github.com/kiliantaylor/portfolio-management-settings
ROOT=${ROOT:-/mnt/project-files}
W=$(mktemp -d); trap 'rm -rf "$W"' EXIT
git clone -q --depth 1 "$REPO" "$W/main"
case "${1:-}" in
  fold)
    python3 "$W/main/live/fold_fills.py" "$W/main/fills" "$ROOT/valuation" ;;
  push)
    git clone -q --depth 1 -b live-data "$REPO" "$W/slice" 2>/dev/null || mkdir -p "$W/slice"
    python3 "$W/main/live/slice.py" "$W/slice" --root "$ROOT"
    cd "$W/slice" && rm -rf .git && git init -q && git checkout -q -b live-data && git add -A
    git -c user.name=nightly -c user.email=41898282+github-actions[bot]@users.noreply.github.com commit -qm "live inputs $(date -u +%FT%TZ)"
    git push -qf "$REPO" live-data
    cd "$W/main"
    # only fills already in the trades.json just pushed; a fill entered during the re-mark stays for tomorrow's fold
    ids=$(python3 -c 'import glob,json,os,sys; have={t.get("fill_id") for t in json.load(open(sys.argv[1]))["trades"]}; print(" ".join(i for i in (os.path.basename(p)[:-5] for p in glob.glob("fills/*.json")) if i in have))' "$W/slice/valuation/data/trades.json")
    for id in $ids; do git rm -q "fills/$id.json"; done
    if [ -n "$ids" ]; then
      git -c user.name=nightly -c user.email=41898282+github-actions[bot]@users.noreply.github.com commit -qm "Fold fills into trades.json: $ids"
      git push -q "$REPO" HEAD:main
    fi ;;
  *) echo "usage: live/nightly.sh fold|push" >&2; exit 2 ;;
esac
