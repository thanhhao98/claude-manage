"""Find running interactive Claude Code sessions and the iTerm2 tab each one lives in."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field

from . import claude_args
from .claude_home import UUID_RE, ClaudeHome, SessionMeta
from .model import Session, parse_version
from .procs import Proc, ProcessTable, ancestors
from .terminal import TabInfo

# Claude Code writes ~/.claude/sessions/<pid>.json for every session from this version
# on (2.1.286 does not). A newer claude without a record is not an identifiable session.
RECORDS_SINCE = (2, 1, 289)
# A guessed transcript must have been written after its process started (minus slack).
_START_SLACK = 5.0
_VERSION_IN_PATH = re.compile(r"/versions/(\d+(?:\.\d+)+)")


@dataclass
class Found:
    session: Session
    tab: TabInfo | None  # None when not in an iTerm2 tab (tmux, another terminal, ...)
    is_self: bool = False  # claude-manage itself runs inside this session
    parent: Proc | None = None  # what started claude: normally the tab's interactive shell
    pgid: int = 0


@dataclass
class Discovery:
    found: list[Found] = field(default_factory=list)
    unresolved: list[Proc] = field(default_factory=list)  # claude whose session is unknown
    layout_error: str = ""  # the terminal could not be read: no session has a tab

    def by_id(self, *, guessed: bool = False) -> dict[str, Found]:
        """Running sessions by id; guessed ids only when asked for."""
        out: dict[str, Found] = {}
        for item in self.found:
            if guessed or not item.session.guessed:
                out.setdefault(item.session.session_id, item)
        return out


def version_from_path(path: str) -> str:
    """``.../claude/versions/2.1.296`` -> ``2.1.296`` (the native installer's layout)."""
    match = _VERSION_IN_PATH.search(path)
    return match.group(1) if match else ""


def discover(
    procs: ProcessTable,
    layout: list[TabInfo],
    claude: ClaudeHome,
    *,
    titles: bool = True,
    window: str | None = None,
) -> Discovery:
    """Every interactive ``claude`` that has a controlling terminal."""
    table = procs.processes()
    by_pid = {p.pid: p for p in table}
    tabs = {t.tty: t for t in layout}
    me = ancestors(table, procs.self_pid())

    candidates: list[tuple[Proc, list[str], SessionMeta | None]] = []
    for proc in table:
        if proc.name != "claude" or not proc.tty:
            continue
        argv = procs.argv(proc.pid) or list(proc.argv)
        if os.path.basename(argv[0]).lstrip("-") != "claude":
            continue
        meta = claude.meta(proc.pid)
        if meta is not None and meta.kind and meta.kind != "interactive":
            continue
        if claude_args.is_interactive(argv):
            candidates.append((proc, argv, meta))

    # Ids that running sessions own: a guess must never hand one of them to another tab.
    claimed = {meta.session_id for _, _, meta in candidates if meta is not None}
    sessions: dict[int, Session | None] = {}
    explicit = [c for c in candidates if c[2] is None and _explicit_id(c[1])]
    the_rest = [c for c in candidates if c[2] is None and not _explicit_id(c[1])]
    for proc, argv, meta in candidates:
        if meta is not None:
            sessions[proc.pid] = _from_record(proc, argv, meta)
    for proc, argv, _ in explicit + the_rest:  # explicit ids claim theirs before guessing
        session = _without_record(proc, argv, claude, procs, claimed)
        if session is not None:
            claimed.add(session.session_id)
        sessions[proc.pid] = session

    out = Discovery()
    for proc, _, meta in candidates:
        tab = tabs.get(proc.tty)
        if window is not None and (tab is None or tab.window_id != window):
            continue
        session = sessions[proc.pid]
        if session is None:
            out.unresolved.append(proc)
            continue
        transcript = claude.transcript(session.session_id, session.cwd)
        session.resumable = transcript is not None
        if titles and not session.title:
            session.title = claude.title(transcript)
        if not session.title and meta is not None:
            session.title = meta.name
        out.found.append(Found(session, tab, proc.pid in me, by_pid.get(proc.ppid), proc.pgid))
    return out


def _from_record(proc: Proc, argv: list[str], meta: SessionMeta) -> Session:
    replay = claude_args.replay(argv)
    return Session(
        session_id=meta.session_id,
        cwd=meta.cwd,
        title=meta.name if meta.name_source != "derived" else "",
        flags=replay.flags,
        dropped=replay.dropped,
        status=meta.status,
        version=meta.version,
        pid=proc.pid,
        tty=proc.tty,
    )


def _explicit_id(argv: list[str]) -> str | None:
    """The id an older claude was started on, when its command line says so."""
    session_id = claude_args.value_of(argv, "--session-id")
    if session_id and UUID_RE.match(session_id):
        return session_id
    resumed = claude_args.value_of(argv, "--resume", "-r")
    if resumed and UUID_RE.match(resumed) and not claude_args.has_option(argv, "--fork-session"):
        return resumed
    return None


def _without_record(
    proc: Proc,
    argv: list[str],
    claude: ClaudeHome,
    procs: ProcessTable,
    claimed: set[str],
) -> Session | None:
    """An older Claude Code session (no record): its id can only be inferred, so it is
    always ``guessed`` (/clear or /resume inside it also change the id silently)."""
    version = version_from_path(procs.exe(proc.pid))
    if version and (parse_version(version) or ()) >= RECORDS_SINCE:
        return None  # would have written a record: not an identifiable session
    cwd = procs.cwd(proc.pid)
    session_id = _explicit_id(argv)
    title = ""
    if session_id is None and cwd:
        resumed = claude_args.value_of(argv, "--resume", "-r")
        if resumed and not UUID_RE.match(resumed):
            found = claude.find_by_title(resumed, cwd)
            if found and found not in claimed:
                session_id, title = found, resumed
    if session_id is None and cwd:
        start = procs.start_time(proc.pid)
        since = start - _START_SLACK if start else 0.0
        session_id = claude.newest_transcript(cwd, exclude=frozenset(claimed), since=since)
    if session_id is None or session_id in claimed:
        return None
    replay = claude_args.replay(argv)
    return Session(
        session_id=session_id,
        cwd=cwd,
        title=title,
        flags=replay.flags,
        dropped=replay.dropped,
        version=version,
        guessed=True,
        pid=proc.pid,
        tty=proc.tty,
    )
