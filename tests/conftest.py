"""Fixtures. See tests/support.py for how the fake world isolates every test."""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from pathlib import Path

import pytest

from tests.support import INSTALLED, SRC, World


@pytest.fixture
def world(tmp_path: Path) -> Iterator[World]:
    claude_dir = tmp_path / "claude"
    sessions, projects = claude_dir / "sessions", claude_dir / "projects"
    sessions.mkdir(parents=True)
    projects.mkdir(parents=True)
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    file = tmp_path / "world.json"
    file.write_text(
        json.dumps(
            {
                "procs": [],
                "tabs": [],
                "self": None,
                "titles": {},
                "next_pid": 910_000,
                "next_tty": 700,
                "next_window": 9000,
                "launch_version": INSTALLED,
                "actions": [],
            }
        )
    )
    stub_bin = tmp_path / "bin"
    stub_bin.mkdir()
    marker = tmp_path / "osascript-was-called"
    osascript = stub_bin / "osascript"
    osascript.write_text(f"#!/bin/sh\ntouch '{marker}'\nexit 1\n")
    osascript.chmod(0o755)
    # What `claude --version` preflight checks find; the fake world never runs it otherwise.
    claude = stub_bin / "claude"
    claude.write_text(f'#!/bin/sh\necho "{INSTALLED} (Claude Code)"\n')
    claude.chmod(0o755)

    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith("CLAUDE_MANAGE_")
        and k not in ("FORCE_COLOR", "PYTHONPATH", "GITHUB_ACTIONS")
    }
    env.update(
        HOME=str(fake_home),
        PYTHONPATH=str(SRC),
        PATH=f"{stub_bin}{os.pathsep}{os.environ.get('PATH', '')}",
        NO_COLOR="1",
        COLUMNS="200",
        TERM="dumb",
        CLAUDE_MANAGE_HOME=str(tmp_path / "cm"),
        CLAUDE_MANAGE_CLAUDE_DIR=str(claude_dir),
        CLAUDE_MANAGE_FAKE_WORLD=str(file),
        CLAUDE_MANAGE_INSTALLED_VERSION=INSTALLED,
        CLAUDE_MANAGE_STOP_TIMEOUT="1",
        CLAUDE_MANAGE_VERIFY_TIMEOUT="3",
    )
    yield World(tmp_path, env, file, sessions, projects, tmp_path / "cm")
    assert not marker.exists(), "a test reached osascript, i.e. the real iTerm2"
