"""Process access for the real system: ps, exact argv, cwd, liveness and signals."""

from __future__ import annotations

import ctypes
import ctypes.util
import functools
import os
import subprocess
import sys
import time
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

SHELLS = frozenset({"zsh", "bash", "fish", "sh", "dash", "ksh", "tcsh", "csh", "nu", "xonsh"})
_NO_TTY = frozenset({"", "?", "??", "-"})


def norm_tty(tty: str) -> str:
    """``/dev/ttys012`` and ``ttys012`` become ``ttys012``; no controlling tty becomes ``""``."""
    tty = tty.strip().removeprefix("/dev/")
    return "" if tty in _NO_TTY else tty


@dataclass(frozen=True)
class Proc:
    pid: int
    ppid: int
    pgid: int
    tpgid: int  # foreground process group of the controlling tty
    tty: str  # "ttys012"; "" without a controlling terminal
    argv: tuple[str, ...]  # from ps, split on spaces; ProcessTable.argv() has the exact args

    @property
    def name(self) -> str:
        return os.path.basename(self.argv[0]).lstrip("-") if self.argv else ""


class ProcessTable(Protocol):
    def processes(self) -> list[Proc]: ...
    def argv(self, pid: int) -> list[str] | None: ...
    def exe(self, pid: int) -> str: ...
    def cwd(self, pid: int) -> str: ...
    def start_time(self, pid: int) -> float | None: ...
    def is_alive(self, pid: int) -> bool: ...
    def signal(self, pid: int, sig: int) -> None: ...
    def self_pid(self) -> int: ...


def ancestors(procs: Iterable[Proc], pid: int) -> set[int]:
    """``pid`` and every process above it."""
    parent = {p.pid: p.ppid for p in procs}
    chain: set[int] = set()
    while pid > 1 and pid not in chain:
        chain.add(pid)
        pid = parent.get(pid, 0)
    return chain


def is_interactive_shell(proc: Proc) -> bool:
    """A shell waiting for typed commands: no script, no ``-c`` (``-zsh``, ``bash -l``)."""
    return proc.name in SHELLS and all(a in _SHELL_FLAGS for a in proc.argv[1:])


_SHELL_FLAGS = frozenset({"-l", "-i", "-il", "-li", "--login", "--interactive"})


def foreground_leader(procs: Iterable[Proc], tty: str) -> Proc | None:
    """The process leading ``tty``'s foreground process group."""
    on_tty = [p for p in procs if p.tty == tty]
    if not on_tty:
        return None
    return next((p for p in on_tty if p.pid == on_tty[0].tpgid), None)


class PsProcessTable:
    """The live system. With ``allow`` set, only those pids may be signalled."""

    def __init__(self, allow: frozenset[int] | None = None) -> None:
        self._allow = allow

    def processes(self) -> list[Proc]:
        try:
            out = subprocess.run(
                ["ps", "-A", "-o", "pid=,ppid=,pgid=,tpgid=,tty=,args="],
                capture_output=True,
                text=True,
                errors="replace",  # any process may have non-UTF-8 arguments
                check=False,
            ).stdout
        except OSError:
            return []
        procs: list[Proc] = []
        for line in out.splitlines():
            parts = line.split(None, 5)
            if len(parts) < 6:
                continue
            try:
                pid, ppid, pgid, tpgid = (int(p) for p in parts[:4])
            except ValueError:
                continue
            procs.append(Proc(pid, ppid, pgid, tpgid, norm_tty(parts[4]), tuple(parts[5].split())))
        return procs

    def argv(self, pid: int) -> list[str] | None:
        if sys.platform == "linux":
            try:
                raw = Path(f"/proc/{pid}/cmdline").read_bytes()
            except OSError:
                return None
            return [a.decode(errors="replace") for a in raw.rstrip(b"\0").split(b"\0")] or None
        if sys.platform == "darwin":
            info = _darwin_procargs(pid)
            return info[1] if info else None
        return None

    def exe(self, pid: int) -> str:
        if sys.platform == "linux":
            try:
                return os.readlink(f"/proc/{pid}/exe")
            except OSError:
                return ""
        if sys.platform == "darwin":
            return _darwin_pidpath(pid)
        return ""

    def cwd(self, pid: int) -> str:
        if sys.platform == "linux":
            try:
                return os.readlink(f"/proc/{pid}/cwd")
            except OSError:
                return ""
        try:
            out = subprocess.run(
                ["lsof", "-a", "-p", str(pid), "-d", "cwd", "-Fn"],
                capture_output=True,
                text=True,
                errors="replace",
                check=False,
            ).stdout
        except OSError:
            return ""
        return next((line[1:] for line in out.splitlines() if line.startswith("n")), "")

    def start_time(self, pid: int) -> float | None:
        try:
            out = subprocess.run(
                ["ps", "-o", "lstart=", "-p", str(pid)],
                capture_output=True,
                text=True,
                errors="replace",
                check=False,
                env={**os.environ, "LC_ALL": "C"},
            ).stdout.strip()
            return time.mktime(time.strptime(" ".join(out.split()), "%a %b %d %H:%M:%S %Y"))
        except (OSError, ValueError, OverflowError):
            return None

    def is_alive(self, pid: int) -> bool:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True

    def signal(self, pid: int, sig: int) -> None:
        if self._allow is not None and pid not in self._allow:
            raise PermissionError(f"pid {pid} is not in CLAUDE_MANAGE_PID_ALLOWLIST")
        os.kill(pid, sig)

    def self_pid(self) -> int:
        return os.getpid()


@functools.cache
def _libc() -> Any:
    return ctypes.CDLL(ctypes.util.find_library("c"), use_errno=True)


def _darwin_pidpath(pid: int) -> str:
    """The executable the process actually runs (symlinks resolved at exec time).

    The exec path in KERN_PROCARGS2 is the path as typed (``~/.local/bin/claude``),
    which an upgrade has since re-pointed at the new version.
    """
    try:
        libproc = ctypes.CDLL("/usr/lib/libproc.dylib")
    except OSError:
        return ""
    buf = ctypes.create_string_buffer(4096)  # PROC_PIDPATHINFO_MAXSIZE
    if libproc.proc_pidpath(pid, buf, ctypes.c_uint32(len(buf))) <= 0:
        return ""
    return buf.value.decode(errors="replace")


def _darwin_procargs(pid: int) -> tuple[str, list[str]] | None:
    """(executable path, exact argv) from sysctl KERN_PROCARGS2.

    ``ps`` joins arguments with spaces, which loses boundaries ("--name my task").
    """
    ctl_kern, kern_argmax, kern_procargs2 = 1, 8, 49
    libc = _libc()
    argmax = ctypes.c_int(0)
    size = ctypes.c_size_t(ctypes.sizeof(argmax))
    mib = (ctypes.c_int * 2)(ctl_kern, kern_argmax)
    if libc.sysctl(mib, 2, ctypes.byref(argmax), ctypes.byref(size), None, 0) != 0:
        return None
    buf = ctypes.create_string_buffer(argmax.value)
    size = ctypes.c_size_t(argmax.value)
    mib3 = (ctypes.c_int * 3)(ctl_kern, kern_procargs2, pid)
    if libc.sysctl(mib3, 3, buf, ctypes.byref(size), None, 0) != 0:
        return None
    raw = buf.raw[: size.value]
    # Layout: int argc | exec path NUL | NUL padding | argv[0] NUL ... argv[argc-1] NUL | env...
    if len(raw) < 4:
        return None
    argc = int.from_bytes(raw[:4], sys.byteorder)
    rest = raw[4:]
    end = rest.find(b"\0")
    if end < 0:
        return None
    exe = rest[:end].decode(errors="replace")
    args = rest[end:].lstrip(b"\0").split(b"\0")[:argc]
    return exe, [a.decode(errors="replace") for a in args]
