"""The terminal port: where sessions live and how to type into them."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


class TerminalError(Exception):
    """The terminal could not be read or driven (e.g. macOS denied Automation access)."""


@dataclass(frozen=True)
class TabInfo:
    window_id: str
    window_index: int  # 1-based, front to back
    tab_index: int  # 1-based within the window (split panes share a tab index)
    tty: str  # "ttys012"
    name: str = ""


class Terminal(Protocol):
    def layout(self) -> list[TabInfo]:
        """All tabs; empty when the terminal app is not running. Raises TerminalError."""
        ...

    def write_text(self, tty: str, text: str) -> bool:
        """Type ``text`` + Enter into the tab on ``tty``. False when no such tab."""
        ...

    def new_tab(self, window_id: str, text: str) -> bool:
        """Open a tab in ``window_id`` and type ``text``. False when no such window."""
        ...

    def new_window(self, texts: list[str]) -> str:
        """Open a window with one tab per text; returns its id. Raises TerminalError."""
        ...

    def label(self, tty: str, text: str, color: tuple[int, int, int] | None) -> bool:
        """Show ``text`` as the tab's badge and ``color`` as its tab color (empty text and
        None clear them). False when there is no such tab."""
        ...
