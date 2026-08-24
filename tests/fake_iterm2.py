"""A stand-in for iTerm2, enough of it to exercise the reflow logic.

Only behaviour actually observed from the real iTerm2 is modelled here, because
a guessed layout engine is worse than none -- it fails strategies that work and
passes ones that don't:

* A cell is a fractional number of points, and each pane floors its own share
  independently. So the cells a tab *occupies* is a little under what it can
  *hold*, and the shortfall moves as focus moves.
* Dividers and per-pane margins cost points that counting cells cannot see, and
  a four-pane tab spends more of them than a one-pane tab.
* Panes are fitted to the window by the *ratio* of their requests.
* Asking for fewer cells than the window holds shrinks the window to fit the
  request -- a cell or more of slack is enough to move it.
* Asking for more cells than the window holds makes iTerm2 decline the layout
  outright: the panes keep the sizes they had and the window does not move.

What is deliberately *not* modelled: the window snapping down to a whole number
of cells after every layout. A real session log showed the window frame pinned
across a dozen consecutive reflows, so iTerm2 does not do this on each pass.
"""

import sys
import types

CELL_WIDTH = 8.4
CELL_HEIGHT = 17.3
DIVIDER = 1.0
MARGIN = 2.0


class Size:
    def __init__(self, width, height):
        self.width = width
        self.height = height

    def __eq__(self, other):
        return (self.width, self.height) == (other.width, other.height)

    def __repr__(self):
        return "{}x{}".format(self.width, self.height)

    @property
    def dict(self):
        return {"width": self.width, "height": self.height}


class Point:
    def __init__(self, x, y):
        self.x = x
        self.y = y

    @property
    def dict(self):
        return {"x": self.x, "y": self.y}


class Frame:
    def __init__(self, origin=None, size=None):
        self.origin = origin or Point(0, 0)
        self.size = size or Size(0, 0)

    def __repr__(self):
        return "<Frame {},{} {}>".format(
            self.origin.x, self.origin.y, self.size)

    @property
    def dict(self):
        return {"origin": self.origin.dict, "size": self.size.dict}


def install():
    """Put the fake in sys.modules so the script under test imports it."""
    module = types.ModuleType("iterm2")
    util = types.ModuleType("iterm2.util")
    util.Size = Size
    util.Point = Point
    util.Frame = Frame
    module.util = util
    module.FocusMonitor = object
    module.run_forever = lambda main: None
    sys.modules["iterm2"] = module
    sys.modules["iterm2.util"] = util
    return module


class Session:
    def __init__(self, session_id):
        self.session_id = session_id
        self.grid_size = Size(0, 0)
        self.preferred_size = Size(0, 0)


class Splitter:
    def __init__(self, vertical, children):
        self.vertical = vertical
        self.children = children

    @property
    def sessions(self):
        found = []
        for child in self.children:
            if isinstance(child, Splitter):
                found.extend(child.sessions)
            else:
                found.append(child)
        return found


class Window:
    """A window whose panes are re-laid-out whenever the layout is updated."""

    def __init__(self, root, width, height, origin=(100, 200)):
        self.root = root
        self.declined = 0
        self.shrank = 0
        self.frame = Frame(Point(*origin), Size(width, height))
        self.tab = Tab(self, root)
        self.tabs = [self.tab]
        self.set_frame_calls = 0
        self._even_layout()

    async def async_get_frame(self):
        return Frame(
            Point(self.frame.origin.x, self.frame.origin.y),
            Size(self.frame.size.width, self.frame.size.height))

    async def async_set_frame(self, frame):
        self.set_frame_calls += 1
        self.frame = Frame(
            Point(frame.origin.x, frame.origin.y),
            Size(frame.size.width, frame.size.height))
        self._relayout()

    # -- layout ----------------------------------------------------------

    def _decorations(self, node, axis):
        """Points spent on dividers and margins along `axis` ('w' or 'h')."""
        panes = node.sessions if isinstance(node, Splitter) else [node]
        if not isinstance(node, Splitter):
            return 2 * MARGIN
        along = (axis == "w") == node.vertical
        inner = max(
            self._decorations(child, axis) for child in node.children) \
            if not along else \
            sum(self._decorations(child, axis) for child in node.children)
        return inner + (DIVIDER * (len(node.children) - 1) if along else 0)

    def _requested_points(self, node, axis):
        if not isinstance(node, Splitter):
            cells = (node.preferred_size.width if axis == "w"
                     else node.preferred_size.height)
            unit = CELL_WIDTH if axis == "w" else CELL_HEIGHT
            return cells * unit
        along = (axis == "w") == node.vertical
        parts = [self._requested_points(child, axis) for child in node.children]
        return sum(parts) if along else max(parts)

    def _even_layout(self):
        """The layout a fresh tab has: panes split evenly, window untouched."""
        for axis, unit in (("w", CELL_WIDTH), ("h", CELL_HEIGHT)):
            usable = self._span(axis) - self._decorations(self.root, axis)
            for pane in self.root.sessions:
                pane.preferred_size = Size(1, 1)
            self._distribute(self.root, axis, usable, unit)
        for pane in self.root.sessions:
            pane.preferred_size = Size(
                pane.grid_size.width, pane.grid_size.height)

    def _relayout(self):
        """Fit panes to the window, or decline if they don't fit."""
        for axis, unit in (("w", CELL_WIDTH), ("h", CELL_HEIGHT)):
            decorations = self._decorations(self.root, axis)
            usable = self._span(axis) - decorations
            wanted = self._requested_points(self.root, axis)

            if wanted > usable + 0.0001:
                # More than the window holds: iTerm2 leaves the layout alone.
                self.declined += 1
                continue

            if wanted < usable - unit:
                # A cell or more of slack: the window shrinks onto the request.
                self.shrank += 1
                self._set_span(axis, wanted + decorations)
                usable = wanted

            self._distribute(self.root, axis, usable, unit)

    def _distribute(self, node, axis, points, unit):
        if not isinstance(node, Splitter):
            cells = max(1, int(points / unit))
            if axis == "w":
                node.grid_size = Size(cells, node.grid_size.height)
            else:
                node.grid_size = Size(node.grid_size.width, cells)
            return

        along = (axis == "w") == node.vertical
        if not along:
            for child in node.children:
                self._distribute(child, axis, points, unit)
            return

        shares = [self._requested_points(child, axis)
                  for child in node.children]
        # A pane that has never been given a preferred size asks for nothing;
        # iTerm2 splits evenly in that case, which is how a fresh tab looks.
        if sum(shares) == 0:
            shares = [1.0] * len(node.children)
        spendable = points - (DIVIDER * (len(node.children) - 1))
        scale = spendable / sum(shares) if sum(shares) else 0
        for child, share in zip(node.children, shares):
            self._distribute(child, axis, share * scale, unit)

    def _occupied(self, node, axis):
        if not isinstance(node, Splitter):
            return node.grid_size.width if axis == "w" else node.grid_size.height
        along = (axis == "w") == node.vertical
        parts = [self._occupied(child, axis) for child in node.children]
        return sum(parts) if along else max(parts)

    def _span(self, axis):
        return self.frame.size.width if axis == "w" else self.frame.size.height

    def _set_span(self, axis, value):
        if axis == "w":
            self.frame.size = Size(value, self.frame.size.height)
        else:
            self.frame.size = Size(self.frame.size.width, value)


class Tab:
    def __init__(self, window, root):
        self.window = window
        self.root = root
        self.tab_id = "tab-1"

    @property
    def sessions(self):
        return self.root.sessions if isinstance(self.root, Splitter) \
            else [self.root]

    async def async_update_layout(self):
        self.window._relayout()
