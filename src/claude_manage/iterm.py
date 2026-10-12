"""iTerm2 terminal backend, driven through AppleScript (``osascript``).

Values reach the scripts as ``on run argv`` arguments, never by string
interpolation, so titles and commands need no AppleScript escaping.
"""

from __future__ import annotations

import os
import subprocess

from .labels import sequences
from .procs import norm_tty
from .terminal import TabInfo, TerminalError

# `tab` is an iTerm2 class inside `tell application "iTerm2"`, so build separators outside it.
_LAYOUT = """
on run argv
  if application "iTerm2" is not running then return ""
  set sep to character id 9
  set nl to character id 10
  set out to ""
  tell application "iTerm2"
    set wi to 0
    repeat with w in windows
      set wi to wi + 1
      set ti to 0
      repeat with t in tabs of w
        set ti to ti + 1
        repeat with s in sessions of t
          set row to "" & (id of w) & sep & wi & sep & ti & sep
          set out to out & row & (tty of s) & sep & (name of s) & nl
        end repeat
      end repeat
    end repeat
  end tell
  return out
end run
"""

_WRITE_TEXT = """
on run argv
  set targetTty to item 1 of argv
  set theText to item 2 of argv
  if application "iTerm2" is not running then return "missing"
  tell application "iTerm2"
    repeat with w in windows
      repeat with t in tabs of w
        repeat with s in sessions of t
          if (tty of s) is targetTty then
            tell s to write text theText
            return "ok"
          end if
        end repeat
      end repeat
    end repeat
  end tell
  return "missing"
end run
"""

_NEW_TAB = """
on run argv
  set wid to (item 1 of argv) as integer
  set theText to item 2 of argv
  if application "iTerm2" is not running then return "missing"
  tell application "iTerm2"
    try
      set w to window id wid
    on error
      return "missing"
    end try
    tell w to set t to (create tab with default profile)
    tell current session of t to write text theText
  end tell
  return "ok"
end run
"""

_NEW_WINDOW = """
on run argv
  tell application "iTerm2"
    set w to (create window with default profile)
    set isFirst to true
    repeat with theText in argv
      if isFirst then
        set isFirst to false
      else
        tell w to create tab with default profile
      end if
      tell current session of w to write text (theText as text)
    end repeat
    return (id of w) as text
  end tell
end run
"""


class ITerm2:
    def layout(self) -> list[TabInfo]:
        tabs: list[TabInfo] = []
        for line in _osascript(_LAYOUT).splitlines():
            parts = line.split("\t", 4)
            if len(parts) < 4:
                continue
            try:
                window_index, tab_index = int(parts[1]), int(parts[2])
            except ValueError:
                continue
            name = parts[4] if len(parts) > 4 else ""
            tabs.append(TabInfo(parts[0], window_index, tab_index, norm_tty(parts[3]), name))
        return tabs

    def write_text(self, tty: str, text: str) -> bool:
        return _osascript(_WRITE_TEXT, f"/dev/{tty}", text).strip() == "ok"

    def new_tab(self, window_id: str, text: str) -> bool:
        if not window_id.isdigit():
            return False
        return _osascript(_NEW_TAB, window_id, text).strip() == "ok"

    def label(self, tty: str, text: str, color: tuple[int, int, int] | None) -> bool:
        try:
            fd = os.open(f"/dev/{tty}", os.O_WRONLY | os.O_NOCTTY | os.O_NONBLOCK)
        except OSError:
            return False
        try:
            os.write(fd, sequences(text, color).encode())  # one write: never interleaved
        except OSError:
            return False
        finally:
            os.close(fd)
        return True

    def new_window(self, texts: list[str]) -> str:
        window_id = _osascript(_NEW_WINDOW, *texts).strip()
        if not window_id:
            raise TerminalError("iTerm2 did not report the new window")
        return window_id


def _osascript(script: str, *args: str) -> str:
    """Run an AppleScript; raise TerminalError when it fails (a script's own "not
    found"-style answers come back as normal output)."""
    try:
        result = subprocess.run(
            ["osascript", "-e", script, *args],
            capture_output=True,
            text=True,
            errors="replace",
            check=False,
            timeout=60,
        )
    except FileNotFoundError as e:
        raise TerminalError("osascript not found: iTerm2 support needs macOS") from e
    except (OSError, subprocess.TimeoutExpired) as e:
        raise TerminalError(f"osascript failed: {e}") from e
    if result.returncode != 0:
        message = result.stderr.strip() or f"exit status {result.returncode}"
        if "-1743" in message or "Not authorized" in message:
            message += (
                " (allow it in System Settings > Privacy & Security > Automation,"
                " for the app running claude-manage, to control iTerm2)"
            )
        raise TerminalError(f"iTerm2 AppleScript failed: {message}")
    return result.stdout
