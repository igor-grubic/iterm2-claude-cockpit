#!/usr/bin/env python3
"""iTerm2 Workflow toolbelt — entry point.

Runs as a daemon under iTerm2 AutoLaunch. Registers a custom toolbelt webview
that loads from a local stdlib HTTP server, then subscribes to layout-change
notifications and refreshes a cached tree snapshot. The webview polls
/api/tree to render the live tree.
"""

from __future__ import annotations

import asyncio
import atexit
import logging
import os
import signal
import sys
from pathlib import Path

# Make the bundled package importable when iTerm2 launches the script via path.
sys.path.insert(0, str(Path(__file__).resolve().parent))

import iterm2  # noqa: E402

from server import http as http_server  # noqa: E402

PORT = 9876
HOST = "127.0.0.1"
TOOL_NAME = "Claude Cockpit"
TOOL_IDENTIFIER = "com.igrubic.iterm2-claude-cockpit"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("iterm2_claude_cockpit")


async def main(connection: iterm2.Connection) -> None:
    app = await iterm2.async_get_app(connection)
    loop = asyncio.get_running_loop()

    state = http_server.State(connection, app, loop)

    await state.refresh()
    http_server.start_server_thread(state, HOST, PORT)

    # Guarantee unsaved changes (e.g. a tab color set moments ago, still inside the 5s
    # persist debounce) survive a daemon restart. Without this, the only writes to disk are
    # debounced and backgrounded (see State._maybe_persist) — nothing forces one to finish
    # before the process exits.
    def _flush_and_exit(sig_name: str) -> None:
        log.info("received %s — flushing workspace state before exit", sig_name)
        state.flush_now()
        os._exit(0)

    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, _flush_and_exit, sig.name)
    atexit.register(state.flush_now)  # best-effort fallback for other exit paths

    async def _periodic_refresh() -> None:
        while True:
            await asyncio.sleep(1)
            if not state.suppress_refresh:
                await state.refresh()

    asyncio.create_task(_periodic_refresh())

    await iterm2.tool.async_register_web_view_tool(
        connection,
        display_name=TOOL_NAME,
        identifier=TOOL_IDENTIFIER,
        reveal_if_already_registered=True,
        url=f"http://{HOST}:{PORT}/",
    )
    log.info("registered toolbelt %r at http://%s:%d/", TOOL_NAME, HOST, PORT)

    async def on_change(_connection, _notification) -> None:
        if not state.suppress_refresh:
            await state.refresh()

    await iterm2.notifications.async_subscribe_to_layout_change_notification(connection, on_change)
    await iterm2.notifications.async_subscribe_to_new_session_notification(connection, on_change)
    await iterm2.notifications.async_subscribe_to_terminate_session_notification(connection, on_change)
    await iterm2.notifications.async_subscribe_to_focus_change_notification(connection, on_change)

    log.info("subscriptions live; waiting for events")


iterm2.run_forever(main)
