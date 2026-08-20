#!/usr/bin/env python3
"""Verify interrupted-turn recovery against an isolated tmux server."""

from __future__ import annotations

import os
import re
import shlex
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WATCHER = ROOT / "bin" / "tmux-codex-auto-continue"
SUBMITTED_MARKER = "__CONTINUE_SUBMITTED__"
GOAL_RESUME_MARKER = "__GOAL_RESUME_SUBMITTED__"
COMPACT_SUBMITTED_MARKER = "__COMPACT_SUBMITTED__"


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
        "PS1='› '\n"
        f"Continue() {{ printf '%s\\n' {SUBMITTED_MARKER}; }}\n"
        "trap 'if [[ \"$BASH_COMMAND\" == \"/compact\" ]]; then "
        f"printf \"%s\\\\n\" {COMPACT_SUBMITTED_MARKER}; "
        f"elif [[ \"$BASH_COMMAND\" == \"/goal resume\" ]]; then "
        f"printf \"%s\\\\n\" {GOAL_RESUME_MARKER}; fi' DEBUG\n",
        encoding="utf-8",
    )

    environment = os.environ.copy()
    environment["XDG_RUNTIME_DIR"] = str(runtime_dir)
    environment["HOME"] = str(home_dir)
    watcher: subprocess.Popen[str] | None = None
    watcher_log_path = temporary_root / "watcher.log"
    watcher_log = watcher_log_path.open("w", encoding="utf-8")

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
        return tmux("capture-pane", "-p", "-S", "-1000", "-t", "0").stdout

    def send_line(line: str) -> None:
        typed = tmux("send-keys", "-t", "0", "-l", line)
        assert typed.returncode == 0, typed.stderr
        submitted = tmux("send-keys", "-t", "0", "Enter")
        assert submitted.returncode == 0, submitted.stderr

    def emit_lines(*lines: str) -> None:
        send_line(
            "printf '%s\\n' "
            + " ".join(shlex.quote(line) for line in lines)
        )

    def diagnostics() -> str:
        watcher_log.flush()
        return f"{capture()}\nWATCHER LOG:\n{watcher_log_path.read_text()}"

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
            stdout=watcher_log,
            stderr=subprocess.STDOUT,
            encoding="utf-8",
            errors="replace",
            env=environment,
        )
        time.sleep(1.5)
        assert watcher.poll() is None, "watcher exited before the test"

        # A normal completion has Codex's final-response separator immediately
        # before the final response block and must never be continued.
        emit_lines(
            "• Ran cargo test",
            "  └ 42 tests passed",
            "",
            "────────────────────────────────────────────────────────",
            "",
            "• Ran all requested tests successfully.",
            "",
            "─ Worked for 0m 01s ───────────────",
        )
        time.sleep(2.5)
        assert capture().count(SUBMITTED_MARKER) == 0, capture()

        # An unrecognized transcript shape also fails closed.
        emit_lines(
            "plain output without a Codex activity block",
            "─ Worked for 0m 02s ───────────────",
        )
        time.sleep(2.5)
        assert capture().count(SUBMITTED_MARKER) == 0, capture()

        # A normal completion arriving during the settle window cancels an
        # older interrupted marker instead of allowing a stale Continue.
        emit_lines(
            "• Ran cargo test",
            "  └ 42 tests passed",
            "─ Worked for 0m 03s ───────────────",
        )
        time.sleep(0.2)
        emit_lines(
            "────────────────────────────────────────────────────────",
            "",
            "• Ran all requested tests successfully.",
            "",
            "─ Worked for 0m 04s ───────────────",
        )
        time.sleep(2.5)
        assert capture().count(SUBMITTED_MARKER) == 0, capture()

        # Interrupted turns end on a recognized internal activity block. Make
        # its header scroll above the viewport to exercise history-backed
        # classification plus visible-marker revalidation.
        tool_output = tuple(
            f"  {index:02d} diff output" for index in range(40)
        )
        emit_lines(
            "• Edited src/main.rs (+1 -1)",
            "  └ diff follows",
            *tool_output,
            "",
            "─ Worked for 10m 56s ───────────────",
        )
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if SUBMITTED_MARKER in capture():
                break
            time.sleep(0.25)
        assert capture().count(SUBMITTED_MARKER) == 1, capture()

        emit_lines("› ─ Worked for 10m 56s ───────────────")
        time.sleep(2.5)
        assert capture().count(SUBMITTED_MARKER) == 1, capture()

        overloaded = (
            "■ Our servers are currently overloaded. Please try again later.  ---"
        )
        send_line("printf '%s\\n' " + shlex.quote(overloaded))
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if capture().count(SUBMITTED_MARKER) == 2:
                break
            time.sleep(0.25)
        assert capture().count(SUBMITTED_MARKER) == 2, capture()

        content_blocked = (
            "ⓘ This content can't be shown",
            (
                "  We take extra caution with cybersecurity requests. If you’re "
                "a security professional, you may be able to apply for Trusted "
                "Access."
            ),
            (
                "  Trusted Access: "
                "https://openai.com/form/enterprise-trusted-access-for-cyber/"
            ),
            "  Learn more: https://help.openai.com/en/articles/20001326",
        )
        emit_lines(*content_blocked)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if capture().count(SUBMITTED_MARKER) == 3:
                break
            time.sleep(0.25)
        assert capture().count(SUBMITTED_MARKER) == 3, capture()

        # Quoted warning text must not be treated as a live Codex state.
        emit_lines(*(f"  {line}" for line in content_blocked))
        time.sleep(2.5)
        assert capture().count(SUBMITTED_MARKER) == 3, capture()

        cyber_risk_flagged = (
            (
                "■ This content was flagged for possible cybersecurity risk. "
                "If this seems wrong, try rephrasing your request. To get "
                "authorized for security work, join the Trusted Access for "
                "Cyber program:"
            ),
            "https://chatgpt.com/cyber",
        )
        emit_lines(*cyber_risk_flagged)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if capture().count(SUBMITTED_MARKER) == 4:
                break
            time.sleep(0.25)
        assert capture().count(SUBMITTED_MARKER) == 4, capture()

        emit_lines(*(f"  {line}" for line in cyber_risk_flagged))
        time.sleep(2.5)
        assert capture().count(SUBMITTED_MARKER) == 4, capture()

        # A compaction marker is never actionable unless this watcher first
        # submitted /compact for the strict 413 context error.
        emit_lines("• Context compacted")
        time.sleep(2.5)
        assert capture().count(COMPACT_SUBMITTED_MARKER) == 0, diagnostics()
        assert capture().count(SUBMITTED_MARKER) == 4, diagnostics()

        context_too_large = (
            "■ unexpected status 413 Payload Too Large: "
            "{'error': 'request_too_large', 'message_en': 'Conversation "
            "context is too large (request body 952599B > 1MB upstream limit) "
            "and auto-compaction is not reducing it.', 'body_bytes': 952599, "
            "'limit_bytes': 950000}"
        )
        emit_lines(context_too_large)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if capture().count(COMPACT_SUBMITTED_MARKER) == 1:
                break
            time.sleep(0.25)
        assert capture().count(COMPACT_SUBMITTED_MARKER) == 1, diagnostics()
        assert capture().count(SUBMITTED_MARKER) == 4, diagnostics()

        # Repeating the same 413 while /compact is in flight must not submit a
        # second command. The next new compaction marker completes the phase.
        emit_lines(context_too_large.replace("952599", "952600"))
        time.sleep(2.5)
        assert capture().count(COMPACT_SUBMITTED_MARKER) == 1, diagnostics()
        send_line(
            "(sleep 0.2; printf '\\r%s\\n' "
            + shlex.quote("• Context compacted")
            + "; sleep 1; printf '%s\\n' "
            + shlex.quote("─ Worked for 0m 02s ───────────────")
            + "; sleep 2; printf '› ') &"
        )
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if capture().count(SUBMITTED_MARKER) == 5:
                break
            time.sleep(0.25)
        assert capture().count(SUBMITTED_MARKER) == 5, diagnostics()

        # The completed phase is latched off; a repeated marker or a quoted
        # context error must not inject more input.
        emit_lines("• Context compacted", f"  {context_too_large}")
        time.sleep(2.5)
        assert capture().count(COMPACT_SUBMITTED_MARKER) == 1, diagnostics()
        assert capture().count(SUBMITTED_MARKER) == 5, diagnostics()

        # A different retryable terminal error replaces an unfinished
        # compaction phase instead of being consumed and lost.
        emit_lines(context_too_large.replace("952599", "952602"))
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if capture().count(COMPACT_SUBMITTED_MARKER) == 2:
                break
            time.sleep(0.25)
        assert capture().count(COMPACT_SUBMITTED_MARKER) == 2, diagnostics()
        emit_lines(
            "■ Our servers are currently overloaded. Please try again later."
        )
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if capture().count(SUBMITTED_MARKER) == 6:
                break
            time.sleep(0.25)
        assert capture().count(SUBMITTED_MARKER) == 6, diagnostics()
        emit_lines("• Context compacted")
        time.sleep(2.5)
        assert capture().count(SUBMITTED_MARKER) == 6, diagnostics()

        # If compaction finishes while copy mode owns the pane, keep the
        # confirmation bounded and revalidate it only after copy mode exits.
        emit_lines(context_too_large.replace("952599", "952601"))
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if capture().count(COMPACT_SUBMITTED_MARKER) == 3:
                break
            time.sleep(0.25)
        assert capture().count(COMPACT_SUBMITTED_MARKER) == 3, diagnostics()
        resized = tmux(
            "resize-window",
            "-t",
            "0",
            "-x",
            "100",
            "-y",
            "22",
        )
        assert resized.returncode == 0, resized.stderr
        time.sleep(1.0)
        send_line(
            "(sleep 1; printf '\\r%s\\n› ' "
            + shlex.quote("• Context compacted")
            + ") &"
        )
        entered_mode = tmux("copy-mode", "-t", "0")
        assert entered_mode.returncode == 0, entered_mode.stderr
        time.sleep(2.5)
        assert capture().count(SUBMITTED_MARKER) == 6, diagnostics()
        exited_mode = tmux("send-keys", "-t", "0", "-X", "cancel")
        assert exited_mode.returncode == 0, exited_mode.stderr
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if capture().count(SUBMITTED_MARKER) == 7:
                break
            time.sleep(0.25)
        assert capture().count(SUBMITTED_MARKER) == 7, diagnostics()

        # An event that arrives while copy mode owns the pane is deferred. It
        # must not receive input in mode, but should recover after mode exits.
        first_error_id = "2b10afaf-84a8-45ee-8901-c32631c94493"
        send_line(
            "(sleep 1; printf '\\r%s\\n› ' "
            + shlex.quote(
                "■ An error occurred while processing your request. You can "
                "retry your request. Please include the request ID "
                f"{first_error_id} in your message."
            )
            + ") &"
        )
        entered_mode = tmux("copy-mode", "-t", "0")
        assert entered_mode.returncode == 0, entered_mode.stderr
        time.sleep(2.5)
        assert capture().count(SUBMITTED_MARKER) == 7, capture()
        exited_mode = tmux("send-keys", "-t", "0", "-X", "cancel")
        assert exited_mode.returncode == 0, exited_mode.stderr
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if capture().count(SUBMITTED_MARKER) == 8:
                break
            time.sleep(0.25)
        assert capture().count(SUBMITTED_MARKER) == 8, diagnostics()

        # Manual recovery during the settle window makes the deferred event
        # stale. The watcher must not submit a duplicate Continue afterward.
        second_error_id = "3b10afaf-84a8-45ee-8901-c32631c94493"
        send_line(
            "(sleep 1; printf '\\r%s\\n› ' "
            + shlex.quote(
                "■ An error occurred while processing your request. You can "
                "retry your request. Please include the request ID "
                f"{second_error_id} in your message."
            )
            + ") &"
        )
        entered_mode = tmux("copy-mode", "-t", "0")
        assert entered_mode.returncode == 0, entered_mode.stderr
        time.sleep(2.5)
        assert capture().count(SUBMITTED_MARKER) == 8, capture()
        exited_mode = tmux("send-keys", "-t", "0", "-X", "cancel")
        assert exited_mode.returncode == 0, exited_mode.stderr
        send_line("Continue")
        time.sleep(3.0)
        assert capture().count(SUBMITTED_MARKER) == 9, capture()

        # Remote compaction failures use a distinct strict prefix but recover
        # through the same bracketed-paste plus real-Enter submission path.
        remote_overloaded = (
            "■ Error running remote compact task: Our servers are currently "
            "overloaded. Please try again later."
        )
        emit_lines(remote_overloaded)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if capture().count(SUBMITTED_MARKER) == 10:
                break
            time.sleep(0.25)
        assert capture().count(SUBMITTED_MARKER) == 10, diagnostics()

        remote_error_id = "30f0c0fa-d1dc-4c5f-8c63-f2909d6f0fc3"
        emit_lines(
            (
                "■ Error running remote compact task: An error occurred while "
                "processing your request. You can retry your request, or "
                "contact us through our help center at help.openai.com if the "
                "error persists."
            ),
            (
                "Please include the request ID "
                f"{remote_error_id} in your message."
            ),
        )
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if capture().count(SUBMITTED_MARKER) == 11:
                break
            time.sleep(0.25)
        assert capture().count(SUBMITTED_MARKER) == 11, diagnostics()

        # A 429 retry-limit marker is recovered with a bounded delay rather
        # than immediately retried in a tight loop. The first retry is one
        # minute later; the unit test covers the backoff sequence and this
        # integration test verifies that an active Goal uses the literal
        # `/goal resume` command.
        rate_limit = (
            "■ exceeded retry limit, last status: 429 Too Many Requests"
        )
        emit_lines(
            rate_limit,
            "• Goal active Objective: resume this work Time: 58m.",
        )
        time.sleep(3.0)
        assert capture().count(SUBMITTED_MARKER) == 11, diagnostics()
        deadline = time.monotonic() + 65
        while time.monotonic() < deadline:
            if GOAL_RESUME_MARKER in capture():
                break
            time.sleep(0.25)
        assert GOAL_RESUME_MARKER in capture(), diagnostics()

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
        watcher_log.flush()
        output = watcher_log_path.read_text()
        assert "event=worked_interrupted" in output
        assert "event=server_overloaded" in output
        assert "event=cyber_content_blocked" in output
        assert "event=cyber_risk_flagged" in output
        assert "sent /compact" in output
        assert "event=context_too_large" in output
        assert "event=context_compacted" in output
        assert f"deferred error:{first_error_id}" in output
        assert "reason=pane-mode" in output
        assert "event=error" in output

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
        assert "worked=" not in status.stdout
        print("worked-integration: PASS")
        return 0
    finally:
        if watcher is not None and watcher.poll() is None:
            watcher.terminate()
            watcher.wait(timeout=5)
        watcher_log.close()
        tmux("kill-server")
        shutil.rmtree(temporary_root, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
