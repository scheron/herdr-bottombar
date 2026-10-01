# Changelog

## 0.1.0

Initial release.

- **Vertical layout.** A bar along the bottom of every tab — spaces with agent
  state, branch and ahead/behind on the left, agents in attention order on the
  right — with herdr's sidebar hidden.
- `layout` swaps the standard and vertical layouts by swapping herdr's config
  for a generated copy and back; a symlinked config is relinked, a regular one
  set aside and restored.
- `minimize` folds the bar into one line of the tab bar. In the vertical layout
  it takes over the key `toggle_sidebar` is bound to.
- Bars are kept one per tab across new tabs, closed bars and server restarts,
  and stay out of the way of tab-titling plugins.
