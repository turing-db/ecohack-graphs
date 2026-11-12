#!/usr/bin/env bash
# Stop the TuringDB server started by ./start.sh (graphs stay on disk in .turing/).
set -euo pipefail
cd "$(dirname "$0")"
if command -v uv >/dev/null; then RUN="uv run"; else RUN=""; fi
$RUN turingdb stop -turing-dir "$PWD/.turing"
