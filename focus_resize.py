#!/usr/bin/env python3
"""Grow the focused split pane and shrink its siblings.

iTerm2 has no built-in focus-driven resize, but it exposes both halves of the
wiring: FocusMonitor reports active-session changes, and a tab's layout can be
recomputed from each session's preferred_size.

preferred_size is a request measured in cells, and iTerm2 sizes the *window* to
fit what a tab's panes collectively ask for. So a reflow measures how many cells
the tab holds right now and divides exactly those between the panes: the focused
one gets GROW shares, each sibling gets one.

Two things make that not quite arithmetic. Asking for the cells a tab already
had does not reproduce the window it had them in -- dividers, pane margins and a
scrollbar all cost room that counting cells cannot see -- so the window drifts a
little smaller. And a tab does not hold a fixed number of cells: a pane floors
its own fractional width, so the same window holds a couple more cells when the
panes are even than when one is large.

The second one is why the total is measured every time rather than remembered.
A remembered total is a few cells too large for some distributions, and a
request iTerm2 cannot satisfy is one it declines entirely: the panes then keep
the layout they had, no SIGWINCH reaches the programs in them, and the resize
silently does nothing. Measuring fresh keeps every request achievable.

The first one is handled by putting the window frame back after the reflow. The
panes have already been fitted to that frame by ratio, so restoring it undoes
only the window's own drift -- which is what stops a measure-every-time reflow
from walking the window narrower one pane switch at a time.
"""

import asyncio
import datetime
import os

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

# Reflows are logged here, but only if the file already exists -- `touch` it to
# turn logging on, delete it to turn logging off. No restart either way.
LOG_PATH = os.path.expanduser("~/Library/Logs/focus_resize.log")


def log(message):
    if not os.path.exists(LOG_PATH):
        return
    stamp = datetime.datetime.now().strftime("%H:%M:%S.%f")[:-3]
    with open(LOG_PATH, "a") as handle:
        handle.write("{} {}\n".format(stamp, message))


def frame_key(frame):
    """A window frame reduced to something comparable across reflows."""
    return (frame.origin.x, frame.origin.y, frame.size.width, frame.size.height)


def frame_from_key(key):
    x, y, width, height = key
    return iterm2.util.Frame(
        iterm2.util.Point(x, y), iterm2.util.Size(width, height))


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

    Summing to exactly `total` is the point: the reflow hands a tab the cells it
    already had, and a division that invents or loses one asks iTerm2 for a
    window it doesn't have. So boundaries are rounded rather than the individual
    shares -- rounding error can't accumulate that way -- and the floor below
    moves cells between panes rather than adding them.
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
        shares.append(boundary - previous)
        previous = boundary

    # Lift anything under the floor by taking from the largest pane, so the
    # total is untouched. A tab too small to give every pane the floor gets as
    # close as it can: the floor is capped at an even split.
    floor = min(MIN_CELLS, total // len(weights))
    while min(shares) < floor:
        smallest = shares.index(min(shares))
        largest = shares.index(max(shares))
        if shares[largest] - 1 < floor:
            break
        shares[smallest] += 1
        shares[largest] -= 1
    return shares


def assign(node, width, height, focused_session_id):
    """Hand `node` a width x height budget, recursing into splitters."""
    if not is_splitter(node):
        # divide() already holds panes at the floor where the tab allows it;
        # clamping here too would add cells the tab doesn't have.
        node.preferred_size = iterm2.util.Size(
            max(1, round(width)), max(1, round(height)))
        return

    weights = [weigh(child, focused_session_id) for child in node.children]
    axis = width if node.vertical else height
    for child, share in zip(node.children, divide(axis, weights)):
        if node.vertical:
            assign(child, share, height, focused_session_id)
        else:
            assign(child, width, share, focused_session_id)


async def resize_around(window, tab, focused_session_id):
    panes = tab.sessions
    if len(panes) < 2:
        return

    before = frame_key(await window.async_get_frame())
    width, height = measure(tab.root)
    log("tab={} panes={} frame={} measured={} grids={}".format(
        tab.tab_id[-4:], len(panes), before, (width, height),
        [(pane.grid_size.width, pane.grid_size.height) for pane in panes]))

    assign(tab.root, width, height, focused_session_id)
    await tab.async_update_layout()

    laid_out = frame_key(await window.async_get_frame())
    after = laid_out
    if after != before:
        await window.async_set_frame(frame_from_key(before))
        after = frame_key(await window.async_get_frame())

    log("  laid_out={}{} -> frame={} grids={}".format(
        laid_out, " RESTORED" if laid_out != before else "", after,
        [(pane.grid_size.width, pane.grid_size.height)
         for pane in tab.sessions]))


def tab_containing(app, session_id):
    for window in app.terminal_windows:
        for tab in window.tabs:
            for session in tab.sessions:
                if session.session_id == session_id:
                    return window, tab
    return None, None


async def resize_after_settling(app, session_id):
    """Resize once focus has stopped moving.

    Cancelled by the next focus change, so panes passed through mid-cycle never
    reflow at all.
    """
    await asyncio.sleep(DEBOUNCE_SECONDS)

    # Re-read the layout so the split tree and window frame below are current,
    # including any resize the user just made by dragging an edge.
    await app.async_refresh()

    window, tab = tab_containing(app, session_id)
    if tab is not None:
        await resize_around(window, tab, session_id)


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
