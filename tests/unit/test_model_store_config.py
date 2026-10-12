from __future__ import annotations

import json
from pathlib import Path

import pytest

from claude_manage.config import ConfigError, load
from claude_manage.model import Group, Session, Snapshot, is_outdated, parse_version
from claude_manage.store import CaptureError, Store


def snap(stamp: str = "2026-01-02T03:04:05", reason: str = "manual") -> Snapshot:
    session = Session("11111111-2222-4333-8444-555555555555", cwd="/w", title="t", flags=["-x"])
    return Snapshot(stamp, [Group("dev", "7", 1, [session])], reason=reason)


def test_snapshot_round_trip() -> None:
    original = snap()
    assert Snapshot.from_dict(json.loads(json.dumps(original.to_dict()))) == original


def test_snapshot_tolerates_hand_edits() -> None:
    data = {
        "groups": [{"sessions": [{"session_id": "abc", "unknown": 1, "title": "x"}]}],
        "extra": True,
    }
    loaded = Snapshot.from_dict(data)
    assert loaded.groups[0].name == "window-1"
    assert loaded.groups[0].sessions[0].title == "x"
    assert loaded.groups[0].sessions[0].label == "x"
    assert Session("abcdef0123").label == "abcdef01… (unnamed)"


@pytest.mark.parametrize(
    "bad",
    [
        {"groups": [{"sessions": [{"cwd": "/x"}]}]},  # no session_id
        {"groups": [{"sessions": [{"session_id": "a", "flags": "--x"}]}]},  # flags not a list
        {"groups": [{"sessions": [{"session_id": "a", "title": 2024}]}]},
        {"groups": [{"sessions": [{"session_id": "a", "tab": True}]}]},
        {"groups": [{"sessions": [{"session_id": "a", "guessed": "no"}]}]},
    ],
)
def test_snapshot_rejects_broken_sessions(bad: dict[str, object]) -> None:
    with pytest.raises(ValueError, match=r"session_id|flags|title|tab|guessed"):
        Snapshot.from_dict(bad)


def test_hand_edited_cwd_with_tilde_is_expanded() -> None:
    loaded = Snapshot.from_dict({"groups": [{"sessions": [{"session_id": "a", "cwd": "~/x"}]}]})
    assert loaded.groups[0].sessions[0].cwd == str(Path("~/x").expanduser())


def test_versions() -> None:
    assert parse_version("2.1.296") == (2, 1, 296)
    assert parse_version("v1.0") == (1, 0)
    assert parse_version("") is None
    assert is_outdated("2.1.289", "2.1.296")
    assert not is_outdated("2.1.296", "2.1.296")
    assert not is_outdated("2.2.0", "2.1.296")
    assert not is_outdated("", "2.1.296")
    assert not is_outdated("2.1.0", "")


def test_store_save_load_latest_and_prune(tmp_path: Path) -> None:
    store = Store(tmp_path / "captures", keep=2)
    assert store.load() is None
    first = store.save(snap("2026-01-01T00:00:00"))
    second = store.save(snap("2026-01-01T00:00:00"))  # same second: distinct file
    assert first != second
    assert second.name == "2026-01-01T00-00-00_2.json"
    third = store.save(snap("2026-01-02T00:00:00", reason="post-relaunch"))
    assert store.history() == [second, third]  # pruned to keep=2, oldest first
    latest = store.load()
    assert latest is not None
    assert latest.reason == "post-relaunch"
    assert not list((tmp_path / "captures").glob(".*.tmp"))  # atomic writes leave no temp files


def test_store_errors(tmp_path: Path) -> None:
    store = Store(tmp_path)
    with pytest.raises(CaptureError, match="no such capture"):
        store.load(tmp_path / "missing.json")
    store.latest.write_text("[]")
    with pytest.raises(CaptureError, match="invalid capture"):
        store.load()


def test_config_precedence(tmp_path: Path) -> None:
    home = tmp_path / "cm"
    home.mkdir()
    (home / "config.toml").write_text('claude_bin = "~/bin/claude"\nstop_timeout = 5\n')
    cfg = load({"CLAUDE_MANAGE_HOME": str(home), "CLAUDE_MANAGE_STOP_TIMEOUT": "7"})
    assert cfg.home == home
    assert cfg.claude_bin == str(Path("~/bin/claude").expanduser())  # from config.toml
    assert cfg.stop_timeout == 7.0  # env beats config.toml
    assert cfg.verify_timeout == 45.0  # default
    assert cfg.captures_dir == home / "captures"
    assert cfg.sessions_dir == Path("~/.claude/sessions").expanduser()
    assert cfg.pid_allowlist is None
    assert cfg.fake_world is None

    cfg = load({"CLAUDE_MANAGE_HOME": str(home), "CLAUDE_MANAGE_PID_ALLOWLIST": "1, 2"})
    assert cfg.pid_allowlist == frozenset({1, 2})


def test_config_errors(tmp_path: Path) -> None:
    (tmp_path / "config.toml").write_text("stop_timeout = [")
    with pytest.raises(ConfigError, match=r"config\.toml"):
        load({"CLAUDE_MANAGE_HOME": str(tmp_path)})
    with pytest.raises(ConfigError, match="invalid setting"):
        load({"CLAUDE_MANAGE_HOME": str(tmp_path / "x"), "CLAUDE_MANAGE_STOP_TIMEOUT": "soon"})
