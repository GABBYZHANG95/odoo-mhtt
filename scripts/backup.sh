#!/usr/bin/env bash
# Odoo 备份：数据库（pg_dump 自定义格式）+ 附件目录（filestore）。
# 用法: scripts/backup.sh [数据库名，默认 mhtt]
# 每次运行时自动清理 KEEP_DAYS 天（默认 14）前的备份。正式库上线后由 crontab 每天执行。
# 注意：备份只存在本机。服务器硬盘坏了备份也会一起丢，正式上线前要再加一份异地拷贝。
set -euo pipefail

DB="${1:-mhtt}"
BACKUP_DIR="${BACKUP_DIR:-$(cd "$(dirname "$0")/.." && pwd)/backups}"
KEEP_DAYS="${KEEP_DAYS:-14}"
TS="$(date +%Y%m%d_%H%M%S)"

mkdir -p "$BACKUP_DIR"
chmod 700 "$BACKUP_DIR"

dump="$BACKUP_DIR/${DB}_${TS}.dump"
files="$BACKUP_DIR/${DB}_${TS}_filestore.tar.gz"

# 先写 .part，成功后再改名，避免中途失败留下一个看起来完整的坏文件
docker exec mhtt-db pg_dump -U odoo -Fc "$DB" > "$dump.part"
mv "$dump.part" "$dump"

docker exec mhtt-app tar -czf - -C /var/lib/odoo/filestore "$DB" > "$files.part"
mv "$files.part" "$files"

find "$BACKUP_DIR" -maxdepth 1 -name "${DB}_*" -mtime +"$KEEP_DAYS" -delete

echo "$(date '+%F %T') OK  $(basename "$dump") ($(du -h "$dump" | cut -f1)), $(basename "$files") ($(du -h "$files" | cut -f1))"
