#!/usr/bin/env bash
set -euo pipefail

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run this script with sudo so service state and ownership can be restored." >&2
  exit 1
fi
if [[ $# -lt 1 || $# -gt 2 ]]; then
  echo "Usage: sudo bash scripts/restore-persistence.sh ARCHIVE [PERSISTENCE_ROOT]" >&2
  exit 2
fi

ARCHIVE="$(realpath -e "$1")"
ROOT="$(realpath -m "${2:-${ENSEMBLE_PERSISTENCE_ROOT:-/var/lib/ensemble}}")"
PARENT="$(dirname "$ROOT")"
NAME="$(basename "$ROOT")"
SERVICE="${ENSEMBLE_SERVICE_NAME:-ensemble}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -p "$PARENT"
STAGE="$(mktemp -d "$PARENT/.${NAME}-restore.XXXXXX")"
OLD="$ROOT.pre-restore-$STAMP"
WAS_ACTIVE=0
SWAPPED=0

cleanup() {
  if [[ "$SWAPPED" -eq 1 ]]; then
    rm -rf "$ROOT"
    if [[ -d "$OLD" ]]; then mv "$OLD" "$ROOT"; fi
  fi
  rm -rf "$STAGE"
  if [[ "$WAS_ACTIVE" -eq 1 ]]; then
    systemctl start "$SERVICE" || true
  fi
}
trap cleanup EXIT

if tar -tzf "$ARCHIVE" | grep -Eq '(^/|(^|/)\.\.(/|$))'; then
  echo "Archive contains an unsafe path." >&2
  exit 1
fi
TOP="$(tar -tzf "$ARCHIVE" | sed -n '1{s#/.*##;p;}')"
if [[ "$TOP" != "$NAME" ]]; then
  echo "Archive root '$TOP' does not match target directory '$NAME'." >&2
  exit 1
fi

mkdir -p "$PARENT"
tar --no-same-owner -xzf "$ARCHIVE" -C "$STAGE"
RESTORED="$STAGE/$NAME"
if [[ ! -d "$RESTORED" ]]; then
  echo "Archive does not contain the expected persistence directory." >&2
  exit 1
fi

python - "$RESTORED" <<'PY'
import sqlite3
import sys
from pathlib import Path

root = Path(sys.argv[1])
databases = sorted(root.rglob("*.db"))
if not databases:
    raise SystemExit(f"No SQLite databases found beneath {root}")
for path in databases:
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        result = connection.execute("PRAGMA integrity_check").fetchone()[0]
        if result != "ok":
            raise SystemExit(f"SQLite integrity check failed for {path}: {result}")
        tables = {row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
        if "ensemble_partition_metadata" not in tables:
            raise SystemExit(f"Partition metadata is missing from {path}")
        partition = connection.execute(
            "SELECT partition_id FROM ensemble_partition_metadata LIMIT 1"
        ).fetchone()
        if partition is None or not partition[0]:
            raise SystemExit(f"Partition identity is missing from {path}")
    finally:
        connection.close()
print(f"Verified {len(databases)} SQLite databases and their partition markers.")
PY

if ! command -v systemctl >/dev/null 2>&1; then
  echo "systemctl is required to restore an installed Ensemble service." >&2
  exit 1
fi
if systemctl is-active --quiet "$SERVICE"; then
  WAS_ACTIVE=1
  systemctl stop "$SERVICE"
fi

if [[ -e "$ROOT" ]]; then
  if [[ -e "$OLD" ]]; then
    echo "Refusing to overwrite rollback directory: $OLD" >&2
    exit 1
  fi
  mv "$ROOT" "$OLD"
fi
if ! mv "$RESTORED" "$ROOT"; then
  if [[ -d "$OLD" ]]; then mv "$OLD" "$ROOT"; fi
  exit 1
fi
SWAPPED=1

if getent passwd ensemble >/dev/null 2>&1; then
  chown -R ensemble:ensemble "$ROOT"
fi
if [[ "$WAS_ACTIVE" -eq 1 ]]; then
  systemctl start "$SERVICE"
  WAS_ACTIVE=0
fi
SWAPPED=0
echo "Persistence restored to $ROOT"
if [[ -d "$OLD" ]]; then
  echo "Previous persistence root retained at $OLD"
fi
