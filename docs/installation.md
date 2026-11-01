# Linux installation

Ensemble supports a standard Linux host installation under systemd. It does not require containers or PostgreSQL.

## Requirements

- Linux with systemd
- Python 3.12 or newer
- tar, runuser, useradd, groupadd and systemctl
- Network access during installation to install Python dependencies

## Install

From a checkout of this repository:

```bash
sudo bash ./scripts/install.sh
```

The installer creates a dedicated ensemble system user, installs the application under /opt/ensemble/app, creates a virtual environment under /opt/ensemble/venv, creates /etc/ensemble/ensemble.env if it does not exist, initializes the SQLite schema as the service user, and enables ensemble.service.

The installer does not overwrite an existing environment file or remove persistence data. Re-running it upgrades the application and applies pending migrations.

## Configure listener and persistence

Edit /etc/ensemble/ensemble.env:

```ini
ENSEMBLE_HOST=127.0.0.1
ENSEMBLE_PORT=8090
ENSEMBLE_PERSISTENCE_ROOT=/var/lib/ensemble
```

- ENSEMBLE_HOST controls the bind address. The default is loopback-only.
- ENSEMBLE_PORT controls the HTTP port. The default is 8090.
- ENSEMBLE_PERSISTENCE_ROOT is the root for durable data. The default is /var/lib/ensemble for installed deployments and ./.ensemble for a source checkout.
- ENSEMBLE_DATABASE_URL is an optional SQLite URL override. If unset, the database is <persistence-root>/ensemble.db. Non-SQLite URLs are rejected.

Command-line options to python -m ensemble override the corresponding environment values: --host, --port, --persistence-root and --database-url.

Restart after changing settings:

```bash
sudo systemctl restart ensemble
sudo systemctl status ensemble
```

The effective settings are available at /api/v1/system/config. Health and readiness are available at /health and /ready.

## Operations

```bash
sudo journalctl -u ensemble -f
sudo systemctl stop ensemble
sudo systemctl start ensemble
```

Back up the configured persistence root while Ensemble is stopped, or use a SQLite-consistent backup procedure. Do not blindly copy a live SQLite database file.
