#!/usr/bin/env python3
"""Grow the focused split pane and shrink its siblings.

iTerm2 has no built-in focus-driven resize, but it exposes both halves of the
wiring: FocusMonitor reports active-session changes, and a tab's layout can be
recomputed from each session's preferred_size.

preferred_size is a request, not a command. iTerm2 fits the panes to the tab it
already has, so what actually matters is the *ratio* between the sizes handed to
the sessions in a tab. The numbers below are therefore weights expressed in
cells: siblings ask for BASE_CELLS, the focused pane asks for BASE_CELLS * GROW.
Because every pane's request is recomputed from those constants on each focus
change rather than from its current size, repeated switching does not compound
into drift.
"""

import asyncio

import iterm2

# Sibling panes ask for a square of this many cells; only the ratio to the
# focused pane's request matters, so the absolute value is arbitrary.
BASE_CELLS = 80

# Seconds to wait for focus to settle before reflowing. Cycling six panes with
# Cmd-] fires six focus events in well under this, so only the pane landed on
# pays for a resize. Long enough to swallow a fast cycle, short enough that a
# deliberate switch still feels immediate.
DEBOUNCE_SECONDS = 0.12

# How much bigger the focused pane asks to be. 1.8 is roughly golden-ratio-ish
# across 2 panes and still leaves 6 panes legible. Tune to taste.
GROW = 1.8


def sessions_in_tab(tab):
    """Flatten a tab's split tree into a list of sessions."""
    return tab.sessions


async def resize_around(tab, focused_session_id):
    panes = sessions_in_tab(tab)
    if len(panes) < 2:
        return

    for session in panes:
        weight = GROW if session.session_id == focused_session_id else 1.0
        cells = round(BASE_CELLS * weight)
        session.preferred_size = iterm2.util.Size(cells, cells)

    await tab.async_update_layout()


async def tab_containing(app, session_id):
    for window in app.terminal_windows:
        for tab in window.tabs:
            for session in tab.sessions:
                if session.session_id == session_id:
                    return tab
    return None


async def resize_after_settling(app, session_id):
    """Resize once focus has stopped moving.

    Cancelled by the next focus change, so panes passed through mid-cycle never
    reflow at all.
    """
    await asyncio.sleep(DEBOUNCE_SECONDS)

    tab = await tab_containing(app, session_id)
    if tab is not None:
        await resize_around(tab, session_id)


async def main(connection):
    app = await iterm2.async_get_app(connection)
    last_session_id = None
    pending = None

    async with iterm2.FocusMonitor(connection) as monitor:
        while True:
            update = await monitor.async_get_next_update()

            change = update.active_session_changed
            if change is None:
                continue

            session_id = change.session_id
            # Focus updates can arrive in bursts (window activation reports the
            # same session again); reflowing every child process for a no-op
            # change is exactly the cost that keeps this out of the core app.
            if session_id is None or session_id == last_session_id:
                continue
            last_session_id = session_id

            if pending is not None:
                pending.cancel()
            pending = asyncio.create_task(resize_after_settling(app, session_id))


iterm2.run_forever(main)
