#!/bin/sh
set -eu
mode="${1:-server}"
case "$mode" in
  server)
    exec ballpark runtime-server --state-dir "$BALLPARK_STATE_DIR" --publication-dir "$BALLPARK_PUBLICATION_DIR" --web-dir /app/web/dist --host "${BALLPARK_HOST:-0.0.0.0}" --port "${PORT:-8080}"
    ;;
  worker)
    exec ballpark runtime-worker --state-dir "$BALLPARK_STATE_DIR" --cache-dir "$BALLPARK_CACHE_DIR" --publication-dir "$BALLPARK_PUBLICATION_DIR"
    ;;
  probe)
    exec ballpark runtime-probe --state-dir "$BALLPARK_STATE_DIR"
    ;;
  *)
    echo "usage: docker-entrypoint.sh [server|worker|probe]" >&2
    exit 2
    ;;
esac
