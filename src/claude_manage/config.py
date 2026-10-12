"""Settings and the on-disk layout under ``~/.claude-manage``.

Everything claude-manage writes lives under ``home`` (default ``~/.claude-manage``)::

    config.toml   optional settings (the keys of :func:`load`)
    captures/     one JSON snapshot per capture, plus latest.json
    logs/         relaunch logs
    runs/         relaunch plans handed to the background worker

Each setting resolves as: ``CLAUDE_MANAGE_<KEY>`` env var, then config.toml, then the
default. Claude Code's own state (``~/.claude``) is only ever read, never written.
"""

from __future__ import annotations

import os
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ENV_PREFIX = "CLAUDE_MANAGE_"


class ConfigError(Exception):
    """config.toml or an environment override is invalid."""


@dataclass(frozen=True)
class Config:
    home: Path
    sessions_dir: Path  # ~/.claude/sessions: one <pid>.json per running Claude Code
    projects_dir: Path  # ~/.claude/projects: transcripts, <project>/<session-id>.jsonl
    claude_bin: str  # what relaunch/resume type into the tab
    stop_timeout: float  # seconds to wait for claude to exit after SIGTERM
    verify_timeout: float  # seconds to wait for a relaunched session to come back
    keep_captures: int
    installed_version: str  # override for the installed Claude Code version
    pid_allowlist: frozenset[int] | None  # test safety net: only these pids may be signalled
    fake_world: Path | None  # test hook: fake process table + terminal (see fakes.py)

    @property
    def captures_dir(self) -> Path:
        return self.home / "captures"

    @property
    def logs_dir(self) -> Path:
        return self.home / "logs"

    @property
    def runs_dir(self) -> Path:
        return self.home / "runs"


def load(env: Mapping[str, str] | None = None) -> Config:
    env = os.environ if env is None else env
    home = _path(env.get(f"{ENV_PREFIX}HOME", "~/.claude-manage"))
    values = _read_toml(home / "config.toml")

    def get(key: str, default: Any) -> Any:
        return env.get(f"{ENV_PREFIX}{key.upper()}", values.get(key, default))

    try:
        claude_dir = _path(get("claude_dir", "~/.claude"))
        fake_world = get("fake_world", "")
        return Config(
            home=home,
            sessions_dir=_path(get("sessions_dir", claude_dir / "sessions")),
            projects_dir=_path(get("projects_dir", claude_dir / "projects")),
            claude_bin=os.path.expanduser(str(get("claude_bin", "claude"))),
            stop_timeout=float(get("stop_timeout", 15)),
            verify_timeout=float(get("verify_timeout", 45)),
            keep_captures=max(1, int(get("keep_captures", 50))),
            installed_version=str(get("installed_version", "")),
            pid_allowlist=_pids(get("pid_allowlist", None)),
            fake_world=_path(fake_world) if fake_world else None,
        )
    except (TypeError, ValueError) as e:
        raise ConfigError(f"invalid setting: {e}") from e


def _path(value: object) -> Path:
    return Path(str(value)).expanduser()


def _pids(value: object) -> frozenset[int] | None:
    if value is None or value == "":
        return None
    items = value.split(",") if isinstance(value, str) else value
    if not isinstance(items, list | tuple):
        raise ValueError(f"pid_allowlist must be a list of pids, got {value!r}")
    return frozenset(int(str(p).strip()) for p in items if str(p).strip())


def _read_toml(path: Path) -> dict[str, Any]:
    try:
        with path.open("rb") as f:
            return tomllib.load(f)
    except FileNotFoundError:
        return {}
    except (OSError, tomllib.TOMLDecodeError) as e:
        raise ConfigError(f"{path}: {e}") from e
