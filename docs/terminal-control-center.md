# Native Terminal Control Center

**Status:** Initial read-only client implementation.

The browser Control Center remains available at /control. The native terminal
Control Center is a second client of the same Ensemble REST API; it does not
access application services or SQLite directly.

## Run

Install the optional terminal UI dependency:

```bash
python -m pip install -e '.[terminal]'
```

Start Ensemble as usual, then run:

```bash
ensemble-tui
```

The default API URL is http://127.0.0.1:8090. Override it with ENSEMBLE_URL
or --url. The default refresh interval is five seconds and can be changed
with --refresh.

## Controls

- 1-9: select a workbench
- R: refresh immediately
- Q or Escape: exit

## Current scope

The initial screen reads /health, /ready, and /api/v1/system/config. The
remaining workbench entries are deliberately marked as not yet wired to REST
contracts. No fake counters, inferred metrics, or simulated mutations are
displayed.

Reclamation is intentionally read-only until its durable ledger, explicit
purge-authorization contract, retention eligibility checks, worker claims,
and recovery behavior are implemented. Startup cleanup must never convert
soft deletion or scheduled deletion into purge authorization.

## Architecture

Both browser and terminal interfaces consume the same versioned REST
contracts. The terminal interface uses curses-tui for theme integration and
Python curses for its application loop. UI navigation and rendering contain
no capability business logic.
