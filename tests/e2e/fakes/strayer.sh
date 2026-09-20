#!/usr/bin/env bash
set -euo pipefail
TASK="$1"; AGENT="$2"
$ALE_BIN claim --task "$TASK" --agent "$AGENT"
mkdir -p out && echo ok > "out/$TASK.txt"
echo "drive-by edit" > UNRELATED.md
$ALE_BIN submit --task "$TASK" --agent "$AGENT" --summary "wrote out/$TASK.txt"
