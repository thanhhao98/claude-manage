"""Wire the process table, terminal and Claude state together (real or fake)."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass

from .claude_home import ClaudeHome
from .config import Config
from .discovery import Discovery, discover, version_from_path
from .fakes import FakeWorld
from .iterm import ITerm2
from .procs import ProcessTable, PsProcessTable
from .terminal import TabInfo, Terminal, TerminalError


@dataclass(frozen=True)
class System:
    procs: ProcessTable
    terminal: Terminal
    claude: ClaudeHome

    def discover(self, *, titles: bool = True, window: str | None = None) -> Discovery:
        """Running sessions. A terminal that can't be read leaves every session without a
        tab and sets ``layout_error``; commands that need tabs must check it."""
        layout: list[TabInfo] = []
        error = ""
        try:
            layout = self.terminal.layout()
        except TerminalError as e:
            error = str(e)
        found = discover(self.procs, layout, self.claude, titles=titles, window=window)
        found.layout_error = error
        return found


def load_system(cfg: Config) -> System:
    claude = ClaudeHome(cfg.sessions_dir, cfg.projects_dir)
    if cfg.fake_world is not None:
        world = FakeWorld(cfg.fake_world, cfg.sessions_dir)
        return System(world, world, claude)
    return System(PsProcessTable(cfg.pid_allowlist), ITerm2(), claude)


class ClaudeBinError(Exception):
    """The configured claude cannot be started from a new tab."""


def installed_version(cfg: Config) -> str:
    """The Claude Code version a new ``claude`` would start (what relaunch upgrades to)."""
    if cfg.installed_version:
        return cfg.installed_version
    path = shutil.which(cfg.claude_bin)
    if not path:
        return ""
    if version := version_from_path(os.path.realpath(path)):
        return version
    try:
        return _run_version(path)
    except ClaudeBinError:
        return ""


def check_claude_bin(cfg: Config) -> str:
    """Make sure ``claude_bin`` runs from a plain shell (no alias needed) before anything
    is stopped or opened. Returns its version."""
    path = shutil.which(cfg.claude_bin)
    if not path:
        raise ClaudeBinError(
            f"claude_bin {cfg.claude_bin!r} is not an executable on PATH (an alias or a "
            "shell function does not count): set claude_bin in ~/.claude-manage/config.toml "
            "or CLAUDE_MANAGE_CLAUDE_BIN to its full path"
        )
    return _run_version(path)


def _run_version(path: str) -> str:
    try:
        result = subprocess.run(
            [path, "--version"],
            capture_output=True,
            text=True,
            errors="replace",
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        raise ClaudeBinError(f"{path} --version failed: {e}") from e
    match = re.search(r"\d+(?:\.\d+)+", result.stdout)
    if result.returncode != 0 or not match:
        output = (result.stderr or result.stdout).strip()[:200]
        raise ClaudeBinError(f"{path} --version failed: {output or result.returncode}")
    return match.group(0)
