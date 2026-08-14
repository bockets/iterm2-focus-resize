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

Both knobs are constants at the top of the file. Edit, then restart the script
from the Console.

| Constant | Default | Effect |
| --- | --- | --- |
| `GROW` | `1.8` | How much bigger the focused pane gets. Raise it if panes are still too small to read. |
| `DEBOUNCE_SECONDS` | `0.12` | How long focus must settle before reflowing. Lower feels snappier; higher swallows fast cycling more completely. |

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

`preferred_size` is a request rather than a command — iTerm2 fits the panes to
the tab it already has, so only the *ratio* between the requested sizes matters.
The sizes are therefore weights: siblings ask for `BASE_CELLS`, the focused pane
asks for `BASE_CELLS * GROW`. Every pane's request is recomputed from those
constants on each focus change rather than from its current size, so switching
back and forth doesn't compound into drift.

Focus events arrive in bursts — cycling six panes with `Cmd-]` fires six of them
— so a resize is scheduled `DEBOUNCE_SECONDS` out and cancelled by the next
event. Panes passed through mid-cycle never reflow at all.

## Known limitations

- **Nested layouts are untested.** Verified on a single row/column of panes. A
  2x3 grid may weight oddly, because flat weights carry no notion of which split
  axis a pane lives on. Fixing that properly needs per-pane geometry.
- **Pane size, not font size.** Panes get more cells; the font stays put.
- iTerm2 only. The equivalent elsewhere is a tmux `pane-focus-in` hook calling
  `resize-pane`, or a Kitty `on_focus_change` watcher calling
  `kitten @ resize-window`.

## Keyboard shortcuts

Nothing is bound by this script — it reacts to iTerm2's own navigation.

- `Cmd-]` / `Cmd-[` — cycle panes in most-recently-used order
- `Cmd-Opt-<arrow>` — move by direction
