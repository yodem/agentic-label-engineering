from __future__ import annotations

import collections
from typing import Dict, List, Optional, Tuple

from .metrics import accuracy, cohen_kappa, confusion_pairs, coverage_table, percentile, position_sensitivity

DEFAULT_FIELDS = ("role", "model_tier", "risk", "effort")
DEFAULT_KINDS = ("short", "full")


def _fmt(value) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, float):
        return "%.3f" % value
    return str(value)


def _rows_by_id(rows: List[dict]) -> Dict[str, dict]:
    return {str(row.get("id")): row for row in rows if row.get("id") is not None}


def _pred_map(rows: List[dict], field: str) -> Dict[str, Optional[str]]:
    return {str(row["id"]): row.get("choice") for row in rows if row.get("field") == field and row.get("perm", 0) == 0}


def _pred_conf_map(rows: List[dict], field: str) -> Dict[str, Tuple[Optional[str], Optional[float]]]:
    return {
        str(row["id"]): (row.get("choice"), row.get("confidence"))
        for row in rows
        if row.get("field") == field and row.get("perm", 0) == 0
    }


def _labeler_map(rows: List[dict], field: str) -> Dict[str, Optional[str]]:
    return {str(row["id"]): row.get(field) for row in rows if row.get("id") is not None and field in row}


def _kappa_against(a: Dict[str, Optional[str]], b: Dict[str, str]) -> Optional[float]:
    ids = sorted(i for i in a if i in b and a[i] is not None)
    return cohen_kappa([str(a[i]) for i in ids], [str(b[i]) for i in ids])


def _gold_for_kind(gold: Dict[str, str], corpus_by_id: Dict[str, dict], kind: str) -> Dict[str, str]:
    return {task_id: value for task_id, value in gold.items() if (corpus_by_id.get(task_id) or {}).get("kind") == kind}


def _counts(rows: List[dict], key: str) -> Dict[str, int]:
    counter = collections.Counter(str(row.get(key)) for row in rows if row.get(key))
    return dict(sorted(counter.items()))


def _promotion_line(field: str, kind: str, acc: dict, cov_rows: List[dict], promotion: dict) -> str:
    min_cases = int(promotion.get("min_cases", 0))
    min_coverage = float(promotion.get("min_coverage", 0.0))
    n = acc["n"]
    coverage = cov_rows[0]["coverage"] if cov_rows else None
    if n < min_cases:
        return "BELOW BAR %s %s: n %d < min_cases %d" % (field, kind, n, min_cases)
    if coverage is None or coverage < min_coverage:
        return "BELOW BAR %s %s: coverage %s < min_coverage %s" % (field, kind, _fmt(coverage), _fmt(min_coverage))
    return "MEETS BAR %s %s: n %d, coverage %s" % (field, kind, n, _fmt(coverage))


def render(stats: dict) -> str:
    roster = stats.get("roster") or {}
    promotion = roster.get("promotion") or {}
    tolerance = promotion.get("tolerance")
    fields = list(stats.get("fields") or DEFAULT_FIELDS)
    kinds = list(stats.get("kinds") or DEFAULT_KINDS)
    corpus = list(stats.get("corpus") or [])
    corpus_by_id = _rows_by_id(corpus)
    gold_bundle = stats.get("gold") or {}
    gold_all = gold_bundle.get("gold") or {}
    judge_rows = list(stats.get("judge") or [])
    labeler_a = list(stats.get("labeler_a") or [])
    labeler_b = list(stats.get("labeler_b") or [])
    arms = stats.get("arms") or {}

    lines = [
        "# Evaluation report",
        "",
        "Tolerance: %s" % _fmt(tolerance),
        "Promotion bar: min_cases %s, min_coverage %s" % (_fmt(promotion.get("min_cases")), _fmt(promotion.get("min_coverage"))),
        "",
        "## Results",
    ]

    for field in fields:
        field_gold = gold_all.get(field) or {}
        lines.extend(["", "### %s" % field])
        for kind in kinds:
            kind_gold = _gold_for_kind(field_gold, corpus_by_id, kind)
            pred = _pred_map(judge_rows, field)
            pred_conf = _pred_conf_map(judge_rows, field)
            acc = accuracy(pred, kind_gold)
            cov = coverage_table(pred_conf, kind_gold, [0.5, 0.75, 0.9])
            a_map = _labeler_map(labeler_a, field)
            lines.extend([
                "",
                "#### %s" % kind,
                "n: %d" % acc["n"],
                "accuracy: %s" % _fmt(acc["accuracy"]),
                "kappa against labeler A: %s" % _fmt(_kappa_against(pred, {i: str(v) for i, v in a_map.items() if i in kind_gold})),
                "coverage: " + ", ".join("%s=%s/%s acc=%s" % (_fmt(r["threshold"]), r["covered"], r["n"], _fmt(r["accuracy_covered"])) for r in cov),
            ])
            pairs = confusion_pairs(pred, kind_gold)[:5]
            if pairs:
                lines.append("confusion: " + ", ".join("%s -> %s: %d" % (p["gold"], p["predicted"], p["count"]) for p in pairs))
            else:
                lines.append("confusion: none")

            relevant_rows = [r for r in judge_rows if r.get("field") == field and r.get("perm", 0) == 0 and r.get("id") in kind_gold]
            abstains = sum(1 for r in relevant_rows if r.get("choice") is None)
            errors = _counts([r for r in relevant_rows if r.get("error")], "error")
            lines.append("abstain: %d" % abstains)
            lines.append("errors: " + (", ".join("%s: %d" % (k, v) for k, v in errors.items()) if errors else "none"))
            lines.append(_promotion_line(field, kind, acc, cov, promotion))

            for arm_name, arm_rows in sorted(arms.items()):
                arm_map = _labeler_map(list(arm_rows), field)
                arm_acc = accuracy(arm_map, kind_gold)
                lines.append("%s accuracy: %s" % (arm_name, _fmt(arm_acc["accuracy"])))
                lines.append("%s kappa: %s" % (arm_name, _fmt(_kappa_against(arm_map, kind_gold))))

        latencies = [r.get("latency_ms") for r in judge_rows if r.get("field") == field and isinstance(r.get("latency_ms"), (int, float))]
        ps = position_sensitivity([r for r in judge_rows if r.get("field") == field])
        lines.extend([
            "Latency p50: %s" % _fmt(percentile(latencies, 50)),
            "Latency p95: %s" % _fmt(percentile(latencies, 95)),
            "Calls: %d" % len([r for r in judge_rows if r.get("field") == field]),
            "Position sensitivity: %s" % _fmt(ps.get("rate")),
        ])

    lines.extend(["", "## Inter-labeler kappa"])
    for field in fields:
        a_map = _labeler_map(labeler_a, field)
        b_map = _labeler_map(labeler_b, field)
        ids = sorted(i for i in a_map if i in b_map)
        lines.append("%s: %s" % (field, _fmt(cohen_kappa([str(a_map[i]) for i in ids], [str(b_map[i]) for i in ids]))))

    return "\n".join(lines) + "\n"
