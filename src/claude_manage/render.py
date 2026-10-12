"""Terminal output: the session tree shared by show/capture/resume/relaunch.

Each session is one aligned row: tab, state icon, title, id, version, cwd and a
note (the live check, or what resume/relaunch will do or did to it).
"""

from __future__ import annotations

import os
import shutil
import sys
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import TextIO

from .model import BUSY_STATUSES, Session, is_outdated

GREEN, RED, YELLOW, CYAN, DIM, BOLD = "32", "31", "33", "36", "2", "1"
TITLE_MAX = 34
MIN_CWD = 12

Seg = tuple[str, str]  # (text, ANSI SGR code, "" for none)
Cell = list[Seg]


@dataclass(frozen=True)
class Style:
    color: bool = False
    width: int = 120

    @classmethod
    def detect(cls, plain: bool = False, stream: TextIO | None = None) -> Style:
        stream = stream or sys.stdout
        is_tty = bool(stream is not None and stream.isatty())
        color = not plain and "NO_COLOR" not in os.environ
        color = color and (is_tty or "FORCE_COLOR" in os.environ)
        return cls(color=color, width=shutil.get_terminal_size((120, 24)).columns)

    def paint(self, text: str, code: str) -> str:
        return f"\033[{code}m{text}\033[0m" if self.color and code and text else text

    def cell(self, cell: Cell) -> str:
        return "".join(self.paint(text, code) for text, code in cell)


@dataclass
class Row:
    session: Session
    icon: Seg
    note: Cell = field(default_factory=list)
    version: str = ""  # shown version: live when running, else captured


@dataclass
class Block:
    """A group in the tree: ``▾ name · detail`` followed by its rows."""

    name: str
    detail: str
    rows: list[Row] = field(default_factory=list)


def state_icon(status: str, running: bool | None) -> Seg:
    """● idle, ◐ busy/shell/waiting, ◇ status unknown (older Claude Code), ○ not running.

    ``running=None`` means there was no live check: the captured status is shown.
    """
    if running is False:
        return ("○", RED)
    if status in BUSY_STATUSES:
        return ("◐", YELLOW)
    if status == "idle":
        return ("●", GREEN)
    return ("◇", YELLOW) if running else ("·", DIM)


def display_width(text: str) -> int:
    """Terminal columns: wide (CJK, emoji) characters take 2, combining marks 0."""
    width = 0
    for ch in text:
        if unicodedata.category(ch) in ("Mn", "Me", "Cf"):
            continue
        width += 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
    return width


def truncate(text: str, width: int) -> str:
    """``text`` cut to ``width`` columns, ending in ``…`` when cut."""
    if display_width(text) <= width:
        return text
    out, used = "", 0
    for ch in text:
        w = display_width(ch)
        if used + w > width - 1:
            break
        out, used = out + ch, used + w
    return out + "…"


def short_path(path: str, width: int) -> str:
    """``/Users/me/a/b/c`` -> ``~/…/b/c``, keeping as many trailing parts as fit."""
    home = str(Path.home())
    if path == home or path.startswith(home + os.sep):
        path = "~" + path[len(home) :]
    if display_width(path) <= width:
        return path
    head, _, rest = path.partition(os.sep)
    parts = rest.split(os.sep)
    kept = parts[-1]
    for part in reversed(parts[:-1]):
        if display_width(f"{head}{os.sep}…{os.sep}{part}{os.sep}{kept}") > width:
            break
        kept = f"{part}{os.sep}{kept}"
    short = f"{head}{os.sep}…{os.sep}{kept}"
    if display_width(short) <= width:
        return short
    tail, used = "", 1
    for ch in reversed(path):
        if used + display_width(ch) > width:
            break
        tail, used = ch + tail, used + display_width(ch)
    return "…" + tail


def fmt_time(stamp: str) -> str:
    """``2026-10-12T14:42:27`` -> ``2026-10-12 14:42``."""
    return stamp.replace("T", " ")[:16] if stamp else "?"


def tree(
    header: Cell,
    blocks: Sequence[Block],
    footer: Cell,
    installed: str,
    style: Style,
    notes: Sequence[Cell] = (),
) -> str:
    """Header, one block per group, the footer, then any ``notes`` (warnings)."""
    lines = [style.cell(header)]
    lines += _rows(blocks, installed, style)
    if footer:
        lines += ["", style.cell(footer)]
    lines += [style.cell(note) for note in notes]
    return "\n".join(lines)


def join(parts: Sequence[Cell], sep: str = " · ") -> Cell:
    out: Cell = []
    for i, part in enumerate(p for p in parts if p):
        if i:
            out.append((sep, DIM))
        out += part
    return out


def _width(cell: Cell) -> int:
    return sum(display_width(text) for text, _ in cell)


def _fixed_cells(row: Row, installed: str) -> list[Cell]:
    s = row.session
    title_cell: Cell = [(truncate(s.label, TITLE_MAX), "")]
    if s.guessed:
        title_cell.append((" ?", YELLOW))
    version: Cell = [(f"v{row.version}", "")] if row.version else [("-", DIM)]
    if is_outdated(row.version, installed):
        version.append((" ↑", YELLOW))
    tab = str(s.tab) if s.tab else "-"
    return [[(tab, DIM)], [row.icon], title_cell, [(s.short_id, DIM)], version]


def _rows(blocks: Sequence[Block], installed: str, style: Style) -> list[str]:
    rows = [row for block in blocks for row in block.rows]
    if not rows:
        return []
    fixed = [_fixed_cells(row, installed) for row in rows]
    widths = [max(_width(cells[i]) for cells in fixed) for i in range(5)]
    note_width = max(_width(row.note) for row in rows)
    used = 2 + sum(widths) + 2 * 6 + note_width  # indent, columns, separators, note
    cwd_max = max(MIN_CWD, style.width - used)
    cwds: list[Cell] = [
        [(short_path(row.session.cwd, cwd_max) if row.session.cwd else "-", DIM)] for row in rows
    ]
    widths.append(max(_width(c) for c in cwds))

    lines: list[str] = []
    it = iter(zip(rows, fixed, cwds, strict=True))
    for block in blocks:
        lines.append("")
        header: Cell = [("▾ ", DIM), (block.name, BOLD)]
        if block.detail:
            header.append((f" · {block.detail}", DIM))
        lines.append(style.cell(header))
        for _ in block.rows:
            row, cells, cwd = next(it)
            parts = [
                style.cell(cell) + " " * (widths[i] - _width(cell))
                for i, cell in enumerate([*cells, cwd])
            ]
            lines.append(("  " + "  ".join(parts) + "  " + style.cell(row.note)).rstrip())
    return lines
