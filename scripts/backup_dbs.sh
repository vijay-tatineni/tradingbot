#!/bin/bash
BACKUP_DIR="/root/trading/backups/$(date +%Y%m%d)"
mkdir -p "$BACKUP_DIR"

# Use sqlite3 .backup for consistency (not cp)
for db in learning_loop.db positions.db news.db; do
    if [ -f "/root/trading/$db" ]; then
        sqlite3 "/root/trading/$db" ".backup '$BACKUP_DIR/$db'"
        echo "Backed up $db -> $BACKUP_DIR/$db"
    fi
done

# Also backup instruments.json
cp /root/trading/instruments.json "$BACKUP_DIR/instruments.json"

# Keep only last 30 days
find /root/trading/backups -maxdepth 1 -type d -mtime +30 -exec rm -rf {} \;

echo "Backup complete: $BACKUP_DIR"
