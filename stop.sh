#!/usr/bin/env bash
# Stop the TuringDB server started by ./start.sh (graphs stay on disk in .turing/).
set -euo pipefail
cd "$(dirname "$0")"
uv run turingdb stop -turing-dir "$PWD/.turing"
