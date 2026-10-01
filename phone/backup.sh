#!/data/data/com.termux/files/usr/bin/bash

set -euo pipefail

PHONE_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=phone/lib.sh
source "$PHONE_DIR/lib.sh"

phone_load_env
: "${PHONE_BACKUP_REMOTE:?Укажите PHONE_BACKUP_REMOTE в .env}"
: "${DATABASE_URL:?DATABASE_URL не задан в .env}"
PHONE_BACKUP_PATH="${PHONE_BACKUP_PATH:-court-monitor}"
PHONE_BACKUP_RETENTION="${PHONE_BACKUP_RETENTION:-14}"
if [[ ! "$PHONE_BACKUP_RETENTION" =~ ^[1-9][0-9]*$ ]]; then
    echo "PHONE_BACKUP_RETENTION должен быть положительным числом." >&2
    exit 1
fi

backup_dir="$APP_ROOT/var/backups"
mkdir -p "$backup_dir"
timestamp="$(date -u +%Y%m%d-%H%M%S)"
filename="court-monitor-$timestamp.dump"
local_dump="$backup_dir/$filename"
remote_dir="${PHONE_BACKUP_REMOTE%:}:$PHONE_BACKUP_PATH"
trap 'rm -f "$local_dump"' EXIT

# SQLAlchemy's driver-qualified URL is not understood by pg_dump.
dump_url="${DATABASE_URL//postgresql+psycopg:/postgresql:}"
dump_url="${dump_url//postgresql+psycopg2:/postgresql:}"
pg_dump -Fc --file="$local_dump" "$dump_url"
rclone copyto "$local_dump" "$remote_dir/$filename"

mapfile -t backups < <(
    rclone lsf --files-only "$remote_dir" \
        | grep -E '^court-monitor-[0-9]{8}-[0-9]{6}\.dump$' \
        | sort
)
if (( ${#backups[@]} > PHONE_BACKUP_RETENTION )); then
    stale_count=$((${#backups[@]} - PHONE_BACKUP_RETENTION))
    for stale in "${backups[@]:0:stale_count}"; do
        rclone deletefile "$remote_dir/$stale"
    done
fi

echo "event=phone_backup status=succeeded file=$filename retained=$PHONE_BACKUP_RETENTION"
