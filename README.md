# tmux-codex-auto-continue

An unofficial tmux watcher that recovers selected Codex CLI retry states and
accepts Codex's **Keep waiting** safety-buffering choice.

> [!WARNING]
> This plugin injects keys into a verified Codex pane. Accepting **Keep
> waiting** can keep a request running longer. Read the behavior and safety
> sections below, and use `prefix` + `A` as the emergency toggle.

## Quick install

Pinned one-line installer (v0.1.1):

```sh
curl -fsSL https://raw.githubusercontent.com/yeahdongcn/tmux-codex-auto-continue/v0.1.1/install.sh | sh
```

For an audit-first install, download and inspect the script before running it:

```sh
curl -fsSLo /tmp/tmux-codex-install.sh \
  https://raw.githubusercontent.com/yeahdongcn/tmux-codex-auto-continue/v0.1.1/install.sh
less /tmp/tmux-codex-install.sh
sh /tmp/tmux-codex-install.sh
```

The installer uses no `sudo`, verifies the watcher SHA-256, runs its self-test,
installs it to `~/.local/bin`, adds one marked block to `~/.tmux.conf`, and
reloads the default tmux server without restarting it. Re-running it is
idempotent.

## Automatic behavior

| Codex state | Action |
| --- | --- |
| `■ An error occurred while processing ...` | Paste `Continue`, then send a real Enter |
| `■ internal streaming error, please retry` | Paste `Continue`, then send a real Enter |
| `⚠ Selected model is at capacity. Please try a different model.` | Paste `Continue`, then send a real Enter |
| Active `Additional safety checks` menu, Retry selected | Down, verify `Keep waiting`, then Enter |
| Active menu, `Keep waiting` already selected | Enter only |
| `─ Worked for ... ─` normal completion | Ignore |

The two-item safety menu (without a faster-model retry choice) is also handled:
when `Keep waiting` is already the first selected item, only Enter is sent.

## Safety model

The watcher fails closed and sends input only after all relevant checks pass:

- The pane's actual foreground process group must contain the native Codex
  executable under an npm `@openai/codex/.../vendor/.../codex` path.
- Shells, Claude, dead panes, copy mode, changed process groups, and disabled
  tmux servers are ignored.
- Error messages require Codex's exact column-zero glyph, preventing ordinary
  user prompts and quoted text from matching.
- Interactive menus are read from the current viewport only, never historical
  scrollback. The exact title, complete explanatory text, numbered rows,
  selected marker, and footer must match, and the footer must be the final
  non-empty line.
- After Down, the watcher re-captures the screen and confirms `Keep waiting`
  before Enter. It latches the handled menu until the menu disappears.
- The ordinary error path rechecks that no selection menu owns the keyboard
  immediately before it sends `Continue`.
- `Continue` uses bracketed paste followed by a real Enter. This avoids Codex's
  rapid-character paste-burst handling, which can turn Enter into a newline.

No tmux session or pane is created, renamed, closed, or killed.

## Requirements and compatibility

- Linux with a mounted `/proc` filesystem (WSL should work, but is not yet
  covered by CI)
- Python 3.10 or newer
- tmux (3.2 or newer recommended)
- Codex CLI installed from the npm `@openai/codex` package
- UTF-8 terminal and the English Codex UI

The initial release is tested with Codex CLI 0.144.5. Codex UI wording and
layout may change in later releases; unknown layouts are ignored rather than
matched loosely. macOS, Homebrew/standalone Codex binaries, localized UI text,
and non-Linux process inspection are not currently supported.

## TPM installation

With [TPM](https://github.com/tmux-plugins/tpm), explicitly enable the watcher
and add the plugin before TPM's own `run` line:

```tmux
set -g @codex-auto-continue on
set -g @plugin 'yeahdongcn/tmux-codex-auto-continue'

# Keep this at the bottom of .tmux.conf:
run '~/.tmux/plugins/tpm/tpm'
```

Press `prefix` + `I` to install it. TPM uses the repository-local executable;
it does not copy anything into `~/.local/bin`.

The toggle key defaults to `prefix` + `A`. Set it before the plugin line to use
another tmux key:

```tmux
set -g @codex-auto-continue-key C-a
```

## Operation

Check the default tmux server:

```sh
~/.local/bin/tmux-codex-auto-continue \
  --socket "$(tmux display-message -p '#{socket_path}')" --status
```

Toggle it with `prefix` + `A`, or set the global option explicitly:

```sh
tmux set-option -g @codex-auto-continue off
tmux set-option -g @codex-auto-continue on
```

Logs contain pane/session identifiers and action kinds, not pane contents, and
are stored at `~/.cache/tmux-codex-auto-continue.log`. A separate tmux socket
(`tmux -L name`) needs its own watcher process and global option.

## Update and uninstall

Re-run the pinned installer after changing `v0.1.1` to a newer release tag.
To remove a curl installation:

```sh
curl -fsSL https://raw.githubusercontent.com/yeahdongcn/tmux-codex-auto-continue/v0.1.1/uninstall.sh | sh
```

The uninstaller disables the option, removes only the marked config block and
installed executable, and never stops the tmux server. A currently idle watcher
exits when that tmux server exits.

For TPM, remove the plugin line and press `prefix` + `alt` + `u` (TPM's clean
command), or remove its plugin directory manually.

## Development

```sh
python3 bin/tmux-codex-auto-continue --self-test
python3 -m py_compile bin/tmux-codex-auto-continue
ruff check bin/tmux-codex-auto-continue
shellcheck install.sh uninstall.sh tmux-codex-auto-continue.tmux
```

The built-in tests cover error signatures, normal-completion exclusion,
three-item and two-item menu parsing, selected rows, and quoted/stale menu
rejection. The release was also exercised against an isolated tmux server with
a native fake-Codex menu to verify Down+Enter, Enter-only, and no repeated keys.

Security reports should follow [SECURITY.md](SECURITY.md).

## License

[MIT](LICENSE)
