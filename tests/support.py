"""Test support: the fake world the end-to-end tests drive the CLI against.

End-to-end tests run the real CLI in a subprocess against a fake world
(``CLAUDE_MANAGE_FAKE_WORLD``): HOME and every claude-manage/Claude path point
into ``tmp_path``, and ``osascript`` is stubbed to fail the test. So no test can
signal a real process, read or write real ``~/.claude*`` state, or touch iTerm2.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SRC = Path(__file__).resolve().parents[1] / "src"
INSTALLED = "2.1.0"  # the "installed" Claude Code version in the fake world
FLAG = "--dangerously-skip-permissions"
CWD = "/tmp/cm-test/proj"


def sid(n: int) -> str:
    """A deterministic, valid session id."""
    return f"{n:08x}-0000-4000-8000-{n:012x}"


def project_dir(projects: Path, cwd: str) -> Path:
    return projects / re.sub(r"[^A-Za-z0-9]", "-", cwd)


@dataclass
class World:
    root: Path
    env: dict[str, str]
    file: Path  # the fake world JSON
    sessions: Path  # fake ~/.claude/sessions
    projects: Path  # fake ~/.claude/projects
    home: Path  # CLAUDE_MANAGE_HOME

    # -- building the world ----------------------------------------------------

    def state(self) -> dict[str, Any]:
        data: dict[str, Any] = json.loads(self.file.read_text())
        return data

    def save(self, state: dict[str, Any]) -> None:
        self.file.write_text(json.dumps(state, indent=2))

    def session(
        self,
        pid: int,
        *,
        window: int = 1,
        tab: int = 1,
        title: str = "",
        ai_title: str = "",
        cwd: str = CWD,
        status: str = "idle",
        version: str = INSTALLED,
        record: bool = True,
        transcript: bool = True,
        argv: list[str] | None = None,
        in_iterm: bool = True,
        ignore_term: bool = False,
        closes_on_exit: bool = False,
        inert: bool = False,
        exe: str = "",
        session_id: str | None = None,
        shell_argv: list[str] | None = None,
        started: float | None = None,
    ) -> str:
        """Add a running ``claude`` (and its iTerm2 tab); returns its session id."""
        session_id = session_id or sid(pid)
        tty = f"tty{pid}"
        state = self.state()
        if in_iterm:
            state["tabs"].append(
                {
                    "window_id": str(window * 100),
                    "window_index": window,
                    "tab_index": tab,
                    "tty": tty,
                    "shell_pid": pid + 50_000,
                    "shell_argv": shell_argv or ["-zsh"],
                    "closes_on_exit": closes_on_exit,
                    "inert": inert,
                }
            )
        proc = {"pid": pid, "tty": tty, "argv": argv or ["claude", FLAG], "cwd": cwd}
        proc.update(ignore_term=ignore_term, exe=exe, start=started or time.time() - 600)
        state["procs"].append(proc)
        if title:
            state["titles"][session_id] = title
        self.save(state)
        if record:
            meta: dict[str, Any] = {
                "pid": pid,
                "sessionId": session_id,
                "cwd": cwd,
                "status": status,
                "version": version,
                "kind": "interactive",
            }
            if title:
                meta.update(name=title, nameSource="user")
            (self.sessions / f"{pid}.json").write_text(json.dumps(meta))
        if transcript:
            folder = project_dir(self.projects, cwd)
            folder.mkdir(parents=True, exist_ok=True)
            lines = [{"type": "user", "message": "hi"}]
            if ai_title:
                lines.append({"type": "ai-title", "aiTitle": ai_title})
            path = folder / f"{session_id}.jsonl"
            path.write_text("".join(json.dumps(line) + "\n" for line in lines))
        return session_id

    def set_status(self, pid: int, status: str) -> None:
        path = self.sessions / f"{pid}.json"
        meta = json.loads(path.read_text())
        meta["status"] = status
        path.write_text(json.dumps(meta))

    def stop(self, pid: int) -> None:
        """The session's claude exits on its own (its tab stays open)."""
        state = self.state()
        state["procs"] = [p for p in state["procs"] if p["pid"] != pid]
        self.save(state)
        (self.sessions / f"{pid}.json").unlink(missing_ok=True)

    def reboot(self) -> None:
        """Every process and every iTerm2 window is gone."""
        state = self.state()
        for p in state["procs"]:
            (self.sessions / f"{p['pid']}.json").unlink(missing_ok=True)
        state["procs"], state["tabs"] = [], []
        self.save(state)

    def set(self, **values: Any) -> None:
        """Set top-level world keys, e.g. layout_error, new_tab_budget, new_window_error."""
        state = self.state()
        state.update(values)
        self.save(state)

    def labels(self) -> dict[str, str]:
        """tty -> label text currently shown on that fake tab."""
        return {t["tty"]: t["label"]["text"] for t in self.state()["tabs"] if t.get("label")}

    def run_inside(self, claude_pid: int) -> None:
        """Make claude-manage itself run inside the session with this pid."""
        state = self.state()
        state["self"] = {"pid": 990_000, "ppid": claude_pid}
        self.save(state)

    # -- observing it ----------------------------------------------------------------

    def actions(self, op: str | None = None) -> list[dict[str, Any]]:
        """What was done to processes and windows (label writes only when asked for)."""
        actions = self.state()["actions"]
        if op is None:
            return [a for a in actions if a["op"] != "label"]
        return [a for a in actions if a["op"] == op]

    def tty(self, pid: int) -> str:
        return f"tty{pid}"

    @property
    def latest_path(self) -> Path:
        return self.home / "captures" / "latest.json"

    def latest(self) -> dict[str, Any]:
        data: dict[str, Any] = json.loads(self.latest_path.read_text())
        return data

    def captures(self) -> list[dict[str, Any]]:
        files = sorted((self.home / "captures").glob("*.json"))
        return [json.loads(p.read_text()) for p in files if p.name != "latest.json"]

    def wait_for_log(self, text: str, timeout: float = 20.0) -> str:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            logs = sorted((self.home / "logs").glob("relaunch-*.log"))
            if logs and text in (content := logs[-1].read_text()):
                return content
            time.sleep(0.1)
        raise AssertionError(f"no relaunch log containing {text!r} after {timeout}s")

    def run(
        self, *args: str, check: bool = True, timeout: float = 60
    ) -> subprocess.CompletedProcess[str]:
        return self.run_env(self.env, *args, check=check, timeout=timeout)

    def run_env(
        self, env: dict[str, str], *args: str, check: bool = True, timeout: float = 60
    ) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(
            [sys.executable, "-m", "claude_manage", *args],
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        if check and result.returncode != 0:
            raise AssertionError(
                f"claude-manage {' '.join(args)} exited {result.returncode}\n"
                f"--- stdout\n{result.stdout}\n--- stderr\n{result.stderr}"
            )
        return result

    def popen(self, *args: str) -> subprocess.Popen[str]:
        return subprocess.Popen(
            [sys.executable, "-m", "claude_manage", *args],
            env=self.env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
