#!/bin/sh
set -e

# Find the Archipelago multiworld package from the output directory.
# Archipelago generates AP_*.zip packages; .archipelago is an older extension.
# ARCHIPELAGO_OUTPUT_DIR can override the default when the workspace is mounted
# as a named volume (the runner passes the session-specific subpath via this var).
GAME_DIR="${ARCHIPELAGO_OUTPUT_DIR:-/archipelago/output}"
GAME_FILE=$(ls "$GAME_DIR"/*.zip "$GAME_DIR"/*.archipelago 2>/dev/null | head -1)

if [ -z "$GAME_FILE" ]; then
    echo '{"event":"no game file found in '"$GAME_DIR"'","severity":"ERROR","run_id":"","timestamp":""}' >&2
    exit 1
fi

# Start Archipelago server in background. Server options are env-overridable per
# session (see epic 27): release/collect default to "disabled" (AP's built-in default
# is "auto", which auto-releases/collects on goal); the other defaults below match
# Archipelago's own defaults, so an unset env changes nothing.
set -- "$GAME_FILE" \
    --host 0.0.0.0 \
    --port 38281 \
    --password "${PASSWORD:-}" \
    --server_password "${SERVER_PASSWORD:-}" \
    --release_mode "${RELEASE_MODE:-disabled}" \
    --collect_mode "${COLLECT_MODE:-disabled}" \
    --remaining_mode "${REMAINING_MODE:-goal}" \
    --countdown_mode "${COUNTDOWN_MODE:-auto}" \
    --hint_cost "${HINT_COST:-10}" \
    --location_check_points "${LOCATION_CHECK_POINTS:-1}" \
    --auto_shutdown "${AUTO_SHUTDOWN:-0}" \
    --compatibility "${COMPATIBILITY:-2}"

# --disable_item_cheat is a presence-only flag: add it only when explicitly enabled.
case "${DISABLE_ITEM_CHEAT:-}" in
    1 | true | yes | on) set -- "$@" --disable_item_cheat ;;
esac

ArchipelagoServer "$@" &
echo "$!" > "${AP_PID_FILE:-/tmp/ap.pid}"

# Wait briefly for the server to initialize before Bridge.py connects
sleep 3

# Start Bridge.py - stdout+stderr go to container logs (docker logs)
python -u /bridge/bridge.py &

# Wait for any child to exit (restart policy handled by Docker)
wait
