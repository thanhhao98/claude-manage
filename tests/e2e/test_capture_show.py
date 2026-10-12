"""capture / show / rename end to end, against dummy groups and sessions."""

from __future__ import annotations

import json
import re
import time

from tests.support import FLAG, INSTALLED, World, sid


def three_sessions(world: World) -> tuple[str, str, str]:
    a = world.session(900_001, window=1, tab=1, title="alpha")
    b = world.session(900_002, window=1, tab=2, title="beta", status="busy")
    c = world.session(900_003, window=2, tab=1, ai_title="gamma-ai")  # no /rename title
    return a, b, c


def test_capture_groups_sessions_by_window(world: World) -> None:
    a, _, c = three_sessions(world)

    out = world.run("capture", "--name", "window-1=dev").stdout

    assert f"installed v{INSTALLED}" in out
    assert "▾ dev · iTerm window 1 · 2 tabs" in out
    assert "▾ window-2 · iTerm window 2 · 1 tab" in out
    assert re.search(r"1\s+●\s+alpha\s+", out)
    assert re.search(r"2\s+◐\s+beta\s+", out)
    assert re.search(rf"1\s+●\s+gamma-ai\s+{c[:8]}", out)
    assert "✓ 3 running" in out
    assert "saved " in out

    latest = world.latest()
    assert [g["name"] for g in latest["groups"]] == ["dev", "window-2"]
    first = latest["groups"][0]["sessions"][0]
    assert first["session_id"] == a
    assert first["flags"] == [FLAG]
    assert first["tab"] == 1
    assert first["resumable"] is True


def test_capture_labels_tabs_with_their_group(world: World) -> None:
    three_sessions(world)
    world.run("capture", "--name", "window-1=dev")
    assert world.labels() == {"tty900001": "dev", "tty900002": "dev", "tty900003": "window-2"}

    world.run("rename", "window-2", "ops")
    assert world.labels()["tty900003"] == "ops"

    assert world.run("label", "--clear").stdout.strip() == "cleared 3 tabs"
    assert set(world.labels().values()) == {""}
    assert world.run("label", "--group", "dev").stdout.strip() == "labelled 2 tabs"


def test_capture_no_label_leaves_tabs_alone(world: World) -> None:
    three_sessions(world)
    world.run("capture", "--no-label")
    assert world.labels() == {}


def test_show_matches_capture_and_verifies_live_state(world: World) -> None:
    three_sessions(world)
    world.run("capture")
    world.stop(900_002)  # beta's claude exited
    world.session(900_004, window=2, tab=2, title="delta")  # started after the capture

    out = world.run("show").stdout

    assert re.search(r"○\s+beta\s+.*✗ not running", out)
    assert re.search(r"●\s+alpha\s+.*✓ pid 900001 idle", out)
    assert "▾ not in capture" in out
    assert re.search(r"●\s+delta\s+.*\+ pid 900004", out)
    assert "✓ 2 running · ✗ 1 not running · + 1 not in capture" in out


def test_show_flags_outdated_versions_and_guessed_ids(world: World) -> None:
    world.session(900_001, title="old", version="2.0.0")
    world.session(900_002, tab=2, title="new", version=INSTALLED)
    # Older Claude Code: no session record and a bare `-r`, so the id is inferred from
    # the newest transcript in its cwd, and the version from its executable path.
    world.session(
        900_003,
        tab=3,
        record=False,
        argv=["claude", FLAG, "-r"],
        cwd="/tmp/cm-test/legacy",
        ai_title="legacy-ai",
        exe="/opt/claude/versions/1.9.0",
    )

    out = world.run("capture").stdout

    assert re.search(r"old\s+\w{8}\s+v2\.0\.0 ↑", out)
    assert re.search(rf"new\s+\w{{8}}\s+v{re.escape(INSTALLED)}\s", out)
    assert re.search(r"◇\s+legacy-ai \?\s+\w{8}\s+v1\.9\.0 ↑", out)  # ◇: status unknown
    assert "↑ 2 outdated" in out
    assert "? 1 guessed id" in out


def test_legacy_ids_never_reuse_a_live_sessions_id(world: World) -> None:
    """DI3: a guess skips ids that running sessions own; with nothing left, the process
    is reported instead of mislabelled."""
    world.session(900_001, title="recorded")  # owns the newest transcript in the folder
    world.session(
        900_002, tab=2, record=False, transcript=False, argv=["claude", FLAG],
        exe="/x/versions/2.0.0",
    )  # fmt: skip

    out = world.run("show").stdout

    assert "recorded" in out
    assert out.count(sid(900_001)[:8]) == 1  # not given to the legacy process too
    assert "1 claude process(es) without an identifiable session (pid 900002)" in out


def test_legacy_guesses_must_be_written_after_the_process_started(world: World) -> None:
    old = world.session(
        900_001, record=False, ai_title="written-before-start", argv=["claude", FLAG],
        started=time.time() + 3600, exe="/x/versions/2.0.0",
    )  # fmt: skip
    out = world.run("show").stdout
    assert old[:8] not in out
    assert "without an identifiable session (pid 900001)" in out


def test_legacy_ids_from_the_command_line(world: World) -> None:
    """DI4: --resume <uuid> names the id unless --fork-session made a new one."""
    target = "0e5f7d5c-0000-4000-8000-00000000abcd"
    world.session(
        900_001,
        record=False,
        session_id=target,
        argv=["claude", FLAG, "--resume", target],
        ai_title="by-uuid",
        exe="/x/versions/2.0.0",
    )
    forked = sid(900_002)
    world.session(
        900_002,
        tab=2,
        record=False,
        session_id=forked,
        cwd="/tmp/cm-test/forked",
        argv=["claude", "--resume", target, "--fork-session"],
        ai_title="the-fork",
        exe="/x/versions/2.0.0",
    )

    out = world.run("capture").stdout
    sessions = {s["title"]: s for s in world.latest()["groups"][0]["sessions"]}

    assert sessions["by-uuid"]["session_id"] == target
    assert sessions["by-uuid"]["guessed"] is True  # no record: /clear could have changed it
    assert sessions["by-uuid"]["flags"] == [FLAG]
    assert sessions["the-fork"]["session_id"] == forked  # not the parent conversation
    assert "? 2 guessed ids" in out


def test_newer_claude_without_a_record_is_not_a_session(world: World) -> None:
    world.session(900_001, record=False, exe="/x/versions/2.1.300")
    out = world.run("show").stdout
    assert "without an identifiable session (pid 900001)" in out


def test_hand_edited_capture_is_what_show_displays(world: World) -> None:
    three_sessions(world)
    world.run("capture", "--name", "window-1=dev")

    data = world.latest()
    data["groups"][0]["name"] = "edited"
    data["groups"][0]["sessions"] = data["groups"][0]["sessions"][:1]  # drop beta
    data["groups"][1]["sessions"][0]["title"] = "gamma-edited"
    data["groups"][1]["sessions"][0]["cwd"] = "~/moved"  # DI8: ~ is expanded
    world.latest_path.write_text(json.dumps(data))

    out = world.run("show").stdout

    assert "▾ edited · iTerm window 1 · 1 tab" in out
    assert "gamma-edited" in out
    assert re.search(r"▾ not in capture[^\n]*\n[^\n]*beta", out)
    shown = json.loads(world.run("show", "--json", "--no-live").stdout)
    assert shown["capture"]["groups"][1]["sessions"][0]["cwd"] == f"{world.root}/home/moved"


def test_rename_persists_and_later_captures_keep_the_names(world: World) -> None:
    three_sessions(world)
    world.run("capture")

    world.run("rename", "window-1", "dev")
    world.run("rename", "window-2", "ops")
    assert "▾ dev" in world.run("show").stdout

    out = world.run("capture").stdout  # names carry over by shared sessions
    assert "▾ dev" in out
    assert "▾ ops" in out

    missing = world.run("rename", "nope", "x", check=False)
    assert missing.returncode == 2
    assert "no group 'nope'" in missing.stderr
    clash = world.run("rename", "dev", "ops", check=False)
    assert clash.returncode == 2
    assert "already exists" in clash.stderr


def test_names_follow_what_show_displays(world: World) -> None:
    """DI6: --name keys can be the shown name; default window-N names are not carried."""
    three_sessions(world)
    world.run("capture", "--name", "window-2=ops")
    state = world.state()  # window order changes: ops is now the front window
    for tab in state["tabs"]:
        tab["window_index"] = 1 if tab["window_id"] == "200" else 2
    world.save(state)

    out = world.run("capture", "--name", "ops=backend", "--name", "window-2=frontend").stdout

    assert "▾ backend · iTerm window 1" in out
    assert "▾ frontend · iTerm window 2" in out


def test_unknown_name_key_warns(world: World) -> None:
    world.session(900_001, title="alpha")
    result = world.run("capture", "--name", "window-9=nothing")
    assert "--name window-9=…: no group or window called 'window-9'" in result.stderr
    bad = world.run("capture", "--name", "no-equals-sign", check=False)
    assert bad.returncode == 2


def test_capture_one_window_merges_into_the_last_capture(world: World) -> None:
    """CL2: --window takes the number views show, and keeps the other groups."""
    three_sessions(world)
    world.run("capture", "--name", "window-1=dev", "--name", "window-2=ops")
    world.session(900_004, window=2, tab=2, title="delta")

    out = world.run("capture", "--window", "2").stdout

    assert "▾ dev" in out
    assert "▾ ops · iTerm window 2 · 2 tabs" in out
    assert [g["name"] for g in world.latest()["groups"]] == ["dev", "ops"]

    bad = world.run("capture", "--window", "7", check=False)
    assert bad.returncode == 2
    assert "no iTerm2 window '7'" in bad.stderr


def test_unreadable_iterm_layout_never_replaces_the_capture(world: World) -> None:
    """DI5: if iTerm2 can't be read, capture refuses; show still works, with a warning."""
    three_sessions(world)
    world.run("capture", "--name", "window-1=dev")
    before = world.latest_path.read_text()
    world.set(layout_error="Not authorized to send Apple events to iTerm2. (-1743)")

    result = world.run("capture", check=False)
    assert result.returncode == 2
    assert "cannot read the iTerm2 windows and tabs" in result.stderr
    assert world.latest_path.read_text() == before

    shown = world.run("show").stdout
    assert "! iTerm2 tabs unknown: Not authorized" in shown
    assert world.run("relaunch", check=False).returncode == 2
    assert world.actions("signal") == []


def test_show_json_and_history(world: World) -> None:
    a, _, _ = three_sessions(world)
    world.run("capture")
    world.run("capture")

    data = json.loads(world.run("show", "--json").stdout)
    assert data["installed_version"] == INSTALLED
    assert data["running"][a]["pid"] == 900_001
    assert len(data["capture"]["groups"]) == 2

    listed = world.run("show", "--list").stdout.strip().splitlines()
    assert len(listed) == 2
    assert all("manual" in line and "3 sessions" in line for line in listed)
    older = listed[0].split()[0]
    assert "▾ window-1" in world.run("show", "--file", older, "--no-live").stdout

    # CL10: one broken capture file doesn't hide the others
    world.home.joinpath("captures", older.rsplit("/", 1)[-1]).write_text("{oops")
    listed = world.run("show", "--list").stdout.strip().splitlines()
    assert "unreadable" in listed[0]
    assert "3 sessions" in listed[1]


def test_show_without_capture_lists_running_sessions(world: World) -> None:
    world.session(900_001, title="alpha")
    world.session(900_002, tab=2, record=False, argv=["claude", FLAG, "-c"], ai_title="old")
    out = world.run("show").stdout
    assert "no capture yet" in out
    assert "▾ not in capture" in out
    assert "alpha" in out
    # CL14: no "✓ 0 running"; guessed ids outside the capture still count
    assert out.splitlines()[-1] == "2 running, none captured · ? 1 guessed id"
    assert world.run("show", "--no-live").stdout.splitlines()[-1] == "nothing captured yet"


def test_broken_capture_file_is_reported_and_capture_recovers(world: World) -> None:
    world.session(900_001, title="alpha")
    world.run("capture")
    world.latest_path.write_text("{not json")
    result = world.run("show", check=False)
    assert result.returncode == 2
    assert "latest.json" in result.stderr

    world.latest_path.write_text(json.dumps({"groups": [{"sessions": [{"cwd": "/x"}]}]}))
    result = world.run("show", check=False)
    assert result.returncode == 2
    assert "invalid capture" in result.stderr

    bad_type = {"groups": [{"sessions": [{"session_id": "x", "title": 2024}]}]}
    world.latest_path.write_text(json.dumps(bad_type))
    result = world.run("show", check=False)
    assert result.returncode == 2
    assert "title must be str" in result.stderr

    # CL10: capture starts over (with a warning) instead of refusing
    recovered = world.run("capture")
    assert "ignoring the last capture" in recovered.stderr
    assert "✓ 1 running" in recovered.stdout


def test_non_interactive_claude_processes_are_ignored(world: World) -> None:
    world.session(900_001, title="alpha")
    world.session(900_002, tab=2, record=False, transcript=False, argv=["claude", "mcp", "serve"])
    world.session(900_003, tab=3, record=False, transcript=False, argv=["claude", "-p", "hi"])
    # DI2: a subcommand behind alias flags is not a session either
    world.session(900_004, tab=4, record=False, transcript=False, argv=["claude", FLAG, "agents"])
    world.session(900_005, tab=5, record=False, argv=["claude", FLAG, "attach", "3f2a9c1e"])
    out = world.run("capture").stdout
    assert "✓ 1 running" in out
    assert "identifiable" not in out


def test_force_color_keeps_colors_when_piped(world: World) -> None:
    """CL8: FORCE_COLOR colors piped output; --plain wins."""
    world.session(900_001, title="alpha")
    world.run("capture")
    env = {k: v for k, v in world.env.items() if k != "NO_COLOR"}
    colored = world.run_env({**env, "FORCE_COLOR": "1"}, "show").stdout
    assert "\033[32m✓\033[0m" in colored
    plain = world.run_env({**env, "FORCE_COLOR": "1"}, "show", "--plain").stdout
    assert "\033[" not in plain
