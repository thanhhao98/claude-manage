"""Restart running sessions in place, e.g. so they pick up a Claude Code upgrade.

For each selected session: SIGTERM the ``claude`` process (it shuts down cleanly and
its tab drops back to the shell), wait until that shell owns the tab again, type
``cd <cwd> && command claude --resume <id> <flags>`` into the same tab, and verify the
session is running again before moving on. The run stops at the first session that
does not come back, so one broken launch never takes down every tab.

When claude-manage runs inside one of the selected sessions (you asked Claude to run
it), the work moves to a detached background process so that session can be
restarted too: last, and only if every other restart worked.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, TextIO

from .commands import launch_command
from .discovery import Discovery, Found
from .model import BUSY_STATUSES, Group, Session, Snapshot, is_outdated
from .procs import Proc, foreground_leader, is_interactive_shell
from .render import (
    BOLD,
    CYAN,
    DIM,
    GREEN,
    RED,
    YELLOW,
    Block,
    Cell,
    Row,
    Style,
    fmt_time,
    join,
    state_icon,
    tree,
)
from .store import write_json
from .system import System

FINISHED = "relaunch finished"
WORKER = "worker pid"
_POLL = 0.25
_KILL_WAIT = 5.0
_SHELL_WAIT = 5.0

Emit = Callable[[str], None]


@dataclass
class Options:
    groups: list[str] = field(default_factory=list)
    sessions: list[str] = field(default_factory=list)  # id, id prefix (4+ chars) or title
    window: str | None = None
    outdated: bool = False  # only sessions older than the installed Claude Code
    include_busy: bool = False  # also interrupt busy sessions and unknown-status ones
    include_guessed: bool = False  # also restart sessions whose id is only a guess
    include_self: bool = True
    wait: float = 0.0  # seconds (in total) to wait for busy sessions to become idle
    kill: bool = False  # SIGKILL a session that ignores SIGTERM
    stop_timeout: float = 15.0
    verify_timeout: float = 45.0


@dataclass
class Step:
    group: str
    session: Session
    window_id: str = ""
    window_index: int = 0
    in_iterm: bool = True
    is_self: bool = False
    parent_pid: int = 0  # the interactive shell claude runs under: where commands are typed
    pgid: int = 0  # claude's process group, to tell when the shell owns the tty again
    action: str = "restart"  # restart | wait (restart once idle) | skip
    reason: str = ""
    outcome: str = ""  # restarted | started | failed | unverified | skipped
    detail: str = ""
    where: str = ""  # same tab | new tab | new window <id>
    stopped: bool = False  # its claude was stopped by this relaunch
    new_pid: int = 0
    new_version: str = ""

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> Step:
        return cls(**{**raw, "session": Session(**raw["session"])})

    @property
    def lost(self) -> bool:
        """Stopped by this relaunch and not running again."""
        return self.stopped and self.outcome in ("failed", "unverified")


class Emitter:
    """Progress lines to a stream, optionally also appended (timestamped) to a log file."""

    def __init__(self, stream: TextIO, log: Path | None = None, stamp: bool = False) -> None:
        self.stream, self.log, self.stamp = stream, log, stamp

    def __call__(self, message: str) -> None:
        stamped = f"[{time.strftime('%H:%M:%S')}] {message}"
        print(stamped if self.stamp else message, file=self.stream, flush=True)
        if self.log is not None:
            self.log.parent.mkdir(parents=True, exist_ok=True)
            with self.log.open("a") as f:
                f.write(stamped + "\n")


# planning ----------------------------------------------------------------------------


def select(
    snap: Snapshot, discovery: Discovery, installed: str, opts: Options
) -> tuple[list[Step], int]:
    """Plan over a fresh capture. Returns the steps in execution order and how many
    sessions the filters left out."""
    by_pid = {f.session.pid: f for f in discovery.found}
    steps: list[Step] = []
    left_out = 0
    for group, session in snap.sessions():
        if not _selected(group.name, group.window_id, session, installed, opts):
            left_out += 1
            continue
        item = by_pid.get(session.pid)
        step = Step(group.name, session, group.window_id, group.window_index)
        if item is not None:
            step.in_iterm, step.is_self, step.pgid = item.tab is not None, item.is_self, item.pgid
            step.parent_pid = item.parent.pid if item.parent else 0
        _classify(step, item, opts)
        steps.append(step)
    order = {"restart": 0, "wait": 1, "skip": 2}
    steps.sort(
        key=lambda st: (st.is_self, order[st.action], st.window_index or 1 << 20, st.session.tab)
    )
    return steps, left_out


def _selected(group: str, window_id: str, s: Session, installed: str, opts: Options) -> bool:
    if opts.groups and group not in opts.groups:
        return False
    if opts.window is not None and window_id != opts.window:
        return False
    if opts.sessions and not any(_matches(s, key) for key in opts.sessions):
        return False
    return not (opts.outdated and not is_outdated(s.version, installed))


def _matches(s: Session, key: str) -> bool:
    return key == s.title or (len(key) >= 4 and s.session_id.startswith(key))


def _restartable_parent(parent: Proc | None) -> bool:
    """claude must run under an interactive shell (or directly under ``login``, when the
    tab exec'd it): typing into anything else, e.g. a wrapper script, is not safe."""
    return parent is not None and (is_interactive_shell(parent) or parent.name == "login")


def _classify(step: Step, item: Found | None, opts: Options) -> None:
    s = step.session
    status = s.status or "status unknown"

    def skip(reason: str) -> None:
        step.action, step.reason = "skip", reason

    if not step.in_iterm:
        skip("not in an iTerm2 tab")
    elif s.guessed and not opts.include_guessed:
        # Restarting with a wrong id would swap this tab's conversation for another one.
        skip("id is a guess (use --include-guessed)")
    elif not _restartable_parent(item.parent if item else None):
        skip("not started from an interactive shell (wrapper script?)")
    elif step.is_self:
        if opts.include_self:
            step.reason = "this session: restarted last, if all others came back"
        else:
            skip("this session")
    elif not s.status or s.status in BUSY_STATUSES:
        if opts.include_busy:
            step.reason = f"{status}: interrupted (--include-busy)"
        elif s.status and opts.wait > 0:
            step.action, step.reason = "wait", f"{status}: restarted once idle"
        elif s.status:
            skip(f"{status} (use --wait or --include-busy)")
        else:
            skip("status unknown: older Claude Code may be busy (use --include-busy)")
    if step.action != "skip" and not s.resumable:
        step.reason = ", ".join(r for r in (step.reason, "no transcript: starts fresh") if r)


# execution ---------------------------------------------------------------------------


def execute(steps: list[Step], system: System, claude_bin: str, opts: Options, emit: Emit) -> None:
    """Run the plan. Stops after the first session that was stopped and did not come
    back; this session (if selected) only goes when everything else worked."""
    known = {f.session.pid for f in system.discover(titles=False).found}  # never "restarted"
    run = _Run(system, claude_bin, opts, emit, known)
    for step in steps:
        if step.action == "skip":
            step.outcome = "skipped"
    for step in (s for s in steps if s.action == "restart" and not s.is_self):
        run.step(step)
    run.waiting([s for s in steps if s.action == "wait"])
    for step in (s for s in steps if s.action == "restart" and s.is_self):
        run.step(step)


class _Run:
    def __init__(
        self, system: System, claude_bin: str, opts: Options, emit: Emit, known: set[int]
    ) -> None:
        self.system, self.claude_bin, self.opts, self.emit = system, claude_bin, opts, emit
        self.known = known  # pids that must not count as a restarted session
        self.halted_by = ""  # label of the session that stopped the run

    def step(self, step: Step) -> None:
        if self.halted_by:
            step.outcome = "skipped"
            step.detail = f"not attempted: {self.halted_by} did not come back"
            return
        try:
            self._restart(step)
        except Exception as e:  # anything unexpected: record it, keep the run accountable
            step.outcome, step.detail = "failed", f"error: {e}"
            self.emit(f"✗ {step.session.label}: {step.detail}")
        if step.lost:
            self.halted_by = step.session.label

    def waiting(self, steps: list[Step]) -> None:
        """Restart busy sessions as they become idle, all against one deadline."""
        deadline = time.monotonic() + self.opts.wait
        pending = list(steps)
        while pending and not self.halted_by:
            for step in list(pending):
                status = self._status(step.session.pid)
                if status is not None and status not in BUSY_STATUSES:
                    pending.remove(step)
                    self.step(step)
            if not pending or time.monotonic() >= deadline:
                break
            time.sleep(_POLL)
        for step in pending:
            if self.halted_by:
                self.step(step)  # records "not attempted"
            else:
                step.outcome = "skipped"
                step.detail = f"still {step.session.status} after {self.opts.wait:g}s"
                self.emit(f"– {step.session.label}: {step.detail}")

    def _status(self, pid: int) -> str | None:
        """The session's status now; None while its record is missing or unreadable
        (Claude Code rewrites it in place, so a read can catch it half-written)."""
        meta = self.system.claude.meta(pid)
        return meta.status if meta is not None else None

    def _skip(self, step: Step, detail: str) -> None:
        step.outcome, step.detail = "skipped", detail
        self.emit(f"– {step.session.label}: {detail}")

    def _restart(self, step: Step) -> None:
        s, procs = step.session, self.system.procs
        # Re-check right before signalling: the pid must still run this session's claude.
        if not procs.is_alive(s.pid):
            return self._skip(step, "exited before the relaunch reached it")
        argv = procs.argv(s.pid)
        if argv and os.path.basename(argv[0]).lstrip("-") != "claude":
            return self._skip(step, f"pid {s.pid} is no longer claude")
        meta = self.system.claude.meta(s.pid)
        if meta is None and not s.guessed:
            time.sleep(_POLL)  # a half-written record: read again once
            meta = self.system.claude.meta(s.pid)
            if meta is None:
                return self._skip(step, "its session record is unreadable")
        if meta is not None and meta.session_id != s.session_id:
            return self._skip(step, "now runs another conversation (/clear or /resume?)")
        status = meta.status if meta is not None else ""
        busy = not status or status in BUSY_STATUSES
        if busy and not step.is_self and not self.opts.include_busy:
            return self._skip(step, f"became {status or 'unknown'}: left running")

        self.emit(f"stopping {s.label} (pid {s.pid})")
        try:
            stopped = self._stop(s.pid)
        except PermissionError as e:
            step.outcome, step.detail = "failed", str(e)
            self.emit(f"✗ {s.label}: {e}")
            return None
        if not stopped:
            hint = "" if self.opts.kill else " (retry with --kill)"
            step.outcome = "failed"
            step.detail = f"still running {self.opts.stop_timeout:g}s after SIGTERM{hint}"
            self.emit(f"✗ {s.label}: {step.detail}")
            return None
        step.stopped = True

        command = launch_command(s, self.claude_bin)
        step.where = self._launch(step, command)
        self.emit(f"started {s.label} in {step.where}: {command}")
        self._verify(step)
        return None

    def _stop(self, pid: int) -> bool:
        procs = self.system.procs

        def gone() -> bool:
            return not procs.is_alive(pid)

        try:
            procs.signal(pid, signal.SIGTERM)
        except ProcessLookupError:
            return True
        if _wait_until(gone, self.opts.stop_timeout):
            return True
        if not self.opts.kill:
            return False
        try:
            procs.signal(pid, signal.SIGKILL)
        except ProcessLookupError:
            return True
        return _wait_until(gone, _KILL_WAIT)

    def _launch(self, step: Step, command: str) -> str:
        """Type into the same tab once claude's own shell leads the terminal again;
        otherwise (tab gone, something else in front) open a new tab or window."""
        terminal, tty = self.system.terminal, step.session.tty

        def shell_ready() -> bool:
            leader = foreground_leader(self.system.procs.processes(), tty)
            return (
                leader is not None
                and leader.pid == step.parent_pid
                and leader.pgid != step.pgid
                and is_interactive_shell(leader)
            )

        in_tab = tty in {t.tty for t in terminal.layout()}
        if in_tab and _wait_until(shell_ready, _SHELL_WAIT) and terminal.write_text(tty, command):
            return "same tab"
        if step.window_id and terminal.new_tab(step.window_id, command):
            return "new tab"
        return f"new window {terminal.new_window([command])}"

    def _verify(self, step: Step) -> None:
        s = step.session
        if not s.resumable and step.where != "same tab":
            step.outcome, step.detail = "started", f"fresh session in {step.where}"
            return
        deadline = time.monotonic() + self.opts.verify_timeout
        while True:
            for item in self.system.discover(titles=False).found:
                new = item.session
                if new.guessed or new.pid in self.known:
                    continue
                same_tab = new.tty == s.tty
                if s.resumable:
                    match = new.session_id == s.session_id and (
                        step.where != "same tab" or same_tab
                    )
                else:
                    match = same_tab
                if match:
                    self.known.add(new.pid)
                    step.outcome = "restarted" if s.resumable else "started"
                    step.new_pid, step.new_version = new.pid, new.version
                    self.emit(f"✓ {s.label}: pid {s.pid} → {new.pid}")
                    return
            if time.monotonic() >= deadline:
                step.outcome = "unverified"
                step.detail = (
                    f"started in {step.where}, not seen running after {self.opts.verify_timeout:g}s"
                )
                self.emit(f"! {s.label}: {step.detail}")
                return
            time.sleep(_POLL * 2)


def _wait_until(condition: Callable[[], bool], timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while not condition():
        if time.monotonic() >= deadline:
            return False
        time.sleep(_POLL)
    return True


def keep_lost(after: Snapshot, steps: list[Step]) -> list[Step]:
    """Put sessions this relaunch stopped but could not bring back into ``after`` (as
    not running), so `show` lists them and `resume` reopens them. Returns them."""
    present = {s.session_id for _, s in after.sessions()}
    lost = [st for st in steps if st.lost and st.session.session_id not in present]
    for step in lost:
        group = next((g for g in after.groups if g.window_id == step.window_id), None)
        group = group or after.group(step.group)
        if group is None:
            group = Group(step.group, step.window_id, step.window_index)
            after.groups.append(group)
        group.sessions.append(Session(**{**asdict(step.session), "pid": 0}))
        group.sessions.sort(key=lambda s: s.tab)
    return lost


# background worker -------------------------------------------------------------------


def save_run(path: Path, steps: list[Step], opts: Options, claude_bin: str) -> None:
    data = {"claude_bin": claude_bin, "options": asdict(opts), "steps": [asdict(s) for s in steps]}
    write_json(path, data)


def load_run(path: Path) -> tuple[list[Step], Options, str]:
    data = json.loads(path.read_text())
    steps = [Step.from_dict(raw) for raw in data["steps"]]
    return steps, Options(**data["options"]), str(data["claude_bin"])


def spawn_worker(run_file: Path, log_file: Path) -> int:
    """Run ``relaunch --worker`` detached (own session, no inherited stdio), so it
    survives the Claude session that started it being stopped."""
    log_file.parent.mkdir(parents=True, exist_ok=True)
    with log_file.open("ab") as log:
        proc = subprocess.Popen(
            [sys.executable, "-m", "claude_manage", "relaunch", "--worker", str(run_file)],
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            close_fds=True,
        )
    return proc.pid


# output ------------------------------------------------------------------------------

_OUTCOMES = ("restarted", "started", "failed", "unverified", "skipped")


def summary(steps: list[Step]) -> str:
    counts = [(n, k) for k in _OUTCOMES if (n := sum(s.outcome == k for s in steps))]
    return ", ".join(f"{n} {k}" for n, k in counts) or "nothing to do"


def ok(steps: list[Step]) -> bool:
    return not any(s.outcome in ("failed", "unverified") for s in steps)


def view(
    steps: list[Step],
    left_out: int,
    installed: str,
    style: Style,
    *,
    done: bool,
    stamp: str = "",
) -> str:
    """The plan (``done=False``) or the outcome of each step, grouped by window."""
    header: Cell = [("claude-manage relaunch", BOLD), (f" · {fmt_time(stamp)}", DIM)]
    if installed:
        header.append((f" · installed v{installed}", DIM))
    by_group: dict[str, list[Step]] = {}
    for step in sorted(steps, key=lambda st: (st.window_index or 1 << 20, st.session.tab)):
        by_group.setdefault(step.group, []).append(step)
    blocks: list[Block] = []
    for name, group_steps in by_group.items():
        first = group_steps[0]
        where = f"iTerm window {first.window_index}" if first.window_id else "not in iTerm2"
        block = Block(name, where)
        for step in group_steps:
            s = step.session
            version = step.new_version or s.version
            block.rows.append(Row(s, state_icon(s.status, True), _note(step, done), version))
        blocks.append(block)

    if done:
        parts: list[Cell] = [
            _count(steps, "restarted", "✓ {} restarted", GREEN),
            _count(steps, "started", "✓ {} started fresh", GREEN),
            _count(steps, "failed", "✗ {} failed", RED),
            _count(steps, "unverified", "! {} unverified", YELLOW),
            _count(steps, "skipped", "– {} skipped", YELLOW),
        ]
    else:
        restart = sum(s.action != "skip" for s in steps)
        parts = [
            [(f"→ {restart} to restart", CYAN)] if restart else [],
            [(f"– {len(steps) - restart} skipped", YELLOW)] if len(steps) - restart else [],
        ]
    if left_out:
        parts.append([(f"{left_out} not selected", DIM)])
    footer = join(parts) or [("no sessions selected", DIM)]
    return tree(header, blocks, footer, installed, style)


def _count(steps: list[Step], outcome: str, text: str, code: str) -> Cell:
    n = sum(s.outcome == outcome for s in steps)
    return [(text.format(n), code)] if n else []


def _note(step: Step, done: bool) -> Cell:
    s = step.session
    reason: Cell = [(f" ({step.reason})", DIM)] if step.reason else []
    dropped: Cell = [(f" (not replayed: {' '.join(s.dropped)})", DIM)] if s.dropped else []
    if not done:
        if step.action == "restart":
            return [("→ restart", CYAN), *reason, *dropped]
        if step.action == "wait":
            return [("→ restart when idle", CYAN), *reason, *dropped]
        return [(f"– skip: {step.reason}", YELLOW)]
    if step.outcome in ("restarted", "started"):
        note: Cell = [(f"✓ {step.outcome}", GREEN)]
        if step.new_pid:
            note.append((f" pid {s.pid} → {step.new_pid}", ""))
        if step.new_version and s.version and step.new_version != s.version:
            note.append((f" · v{s.version} → v{step.new_version}", DIM))
        if step.where and step.where != "same tab":
            note.append((f" · {step.where}", DIM))
        return note
    if step.outcome == "failed":
        return [(f"✗ {step.detail}", RED)]
    if step.outcome == "unverified":
        return [(f"! {step.detail}", YELLOW)]
    return [(f"– {step.detail or step.reason or 'skipped'}", YELLOW)]
