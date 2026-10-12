"""Reopen captured sessions that are not running (after a reboot, or closed tabs)."""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from .commands import launch_command
from .discovery import Discovery
from .model import Group, Session, Snapshot
from .render import (
    BOLD,
    CYAN,
    DIM,
    GREEN,
    RED,
    YELLOW,
    Block,
    Cell,
    Row,
    Style,
    fmt_time,
    join,
    state_icon,
    tree,
)
from .system import System
from .terminal import Terminal, TerminalError


@dataclass
class Item:
    session: Session
    skip: str = ""  # why it is not opened (empty: open it)
    pid: int = 0  # the running process, when skipped for running or once verified
    where: str = ""  # after execution: where it was opened
    error: str = ""  # after execution: why it could not be opened
    verified: bool | None = None  # after verification


@dataclass
class GroupPlan:
    group: Group
    items: list[Item] = field(default_factory=list)
    window_id: str = ""  # a live window that verifiably still holds this group

    @property
    def todo(self) -> list[Item]:
        return [i for i in self.items if not i.skip]


def plan(
    snap: Snapshot,
    discovery: Discovery,
    groups: list[str],
    *,
    force: bool = False,
    include_guessed: bool = False,
) -> list[GroupPlan]:
    running = discovery.by_id()
    maybe = discovery.by_id(guessed=True)
    seen: set[str] = set()
    plans: list[GroupPlan] = []
    for group in snap.groups:
        if groups and group.name not in groups:
            continue
        gp = GroupPlan(group)
        for s in group.sessions:
            item = Item(s)
            if s.session_id in seen:
                item.skip = "duplicate: already in this resume"
            elif s.guessed and not include_guessed:
                item.skip = "id is a guess (use --include-guessed)"
            elif not force and s.session_id in running:
                item.skip, item.pid = "running", running[s.session_id].session.pid
            elif not force and s.session_id in maybe:
                item.skip, item.pid = "maybe running (id guessed)", maybe[s.session_id].session.pid
            seen.add(s.session_id)
            gp.items.append(item)
        # Window ids only mean something while iTerm2 runs, so reuse a window only when
        # one of this group's sessions is verifiably running in it.
        for s in group.sessions:
            live = running.get(s.session_id)
            if live is not None and live.tab is not None:
                gp.window_id = live.tab.window_id
                break
        plans.append(gp)
    return plans


def execute(plans: list[GroupPlan], terminal: Terminal, claude_bin: str) -> None:
    """Open each group's sessions: in its live window when there is one (tab by tab,
    stopping at the first failure), the rest in one new window."""
    for gp in plans:
        remaining = list(gp.todo)
        if gp.window_id:
            while remaining:
                item = remaining[0]
                try:
                    opened = terminal.new_tab(
                        gp.window_id, launch_command(item.session, claude_bin)
                    )
                except TerminalError:
                    opened = False
                if not opened:
                    break
                item.where = f"window {gp.window_id}"
                remaining.pop(0)
        if not remaining:
            continue
        try:
            window = terminal.new_window([launch_command(i.session, claude_bin) for i in remaining])
        except TerminalError as e:
            for item in remaining:
                item.error = str(e)
            continue
        for item in remaining:
            item.where = f"new window {window}"


def verify(plans: list[GroupPlan], system: System, timeout: float) -> None:
    """Wait until every opened session is running again (or ``timeout`` passes)."""
    pending = [i for gp in plans for i in gp.todo if i.where and i.session.resumable]
    for gp in plans:
        for item in gp.todo:
            if item.where and not item.session.resumable:
                item.verified = True  # a fresh session has no id to look for
    deadline = time.monotonic() + timeout
    while pending:
        running = system.discover(titles=False).by_id()
        for item in list(pending):
            if item.session.session_id in running:
                item.verified, item.pid = True, running[item.session.session_id].session.pid
                pending.remove(item)
        if not pending or time.monotonic() >= deadline:
            break
        time.sleep(0.5)
    for item in pending:
        item.verified = False


def failed(plans: list[GroupPlan]) -> bool:
    return any(i.error or i.verified is False for gp in plans for i in gp.items)


def view(
    snap: Snapshot,
    plans: list[GroupPlan],
    installed: str,
    style: Style,
    *,
    done: bool,
    timeout: float = 0,
) -> str:
    header: Cell = [
        ("claude-manage resume", BOLD),
        (f" · capture {fmt_time(snap.captured_at)}", DIM),
    ]
    blocks: list[Block] = []
    counts = {"open": 0, "opened": 0, "back": 0, "failed": 0, "running": 0, "skipped": 0}
    for gp in plans:
        if not gp.todo:
            detail = "nothing to open"
        elif done:
            detail = "opened in " + ", ".join(
                sorted({i.where for i in gp.todo if i.where}) or ["-"]
            )
        else:
            detail = "opens in " + (f"window {gp.window_id}" if gp.window_id else "a new window")
        block = Block(gp.group.name, detail)
        for item in gp.items:
            s = item.session
            note: Cell
            if item.skip == "running":
                counts["running"] += 1
                note = [("✓ running", GREEN), (f" pid {item.pid}", DIM)]
                block.rows.append(Row(s, state_icon("idle", True), note, s.version))
                continue
            if item.skip:
                counts["skipped"] += 1
                pid = f" pid {item.pid}" if item.pid else ""
                note = [(f"– {item.skip}{pid}", YELLOW)]
                block.rows.append(Row(s, state_icon("", False), note, s.version))
                continue
            if not done:
                counts["open"] += 1
                note = [("→ open", CYAN)]
            elif item.error:
                counts["failed"] += 1
                note = [(f"✗ not opened: {item.error}", RED)]
            elif item.verified is False:
                counts["failed"] += 1
                note = [(f"! opened, not seen running after {timeout:g}s", YELLOW)]
            elif item.verified:
                counts["back"] += 1
                note = [("✓ back", GREEN)] + ([(f" pid {item.pid}", "")] if item.pid else [])
            else:
                counts["opened"] += 1
                note = [("→ opened", CYAN)]
            if not s.resumable:
                note.append((" (no transcript: fresh session)", YELLOW))
            if s.dropped:
                note.append((f" (not replayed: {' '.join(s.dropped)})", DIM))
            block.rows.append(Row(s, state_icon("", False), note, s.version))
        blocks.append(block)
    footer = join(
        [
            [(f"→ {counts['open']} to open", CYAN)] if counts["open"] else [],
            [(f"✓ {counts['back']} back", GREEN)] if counts["back"] else [],
            [(f"→ {counts['opened']} opened", CYAN)] if counts["opened"] else [],
            [(f"✗ {counts['failed']} failed", RED)] if counts["failed"] else [],
            [(f"✓ {counts['running']} already running", GREEN)] if counts["running"] else [],
            [(f"– {counts['skipped']} skipped", YELLOW)] if counts["skipped"] else [],
        ]
    )
    return tree(header, blocks, footer or [("nothing to resume", DIM)], installed, style)
