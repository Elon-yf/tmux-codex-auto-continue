#!/usr/bin/env python3
"""Verify Worked-for recovery against an isolated tmux server."""

from __future__ import annotations

import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import tempfile
import time


ROOT = Path(__file__).resolve().parents[1]
WATCHER = ROOT / "bin" / "tmux-codex-auto-continue"
SUBMITTED_MARKER = "__CONTINUE_SUBMITTED__"


def main() -> int:
    temporary_root = Path(tempfile.mkdtemp(prefix="tmux-codex-auto-test-"))
    socket = f"/tmp/tmux-codex-auto-test-{os.getpid()}"
    runtime_dir = temporary_root / "runtime"
    runtime_dir.mkdir(mode=0o700)
    home_dir = temporary_root / "home"
    home_dir.mkdir(mode=0o700)
    fake_codex = (
        temporary_root / "@openai" / "codex" / "vendor" / "bin" / "codex"
    )
    fake_codex.parent.mkdir(parents=True)
    shutil.copy2("/bin/bash", fake_codex)
    fake_codex.chmod(0o755)
    bash_rc = temporary_root / "bashrc"
    bash_rc.write_text(
        "PS1='X '\n"
        f"Continue() {{ printf '%s\\n' {SUBMITTED_MARKER}; }}\n",
        encoding="utf-8",
    )

    environment = os.environ.copy()
    environment["XDG_RUNTIME_DIR"] = str(runtime_dir)
    environment["HOME"] = str(home_dir)
    watcher: subprocess.Popen[str] | None = None

    def tmux(*arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["tmux", "-f", "/dev/null", "-S", socket, *arguments],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            encoding="utf-8",
            errors="replace",
            check=False,
            env=environment,
        )

    def capture() -> str:
        return tmux("capture-pane", "-p", "-S", "-100", "-t", "0").stdout

    def send_line(line: str) -> None:
        typed = tmux("send-keys", "-t", "0", "-l", line)
        assert typed.returncode == 0, typed.stderr
        submitted = tmux("send-keys", "-t", "0", "Enter")
        assert submitted.returncode == 0, submitted.stderr

    try:
        created = tmux(
            "new-session",
            "-d",
            "-x",
            "120",
            "-y",
            "24",
            f"{fake_codex} --noprofile --rcfile {shlex.quote(str(bash_rc))} -i",
        )
        assert created.returncode == 0, created.stderr
        enabled = tmux("set-option", "-g", "@codex-auto-continue", "on")
        assert enabled.returncode == 0, enabled.stderr

        watcher = subprocess.Popen(
            ["python3", str(WATCHER), "--socket", socket],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            encoding="utf-8",
            errors="replace",
            env=environment,
        )
        time.sleep(1.5)
        assert watcher.poll() is None, "watcher exited before the test"

        # Completion continuation is a separate explicit opt-in.
        send_line("printf '%s\\n' '─ Worked for 0m 01s ───────────────'")
        time.sleep(2.5)
        assert capture().count(SUBMITTED_MARKER) == 0, capture()
        worked_enabled = tmux(
            "set-option", "-g", "@codex-auto-continue-worked", "on"
        )
        assert worked_enabled.returncode == 0, worked_enabled.stderr

        send_line("printf '%s\\n' '─ Worked for 10m 56s ───────────────'")
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if SUBMITTED_MARKER in capture():
                break
            time.sleep(0.25)
        assert capture().count(SUBMITTED_MARKER) == 1, capture()

        send_line("printf '%s\\n' '› ─ Worked for 10m 56s ───────────────'")
        time.sleep(2.5)
        assert capture().count(SUBMITTED_MARKER) == 1, capture()

        restarted = subprocess.run(
            ["python3", str(WATCHER), "--socket", socket, "--restart"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            encoding="utf-8",
            errors="replace",
            check=False,
            env=environment,
        )
        assert restarted.returncode == 0, restarted.stdout
        watcher.wait(timeout=5)
        output = watcher.stdout.read() if watcher.stdout is not None else ""
        assert "event=worked" in output

        status = subprocess.run(
            ["python3", str(WATCHER), "--socket", socket, "--status"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            encoding="utf-8",
            errors="replace",
            check=False,
            env=environment,
        )
        assert status.returncode == 0, status.stdout
        replacement = re.search(r"\bdaemon=(\d+)\b", status.stdout)
        assert replacement is not None, status.stdout
        assert int(replacement.group(1)) != watcher.pid
        assert "worked=on" in status.stdout
        print("worked-integration: PASS")
        return 0
    finally:
        if watcher is not None and watcher.poll() is None:
            watcher.terminate()
            watcher.wait(timeout=5)
        tmux("kill-server")
        shutil.rmtree(temporary_root, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
