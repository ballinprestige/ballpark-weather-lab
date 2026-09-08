#!/bin/sh
set -eu
mode="${1:-server}"
if [ "$#" -gt 0 ]; then
  shift
fi
worker_command() {
  if [ -n "${BALLPARK_FIXTURE:-}" ]; then
    exec ballpark runtime-worker --state-dir "$BALLPARK_STATE_DIR" --cache-dir "$BALLPARK_CACHE_DIR" --publication-dir "$BALLPARK_PUBLICATION_DIR" --fixture "$BALLPARK_FIXTURE" --date "${BALLPARK_DATE:?BALLPARK_DATE is required with BALLPARK_FIXTURE}"
  else
    exec ballpark runtime-worker --state-dir "$BALLPARK_STATE_DIR" --cache-dir "$BALLPARK_CACHE_DIR" --publication-dir "$BALLPARK_PUBLICATION_DIR"
  fi
}
server_command() {
  exec ballpark runtime-server --state-dir "$BALLPARK_STATE_DIR" --cache-dir "$BALLPARK_CACHE_DIR" --publication-dir "$BALLPARK_PUBLICATION_DIR" --web-dir /app/web/dist --host "${BALLPARK_HOST:-127.0.0.1}" --port "${PORT:-8080}"
}
supervise() {
  worker_command & worker_pid=$!
  server_command & server_pid=$!
  terminate() {
    kill -TERM "$worker_pid" "$server_pid" 2>/dev/null || true
    attempts=0
    while [ "$attempts" -lt 10 ]; do
      if ! kill -0 "$worker_pid" 2>/dev/null && ! kill -0 "$server_pid" 2>/dev/null; then
        break
      fi
      attempts=$((attempts + 1))
      sleep 1
    done
    kill -KILL "$worker_pid" "$server_pid" 2>/dev/null || true
    wait "$worker_pid" 2>/dev/null || true
    wait "$server_pid" 2>/dev/null || true
  }
  trap 'terminate; exit 0' INT TERM
  while :; do
    if ! kill -0 "$worker_pid" 2>/dev/null || ! kill -0 "$server_pid" 2>/dev/null; then
      terminate
      exit 1
    fi
    sleep 1
  done
}
case "$mode" in
  service)
    supervise
    ;;
  server)
    server_command
    ;;
  worker)
    worker_command
    ;;
  probe)
    exec ballpark runtime-probe --state-dir "$BALLPARK_STATE_DIR"
    ;;
  monitor)
    : "${BALLPARK_RUNTIME_URL:?BALLPARK_RUNTIME_URL is required for monitor mode}"
    exec ballpark runtime-monitor --url "$BALLPARK_RUNTIME_URL" "$@"
    ;;
  backup)
    exec ballpark runtime-backup "$@"
    ;;
  verify-backup)
    exec ballpark runtime-verify-backup "$@"
    ;;
  restore)
    exec ballpark runtime-restore "$@"
    ;;
  *)
    echo "usage: docker-entrypoint.sh [service|server|worker|probe|monitor|backup|verify-backup|restore]" >&2
    exit 2
    ;;
esac
