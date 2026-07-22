# Changelog

## v0.1.4 - 2026-07-22

- Keep `Worked for ...` continuation disabled in newly generated curl-install
  configurations. Users must opt in explicitly with
  `@codex-auto-continue-worked on`.
- Preserve existing configuration files during upgrades, including an existing
  explicit `on` or `off` choice.

## v0.1.3 - 2026-07-22

- Keep newly observed retry/completion events for up to 30 seconds when a tmux
  pane mode owns the keyboard, then recover only if the event is still the
  current visible terminal state after the mode exits.
- Treat every nonzero `pane_in_mode` depth as active, including nested mode
  stacks reported as `2` or higher.
- Revalidate the exact terminal event, empty composer, Codex process, global
  option, pane mode, and safety menu immediately before submitting input.
- Cancel deferred input after a manual `Continue`, later assistant/tool output,
  a new terminal event, a resize, a safety menu, or the 30-second deadline.
- Extend the isolated tmux integration test with pane-mode recovery and
  manual-recovery deduplication.

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
