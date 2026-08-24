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

`preferred_size` is a request measured in cells, and a tab whose panes ask for
more room than the window has will make iTerm2 grow the *window* to fit. So each
reflow starts by measuring how many cells the tab currently occupies, walking
`Tab.root` to add up the split tree, and then divides exactly that many cells
between the panes: the focused one gets `GROW` shares, each sibling gets one.
Cells in equals cells out, so the window keeps whatever size you gave it, and a
window you shrink by dragging its edge stays shrunk.

Those measurements are read fresh on every focus change — the app model is
refreshed first, so a window resized a moment ago reports its new grid sizes.
Nothing about the previous layout is carried forward, which means there is no
stale size to spring back to and no drift from switching back and forth.

The division recurses through the split tree rather than treating the panes as a
flat list, so a splitter with vertical dividers divides width among its children
and one with horizontal dividers divides height. Boundaries are rounded instead
of individual shares, so rounding error can't accumulate and change the total.

Focus events arrive in bursts — cycling six panes with `Cmd-]` fires six of them
— so a resize is scheduled `DEBOUNCE_SECONDS` out and cancelled by the next
event. Panes passed through mid-cycle never reflow at all.

## Known limitations

- **Pane size, not font size.** Panes get more cells; the font stays put.
- iTerm2 only. The equivalent elsewhere is a tmux `pane-focus-in` hook calling
  `resize-pane`, or a Kitty `on_focus_change` watcher calling
  `kitten @ resize-window`.

## Keyboard shortcuts

Nothing is bound by this script — it reacts to iTerm2's own navigation.

- `Cmd-]` / `Cmd-[` — cycle panes in most-recently-used order
- `Cmd-Opt-<arrow>` — move by direction
