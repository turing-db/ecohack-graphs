#!/usr/bin/env bash
# tdb.sh — shell twin of tc.tdb_server for run.sh / rebuild scripts (no python needed).
#   BIN=.venv/bin/turingdb DIR=.turing PORT=6694 tdb.sh up|down|status|drop <graph>|rebuild <graph> -- <loader cmd…>
# Rules: stop by -turing-dir only (never -p, never fuser -k / pkill); adopt a listener only if its
# -turing-dir is ours; no DROP GRAPH → stop, rm graphs/<g>, start; graphs need LOAD GRAPH after a restart.
set -euo pipefail
BIN=${BIN:-$(command -v turingdb)}; DIR=$(mkdir -p "${DIR:-.turing}" && cd "${DIR:-.turing}" && pwd)
PORT=${PORT:?PORT required}; LISTEN=${LISTEN:-127.0.0.1}; START_TIMEOUT=${START_TIMEOUT:-10000}
URL="http://127.0.0.1:$PORT"
die(){ echo "tdb.sh: $*" >&2; exit 1; }
up(){ curl -s -m 2 -X POST "$URL/query" -d 'LIST AVAILABLE GRAPHS' >/dev/null 2>&1; }
owner_pid(){ { if command -v lsof >/dev/null; then lsof -nP -iTCP:"$PORT" -sTCP:LISTEN -t 2>/dev/null
             else ss -ltnpH "sport = :$PORT" 2>/dev/null | sed -n 's/.*pid=\([0-9]*\).*/\1/p'; fi; } | head -1 || true; }
owner_dir(){ local pid=$1 args
  if [ -r /proc/$pid/cmdline ]; then args=$(tr '\0' ' ' < /proc/$pid/cmdline); else args=$(ps -ww -o args= -p "$pid"); fi
  echo "$args" | sed -n 's/.*-turing-dir \([^ ]*\).*/\1/p'; }
ours(){ local pid; pid=$(owner_pid); [ -n "$pid" ] || return 1
  local d; d=$(owner_dir "$pid"); [ -n "$d" ] && [ "$(cd "$d" 2>/dev/null && pwd)" = "$DIR" ]; }
guard(){ local pid; pid=$(owner_pid)
  if [ -n "$pid" ] && ! ours; then die "port $PORT held by pid $pid ($(owner_dir "$pid")), not $DIR — refusing"; fi
  if [ -z "$pid" ] && up; then die "$URL answers but no local process owns it (tunnel?) — refusing"; fi; }
start(){ guard; if ours; then echo "adopted $URL ($DIR)"; return; fi
  mkdir -p "$DIR"/{graphs,data,logs}
  local detach=setsid; command -v setsid >/dev/null || detach=nohup   # macOS has no setsid
  $detach bash -c "'$BIN' start -turing-dir '$DIR' -p $PORT -i $LISTEN -demon -start-timeout $START_TIMEOUT ${EXTRA:-} > '$DIR/logs/start.out' 2>&1" < /dev/null > /dev/null 2>&1 || true
  for _ in $(seq 1 240); do up && ours && { echo "started $URL ($DIR)"; return; }; sleep 0.25; done
  tail -20 "$DIR/logs/turingdb.log" >&2 || true; die "did not come up on :$PORT"; }
stop(){ guard; ours || { echo "not running"; return; }
  "$BIN" stop -turing-dir "$DIR" -timeout "${STOP_TIMEOUT_MS:-60000}" >/dev/null 2>&1 || true
  for _ in $(seq 1 240); do [ -z "$(owner_pid)" ] && ! up && { echo "stopped $URL"; return; }; sleep 0.25; done
  die "still running after stop (pid $(owner_pid)) — do NOT kill by port; inspect it"; }
load_all(){ for g in "$DIR"/graphs/*/; do g=$(basename "$g"); [ "$g" = default ] && continue
  curl -s -X POST "$URL/query" -d "LOAD GRAPH $g" >/dev/null; done; }
drop(){ local g=${1:?graph}; case "$g" in */*|.|..|'') die "bad graph name";; esac
  stop; rm -rf "${DIR:?}/graphs/$g" "${DIR:?}/vector/$g" "${DIR:?}/data/$g.jsonl"; start; load_all; }
case "${1:-}" in
  up) start; load_all;;
  down) stop;;
  status) pid=$(owner_pid); echo "url=$URL dir=$DIR pid=${pid:-none} owner_dir=${pid:+$(owner_dir "$pid")} up=$(up && echo yes || echo no)";;
  drop) drop "${2:-}";;
  rebuild) g=${2:?graph}; shift 2; [ "${1:-}" = "--" ] && shift; drop "$g"; TURING_HOST=$URL "$@";;
  *) die "usage: tdb.sh up|down|status|drop <g>|rebuild <g> -- <loader…>";;
esac
