#!/usr/bin/env bash
# Ежедневный бэкап всех баз кластера postgres из compose (основная и dagster).
# Запуск из любой директории; нужен только docker и запущенный сервис postgres.
#
# Дампы: $BACKUP_DIR/<база>-<дата>-<время>.dump (pg_dump -Fc), по умолчанию
# var/backups/daily. Дамп пишется во временный .part и переименовывается только после
# того, как pg_restore --list прочитал его оглавление: пустого или нечитаемого файла под
# настоящим именем не остаётся (блоки данных этой проверкой не читаются).
# Сбой одной базы не прерывает остальные и не отключает ротацию: после цикла дампы и .part
# старше BACKUP_KEEP_DAYS (14) дней удаляются, кроме последнего дампа каждой базы; код
# выхода при сбое ненулевой. Если postgres не поднялся (выход до дампов), ротации нет.
#
# Сразу после загрузки (таймер Persistent) docker и postgres могут быть ещё не готовы:
# скрипт ждёт до 10 минут, а не падает и не ждёт следующих суток.
#
# Расписание: scripts/install_backup_timer.sh.
# Восстановление: pg_restore -U <user> -d <пустая база> --no-owner <файл>.dump
set -euo pipefail
umask 077
cd "$(dirname "$0")/.."

DIR=${BACKUP_DIR:-var/backups/daily}
KEEP_DAYS=${BACKUP_KEEP_DAYS:-14}
mkdir -p "$DIR"

PG=""
for _ in $(seq 1 60); do
  PG=$(docker compose ps -q postgres </dev/null 2>/dev/null || true)
  if [ -n "$PG" ] && docker exec "$PG" sh -c 'pg_isready -U "$POSTGRES_USER" -d "$POSTGRES_DB"' </dev/null >/dev/null 2>&1; then
    break
  fi
  PG=""
  sleep 10
done
[ -n "$PG" ] || { echo "СТОП: postgres не готов за 10 минут, бэкапа нет." >&2; exit 1; }

# Не шаблоны и не служебная база postgres: то, что создали мы.
DBS=$(docker exec "$PG" sh -c \
  'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atc "select datname from pg_database where not datistemplate and datname <> '"'postgres'"' order by 1"' </dev/null)
[ -n "$DBS" ] || { echo "СТОП: баз для бэкапа не найдено." >&2; exit 1; }

STAMP=$(date +%F-%H%M)
TMP=""
trap '[ -z "$TMP" ] || rm -f "$TMP"' EXIT

dump_one() {
  local db=$1
  local out="$DIR/$db-$STAMP.dump"
  TMP="$out.part"
  docker exec "$PG" sh -c 'pg_dump -U "$POSTGRES_USER" -Fc "$1"' sh "$db" </dev/null > "$TMP" \
    || { echo "СТОП: pg_dump $db завершился ошибкой." >&2; return 1; }
  [ -s "$TMP" ] || { echo "СТОП: дамп $db пустой." >&2; return 1; }
  docker exec -i "$PG" pg_restore --list < "$TMP" >/dev/null \
    || { echo "СТОП: дамп $db не читается pg_restore." >&2; return 1; }
  # Вызов через `dump_one || ...` отключает set -e внутри функции: ошибки проверяем сами.
  mv "$TMP" "$out" \
    || { echo "СТОП: дамп $db не удалось переименовать." >&2; return 1; }
  TMP=""
  echo "ok: $out ($(du -h "$out" | cut -f1))"
}

FAILED=0
for DB in $DBS; do
  dump_one "$DB" || { rm -f "$DIR/$DB-$STAMP.dump.part"; TMP=""; FAILED=1; }
done

# Последний дамп каждой базы не удаляется никогда: если новые дампы долго не получаются,
# старая рабочая копия остаётся.
KEEP=()
for DB in $DBS; do
  NEWEST=$(ls -t "$DIR/$DB"-*.dump 2>/dev/null | head -n 1 || true)
  [ -z "$NEWEST" ] || KEEP+=(! -path "$NEWEST")
done

find "$DIR" -maxdepth 1 -type f \( -name '*.dump' -o -name '*.dump.part' \) \
  -mmin +"$((KEEP_DAYS * 24 * 60))" "${KEEP[@]}" -print -delete \
  | sed 's/^/удалён старый: /'

[ "$FAILED" = 0 ] || { echo "СТОП: часть баз не сохранена, см. выше." >&2; exit 1; }
