"""Read-only access to Claude Code's own state under ``~/.claude``."""

from __future__ import annotations

import json
import mmap
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
_STRING_VALUE = re.compile(rb'\s*:\s*"((?:[^"\\]|\\.)*)"')


@dataclass(frozen=True)
class SessionMeta:
    """``~/.claude/sessions/<pid>.json``, kept up to date by every running Claude Code."""

    pid: int
    session_id: str
    cwd: str = ""
    name: str = ""
    name_source: str = ""  # user | auto | derived | peer
    status: str = ""  # busy | shell | idle | waiting
    version: str = ""
    kind: str = ""  # interactive | bg | daemon | daemon-worker


class ClaudeHome:
    def __init__(self, sessions_dir: Path, projects_dir: Path) -> None:
        self.sessions_dir = sessions_dir
        self.projects_dir = projects_dir

    def meta(self, pid: int) -> SessionMeta | None:
        try:
            raw: Any = json.loads((self.sessions_dir / f"{pid}.json").read_text())
        except (OSError, ValueError):
            return None
        if not isinstance(raw, dict) or not raw.get("sessionId"):
            return None
        return SessionMeta(
            pid=pid,
            session_id=str(raw["sessionId"]),
            cwd=str(raw.get("cwd") or ""),
            name=str(raw.get("name") or ""),
            name_source=str(raw.get("nameSource") or ""),
            status=str(raw.get("status") or ""),
            version=str(raw.get("version") or ""),
            kind=str(raw.get("kind") or ""),
        )

    def project_dir(self, cwd: str) -> Path:
        """Where Claude Code keeps the transcripts of sessions started in ``cwd``."""
        return self.projects_dir / project_dir_name(cwd)

    def transcript(self, session_id: str, cwd: str = "") -> Path | None:
        if cwd:
            path = self.project_dir(cwd) / f"{session_id}.jsonl"
            if path.is_file():
                return path
        return next(iter(sorted(self.projects_dir.glob(f"*/{session_id}.jsonl"))), None)

    def title(self, transcript: Path | None) -> str:
        """The last /rename title, else the last auto-generated one."""
        if transcript is None:
            return ""
        return (
            _last_string(transcript, b'"customTitle"')
            or _last_string(transcript, b'"aiTitle"')
            or ""
        )

    def find_by_title(self, name: str, cwd: str) -> str | None:
        """The session ``claude --resume <name>`` picks in ``cwd``."""
        for path in self._newest_first(cwd):
            if _last_string(path, b'"customTitle"') == name:
                return path.stem
        return None

    def newest_transcript(
        self, cwd: str, *, exclude: frozenset[str] = frozenset(), since: float = 0.0
    ) -> str | None:
        """The most recently written transcript in ``cwd``'s project, skipping ``exclude``
        ids and transcripts last written before ``since`` (epoch seconds)."""
        for path in self._newest_first(cwd):
            if _mtime(path) < since:
                break
            if path.stem not in exclude:
                return path.stem
        return None

    def _newest_first(self, cwd: str) -> list[Path]:
        folder = self.project_dir(cwd)
        if not folder.is_dir():
            return []
        files = [p for p in folder.glob("*.jsonl") if UUID_RE.match(p.stem)]
        return sorted(files, key=_mtime, reverse=True)


def project_dir_name(cwd: str) -> str:
    """Claude Code's folder name for ``cwd``: every UTF-16 code unit that is not
    ASCII alphanumeric becomes ``-``; past 200 characters the name is cut and a hash
    of the path is appended (its ``DO()``/``Use()`` functions)."""
    units = _utf16_units(cwd)
    name = "".join(chr(u) if chr(u).isascii() and chr(u).isalnum() else "-" for u in units)
    if len(name) <= _NAME_MAX:
        return name
    return f"{name[:_NAME_MAX]}-{_base36(abs(_java_hash(units)))}"


_NAME_MAX = 200


def _utf16_units(text: str) -> list[int]:
    data = text.encode("utf-16-le", "surrogatepass")
    return [int.from_bytes(data[i : i + 2], "little") for i in range(0, len(data), 2)]


def _java_hash(units: list[int]) -> int:
    """``h = (h << 5) - h + unit | 0`` over UTF-16 units, as a signed 32-bit int."""
    h = 0
    for unit in units:
        h = (h * 31 + unit) & 0xFFFFFFFF
    return h - (1 << 32) if h >= 1 << 31 else h


def _base36(n: int) -> str:
    digits = "0123456789abcdefghijklmnopqrstuvwxyz"
    out = ""
    while True:
        n, r = divmod(n, 36)
        out = digits[r] + out
        if n == 0:
            return out


def _mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def _last_string(path: Path, key: bytes) -> str | None:
    """Value of the last ``"key": "..."`` in a (possibly huge) JSONL transcript.

    Scans backwards through an mmap, so multi-hundred-MB transcripts cost little.
    Keys quoted inside other strings appear escaped (``\\"key\\"``) and never match.
    """
    try:
        with path.open("rb") as f, mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) as mm:
            end = len(mm)
            while (i := mm.rfind(key, 0, end)) >= 0:
                match = _STRING_VALUE.match(mm, i + len(key))
                if match:
                    try:
                        return str(json.loads(b'"' + match.group(1) + b'"'))
                    except ValueError:
                        return match.group(1).decode("utf-8", "replace")
                end = i
    except (OSError, ValueError):  # missing, unreadable, or empty (mmap rejects 0 bytes)
        return None
    return None
