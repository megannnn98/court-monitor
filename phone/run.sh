#!/data/data/com.termux/files/usr/bin/bash

set -euo pipefail

PHONE_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=phone/lib.sh
source "$PHONE_DIR/lib.sh"

phone_load_env
phone_start_database
mkdir -p "$PHONE_STATE_DIR"

if ! phone_server_running; then
    rm -f "$PHONE_PID_FILE"
    cd "$APP_ROOT"
    nohup "$PHONE_VENV/bin/python" -m uvicorn api:app \
        --app-dir "$APP_ROOT/src" --host 127.0.0.1 --port "$PHONE_PORT" \
        >>"$PHONE_LOG_FILE" 2>&1 &
    server_pid=$!
    printf '%s\n' "$server_pid" >"$PHONE_PID_FILE"
fi

for _attempt in {1..30}; do
    if curl --silent --fail --max-time 2 \
        "http://127.0.0.1:$PHONE_PORT/health/live" >/dev/null; then
        if command -v termux-open-url >/dev/null 2>&1; then
            termux-open-url "$PHONE_URL"
        else
            echo "$PHONE_URL"
        fi
        exit 0
    fi
    sleep 1
done

echo "Сервер не запустился. Лог: $PHONE_LOG_FILE" >&2
exit 1
