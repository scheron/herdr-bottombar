# bottombar

<p align="center">
  <a href="#install">install</a> · <a href="#two-layouts">layouts</a> · <a href="#the-bar">the bar</a> · <a href="#how-it-swaps-layouts">how it works</a> · <a href="#limitations">limitations</a>
</p>

A vertical layout for [herdr](https://herdr.dev). On a portrait monitor the left
sidebar takes a quarter of the one dimension you are short of. bottombar puts
spaces and agents in a bar along the bottom of every tab instead, and one key
swaps the two layouts.

```
│ claude …                                                          │
│───────────────────────────────────────────────────────────────────│
│ spaces                          │ agents               1 working  │
│ · Due App  plan/frame-and-loop  │ ● dotfiles · claude    claude   │
│ ● dotfiles  main ↑1             │                                 │
│ 1 claude  2 tests  +                                              │
```

## Install

```sh
herdr plugin install scheron/herdr-bottombar
```

Bind a key in `~/.config/herdr/config.toml` — herdr does not bind keys declared
by a plugin — then `herdr server reload-config`:

```toml
[[keys.command]]
key = "prefix+shift+e"
type = "plugin_action"
command = "scheron.bottombar.layout"
description = "Swap the sidebar and the bottom bar"
```

Needs herdr 0.9 and the system `/usr/bin/python3`; nothing beyond its standard
library.

## Two layouts

| | Standard | Vertical |
|---|---|---|
| Spaces and agents | herdr's own sidebar | a bar at the bottom of every tab |
| The sidebar key | collapses the sidebar | folds the bar into the tab bar |
| Tab bar | where your config puts it | at the bottom |
| Pane borders | as your config draws them | no outer frame, no gaps |

Standard is herdr exactly as you configured it. "The sidebar key" is whatever
your `toggle_sidebar` is bound to — herdr's default is `prefix+b` — and in the
vertical layout bottombar hands it to its own `minimize` action, so the same key
folds whichever panel is on screen.

Folded, the bar closes and its gist moves to the right end of the tab bar:

```
│ 1 claude  2 tests  +             ● dotfiles  main ↑1  │  ● claude working │
```

## The bar

Spaces on the left, as the sidebar shows them: agent state, name, branch, and
how far it is ahead (`↑`) of or behind (`↓`) its upstream. Agents on the right,
in attention order — blocked, done, working, idle — with a count of the ones
that need you in the header.

Click a space or an agent to go there; the wheel scrolls a column. The bar is a
pane, so `prefix+j` from the pane above focuses it, and then:

| Key | Does |
|---|---|
| `j` `k` | Move |
| `h` `l` `Tab` | Switch column |
| `Enter` | Go to the selected space or agent |
| `Esc` `q` | Back to the pane above |

## How it swaps layouts

A plugin cannot show or hide herdr's sidebar: there is no API for it, and a key
bound to both `toggle_sidebar` and a plugin action keeps only the built-in. What
a config reload can change is the sidebar's width. So switching layouts
switches configs.

Going vertical, bottombar replaces `~/.config/herdr/config.toml` with a
generated copy — your config with the vertical settings laid over it, the
sidebar at zero width — and reloads. Going back, it puts yours back: a
symlinked config (dotfiles) is relinked to its target, a regular file is moved
back from `config.toml.bottombar-base`, where it waited.

While the vertical layout is on, edit the file the generated config's first
line names, not the generated copy: bottombar rewrites the copy from it the next
time you switch tabs.

The bars themselves are ordinary panes split off the bottom of each tab. One
sync, run on startup, on every new tab and on every tab switch, keeps exactly
one bar per tab — so a tab whose bar you closed gets it back the next time it
is shown, and the shells a server restart leaves where bars were are replaced.

## Limitations

- herdr keeps every split between 10% and 90%, so no pane can be shorter than a
  tenth of its tab. That is why folding moves the bar into the tab bar rather
  than shrinking the pane to one row.
- herdr strips colours from the tab bar, so the folded line's state marks are
  plain.
- Colours are tokyo-night's, whatever theme herdr is on.
- A tab that was already split side by side when its bar arrived gets the bar
  under one half only. New tabs get it across the full width.
- The bar holds a fixed height and takes it back if you drag the divider.
- A herdr Settings change made in the vertical layout lands in the generated
  copy and is lost on the next switch. Make it in your own config.
- An unfocused tab is titled by tab-titling plugins after its most recently
  changed pane, which would be the bar. Two seconds after it starts, the bar
  touches the other panes in its tab with a short-lived metadata token so they
  count as newer.
- Tested on macOS only.

## License

MIT
