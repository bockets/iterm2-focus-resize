# iterm2-focus-resize

Grows the focused iTerm2 split pane and shrinks its siblings, automatically, as
you move between panes. The others stay visible — just smaller.

Built for running several agent or REPL sessions side by side in one tab, where
the one you're reading should always be the biggest.

## Install

Requires iTerm2 3.3+ and its Python runtime (iTerm2 offers to install the
runtime the first time you open **Scripts → Manage**; it's a one-time download).

```sh
cp focus_resize.py ~/Library/Application\ Support/iTerm2/Scripts/AutoLaunch/
```

Start it now via **Scripts → AutoLaunch → focus_resize.py**. Anything in
`AutoLaunch` starts with iTerm2 from then on. Script output and tracebacks go to
**Scripts → Manage → Console**.

## Tuning

These knobs are constants at the top of the file. Edit, then restart the script
from the Console.

| Constant | Default | Effect |
| --- | --- | --- |
| `GROW` | `1.8` | How much bigger the focused pane gets. Raise it if panes are still too small to read. |
| `DEBOUNCE_SECONDS` | `0.12` | How long focus must settle before reflowing. Lower feels snappier; higher swallows fast cycling more completely. |
| `MIN_CELLS` | `4` | Floor on a pane's width and height, however lopsided the weights get. |

## How it works

iTerm2 dims inactive panes out of the box but won't resize them, and the reason
is cost: dimming is one alpha value on a redraw, while resizing changes each
pane's cell grid, which means a `SIGWINCH` to every child process and a
scrollback reflow. That's too much to do on every pane switch by default — but
it's fine to do deliberately.

The script wires up the two pieces iTerm2 exposes:

- `iterm2.FocusMonitor` reports active-session changes.
- Setting each session's `preferred_size` and calling `Tab.async_update_layout()`
  recomputes the tab's layout.

`preferred_size` is a request measured in cells, and iTerm2 answers one of three
ways:

| Request | What happens |
| --- | --- |
| Exactly what the tab holds | Panes are re-fitted, window untouched — the goal |
| Less | The window shrinks onto the request |
| More | The layout is declined outright: panes keep their sizes, no `SIGWINCH` reaches the programs in them, nothing visibly happens |

So a reflow needs the number of cells the tab can *hold*, and that number can't
be measured. Adding up the panes gives what they *occupy*, which is slightly
less — each pane floors its own fractional share — and the shortfall changes as
focus moves. Requesting the measured total is what makes the window creep a
character smaller on every pane switch.

Capacity is therefore probed for, which works because a declined request is
free: it changes nothing, on screen or in the child processes. The first reflow
on a tab asks for a pane-count more cells than the panes occupy — the most their
floored remainders can be hiding — and steps down until iTerm2 accepts, which it
happens at exactly capacity. The answer belongs to the window, so it's reused
until the window's frame changes and every later reflow lands first time. In
practice that's a handful of declined attempts the first time a tab is focused
and none after, with the window never asked to grow or shrink.

Acceptance is judged per axis, because an accepted layout spends every cell it
asked for and iTerm2 answers width and height separately. Checking them together
lets a landed width mask a height that's still too big, which leaves that axis
permanently wrong and re-declining on every switch.

The division itself recurses through the split tree rather than treating the
panes as a flat list, so a splitter with vertical dividers divides width among
its children and one with horizontal dividers divides height. Boundaries are
rounded instead of individual shares, and the `MIN_CELLS` floor moves cells
between panes rather than adding them, so a division always sums to exactly what
was asked for.

Focus events arrive in bursts — cycling six panes with `Cmd-]` fires six of them
— so a resize is scheduled `DEBOUNCE_SECONDS` out and cancelled by the next
event. Panes passed through mid-cycle never reflow at all.

## Tests

```sh
python3 -m unittest discover -s tests
```

No dependencies — `tests/fake_iterm2.py` stands in for iTerm2. It models only
behaviour observed from the real thing: fractional cells that each pane floors
independently, dividers and margins that cost invisible points, panes fitted by
the ratio of their requests, and a window that grows when a request won't fit.
It deliberately does *not* model the window snapping to whole cells after every
layout — a real session log showed the frame pinned across a dozen consecutive
reflows, and a guessed engine is worse than none: it fails strategies that work
in practice and passes ones that don't.

The load-bearing tests are that the window is never grown or shrunk across many
switches, that the panes move every time, and that a settled tab costs no
declined attempts. Each of those is a bug that shipped: a window creeping
smaller per switch, panes silently freezing when a request was a few cells too
big, and a shrink-then-recover flicker on every pane change from an earlier fix
that put the window frame back by hand.

## Logging

Every reflow can log the window frame, the pane grids it measured, the total it
used and why, and the frame that resulted. It writes to
`~/Library/Logs/focus_resize.log`, but only when that file already exists:

```sh
touch ~/Library/Logs/focus_resize.log   # on
rm ~/Library/Logs/focus_resize.log      # off
```

Neither needs a restart, and with the file absent the check is one `stat` per
pane switch.

## Known limitations

- **Pane size, not font size.** Panes get more cells; the font stays put.
- iTerm2 only. The equivalent elsewhere is a tmux `pane-focus-in` hook calling
  `resize-pane`, or a Kitty `on_focus_change` watcher calling
  `kitten @ resize-window`.

## Keyboard shortcuts

Nothing is bound by this script — it reacts to iTerm2's own navigation.

- `Cmd-]` / `Cmd-[` — cycle panes in most-recently-used order
- `Cmd-Opt-<arrow>` — move by direction
