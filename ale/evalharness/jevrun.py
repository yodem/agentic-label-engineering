from __future__ import annotations

import random
from typing import Iterator, List, Optional, Set, Tuple

from ale.labeling.judge import options_for


def _sampled_ids(corpus: List[dict], seed: int, sensitivity_sample: int) -> Set[str]:
    if sensitivity_sample <= 0:
        return set()
    rows = list(corpus)
    random.Random(seed).shuffle(rows)
    return set(str(row.get("id")) for row in rows[:sensitivity_sample] if row.get("id") is not None)


def _order_for(roster: dict, field: str, seed: int, task_id: str, perm: int) -> Optional[List[str]]:
    if perm == 0:
        return None
    order = list(roster["vocab"][field].keys())
    random.Random("%s|%s|%d" % (seed, task_id, perm)).shuffle(order)
    return order


def run(corpus: List[dict], roster: dict, judge, fields, perms: int, seed: int,
        sensitivity_sample: int, done: Optional[Set[Tuple[str, str, int]]] = None) -> Iterator[dict]:
    done = done or set()
    sampled = _sampled_ids(corpus, seed, sensitivity_sample)
    max_perms = max(1, int(perms))
    for item in corpus:
        task_id = str(item.get("id"))
        state = str(item.get("text") or "")
        for field in fields:
            perm_values = [0]
            if task_id in sampled:
                perm_values.extend(range(1, max_perms))
            for perm in perm_values:
                triple = (task_id, field, perm)
                if triple in done:
                    continue
                options = options_for(field, roster, order=_order_for(roster, field, seed, task_id, perm))
                vote = judge.ask(field, roster["judge"]["questions"][field], options, state)
                detail = vote.get("detail") or {}
                yield {
                    "id": task_id,
                    "field": field,
                    "perm": perm,
                    "choice": vote.get("value"),
                    "confidence": vote.get("confidence"),
                    "probabilities": detail.get("probabilities") or {},
                    "latency_ms": detail.get("latency_ms"),
                    "error": detail.get("error"),
                }
