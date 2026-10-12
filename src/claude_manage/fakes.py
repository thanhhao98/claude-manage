"""A file-backed fake process table and terminal, enabled by ``CLAUDE_MANAGE_FAKE_WORLD``.

The end-to-end tests run the real CLI in a subprocess against this fake, so no
real process is signalled and no iTerm2 window is touched. Typing a ``claude``
command into a fake tab starts a fake Claude: a process entry plus the session
record a real Claude Code would write into the sessions dir.

World file::

    {"procs": [{"pid", "tty", "argv", "cwd", "exe"?, "start"?, "ppid"?,
                "ignore_term"?}],
     "tabs": [{"window_id", "window_index", "tab_index", "tty", "shell_pid",
               "shell_argv"?,       # default ["-zsh"]
               "closes_on_exit"?,   # the tab closes when its claude exits
               "inert"?,            # typed text starts nothing (a dead shell)
               "label"?}],          # the group label shown on the tab
     "self": {"pid", "ppid"} | null,      # where claude-manage itself "runs"
     "titles": {session_id: title},      # names fake launches report
     "layout_error": str?,               # layout() fails (e.g. Automation denied)
     "new_tab_budget": int?,             # new_tab succeeds this many more times
     "new_window_error": str?,           # new_window() fails
     "new_tabs_inert": bool?,            # tabs opened from now on start nothing
     "next_pid": int, "next_tty": int, "next_window": int,
     "launch_version": str,              # version fake launches report
     "actions": [...]}                   # everything done to the world, in order
"""

from __future__ import annotations

import fcntl
import json
import os
import shlex
import signal
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from .procs import Proc
from .store import write_json
from .terminal import TabInfo, TerminalError

World = dict[str, Any]


class FakeWorld:
    def __init__(self, path: Path, sessions_dir: Path) -> None:
        self.path = path
        self.sessions_dir = sessions_dir

    # ProcessTable ------------------------------------------------------------------

    def processes(self) -> list[Proc]:
        world = self._read()
        claude_on = {p["tty"]: p for p in world["procs"]}
        procs: list[Proc] = []
        shells: dict[str, int] = {}
        for tab in world["tabs"]:
            shell = int(tab["shell_pid"])
            shells[tab["tty"]] = shell
            running = claude_on.get(tab["tty"])
            foreground = running.get("pgid", running["pid"]) if running else shell
            argv = tuple(tab.get("shell_argv") or ["-zsh"])
            procs.append(Proc(shell, 1, shell, foreground, tab["tty"], argv))
        for p in world["procs"]:
            parent = p.get("ppid") or shells.get(p["tty"], 1)
            pgid = p.get("pgid", p["pid"])
            procs.append(Proc(p["pid"], parent, pgid, pgid, p["tty"], tuple(p["argv"])))
        if me := world.get("self"):
            argv = ("python3", "-m", "claude_manage")
            procs.append(Proc(int(me["pid"]), int(me["ppid"]), int(me["pid"]), 0, "", argv))
        return procs

    def argv(self, pid: int) -> list[str] | None:
        proc = self._proc(pid)
        return list(proc["argv"]) if proc else None

    def exe(self, pid: int) -> str:
        proc = self._proc(pid)
        return str(proc.get("exe", "")) if proc else ""

    def cwd(self, pid: int) -> str:
        proc = self._proc(pid)
        return str(proc.get("cwd", "")) if proc else ""

    def start_time(self, pid: int) -> float | None:
        proc = self._proc(pid)
        return float(proc["start"]) if proc and "start" in proc else None

    def is_alive(self, pid: int) -> bool:
        return self._proc(pid) is not None

    def signal(self, pid: int, sig: int) -> None:
        with self._edit() as world:
            proc = next((p for p in world["procs"] if p["pid"] == pid), None)
            if proc is None:
                raise ProcessLookupError(pid)
            world["actions"].append({"op": "signal", "pid": pid, "sig": signal.Signals(sig).name})
            if sig == signal.SIGKILL or (sig == signal.SIGTERM and not proc.get("ignore_term")):
                self._exit(world, proc)

    def self_pid(self) -> int:
        me = self._read().get("self")
        return int(me["pid"]) if me else os.getpid()

    # Terminal ----------------------------------------------------------------------

    def layout(self) -> list[TabInfo]:
        world = self._read()
        if error := world.get("layout_error"):
            raise TerminalError(error)
        return [
            TabInfo(str(t["window_id"]), t["window_index"], t["tab_index"], t["tty"])
            for t in world["tabs"]
        ]

    def write_text(self, tty: str, text: str) -> bool:
        with self._edit() as world:
            world["actions"].append({"op": "write_text", "tty": tty, "text": text})
            if not any(t["tty"] == tty for t in world["tabs"]):
                return False
            self._run(world, tty, text)
            return True

    def new_tab(self, window_id: str, text: str) -> bool:
        with self._edit() as world:
            world["actions"].append({"op": "new_tab", "window_id": window_id, "text": text})
            tabs = [t for t in world["tabs"] if str(t["window_id"]) == window_id]
            budget = world.get("new_tab_budget")
            if not tabs or budget == 0:
                return False
            if budget is not None:
                world["new_tab_budget"] = budget - 1
            tab = self._add_tab(world, window_id, tabs[0]["window_index"])
            self._run(world, tab["tty"], text)
            return True

    def new_window(self, texts: list[str]) -> str:
        with self._edit() as world:
            if error := world.get("new_window_error"):
                world["actions"].append({"op": "new_window_failed", "texts": texts})
                raise TerminalError(error)
            window_id = str(world.setdefault("next_window", 9000))
            world["next_window"] += 1
            index = 1 + max((t["window_index"] for t in world["tabs"]), default=0)
            world["actions"].append({"op": "new_window", "window_id": window_id, "texts": texts})
            for text in texts:
                tab = self._add_tab(world, window_id, index)
                self._run(world, tab["tty"], text)
            return window_id

    def label(self, tty: str, text: str, color: tuple[int, int, int] | None) -> bool:
        with self._edit() as world:
            tab = next((t for t in world["tabs"] if t["tty"] == tty), None)
            if tab is None:
                return False
            tab["label"] = {"text": text, "color": list(color) if color else None}
            world["actions"].append({"op": "label", "tty": tty, "text": text})
            return True

    # internals -------------------------------------------------------------------------

    def _read(self) -> World:
        data: World = json.loads(self.path.read_text())
        return data

    @contextmanager
    def _edit(self) -> Iterator[World]:
        with self.path.with_suffix(".lock").open("w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            world = self._read()
            yield world
            write_json(self.path, world)

    def _proc(self, pid: int) -> dict[str, Any] | None:
        return next((p for p in self._read()["procs"] if p["pid"] == pid), None)

    def _exit(self, world: World, proc: dict[str, Any]) -> None:
        world["procs"].remove(proc)
        (self.sessions_dir / f"{proc['pid']}.json").unlink(missing_ok=True)
        tab = next((t for t in world["tabs"] if t["tty"] == proc["tty"]), None)
        if tab and tab.get("closes_on_exit"):
            world["tabs"].remove(tab)

    def _add_tab(self, world: World, window_id: str, window_index: int) -> dict[str, Any]:
        n = world.setdefault("next_tty", 700)
        world["next_tty"] += 1
        tab_index = 1 + max(
            (t["tab_index"] for t in world["tabs"] if str(t["window_id"]) == window_id), default=0
        )
        tab = {
            "window_id": window_id,
            "window_index": window_index,
            "tab_index": tab_index,
            "tty": f"ttys{n}",
            "shell_pid": 600000 + n,
            "inert": bool(world.get("new_tabs_inert")),
        }
        world["tabs"].append(tab)
        return tab

    def _run(self, world: World, tty: str, text: str) -> None:
        """What the tab's shell would do: ``cd DIR && [command] claude ARGS`` starts Claude."""
        tokens = shlex.split(text)
        cwd = ""
        if len(tokens) >= 3 and tokens[0] == "cd" and tokens[2] == "&&":
            cwd, tokens = tokens[1], tokens[3:]
        if tokens[:1] == ["command"]:
            tokens = tokens[1:]
        tab: dict[str, Any] = next((t for t in world["tabs"] if t["tty"] == tty), {})
        if not tokens or tab.get("inert") or any(p["tty"] == tty for p in world["procs"]):
            return  # nothing to start, a dead shell, or the text went to a running program
        args = tokens[1:]
        # The real claude only resumes reliably when nothing can take --resume as a value;
        # claude-manage puts it first. Anywhere else, behave as if it were swallowed.
        resumed = args[1] if args[:1] == ["--resume"] and len(args) > 1 else None
        session_id = resumed or str(uuid.uuid4())
        pid = world["next_pid"]
        world["next_pid"] += 1
        world["procs"].append(
            {"pid": pid, "tty": tty, "argv": ["claude", *args], "cwd": cwd, "start": time.time()}
        )
        record: dict[str, Any] = {
            "pid": pid,
            "sessionId": session_id,
            "cwd": cwd,
            "status": "idle",
            "kind": "interactive",
            "version": world.get("launch_version", "0.0.0"),
        }
        if title := world.get("titles", {}).get(session_id):
            record.update(name=title, nameSource="user")
        write_json(self.sessions_dir / f"{pid}.json", record)
