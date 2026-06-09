#!/bin/sh
set -e
GAME_DIR="${ARCHIPELAGO_OUTPUT_DIR:-/data/output}"
GAME_FILE=$(ls "$GAME_DIR"/*.zip "$GAME_DIR"/*.archipelago 2>/dev/null | head -1)
if [ -z "$GAME_FILE" ]; then
    echo "ERROR: no game file found in $GAME_DIR" >&2
    exit 1
fi

# Server options are env-overridable per session (the orchestrateur passes them; see
# epic 27). release/collect default to "disabled" — ArchipelagoServer's built-in default
# is "auto" (settings.py), which would auto-release/collect items on goal; weekly runs are
# individual competitive seeds, so force them off unless overridden. The other defaults
# below match Archipelago's own defaults, so an unset env changes nothing.
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

exec ArchipelagoServer "$@"
