from __future__ import annotations

import json
from collections import Counter
from typing import Iterable, Set


# Claude Code 2.1.278 transcript facts: assistant lines contain message.id,
# message.model and message.usage token fields; isSidechain marks subagents.
# Streamed chunks repeat ids, so the last assistant line for each id wins.
_USAGE_FIELDS = {
    "input_tokens": "input_tokens",
    "output_tokens": "output_tokens",
    "cache_read_tokens": "cache_read_input_tokens",
    "cache_creation_tokens": "cache_creation_input_tokens",
}


def _number(value) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def sum_usage(lines: Iterable[str], already_counted: Set[str]) -> dict:
    latest = {}
    for line in lines:
        try:
            row = json.loads(line)
            message = row.get("message") if isinstance(row, dict) else None
            if not isinstance(row, dict) or row.get("type") != "assistant" or not isinstance(message, dict):
                continue
            mid = message.get("id")
            usage = message.get("usage")
            if not isinstance(mid, str) or not isinstance(usage, dict):
                continue
            latest[mid] = (message, bool(row.get("isSidechain")))
        except (TypeError, ValueError, AttributeError):
            continue

    totals = {name: 0 for name in _USAGE_FIELDS}
    sidechain = {name: 0 for name in _USAGE_FIELDS}
    models = Counter()
    message_ids = []
    for mid, (message, is_sidechain) in latest.items():
        if mid in already_counted:
            continue
        usage = message["usage"]
        values = {name: _number(usage.get(field)) for name, field in _USAGE_FIELDS.items()}
        for name, value in values.items():
            totals[name] += value
            if is_sidechain:
                sidechain[name] += value
        if isinstance(message.get("model"), str):
            models[message["model"]] += 1
        message_ids.append(mid)
        already_counted.add(mid)
    result = dict(totals)
    result.update({"model": models.most_common(1)[0][0] if models else None,
                   "message_ids": message_ids, "sidechain": sidechain})
    return result
