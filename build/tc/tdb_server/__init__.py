"""Dedicated TuringDB server per demo: own port + own turing-dir, guarded start/stop.

    from tc.tdb_server import TdbServer, free_port, find_binary
    srv = TdbServer(".turing", 6694).start()          # adopts only if the listener is OURS
    srv.load_graphs()                                 # 3.0: graphs are NOT loaded after a restart
    srv.rebuild_graph("perimeter", build=lambda url: run_loader(url))
    srv.stop()                                        # `turingdb stop -turing-dir`, then polls the port

Hard rules this module enforces (each one cost a demo an afternoon):

* A listening port is NOT proof the server is ours. Adoption requires the
  listener's `-turing-dir` to equal ours; a mismatch is a hard error naming the
  squatter (codegraph once silently adopted Kraken's 1.35 server and wrote its
  graphs into that demo's data dir).
* Read the owner's full command line (/proc/<pid>/cmdline, or `ps -ww`): plain
  `ps -o args=` truncates at the terminal width and made the guard reject its
  own server.
* Stop with `turingdb stop -turing-dir <dir>` -- never `-p` (it fails and leaves
  the server up), never a bare `stop` (targets ~/.turing), never pkill / fuser -k
  / `lsof -ti | xargs kill` (kills other demos on a shared box). `stop` can fail
  silently, so poll the port and say what went wrong.
* There is no DROP GRAPH: removing a graph = stop -> rm <dir>/graphs/<name> ->
  start. Remove it only with the server stopped.
* After any restart, graphs on disk are not loaded (GRAPH_NOT_FOUND until
  LOAD GRAPH); unsubmitted changes are gone; past commits need LOAD COMMIT.
* Use the demo's own pinned binary (venv / wheel), never whatever `turingdb` is
  first on PATH -- on the Mac that was a 1.0 uv tool while the venv held 3.0.

Stdlib only, so it can run before any pip install (bootstrap scripts, Dockerfiles).
"""
from __future__ import annotations

import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

__all__ = ["TdbServer", "TdbServerError", "find_binary", "binary_version", "check_version",
           "free_port", "port_owner", "query_raw", "is_up"]

# Subdirectories a server creates when it initialises a turing-dir. A dir that
# exists with only data/ (JSONL written before the first start) was, on 1.37,
# never initialised and LOAD JSONL failed with "Failed to load JSONL graph".
# 3.0 initialises it fine (measured 2026-10-09); we still repair it, cheaply.
SERVER_DIRS = ("graphs", "data", "logs", "extensions", "vector")


class TdbServerError(RuntimeError):
    pass


# --------------------------------------------------------------- binary ---
def find_binary(explicit: str | os.PathLike | None = None) -> str:
    """The turingdb server binary to use, most specific first.

    1. `explicit` / $TURINGDB_BIN (a local unreleased build, or a pinned path)
    2. the wheel's own `turingdb/bin/turingdb` for the running interpreter
       (the real server, not the python console-script wrapper)
    3. `<dir of sys.executable>/turingdb` (the venv's console script)
    4. `turingdb` on PATH (last resort: may be another version entirely)
    """
    for cand in (explicit, os.environ.get("TURINGDB_BIN")):
        if cand:
            p = Path(cand).expanduser()
            if not p.exists():
                raise TdbServerError(f"turingdb binary not found: {p}")
            return str(p)
    try:
        import importlib.util
        spec = importlib.util.find_spec("turingdb")
        if spec and spec.submodule_search_locations:
            for loc in spec.submodule_search_locations:
                p = Path(loc) / "bin" / "turingdb"
                if p.exists():
                    return str(p)
    except Exception:  # noqa: BLE001 - a broken turingdb install should fall through
        pass
    p = Path(sys.executable).parent / "turingdb"
    if p.exists():
        return str(p)
    w = shutil.which("turingdb")
    if w:
        return w
    raise TdbServerError("no turingdb binary: pip install turingdb==3.0 in this venv or set TURINGDB_BIN")


def binary_version(binary: str | None = None) -> str:
    """Engine version of a binary, from the wheel's dist-info next to it ('3.0', '1.35'...).

    `turingdb --version` prints '1.0' for every release (measured on 1.35 and 3.0), so it
    cannot tell a stale uv-tool install on PATH from the venv's 3.0. Returns 'unknown'
    for a source build (then check the build hash in logs/turingdb.log)."""
    p = Path(find_binary(binary)).resolve()
    for parent in list(p.parents)[:4]:
        for pat in ("turingdb-*.dist-info", "lib/python*/site-packages/turingdb-*.dist-info"):
            hits = sorted(parent.glob(pat))
            if hits:
                return hits[-1].name[len("turingdb-"):-len(".dist-info")]
    return "unknown"


def check_version(binary: str | None, prefix: str | None) -> str:
    """Raise unless the binary's version starts with `prefix` (None/'' = no check)."""
    v = binary_version(binary)
    if prefix and not v.startswith(prefix):
        raise TdbServerError(f"turingdb binary {find_binary(binary)} is version {v}, need {prefix}* "
                             "(set TURINGDB_BIN or install turingdb==3.0 in this venv)")
    return v


# ------------------------------------------------------------------ http ---
def query_raw(url: str, cypher: str, graph: str = "", timeout: float = 5.0) -> dict:
    """POST one statement; returns the decoded JSON body (which may hold `error`).
    Raises OSError/URLError when nothing answers."""
    req = urllib.request.Request(f"{url.rstrip('/')}/query?graph={graph}", data=cypher.encode(),
                                 method="POST", headers={"Content-Type": "text/plain"})
    tok = os.environ.get("TURINGDB_AUTH_TOKEN")
    if tok:
        req.add_header("Authorization", f"Bearer {tok}")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read() or b"{}")


def is_up(url: str, timeout: float = 2.0) -> bool:
    """Any JSON answer to a graphless statement = up (no SDK, no removed endpoints)."""
    try:
        query_raw(url, "LIST AVAILABLE GRAPHS", timeout=timeout)
        return True
    except urllib.error.HTTPError:
        return True                      # it answered: up (e.g. auth on)
    except Exception:  # noqa: BLE001
        return False


# ------------------------------------------------------------- ownership ---
def _pid_on(port: int) -> int | None:
    """pid listening on TCP `port` (lsof, else ss), or None."""
    if shutil.which("lsof"):
        out = subprocess.run(["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-t"],
                             capture_output=True, text=True).stdout.split()
        return int(out[0]) if out else None
    if shutil.which("ss"):
        out = subprocess.run(["ss", "-ltnpH", f"sport = :{port}"], capture_output=True, text=True).stdout
        if "pid=" in out:
            return int(out.split("pid=", 1)[1].split(",", 1)[0])
    return None


def cmdline_of(pid: int) -> str:
    """Full command line: /proc first (cannot be truncated), else `ps -ww`."""
    try:
        raw = Path(f"/proc/{int(pid)}/cmdline").read_bytes()
        if raw:
            return " ".join(raw.decode("utf8", "replace").split("\0")).strip()
    except (OSError, ValueError):
        pass
    return subprocess.run(["ps", "-ww", "-o", "args=", "-p", str(pid)],
                          capture_output=True, text=True).stdout.strip()


def port_owner(port: int) -> tuple[int, Path | None] | None:
    """(pid, its -turing-dir or None) for whatever listens on `port`, or None."""
    pid = _pid_on(port)
    if pid is None:
        return None
    argv = cmdline_of(pid).split()
    tdir = None
    if "-turing-dir" in argv and argv.index("-turing-dir") + 1 < len(argv):
        tdir = Path(argv[argv.index("-turing-dir") + 1]).expanduser().resolve()
    return pid, tdir


def free_port(start: int = 6751, end: int = 6999, host: str = "127.0.0.1",
              avoid: tuple[int, ...] = ()) -> int:
    """First port in [start, end] nothing listens on (bind test + no HTTP answer)."""
    for p in range(start, end + 1):
        if p in avoid:
            continue
        with socket.socket() as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                s.bind((host, p))
            except OSError:
                continue
        if _pid_on(p) is None and not is_up(f"http://{host}:{p}", timeout=0.3):
            return p
    raise TdbServerError(f"no free port in {start}-{end}")


# ---------------------------------------------------------------- server ---
class TdbServer:
    """One TuringDB process that this project owns: (turing_dir, port).

    `forbidden_ports` are other demos' servers on a shared box: the class refuses
    to touch them and asserts their pids did not change across start/stop.
    """

    def __init__(self, turing_dir: str | os.PathLike, port: int, host: str = "127.0.0.1",
                 binary: str | None = None, in_memory: bool = False, load: str | None = None,
                 start_timeout_ms: int = 10000, ui_port: int | None = None,
                 forbidden_ports: tuple[int, ...] = (), extra_args: tuple[str, ...] = (),
                 require_version: str | None = None):
        self.turing_dir = Path(turing_dir).expanduser().resolve()
        self.port = int(port)
        self.host = host
        self.binary = binary
        self.in_memory = in_memory
        self.load = load
        self.start_timeout_ms = start_timeout_ms
        self.ui_port = ui_port
        self.forbidden_ports = tuple(forbidden_ports)
        self.extra_args = tuple(extra_args)
        self.require_version = require_version
        self.adopted = False
        self.started = False

    # -- info
    @property
    def url(self) -> str:
        h = "127.0.0.1" if self.host in ("0.0.0.0", "") else self.host
        return f"http://{h}:{self.port}"

    @property
    def log_path(self) -> Path:
        return self.turing_dir / "logs" / "turingdb.log"

    def is_up(self) -> bool:
        return is_up(self.url)

    def owner(self) -> tuple[int, Path | None] | None:
        return port_owner(self.port)

    def is_ours(self) -> bool:
        o = self.owner()
        return bool(o and o[1] == self.turing_dir)

    def __repr__(self):
        return f"TdbServer({str(self.turing_dir)!r}, {self.port})"

    # -- guards
    def _guard(self):
        if self.port in self.forbidden_ports:
            raise TdbServerError(f"refusing port {self.port}: it belongs to another demo "
                                 f"(forbidden {self.forbidden_ports})")

    def _others(self) -> dict[int, int | None]:
        return {p: _pid_on(p) for p in self.forbidden_ports}

    def _assert_ours_or_free(self) -> int | None:
        o = self.owner()
        if o is None:
            if self.is_up():   # answers but no visible pid: tunnel, container, other user
                raise TdbServerError(
                    f"{self.url} answers but no local process owns :{self.port} (ssh tunnel? docker?). "
                    "Refusing to adopt it.")
            return None
        pid, theirs = o
        if theirs == self.turing_dir:
            return pid
        raise TdbServerError(
            f"port {self.port} is held by pid {pid}, which is NOT this project's server:\n"
            f"  its  -turing-dir: {theirs}\n  ours -turing-dir: {self.turing_dir}\n"
            f"  cmdline: {cmdline_of(pid)}\n"
            "Refusing to adopt it (we would write graphs into its data dir). Pick another port "
            "(free_port()) or stop that server yourself if it really is disposable.")

    def _tail_log(self, n: int = 15) -> str:
        try:
            return "\n".join(self.log_path.read_text(errors="replace").splitlines()[-n:])
        except OSError:
            return "(no log)"

    # -- lifecycle
    def start(self, timeout: float = 60.0) -> "TdbServer":
        """Start (or adopt our own already-running) server and wait until it answers."""
        self._guard()
        if self._assert_ours_or_free() is not None:
            self.adopted = True
            return self
        check_version(self.binary, self.require_version)
        before = self._others()
        self.turing_dir.mkdir(parents=True, exist_ok=True)
        if self.turing_dir.exists() and not (self.turing_dir / "graphs").is_dir():
            for d in SERVER_DIRS:
                (self.turing_dir / d).mkdir(parents=True, exist_ok=True)
        cmd = [find_binary(self.binary), "start", "-turing-dir", str(self.turing_dir),
               "-p", str(self.port), "-i", self.host, "-demon",
               "-start-timeout", str(self.start_timeout_ms)]
        if self.in_memory:
            cmd.append("-in-memory")
        if self.load:
            cmd += ["-load", self.load]
        if self.ui_port:
            cmd += ["-ui", "-ui-port", str(self.ui_port)]
        cmd += list(self.extra_args)
        env = dict(os.environ)
        if self.ui_port and sys.platform == "darwin":
            env.setdefault("OBJC_DISABLE_INITIALIZE_FORK_SAFETY", "YES")
        # stdin from /dev/null + new session: a foreground server exits when stdin hits
        # EOF, and the daemon must outlive the caller's SSH session / API process.
        # Output goes to a file, not a pipe: a daemon that inherits the pipe would keep
        # subprocess.run waiting for EOF.
        (self.turing_dir / "logs").mkdir(parents=True, exist_ok=True)
        out_path = self.turing_dir / "logs" / "start.out"
        with open(out_path, "w") as out:
            rc = subprocess.run(cmd, stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT,
                                env=env, start_new_session=True).returncode
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.is_ours() and self.is_up():
                break
            time.sleep(0.25)
        else:
            raise TdbServerError(
                f"TuringDB did not come up on :{self.port} (dir {self.turing_dir}); start exited "
                f"{rc}\n{out_path.read_text(errors='replace')[-800:]}\n--- {self.log_path}:\n{self._tail_log()}")
        if self._others() != before:
            raise TdbServerError("another demo's server changed pid during our start -- aborting")
        if self.ui_port:
            # start prints "ready" even when -ui-port is taken; only the log says Bind failed
            log = self._tail_log(60)
            if "Address already in use" in log or "Failed to initialize visualizer" in log:
                raise TdbServerError(f"visualizer proxy could not bind :{self.ui_port}\n{log}")
        self.started = True
        return self

    def stop(self, timeout: float = 60.0) -> None:
        """Stop OUR server (never a stranger's) and verify the port is released."""
        self._guard()
        pid = self._assert_ours_or_free()
        if pid is None:
            return
        before = self._others()
        # -timeout: the shutdown dump of a big graph outlasts the CLI's 3 s default
        r = subprocess.run([find_binary(self.binary), "stop", "-turing-dir", str(self.turing_dir),
                            "-timeout", str(int(timeout * 1000))],
                           capture_output=True, text=True, stdin=subprocess.DEVNULL)
        deadline = time.time() + timeout
        while time.time() < deadline:
            if _pid_on(self.port) is None and not self.is_up():
                break
            time.sleep(0.25)
        else:
            detail = (r.stderr or r.stdout or "").strip().splitlines()
            raise TdbServerError(
                f"failed to stop :{self.port} (pid {pid}, dir {self.turing_dir}); `turingdb stop` exited "
                f"{r.returncode}{': ' + detail[-1] if detail else ''}. It is still running, so a later "
                f"start would ADOPT it. If its data dir was deleted while it ran: kill {pid}")
        if self._others() != before:
            raise TdbServerError("another demo's server changed pid during our stop -- check it")
        self.started = False

    def kill(self) -> None:
        """Last resort for OUR pid only (e.g. its turing-dir was deleted under it)."""
        pid = self._assert_ours_or_free()
        if pid:
            os.kill(pid, signal.SIGTERM)

    def restart(self, timeout: float = 60.0) -> "TdbServer":
        self.stop(timeout)
        return self.start(timeout)

    # -- graphs
    def query(self, cypher: str, graph: str = "", timeout: float = 120.0) -> dict:
        body = query_raw(self.url, cypher, graph=graph, timeout=timeout)
        if body.get("error"):
            raise TdbServerError(f"{body['error']}: {body.get('error_details', '')} :: {cypher[:200]}")
        return body

    def available_graphs(self) -> dict[str, bool]:
        """{name: isLoaded} from LIST AVAILABLE GRAPHS (in-memory graphs are NOT listed)."""
        d = self.query("LIST AVAILABLE GRAPHS")["data"]
        if not d:
            return {}
        names, loaded = d[0][0], d[0][1]
        return dict(zip(names, loaded))

    def load_graph(self, name: str) -> None:
        """LOAD GRAPH, idempotent (an already-loaded graph is not an error here)."""
        body = query_raw(self.url, f"LOAD GRAPH {name}", timeout=600)
        err = (body.get("error") or "") + " " + (body.get("error_details") or "")
        if body.get("error") and "already" not in err.lower():
            raise TdbServerError(f"LOAD GRAPH {name}: {err.strip()}")

    def load_graphs(self, names: list[str] | None = None, skip: tuple[str, ...] = ("default",)) -> list[str]:
        """Load every on-disk graph (or `names`). Call after every restart."""
        names = names if names is not None else [n for n in self.available_graphs() if n not in skip]
        for n in names:
            self.load_graph(n)
        return names

    def graph_dir(self, name: str) -> Path:
        return self.turing_dir / "graphs" / name

    def drop_graph(self, name: str, staged: tuple[str, ...] = ()) -> None:
        """No DROP GRAPH in 3.0: stop -> rm graphs/<name> (+ vector/<name>, staged data files) -> start
        -> reload the sibling graphs. Never removes anything with the server running."""
        if not name or "/" in name or name in (".", ".."):
            raise TdbServerError(f"bad graph name {name!r}")
        was_up = self.is_ours()
        siblings = [n for n in (self.available_graphs() if was_up else {}) if n not in ("default", name)]
        if was_up:
            self.stop()
        for p in (self.graph_dir(name), self.turing_dir / "vector" / name,
                  *(self.turing_dir / "data" / s for s in staged)):
            if p.is_dir():
                shutil.rmtree(p)
            elif p.exists():
                p.unlink()
        if was_up:
            self.start()
            self.load_graphs(siblings)

    def rebuild_graph(self, name: str, build, staged: tuple[str, ...] = ()):
        """drop_graph + start + build(url) (your loader: LOAD JSONL, a builder script...)."""
        self.drop_graph(name, staged=staged)
        if not self.is_ours():
            self.start()
        return build(self.url)

    # -- context manager: stop only what we started (never an adopted server)
    def __enter__(self):
        return self.start()

    def __exit__(self, *exc):
        if self.started and not self.adopted:
            self.stop()
        return False
