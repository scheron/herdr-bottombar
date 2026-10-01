import itertools
import json
import os
import socket

PLUGIN_ID = os.environ.get("HERDR_PLUGIN_ID", "scheron.bottombar")
PLUGIN_ROOT = os.path.realpath(
    os.environ.get("HERDR_PLUGIN_ROOT") or os.path.join(os.path.dirname(__file__), ".."))
STATE_DIR = os.environ.get("HERDR_PLUGIN_STATE_DIR") or os.path.expanduser(
    "~/.local/state/herdr/plugins/" + PLUGIN_ID)
VERTICAL_FLAG = os.path.join(STATE_DIR, "vertical")
MINIMIZED_FLAG = os.path.join(STATE_DIR, "minimized")
REGISTRY = os.path.join(STATE_DIR, "bars.json")

BAR_ROWS = 7

AGENT_GLYPHS = {"blocked": "●", "working": "●", "done": "●", "idle": "○", "unknown": "·"}
AGENT_PRIORITY = {"blocked": 0, "done": 1, "working": 2, "idle": 3, "unknown": 4}

_ids = itertools.count(1)


class HerdrError(Exception):
    def __init__(self, code, message):
        super().__init__(f"{code}: {message}")
        self.code = code


def socket_path():
    path = os.environ.get("HERDR_SOCKET_PATH")
    if path:
        return path
    return os.path.expanduser("~/.config/herdr/herdr.sock")


def _connect(timeout):
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    sock.connect(socket_path())
    return sock


def call(method, params=None, timeout=5.0):
    """Send one request and return its `result`, raising HerdrError on an error response."""
    req_id = f"bb{os.getpid()}_{next(_ids)}"
    line = json.dumps({"id": req_id, "method": method, "params": params or {}})
    with _connect(timeout) as sock:
        sock.sendall(line.encode() + b"\n")
        reader = sock.makefile("rb")
        for raw in reader:
            msg = json.loads(raw)
            if msg.get("id") != req_id:
                continue
            if "error" in msg:
                err = msg["error"]
                raise HerdrError(err.get("code"), err.get("message"))
            return msg.get("result", {})
    raise HerdrError("closed", f"{method}: connection closed without a response")


def snapshot():
    return call("session.snapshot")["snapshot"]


def is_vertical():
    return os.path.exists(VERTICAL_FLAG)


def is_minimized():
    return os.path.exists(MINIMIZED_FLAG)


def set_flag(path, on):
    os.makedirs(STATE_DIR, exist_ok=True)
    if on:
        open(path, "w").close()
    elif os.path.exists(path):
        os.remove(path)


def read_registry():
    try:
        with open(REGISTRY) as f:
            return set(json.load(f))
    except (OSError, ValueError):
        return set()


def write_registry(pane_ids):
    os.makedirs(STATE_DIR, exist_ok=True)
    tmp = REGISTRY + ".tmp"
    with open(tmp, "w") as f:
        json.dump(sorted(pane_ids), f)
    os.replace(tmp, REGISTRY)


def in_plugin_root(pane):
    return (pane.get("cwd") or "").rstrip("/") == PLUGIN_ROOT


def split_path(node, pane_id, path=()):
    """Path of booleans (True = second child) to the split whose direct child is `pane_id`."""
    if node.get("type") != "split":
        return None
    for second, child in ((False, node["first"]), (True, node["second"])):
        if child.get("type") == "pane" and child.get("pane_id") == pane_id:
            return list(path)
        found = split_path(child, pane_id, path + (second,))
        if found is not None:
            return found
    return None


def bar_parent(layout, pane_id):
    """Height of the down-split whose lower half is `pane_id`, or None when it has none."""
    if not layout or layout.get("zoomed"):
        return None
    mine = next((p["rect"] for p in layout.get("panes", []) if p["pane_id"] == pane_id), None)
    if not mine:
        return None
    bottom = mine["y"] + mine["height"]
    heights = [s["rect"]["height"] for s in layout.get("splits", [])
               if s.get("direction") == "down"
               and s["rect"]["x"] == mine["x"] and s["rect"]["width"] == mine["width"]
               and s["rect"]["y"] + s["rect"]["height"] == bottom
               and s["rect"]["height"] > mine["height"]]
    return min(heights) if heights else None


def fit_bar(tab_id, pane_id, parent_height, bar_height):
    """Set the split above the bar so the bar's rect is `bar_height` rows tall."""
    tree = call("layout.export", {"tab_id": tab_id})["layout"]["root"]
    path = split_path(tree, pane_id)
    if path is None:
        return
    call("layout.set_split_ratio", {
        "tab_id": tab_id,
        "path": path,
        "ratio": (parent_height - bar_height) / parent_height,
    })


class Subscription:
    """A long-lived events.subscribe connection; fileno() makes it usable with select()."""

    def __init__(self, types):
        self.sock = _connect(5.0)
        req = {
            "id": f"bbsub{os.getpid()}",
            "method": "events.subscribe",
            "params": {"subscriptions": [{"type": t} for t in types]},
        }
        self.sock.sendall(json.dumps(req).encode() + b"\n")
        self.sock.setblocking(False)
        self.buf = b""

    def fileno(self):
        return self.sock.fileno()

    def drain(self):
        """Read what is available; return the parsed messages, or None once the server hung up."""
        try:
            chunk = self.sock.recv(65536)
        except BlockingIOError:
            return []
        if not chunk:
            return None
        self.buf += chunk
        *lines, self.buf = self.buf.split(b"\n")
        out = []
        for raw in lines:
            if raw.strip():
                msg = json.loads(raw)
                if "error" in msg:
                    return None
                out.append(msg)
        return out

    def close(self):
        try:
            self.sock.close()
        except OSError:
            pass
