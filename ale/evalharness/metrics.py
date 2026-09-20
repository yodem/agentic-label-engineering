from __future__ import annotations

import collections
from typing import Dict, List, Optional, Sequence, Tuple


def accuracy(pred: Dict[str, Optional[str]], gold: Dict[str, str]) -> dict:
    ids = [i for i in pred if i in gold]
    correct = sum(1 for i in ids if pred[i] == gold[i])
    return {"n": len(ids), "correct": correct, "accuracy": (correct / len(ids)) if ids else None}


def coverage_table(pred: Dict[str, Tuple[Optional[str], Optional[float]]], gold: Dict[str, str],
                   thresholds: Sequence[float]) -> List[dict]:
    ids = [i for i in pred if i in gold]
    rows = []
    for t in thresholds:
        covered = [i for i in ids if pred[i][0] not in (None, "other") and pred[i][1] is not None and pred[i][1] >= t]
        correct = sum(1 for i in covered if pred[i][0] == gold[i])
        rows.append({"threshold": t, "n": len(ids), "covered": len(covered),
                     "coverage": (len(covered) / len(ids)) if ids else None, "correct": correct,
                     "accuracy_covered": (correct / len(covered)) if covered else None})
    return rows


def cohen_kappa(a: Sequence[str], b: Sequence[str]) -> Optional[float]:
    if len(a) != len(b):
        raise ValueError("rater sequences differ in length")
    n = len(a)
    if n == 0:
        return None
    observed = sum(1 for x, y in zip(a, b) if x == y) / n
    ca, cb = collections.Counter(a), collections.Counter(b)
    expected = sum((ca[k] / n) * (cb[k] / n) for k in set(ca) | set(cb))
    if expected == 1.0:
        return None
    return (observed - expected) / (1.0 - expected)


def confusion_pairs(pred: Dict[str, Optional[str]], gold: Dict[str, str]) -> List[dict]:
    counts = collections.Counter((gold[i], pred[i]) for i in pred if i in gold and pred[i] != gold[i])
    ordered = sorted(counts.items(), key=lambda kv: (-kv[1], str(kv[0][0]), str(kv[0][1])))
    return [{"gold": g, "predicted": p, "count": c} for (g, p), c in ordered]


def percentile(values: Sequence[float], q: float) -> Optional[float]:
    if not values:
        return None
    s = sorted(values)
    if len(s) == 1:
        return s[0]
    pos = (len(s) - 1) * q / 100.0
    lo = int(pos)
    hi = min(lo + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (pos - lo)


def position_sensitivity(rows: List[dict]) -> dict:
    by_item: Dict[str, set] = collections.defaultdict(set)
    perms: Dict[str, int] = collections.Counter()
    for r in rows:
        by_item[r["id"]].add(r.get("choice"))
        perms[r["id"]] += 1
    repeated = [i for i in by_item if perms[i] > 1]
    unstable = sum(1 for i in repeated if len(by_item[i]) > 1)
    return {"items_with_repeats": len(repeated), "unstable": unstable,
            "rate": (unstable / len(repeated)) if repeated else None}
