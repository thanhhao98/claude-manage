"""The shell command that brings a session back in a tab."""

from __future__ import annotations

import shlex

from .model import Session


def launch_command(session: Session, claude_bin: str) -> str:
    """``cd <cwd> && command claude --resume <id> <flags>``.

    ``--resume <id>`` comes first, so no replayed option can take it as its value.
    ``command`` skips shell aliases (``alias claude='claude --flag'`` would otherwise add
    flags the saved argv already has). A session without a transcript cannot be resumed,
    so it gets a fresh ``claude`` in the same folder instead.
    """
    resume = ["--resume", session.session_id] if session.resumable else []
    command = shlex.join(["command", claude_bin, *resume, *session.flags])
    return f"cd {shlex.quote(session.cwd)} && {command}" if session.cwd else command
