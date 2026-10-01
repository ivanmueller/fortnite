#!/usr/bin/env bash
# run.sh - pipeline stages on macOS/Linux (same stages as run.ps1).
#   ./pipeline/run.sh demo                       synthetic data through the whole analysis
#   ./pipeline/run.sh local <folder-with-.replay-files>
#   ./pipeline/run.sh tournaments [season]
#   ./pipeline/run.sh find <eventWindowId> [players]
#   ./pipeline/run.sh pilot [limit]
#   ./pipeline/run.sh analyze [season-label]
set -euo pipefail
cd "$(dirname "$0")/.."   # repo root
extractor() {
  for p in pipeline/extractor/bin/source/fn-extract.dll pipeline/extractor/bin/nuget/fn-extract.dll; do [[ -f $p ]] && { echo "$p"; return; }; done
  echo "Extractor not built. Run ./pipeline/setup.sh first." >&2; exit 1
}
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
  tournaments) (cd pipeline/node && node find_matches.js tournaments ${2:+--season $2}) ;;
  find)  (cd pipeline/node && node find_matches.js window "${2:?usage: ./run.sh find <eventWindowId>}" --players "${3:-60}") ;;
  pilot) (cd pipeline/node && node download.js --limit "${2:-10}"); dotnet "$(extractor)" data/raw data/parsed --mode full; analyze data ;;
  analyze) analyze data "${2:-}" ;;
  *) sed -n 2,8p "$0" ;;
esac
