# tmux-codex-auto-continue

An unofficial tmux watcher that optionally continues completed Codex CLI
turns, recovers selected retry states, and accepts Codex's **Keep waiting**
safety-buffering choice.

> [!WARNING]
> This plugin injects keys into a verified Codex pane. Continuing every
> completed turn or accepting **Keep waiting** can keep a session running and
> consume additional time or tokens. Read the behavior and safety sections
> below, and use `prefix` + `A` as the emergency toggle.

## Quick install

Pinned one-line installer (v0.1.5):

```sh
curl -fsSL https://raw.githubusercontent.com/yeahdongcn/tmux-codex-auto-continue/v0.1.5/install.sh | sh
```

For an audit-first install, download and inspect the script before running it:

```sh
curl -fsSLo /tmp/tmux-codex-install.sh \
  https://raw.githubusercontent.com/yeahdongcn/tmux-codex-auto-continue/v0.1.5/install.sh
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
| `■ Our servers are currently overloaded. Please try again later.` | Paste `Continue`, then send a real Enter |
| `⚠ Selected model is at capacity. Please try a different model.` | Paste `Continue`, then send a real Enter |
| Active `Additional safety checks` menu, Retry selected | Down, verify `Keep waiting`, then Enter |
| Active menu, `Keep waiting` already selected | Enter only |
| `─ Worked for ... ─` normal completion, when opted in | Paste `Continue`, then send a real Enter |

The two-item safety menu (without a faster-model retry choice) is also handled:
when `Keep waiting` is already the first selected item, only Enter is sent.

Normal-completion continuation is controlled separately by:

```tmux
set -g @codex-auto-continue-worked on
```

The watcher treats an unset option as off, and a fresh curl configuration
explicitly sets it to off. Upgrades preserve an existing `on` or `off` choice.
Each injected `Continue` can itself finish with another `Worked for` line, so
opt in only when that repeated behavior is intentional.

## Safety model

The watcher fails closed and sends input only after all relevant checks pass:

- The pane's actual foreground process group must contain the native Codex
  executable under an npm `@openai/codex/.../vendor/.../codex` path.
- Shells, Claude, dead panes, changed process groups, and disabled tmux servers
  are ignored. Every nonzero tmux pane-mode depth is treated as owning the
  keyboard, including nested copy-mode stacks.
- Error and completion messages require Codex's exact column-zero glyph and
  structure, preventing ordinary user prompts and indented/quoted text from
  matching.
- Interactive menus are read from the current viewport only, never historical
  scrollback. The exact title, complete explanatory text, numbered rows,
  selected marker, and footer must match, and the footer must be the final
  non-empty line.
- After Down, the watcher re-captures the screen and confirms `Keep waiting`
  before Enter. It latches the handled menu until the menu disappears.
- The ordinary event/recovery path rechecks that no selection menu owns the
  keyboard immediately before it sends `Continue`.
- Retry/completion events first seen while a pane mode owns the keyboard are
  retained for at most 30 seconds. After mode exits, the watcher sends only if
  that exact event is still the current visible terminal state and the composer
  is empty. Manual `Continue`, later output, a new event, resize, menu, disable,
  or timeout cancels the deferred action.
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

v0.1.5 is tested with Codex CLI 0.144.5, plus isolated tmux integrations
using a native fake-Codex process. Codex UI wording and layout may change in
later releases; unknown layouts are ignored rather than matched loosely.
macOS, Homebrew/standalone Codex binaries, localized UI text, and non-Linux
process inspection are not currently supported.

## TPM installation

With [TPM](https://github.com/tmux-plugins/tpm), explicitly enable the watcher
and add the plugin before TPM's own `run` line:

```tmux
set -g @codex-auto-continue on
# Optional; normal-completion continuation defaults off:
# set -g @codex-auto-continue-worked on
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
tmux set-option -g @codex-auto-continue-worked off
tmux set-option -g @codex-auto-continue-worked on
```

Logs contain pane/session identifiers and action kinds, not pane contents, and
are stored at `~/.cache/tmux-codex-auto-continue.log`. A separate tmux socket
(`tmux -L name`) needs its own watcher process and global options.

## Update and uninstall

Re-run the pinned v0.1.5 installer to update. It replaces only the verified
watcher process for the default tmux socket; it does not restart the tmux
server or any pane. Existing configuration files retain their explicit choice;
new configurations leave `Worked for` continuation off until you add:

```tmux
set -g @codex-auto-continue-worked on
```

After manually replacing the executable, reload it safely with:

```sh
~/.local/bin/tmux-codex-auto-continue \
  --socket "$(tmux display-message -p '#{socket_path}')" --restart
```

To remove a curl installation:

```sh
curl -fsSL https://raw.githubusercontent.com/yeahdongcn/tmux-codex-auto-continue/v0.1.5/uninstall.sh | sh
```

The uninstaller disables both options, removes only the marked config block
and installed executable, and never stops the tmux server. A currently idle
watcher exits when that tmux server exits.

For TPM, remove the plugin line and press `prefix` + `alt` + `u` (TPM's clean
command), or remove its plugin directory manually.

## Development

```sh
python3 bin/tmux-codex-auto-continue --self-test
python3 tests/worked_integration.py
python3 tests/install_integration.py
python3 -m py_compile bin/tmux-codex-auto-continue
python3 -m py_compile tests/worked_integration.py
python3 -m py_compile tests/install_integration.py
ruff check bin/tmux-codex-auto-continue
ruff check tests/worked_integration.py
ruff check tests/install_integration.py
shellcheck install.sh uninstall.sh tmux-codex-auto-continue.tmux
```

The built-in tests cover error and completion signatures, three-item and
two-item menu parsing, selected rows, and quoted/stale prompt rejection. The
integration test uses an isolated tmux server and a native fake-Codex process
to verify opt-in gating, `Worked for` submission, quoted-line rejection,
bounded pane-mode recovery, manual-recovery deduplication, and watcher-only
restart. The installer integration verifies that fresh configurations keep
`Worked for` continuation off and upgrades preserve existing choices.
Separately, the safety-menu path was exercised against an isolated native
fake-Codex process to verify Down+Enter, Enter-only, and no repeated keys.

Security reports should follow [SECURITY.md](SECURITY.md).

## License

[MIT](LICENSE)
