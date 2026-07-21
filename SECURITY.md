# Security policy

This plugin injects input only into a pane whose foreground process group has
been verified as a native npm-installed Codex CLI process. Please do not submit
changes that weaken this to pane titles, `pane_current_command`, arbitrary
regular expressions, or shell-configured commands.

To report a vulnerability, use GitHub's private security-advisory flow for this
repository. Include the Codex version, tmux version, terminal width/height, the
rendered prompt structure, and whether copy mode was active. Do not include
credentials, request contents, or other private pane output.

The supported surface for v0.1.1 is Linux, Python 3.10+, an English Codex UI,
and the npm `@openai/codex` native binary layout. Unsupported environments fail
closed or are ignored.
