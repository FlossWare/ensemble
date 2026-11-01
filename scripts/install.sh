#!/usr/bin/env bash
set -Eeuo pipefail

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run as root: sudo bash ./scripts/install.sh" >&2
  exit 1
fi

SOURCE_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
INSTALL_ROOT="${ENSEMBLE_INSTALL_ROOT:-/opt/ensemble}"
APP_DIR="${INSTALL_ROOT}/app"
VENV_DIR="${INSTALL_ROOT}/venv"
PERSISTENCE_ROOT="${ENSEMBLE_PERSISTENCE_ROOT:-/var/lib/ensemble}"
CONFIG_DIR="/etc/ensemble"
ENV_FILE="${CONFIG_DIR}/ensemble.env"
SERVICE_FILE="/etc/systemd/system/ensemble.service"
SERVICE_USER="ensemble"
SERVICE_GROUP="ensemble"

if command -v python3.12 >/dev/null 2>&1; then
  PYTHON="$(command -v python3.12)"
elif command -v python3 >/dev/null 2>&1 && python3 -c 'import sys; raise SystemExit(sys.version_info < (3, 12))'; then
  PYTHON="$(command -v python3)"
else
  echo "Python 3.12 or newer is required." >&2
  exit 1
fi
command -v systemctl >/dev/null 2>&1 || { echo "systemd is required." >&2; exit 1; }

getent group "${SERVICE_GROUP}" >/dev/null || groupadd --system "${SERVICE_GROUP}"
id "${SERVICE_USER}" >/dev/null 2>&1 || useradd --system --gid "${SERVICE_GROUP}" --home-dir "${PERSISTENCE_ROOT}" --shell /usr/sbin/nologin "${SERVICE_USER}"

install -d -o root -g root -m 0755 "${INSTALL_ROOT}" "${APP_DIR}"
install -d -o "${SERVICE_USER}" -g "${SERVICE_GROUP}" -m 0750 "${PERSISTENCE_ROOT}"
install -d -o root -g "${SERVICE_GROUP}" -m 0750 "${CONFIG_DIR}"

tar -C "${SOURCE_DIR}" --exclude='./.git' --exclude='./.venv' --exclude='./venv' --exclude='./.ensemble' --exclude='./.pytest_cache' --exclude='./__pycache__' -cf - . | tar -C "${APP_DIR}" -xf -
chown -R root:root "${APP_DIR}"

"${PYTHON}" -m venv "${VENV_DIR}"
"${VENV_DIR}/bin/python" -m pip install --upgrade pip
"${VENV_DIR}/bin/python" -m pip install "${APP_DIR}"

if [[ ! -f "${ENV_FILE}" ]]; then
  cat > "${ENV_FILE}" <<EOF
# Edit these values to configure the Ensemble server.
ENSEMBLE_HOST=127.0.0.1
ENSEMBLE_PORT=8090
ENSEMBLE_PERSISTENCE_ROOT=${PERSISTENCE_ROOT}
# Optional SQLite URL override. Normally the database is <persistence-root>/ensemble.db.
# ENSEMBLE_DATABASE_URL=sqlite+aiosqlite:////var/lib/ensemble/ensemble.db
EOF
fi
chown root:"${SERVICE_GROUP}" "${ENV_FILE}"
chmod 0640 "${ENV_FILE}"

set -a
# shellcheck disable=SC1090
. "${ENV_FILE}"
set +a
EFFECTIVE_ROOT="${ENSEMBLE_PERSISTENCE_ROOT:-${PERSISTENCE_ROOT}}"
install -d -o "${SERVICE_USER}" -g "${SERVICE_GROUP}" -m 0750 "${EFFECTIVE_ROOT}"
DATABASE_PARENT="$("${VENV_DIR}/bin/python" -c 'import os; from sqlalchemy.engine import make_url; from ensemble.config import resolve_database_url; u=make_url(resolve_database_url()); print(os.path.dirname(os.path.abspath(u.database)) if u.database not in (None, "", ":memory:") else "")')"
if [[ -n "${DATABASE_PARENT}" ]]; then
  install -d -o "${SERVICE_USER}" -g "${SERVICE_GROUP}" -m 0750 "${DATABASE_PARENT}"
fi

runuser -u "${SERVICE_USER}" -- env \
  ENSEMBLE_HOST="${ENSEMBLE_HOST:-127.0.0.1}" \
  ENSEMBLE_PORT="${ENSEMBLE_PORT:-8090}" \
  ENSEMBLE_PERSISTENCE_ROOT="${EFFECTIVE_ROOT}" \
  ENSEMBLE_DATABASE_URL="${ENSEMBLE_DATABASE_URL:-}" \
  "${VENV_DIR}/bin/alembic" -c "${APP_DIR}/alembic.ini" upgrade head

cat > "${SERVICE_FILE}" <<EOF
[Unit]
Description=FlossWare Ensemble REST platform
After=network.target

[Service]
Type=simple
User=${SERVICE_USER}
Group=${SERVICE_GROUP}
WorkingDirectory=${APP_DIR}
EnvironmentFile=${ENV_FILE}
ExecStart=${VENV_DIR}/bin/python -m ensemble
Restart=on-failure
RestartSec=5
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=${EFFECTIVE_ROOT}
EOF
if [[ -n "${DATABASE_PARENT}" && "${DATABASE_PARENT}" != "${EFFECTIVE_ROOT}" ]]; then
  echo "ReadWritePaths=${DATABASE_PARENT}" >> "${SERVICE_FILE}"
fi
cat >> "${SERVICE_FILE}" <<EOF
UMask=0027

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable --now ensemble.service

echo "Ensemble installed."
echo "Configuration: ${ENV_FILE}"
echo "Persistence:   ${EFFECTIVE_ROOT}"
echo "Service:       systemctl status ensemble"
echo "Logs:          journalctl -u ensemble -f"
