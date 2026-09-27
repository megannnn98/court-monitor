#!/data/data/com.termux/files/usr/bin/bash

set -euo pipefail

PHONE_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=phone/lib.sh
source "$PHONE_DIR/lib.sh"

if ! phone_server_running; then
    rm -f "$PHONE_PID_FILE"
    exit 0
fi

server_pid="$(<"$PHONE_PID_FILE")"
kill "$server_pid"
for _attempt in {1..20}; do
    if ! kill -0 "$server_pid" 2>/dev/null; then
        rm -f "$PHONE_PID_FILE"
        exit 0
    fi
    sleep 1
done

echo "Сервер не остановился за 20 секунд (PID $server_pid)." >&2
exit 1
