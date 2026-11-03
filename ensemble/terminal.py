"""Native curses Control Center for Ensemble.

Run with python -m ensemble.terminal. This initial slice is read-only and
communicates exclusively through Ensemble REST endpoints.
"""
from __future__ import annotations

import argparse
import curses
import os
import textwrap
import time
from typing import Any

import httpx

from .terminal_client import ControlCenterClient, ServiceSnapshot

try:
    from curses_tui import ThemeManager
except ImportError:
    ThemeManager = None


NAVIGATION = (
    ("1", "Overview", "overview"),
    ("2", "Reclamation", "reclamation"),
    ("3", "Thompson", "thompson"),
    ("4", "Graph", "graph"),
    ("5", "Vector", "vector"),
    ("6", "Knowledge", "knowledge"),
    ("7", "Memory", "memory"),
    ("8", "Pipelines", "pipelines"),
    ("9", "Diagnostics", "diagnostics"),
)


def _safe_addstr(window: Any, y: int, x: int, value: str, attr: int = 0) -> None:
    height, width = window.getmaxyx()
    if 0 <= y < height and 0 <= x < width:
        window.addnstr(y, x, value, max(0, width - x - 1), attr)


def _draw_header(stdscr: Any, title: str, connected: bool) -> None:
    _, width = stdscr.getmaxyx()
    stdscr.attron(curses.A_REVERSE)
    _safe_addstr(stdscr, 0, 0, f" FLOSSWARE / ENSEMBLE  |  {title} ")
    status = "CONNECTED" if connected else "DISCONNECTED"
    _safe_addstr(stdscr, 0, max(1, width - len(status) - 3), status)
    stdscr.attroff(curses.A_REVERSE)
    _safe_addstr(stdscr, 1, 2, "Terminal Control Center - REST client")
    stdscr.hline(2, 0, curses.ACS_HLINE, max(0, width - 1))


def _draw_navigation(stdscr: Any, selected: str, top: int = 4) -> None:
    _safe_addstr(stdscr, top, 2, "WORKBENCHES", curses.A_BOLD)
    for index, (_, label, page) in enumerate(NAVIGATION, start=1):
        attr = curses.A_REVERSE if page == selected else curses.A_NORMAL
        _safe_addstr(stdscr, top + index, 2, f" {index}  {label}", attr)


def _draw_overview(stdscr: Any, snapshot: ServiceSnapshot | None, error: str | None, x: int) -> None:
    height, width = stdscr.getmaxyx()
    _safe_addstr(stdscr, 4, x, "PLATFORM OVERVIEW", curses.A_BOLD)
    if error:
        _safe_addstr(stdscr, 6, x, "API unavailable", curses.A_BOLD)
        for index, line in enumerate(textwrap.wrap(error, max(20, width - x - 4))[:4]):
            _safe_addstr(stdscr, 7 + index, x, line)
        return
    if snapshot is None:
        _safe_addstr(stdscr, 6, x, "Waiting for first API response...")
        return

    _safe_addstr(stdscr, 6, x, f"Health:       {snapshot.health.get('status', 'unknown')}")
    _safe_addstr(stdscr, 7, x, f"Readiness:    {snapshot.readiness.get('status', 'unknown')}")
    _safe_addstr(stdscr, 8, x, f"Persistence:  {snapshot.readiness.get('persistence', 'not reported')}")
    _safe_addstr(stdscr, 9, x, f"Embeddings:   {snapshot.readiness.get('embeddings', 'not reported')}")
    capabilities = snapshot.readiness.get("capabilities", [])
    _safe_addstr(stdscr, 11, x, "CAPABILITIES", curses.A_BOLD)
    if isinstance(capabilities, list):
        for index, capability in enumerate(capabilities[: max(0, height - 15)]):
            _safe_addstr(stdscr, 12 + index, x, f"- {capability}")
    else:
        _safe_addstr(stdscr, 12, x, "Capability inventory not reported")

    config = snapshot.configuration or {}
    if config:
        y = min(height - 3, 13 + min(len(capabilities), max(0, height - 15)))
        _safe_addstr(stdscr, y, x, f"API: {config.get('host', '?')}:{config.get('port', '?')}  DB: {config.get('database', '?')}")


def _draw_placeholder(stdscr: Any, page: str, x: int) -> None:
    labels = {item[2]: item[1] for item in NAVIGATION}
    _safe_addstr(stdscr, 4, x, labels.get(page, page.title()).upper(), curses.A_BOLD)
    _safe_addstr(stdscr, 6, x, "This workbench is not wired to a REST contract yet.")
    _safe_addstr(stdscr, 8, x, "No placeholder metrics or simulated operations are shown.")
    if page == "reclamation":
        _safe_addstr(stdscr, 10, x, "Reclamation is read-only until its ledger and authorization API exist.")
        _safe_addstr(stdscr, 11, x, "Soft deletion and scheduled deletion never imply purge authorization.")


def _run(stdscr: Any, client: ControlCenterClient, refresh_seconds: float) -> None:
    curses.curs_set(0)
    stdscr.keypad(True)
    stdscr.timeout(250)
    if ThemeManager is not None:
        try:
            ThemeManager.load("dark").apply(stdscr)
        except (KeyError, curses.error, RuntimeError):
            pass

    selected = "overview"
    snapshot: ServiceSnapshot | None = None
    error: str | None = None
    last_refresh = 0.0
    while True:
        now = time.monotonic()
        if now - last_refresh >= refresh_seconds:
            try:
                snapshot = client.snapshot()
                error = None
            except (httpx.HTTPError, ValueError) as exc:
                error = str(exc)
            last_refresh = now

        stdscr.erase()
        height, width = stdscr.getmaxyx()
        connected = error is None and snapshot is not None
        _draw_header(stdscr, "CONTROL CENTER", connected)
        nav_width = min(24, max(18, width // 4))
        _draw_navigation(stdscr, selected)
        if width > nav_width + 8:
            if selected == "overview":
                _draw_overview(stdscr, snapshot, error, nav_width + 3)
            else:
                _draw_placeholder(stdscr, selected, nav_width + 3)
        if height >= 2:
            stdscr.hline(height - 2, 0, curses.ACS_HLINE, max(0, width - 1))
            _safe_addstr(stdscr, height - 1, 2, "1-9 Navigate  R Refresh  Q Exit")
        stdscr.refresh()

        key = stdscr.getch()
        if key in (ord("q"), ord("Q"), 27):
            return
        if key in (ord("r"), ord("R")):
            last_refresh = 0.0
        elif ord("1") <= key <= ord("9"):
            selected = NAVIGATION[key - ord("1")][2]


def main() -> None:
    parser = argparse.ArgumentParser(description="Ensemble terminal Control Center")
    parser.add_argument("--url", default=os.environ.get("ENSEMBLE_URL", "http://127.0.0.1:8090"))
    parser.add_argument("--refresh", type=float, default=5.0, help="refresh interval in seconds")
    args = parser.parse_args()
    if args.refresh < 1.0:
        parser.error("--refresh must be at least 1 second")
    curses.wrapper(_run, ControlCenterClient(args.url), args.refresh)


if __name__ == "__main__":
    main()
