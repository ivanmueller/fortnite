#!/usr/bin/env bash
# setup.sh - one-time pipeline setup on macOS/Linux.   ./pipeline/setup.sh   or   ./pipeline/setup.sh --from-source
set -euo pipefail
cd "$(dirname "$0")/.."   # repo root
need() { command -v "$1" >/dev/null || { echo "Missing: $1. $2"; exit 1; }; }
need node "Install Node.js LTS from https://nodejs.org"
need python3 "Install Python 3.10+"
need dotnet "Install the .NET 10 SDK from https://dotnet.microsoft.com/download"

echo "[1/4] Node packages";   (cd pipeline/node && npm install)
echo "[2/4] Python packages"; node scripts/setup-python.mjs   # project .venv, shared with the API
echo "[3/4] Replay extractor"
build_from_source() {
  need git "Install git"
  v=pipeline/extractor/vendor/FortniteReplayDecompressor
  if [[ -d $v ]]; then git -C $v pull --ff-only; else git clone --depth 1 https://github.com/Shiqan/FortniteReplayDecompressor.git $v; fi
  dotnet build pipeline/extractor/source/fn-extract.source.csproj -c Release
}
if [[ "${1:-}" == "--from-source" ]]; then
  build_from_source || { echo "The replay parser did not build. Send the errors above."; exit 1; }
else
  dotnet build pipeline/extractor/fn-extract.csproj -c Release \
    || { echo "NuGet build failed. Building from the latest GitHub source instead..."; build_from_source; } \
    || { echo "The replay parser did not build. Send the errors above."; exit 1; }
fi
echo "Replay parser built."
echo "[4/4] Config"; [[ -f .env ]] || { cp .env.example .env; echo "Created .env (only needed for the api-fortnite.com fallback)."; }
echo "Setup done. Next: ./pipeline/run.sh local <folder-with-replays>"
