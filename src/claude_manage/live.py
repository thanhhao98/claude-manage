"""Verify a capture against what is running right now."""

from __future__ import annotations

from dataclasses import dataclass, field

from .discovery import Discovery, Found
from .model import Session, Snapshot
from .procs import Proc


@dataclass
class Live:
    running: dict[str, Session] = field(default_factory=dict)  # id -> session, record-backed
    guessed: dict[str, Session] = field(default_factory=dict)  # id -> session, id inferred
    untracked: list[Found] = field(default_factory=list)  # running but not in the capture
    unresolved: list[Proc] = field(default_factory=list)  # claude with an unknown session
    layout_error: str = ""


def check(snap: Snapshot | None, discovery: Discovery) -> Live:
    captured = {s.session_id for _, s in snap.sessions()} if snap else set()
    live = Live(unresolved=discovery.unresolved, layout_error=discovery.layout_error)
    for item in discovery.found:
        session = item.session
        bucket = live.guessed if session.guessed else live.running
        bucket.setdefault(session.session_id, session)
        if session.session_id not in captured:
            live.untracked.append(item)
    return live
