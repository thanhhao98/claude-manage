"""Make groups visible in iTerm2: a badge (watermark text) and a tab color per group.

Labels are iTerm2 escape sequences written to the tab's terminal device, so they are
display state, never typed input: nothing reaches the shell or Claude.
"""

from __future__ import annotations

import base64
import hashlib

from .discovery import Discovery
from .model import Snapshot
from .terminal import Terminal, TerminalError

Color = tuple[int, int, int]

# Distinct, readable tab colors; a group keeps its color as long as it keeps its name.
PALETTE: tuple[Color, ...] = (
    (231, 111, 81), (42, 157, 143), (233, 196, 106), (69, 123, 157),
    (178, 102, 255), (244, 162, 97), (82, 183, 136), (230, 57, 70),
    (100, 149, 237), (199, 125, 186),
)  # fmt: skip


def color_for(group: str) -> Color:
    digest = hashlib.sha1(group.encode(), usedforsecurity=False).digest()
    return PALETTE[digest[0] % len(PALETTE)]


def sequences(text: str, color: Color | None) -> str:
    """iTerm2 OSC sequences: badge text (empty clears it) and tab color (None resets)."""
    badge = base64.b64encode(text.encode()).decode()
    out = f"\033]1337;SetBadgeFormat={badge}\a\033]1337;SetUserVar=claudeManageGroup={badge}\a"
    if color is None:
        return out + "\033]6;1;bg;*;default\a"
    for channel, value in zip(("red", "green", "blue"), color, strict=True):
        out += f"\033]6;1;bg;{channel};brightness;{value}\a"
    return out


def apply(snap: Snapshot, discovery: Discovery, terminal: Terminal, *, clear: bool = False) -> int:
    """Label every running session of ``snap`` with its group; returns how many."""
    running = discovery.by_id(guessed=True)
    done = 0
    for group, session in snap.sessions():
        item = running.get(session.session_id)
        if item is None or item.tab is None:
            continue
        text, color = ("", None) if clear else (group.name, color_for(group.name))
        try:
            done += terminal.label(item.session.tty, text, color)
        except TerminalError:
            continue
    return done
