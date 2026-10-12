from __future__ import annotations

import re

from typer.testing import CliRunner

from claude_manage import __version__
from claude_manage.cli import app

runner = CliRunner()
ENV = {"NO_COLOR": "1", "TERM": "dumb", "COLUMNS": "160"}


def plain(text: str) -> str:
    """Typer forces rich styling in some environments (e.g. GITHUB_ACTIONS)."""
    return re.sub(r"\x1b\[[0-9;]*m", "", text)


def test_help_lists_commands() -> None:
    result = runner.invoke(app, ["--help"], env=ENV)
    assert result.exit_code == 0
    for command in ("capture", "show", "rename", "label", "resume", "relaunch", "log"):
        assert command in plain(result.output)


def test_version() -> None:
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert result.output.strip() == f"claude-manage {__version__}"


def test_relaunch_help_documents_safety_flags() -> None:
    result = runner.invoke(app, ["relaunch", "--help"], env=ENV)
    assert result.exit_code == 0
    out = plain(result.output)
    for flag in ("--dry-run", "--include-busy", "--include-guessed", "--wait", "--kill"):
        assert flag in out
    assert "--no-self" in out
    assert "--worker" not in out  # internal
