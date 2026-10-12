from __future__ import annotations

import re
from pathlib import Path

import pytest

from claude_manage.capture import NOT_IN_ITERM, build_snapshot, merge_window
from claude_manage.discovery import Discovery, Found
from claude_manage.live import check
from claude_manage.model import Group, Session, Snapshot
from claude_manage.render import Style, display_width, short_path, state_icon, truncate
from claude_manage.terminal import TabInfo
from claude_manage.views import snapshot_view


def found(n: int, window: str | None, tab: int = 1, **kw: object) -> Found:
    session = Session(f"{n:08d}-0000-4000-8000-000000000000", cwd="/w", pid=n, **kw)  # type: ignore[arg-type]
    info = TabInfo(window, int(window) // 100, tab, f"tty{n}") if window else None
    return Found(session, info)


def test_groups_are_windows_named_by_preference() -> None:
    items = [found(1, "100", 1), found(2, "100", 2), found(3, "200"), found(4, None)]
    previous = Snapshot("", [Group("ops", "999", 1, [Session(items[2].session.session_id)])])

    snap, unknown = build_snapshot(
        items, previous=previous, names={"window-1": "dev", "window-7": "x"}
    )

    assert [g.name for g in snap.groups] == ["dev", "ops", NOT_IN_ITERM]
    assert [s.tab for s in snap.groups[0].sessions] == [1, 2]
    assert unknown == ["window-7"]


def test_explicit_names_match_shown_names_and_defaults_are_not_carried() -> None:
    items = [found(1, "100"), found(2, "200")]
    previous = Snapshot(
        "",
        [
            Group("window-5", "100", 1, [Session(items[0].session.session_id)]),
            Group("ops", "200", 2, [Session(items[1].session.session_id)]),
        ],
    )
    snap, _ = build_snapshot(items, previous=previous, names={"ops": "backend"})
    assert [g.name for g in snap.groups] == ["window-1", "backend"]


def test_merge_window_replaces_only_that_window() -> None:
    a, b, c = (Session(x) for x in ("a", "b", "c"))
    previous = Snapshot("old", [Group("dev", "100", 1, [a]), Group("ops", "200", 2, [b, c])])
    partial = Snapshot("new", [Group("ops", "200", 2, [Session("c"), Session("d")])])
    merged = merge_window(previous, partial, "200")
    assert [(g.name, [s.session_id for s in g.sessions]) for g in merged.groups] == [
        ("dev", ["a"]),
        ("ops", ["c", "d"]),
    ]
    assert merged.captured_at == "new"


def test_group_names_stay_unique() -> None:
    items = [found(1, "100"), found(2, "200")]
    snap, _ = build_snapshot(items, names={"window-1": "dev", "window-2": "dev"})
    assert [g.name for g in snap.groups] == ["dev", "dev-2"]


def test_short_path(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    deep = f"{tmp_path}/workspace/team/project/service"
    assert short_path(deep, 80) == "~/workspace/team/project/service"
    assert short_path(deep, 20) == "~/…/project/service"
    assert short_path("/very/long/single-component-name", 12) == "…ponent-name"


def test_state_icons() -> None:
    assert state_icon("idle", True)[0] == "●"
    assert state_icon("busy", True)[0] == "◐"
    assert state_icon("waiting", None)[0] == "◐"
    assert state_icon("idle", False)[0] == "○"
    assert state_icon("", True)[0] == "◇"  # running, state unknown
    assert state_icon("", None)[0] == "·"


def test_display_width() -> None:
    assert display_width("abc") == 3
    assert display_width("修复登录") == 8
    assert display_width("🚀x") == 3
    assert display_width("e\u0301") == 1  # combining accent
    assert truncate("修复登录页面的错误", 7) == "修复登…"
    assert display_width(truncate("修复登录页面的错误", 7)) <= 7


def test_view_rows_align_and_color_is_optional() -> None:
    long_title = "a very long session title that will not fit in the column"
    snap = Snapshot(
        "2026-10-12T14:42:27",
        [
            Group(
                "dev",
                "100",
                1,
                [
                    Session("11111111-a", "/w", "short", tab=1, version="2.0.0", status="idle"),
                    Session("22222222-b", "/w", long_title, tab=2, version="2.1.0", guessed=True),
                ],
            )
        ],
    )
    live = check(snap, Discovery([Found(Session("11111111-a", "/w", pid=7, status="idle"), None)]))
    plain = snapshot_view(snap, live, "2.1.0", Style(color=False, width=120))
    lines = plain.splitlines()

    assert lines[0] == "claude-manage · capture 2026-10-12 14:42 · installed v2.1.0"
    rows = [line for line in lines if re.match(r"\s+\d\s", line)]
    assert len(rows) == 2
    assert rows[0].index("11111111") == rows[1].index("22222222")  # columns line up
    assert "a very long session title that wi… ?" in rows[1]
    assert "v2.0.0 ↑" in rows[0]
    assert rows[0].endswith("✓ pid 7 idle")
    assert rows[1].endswith("✗ not running")
    assert lines[-1] == "✓ 1 running · ✗ 1 not running · ↑ 1 outdated · ? 1 guessed id"
    assert "\033[" not in plain

    colored = snapshot_view(snap, live, "2.1.0", Style(color=True, width=120))
    assert "\033[32m✓\033[0m" in colored
    assert re.sub(r"\033\[[0-9;]*m", "", colored) == plain


def test_wide_titles_keep_columns_aligned() -> None:
    sessions = [
        Session("11111111-a", "/w", "修复登录页面的错误", tab=1),
        Session("22222222-b", "/w", "ok", tab=2),
    ]
    snap = Snapshot("", [Group("dev", "100", 1, sessions)])
    out = snapshot_view(snap, None, "", Style(width=120)).splitlines()
    rows = [line for line in out if re.match(r"\s+\d\s", line)]
    ids = ("11111111", "22222222")
    cols = [display_width(r[: r.index(i)]) for r, i in zip(rows, ids, strict=True)]
    assert cols[0] == cols[1]


def test_view_without_live_check() -> None:
    snap = Snapshot("", [Group("dev", "100", 1, [Session("abc", status="busy")])])
    out = snapshot_view(snap, None, "", Style())
    assert "◐" in out
    assert out.splitlines()[-1] == "1 sessions in 1 groups"
