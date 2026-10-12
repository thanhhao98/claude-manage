"""resume end to end: reopen captured sessions after a reboot."""

from __future__ import annotations

import json

from tests.support import CWD, FLAG, World


def command(session_id: str) -> str:
    return f"cd {CWD} && command claude --resume {session_id} {FLAG}"


def test_resume_after_reboot_opens_one_labelled_window_per_group(world: World) -> None:
    a = world.session(900_001, window=1, tab=1, title="alpha")
    b = world.session(900_002, window=1, tab=2, title="beta")
    c = world.session(900_003, window=2, tab=1, title="gamma")
    world.run("capture", "--name", "window-1=dev", "--name", "window-2=ops")
    world.reboot()

    plan = world.run("resume", "--dry-run").stdout
    assert "▾ dev · opens in a new window" in plan
    assert "→ 3 to open" in plan
    assert world.actions() == []

    out = world.run("resume").stdout
    assert "✓ 3 back" in out  # verified: the sessions are running again
    windows = world.actions("new_window")
    assert [w["texts"] for w in windows] == [[command(a), command(b)], [command(c)]]
    # the recreated groups are visible: each new tab carries its group's badge
    assert sorted(world.labels().values()) == ["dev", "dev", "ops"]

    shown = world.run("show").stdout
    assert "✓ 3 running" in shown
    again = world.run("resume").stdout
    assert "✓ 3 already running" in again
    assert len(world.actions("new_window")) == 2


def test_resume_one_group_and_unknown_group(world: World) -> None:
    world.session(900_001, window=1, title="alpha")
    c = world.session(900_003, window=2, title="gamma")
    world.run("capture", "--name", "window-2=ops")
    world.reboot()

    world.run("resume", "--group", "ops")
    assert [w["texts"] for w in world.actions("new_window")] == [[command(c)]]

    bad = world.run("resume", "--group", "nope", check=False)
    assert bad.returncode == 2
    assert "no group nope" in bad.stderr


def test_resume_reuses_the_window_still_holding_the_group(world: World) -> None:
    world.session(900_001, window=1, tab=1, title="alpha")
    b = world.session(900_002, window=1, tab=2, title="beta")
    world.run("capture")
    world.stop(900_002)  # beta closed; alpha still runs in window 100

    world.run("resume")

    assert world.actions("new_window") == []
    assert world.actions("new_tab") == [{"op": "new_tab", "window_id": "100", "text": command(b)}]


def test_a_failing_tab_never_opens_a_session_twice(world: World) -> None:
    """CL11: the window takes one tab, then refuses; only the rest go to a new window."""
    world.session(900_001, window=1, tab=1, title="alpha")
    b = world.session(900_002, window=1, tab=2, title="beta")
    c = world.session(900_003, window=1, tab=3, title="gamma")
    world.run("capture")
    world.stop(900_002)
    world.stop(900_003)
    world.set(new_tab_budget=1)

    out = world.run("resume").stdout

    opened = [a["text"] for a in world.actions("new_tab")[:1]]
    opened += world.actions("new_window")[0]["texts"]
    assert opened == [command(b), command(c)]
    assert "✓ 2 back" in out


def test_resume_reports_terminal_failures(world: World) -> None:
    """CL1: if the window can't be opened, say so and exit 1."""
    world.session(900_001, title="alpha")
    world.run("capture")
    world.reboot()
    world.set(new_window_error="Not authorized to send Apple events to iTerm2.")

    result = world.run("resume", check=False)

    assert result.returncode == 1
    assert "✗ not opened: Not authorized" in result.stdout
    assert "✗ 1 failed" in result.stdout


def test_resume_reports_sessions_that_did_not_come_back(world: World) -> None:
    world.session(900_001, title="alpha")
    world.run("capture")
    world.reboot()
    world.set(new_tabs_inert=True)  # the new tabs' shells never start claude

    result = world.run("resume", check=False)
    assert result.returncode == 1
    assert "! opened, not seen running after 3s" in result.stdout

    unverified = world.run("resume", "--no-verify")
    assert "→ 1 opened" in unverified.stdout


def test_guessed_and_duplicate_ids_are_not_reopened(world: World) -> None:
    """RE7: identical guesses must not reopen one conversation in several tabs."""
    world.session(900_001, title="alpha")
    guess = world.session(900_002, tab=2, record=False, argv=["claude", FLAG, "-c"])
    world.run("capture")
    data = world.latest()
    duplicate = dict(data["groups"][0]["sessions"][0])
    duplicate["tab"] = 9
    data["groups"][0]["sessions"].append(duplicate)
    world.latest_path.write_text(json.dumps(data))
    world.reboot()

    out = world.run("resume").stdout

    assert len(world.actions("new_window")[0]["texts"]) == 1
    assert "– id is a guess (use --include-guessed)" in out
    assert "– duplicate: already in this resume" in out

    world.reboot()
    world.run("resume", "--include-guessed")
    assert any(guess in t for t in world.actions("new_window")[-1]["texts"])


def test_session_without_transcript_starts_fresh(world: World) -> None:
    world.session(900_001, title="empty", transcript=False)
    world.run("capture")
    world.reboot()

    out = world.run("resume").stdout

    assert "no transcript: fresh session" in out
    assert world.actions("new_window")[0]["texts"] == [f"cd {CWD} && command claude {FLAG}"]


def test_resume_force_reopens_running_sessions(world: World) -> None:
    a = world.session(900_001, title="alpha")
    world.run("capture")
    world.run("resume", "--force", "--no-verify")
    assert world.actions("new_tab") == [{"op": "new_tab", "window_id": "100", "text": command(a)}]


def test_resume_needs_a_startable_claude(world: World) -> None:
    """CL5: an alias-only claude would leave every tab at 'command not found'."""
    world.session(900_001, title="alpha")
    world.run("capture")
    world.reboot()
    env = {**world.env, "CLAUDE_MANAGE_CLAUDE_BIN": "/nonexistent/claude"}
    result = world.run_env(env, "resume", check=False)
    assert result.returncode == 2
    assert "is not an executable on PATH" in result.stderr
    assert world.actions() == []
