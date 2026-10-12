"""PsProcessTable against real dummy processes named ``claude``.

The dummies are spawned by these tests (detached, without a terminal, so
claude-manage's discovery ignores them) and are the only processes signalled.
"""

from __future__ import annotations

import contextlib
import os
import signal
import subprocess
import sys
import time
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from claude_manage.procs import (
    Proc,
    PsProcessTable,
    ancestors,
    foreground_leader,
    is_interactive_shell,
)

pytestmark = pytest.mark.skipif(sys.platform not in ("darwin", "linux"), reason="POSIX ps")

SLEEP = "import signal, time; {ignore} time.sleep(120)"
# The binary really running this interpreter: framework builds (python.org, Homebrew)
# re-exec sys.executable into Python.app, which would rename the dummy "Python".
REAL_PYTHON = PsProcessTable().exe(os.getpid()) or sys.executable


@pytest.fixture
def spawn(tmp_path: Path) -> Iterator[Callable[..., int]]:
    link = tmp_path / "claude"  # argv[0] basename "claude", runs the test interpreter
    link.symlink_to(REAL_PYTHON)
    pids: list[int] = []

    def _spawn(*args: str, ignore_term: bool = False) -> int:
        ignore = "signal.signal(signal.SIGTERM, signal.SIG_IGN);" if ignore_term else ""
        code = SLEEP.format(ignore=ignore)
        # A short-lived parent starts the dummy in its own session and exits, so the
        # dummy is reparented to init, which reaps it (no zombie that looks alive).
        launcher = (
            "import subprocess, sys; "
            "p = subprocess.Popen(sys.argv[1:], start_new_session=True, "
            "stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL); "
            "print(p.pid)"
        )
        out = subprocess.run(
            [sys.executable, "-c", launcher, str(link), "-c", code, *args],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        pid = int(out)
        pids.append(pid)
        _wait(lambda: PsProcessTable().argv(pid) is not None)
        return pid

    yield _spawn
    for pid in pids:
        with contextlib.suppress(ProcessLookupError):
            os.kill(pid, signal.SIGKILL)


def _wait(condition: Callable[[], bool], timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.05)
    return False


def test_lists_and_describes_a_process(spawn: Callable[..., int], tmp_path: Path) -> None:
    pid = spawn("--name", "my task")
    table = PsProcessTable()

    proc = next(p for p in table.processes() if p.pid == pid)
    assert proc.name == "claude"
    assert proc.tty == ""  # no controlling terminal: discovery skips it

    argv = table.argv(pid)
    assert argv is not None
    assert Path(argv[0]).name == "claude"
    assert argv[-2:] == ["--name", "my task"]  # exact boundaries, unlike `ps`
    assert os.path.realpath(table.cwd(pid)) == os.path.realpath(tmp_path)
    assert os.path.realpath(table.exe(pid)) == os.path.realpath(REAL_PYTHON)
    assert table.is_alive(pid)


def test_sigterm_stops_a_process(spawn: Callable[..., int]) -> None:
    pid = spawn()
    table = PsProcessTable()
    table.signal(pid, signal.SIGTERM)
    assert _wait(lambda: not table.is_alive(pid))


def test_a_process_ignoring_sigterm_needs_sigkill(spawn: Callable[..., int]) -> None:
    pid = spawn(ignore_term=True)
    table = PsProcessTable()
    time.sleep(0.3)  # let it install the handler
    table.signal(pid, signal.SIGTERM)
    assert not _wait(lambda: not table.is_alive(pid), timeout=0.5)
    table.signal(pid, signal.SIGKILL)
    assert _wait(lambda: not table.is_alive(pid))


def test_allowlist_refuses_other_pids(spawn: Callable[..., int]) -> None:
    pid = spawn()
    table = PsProcessTable(allow=frozenset({pid}))
    with pytest.raises(PermissionError, match="ALLOWLIST"):
        table.signal(os.getpid(), 0)  # signal 0 is harmless even if the guard failed
    table.signal(pid, 0)


def test_dead_process() -> None:
    table = PsProcessTable()
    assert not table.is_alive(2**22 + 12345)
    assert table.argv(2**22 + 12345) is None


def test_ancestors_and_foreground() -> None:
    procs = [
        Proc(1, 0, 1, 0, "", ("launchd",)),
        Proc(10, 1, 10, 20, "ttys001", ("-zsh",)),
        Proc(20, 10, 20, 20, "ttys001", ("claude", "--x")),
        Proc(30, 20, 30, 0, "", ("python3",)),
        Proc(40, 1, 40, 40, "ttys002", ("-zsh",)),
    ]
    assert ancestors(procs, 30) == {30, 20, 10}
    leader = foreground_leader(procs, "ttys001")
    assert leader is not None
    assert leader.pid == 20  # claude owns the terminal
    assert foreground_leader(procs, "ttys002") == procs[4]
    assert foreground_leader(procs, "ttys009") is None


@pytest.mark.parametrize(
    ("argv", "interactive"),
    [
        (("-zsh",), True),
        (("zsh",), True),
        (("/bin/bash", "-l"), True),
        (("fish", "--login"), True),
        (("bash", "keep-alive.sh"), False),
        (("zsh", "-c", "claude"), False),
        (("python3",), False),
    ],
)
def test_is_interactive_shell(argv: tuple[str, ...], interactive: bool) -> None:
    assert is_interactive_shell(Proc(1, 0, 1, 1, "ttys001", argv)) is interactive


def test_start_time(spawn: Callable[..., int]) -> None:
    pid = spawn()
    started = PsProcessTable().start_time(pid)
    assert started is not None
    assert abs(started - time.time()) < 120
