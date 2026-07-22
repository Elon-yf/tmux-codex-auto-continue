#!/bin/sh
set -eu
umask 077

VERSION=v0.1.3
REPO_RAW_BASE=${TMUX_CODEX_AUTO_CONTINUE_RAW_BASE:-"https://raw.githubusercontent.com/yeahdongcn/tmux-codex-auto-continue/$VERSION"}
WATCHER_SHA256=5a38caafad87c7ae70c3e8e35a0d18d8a7c5325b4cd70014d51c932dd3d51b23
BIN_DIR=${TMUX_CODEX_AUTO_CONTINUE_BIN_DIR:-"$HOME/.local/bin"}
TMUX_CONF=${TMUX_CODEX_AUTO_CONTINUE_TMUX_CONF:-"$HOME/.tmux.conf"}
INSTALL_CONFIG=1

usage() {
    printf '%s\n' 'Usage: install.sh [--no-config]'
    printf '%s\n' ''
    printf '%s\n' 'Environment overrides:'
    printf '%s\n' '  TMUX_CODEX_AUTO_CONTINUE_BIN_DIR   install directory (default: ~/.local/bin)'
    printf '%s\n' '  TMUX_CODEX_AUTO_CONTINUE_TMUX_CONF tmux config path (default: ~/.tmux.conf)'
    printf '%s\n' '  TMUX_CODEX_AUTO_CONTINUE_RAW_BASE  raw file base URL for mirrors/forks'
}

for arg in "$@"; do
    case "$arg" in
        --no-config) INSTALL_CONFIG=0 ;;
        -h|--help) usage; exit 0 ;;
        *) printf 'unknown option: %s\n' "$arg" >&2; usage >&2; exit 2 ;;
    esac
done

if [ "$(uname -s)" != Linux ] || [ ! -r /proc/self/stat ]; then
    printf '%s\n' 'tmux-codex-auto-continue currently requires Linux with /proc.' >&2
    exit 1
fi
for command in curl install python3 sha256sum tmux; do
    if ! command -v "$command" >/dev/null 2>&1; then
        printf 'required command not found: %s\n' "$command" >&2
        exit 1
    fi
done
if ! python3 -c 'import sys; raise SystemExit(sys.version_info < (3, 10))'; then
    printf '%s\n' 'Python 3.10 or newer is required.' >&2
    exit 1
fi

mkdir -p "$BIN_DIR" "$HOME/.cache"
tmp=$(mktemp "${TMPDIR:-/tmp}/tmux-codex-auto-continue.XXXXXX")
trap 'rm -f "$tmp"' EXIT HUP INT TERM
curl --proto '=https' --tlsv1.2 --fail --location --silent --show-error \
    "$REPO_RAW_BASE/bin/tmux-codex-auto-continue" \
    --output "$tmp"
printf '%s  %s\n' "$WATCHER_SHA256" "$tmp" | sha256sum --check --status
python3 "$tmp" --self-test
install -m 0755 "$tmp" "$BIN_DIR/tmux-codex-auto-continue"

if [ "$INSTALL_CONFIG" -eq 1 ]; then
    mkdir -p "$(dirname "$TMUX_CONF")"
    touch "$TMUX_CONF"
    start_marker='# >>> tmux-codex-auto-continue >>>'
    end_marker='# <<< tmux-codex-auto-continue <<<'
    if ! grep -Fq "$start_marker" "$TMUX_CONF" 2>/dev/null \
        && ! grep -Fq 'tmux-codex-auto-continue' "$TMUX_CONF" 2>/dev/null; then
        script="$BIN_DIR/tmux-codex-auto-continue"
        {
            printf '\n%s\n' "$start_marker"
            printf '%s\n' '# Auto-continue recognized Codex completion/retry/wait prompts.'
            printf '%s\n' 'set -goq @codex-auto-continue on'
            printf '%s\n' 'set -goq @codex-auto-continue-worked on'
            printf "bind-key A run-shell -b '\"%s\" --socket \"#{socket_path}\" --toggle'\n" "$script"
            printf "run-shell -b 'mkdir -p \"%s/.cache\" && \"%s\" --socket \"#{socket_path}\" >> \"%s/.cache/tmux-codex-auto-continue.log\" 2>&1'\n" "$HOME" "$script" "$HOME"
            printf '%s\n' "$end_marker"
        } >> "$TMUX_CONF"
        printf 'Added an idempotent configuration block to %s\n' "$TMUX_CONF"
    else
        printf '%s\n' 'Existing tmux-codex-auto-continue configuration detected; left it unchanged.'
    fi

    if tmux source-file "$TMUX_CONF" 2>/dev/null; then
        socket=$(tmux display-message -p '#{socket_path}')
        "$BIN_DIR/tmux-codex-auto-continue" \
            --socket "$socket" --restart
        printf 'Reloaded %s and refreshed the watcher without restarting tmux.\n' "$TMUX_CONF"
    else
        printf 'Start tmux (or source %s) to activate the watcher.\n' "$TMUX_CONF"
    fi
fi

printf 'Installed %s/tmux-codex-auto-continue\n' "$BIN_DIR"
printf '%s\n' 'Use tmux prefix+A to toggle it on the current tmux server.'
