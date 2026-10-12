from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from claude_manage.claude_home import ClaudeHome, project_dir_name

A = "aaaaaaaa-0000-4000-8000-000000000001"
B = "bbbbbbbb-0000-4000-8000-000000000002"
LONG = "/Users/me/" + "very-long-folder-name/" * 12


def home(tmp_path: Path) -> ClaudeHome:
    return ClaudeHome(tmp_path / "sessions", tmp_path / "projects")


def transcript(claude: ClaudeHome, cwd: str, session_id: str, *lines: object) -> Path:
    folder = claude.project_dir(cwd)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{session_id}.jsonl"
    path.write_text("".join(json.dumps(line) + "\n" for line in lines))
    return path


@pytest.mark.parametrize(
    ("cwd", "name"),
    [
        ("/Users/me/my_repo.v2", "-Users-me-my-repo-v2"),
        ("/Users/me/🚀 app", "-Users-me----app"),  # one emoji = two UTF-16 units
        # computed with Claude Code's own DO()/Use() in node
        (LONG, ("-Users-me-" + "very-long-folder-name-" * 12)[:200] + "-u8wc17"),
    ],
)
def test_project_dir_matches_claude_code_naming(cwd: str, name: str) -> None:
    assert project_dir_name(cwd) == name


def test_meta(tmp_path: Path) -> None:
    claude = home(tmp_path)
    claude.sessions_dir.mkdir()
    (claude.sessions_dir / "42.json").write_text(
        json.dumps({"sessionId": A, "cwd": "/w", "status": "busy", "version": "2.1.0"})
    )
    (claude.sessions_dir / "43.json").write_text("{broken")
    (claude.sessions_dir / "44.json").write_text(json.dumps({"cwd": "/w"}))
    meta = claude.meta(42)
    assert meta is not None
    assert (meta.session_id, meta.status, meta.version) == (A, "busy", "2.1.0")
    assert claude.meta(43) is None
    assert claude.meta(44) is None
    assert claude.meta(45) is None


def test_title_prefers_the_last_custom_title(tmp_path: Path) -> None:
    claude = home(tmp_path)
    path = transcript(
        claude,
        "/w",
        A,
        {"type": "ai-title", "aiTitle": "auto title"},
        {"type": "custom-title", "customTitle": "first"},
        # the key quoted inside another string is escaped, so it must not match
        {"type": "user", "message": 'set "customTitle": "fake" please'},
        {"type": "custom-title", "customTitle": 'second "quoted" ünïcode'},
    )
    assert claude.title(path) == 'second "quoted" ünïcode'
    only_ai = transcript(claude, "/w", B, {"type": "ai-title", "aiTitle": "auto title"})
    assert claude.title(only_ai) == "auto title"
    assert claude.title(None) == ""
    empty = transcript(claude, "/w", "cccccccc-0000-4000-8000-000000000003")
    assert claude.title(empty) == ""


def test_lookup_by_title_and_newest(tmp_path: Path) -> None:
    claude = home(tmp_path)
    old = transcript(claude, "/w", A, {"customTitle": "release"})
    new = transcript(claude, "/w", B, {"customTitle": "other"})
    os.utime(old, (1_000, 1_000))
    os.utime(new, (2_000, 2_000))
    (claude.project_dir("/w") / "agent-1234.jsonl").write_text("{}\n")  # not a session

    assert claude.find_by_title("release", "/w") == A
    assert claude.find_by_title("missing", "/w") is None
    assert claude.newest_transcript("/w") == B
    assert claude.newest_transcript("/w", exclude=frozenset({B})) == A
    assert claude.newest_transcript("/w", since=1_500) == B
    assert claude.newest_transcript("/w", exclude=frozenset({B}), since=1_500) is None
    assert claude.newest_transcript("/elsewhere") is None
    assert claude.transcript(A, "/w") == old
    assert claude.transcript(A) == old  # found without knowing the cwd
    assert claude.transcript("dddddddd-0000-4000-8000-000000000004") is None
