#!/bin/sh
set -eu
umask 077

BIN_DIR=${TMUX_CODEX_AUTO_CONTINUE_BIN_DIR:-"$HOME/.local/bin"}
TMUX_CONF=${TMUX_CODEX_AUTO_CONTINUE_TMUX_CONF:-"$HOME/.tmux.conf"}
start_marker='# >>> tmux-codex-auto-continue >>>'
end_marker='# <<< tmux-codex-auto-continue <<<'

if command -v tmux >/dev/null 2>&1; then
    tmux set-option -g @codex-auto-continue off 2>/dev/null || true
fi

if [ -f "$TMUX_CONF" ] && grep -Fq "$start_marker" "$TMUX_CONF"; then
    tmp=$(mktemp "${TMPDIR:-/tmp}/tmux-codex-uninstall.XXXXXX")
    trap 'rm -f "$tmp"' EXIT HUP INT TERM
    awk -v start="$start_marker" -v end="$end_marker" '
        $0 == start { skipping = 1; next }
        $0 == end { skipping = 0; next }
        !skipping { print }
    ' "$TMUX_CONF" > "$tmp"
    chmod --reference="$TMUX_CONF" "$tmp"
    mv "$tmp" "$TMUX_CONF"
    printf 'Removed the managed block from %s\n' "$TMUX_CONF"
fi

if [ -f "$BIN_DIR/tmux-codex-auto-continue" ]; then
    rm -f "$BIN_DIR/tmux-codex-auto-continue"
    printf 'Removed %s/tmux-codex-auto-continue\n' "$BIN_DIR"
fi

printf '%s\n' 'The current watcher is disabled; it exits with the tmux server.'
