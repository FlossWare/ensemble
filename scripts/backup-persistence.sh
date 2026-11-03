#!/usr/bin/env bash
set -euo pipefail

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run this script with sudo so ownership and service state are preserved." >&2
  exit 1
fi

ROOT="$(realpath -e "${ENSEMBLE_PERSISTENCE_ROOT:-/var/lib/ensemble}")"
SERVICE="${ENSEMBLE_SERVICE_NAME:-ensemble}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
ARCHIVE="$(realpath -m "${1:-$(dirname "$ROOT")/$(basename "$ROOT")-$STAMP.tar.gz}")"

case "$ARCHIVE" in
  "$ROOT"/*) echo "Backup archive must be outside the persistence root." >&2; exit 1 ;;
esac
if [[ -e "$ARCHIVE" ]]; then
  echo "Refusing to overwrite existing backup: $ARCHIVE" >&2
  exit 1
fi

if ! command -v systemctl >/dev/null 2>&1; then
  echo "systemctl is required for a consistent service backup." >&2
  exit 1
fi
WAS_ACTIVE=0
if systemctl is-active --quiet "$SERVICE"; then
  WAS_ACTIVE=1
  systemctl stop "$SERVICE"
fi
restart_service() {
  if [[ "$WAS_ACTIVE" -eq 1 ]]; then
    systemctl start "$SERVICE"
  fi
}
trap restart_service EXIT

python - "$ROOT" <<'PY'
import sqlite3
import sys
from pathlib import Path

root = Path(sys.argv[1])
databases = sorted(root.rglob("*.db"))
if not databases:
    raise SystemExit(f"No SQLite databases found beneath {root}")
for path in databases:
    connection = sqlite3.connect(path)
    try:
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        result = connection.execute("PRAGMA integrity_check").fetchone()[0]
        if result != "ok":
            raise SystemExit(f"SQLite integrity check failed for {path}: {result}")
    finally:
        connection.close()
print(f"Verified {len(databases)} SQLite databases.")
PY

mkdir -p "$(dirname "$ARCHIVE")"
tar --xattrs --acls --numeric-owner -czf "$ARCHIVE" -C "$(dirname "$ROOT")" "$(basename "$ROOT")"
tar -tzf "$ARCHIVE" >/dev/null
sha256sum "$ARCHIVE"
echo "Backup created: $ARCHIVE"
