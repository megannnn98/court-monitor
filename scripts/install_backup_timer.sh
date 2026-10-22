#!/usr/bin/env bash
# Ставит systemd-таймер ежедневного бэкапа (scripts/backup_db.sh).
#   scripts/install_backup_timer.sh user     рабочий компьютер, systemd --user
#   scripts/install_backup_timer.sh system   сервер (root), системный systemd
# Persistent=true: если машина в 03:30 была выключена, бэкап сделается при включении.
set -euo pipefail

MODE=${1:-}
REPO=$(cd "$(dirname "$0")/.." && pwd)

case "$MODE" in
  user)
    UNIT_DIR="$HOME/.config/systemd/user"
    CTL=(systemctl --user)
    ;;
  system)
    UNIT_DIR=/etc/systemd/system
    CTL=(systemctl)
    ;;
  *)
    echo "usage: $0 user|system" >&2
    exit 2
    ;;
esac

mkdir -p "$UNIT_DIR"
cat > "$UNIT_DIR/ebnv-backup.service" <<EOF
[Unit]
Description=Backup of court-monitor databases

[Service]
Type=oneshot
WorkingDirectory=$REPO
ExecStart=$REPO/scripts/backup_db.sh
EOF
cat > "$UNIT_DIR/ebnv-backup.timer" <<EOF
[Unit]
Description=Daily backup of court-monitor databases

[Timer]
OnCalendar=*-*-* 03:30:00
Persistent=true

[Install]
WantedBy=timers.target
EOF

"${CTL[@]}" daemon-reload
"${CTL[@]}" enable --now ebnv-backup.timer
"${CTL[@]}" list-timers ebnv-backup.timer
