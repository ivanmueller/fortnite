#!/usr/bin/env bash
# run.sh - pipeline stages on macOS/Linux (same stages as run.ps1).
#   ./pipeline/run.sh demo                       synthetic data through the whole analysis
#   ./pipeline/run.sh local <folder-with-.replay-files>
#   ./pipeline/run.sh login | logout | test
#   ./pipeline/run.sh tournaments [extra args, e.g. --region EU --search cash --days 30]
#   ./pipeline/run.sh find <eventWindowId> [pages|all]
#   ./pipeline/run.sh pilot [limit] [epic|api-fortnite]
#   ./pipeline/run.sh analyze [season-label]
#   ./pipeline/run.sh reparse
set -euo pipefail
cd "$(dirname "$0")/.."   # repo root
extractor() {
  for p in pipeline/extractor/bin/source/ZoneLab.ReplayReader.dll pipeline/extractor/bin/nuget/ZoneLab.ReplayReader.dll; do [[ -f $p ]] && { echo "$p"; return; }; done
  echo "The replay parser needs rebuilding after an update. Run ./pipeline/setup.sh first." >&2; exit 1
}
DATA=$( [[ -f .env ]] && sed -n 's/^[[:space:]]*ZONELAB_DATA_DIR[[:space:]]*=[[:space:]]*//p' .env | head -1 | tr -d '"\r' )
DATA=${DATA:-data}
analyze() {
  local dir="$1" season="${2:-}"
  node scripts/py.mjs pipeline/python/flatten.py --data-dir "$dir"
  node scripts/py.mjs pipeline/python/validate.py --data-dir "$dir"
  if [[ -n $season ]]; then node scripts/py.mjs pipeline/python/zone_analysis.py --data-dir "$dir" --season "$season"
  else node scripts/py.mjs pipeline/python/zone_analysis.py --data-dir "$dir"; fi
  echo "Reports in $dir/reports"
}
case "${1:-help}" in
  demo)  node scripts/py.mjs pipeline/python/make_synthetic.py --preset demo --sample-dt 3; analyze data_synthetic ;;
  local) src="${2:?usage: ./run.sh local <folder>}"; mkdir -p data_local/raw; cp "$src"/*.replay data_local/raw/
         dotnet "$(extractor)" data_local/raw data_local/parsed --mode full; analyze data_local ;;
  login) (cd pipeline/node && node epic_auth.js login && node epic_auth.js status) ;;
  logout) (cd pipeline/node && node epic_auth.js logout) ;;
  test)  (cd pipeline/node && node find_matches.js test) ;;
  tournaments) shift; (cd pipeline/node && node find_matches.js tournaments "$@") ;;
  find)  (cd pipeline/node && node find_matches.js window "${2:?usage: ./run.sh find <eventWindowId>}" --pages "${3:-10}") ;;
  pilot) (cd pipeline/node && node download.js --limit "${2:-10}" --via "${3:-epic}" ${4:+--window "$4"}); dotnet "$(extractor)" "$DATA/raw" "$DATA/parsed" --mode full; analyze "$DATA" ;;
  analyze) analyze "$DATA" "${2:-}" ;;
  pois) (cd pipeline/node && node pois.js) && analyze "$DATA" ;;
  survey) f=$(ls -t "$DATA"/raw/${2:-}*.replay | head -1); mkdir -p "$DATA/reports/survey"; dotnet "$(extractor)" "$f" "$DATA/reports/survey" --survey --overwrite ;;
  weekly) (cd pipeline/node && node find_matches.js weekly --days "${2:-7}" && node download.js --limit "${3:-100}" --min-top1000 "${4:-3}"); dotnet "$(extractor)" "$DATA/raw" "$DATA/parsed" --mode full; analyze "$DATA" ;;
  genexports) s=$(ls -t "$DATA"/reports/survey/*.survey.json | head -1); python3 pipeline/python/gen_exports.py "$s" ${PARSER_SRC:+--parser-src "$PARSER_SRC"} ;;
  plan) (cd pipeline/node && node download.js --plan) ;;
  pr) (cd pipeline/node && node find_matches.js powerrankings --pages "${2:-all}"); analyze "$DATA" ;;
  reparse) dotnet "$(extractor)" "$DATA/raw" "$DATA/parsed" --mode full --overwrite; analyze "$DATA" ;;
  *) sed -n 2,8p "$0" ;;
esac
