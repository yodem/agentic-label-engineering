#!/usr/bin/env bash
set -euo pipefail
TASK="$1"; AGENT="$2"
$ALE_BIN claim --task "$TASK" --agent "$AGENT"
$ALE_BIN heartbeat --task "$TASK" --agent "$AGENT" --step "starting"
mkdir -p out && echo ok > "out/$TASK.txt"
$ALE_BIN heartbeat --task "$TASK" --agent "$AGENT" --step "wrote out/$TASK.txt" --files "out/$TASK.txt"
$ALE_BIN submit --task "$TASK" --agent "$AGENT" --summary "wrote out/$TASK.txt"
