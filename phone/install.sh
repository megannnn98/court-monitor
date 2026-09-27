#!/data/data/com.termux/files/usr/bin/bash

set -euo pipefail

PGVECTOR_VERSION=v0.8.6
PHONE_REPO_URL="${PHONE_REPO_URL:-https://github.com/megannnn98/court-monitor.git}"
PHONE_APP_DIR="${PHONE_APP_DIR:-${HOME:?HOME is not set}/court-monitor}"

usage() {
    echo "Использование: phone/install.sh [путь-к-dump]"
}

if [[ "${1:-}" == "--help" ]]; then
    usage
    exit 0
fi
if [[ -z "${TERMUX_VERSION:-}" ]] || ! command -v pkg >/dev/null 2>&1; then
    echo "Скрипт запускается только в Termux." >&2
    exit 1
fi

pkg update -y
pkg install -y python postgresql clang make git rclone termux-api curl
if ! python -c 'import sys; raise SystemExit(sys.version_info < (3, 13))'; then
    echo "Нужен Python 3.13+; обновите пакеты Termux." >&2
    exit 1
fi

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
SOURCE_ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd)"
if [[ -d "$SOURCE_ROOT/.git" ]]; then
    APP_ROOT="$SOURCE_ROOT"
elif [[ -d "$PHONE_APP_DIR/.git" ]]; then
    APP_ROOT="$PHONE_APP_DIR"
else
    git clone "$PHONE_REPO_URL" "$PHONE_APP_DIR"
    exec "$PHONE_APP_DIR/phone/install.sh" "$@"
fi
PHONE_DIR="$APP_ROOT/phone"
PHONE_STATE_DIR="$APP_ROOT/var/phone"
PHONE_VENV="$APP_ROOT/.venv-phone"
PHONE_ENV_FILE="$APP_ROOT/.env"
PHONE_PGDATA="${PHONE_PGDATA:-${PREFIX:?Termux PREFIX is not set}/var/lib/postgresql}"
dump_path="${1:-}"

mkdir -p "$PHONE_STATE_DIR"
termux-info >"$PHONE_STATE_DIR/termux-info.txt"
android_api="$(getprop ro.build.version.sdk)"
if [[ ! "$android_api" =~ ^[0-9]+$ ]] || (( android_api < 28 )); then
    echo "Нужен Android 9+ (API 28+); найден API ${android_api:-unknown}." >&2
    exit 1
fi
memory_kib="$(awk '/^MemTotal:/ {print $2}' /proc/meminfo)"
if [[ "$memory_kib" =~ ^[0-9]+$ ]] && (( memory_kib < 6000000 )); then
    echo "Предупреждение: памяти меньше ориентира 8 ГБ; долгие шаги могут завершаться ОС." >&2
fi

vector_control="$(pg_config --sharedir)/extension/vector.control"
if [[ ! -f "$vector_control" ]]; then
    pgvector_build_dir="$(mktemp -d)"
    trap 'rm -rf "$pgvector_build_dir"' EXIT
    git clone --depth 1 --branch "$PGVECTOR_VERSION" \
        https://github.com/pgvector/pgvector.git "$pgvector_build_dir/pgvector"
    make -C "$pgvector_build_dir/pgvector"
    make -C "$pgvector_build_dir/pgvector" install
    rm -rf "$pgvector_build_dir"
    trap - EXIT
fi

if [[ ! -s "$PHONE_PGDATA/PG_VERSION" ]]; then
    initdb --pgdata="$PHONE_PGDATA" --auth=trust
fi
if ! pg_ctl -D "$PHONE_PGDATA" status >/dev/null 2>&1; then
    pg_ctl -D "$PHONE_PGDATA" -l "$PHONE_STATE_DIR/postgresql.log" start
fi

if ! psql --dbname=postgres --tuples-only --no-align \
    --command="SELECT 1 FROM pg_roles WHERE rolname = 'court_monitor'" | grep -qx 1; then
    createuser --login court_monitor
fi
database_created=0
if ! psql --dbname=postgres --tuples-only --no-align \
    --command="SELECT 1 FROM pg_database WHERE datname = 'court_monitor'" | grep -qx 1; then
    createdb --owner=court_monitor court_monitor
    database_created=1
fi
if [[ -n "$dump_path" ]]; then
    if [[ ! -f "$dump_path" ]]; then
        echo "Дамп не найден: $dump_path" >&2
        exit 1
    fi
    if (( database_created == 0 )); then
        echo "База уже существует; автоматическое восстановление поверх неё запрещено." >&2
        exit 1
    fi
    psql --dbname=postgres --command="ALTER ROLE court_monitor SUPERUSER"
    if ! pg_restore \
        --dbname=court_monitor --role=court_monitor --no-owner --no-privileges "$dump_path"; then
        psql --dbname=postgres --command="ALTER ROLE court_monitor NOSUPERUSER"
        exit 1
    fi
    psql --dbname=postgres --command="ALTER ROLE court_monitor NOSUPERUSER"
fi
psql --dbname=court_monitor --command="CREATE EXTENSION IF NOT EXISTS vector"
psql --dbname=court_monitor --command="CREATE EXTENSION IF NOT EXISTS pg_trgm"

python -m venv "$PHONE_VENV"
"$PHONE_VENV/bin/python" -m pip install --upgrade pip
"$PHONE_VENV/bin/python" -m pip install -r "$PHONE_DIR/requirements.txt"

if [[ ! -f "$PHONE_ENV_FILE" ]]; then
    read -r -s -p "OPENROUTER_API_KEY: " openrouter_key
    echo
    if [[ -z "$openrouter_key" ]]; then
        echo "Ключ не может быть пустым." >&2
        exit 1
    fi
    umask 077
    {
        printf '%s\n' \
            'DATABASE_URL=postgresql+psycopg://court_monitor@127.0.0.1:5432/court_monitor'
        printf 'OPENROUTER_API_KEY=%q\n' "$openrouter_key"
        printf '%s\n' \
            'PERSON_EXTRACTION_STRATEGY=rule_based' \
            'ENTITY_REVIEW_PROVIDER=none' \
            'JUNK_SCREEN=0' \
            'DATABASE_POOL_SIZE=2' \
            'DATABASE_MAX_OVERFLOW=1'
    } >"$PHONE_ENV_FILE"
fi

set -a
# shellcheck disable=SC1090
source "$PHONE_ENV_FILE"
set +a
cd "$APP_ROOT"
"$PHONE_VENV/bin/python" -m alembic upgrade head

mkdir -p "${HOME:?HOME is not set}/.shortcuts"
printf '#!/data/data/com.termux/files/usr/bin/bash\nexec %q\n' \
    "$APP_ROOT/phone/run.sh" >"$HOME/.shortcuts/court-monitor"
chmod 700 "$HOME/.shortcuts" "$HOME/.shortcuts/court-monitor"

echo "Установка завершена. Запуск: $APP_ROOT/phone/run.sh"
