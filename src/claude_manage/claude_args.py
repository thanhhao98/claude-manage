"""Claude Code's command line, as its own argv classifier sees it.

The option tables mirror the sets Claude Code (2.1.296) uses to scan its argv
(commander rules: an option that requires a value takes the next token even when it
starts with ``-``; a variadic option then keeps taking tokens until the next option;
an optional-value option takes the next token only when it is not an option).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

# Options that require a value (variadic ones included).
REQUIRED = frozenset(
    {
        "--add-dir", "--advisor", "--agent", "--agent-color", "--agent-id", "--agent-name",
        "--agent-type", "--agents", "--allowed-tools", "--allowedTools",
        "--append-subagent-system-prompt", "--append-subagent-system-prompt-file",
        "--append-system-prompt", "--append-system-prompt-file", "--attach-serve",
        "--autocompact", "--betas", "--channels", "--client-data-url", "--correlation-id",
        "--dangerously-load-development-channels", "--debug-file", "--deep-link-cwd-b64",
        "--deep-link-last-fetch", "--deep-link-repo", "--disallowed-tools", "--disallowedTools",
        "--effort", "--environment", "--exec", "--fallback-model", "--file",
        "--forward-home-settings", "--handle-uri", "--inherit-permission-mode", "--input-format",
        "--json-schema", "--managed-settings", "--max-budget-usd", "--max-thinking-tokens",
        "--max-turns", "--mcp-config", "--messaging-socket-path", "--model", "--name",
        "--on-branch", "--output-format", "--parent-session-id", "--permission-mode",
        "--permission-prompt-tool", "--permission-prompts", "--plan-mode-instructions",
        "--plugin-dir", "--plugin-dir-no-mcp", "--plugin-url", "--pool", "--prefill",
        "--prefill-b64", "--proactivity", "--project-config-root", "--ref",
        "--remote-control-session-name-prefix", "--resume-drops-turn", "--resume-session-at",
        "--rewind-files", "--routine", "--sdk-url", "--session-id", "--setting-sources",
        "--settings", "--system-prompt", "--system-prompt-file", "--system-prompt-snapshot",
        "--task-budget", "--team-name", "--teammate-mode", "--thinking", "--thinking-display",
        "--tools", "--watch-artifact", "--watch-artifact-no-autoreact", "--workload", "-m", "-n",
    }
)  # fmt: skip
VARIADIC = frozenset(
    {
        "--add-dir", "--allowed-tools", "--allowedTools", "--betas", "--channels",
        "--dangerously-load-development-channels", "--disallowed-tools", "--disallowedTools",
        "--file", "--mcp-config", "--tools",
    }
)  # fmt: skip
OPTIONAL = frozenset(
    {
        "--cloud", "--debug", "--from-pr", "--project", "--prompt-suggestions", "--rc",
        "--remote", "--remote-control", "--resume", "--teleport", "--worktree", "-d", "-r", "-w",
    }
)  # fmt: skip
# Switches (no value), from Claude Code's own list plus `claude --help`.
SWITCHES = frozenset(
    {
        "-d2e", "--debug-to-stderr", "--verbose", "-p", "--print", "--bare", "--safe-mode",
        "--init", "--init-only", "--maintenance", "--include-hook-events",
        "--include-partial-messages", "--forward-subagent-text", "--session-mirror",
        "--await-claim", "--await-initialize", "--dangerously-skip-permissions",
        "--allow-dangerously-skip-permissions", "--replay-user-messages", "--enable-auth-status",
        "--restricted", "--exclude-dynamic-system-prompt-sections", "-c", "--continue",
        "--fork-session", "--deep-link-origin", "--no-session-persistence", "--reply-on-resume",
        "--ide", "--desktop", "--strict-mcp-config", "--disable-slash-commands", "--chrome",
        "--no-chrome", "--tmux", "--enable-auto-mode", "--bg", "--background", "--brief",
        "--ax-screen-reader", "--plan-mode-required", "--hard-fail", "-h", "--help", "-v",
        "-V", "--version",
    }
)  # fmt: skip
# Root commands: `claude [options] <command>` is not an interactive session.
SUBCOMMANDS = frozenset(
    {
        "agents", "attach", "auth", "auto-mode", "config", "doctor", "gateway", "import",
        "import-conversations", "install", "kill", "logs", "mcp", "migrate-installer", "plugin",
        "plugins", "purge", "rc", "remote-control", "respawn", "rm", "sandbox", "setup-token",
        "stop", "ultrareview", "update", "upgrade",
    }
)  # fmt: skip
# Never replayed on relaunch: they select or create the conversation, its turn, its
# worktree or a cloud session, or are one-shot inputs. Relaunch adds `--resume <id>`.
NOT_REPLAYED = frozenset(
    {
        "-r", "--resume", "-c", "--continue", "--fork-session", "--session-id", "--from-pr",
        "--teleport", "--cloud", "--remote", "--environment", "--pool", "--ref", "--on-branch",
        "--forward-home-settings", "--attach-serve", "--desktop", "-w", "--worktree", "--tmux",
        "--bg", "--background", "--resume-session-at", "--resume-drops-turn", "--rewind-files",
        "--prefill", "--prefill-b64", "--deep-link-cwd-b64", "--deep-link-last-fetch",
        "--deep-link-repo", "--deep-link-origin", "--handle-uri", "--exec", "--routine",
        "--sdk-url", "--messaging-socket-path", "--client-data-url", "--correlation-id",
        "--reply-on-resume", "--init", "--init-only", "--maintenance",
    }
)  # fmt: skip
NOT_INTERACTIVE = frozenset({"-p", "--print", "--bg", "--background", "--init-only", "--sdk-url"})


def is_option(token: str) -> bool:
    return len(token) > 1 and token.startswith("-")


def option_end(args: Sequence[str], i: int) -> int:
    """Index of the last token that belongs to the option at ``args[i]`` (Claude's rules)."""
    name = args[i]
    if name in OPTIONAL:
        return i + 1 if i + 1 < len(args) and not is_option(args[i + 1]) else i
    if name not in REQUIRED:
        return i
    end = i + 1  # the value, even when it looks like an option
    if name in VARIADIC:
        while end + 1 < len(args) and not is_option(args[end + 1]):
            end += 1
    return min(end, len(args) - 1)


def first_operand(args: Sequence[str]) -> int | None:
    """Index of the first positional argument (a command or the prompt), if any."""
    i = 0
    while i < len(args):
        if args[i] == "--":
            return i + 1 if i + 1 < len(args) else None
        if not is_option(args[i]):
            return i
        i = option_end(args, i) + 1
    return None


@dataclass
class Replay:
    """How a session's options are replayed when it is restarted."""

    flags: list[str] = field(default_factory=list)
    # Not replayed and worth telling: a prompt, or an unknown option. (Options that pick
    # the conversation are dropped silently: relaunch picks it with --resume.)
    dropped: list[str] = field(default_factory=list)


def replay(argv: Sequence[str]) -> Replay:
    """Split ``argv`` (argv[0] excluded) into options to replay and the rest.

    Positional arguments (an initial prompt must not be sent again) and
    conversation-selecting options are dropped. An option Claude Code did not
    know about when these tables were written is kept only if nothing could be
    its value; with a following positional token it is dropped together with it,
    so a value-taking option can never swallow the next token on relaunch.
    """
    out = Replay()
    args = list(argv[1:])
    i = 0
    while i < len(args):
        token = args[i]
        if token == "--":
            out.dropped += args[i + 1 :]
            break
        if not is_option(token):
            out.dropped.append(token)
            i += 1
            continue
        name, has_value, _ = token.partition("=")
        if has_value:
            group, end = [token], i
        elif name in REQUIRED or name in OPTIONAL or name in SWITCHES:
            end = option_end(args, i)
            group = args[i : end + 1]
        elif i + 1 < len(args) and not is_option(args[i + 1]):
            end = i + 1  # unknown option + a token: its value or a prompt, can't tell
            out.dropped += args[i : end + 1]
            i = end + 1
            continue
        else:
            group, end = [token], i
        if name not in NOT_REPLAYED:
            out.flags.extend(group)
        i = end + 1
    return out


def value_of(argv: Sequence[str], *names: str) -> str | None:
    """The value given to option ``names`` (``--opt value`` or ``--opt=value``), if any."""
    args = list(argv[1:])
    i = 0
    while i < len(args):
        token = args[i]
        if token == "--":
            return None
        name, has_value, value = token.partition("=")
        if name in names:
            if has_value:
                return value or None
            end = option_end(args, i)
            return args[i + 1] if end > i else None
        i = (option_end(args, i) if is_option(token) else i) + 1
    return None


def has_option(argv: Sequence[str], *names: str) -> bool:
    args = list(argv[1:])
    i = 0
    while i < len(args):
        token = args[i]
        if token == "--":
            return False
        if is_option(token) and token.partition("=")[0] in names:
            return True
        i = (option_end(args, i) if is_option(token) else i) + 1
    return False


def is_interactive(argv: Sequence[str]) -> bool:
    """True unless ``argv`` runs a subcommand, print mode or a background session."""
    if has_option(argv, *NOT_INTERACTIVE):
        return False
    args = list(argv[1:])
    first = first_operand(args)
    if first is None or (first > 0 and args[first - 1] == "--"):
        return True  # no operand, or a prompt after `--`
    return args[first] not in SUBCOMMANDS
