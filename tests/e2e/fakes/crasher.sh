#!/usr/bin/env bash
set -euo pipefail
TASK="$1"; AGENT="$2"
$ALE_BIN claim --task "$TASK" --agent "$AGENT"
$ALE_BIN heartbeat --task "$TASK" --agent "$AGENT" --step "half way"
exit 0
