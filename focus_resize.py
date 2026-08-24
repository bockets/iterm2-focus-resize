#!/usr/bin/env python3
"""Grow the focused split pane and shrink its siblings.

iTerm2 has no built-in focus-driven resize, but it exposes both halves of the
wiring: FocusMonitor reports active-session changes, and a tab's layout can be
recomputed from each session's preferred_size.

preferred_size is a request measured in cells, and iTerm2 will grow the *window*
to honour a tab whose panes collectively ask for more room than the window has.
So every reflow starts by measuring what the tab currently occupies and then
divides exactly that many cells between the panes: the focused one gets GROW
shares, each sibling gets one. The totals in and out match, so the window keeps
whatever size the user gave it.

Those measurements are read fresh from iTerm2 on every focus change -- the app
model is refreshed first so a window the user just resized reports its new grid
sizes -- which is what makes a manual window resize stick. Nothing about the
old layout is remembered between reflows, so there is no stale size to grow
back to, and no drift from repeated switching either.
"""

import asyncio

import iterm2

# Seconds to wait for focus to settle before reflowing. Cycling six panes with
# Cmd-] fires six focus events in well under this, so only the pane landed on
# pays for a resize. Long enough to swallow a fast cycle, short enough that a
# deliberate switch still feels immediate.
DEBOUNCE_SECONDS = 0.12

# How much bigger the focused pane asks to be. 1.8 is roughly golden-ratio-ish
# across 2 panes and still leaves 6 panes legible. Tune to taste.
GROW = 1.8

# Never hand a pane fewer cells than this, however lopsided the weights get.
MIN_CELLS = 4


def is_splitter(node):
    return hasattr(node, "children")


def measure(node):
    """Cells the node currently occupies, as (width, height).

    Dividers are not counted, but a reflow neither adds nor removes dividers,
    so a layout that spends the same number of cells occupies the same pixels.
    """
    if not is_splitter(node):
        size = node.grid_size
        return size.width, size.height

    parts = [measure(child) for child in node.children]
    widths = [width for width, _ in parts]
    heights = [height for _, height in parts]
    if node.vertical:
        return sum(widths), max(heights)
    return max(widths), sum(heights)


def weigh(node, focused_session_id):
    """How many shares of its parent's axis this node is asking for."""
    if is_splitter(node):
        return sum(weigh(child, focused_session_id) for child in node.children)
    return GROW if node.session_id == focused_session_id else 1.0


def divide(total, weights):
    """Split `total` into integers proportional to `weights`, summing to total.

    Boundaries are rounded rather than the individual shares, so rounding error
    cannot accumulate and change the total the tab asks for.
    """
    scale = total / sum(weights)
    boundaries = []
    running = 0.0
    for weight in weights:
        running += weight * scale
        boundaries.append(round(running))

    previous = 0
    shares = []
    for boundary in boundaries:
        shares.append(max(MIN_CELLS, boundary - previous))
        previous = boundary
    return shares


def assign(node, width, height, focused_session_id):
    """Hand `node` a width x height budget, recursing into splitters."""
    if not is_splitter(node):
        node.preferred_size = iterm2.util.Size(
            max(MIN_CELLS, round(width)), max(MIN_CELLS, round(height)))
        return

    weights = [weigh(child, focused_session_id) for child in node.children]
    axis = width if node.vertical else height
    for child, share in zip(node.children, divide(axis, weights)):
        if node.vertical:
            assign(child, share, height, focused_session_id)
        else:
            assign(child, width, share, focused_session_id)


async def resize_around(tab, focused_session_id):
    if len(tab.sessions) < 2:
        return

    width, height = measure(tab.root)
    assign(tab.root, width, height, focused_session_id)

    await tab.async_update_layout()


def tab_containing(app, session_id):
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

    # Re-read the layout so the measurements below reflect the window as it is
    # now, including any resize the user just made by dragging its edge.
    await app.async_refresh()

    tab = tab_containing(app, session_id)
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
