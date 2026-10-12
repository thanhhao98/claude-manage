from __future__ import annotations

import pytest

from claude_manage.claude_args import first_operand, has_option, is_interactive, replay, value_of

F = "--dangerously-skip-permissions"


@pytest.mark.parametrize(
    ("argv", "flags", "dropped"),
    [
        (["claude"], [], []),
        (["claude", F], [F], []),
        (["claude", F, "-r"], [F], []),
        (["claude", F, "-r", "some-title"], [F], []),
        (["claude", "--resume", "abc", F], [F], []),
        (["claude", "--resume=abc", F], [F], []),
        (["claude", "-c", F], [F], []),
        (["claude", "--continue", "--fork-session", F], [F], []),
        (["claude", "--session-id", "x", F], [F], []),
        (["claude", "-w", "feature", F], [F], []),
        (["claude", "--worktree", "--tmux", F], [F], []),
        (["claude", "--model", "opus", F], ["--model", "opus", F], []),
        (["claude", "--model=opus"], ["--model=opus"], []),
        (["claude", "-n", "my task", F], ["-n", "my task", F], []),
        (["claude", "--add-dir", "a", "b", F], ["--add-dir", "a", "b", F], []),
        (["claude", "--debug", "api", F], ["--debug", "api", F], []),
        (["claude", "--debug", F], ["--debug", F], []),
        # options Claude Code knows but `claude --help` hides
        (["claude", F, "--channels", "p:a", "p:b"], [F, "--channels", "p:a", "p:b"], []),
        (["claude", "--advisor", "opus", F], ["--advisor", "opus", F], []),
        (["claude", "--thinking", "enabled", F], ["--thinking", "enabled", F], []),
        # a required value is taken even when it looks like an option
        (["claude", "--append-system-prompt", "-be brief", F],
         ["--append-system-prompt", "-be brief", F], []),
        # an initial prompt must never be sent again
        (["claude", F, "fix the bug"], [F], ["fix the bug"]),
        (["claude", "--model", "opus", "fix the bug"], ["--model", "opus"], ["fix the bug"]),
        (["claude", F, "--", "--not-a-flag"], [F], ["--not-a-flag"]),
        # unknown option: kept alone; with a following token (value or prompt?) both go
        (["claude", "--new-option", F], ["--new-option", F], []),
        (["claude", "--new-option", "value", F], [F], ["--new-option", "value"]),
        (["claude", "--new-option=value", F], ["--new-option=value", F], []),
        # one-shot inputs and cloud sessions are not replayed
        (["claude", "--prefill", "hello", F], [F], []),
        (["claude", "--remote", F], [F], []),
    ],
)  # fmt: skip
def test_replay(argv: list[str], flags: list[str], dropped: list[str]) -> None:
    result = replay(argv)
    assert result.flags == flags
    assert result.dropped == dropped


def test_value_of_and_has_option() -> None:
    argv = ["claude", F, "--model", "opus", "--resume", "abc", "--fork-session"]
    assert value_of(argv, "--resume", "-r") == "abc"
    assert value_of(argv, "--model") == "opus"
    assert value_of(["claude", "-r", F], "--resume", "-r") is None
    assert value_of(["claude", "--resume=x"], "--resume") == "x"
    assert value_of(["claude", "--append-system-prompt", "--resume", "x"], "--resume") is None
    assert has_option(argv, "--fork-session")
    assert not has_option(["claude", "--model", "--fork-session"], "--fork-session")


def test_first_operand() -> None:
    assert first_operand([F, "agents"]) == 1
    assert first_operand(["--model", "opus", "fix it"]) == 2
    assert first_operand(["--add-dir", "a", "b", "--", "prompt"]) == 4
    assert first_operand([F]) is None


@pytest.mark.parametrize(
    ("argv", "interactive"),
    [
        (["claude", F], True),
        (["claude", "write a poem"], True),
        (["claude", F, "-r"], True),
        (["claude", "--model", "opus", "agents please"], True),
        (["claude", "--", "agents"], True),
        (["claude", "-p", "hi"], False),
        (["claude", "--print", "hi"], False),
        (["claude", "--bg"], False),
        (["claude", "mcp", "serve"], False),
        (["claude", "update"], False),
        (["claude", F, "agents"], False),
        (["claude", F, "attach", "3f2a9c1e"], False),
        (["claude", "--model", "opus", "doctor"], False),
        (["claude", "rc"], False),
    ],
)
def test_is_interactive(argv: list[str], interactive: bool) -> None:
    assert is_interactive(argv) is interactive
