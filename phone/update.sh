#!/data/data/com.termux/files/usr/bin/bash

set -euo pipefail

PHONE_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=phone/lib.sh
source "$PHONE_DIR/lib.sh"

cd "$APP_ROOT"
mkdir -p "$PHONE_STATE_DIR"
if ! git diff --quiet || ! git diff --cached --quiet; then
    echo "Обновление остановлено: в репозитории есть локальные изменения." >&2
    exit 1
fi

git rev-parse HEAD >"$PHONE_STATE_DIR/rollback-commit"
git pull --ff-only
"$PHONE_VENV/bin/python" -m pip install --upgrade -r "$PHONE_DIR/requirements.txt"

phone_load_env
phone_start_database
"$PHONE_VENV/bin/python" -m alembic upgrade head
"$APP_ROOT/phone/stop.sh"
"$APP_ROOT/phone/run.sh"

echo "Обновлено. Commit для отката: $(<"$PHONE_STATE_DIR/rollback-commit")"
