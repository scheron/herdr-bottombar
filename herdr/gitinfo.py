import os
import subprocess
import time

_git_cache = {}


def git_info(path):
    """Return (branch, ahead, behind) for the checkout holding `path`, cached for a few seconds."""
    if not path:
        return "", 0, 0
    hit = _git_cache.get(path)
    now = time.monotonic()
    if hit and now - hit[0] < 5:
        return hit[1]
    branch = _read_branch(path)
    ahead = behind = 0
    if branch and not branch.startswith("@"):
        ahead, behind = _upstream_counts(path)
    info = (branch.lstrip("@"), ahead, behind)
    _git_cache[path] = (now, info)
    return info


def _upstream_counts(path):
    try:
        out = subprocess.run(
            ["git", "-C", path, "rev-list", "--left-right", "--count", "HEAD...@{upstream}"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=1, text=True,
        ).stdout.split()
        return int(out[0]), int(out[1])
    except (OSError, ValueError, IndexError, subprocess.TimeoutExpired):
        return 0, 0


def _read_branch(path):
    d = path
    while True:
        dotgit = os.path.join(d, ".git")
        if os.path.isdir(dotgit):
            head = os.path.join(dotgit, "HEAD")
            break
        if os.path.isfile(dotgit):
            try:
                with open(dotgit) as f:
                    gitdir = f.read().strip().split("gitdir:", 1)[-1].strip()
            except OSError:
                return ""
            head = os.path.join(d, gitdir, "HEAD")
            break
        parent = os.path.dirname(d)
        if parent == d:
            return ""
        d = parent
    try:
        with open(head) as f:
            ref = f.read().strip()
    except OSError:
        return ""
    if ref.startswith("ref: refs/heads/"):
        return ref[len("ref: refs/heads/"):]
    return "@" + ref[:7]
