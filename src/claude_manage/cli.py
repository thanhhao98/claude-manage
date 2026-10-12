"""The ``claude-manage`` command line (Typer)."""

from __future__ import annotations

import json
import os
import sys
import time
from dataclasses import asdict
from pathlib import Path
from typing import Annotated, NoReturn

import typer

from . import __version__, labels, relaunch, resume
from .capture import build_snapshot, merge_window, now_stamp
from .config import Config, ConfigError
from .config import load as load_config
from .discovery import Discovery
from .live import Live, check
from .model import Session, Snapshot
from .render import Style
from .store import CaptureError, Store
from .system import ClaudeBinError, System, check_claude_bin, installed_version, load_system
from .terminal import TerminalError
from .views import snapshot_view

app = typer.Typer(
    name="claude-manage",
    help="Capture, show, resume and relaunch Claude Code sessions, grouped by iTerm2 window.",
    no_args_is_help=False,
    pretty_exceptions_enable=False,  # rich tracebacks would print locals (env, paths)
)

PlainOpt = Annotated[bool, typer.Option("--plain", help="No colors.")]
JsonOpt = Annotated[bool, typer.Option("--json", help="Machine-readable output.")]
FileOpt = Annotated[
    Path | None, typer.Option("--file", "-f", help="Use this capture instead of the latest.")
]
GroupOpt = Annotated[
    list[str] | None, typer.Option("--group", "-g", help="Only this group (repeatable).")
]
GuessedOpt = Annotated[
    bool, typer.Option("--include-guessed", help="Also sessions whose id is only a guess.")
]
NoLabelOpt = Annotated[
    bool, typer.Option("--no-label", help="Don't put group badges and tab colors on tabs.")
]


class Runtime:
    """Config, system adapters, capture store and output style for one command."""

    def __init__(self, plain: bool = False) -> None:
        try:
            self.cfg: Config = load_config()
        except ConfigError as e:
            _die(str(e))
        self.system: System = load_system(self.cfg)
        self.store = Store(self.cfg.captures_dir, self.cfg.keep_captures)
        self.style = Style.detect(plain)

    def echo(self, text: str) -> None:
        typer.echo(text, color=self.style.color)

    def load(self, path: Path | None = None, *, required: bool = True) -> Snapshot | None:
        try:
            snap = self.store.load(path)
        except CaptureError as e:
            _die(str(e))
        if snap is None and required:
            _die("no capture yet: run `claude-manage capture` first")
        return snap

    def previous(self) -> Snapshot | None:
        """The last capture, to carry group names over; an unreadable one is skipped."""
        try:
            return self.store.load()
        except CaptureError as e:
            _warn(f"ignoring the last capture (group names start over): {e}")
            return None

    def discover(
        self, *, titles: bool = True, window: str | None = None, need_tabs: bool = False
    ) -> Discovery:
        found = self.system.discover(titles=titles, window=window)
        if found.layout_error and need_tabs:
            _die(f"cannot read the iTerm2 windows and tabs: {found.layout_error}")
        return found

    def snapshot(
        self,
        *,
        names: dict[str, str] | None = None,
        window: str | None = None,
        reason: str = "manual",
        installed: str = "",
        need_tabs: bool = True,
    ) -> tuple[Snapshot, Discovery, list[str]]:
        found = self.discover(window=window, need_tabs=need_tabs)
        snap, unknown = build_snapshot(
            found.found,
            previous=self.previous(),
            names=names,
            installed=installed,
            reason=reason,
        )
        return snap, found, unknown

    def window_id(self, value: str) -> str:
        """An iTerm2 window by id, or by the number views show (``iTerm window N``)."""
        try:
            tabs = self.system.terminal.layout()
        except TerminalError as e:
            _die(f"cannot read the iTerm2 windows and tabs: {e}")
        if any(t.window_id == value for t in tabs):
            return value
        by_index = {str(t.window_index): t.window_id for t in tabs}
        if value not in by_index:
            _die(f"no iTerm2 window {value!r}: give the N of 'iTerm window N' or a window id")
        return by_index[value]

    def label(self, snap: Snapshot) -> int:
        """Badge and color every running tab of ``snap`` with its group."""
        return labels.apply(snap, self.discover(titles=False), self.system.terminal)


def _die(message: str, code: int = 2) -> NoReturn:
    typer.echo(f"claude-manage: {message}", err=True)
    raise typer.Exit(code)


def _warn(message: str) -> None:
    typer.echo(f"claude-manage: warning: {message}", err=True)


def _version(value: bool) -> None:
    if value:
        typer.echo(f"claude-manage {__version__}")
        raise typer.Exit


@app.callback(invoke_without_command=True)
def main_callback(
    ctx: typer.Context,
    version: Annotated[
        bool, typer.Option("--version", callback=_version, is_eager=True, help="Show version.")
    ] = False,
) -> None:
    """Without a command: `show`."""
    if ctx.invoked_subcommand is None:
        show()


@app.command()
def capture(
    name: Annotated[
        list[str] | None,
        typer.Option(
            "--name",
            "-n",
            help="Name a group: CURRENT=NAME, where CURRENT is the name shown now, "
            "window-N (iTerm window N) or an iTerm2 window id.",
        ),
    ] = None,
    window: Annotated[
        str | None,
        typer.Option(help="Only this window (N of 'iTerm window N', or its id); merged."),
    ] = None,
    no_label: NoLabelOpt = False,
    plain: PlainOpt = False,
    json_out: JsonOpt = False,
) -> None:
    """Snapshot running sessions (grouped by iTerm2 window) into ~/.claude-manage."""
    rt = Runtime(plain)
    names: dict[str, str] = {}
    for item in name or []:
        key, sep, value = item.partition("=")
        if not sep or not key or not value:
            _die(f"--name expects CURRENT=NAME, got {item!r}")
        names[key] = value
    window_id = rt.window_id(window) if window is not None else None
    installed = installed_version(rt.cfg)
    snap, found, unknown = rt.snapshot(names=names, window=window_id, installed=installed)
    if window_id is not None:
        snap = merge_window(rt.previous(), snap, window_id)
        found = rt.discover(titles=False)
    path = rt.store.save(snap)
    for key in unknown:
        _warn(f"--name {key}=…: no group or window called {key!r}")
    if not no_label:
        labels.apply(snap, found, rt.system.terminal)
    if json_out:
        typer.echo(json.dumps(snap.to_dict(), indent=2, ensure_ascii=False))
        return
    rt.echo(snapshot_view(snap, check(snap, found), installed, rt.style))
    typer.echo(f"\nsaved {path}")


@app.command()
def show(
    file: FileOpt = None,
    list_captures: Annotated[bool, typer.Option("--list", help="List capture files.")] = False,
    no_live: Annotated[
        bool, typer.Option("--no-live", help="Skip checking what is running now.")
    ] = False,
    plain: PlainOpt = False,
    json_out: JsonOpt = False,
) -> None:
    """Show the last capture, verified against what is running now."""
    rt = Runtime(plain)
    if list_captures:
        for path in rt.store.history():
            try:
                snap = rt.store.load(path)
            except CaptureError as e:
                typer.echo(f"{path}  unreadable: {e}")
                continue
            assert snap is not None
            n = sum(1 for _ in snap.sessions())
            typer.echo(f"{path}  {snap.reason:<13} {n:>3} sessions  {len(snap.groups)} groups")
        return
    snap = rt.load(file, required=file is not None)
    installed = installed_version(rt.cfg)
    live = None if no_live else check(snap, rt.discover(titles=snap is None))
    if json_out:
        typer.echo(json.dumps(_show_json(snap, live, installed), indent=2, ensure_ascii=False))
        return
    rt.echo(snapshot_view(snap, live, installed, rt.style))


def _show_json(snap: Snapshot | None, live: Live | None, installed: str) -> dict[str, object]:
    def brief(sessions: dict[str, Session]) -> dict[str, object]:
        return {
            sid: {"pid": s.pid, "tty": s.tty, "status": s.status, "version": s.version}
            for sid, s in sessions.items()
        }

    return {
        "installed_version": installed,
        "capture": snap.to_dict() if snap else None,
        "running": brief(live.running) if live else None,
        "guessed": brief(live.guessed) if live else None,
        "untracked": [asdict(f.session) for f in live.untracked] if live else [],
        "unresolved_pids": [p.pid for p in live.unresolved] if live else [],
        "layout_error": live.layout_error if live else "",
    }


@app.command()
def rename(
    old: Annotated[str, typer.Argument(help="Current group name.")],
    new: Annotated[str, typer.Argument(help="New group name.")],
    no_label: NoLabelOpt = False,
) -> None:
    """Rename a group in the last capture (kept by later captures)."""
    rt = Runtime()
    snap = rt.load()
    assert snap is not None
    group = snap.group(old)
    if group is None:
        _die(f"no group {old!r}; groups: {', '.join(g.name for g in snap.groups)}")
    if new != old and snap.group(new) is not None:
        _die(f"a group named {new!r} already exists")
    group.name = new
    rt.store.write_latest(snap)
    if not no_label:
        rt.label(snap)
    rt.echo(snapshot_view(snap, None, snap.installed_version, rt.style))


@app.command()
def label(
    group: GroupOpt = None,
    clear: Annotated[bool, typer.Option("--clear", help="Remove the labels instead.")] = False,
) -> None:
    """Put each group's name (badge) and color (tab color) on its running tabs."""
    rt = Runtime()
    snap = rt.load()
    assert snap is not None
    if group:
        if missing := [g for g in group if snap.group(g) is None]:
            _die(f"no group {', '.join(missing)}; groups: {', '.join(g.name for g in snap.groups)}")
        snap = Snapshot(snap.captured_at, [g for g in snap.groups if g.name in group])
    n = labels.apply(
        snap, rt.discover(titles=False, need_tabs=True), rt.system.terminal, clear=clear
    )
    typer.echo(f"{'cleared' if clear else 'labelled'} {n} tab{'s' * (n != 1)}")


@app.command("resume")
def resume_cmd(
    group: GroupOpt = None,
    file: FileOpt = None,
    include_guessed: GuessedOpt = False,
    force: Annotated[
        bool, typer.Option("--force", help="Also reopen sessions that are running.")
    ] = False,
    no_verify: Annotated[
        bool, typer.Option("--no-verify", help="Don't wait for the sessions to come back.")
    ] = False,
    no_label: NoLabelOpt = False,
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Only show the plan.")] = False,
    plain: PlainOpt = False,
) -> None:
    """Reopen captured sessions that are not running: one iTerm2 window per group."""
    rt = Runtime(plain)
    snap = rt.load(file)
    assert snap is not None
    groups = group or []
    if missing := [g for g in groups if snap.group(g) is None]:
        _die(f"no group {', '.join(missing)}; groups: {', '.join(g.name for g in snap.groups)}")
    found = rt.discover(titles=False, need_tabs=not dry_run)
    plans = resume.plan(snap, found, groups, force=force, include_guessed=include_guessed)
    installed = installed_version(rt.cfg)
    if dry_run or not any(gp.todo for gp in plans):
        rt.echo(resume.view(snap, plans, installed, rt.style, done=False))
        return
    try:
        check_claude_bin(rt.cfg)
    except ClaudeBinError as e:
        _die(str(e))
    resume.execute(plans, rt.system.terminal, rt.cfg.claude_bin)
    if not no_verify:
        resume.verify(plans, rt.system, rt.cfg.verify_timeout)
    if not no_label:
        rt.label(snap)
    timeout = rt.cfg.verify_timeout
    rt.echo(resume.view(snap, plans, installed, rt.style, done=True, timeout=timeout))
    if resume.failed(plans):
        raise typer.Exit(1)


@app.command("relaunch")
def relaunch_cmd(
    group: GroupOpt = None,
    session: Annotated[
        list[str] | None,
        typer.Option("--session", "-s", help="Only this session: id, id prefix or title."),
    ] = None,
    window: Annotated[
        str | None, typer.Option(help="Only this window (N of 'iTerm window N', or its id).")
    ] = None,
    outdated: Annotated[
        bool, typer.Option("--outdated", help="Only sessions older than the installed Claude.")
    ] = False,
    include_busy: Annotated[
        bool,
        typer.Option(
            "--include-busy", help="Also interrupt busy sessions and ones whose state is unknown."
        ),
    ] = False,
    include_guessed: GuessedOpt = False,
    wait: Annotated[
        float, typer.Option(help="Seconds (in total) to wait for busy sessions to become idle.")
    ] = 0.0,
    kill: Annotated[
        bool, typer.Option("--kill", help="SIGKILL sessions that ignore SIGTERM.")
    ] = False,
    no_self: Annotated[
        bool, typer.Option("--no-self", help="Leave the session running this command alone.")
    ] = False,
    foreground: Annotated[
        bool, typer.Option("--foreground", help="Never move to the background.")
    ] = False,
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Only show the plan.")] = False,
    plain: PlainOpt = False,
    worker: Annotated[Path | None, typer.Option(hidden=True)] = None,
) -> None:
    """Restart running sessions in place (same tab), e.g. after a Claude Code upgrade."""
    rt = Runtime(plain)
    if worker is not None:
        _relaunch_worker(rt, worker)
        return
    opts = relaunch.Options(
        groups=group or [],
        sessions=session or [],
        window=rt.window_id(window) if window is not None else None,
        outdated=outdated,
        include_busy=include_busy,
        include_guessed=include_guessed,
        include_self=not no_self and not foreground,
        wait=wait,
        kill=kill,
        stop_timeout=rt.cfg.stop_timeout,
        verify_timeout=rt.cfg.verify_timeout,
    )
    installed = installed_version(rt.cfg)
    if not dry_run:  # before anything is stopped: the new claude must be startable
        try:
            version = check_claude_bin(rt.cfg)
        except ClaudeBinError as e:
            _die(str(e))
        installed = rt.cfg.installed_version or version
    snap, found, _ = rt.snapshot(reason="pre-relaunch", installed=installed)
    if missing := [g for g in opts.groups if snap.group(g) is None]:
        _die(f"no group {', '.join(missing)}; groups: {', '.join(g.name for g in snap.groups)}")
    steps, left_out = relaunch.select(snap, found, installed, opts)
    plan = relaunch.view(steps, left_out, installed, rt.style, done=False, stamp=snap.captured_at)
    rt.echo(plan)
    if dry_run or not any(s.action != "skip" for s in steps):
        return
    pre = rt.store.save(snap)  # safety net: `claude-manage resume --file` reopens everything
    stamp = now_stamp().replace(":", "-")
    log = rt.cfg.logs_dir / f"relaunch-{stamp}.log"
    if any(s.is_self and s.action != "skip" for s in steps):
        run_file = rt.cfg.runs_dir / f"relaunch-{stamp}.json"
        relaunch.save_run(run_file, steps, opts, rt.cfg.claude_bin)
        pid = relaunch.spawn_worker(run_file, log)
        typer.echo(
            f"\nThis session is part of the relaunch, so it continues in the background "
            f"(pid {pid}) and restarts this session last.\n"
            f"Follow it with: claude-manage log -f   ({log})"
        )
        return
    emit = relaunch.Emitter(sys.stdout, log)
    typer.echo("")
    try:
        relaunch.execute(steps, rt.system, rt.cfg.claude_bin, opts, emit)
    except BaseException as e:
        emit(f"{relaunch.FINISHED}: aborted ({type(e).__name__}) · before: {pre}")
        raise
    _relaunch_finish(rt, steps, left_out, emit, pre)


def _relaunch_worker(rt: Runtime, run_file: Path) -> None:
    emit = relaunch.Emitter(sys.stdout, stamp=True)
    emit(f"{relaunch.WORKER} {os.getpid()}")
    try:
        steps, opts, claude_bin = relaunch.load_run(run_file)
        emit(f"restarting {sum(s.action != 'skip' for s in steps)} sessions")
        relaunch.execute(steps, rt.system, claude_bin, opts, emit)
        _relaunch_finish(rt, steps, 0, emit, None)
    except typer.Exit:
        raise
    except BaseException as e:
        emit(f"{relaunch.FINISHED}: aborted ({type(e).__name__}: {e})")
        raise typer.Exit(2) from e
    finally:
        run_file.unlink(missing_ok=True)


def _relaunch_finish(
    rt: Runtime,
    steps: list[relaunch.Step],
    left_out: int,
    emit: relaunch.Emitter,
    pre: Path | None,
) -> None:
    after, found, _ = rt.snapshot(
        reason="post-relaunch", installed=installed_version(rt.cfg), need_tabs=False
    )
    lost = relaunch.keep_lost(after, steps)
    path = rt.store.save(after)
    labels.apply(after, found, rt.system.terminal)
    typer.echo("")
    stamp = after.captured_at
    rt.echo(
        relaunch.view(steps, left_out, after.installed_version, rt.style, done=True, stamp=stamp)
    )
    tail = f" · capture {path}"
    if lost:
        before = f" (before: {pre})" if pre else ""
        tail += f" · {len(lost)} stopped and not back: `claude-manage resume` reopens them{before}"
    emit(f"{relaunch.FINISHED}: {relaunch.summary(steps)}{tail}")
    if not relaunch.ok(steps):
        raise typer.Exit(1)


@app.command()
def log(
    follow: Annotated[
        bool, typer.Option("--follow", "-f", help="Keep printing until the relaunch finishes.")
    ] = False,
    path_only: Annotated[bool, typer.Option("--path", help="Only print the log path.")] = False,
) -> None:
    """Print the latest relaunch log (exit 1 if that relaunch had failures)."""
    rt = Runtime()
    logs = sorted(rt.cfg.logs_dir.glob("relaunch-*.log")) if rt.cfg.logs_dir.is_dir() else []
    if not logs:
        _die("no relaunch logs yet", 1)
    path = logs[-1]
    if path_only:
        typer.echo(str(path))
        return
    worker, finished, quiet_since = 0, "", time.monotonic()
    with path.open() as f:
        while True:
            line = f.readline()
            if line:
                typer.echo(line, nl=False)
                quiet_since = time.monotonic()
                if f"{relaunch.WORKER} " in line:
                    worker = int(line.rsplit(" ", 1)[-1].strip() or 0)
                if relaunch.FINISHED in line:
                    finished = line
                    break
            elif not follow:
                break
            elif worker and not _alive(worker) and time.monotonic() - quiet_since > 1.0:
                _die("the relaunch worker exited without finishing", 1)
            else:
                time.sleep(0.3)
    if any(word in finished for word in ("failed", "unverified", "aborted")):
        raise typer.Exit(1)


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def main() -> None:
    app()
