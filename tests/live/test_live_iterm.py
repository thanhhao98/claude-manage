"""Opt-in (``CM_LIVE=1``, macOS): drive a real, throwaway iTerm2 window.

Two fake ``claude`` processes (a Python dummy whose argv[0] is ``.../claude``) run in
a new window. capture/show/relaunch/resume are scoped to that window, and
``CLAUDE_MANAGE_PID_ALLOWLIST`` limits signals to the dummies, so real sessions stay
safe even if a filter were wrong. Every window opened here is closed at the end.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import signal
import subprocess
import sys
import time
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from claude_manage.procs import PsProcessTable

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        os.environ.get("CM_LIVE") != "1" or sys.platform != "darwin",
        reason="drives iTerm2: set CM_LIVE=1 on macOS",
    ),
]

SRC = Path(__file__).resolve().parents[2] / "src"
FLAG = "--dangerously-skip-permissions"
SID_A = "1a1a1a1a-0000-4000-8000-00000000000a"
SID_B = "2b2b2b2b-0000-4000-8000-00000000000b"

DUMMY = r"""
import json, os, signal, sys, time, uuid
sessions, args = sys.argv[1], sys.argv[2:]
if "--version" in args:  # claude-manage's preflight check
    print("9.9.9 (Claude Code)")
    sys.exit(0)
sid = args[args.index("--resume") + 1] if "--resume" in args else str(uuid.uuid4())
names = json.load(open(os.path.join(sessions, "names.json")))
path = os.path.join(sessions, f"{os.getpid()}.json")
record = {"pid": os.getpid(), "sessionId": sid, "cwd": os.getcwd(), "status": "idle",
          "kind": "interactive", "version": "9.9.9", "name": names.get(sid, ""),
          "nameSource": "user"}
json.dump(record, open(path, "w"))
def bye(*_):
    try:
        os.unlink(path)
    finally:
        sys.exit(0)
signal.signal(signal.SIGTERM, bye)
signal.signal(signal.SIGHUP, bye)
while True:
    time.sleep(1)
"""


def osascript(script: str, *args: str) -> str:
    if not script.startswith("on run"):  # a handler can't sit inside a timeout block
        script = f"with timeout of 20 seconds\n{script}\nend timeout"
    return subprocess.run(
        ["osascript", "-e", script, *args], capture_output=True, text=True, check=True
    ).stdout.strip()


def wait(condition: Callable[[], object], timeout: float = 20.0) -> object:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if result := condition():
            return result
        time.sleep(0.2)
    raise AssertionError(f"timed out after {timeout}s")


class Lab:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.claude = root / "realbin" / "claude"  # argv[0] basename "claude"
        self.claude.parent.mkdir()
        self.claude.symlink_to(PsProcessTable().exe(os.getpid()) or sys.executable)
        self.dummy = root / "dummy_claude.py"
        self.dummy.write_text(DUMMY)
        self.sessions = root / "sessions"
        self.sessions.mkdir()
        (self.sessions / "names.json").write_text(json.dumps({SID_A: "live-a", SID_B: "live-b"}))
        self.projects = root / "projects"
        project = self.projects / re.sub(r"[^A-Za-z0-9]", "-", str(root))
        project.mkdir(parents=True)
        for sid in (SID_A, SID_B):  # transcripts make the sessions resumable
            (project / f"{sid}.jsonl").write_text('{"type": "user"}\n')
        self.stub = root / "claude-stub"
        self.stub.write_text(
            f'#!/bin/sh\nexec "{self.claude}" "{self.dummy}" "{self.sessions}" "$@"\n'
        )
        self.stub.chmod(0o755)
        self.windows: list[str] = []

    def dummies(self) -> dict[str, int]:
        """tty -> pid of every running dummy."""
        table = PsProcessTable()
        return {
            p.tty: p.pid
            for p in table.processes()
            if p.argv and p.argv[0] == str(self.claude) and p.tty
        }

    def open_window(self, *commands: str) -> str:
        window = osascript(
            'tell application "iTerm2"\n'
            "set w to (create window with default profile)\n"
            "return (id of w) as text\nend tell"
        )
        self.windows.append(window)
        for i, command in enumerate(commands):
            if i:
                osascript(
                    f'tell application "iTerm2" to tell window id {window} '
                    "to create tab with default profile"
                )
            osascript(
                "on run argv\n"
                f'tell application "iTerm2" to tell current session of window id {window} '
                "to write text (item 1 of argv)\nend run",
                command,
            )
        return window

    def launch(self, sid: str) -> str:
        return f"cd {self.root} && {self.stub} {FLAG} --resume {sid}"

    def cli(self, *args: str, allow: dict[str, int]) -> subprocess.CompletedProcess[str]:
        env = {k: v for k, v in os.environ.items() if not k.startswith("CLAUDE_MANAGE_")}
        env.update(
            PYTHONPATH=str(SRC),
            NO_COLOR="1",
            COLUMNS="200",
            CLAUDE_MANAGE_HOME=str(self.root / "cm"),
            CLAUDE_MANAGE_SESSIONS_DIR=str(self.sessions),
            CLAUDE_MANAGE_PROJECTS_DIR=str(self.projects),
            CLAUDE_MANAGE_CLAUDE_BIN=str(self.stub),
            CLAUDE_MANAGE_INSTALLED_VERSION="9.9.9",
            CLAUDE_MANAGE_PID_ALLOWLIST=",".join(str(p) for p in allow.values()) or "0",
        )
        return subprocess.run(
            [sys.executable, "-m", "claude_manage", *args],
            env=env,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )

    def stop_dummies(self) -> None:
        """Our dummies only: iTerm2 asks before closing a window with running jobs."""
        for pid in self.dummies().values():
            with contextlib.suppress(ProcessLookupError):
                os.kill(pid, signal.SIGTERM)
        wait(lambda: not self.dummies())

    def close(self) -> None:
        with contextlib.suppress(AssertionError):
            self.stop_dummies()
        for window in self.windows:
            with contextlib.suppress(subprocess.CalledProcessError):
                osascript(f'tell application "iTerm2" to close window id {window}')
        for pid in self.dummies().values():
            with contextlib.suppress(ProcessLookupError):
                os.kill(pid, signal.SIGKILL)


@pytest.fixture
def lab(tmp_path: Path) -> Iterator[Lab]:
    lab = Lab(Path(os.path.realpath(tmp_path)))
    try:
        yield lab
    finally:
        lab.close()


def test_capture_relaunch_and_resume_a_real_window(lab: Lab) -> None:
    window = lab.open_window(lab.launch(SID_A), lab.launch(SID_B))
    before: dict[str, int] = wait(lambda: len(d := lab.dummies()) == 2 and d)  # type: ignore[assignment]

    out = lab.cli("capture", "--window", window, "--name", f"{window}=live", allow={})
    assert out.returncode == 0, out.stderr
    assert re.search(r"1\s+●\s+live-a\s+1a1a1a1a", out.stdout)
    assert re.search(r"2\s+●\s+live-b\s+2b2b2b2b", out.stdout)
    assert "✓ 2 running" in out.stdout

    # relaunch: same tabs (ttys), new processes, same sessions
    out = lab.cli("relaunch", "--window", window, allow=before)
    assert out.returncode == 0, out.stdout + out.stderr
    assert "2 restarted" in out.stdout
    after: dict[str, int] = wait(  # type: ignore[assignment]
        lambda: (
            (d := lab.dummies()).keys() == before.keys()
            and set(d.values()).isdisjoint(before.values())
            and d
        )
    )
    assert set(after) == set(before)
    shown = lab.cli("show", allow={}).stdout
    assert re.search(r"live-a\s+.*✓ pid", shown)
    assert re.search(r"live-b\s+.*✓ pid", shown)

    # the group is visible on every tab of the window
    badges = [
        osascript(
            f'tell application "iTerm2" to tell session 1 of tab {i} of window id {window} '
            'to return variable named "badge"'
        )
        for i in (1, 2)
    ]
    assert badges == ["live", "live"]

    # "reboot": the window and its sessions are gone; resume brings them back
    lab.stop_dummies()
    osascript(f'tell application "iTerm2" to close window id {window}')
    out = lab.cli("resume", "--group", "live", allow={})
    assert out.returncode == 0, out.stderr
    match = re.search(r"opened in new window (\d+)", out.stdout)
    assert match, out.stdout
    lab.windows.append(match.group(1))
    wait(lambda: len(lab.dummies()) == 2)
    shown = lab.cli("show", allow={}).stdout
    assert re.search(r"live-a\s+.*✓ pid", shown)
    assert re.search(r"live-b\s+.*✓ pid", shown)
