# claude-manage

[![ci](https://github.com/thanhhao98/claude-manage/actions/workflows/ci.yml/badge.svg)](https://github.com/thanhhao98/claude-manage/actions/workflows/ci.yml)

Keep a fleet of [Claude Code](https://claude.com/claude-code) sessions running in iTerm2
across reboots and upgrades. Each iTerm2 window is a **group**; each tab is a **session**.

- `capture` saves every running session: window, tab, folder, options, session id.
- `show` draws the last capture and checks it against what is running right now.
- `resume` reopens the sessions that are gone (after a reboot): one window per group.
- `relaunch` restarts running sessions in their own tabs, e.g. so they pick up a new Claude Code version.
- Groups are visible in iTerm2: every tab shows its group's name as a badge, in its group's tab color.

```console
$ claude-manage show
claude-manage · capture 2026-10-12 14:42 · installed v2.1.296

▾ frontend · iTerm window 1 · 3 tabs
  1  ●  checkout-redesign    3f2a9c1e  v2.1.296    ~/code/shop      ✓ pid 4211 idle
  2  ◐  flaky-e2e-tests      9b7d0e44  v2.1.296    ~/code/shop      ✓ pid 4377 busy
  3  ○  storybook-upgrade    c81e2f09  v2.1.289 ↑  ~/code/shop-ui   ✗ not running

▾ backend · iTerm window 2 · 2 tabs
  1  ●  rate-limiter         5d0c7ab2  v2.1.289 ↑  ~/code/api       ✓ pid 5120 idle
  2  ◇  db-migration ?       e4f19a73  v2.1.286 ↑  ~/code/api       ? pid 5188 (id guessed)

✓ 3 running · ? 1 maybe running · ✗ 1 not running · ↑ 3 outdated · ? 1 guessed id
```

`●` idle · `◐` busy (working, running a shell command, or waiting for you) · `◇` state unknown
(older Claude Code) · `○` not running · `↑` older than the installed Claude Code ·
`?` session id inferred, see [how sessions are found](#how-sessions-are-found).

## Install

Requires macOS, iTerm2, Python 3.11+ and [uv](https://docs.astral.sh/uv/).

```sh
uv tool install git+https://github.com/thanhhao98/claude-manage
# or, from a checkout (editable):
git clone https://github.com/thanhhao98/claude-manage && cd claude-manage && make install
```

Both put `claude-manage` in `~/.local/bin`. Shell completion: run
`claude-manage --install-completion` in your shell (it detects zsh, bash or fish), or
`claude-manage --show-completion` to print the script.

The first time it drives iTerm2, macOS asks whether your terminal may control iTerm2
(System Settings › Privacy & Security › Automation). It needs that to read and open tabs.

## Everyday use

Put the sessions that belong together in one iTerm2 window (drag tabs between windows),
then name the windows:

```sh
# Before a reboot
claude-manage capture --name window-1=frontend --name window-2=backend
claude-manage show                      # verify: everything ✓

# After the reboot
claude-manage resume --dry-run          # the plan
claude-manage resume                    # one labelled window per group, then checks they're back

# After Claude Code updated itself
claude-manage relaunch --outdated       # restart sessions on an older version
claude-manage relaunch                  # or every idle session
```

| Command | What it does |
| --- | --- |
| `capture [--name CURRENT=NAME] [--window N] [--no-label] [--json]` | Save running sessions and label their tabs. `CURRENT` is the name a group shows now, `window-N` for "iTerm window N", or a window id. Names carry over between captures. `--window` captures one window and merges it into the last capture. |
| `show [--file F] [--list] [--no-live] [--json] [--plain]` | Draw a capture, verified against the live sessions. Bare `claude-manage` runs `show`. |
| `rename OLD NEW` | Rename a group in the last capture (and relabel its tabs). |
| `label [--group G] [--clear]` | Put the group badges and tab colors on running tabs again, or remove them. |
| `resume [--group G] [--include-guessed] [--force] [--no-verify] [--dry-run]` | Reopen captured sessions that are not running, label them, then wait until they are back. |
| `relaunch [filters] [--dry-run] …` | Restart running sessions in place (see below). |
| `log [-f] [--path]` | Print (or follow) the latest relaunch log; exits 1 if that relaunch failed. |

Every command has `--help`. Output is colored on a terminal; `--plain` or `NO_COLOR` turn
that off, and `FORCE_COLOR` keeps it when piping (e.g. into `less -R`).

## Group labels

A label is the group name as the tab's [badge](https://iterm2.com/documentation-badges.html)
(the large faint text in the top right) plus a tab color picked from the group name, so a
group keeps its color. `capture`, `rename`, `resume` and `relaunch` set them; `label`
re-applies them and `label --clear` removes them. Labels are iTerm2 escape sequences
written to the tab's terminal device: display state only, nothing is typed into the shell
or Claude. Each group also sets the iTerm2 variable `user.claudeManageGroup`, usable in
your own badge or title formats.

## How relaunch works

For each selected session, `relaunch`:

1. sends `SIGTERM` to its `claude` process. Claude Code shuts down cleanly and the tab drops
   back to the shell that started it;
2. waits until that shell owns the terminal again, then types
   `cd <folder> && command claude --resume <session id> <original options>` into **the same
   tab**. `--resume` comes first, so no replayed option can take it as its value;
3. waits until the session is running again (old and new pid and version are reported)
   before it moves on to the next one.

What it will and won't do:

- **One failure stops the run.** If a restarted session does not come back, relaunch does not
  touch the rest. Sessions it stopped stay in the capture (as not running), so `show` lists
  them and `resume` reopens them.
- **It checks `claude` first.** Before stopping anything it runs `claude --version` from the
  `PATH`. Shell aliases and functions don't count: if `claude` only exists as an alias, set
  `claude_bin` to its full path. Since commands are typed through `command claude`, aliases
  that add flags (`alias claude='claude --some-flag'`) don't add them twice.
- **Busy sessions are skipped** (working, running a shell command, or waiting on a prompt):
  interrupting them would lose the turn in progress. `--wait 300` restarts them as they become
  idle, within 300 seconds in total (idle sessions go first). `--include-busy` interrupts them.
  Older Claude Code doesn't report its state, so those sessions count as busy too.
- **Guessed session ids are skipped** (`?` in `show`). Restarting with a wrong id would put
  another conversation in that tab. Check the id (`claude-manage show --json`) and pass
  `--include-guessed` if it's right.
- **Only interactive shells are typed into.** A `claude` started by a wrapper script (say, a
  restart loop) is skipped. Sessions outside iTerm2 (tmux, other terminals) are skipped too.
- **The session you run it from** (when you ask Claude to run `claude-manage relaunch`) goes
  last, from a detached background process, and only if every other restart worked:
  `claude-manage log -f` follows it. `--no-self` leaves it alone.
- A process that ignores `SIGTERM` is reported, not killed; `--kill` escalates to `SIGKILL`.
- Before touching anything it saves a `pre-relaunch` capture.
- Original options are kept, except the initial prompt (`claude "fix the bug"` must not send
  "fix the bug" again), options that pick a conversation, turn, worktree or cloud session
  (`--resume`, `--continue`, `--session-id`, `--worktree`, `--remote`, …), and options
  claude-manage cannot tell apart from a prompt. The plan lists what is not replayed.
- **Environment variables are not replayed.** Variables set by an alias, a wrapper script or
  inline (`AWS_PROFILE=work claude`) are not in the command line, so the restarted session
  gets the tab shell's environment. Put such settings in your shell profile or in Claude
  Code's `settings.json` (`env`).

Filters: `--group NAME`, `--session ID|PREFIX|TITLE`, `--window N`, `--outdated`.

> Claude Code's own `claude respawn --all` restarts *background* sessions; `claude-manage relaunch`
> is for the interactive ones in your terminal tabs.

## How resume works

`resume` opens one iTerm2 window per group (or adds tabs to the group's window when one of
its sessions is still running there), types the same `cd … && command claude --resume …`
command into each tab, labels the tabs, and waits until every session is running again. It
skips sessions that are running, guessed ids (unless `--include-guessed`) and duplicate ids,
and reports any tab it could not open or that did not come back (exit status 1).

## How sessions are found

Every running Claude Code writes `~/.claude/sessions/<pid>.json` with its session id, folder,
state and version. `claude-manage` reads those, the process table (exact argv, executable and
start time) and iTerm2's windows and tabs (via AppleScript), and pairs them up by terminal.
Titles come from the session name, or the last `/rename` or automatic title in the transcript.

Older Claude Code versions write no such record, so their id is **inferred** and marked `?`:
from `--session-id` or `--resume <id>` on the command line, or else the newest transcript in
the session's folder that was written after the process started and that no other running
session owns. When nothing fits, `show` reports the process instead of guessing. The version
comes from the executable's path.

## Files and settings

Everything claude-manage writes lives in `~/.claude-manage`. It only reads `~/.claude`.

```text
~/.claude-manage/
  config.toml        optional settings
  captures/          one JSON per capture + latest.json (plain JSON: safe to edit by hand)
  logs/              relaunch logs
  runs/              relaunch plans handed to the background worker
```

| `config.toml` key | Env override | Default |
| --- | --- | --- |
| `claude_bin` | `CLAUDE_MANAGE_CLAUDE_BIN` | `claude` |
| `claude_dir` | `CLAUDE_MANAGE_CLAUDE_DIR` | `~/.claude` |
| `stop_timeout` (seconds) | `CLAUDE_MANAGE_STOP_TIMEOUT` | `15` |
| `verify_timeout` (seconds) | `CLAUDE_MANAGE_VERIFY_TIMEOUT` | `45` |
| `keep_captures` | `CLAUDE_MANAGE_KEEP_CAPTURES` | `50` |
| `installed_version` | `CLAUDE_MANAGE_INSTALLED_VERSION` | detected from `claude` |

`CLAUDE_MANAGE_HOME` moves `~/.claude-manage` itself.

## Limitations

- macOS + iTerm2 only (tmux / Terminal.app sessions are shown but not relaunched or resumed into place).
- Split panes come back as separate tabs on `resume`.
- iTerm2 window ids only mean something while iTerm2 runs; after a restart `resume` opens new windows.

## Development

```sh
make sync        # .venv with ruff, mypy, pytest
make check       # lint (ruff + format + mypy --strict) and tests
make test-live   # also drives a real, throwaway iTerm2 window (macOS)
```

The code is `src/claude_manage/`: `claude_args` (Claude Code's command line),
`discovery` (processes, tabs, session records), `capture` and `views` (what `show` draws),
`labels`, `resume`, `relaunch`, and `cli` (Typer). Process and terminal access sit behind
small ports (`procs.ProcessTable`, `terminal.Terminal`) with real (`ps`/sysctl, iTerm2
AppleScript) and fake implementations.

Tests come in three layers:

- `tests/unit`: pure logic, plus real dummy processes for the process table.
- `tests/e2e`: the real CLI in a subprocess against a file-backed fake world
  (`CLAUDE_MANAGE_FAKE_WORLD`): dummy groups and sessions, captures edited by hand,
  relaunch and resume, failures included. `HOME` and all paths point into a temp dir, and
  `osascript` is stubbed to fail.
- `tests/live` (opt-in, `CM_LIVE=1`): a real iTerm2 window with fake `claude` processes.
  Signals are limited to those processes by `CLAUDE_MANAGE_PID_ALLOWLIST`.

## License

MIT
