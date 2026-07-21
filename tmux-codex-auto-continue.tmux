#!/usr/bin/env bash
set -euo pipefail

CURRENT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCRIPT="$CURRENT_DIR/bin/tmux-codex-auto-continue"
KEY="$(tmux show-option -gqv @codex-auto-continue-key)"
KEY="${KEY:-A}"

toggle_command="\"$SCRIPT\" --socket '#{socket_path}' --toggle"
start_command="umask 077; mkdir -p \"$HOME/.cache\"; \"$SCRIPT\" --socket '#{socket_path}' --restart >> \"$HOME/.cache/tmux-codex-auto-continue.log\" 2>&1"

tmux bind-key "$KEY" run-shell -b "$toggle_command"
tmux run-shell -b "$start_command"
