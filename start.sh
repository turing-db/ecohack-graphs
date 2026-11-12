#!/usr/bin/env bash
# Start TuringDB with the visualizer and make the EcoHack graphs ready to query.
#
#   ./start.sh            server on :6666, visualizer on http://localhost:8080
#   PORT=6700 UI_PORT=8090 ./start.sh
#
# Needs TuringDB (pip install turingdb, or uv) and macOS 15+ on Apple silicon or Linux.
# On anything else (macOS 14, Intel Mac, Windows) use Docker: see README.
set -euo pipefail
cd "$(dirname "$0")"
PORT="${PORT:-6666}"
UI_PORT="${UI_PORT:-8080}"
LISTEN="${LISTEN:-127.0.0.1}"   # 0.0.0.0 inside Docker

# Use uv if it is installed, otherwise the python where you ran pip install turingdb.
if command -v uv >/dev/null; then
  uv sync --quiet
  RUN="uv run"
else
  PY="$(command -v python3 || command -v python || true)"
  [ -n "$PY" ] && "$PY" -c "import turingdb" 2>/dev/null || { echo "Install TuringDB first: pip install turingdb"; exit 1; }
  RUN=""
  export PATH="$(dirname "$PY"):$PATH"
  python() { "$PY" "$@"; }
fi

mkdir -p .turing
if $RUN python -c "from turingdb import TuringDB; TuringDB(host='localhost', port=$PORT).list_loaded_graphs()" 2>/dev/null; then
  echo "TuringDB already running on :$PORT"
else
  echo "Starting TuringDB on :$PORT (visualizer :$UI_PORT)"
  $RUN turingdb start -turing-dir "$PWD/.turing" -p "$PORT" -i "$LISTEN" -ui -ui-port "$UI_PORT" -demon >/dev/null
fi

echo "Loading graphs"
$RUN python scripts/load_graphs.py --port "$PORT"

cat <<EOF

Ready.
  Visualizer   http://localhost:$UI_PORT
  Python       from turingdb import TuringDB; c = TuringDB(host="localhost", port=$PORT); c.set_graph("norwood")
  Examples     ${RUN:+$RUN }python queries/run.py norwood
  Stop         ./stop.sh
EOF
