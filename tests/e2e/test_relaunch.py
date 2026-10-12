"""relaunch end to end: restart running sessions in place."""

from __future__ import annotations

import json
import re
import time

from tests.support import CWD, FLAG, INSTALLED, World


def command(session_id: str, flags: str = FLAG) -> str:
    return f"cd {CWD} && command claude --resume {session_id} {flags}".rstrip()


def signals(world: World) -> list[tuple[int, str]]:
    return [(a["pid"], a["sig"]) for a in world.actions("signal")]


def test_dry_run_changes_nothing(world: World) -> None:
    world.session(900_001, title="alpha")
    out = world.run("relaunch", "--dry-run").stdout
    assert "→ restart" in out
    assert "→ 1 to restart" in out
    assert world.actions() == []
    assert not world.latest_path.exists()


def test_restarts_idle_sessions_in_their_own_tab(world: World) -> None:
    a = world.session(900_001, tab=1, title="alpha", version="2.0.0")
    world.session(900_002, tab=2, title="beta", status="busy")
    world.run("capture", "--name", "window-1=dev")

    out = world.run("relaunch").stdout

    assert signals(world) == [(900_001, "SIGTERM")]
    assert world.actions("write_text") == [
        {"op": "write_text", "tty": world.tty(900_001), "text": command(a)}
    ]
    assert re.search(r"alpha\s.*✓ restarted pid 900001 → 910000 · v2\.0\.0 → v2\.1\.0", out)
    assert re.search(r"beta\s.*– busy \(use --wait or --include-busy\)", out)
    assert "relaunch finished: 1 restarted, 1 skipped" in out

    latest = world.latest()
    assert latest["reason"] == "post-relaunch"
    assert latest["groups"][0]["name"] == "dev"  # group names survive the relaunch
    alpha = next(s for s in latest["groups"][0]["sessions"] if s["title"] == "alpha")
    assert (alpha["pid"], alpha["version"]) == (910_000, INSTALLED)
    assert "pre-relaunch" in [c["reason"] for c in world.captures()]
    assert world.labels()[world.tty(900_001)] == "dev"
    assert "relaunch finished" in world.wait_for_log("relaunch finished")


def test_include_busy_restarts_busy_sessions_too(world: World) -> None:
    world.session(900_001, title="alpha")
    world.session(900_002, tab=2, title="beta", status="busy")
    out = world.run("relaunch", "--include-busy").stdout
    assert signals(world) == [(900_001, "SIGTERM"), (900_002, "SIGTERM")]
    assert "2 restarted" in out


def test_wait_restarts_a_busy_session_once_it_is_idle(world: World) -> None:
    world.session(900_001, title="beta", status="busy")
    proc = world.popen("relaunch", "--wait", "10")
    time.sleep(1.5)
    assert signals(world) == []  # still waiting
    world.set_status(900_001, "idle")
    out, err = proc.communicate(timeout=30)
    assert proc.returncode == 0, out + err
    assert signals(world) == [(900_001, "SIGTERM")]
    assert "1 restarted" in out


def test_wait_shares_one_deadline_and_idle_sessions_go_first(world: World) -> None:
    """CL6: --wait is a total, and an idle session is not stuck behind busy ones."""
    world.session(900_001, tab=1, title="busy-1", status="busy")
    world.session(900_002, tab=2, title="busy-2", status="busy")
    world.session(900_003, tab=3, title="idle")
    started = time.monotonic()
    out = world.run("relaunch", "--wait", "1.5").stdout
    assert time.monotonic() - started < 10
    assert signals(world) == [(900_003, "SIGTERM")]
    assert "– busy-1: still busy after 1.5s" in out
    assert "– busy-2: still busy after 1.5s" in out


def test_wait_skips_a_session_that_switched_conversation(world: World) -> None:
    """RE6: /clear while waiting changes the session id: leave it alone."""
    world.session(900_001, title="beta", status="busy")
    proc = world.popen("relaunch", "--wait", "10")
    time.sleep(1.0)
    record = world.sessions / "900001.json"
    meta = json.loads(record.read_text())
    meta.update(sessionId="11111111-0000-4000-8000-000000000099", status="idle")
    record.write_text(json.dumps(meta))
    out, _ = proc.communicate(timeout=30)
    assert signals(world) == []
    assert "now runs another conversation" in out


def test_filters_select_sessions(world: World) -> None:
    world.session(900_001, window=1, tab=1, title="old", version="2.0.0")
    world.session(900_002, window=1, tab=2, title="new", version=INSTALLED)
    c = world.session(900_003, window=2, tab=1, title="other", version="2.0.0")
    world.run("capture", "--name", "window-2=ops")

    out = world.run("relaunch", "--outdated", "--dry-run").stdout
    assert "→ 2 to restart" in out
    assert "1 not selected" in out

    out = world.run("relaunch", "--group", "ops", "--dry-run").stdout
    assert "→ 1 to restart · 2 not selected" in out

    out = world.run("relaunch", "--session", c[:8], "--session", "new", "--dry-run").stdout
    assert "→ 2 to restart · 1 not selected" in out

    out = world.run("relaunch", "--window", "2", "--dry-run").stdout
    assert "→ 1 to restart · 2 not selected" in out

    bad = world.run("relaunch", "--group", "nope", check=False)
    assert bad.returncode == 2
    assert world.actions("signal") == []


def test_a_process_ignoring_sigterm_needs_kill(world: World) -> None:
    world.session(900_001, title="stubborn", ignore_term=True)

    failed = world.run("relaunch", check=False)
    assert failed.returncode == 1
    assert "still running 1s after SIGTERM (retry with --kill)" in failed.stdout
    assert world.actions("write_text") == []

    out = world.run("relaunch", "--kill").stdout
    assert signals(world)[-2:] == [(900_001, "SIGTERM"), (900_001, "SIGKILL")]
    assert "1 restarted" in out


def test_tab_that_closed_with_claude_gets_a_new_tab(world: World) -> None:
    a = world.session(900_001, title="exec-tab", closes_on_exit=True)
    world.session(900_002, tab=2, title="neighbour", status="busy")  # keeps the window open
    out = world.run("relaunch").stdout
    assert world.actions("new_tab") == [{"op": "new_tab", "window_id": "100", "text": command(a)}]
    assert re.search(r"exec-tab\s.*✓ restarted pid 900001 → \d+ · new tab", out)


def test_last_tab_closing_takes_the_window_so_a_new_one_opens(world: World) -> None:
    a = world.session(900_001, title="exec-tab", closes_on_exit=True)
    out = world.run("relaunch").stdout
    assert world.actions("new_window")[0]["texts"] == [command(a)]
    assert re.search(r"✓ restarted pid 900001 → \d+ · new window", out)


def test_skips_outside_iterm_guessed_ids_unknown_status_and_wrappers(world: World) -> None:
    world.session(900_001, title="tmux", in_iterm=False)
    world.session(900_002, tab=2, record=False, argv=["claude", FLAG, "-c"], ai_title="guess")
    world.session(900_003, tab=3, title="wrapped", shell_argv=["bash", "keep-alive.sh"])
    out = world.run("relaunch", "--dry-run").stdout
    assert "skip: not in an iTerm2 tab" in out
    assert "skip: id is a guess (use --include-guessed)" in out
    assert "skip: not started from an interactive shell (wrapper script?)" in out

    # CL4/RE8: an older Claude Code's state is unknown, so it also needs --include-busy
    out = world.run("relaunch", "--include-guessed", "--dry-run").stdout
    assert "status unknown: older Claude Code may be busy (use --include-busy)" in out
    out = world.run("relaunch", "--include-guessed", "--include-busy").stdout
    assert signals(world) == [(900_002, "SIGTERM")]
    assert "1 restarted" in out


def test_non_resumable_session_restarts_fresh_in_the_same_tab(world: World) -> None:
    world.session(900_001, title="empty", transcript=False)
    out = world.run("relaunch").stdout
    assert world.actions("write_text")[0]["text"] == f"cd {CWD} && command claude {FLAG}"
    assert "✓ started" in out


def test_stops_at_the_first_session_that_does_not_come_back(world: World) -> None:
    """RE3/RE4: one broken restart must not take every tab down; what it stopped stays
    in the capture so `show` lists it and `resume` reopens it."""
    a = world.session(900_001, tab=1, title="alpha", inert=True)
    world.session(900_002, tab=2, title="beta")

    result = world.run("relaunch", check=False)

    assert result.returncode == 1
    assert signals(world) == [(900_001, "SIGTERM")]  # beta was never touched
    assert re.search(r"alpha\s.*! started in same tab, not seen running after 3s", result.stdout)
    assert re.search(r"beta\s.*– not attempted: alpha did not come back", result.stdout)
    assert "1 stopped and not back: `claude-manage resume` reopens them" in result.stdout

    shown = world.run("show").stdout
    assert re.search(r"alpha\s.*✗ not running", shown)
    world.run("resume", "--no-verify")  # beta still runs in window 100: alpha goes there
    assert [x["text"] for x in world.actions("new_tab")] == [command(a)]


def test_this_session_restarts_last_from_a_background_worker(world: World) -> None:
    world.session(900_001, tab=1, title="alpha")
    world.session(900_002, tab=2, title="me", status="busy")  # busy: it runs this command
    world.run_inside(900_002)

    out = world.run("relaunch").stdout
    assert "this session: restarted last" in out
    assert "continues in the background" in out

    log = world.wait_for_log("relaunch finished")
    assert signals(world) == [(900_001, "SIGTERM"), (900_002, "SIGTERM")]
    assert "2 restarted" in log
    assert re.search(r"worker pid \d+", log)
    assert world.latest()["reason"] == "post-relaunch"
    assert "relaunch finished" in world.run("log").stdout
    assert world.run("log", "--path").stdout.strip().endswith(".log")
    assert not list((world.home / "runs").glob("*.json"))  # CL13: run file cleaned up


def test_this_session_is_not_restarted_when_another_failed(world: World) -> None:
    world.session(900_001, tab=1, title="alpha", inert=True)
    world.session(900_002, tab=2, title="me", status="busy")
    world.run_inside(900_002)

    world.run("relaunch")
    log = world.wait_for_log("relaunch finished")

    assert signals(world) == [(900_001, "SIGTERM")]
    assert "not attempted: alpha did not come back" in log
    assert world.run("log", check=False).returncode == 1
    assert not list((world.home / "runs").glob("*.json"))


def test_a_guessed_id_is_never_restarted_as_this_session(world: World) -> None:
    """CL3/RE1: no record (older Claude Code) means its id is a guess, self or not."""
    world.session(900_001, record=False, argv=["claude", FLAG, "-c"], ai_title="old-me")
    world.run_inside(900_001)
    out = world.run("relaunch", "--dry-run").stdout
    assert "skip: id is a guess (use --include-guessed)" in out


def test_foreground_and_no_self_leave_this_session_alone(world: World) -> None:
    world.session(900_001, tab=1, title="alpha")
    world.session(900_002, tab=2, title="me", status="busy")
    world.run_inside(900_002)

    for flag in ("--foreground", "--no-self"):
        out = world.run("relaunch", flag, "--dry-run").stdout
        assert "skip: this session" in out

    world.run("relaunch", "--no-self")
    assert [pid for pid, _ in signals(world)] == [900_001]


def test_options_are_replayed_safely(world: World) -> None:
    """DI1/RE2: --resume comes first, values stay with their options, prompts and
    unknown option/value pairs are not replayed (and the plan says so)."""
    argv = [
        "claude", FLAG, "--channels", "plugin:a", "plugin:b", "--advisor", "opus",
        "--model", "sonnet", "fix the bug", "--new-option", "value",
    ]  # fmt: skip
    a = world.session(900_001, title="flags", argv=argv)

    plan = world.run("relaunch", "--dry-run").stdout
    assert "(not replayed: fix the bug --new-option value)" in plan

    world.run("relaunch")
    text = world.actions("write_text")[0]["text"]
    expected = f"{FLAG} --channels plugin:a plugin:b --advisor opus --model sonnet"
    assert text == command(a, expected)


def test_relaunch_needs_a_startable_claude(world: World) -> None:
    """CL5/RE3: never stop sessions when the new claude can't start."""
    world.session(900_001, title="alpha")
    env = {**world.env, "CLAUDE_MANAGE_CLAUDE_BIN": "/nonexistent/claude"}
    result = world.run_env(env, "relaunch", check=False)
    assert result.returncode == 2
    assert "is not an executable on PATH" in result.stderr
    assert world.actions() == []
    broken = world.root / "bin" / "broken-claude"
    broken.write_text("#!/bin/sh\necho 'dyld: Library not loaded' >&2\nexit 134\n")
    broken.chmod(0o755)
    env["CLAUDE_MANAGE_CLAUDE_BIN"] = str(broken)
    result = world.run_env(env, "relaunch", check=False)
    assert result.returncode == 2
    assert "Library not loaded" in result.stderr
    assert world.actions() == []


def test_log_follow_stops_when_the_worker_died(world: World) -> None:
    """CL9/RE5: `log -f` must not wait forever for a worker that is gone."""
    logs = world.home / "logs"
    logs.mkdir(parents=True)
    dead = 2**22 + 4321
    (logs / "relaunch-2026-01-01T00-00-00.log").write_text(
        f"[00:00:00] worker pid {dead}\n[00:00:01] stopping alpha (pid 1)\n"
    )
    started = time.monotonic()
    result = world.run("log", "-f", check=False, timeout=30)
    assert result.returncode == 1
    assert "exited without finishing" in result.stderr
    assert time.monotonic() - started < 15


def test_log_without_relaunch(world: World) -> None:
    result = world.run("log", check=False)
    assert result.returncode == 1
    assert "no relaunch logs yet" in result.stderr
