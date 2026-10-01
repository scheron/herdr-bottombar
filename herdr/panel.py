import os
import re
import select
import signal
import sys
import termios
import time
import tty
import unicodedata

from gitinfo import git_info
from herdr_api import (
    AGENT_GLYPHS, AGENT_PRIORITY, BAR_ROWS, PLUGIN_ID, HerdrError, Subscription, bar_parent,
    call, fit_bar, in_plugin_root, snapshot,
)

FG = (169, 177, 214)
FG_FOCUSED = (192, 202, 245)
MUTED = (115, 122, 162)
DIM = (86, 95, 137)
GUTTER = (59, 66, 97)
ROW_BG = (35, 38, 54)
SEL_BG = (40, 52, 87)
ACCENT = (122, 162, 247)
MAGENTA = (187, 154, 247)
RED = (247, 118, 142)
YELLOW = (224, 175, 104)
GREEN = (158, 206, 106)
CYAN = (125, 207, 255)

STATUS_COLORS = {"blocked": RED, "working": YELLOW, "done": CYAN, "idle": GREEN, "unknown": DIM}
STATUS = {s: (AGENT_GLYPHS[s], STATUS_COLORS[s]) for s in AGENT_GLYPHS}

EVENTS = [
    "workspace.created", "workspace.updated", "workspace.renamed", "workspace.moved",
    "workspace.reordered", "workspace.closed", "workspace.focused",
    "tab.created", "tab.closed", "tab.focused", "tab.renamed", "tab.moved",
    "pane.created", "pane.closed", "pane.updated", "pane.focused", "pane.moved",
    "pane.exited", "pane.agent_detected",
]

VISIBLE_POLL = 1.5
HIDDEN_POLL = 30.0
DEBOUNCE = 0.12
NUDGE_DELAY = 2.0

MOUSE_RE = re.compile(rb"\x1b\[<(\d+);(\d+);(\d+)([Mm])")
KEY_RE = re.compile(rb"\x1b\[[A-D]|\x1bO[A-D]|\x1b\[Z|.", re.S)


def char_width(ch):
    if unicodedata.combining(ch):
        return 0
    return 2 if unicodedata.east_asian_width(ch) in "WF" else 1


def text_width(text):
    return sum(char_width(c) for c in text)


def clip(text, width):
    if width <= 0:
        return ""
    if text_width(text) <= width:
        return text
    out, used = [], 0
    for c in text:
        w = char_width(c)
        if used + w > width - 1:
            break
        out.append(c)
        used += w
    return "".join(out) + "…"


def sgr(fg=None, bg=None, bold=False):
    codes = ["0"]
    if bold:
        codes.append("1")
    if fg:
        codes.append("38;2;%d;%d;%d" % fg)
    if bg:
        codes.append("48;2;%d;%d;%d" % bg)
    return "\x1b[" + ";".join(codes) + "m"


def cell(width, left, right=(), bg=None, min_left=12):
    """Render styled segments into exactly `width` columns: `left` clipped, `right` flush right,
    and `right` dropped when it would leave `left` fewer than `min_left` columns."""
    right_w = sum(text_width(t) for t, _ in right)
    avail = width - right_w - (1 if right else 0)
    if right and avail < min_left:
        right, right_w, avail = (), 0, width
    out, used = [], 0
    for text, style in left:
        room = avail - used
        if room <= 0:
            break
        piece = clip(text, room)
        out.append(sgr(bg=bg, **style) + piece)
        used += text_width(piece)
    out.append(sgr(bg=bg) + " " * max(0, width - used - right_w))
    for text, style in right:
        out.append(sgr(bg=bg, **style) + text)
    return "".join(out) + "\x1b[0m"


class Panel:
    def __init__(self):
        self.pane_id = os.environ.get("HERDR_PANE_ID", "")
        self.terminal_id = None
        self.tab_id = os.environ.get("HERDR_TAB_ID", "")
        self.focused = False
        self.visible = True
        self.spaces = []
        self.agents = []
        self.focused_ws = None
        self.error = None
        self.offset = [0, 0]
        self.col = 0
        self.sel = [None, None]
        self.rows = []
        self.width, self.height = 80, 9
        self.last_fit = None

    def refresh(self):
        try:
            snap = snapshot()
        except (OSError, HerdrError, ValueError) as e:
            self.error = str(e) or e.__class__.__name__
            return True
        self.error = None
        panes = {p["pane_id"]: p for p in snap.get("panes", [])}
        me = None
        if self.terminal_id:
            me = next((p for p in panes.values() if p.get("terminal_id") == self.terminal_id), None)
        if me is None:
            me = panes.get(self.pane_id)
        if me:
            self.pane_id = me["pane_id"]
            self.terminal_id = me.get("terminal_id")
            self.tab_id = me.get("tab_id", self.tab_id)
            self.focused = bool(me.get("focused"))
            tab = next((t for t in snap.get("tabs", []) if t["tab_id"] == self.tab_id), None)
            if tab and tab.get("pane_count", 2) <= 1:
                return False
        self.visible = snap.get("focused_tab_id") in (None, self.tab_id)

        layouts = {l["tab_id"]: l for l in snap.get("layouts", [])}
        tabs = {t["tab_id"]: t for t in snap.get("tabs", [])}
        self.keep_height(layouts.get(self.tab_id))

        def work_cwd(tab_id):
            layout = layouts.get(tab_id) or {}
            ids = [layout.get("focused_pane_id")] + [p["pane_id"] for p in layout.get("panes", [])]
            for pid in ids:
                p = panes.get(pid)
                if p and not in_plugin_root(p):
                    return p.get("foreground_cwd") or p.get("cwd")
            return None

        self.spaces = []
        self.focused_ws = snap.get("focused_workspace_id")
        for ws in sorted(snap.get("workspaces", []), key=lambda w: w.get("number", 0)):
            self.spaces.append({
                "id": ws["workspace_id"],
                "label": ws.get("label") or ws["workspace_id"],
                "status": ws.get("agent_status", "unknown"),
                "focused": ws["workspace_id"] == self.focused_ws,
                "git": git_info(work_cwd(ws.get("active_tab_id"))) if self.visible else ("", 0, 0),
            })
        ws_label = {s["id"]: s["label"] for s in self.spaces}

        agents = []
        for a in snap.get("agents", []):
            if a.get("pane_id") == self.pane_id:
                continue
            agents.append({
                "id": a["pane_id"],
                "status": a.get("agent_status", "unknown"),
                "workspace": ws_label.get(a.get("workspace_id"), a.get("workspace_id", "")),
                "tab": (tabs.get(a.get("tab_id")) or {}).get("label", ""),
                "agent": a.get("agent") or "",
                "focused": bool(a.get("focused")),
                "seq": a.get("state_change_seq", 0),
            })
        agents.sort(key=lambda a: (AGENT_PRIORITY.get(a["status"], 9), -a["seq"]))
        self.agents = agents
        return True

    def keep_height(self, layout):
        parent_h = bar_parent(layout, self.pane_id)
        if not parent_h:
            return
        mine_h = next(p["rect"]["height"] for p in layout["panes"] if p["pane_id"] == self.pane_id)
        try:
            pty_rows = os.get_terminal_size(sys.stdin.fileno()).lines
        except OSError:
            return
        target = BAR_ROWS + min(2, max(0, mine_h - pty_rows))
        if mine_h == target or parent_h < target + 4:
            return
        if self.last_fit == (parent_h, mine_h, target):
            return
        self.last_fit = (parent_h, mine_h, target)
        try:
            fit_bar(self.tab_id, self.pane_id, parent_h, target)
        except (OSError, HerdrError):
            pass

    def nudge_neighbours(self):
        """Advance the revision of every other pane in this tab, so tab titlers that
        name an unfocused tab after its most recently changed pane pass the bar over."""
        try:
            panes = snapshot().get("panes", [])
        except (OSError, HerdrError, ValueError):
            return
        stamp = str(int(time.time()))
        for p in panes:
            if p.get("tab_id") != self.tab_id or p["pane_id"] == self.pane_id or in_plugin_root(p):
                continue
            try:
                call("pane.report_metadata", {
                    "pane_id": p["pane_id"],
                    "source": PLUGIN_ID,
                    "tokens": {"bottombar_nudge": stamp},
                    "ttl_ms": 5000,
                })
            except (OSError, HerdrError):
                pass

    def lists(self):
        return [self.spaces, self.agents]

    def selected_index(self, col):
        items = self.lists()[col]
        key = self.sel[col]
        for i, item in enumerate(items):
            if item["id"] == key:
                return i
        if col == 0:
            for i, item in enumerate(items):
                if item["focused"]:
                    return i
        return 0 if items else None

    def body_rows(self):
        return max(0, self.height - 1)

    def keep_visible(self, col, idx):
        rows = self.body_rows()
        if idx is None or rows == 0:
            return
        if idx < self.offset[col]:
            self.offset[col] = idx
        elif idx >= self.offset[col] + rows:
            self.offset[col] = idx - rows + 1

    def clamp_offsets(self):
        rows = self.body_rows()
        for col, items in enumerate(self.lists()):
            self.offset[col] = max(0, min(self.offset[col], len(items) - rows))

    def header(self, title, items, col, width, extra=()):
        rows = self.body_rows()
        right = list(extra)
        if len(items) > rows > 0:
            start = self.offset[col] + 1
            end = min(len(items), self.offset[col] + rows)
            right = [("%d–%d/%d " % (start, end, len(items)), {"fg": DIM})]
        style = {"fg": ACCENT if self.focused and self.col == col else DIM}
        return cell(width, [(" " + title, style)], right, min_left=text_width(title) + 2)

    def space_row(self, item, width, selected):
        icon, color = STATUS.get(item["status"], STATUS["unknown"])
        focused = item["focused"]
        left = [(" ", {}), (icon, {"fg": color}), (" ", {}),
                (item["label"], {"fg": FG_FOCUSED if focused else FG, "bold": focused})]
        branch, ahead, behind = item["git"]
        if branch:
            left.append(("  " + branch, {"fg": MAGENTA if focused else DIM}))
        if ahead:
            left.append((" ↑%d" % ahead, {"fg": GREEN}))
        if behind:
            left.append((" ↓%d" % behind, {"fg": RED}))
        bg = SEL_BG if selected else (ROW_BG if focused else None)
        return cell(width, left, bg=bg)

    def agent_row(self, item, width, selected):
        icon, color = STATUS.get(item["status"], STATUS["unknown"])
        left = [(" ", {}), (icon, {"fg": color}), (" ", {}),
                (item["workspace"], {"fg": FG_FOCUSED if item["focused"] else FG,
                                     "bold": item["focused"]})]
        if item["tab"] and not item["tab"].isdigit():
            left.append((" · " + item["tab"], {"fg": MUTED}))
        right = [(item["agent"] + " ", {"fg": DIM})] if item["agent"] else ()
        bg = SEL_BG if selected else (ROW_BG if item["focused"] else None)
        return cell(width, left, right, bg=bg)

    def summary(self):
        counts = {}
        for a in self.agents:
            counts[a["status"]] = counts.get(a["status"], 0) + 1
        parts = []
        for status in ("blocked", "done", "working"):
            if counts.get(status):
                if parts:
                    parts.append((" · ", {"fg": DIM}))
                parts.append(("%d %s" % (counts[status], status), {"fg": STATUS[status][1]}))
        if parts:
            parts.append((" ", {}))
        return parts

    def render(self):
        w, h = self.width, self.height
        if self.error:
            lines = [cell(w, [(" herdr: " + self.error, {"fg": RED})])]
            return lines + [""] * (h - 1)
        spaces, agents = self.lists()
        left_w = max(10, w // 2)
        right_w = max(0, w - left_w - 1)
        self.clamp_offsets()
        sel = [self.selected_index(0), self.selected_index(1)] if self.focused else [None, None]
        bar = sgr(fg=GUTTER) + "│" + "\x1b[0m"
        lines = [self.header("spaces", spaces, 0, left_w) + bar +
                 self.header("agents", agents, 1, right_w, self.summary())]
        self.rows = []
        for r in range(self.body_rows()):
            i0, i1 = self.offset[0] + r, self.offset[1] + r
            if i0 < len(spaces):
                left = self.space_row(spaces[i0], left_w, self.col == 0 and sel[0] == i0)
            else:
                left = cell(left_w, [])
            if i1 < len(agents):
                right = self.agent_row(agents[i1], right_w, self.col == 1 and sel[1] == i1)
            elif r == 0 and not agents:
                right = cell(right_w, [(" no agents", {"fg": DIM})])
            else:
                right = cell(right_w, [])
            lines.append(left + bar + right)
            self.rows.append((i0, i1))
        return lines[:h]

    def activate(self, col, idx):
        items = self.lists()[col]
        if idx is None or not 0 <= idx < len(items):
            return
        item = items[idx]
        try:
            if call("pane.get", {"pane_id": self.pane_id})["pane"].get("focused"):
                call("pane.focus_direction", {"pane_id": self.pane_id, "direction": "up"})
            if col == 0:
                call("workspace.focus", {"workspace_id": item["id"]})
            else:
                call("agent.focus", {"target": item["id"]})
        except (OSError, HerdrError):
            pass

    def leave(self):
        try:
            call("pane.focus_direction", {"pane_id": self.pane_id, "direction": "up"})
        except (OSError, HerdrError):
            pass

    def move(self, delta):
        idx = self.selected_index(self.col)
        items = self.lists()[self.col]
        if idx is None:
            return
        idx = max(0, min(len(items) - 1, idx + delta))
        self.sel[self.col] = items[idx]["id"]
        self.keep_visible(self.col, idx)

    def scroll(self, col, delta):
        self.offset[col] += delta
        self.clamp_offsets()

    def on_mouse(self, button, x, y, press):
        col = 0 if x <= max(10, self.width // 2) else 1
        if button in (64, 65):
            self.scroll(col, -1 if button == 64 else 1)
            return
        row = y - 2
        if button != 0 or not press or not 0 <= row < len(self.rows):
            return
        idx = self.rows[row][col]
        if idx < len(self.lists()[col]):
            self.col = col
            self.sel[col] = self.lists()[col][idx]["id"]
            self.activate(col, idx)

    def on_key(self, key):
        if key in (b"j", b"\x1b[B", b"\x1bOB"):
            self.move(1)
        elif key in (b"k", b"\x1b[A", b"\x1bOA"):
            self.move(-1)
        elif key in (b"h", b"\x1b[D", b"\x1bOD"):
            self.col = 0
        elif key in (b"l", b"\x1b[C", b"\x1bOC"):
            self.col = 1
        elif key in (b"\t", b"\x1b[Z"):
            self.col = 1 - self.col
        elif key in (b"\r", b"\n", b" "):
            self.activate(self.col, self.selected_index(self.col))
        elif key in (b"\x1b", b"q"):
            self.leave()


def parse_input(data, panel):
    pos = 0
    while pos < len(data):
        m = MOUSE_RE.match(data, pos)
        if m:
            panel.on_mouse(int(m.group(1)), int(m.group(2)), int(m.group(3)), m.group(4) == b"M")
            pos = m.end()
            continue
        if data[pos:pos + 1] == b"\x1b" and pos + 1 == len(data):
            panel.on_key(b"\x1b")
            break
        k = KEY_RE.match(data, pos)
        panel.on_key(k.group(0))
        pos = k.end()


def main():
    fd = sys.stdin.fileno()
    saved = termios.tcgetattr(fd)
    tty.setraw(fd)
    out = sys.stdout
    out.write("\x1b[?1049h\x1b[?25l\x1b[?1000h\x1b[?1006h")
    out.flush()

    wake_r, wake_w = os.pipe()
    os.set_blocking(wake_w, False)
    signal.set_wakeup_fd(wake_w)
    signal.signal(signal.SIGWINCH, lambda *_: None)
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    signal.signal(signal.SIGHUP, lambda *_: sys.exit(0))

    panel = Panel()
    sub = None
    sub_retry = 0.0
    due = 0.0
    last_refresh = 0.0
    nudge_at = time.monotonic() + NUDGE_DELAY

    def draw():
        size = os.get_terminal_size(fd)
        panel.width, panel.height = size.columns, size.lines
        frame = ["\x1b[?2026h"]
        for i, line in enumerate(panel.render()):
            frame.append("\x1b[%d;1H%s\x1b[0m\x1b[K" % (i + 1, line))
        frame.append("\x1b[J\x1b[?2026l")
        out.write("".join(frame))
        out.flush()

    try:
        while True:
            now = time.monotonic()
            if sub is None and now >= sub_retry:
                try:
                    sub = Subscription(EVENTS)
                except OSError:
                    sub_retry = now + 2.0
                due = due or now
            if nudge_at and now >= nudge_at:
                panel.nudge_neighbours()
                nudge_at = 0.0
            poll = VISIBLE_POLL if panel.visible else HIDDEN_POLL
            if not due and now - last_refresh >= poll:
                due = now
            if due and now >= due:
                if not panel.refresh():
                    return
                last_refresh = time.monotonic()
                due = 0.0
                draw()
                continue
            timeout = (due - now) if due else max(0.05, poll - (now - last_refresh))
            if nudge_at:
                timeout = max(0.05, min(timeout, nudge_at - now))
            fds = [fd, wake_r] + ([sub] if sub else [])
            ready, _, _ = select.select(fds, [], [], timeout)
            if wake_r in ready:
                os.read(wake_r, 512)
                due = due or time.monotonic() + DEBOUNCE
                draw()
            if fd in ready:
                data = os.read(fd, 4096)
                if not data:
                    return
                parse_input(data, panel)
                draw()
            if sub and sub in ready:
                msgs = sub.drain()
                if msgs is None:
                    sub.close()
                    sub = None
                    sub_retry = time.monotonic() + 1.0
                elif msgs and not due:
                    due = time.monotonic() + DEBOUNCE
    finally:
        out.write("\x1b[?1006l\x1b[?1000l\x1b[?25h\x1b[?1049l")
        out.flush()
        termios.tcsetattr(fd, termios.TCSADRAIN, saved)


if __name__ == "__main__":
    main()
