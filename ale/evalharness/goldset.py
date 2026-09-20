from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from .metrics import cohen_kappa

FIELDS = ("role", "model_tier", "risk", "effort")


def _by_id(rows: List[dict]) -> Dict[str, dict]:
    out = {}
    for row in rows:
        task_id = row.get("id")
        if isinstance(task_id, str):
            out[task_id] = row
    return out


def build(labels_a: List[dict], labels_b: List[dict], rulings: List[dict]) -> dict:
    by_a = _by_id(labels_a)
    by_b = _by_id(labels_b)
    ruling_values: Dict[Tuple[str, str], object] = {}
    for ruling in rulings:
        task_id = ruling.get("id")
        field = ruling.get("field")
        if isinstance(task_id, str) and isinstance(field, str) and field in FIELDS:
            ruling_values[(task_id, field)] = ruling.get("value")

    gold: Dict[str, Dict[str, object]] = {}
    basis: Dict[str, Dict[str, str]] = {}
    kappa: Dict[str, Optional[float]] = {}
    agreement: Dict[str, float] = {}
    missing: Dict[str, int] = {}
    queue = []

    all_ids = sorted(set(by_a) | set(by_b))
    for field in FIELDS:
        gold[field] = {}
        basis[field] = {}
        common_a = []
        common_b = []
        common_total = 0
        agreed = 0
        missing_count = 0

        for task_id in all_ids:
            a_row = by_a.get(task_id)
            b_row = by_b.get(task_id)
            if a_row is None or b_row is None or field not in a_row or field not in b_row:
                missing_count += 1
                continue

            a_value = a_row[field]
            b_value = b_row[field]
            common_a.append(str(a_value))
            common_b.append(str(b_value))
            common_total += 1

            ruling_key = (task_id, field)
            if ruling_key in ruling_values:
                gold[field][task_id] = ruling_values[ruling_key]
                basis[field][task_id] = "human"
            elif a_value == b_value:
                gold[field][task_id] = a_value
                basis[field][task_id] = "agree"
                agreed += 1
            else:
                queue.append({"id": task_id, "field": field, "a": a_value, "b": b_value})

        missing[field] = missing_count
        agreement[field] = (float(agreed) / float(common_total)) if common_total else 0.0
        kappa[field] = cohen_kappa(common_a, common_b)

    queue.sort(key=lambda item: (item["id"], item["field"]))
    return {"gold": gold, "queue": queue, "kappa": kappa, "agreement": agreement, "missing": missing, "basis": basis}
