"""Snapshot data model. Capture files are plain JSON dumps of these dataclasses."""

from __future__ import annotations

import os
import re
from collections.abc import Iterator
from dataclasses import asdict, dataclass, field, fields
from typing import Any

SCHEMA = 1
BUSY_STATUSES = frozenset({"busy", "shell", "waiting"})


@dataclass
class Session:
    """One Claude Code session: one iTerm2 tab running ``claude``."""

    session_id: str
    cwd: str = ""
    title: str = ""
    tab: int = 0
    flags: list[str] = field(default_factory=list)  # options replayed on relaunch/resume
    dropped: list[str] = field(default_factory=list)  # argv not replayed (prompt, unknown, ...)
    status: str = ""  # busy | shell | idle | waiting | "" (unknown: older Claude Code)
    version: str = ""
    resumable: bool = True  # a transcript exists, so `claude --resume <id>` works
    guessed: bool = False  # no session record (older Claude Code): the id is inferred
    pid: int = 0
    tty: str = ""

    @property
    def short_id(self) -> str:
        return self.session_id[:8]

    @property
    def label(self) -> str:
        return self.title or f"{self.short_id}… (unnamed)"


@dataclass
class Group:
    """One iTerm2 window."""

    name: str
    window_id: str = ""
    window_index: int = 0
    sessions: list[Session] = field(default_factory=list)


@dataclass
class Snapshot:
    captured_at: str
    groups: list[Group] = field(default_factory=list)
    reason: str = "manual"  # manual | pre-relaunch | post-relaunch
    installed_version: str = ""
    schema: int = SCHEMA

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Snapshot:
        """Tolerant of hand edits: unknown keys are ignored and missing ones defaulted."""
        groups = [
            Group(
                name=str(g.get("name") or f"window-{i}"),
                window_id=str(g.get("window_id", "")),
                window_index=int(g.get("window_index", i)),
                sessions=[_session(s) for s in g.get("sessions", [])],
            )
            for i, g in enumerate(data.get("groups", []), start=1)
        ]
        return cls(
            captured_at=str(data.get("captured_at", "")),
            groups=groups,
            reason=str(data.get("reason", "manual")),
            installed_version=str(data.get("installed_version", "")),
            schema=int(data.get("schema", SCHEMA)),
        )

    def sessions(self) -> Iterator[tuple[Group, Session]]:
        for group in self.groups:
            for session in group.sessions:
                yield group, session

    def group(self, name: str) -> Group | None:
        return next((g for g in self.groups if g.name == name), None)


_SESSION_FIELDS = frozenset(f.name for f in fields(Session))


def _session(raw: dict[str, Any]) -> Session:
    if not isinstance(raw, dict) or not raw.get("session_id"):
        raise ValueError(f"session without session_id: {raw!r}")
    values = {k: v for k, v in raw.items() if k in _SESSION_FIELDS}
    for name, value in values.items():
        kind = _FIELD_TYPES[name]
        if kind is list:
            if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
                raise ValueError(f"{name} must be a list of strings: {value!r}")
        elif kind is int:
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError(f"{name} must be an integer: {value!r}")
        elif not isinstance(value, kind):
            raise ValueError(f"{name} must be {kind.__name__}: {value!r}")
    session = Session(**values)
    session.cwd = os.path.expanduser(session.cwd)  # hand edits may use ~/...
    return session


_FIELD_TYPES: dict[str, type] = {
    "session_id": str, "cwd": str, "title": str, "tab": int, "flags": list, "dropped": list,
    "status": str, "version": str, "resumable": bool, "guessed": bool, "pid": int, "tty": str,
}  # fmt: skip


def parse_version(text: str) -> tuple[int, ...] | None:
    match = re.match(r"\s*v?(\d+(?:\.\d+)*)", text or "")
    return tuple(int(p) for p in match.group(1).split(".")) if match else None


def is_outdated(version: str, installed: str) -> bool:
    current, latest = parse_version(version), parse_version(installed)
    return current is not None and latest is not None and current < latest
