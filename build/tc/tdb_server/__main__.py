"""python -m tc.tdb_server {up,down,status,load,drop} --dir .turing --port 6694 [...]

Exit codes (docs/AUTHORING.md "CLI exit codes"): 0 ok · 2 usage error · 3 the server could not be started/stopped/loaded or crashed.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

from . import TdbServer, TdbServerError, find_binary, port_owner


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="tdb_server")
    p.add_argument("cmd", choices=["up", "down", "status", "load", "drop", "restart"])
    p.add_argument("graphs", nargs="*")
    p.add_argument("--dir", default=os.environ.get("TURING_DIR", ".turing"))
    p.add_argument("--port", type=int, default=int(os.environ.get("TURINGDB_PORT", "0") or 0), required=False)
    p.add_argument("--host", default=os.environ.get("TURINGDB_LISTEN", "127.0.0.1"))
    p.add_argument("--bin", default=None)
    p.add_argument("--in-memory", action="store_true")
    p.add_argument("--ui-port", type=int, default=None)
    p.add_argument("--start-timeout-ms", type=int, default=10000)
    p.add_argument("--forbid", default=os.environ.get("TDB_FORBIDDEN_PORTS", ""),
                   help="comma list of other demos' ports to never touch")
    a = p.parse_args(argv)
    if not a.port:
        p.error("--port (or TURINGDB_PORT) is required")
    srv = TdbServer(a.dir, a.port, host=a.host, binary=a.bin, in_memory=a.in_memory,
                    ui_port=a.ui_port, start_timeout_ms=a.start_timeout_ms,
                    forbidden_ports=tuple(int(x) for x in a.forbid.split(",") if x.strip()))
    try:
        if a.cmd == "up":
            srv.start()
            loaded = srv.load_graphs(a.graphs or None)
            print(f"{'adopted' if srv.adopted else 'started'} {srv.url} dir={srv.turing_dir} loaded={loaded}")
        elif a.cmd == "down":
            srv.stop()
            print(f"stopped {srv.url}")
        elif a.cmd == "restart":
            srv.restart()
            print(f"restarted {srv.url} loaded={srv.load_graphs(a.graphs or None)}")
        elif a.cmd == "load":
            print(json.dumps(srv.load_graphs(a.graphs or None)))
        elif a.cmd == "drop":
            for g in a.graphs:
                srv.drop_graph(g, staged=(f"{g}.jsonl",))
                print(f"dropped {g}")
        else:
            o = port_owner(srv.port)
            print(json.dumps({"url": srv.url, "binary": find_binary(a.bin), "owner_pid": o and o[0],
                              "owner_dir": o and str(o[1]), "ours": srv.is_ours(), "up": srv.is_up(),
                              "graphs": srv.available_graphs() if srv.is_up() else None}, indent=2))
    except TdbServerError as e:
        print(f"error: {e}", file=sys.stderr)
        return 3
    return 0


def _entry() -> int:
    """Library exit-code table (docs/AUTHORING.md): a crash is 3, never 1 (1 means checks failed)."""
    try:
        return main()
    except Exception:  # noqa: BLE001
        import traceback
        traceback.print_exc()
        return 3


if __name__ == "__main__":
    sys.exit(_entry())
