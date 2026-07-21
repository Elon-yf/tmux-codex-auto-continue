# Changelog

## v0.1.2 - 2026-07-21

- Recognize strict column-zero `─ Worked for ... ─` completion separators,
  including durations with an optional hour component.
- Submit `Continue` with bracketed paste followed by a real Enter after a new
  completion event.
- Gate normal-completion continuation behind the explicit
  `@codex-auto-continue-worked` option so existing upgrades remain opted out.
- Add a safe watcher-only restart path so curl and TPM upgrades load the new
  code without restarting tmux sessions or panes.
- Keep quoted and indented lookalikes excluded and add an isolated tmux
  integration test for the completion path.

## v0.1.1 - 2026-07-21

- Recognize the column-zero `■ internal streaming error, please retry` Codex
  error, including the optional displayed `---` separator suffix.
- Reuse the verified bracketed-paste plus real-Enter `Continue` submission.

## v0.1.0 - 2026-07-21

- Detect retryable Codex request errors and model-capacity errors.
- Submit `Continue` with bracketed paste and a real Enter key.
- Detect the live safety-buffering selection view and safely accept
  `Keep waiting`.
- Add foreground-process, copy-mode, viewport, resize, and singleton-daemon
  safeguards.
- Add pinned curl, TPM, manual installation, self-tests, ShellCheck, Ruff, and
  GitHub Actions CI.
