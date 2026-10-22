#!/usr/bin/env bash
# Ежедневный бэкап всех баз кластера postgres из compose (основная и dagster).
# Запуск из любой директории; нужен только docker и запущенный сервис postgres.
#
# Дампы: $BACKUP_DIR/<база>-<дата>-<время>.dump (pg_dump -Fc), по умолчанию
# var/backups/daily. Дамп пишется во временный .part и переименовывается только после
# того, как pg_restore --list его прочитал: пустого или битого файла под настоящим
# именем не остаётся. Дампы старше BACKUP_KEEP_DAYS (14) дней удаляются.
#
# Расписание: scripts/install_backup_timer.sh.
# Восстановление: pg_restore -U <user> -d <пустая база> --no-owner <файл>.dump
set -euo pipefail
cd "$(dirname "$0")/.."

DIR=${BACKUP_DIR:-var/backups/daily}
KEEP_DAYS=${BACKUP_KEEP_DAYS:-14}
mkdir -p "$DIR"

PG=$(docker compose ps -q postgres </dev/null)
[ -n "$PG" ] || { echo "СТОП: сервис postgres не запущен, бэкапа нет." >&2; exit 1; }

# Не шаблоны и не служебная база postgres: то, что создали мы.
DBS=$(docker exec "$PG" sh -c \
  'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atc "select datname from pg_database where not datistemplate and datname <> '"'postgres'"' order by 1"' </dev/null)
[ -n "$DBS" ] || { echo "СТОП: баз для бэкапа не найдено." >&2; exit 1; }

STAMP=$(date +%F-%H%M)
TMP=""
trap '[ -z "$TMP" ] || rm -f "$TMP"' EXIT

for DB in $DBS; do
  OUT="$DIR/$DB-$STAMP.dump"
  TMP="$OUT.part"
  docker exec "$PG" sh -c 'pg_dump -U "$POSTGRES_USER" -Fc "$1"' sh "$DB" </dev/null > "$TMP"
  [ -s "$TMP" ] || { echo "СТОП: дамп $DB пустой." >&2; exit 1; }
  docker exec -i "$PG" pg_restore --list < "$TMP" >/dev/null \
    || { echo "СТОП: дамп $DB не читается pg_restore." >&2; exit 1; }
  mv "$TMP" "$OUT"
  TMP=""
  echo "ok: $OUT ($(du -h "$OUT" | cut -f1))"
done

find "$DIR" -maxdepth 1 -name '*.dump' -mtime +"$KEEP_DAYS" -print -delete \
  | sed 's/^/удалён старый: /'
