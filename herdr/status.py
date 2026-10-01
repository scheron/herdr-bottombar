from gitinfo import git_info
from herdr_api import AGENT_GLYPHS, AGENT_PRIORITY, HerdrError, snapshot


def main():
    try:
        snap = snapshot()
    except (OSError, HerdrError, ValueError):
        return
    panes = {p["pane_id"]: p for p in snap.get("panes", [])}
    ws_id = snap.get("focused_workspace_id")
    tab_id = snap.get("focused_tab_id")
    ws = next((w for w in snap.get("workspaces", []) if w["workspace_id"] == ws_id), None)
    parts = []
    if ws:
        focused = panes.get(snap.get("focused_pane_id")) or {}
        branch, ahead, behind = git_info(focused.get("foreground_cwd") or focused.get("cwd"))
        space = "%s %s" % (AGENT_GLYPHS.get(ws.get("agent_status"), "·"), ws.get("label") or ws_id)
        if branch:
            space += "  " + branch
        if ahead:
            space += " ↑%d" % ahead
        if behind:
            space += " ↓%d" % behind
        parts.append(space)
    agents = sorted(snap.get("agents", []),
                    key=lambda a: (AGENT_PRIORITY.get(a.get("agent_status"), 9),
                                   -a.get("state_change_seq", 0)))
    here = sorted((a for a in agents if a.get("tab_id") == tab_id), key=lambda a: not a.get("focused"))
    agent = (here or agents or [None])[0]
    if agent:
        status = agent.get("agent_status", "unknown")
        text = "%s %s %s" % (AGENT_GLYPHS.get(status, "·"), agent.get("agent") or "agent", status)
        if agent.get("workspace_id") != ws_id:
            other = next((w for w in snap.get("workspaces", [])
                          if w["workspace_id"] == agent.get("workspace_id")), {})
            text += " · " + (other.get("label") or agent.get("workspace_id", ""))
        parts.append(text)
    print("  │  ".join(parts))


if __name__ == "__main__":
    main()
