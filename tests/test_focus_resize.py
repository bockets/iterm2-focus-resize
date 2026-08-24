"""Tests for the reflow logic, run against tests/fake_iterm2.py.

Run with: python3 -m unittest discover -s tests
"""

import asyncio
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import fake_iterm2

fake_iterm2.install()

SCRIPT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "focus_resize.py")

focus_resize = {}
with open(SCRIPT) as handle:
    exec(compile(handle.read(), SCRIPT, "exec"), focus_resize)

divide = focus_resize["divide"]
assign = focus_resize["assign"]
measure = focus_resize["measure"]
resize_around = focus_resize["resize_around"]
GROW = focus_resize["GROW"]
MIN_CELLS = focus_resize["MIN_CELLS"]


def row(*ids):
    """Panes side by side."""
    return fake_iterm2.Splitter(
        True, [fake_iterm2.Session(i) for i in ids])


def column(*ids):
    """Panes stacked."""
    return fake_iterm2.Splitter(
        False, [fake_iterm2.Session(i) for i in ids])


def reflow(window, session_id, capacities=None):
    asyncio.run(resize_around(
        window, window.tab, session_id,
        capacities if capacities is not None else {}))


def reflows(window, session_ids):
    """Repeated reflows sharing the learned capacities, as the script does."""
    capacities = {}
    for session_id in session_ids:
        reflow(window, session_id, capacities)


def widths(window):
    return [pane.grid_size.width for pane in window.tab.sessions]


class DivideTest(unittest.TestCase):
    def test_shares_sum_to_the_total(self):
        for total in (10, 47, 100, 233, 1001):
            for weights in ([1, 1], [1.8, 1, 1], [1, 1, 1, 1.8], [1] * 7):
                self.assertEqual(sum(divide(total, weights)), total,
                                 (total, weights))

    def test_shares_are_proportional_to_weights(self):
        small, large = divide(280, [1, GROW])

        self.assertEqual(large / small, GROW)

    def test_no_pane_falls_below_the_minimum(self):
        shares = divide(200, [1, 1, 1, 500])

        self.assertTrue(all(share >= MIN_CELLS for share in shares), shares)
        self.assertEqual(sum(shares), 200)

    def test_a_total_too_small_for_the_floor_still_adds_up(self):
        shares = divide(6, [1.8, 1, 1])

        self.assertEqual(sum(shares), 6)


class AssignTest(unittest.TestCase):
    def test_row_divides_width_and_shares_height(self):
        root = row("a", "b", "c")

        assign(root, 240, 50, "b")
        sizes = [pane.preferred_size for pane in root.sessions]

        self.assertEqual(sum(size.width for size in sizes), 240)
        self.assertTrue(all(size.height == 50 for size in sizes), sizes)

    def test_column_divides_height_and_shares_width(self):
        root = column("a", "b")

        assign(root, 100, 40, "a")
        sizes = [pane.preferred_size for pane in root.sessions]

        self.assertEqual(sum(size.height for size in sizes), 40)
        self.assertTrue(all(size.width == 100 for size in sizes), sizes)

    def test_nested_splitters_divide_their_own_axis(self):
        left = fake_iterm2.Session("a")
        right = column("b", "c")
        root = fake_iterm2.Splitter(True, [left, right])

        assign(root, 240, 40, "c")

        self.assertEqual(
            left.preferred_size.width + right.children[0].preferred_size.width,
            240)
        self.assertEqual(
            sum(child.preferred_size.height for child in right.children), 40)

    def test_the_focused_pane_asks_for_the_most(self):
        root = row("a", "b", "c")

        assign(root, 240, 50, "b")
        sizes = {pane.session_id: pane.preferred_size.width
                 for pane in root.sessions}

        self.assertGreater(sizes["b"], sizes["a"])
        self.assertEqual(sizes["a"], sizes["c"])


def requested(node, axis):
    """Cells a node's subtree asks for along an axis, as iTerm2 would add up."""
    if not hasattr(node, "children"):
        size = node.preferred_size
        return size.width if axis == "w" else size.height
    along = (axis == "w") == node.vertical
    parts = [requested(child, axis) for child in node.children]
    return sum(parts) if along else max(parts)


class RequestTest(unittest.TestCase):
    """A request iTerm2 can't satisfy is one it declines outright.

    This was a real production failure: asking for a few more cells than the tab
    could hold left the panes exactly as they were, so no SIGWINCH reached the
    programs inside them and the resize silently did nothing.
    """

    def test_a_row_never_asks_for_more_cells_than_it_has(self):
        root = row("a", "b", "c", "d")
        width, height = 231, 92

        for session_id in ["a", "b", "c", "d"]:
            assign(root, width, height, session_id)

            self.assertEqual(requested(root, "w"), width, session_id)
            self.assertEqual(requested(root, "h"), height, session_id)

    def test_a_nested_layout_never_asks_for_more_than_it_has(self):
        root = fake_iterm2.Splitter(
            True, [fake_iterm2.Session("a"), column("b", "c")])
        width, height = 233, 91

        for session_id in ["a", "b", "c"]:
            assign(root, width, height, session_id)

            self.assertEqual(requested(root, "w"), width, session_id)
            self.assertEqual(requested(root, "h"), height, session_id)

    def test_odd_totals_still_add_up_exactly(self):
        for width in range(20, 240, 7):
            root = row("a", "b", "c")

            assign(root, width, 50, "b")

            self.assertEqual(requested(root, "w"), width, width)


class ReflowTest(unittest.TestCase):
    def test_the_focused_pane_becomes_the_widest(self):
        window = fake_iterm2.Window(row("a", "b", "c"), 2000, 900)

        reflow(window, "b")
        sizes = dict(zip(["a", "b", "c"], widths(window)))

        self.assertGreater(sizes["b"], sizes["a"])
        self.assertGreater(sizes["b"], sizes["c"])

    def test_panes_move_on_every_switch(self):
        """Guards the production failure where panes stopped moving at all."""
        window = fake_iterm2.Window(row("a", "b", "c", "d"), 2000, 900)
        capacities = {}
        reflow(window, "a", capacities)

        for session_id in ["b", "c", "d", "a"] * 3:
            reflow(window, session_id, capacities)
            largest = widths(window).index(max(widths(window)))

            self.assertEqual(largest, ["a", "b", "c", "d"].index(session_id))

    def test_the_window_keeps_its_size_across_many_switches(self):
        for root in (row("a", "b", "c", "d"), column("a", "b", "c"),
                     fake_iterm2.Splitter(
                         True, [fake_iterm2.Session("a"), column("b", "c")])):
            window = fake_iterm2.Window(root, 2000, 900)
            ids = [pane.session_id for pane in window.tab.sessions]
            reflows(window, ids)
            settled = (window.frame.size.width, window.frame.size.height)

            reflows(window, ids * 6)

            self.assertEqual(
                (window.frame.size.width, window.frame.size.height), settled)

    def test_the_window_is_never_shrunk(self):
        """Shrinking is what made the window creep smaller per pane switch."""
        window = fake_iterm2.Window(row("a", "b", "c", "d"), 2000, 900)
        capacities = {}
        reflow(window, "a", capacities)
        window.shrank = 0

        for session_id in ["b", "c", "d", "a"] * 5:
            reflow(window, session_id, capacities)

        self.assertEqual(window.shrank, 0)

    def test_a_resized_window_is_probed_again(self):
        window = fake_iterm2.Window(row("a", "b", "c"), 2000, 900)
        capacities = {}
        reflow(window, "a", capacities)

        window.frame.size = fake_iterm2.Size(1400, 900)
        window.shrank = 0
        for session_id in ["b", "c", "a"] * 4:
            reflow(window, session_id, capacities)

            self.assertEqual(window.shrank, 0, session_id)
        self.assertLessEqual(window.frame.size.width, 1400)

    def test_a_settled_tab_costs_no_declined_attempts(self):
        """Capacity is found once per window size, not re-probed per switch."""
        window = fake_iterm2.Window(row("a", "b", "c", "d"), 2000, 900)
        capacities = {}
        reflow(window, "a", capacities)
        window.declined = 0

        for session_id in ["b", "c", "d", "a"] * 5:
            reflow(window, session_id, capacities)

        self.assertEqual(window.declined, 0)

    def test_the_window_keeps_its_position(self):
        window = fake_iterm2.Window(row("a", "b"), 2000, 900, origin=(37, 91))

        for session_id in ["a", "b"] * 5:
            reflow(window, session_id)

            self.assertEqual(
                (window.frame.origin.x, window.frame.origin.y), (37, 91))

    def test_a_single_pane_tab_is_left_alone(self):
        only = fake_iterm2.Session("a")
        window = fake_iterm2.Window(
            fake_iterm2.Splitter(True, [only]), 2000, 900)
        before = window.frame.size.width

        reflow(window, "a")

        self.assertEqual(window.frame.size.width, before)
        self.assertEqual(window.set_frame_calls, 0)


if __name__ == "__main__":
    unittest.main()
