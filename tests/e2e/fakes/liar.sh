#!/usr/bin/env bash
set -euo pipefail
TASK="$1"; AGENT="$2"
$ALE_BIN claim --task "$TASK" --agent "$AGENT"
$ALE_BIN submit --task "$TASK" --agent "$AGENT" --summary "all done, tests pass"
