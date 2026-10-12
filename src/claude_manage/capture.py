"""Turn running sessions into a :class:`Snapshot`: one group per iTerm2 window."""

from __future__ import annotations

import re
from datetime import datetime

from .discovery import Found
from .model import Group, Snapshot

NOT_IN_ITERM = "(not in iTerm2)"
_DEFAULT_NAME = re.compile(r"window-\d+")


def now_stamp() -> str:
    return datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


def build_snapshot(
    found: list[Found],
    *,
    previous: Snapshot | None = None,
    names: dict[str, str] | None = None,
    installed: str = "",
    reason: str = "manual",
    captured_at: str = "",
) -> tuple[Snapshot, list[str]]:
    """Group sessions by window and name the groups.

    Names are applied in this order: ``names`` (a key may be the name a group shows
    now, its iTerm2 window id, or ``window-N`` for the N-th window front to back),
    then the name the previous capture gave the group sharing the most sessions
    (only names someone chose, never a ``window-N`` default), then ``window-N``.
    Returns the snapshot and the ``names`` keys that matched no window.
    """
    names = dict(names or {})
    groups: dict[str, Group] = {}
    for item in found:
        if item.tab is None:
            group = groups.setdefault("", Group(name=NOT_IN_ITERM, window_index=1_000_000))
        else:
            key = item.tab.window_id
            group = groups.setdefault(
                key,
                Group(
                    name=f"window-{item.tab.window_index}",
                    window_id=key,
                    window_index=item.tab.window_index,
                ),
            )
            item.session.tab = item.tab.tab_index
        group.sessions.append(item.session)

    ordered = sorted(groups.values(), key=lambda g: g.window_index)
    windows = [g for g in ordered if g.name != NOT_IN_ITERM]
    for group in ordered:
        group.sessions.sort(key=lambda s: (s.tab, s.tty))

    carried = {id(g): _carried_name(g, previous) for g in windows}
    chosen: dict[int, str] = {}
    used = {NOT_IN_ITERM}
    for group in windows:  # explicit names first: they win over carried ones
        for candidate in (carried[id(group)], group.window_id, group.name):
            if candidate and candidate in names:
                chosen[id(group)] = _unique(names.pop(candidate), used)
                used.add(chosen[id(group)])
                break
    for group in windows:
        explicit = chosen.get(id(group))
        old = carried[id(group)]
        if explicit is not None:
            name = explicit
        elif old is not None and old not in used:
            name = old
        else:
            name = _unique(group.name, used)
        group.name = name
        used.add(name)
    snap = Snapshot(
        captured_at=captured_at or now_stamp(),
        groups=ordered,
        reason=reason,
        installed_version=installed,
    )
    return snap, sorted(names)


def merge_window(previous: Snapshot | None, partial: Snapshot, window_id: str) -> Snapshot:
    """``previous`` with the group of ``window_id`` replaced by ``partial``'s capture of it,
    so capturing one window never drops the others."""
    if previous is None:
        return partial
    fresh = {s.session_id for _, s in partial.sessions()}
    groups = []
    replaced = False
    for group in previous.groups:
        if group.window_id == window_id:
            groups += partial.groups
            replaced = True
            continue
        group.sessions = [s for s in group.sessions if s.session_id not in fresh]
        if group.sessions:
            groups.append(group)
    if not replaced:
        groups += partial.groups
    return Snapshot(
        captured_at=partial.captured_at,
        groups=groups,
        reason=partial.reason,
        installed_version=partial.installed_version,
    )


def _carried_name(group: Group, previous: Snapshot | None) -> str | None:
    if previous is None:
        return None
    ids = {s.session_id for s in group.sessions}
    best, best_overlap = None, 0
    for old in previous.groups:
        if old.name == NOT_IN_ITERM or _DEFAULT_NAME.fullmatch(old.name):
            continue
        overlap = len(ids & {s.session_id for s in old.sessions})
        if overlap > best_overlap:
            best, best_overlap = old.name, overlap
    return best


def _unique(name: str, used: set[str]) -> str:
    candidate, n = name, 1
    while candidate in used:
        n += 1
        candidate = f"{name}-{n}"
    return candidate
