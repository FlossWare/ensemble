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
- ENSEMBLE_DATABASE_URL is an optional SQLite URL override. If unset, the database is <persistence-root>/ensemble.db. Non-SQLite URLs are rejected. For the systemd installer, the database file must remain beneath ENSEMBLE_PERSISTENCE_ROOT so the service can be safely confined to that directory.

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

## Configure authenticated partitions

The default installation listens on all interfaces (`0.0.0.0`) so LAN federation works without a separate bind-host change, and uses the existing `default` SQLite database. For this federation milestone, the default partition can be accessed over the configured LAN listener without authentication. This is intentionally simple and must not be exposed to untrusted networks. FlossWare proper is responsible for the production security model. To enable named partitions and token enforcement, create a high-entropy Bearer token for each principal and place only its SHA-256 digest in the service environment file. For example:

```bash
TOKEN="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"
printf '%s\\n' "$TOKEN"
printf '%s' "$TOKEN" | sha256sum
```

Keep the token secret for the client and store the digest in `ENSEMBLE_PARTITION_TOKENS`:

```ini
ENSEMBLE_AUTH_REQUIRED=1
ENSEMBLE_PARTITION_TOKENS={"<sha256-token-digest>":{"subject":"client-a","partitions":["workspace-a"]}}
```

The client sends `Authorization: Bearer <token>`. If its token authorizes multiple partitions, it also sends `X-Ensemble-Partition: <partition-id>`. The partition header is a selector, not an access credential. A token can list multiple authorized partitions.

The existing database remains the `default` partition. Named partitions are created as separate SQLite databases under `<database-directory>/partitions/` and are migrated automatically on first use. Back up the entire persistence root, including the `partitions` directory. Do not move or rename individual partition database files.

If the token map is configured, API requests without valid credentials are rejected. The health/readiness endpoints remain public. The Control Center's authenticated-session workflow is being completed separately; use authenticated REST clients for protected operations until that work is finished.

## Partition-aware backup and restore

Use the repository scripts rather than copying a live SQLite file. They stop the systemd service when it is active, checkpoint and integrity-check every database, and include the entire persistence root (the default database plus every named partition):

```bash
sudo bash scripts/backup-persistence.sh /path/to/backups/ensemble.tar.gz
sudo bash scripts/restore-persistence.sh /path/to/backups/ensemble.tar.gz
```

The backup script prints a SHA-256 checksum. The restore script validates archive paths, checks SQLite integrity and partition identity markers before replacing anything, and retains the previous persistence root as a rollback directory. If using a custom persistence root or service name, set `ENSEMBLE_PERSISTENCE_ROOT` and `ENSEMBLE_SERVICE_NAME` for the command. Keep backups outside the persistence root.


## Federation deployment

LAN peer discovery and presence broadcasting are implemented and enabled by default. Each instance announces its identity, HTTP endpoint, protocol version, and capability identifiers over IPv4 multicast. The Control Center displays discovered peers and retains recently expired peers as offline.

Configure each LAN instance with a reachable listener. For example:

```ini
ENSEMBLE_HOST=0.0.0.0
ENSEMBLE_PORT=8090
ENSEMBLE_NAME=server-01
ENSEMBLE_FEDERATION_ENABLED=true
ENSEMBLE_FEDERATION_GROUP=239.255.76.76
ENSEMBLE_FEDERATION_PORT=47676
ENSEMBLE_FEDERATION_HEARTBEAT_SECONDS=30
ENSEMBLE_FEDERATION_TTL_SECONDS=90
```

A non-loopback listener is currently allowed to use the default unauthenticated `default` partition so LAN federation can work without setup. This is intentionally a development/home-LAN posture, not a production security boundary. Use a distinct `ENSEMBLE_NAME` per instance. Ensure the host firewall permits the configured UDP multicast traffic and the advertised HTTP endpoint between the instances. Multicast normally does not cross routers or VLAN boundaries; routed/VPN peer configuration is not implemented yet.

The local identity is generated once and stored as `instance-id` beneath the persistence root. Do not copy that file when cloning an instance; each deployment needs its own identity. The discovery registry is currently in memory, so peers are rediscovered after restart.

Implemented endpoints:

- `GET /federation/status` returns local identity, discovery state, and the peer list.
- `GET /federation/peers` returns the local identity and discovered peers.

This milestone does not yet implement reliable peer messaging, authenticated sessions, remote task delegation, federated RAG, or derived-artifact transfer. UDP is only for discovery and presence, never task or data delivery. See [Federation](federation.md) for the target architecture.
