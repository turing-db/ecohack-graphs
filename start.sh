#!/usr/bin/env bash
# Start TuringDB with the visualizer and make the EcoHack graphs ready to query.
#
#   ./start.sh            server on :6666, visualizer on http://localhost:8080
#   PORT=6700 UI_PORT=8090 ./start.sh
#
# Needs uv (https://docs.astral.sh/uv/) and macOS 15+ on Apple silicon or Linux.
# On anything else (macOS 14, Intel Mac, Windows) use Docker: see README.
set -euo pipefail
cd "$(dirname "$0")"
PORT="${PORT:-6666}"
UI_PORT="${UI_PORT:-8080}"
LISTEN="${LISTEN:-127.0.0.1}"   # 0.0.0.0 inside Docker

command -v uv >/dev/null || { echo "Install uv first: curl -LsSf https://astral.sh/uv/install.sh | sh"; exit 1; }
uv sync --quiet

mkdir -p .turing
if uv run python -c "from turingdb import TuringDB; TuringDB(host='localhost', port=$PORT).list_loaded_graphs()" 2>/dev/null; then
  echo "TuringDB already running on :$PORT"
else
  echo "Starting TuringDB on :$PORT (visualizer :$UI_PORT)"
  uv run turingdb start -turing-dir "$PWD/.turing" -p "$PORT" -i "$LISTEN" -ui -ui-port "$UI_PORT" -demon >/dev/null
fi

echo "Loading graphs"
uv run python scripts/load_graphs.py --port "$PORT"

cat <<EOF

Ready.
  Visualizer   http://localhost:$UI_PORT
  Python       from turingdb import TuringDB; c = TuringDB(host="localhost", port=$PORT); c.set_graph("norwood")
  Examples     uv run python queries/run.py norwood
  Stop         ./stop.sh
EOF
