#!/data/data/com.termux/files/usr/bin/bash

set -euo pipefail

PHONE_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
APP_ROOT="$(cd -- "$PHONE_DIR/.." && pwd)"
PHONE_VENV="$APP_ROOT/.venv-phone"
PHONE_ENV_FILE="$APP_ROOT/.env"
PHONE_STATE_DIR="$APP_ROOT/var/phone"
PHONE_PID_FILE="$PHONE_STATE_DIR/server.pid"
PHONE_LOG_FILE="$PHONE_STATE_DIR/server.log"
PHONE_PORT="${PHONE_PORT:-8000}"
PHONE_URL="http://127.0.0.1:$PHONE_PORT/ui/overview"
PHONE_PGDATA="${PHONE_PGDATA:-${PREFIX:?Termux PREFIX is not set}/var/lib/postgresql}"

phone_load_env() {
    if [[ ! -f "$PHONE_ENV_FILE" ]]; then
        echo "Не найден $PHONE_ENV_FILE. Сначала запустите phone/install.sh." >&2
        return 1
    fi
    set -a
    # shellcheck disable=SC1090
    source "$PHONE_ENV_FILE"
    set +a
}

phone_start_database() {
    if ! pg_ctl -D "$PHONE_PGDATA" status >/dev/null 2>&1; then
        mkdir -p "$PHONE_STATE_DIR"
        pg_ctl -D "$PHONE_PGDATA" -l "$PHONE_STATE_DIR/postgresql.log" start
    fi
}

phone_server_running() {
    [[ -f "$PHONE_PID_FILE" ]] || return 1
    local server_pid
    server_pid="$(<"$PHONE_PID_FILE")"
    [[ "$server_pid" =~ ^[0-9]+$ ]] && kill -0 "$server_pid" 2>/dev/null
}
