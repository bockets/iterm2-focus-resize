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

`preferred_size` is a request measured in cells, and iTerm2 sizes the *window*
to fit what a tab's panes collectively ask for. A reflow therefore divides a
fixed number of cells between the panes — the focused one gets `GROW` shares,
each sibling gets one — so the window keeps whatever size you gave it.

The division recurses through the split tree rather than treating the panes as a
flat list, so a splitter with vertical dividers divides width among its children
and one with horizontal dividers divides height. Boundaries are rounded instead
of individual shares, so rounding error can't accumulate within a pass.

Asking for the cells a tab already had does not reliably reproduce the window it
had them in. Dividers, per-pane margins and a scrollbar all take room that
counting pane cells can't see, and they cost different amounts in a tab with one
pane than in a tab with four — so the first reflow on a tab would shrink the
window by those few points. Rather than price each of them, the script reads the
window frame before the reflow and puts it back afterwards. The panes have
already been fitted to that frame by ratio, so restoring it only undoes the
window's own drift.

The total is *anchored* rather than re-measured on every pass, and that part is
load-bearing. A cell is not a whole number of points wide, so a window sized to
hold exactly N cells falls a fraction of a cell short and iTerm2 lays out N-1.
Measuring the panes again would feed that loss straight back in, walking the
window one character narrower with every pane switch.

A window you resize does need a fresh anchor, and the only way to tell your
resize apart from the script's own one-cell settling is to remember the frame
each reflow left behind. A frame that still matches is nobody's doing but the
script's, so the anchor stands; any other frame means you moved an edge, so the
tab is measured again. Splitting or closing a pane re-anchors too.

Focus events arrive in bursts — cycling six panes with `Cmd-]` fires six of them
— so a resize is scheduled `DEBOUNCE_SECONDS` out and cancelled by the next
event. Panes passed through mid-cycle never reflow at all.

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
