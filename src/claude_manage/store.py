"""Capture files under ``~/.claude-manage/captures``."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from .model import Snapshot


class CaptureError(Exception):
    """A capture file is missing or unreadable."""


def write_json(path: Path, data: Any) -> None:
    """Write JSON atomically, so a crash never leaves a half-written file behind."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.write("\n")
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


class Store:
    def __init__(self, captures_dir: Path, keep: int = 50) -> None:
        self.dir = captures_dir
        self.keep = max(1, keep)

    @property
    def latest(self) -> Path:
        return self.dir / "latest.json"

    def save(self, snap: Snapshot) -> Path:
        """Write a new timestamped capture and point latest.json at it."""
        stamp = snap.captured_at.replace(":", "-") or "capture"
        path, n = self.dir / f"{stamp}.json", 1
        while path.exists():
            n += 1
            path = self.dir / f"{stamp}_{n}.json"
        data = snap.to_dict()
        write_json(path, data)
        write_json(self.latest, data)
        self._prune()
        return path

    def write_latest(self, snap: Snapshot) -> None:
        write_json(self.latest, snap.to_dict())

    def load(self, path: Path | None = None) -> Snapshot | None:
        """The given capture, or latest.json (None when nothing was captured yet)."""
        target = path or self.latest
        try:
            data = json.loads(target.read_text())
        except FileNotFoundError:
            if path is None:
                return None
            raise CaptureError(f"{target}: no such capture") from None
        except (OSError, ValueError) as e:
            raise CaptureError(f"{target}: {e}") from e
        try:
            return Snapshot.from_dict(data)
        except (AttributeError, KeyError, TypeError, ValueError) as e:
            raise CaptureError(f"{target}: invalid capture ({e})") from e

    def history(self) -> list[Path]:
        """Capture files, oldest first."""
        if not self.dir.is_dir():
            return []
        return sorted(p for p in self.dir.glob("*.json") if p.name != self.latest.name)

    def _prune(self) -> None:
        old = self.history()
        for path in old[: max(0, len(old) - self.keep)]:
            path.unlink(missing_ok=True)
