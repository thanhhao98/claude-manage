"""The show/capture view: a capture verified against what is running now."""

from __future__ import annotations

from .live import Live
from .model import Snapshot, is_outdated
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


def snapshot_view(snap: Snapshot | None, live: Live | None, installed: str, style: Style) -> str:
    """The session tree. With ``live``, every row says whether it is running right now."""
    header: Cell = [("claude-manage", BOLD)]
    if snap is None:
        header.append((" · no capture yet (run `claude-manage capture`)", DIM))
    else:
        header.append((f" · capture {fmt_time(snap.captured_at)}", DIM))
        if snap.reason != "manual":
            header.append((f" · {snap.reason}", DIM))
    if installed:
        header.append((f" · installed v{installed}", DIM))

    blocks: list[Block] = []
    rows: list[Row] = []
    running = maybe = missing = 0
    for group in snap.groups if snap else []:
        n = len(group.sessions)
        where = f"iTerm window {group.window_index}" if group.window_id else "not in iTerm2"
        block = Block(group.name, f"{where} · {n} tab{'s' * (n != 1)}")
        for s in group.sessions:
            if live is None:
                row = Row(s, state_icon(s.status, None), version=s.version)
            elif (now := live.running.get(s.session_id)) is not None:
                running += 1
                note: Cell = [("✓", GREEN), (f" pid {now.pid}", "")]
                if now.status:
                    note.append((f" {now.status}", DIM))
                row = Row(s, state_icon(now.status, True), note, now.version or s.version)
            elif (guess := live.guessed.get(s.session_id)) is not None:
                maybe += 1
                note = [("? ", YELLOW), (f"pid {guess.pid}", ""), (" (id guessed)", DIM)]
                row = Row(s, state_icon(guess.status, True), note, guess.version or s.version)
            else:
                missing += 1
                row = Row(s, state_icon("", False), [("✗ not running", RED)], s.version)
            block.rows.append(row)
        blocks.append(block)
        rows += block.rows

    untracked = live.untracked if live else []
    if untracked:
        hint = "run `claude-manage capture` to add" if snap else "run `claude-manage capture`"
        extra = Block("not in capture", f"{len(untracked)} running · {hint}")
        for item in untracked:
            s = item.session
            note = [("+", CYAN), (f" pid {s.pid}", "")]
            if s.status:
                note.append((f" {s.status}", DIM))
            extra.rows.append(Row(s, state_icon(s.status, True), note, s.version))
        blocks.append(extra)
        rows += extra.rows

    outdated = sum(is_outdated(r.version, installed) for r in rows)
    guessed = sum(r.session.guessed for r in rows)
    parts: list[Cell] = []
    if live is None:
        n = sum(len(b.rows) for b in blocks)
        parts.append([(f"{n} sessions in {len(blocks)} groups", "")] if snap else [])
    elif snap is None:
        parts.append([(f"{len(untracked)} running, none captured", "")] if untracked else [])
    else:
        parts.append([(f"✓ {running} running", GREEN)])
        if maybe:
            parts.append([(f"? {maybe} maybe running", YELLOW)])
        if missing:
            parts.append([(f"✗ {missing} not running", RED)])
    if outdated:
        parts.append([(f"↑ {outdated} outdated", YELLOW)])
    if guessed:
        parts.append([(f"? {guessed} guessed id{'s' * (guessed != 1)}", YELLOW)])
    if untracked and snap is not None:
        parts.append([(f"+ {len(untracked)} not in capture", CYAN)])
    footer = join(parts)
    if not footer:
        footer = [("nothing captured yet" if snap is None else "empty capture", DIM)]
    return tree(header, blocks, footer, installed, style, warnings(live))


def warnings(live: Live | None) -> list[Cell]:
    if live is None:
        return []
    notes: list[Cell] = []
    if live.layout_error:
        notes.append([("! ", YELLOW), (f"iTerm2 tabs unknown: {live.layout_error}", "")])
    if live.unresolved:
        pids = ", ".join(str(p.pid) for p in live.unresolved)
        text = f"{len(live.unresolved)} claude process(es) without an identifiable session"
        notes.append([("! ", YELLOW), (f"{text} (pid {pids})", "")])
    return notes
