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


def _gold_for_basis(gold: Dict[str, str], basis: Dict[str, str], wanted: str) -> Dict[str, str]:
    return {task_id: value for task_id, value in gold.items() if basis.get(task_id) == wanted}


def _counts(rows: List[dict], key: str) -> Dict[str, int]:
    counter = collections.Counter(str(row.get(key)) for row in rows if row.get(key))
    return dict(sorted(counter.items()))


def _coverage_line(pred_conf: Dict[str, Tuple[Optional[str], Optional[float]]], gold: Dict[str, str]) -> str:
    cov = coverage_table(pred_conf, gold, [0.5, 0.75, 0.9])
    return "coverage: " + ", ".join(
        "%s=%s/%s acc=%s" % (_fmt(r["threshold"]), r["covered"], r["n"], _fmt(r["accuracy_covered"]))
        for r in cov
    )


def _score_lines(name: str, pred: Dict[str, Optional[str]], gold: Dict[str, str]) -> List[str]:
    acc = accuracy(pred, gold)
    return [
        "%s n: %d" % (name, acc["n"]),
        "%s accuracy: %s" % (name, _fmt(acc["accuracy"])),
    ]


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


def _promotion_verdict_line(field: str, judge_acc: dict, incumbent_acc: Optional[dict],
                            incumbent_name: Optional[str], tolerance, cov_row: Optional[dict],
                            sensitivity: dict, promotion: dict, max_instability: float) -> str:
    min_cases = int(promotion.get("min_cases", 0))
    min_coverage = float(promotion.get("min_coverage", 0.0))
    failures = []

    if judge_acc["n"] < min_cases:
        failures.append("n %d < min_cases %d" % (judge_acc["n"], min_cases))

    accuracy_bar = None
    if not incumbent_name:
        failures.append("no incumbent arm")
    elif incumbent_acc is None:
        failures.append("incumbent arm %s not found" % incumbent_name)
    elif judge_acc["accuracy"] is None or incumbent_acc["accuracy"] is None or tolerance is None:
        failures.append("judge %s vs %s %s - tolerance %s" % (
            _fmt(judge_acc["accuracy"]), incumbent_name, _fmt(incumbent_acc["accuracy"]), _fmt(tolerance)))
    else:
        accuracy_bar = incumbent_acc["accuracy"] - float(tolerance)
        if judge_acc["accuracy"] < accuracy_bar:
            failures.append("judge %s < %s %s - tolerance %s" % (
                _fmt(judge_acc["accuracy"]), incumbent_name, _fmt(incumbent_acc["accuracy"]), _fmt(tolerance)))

    if cov_row is None or cov_row.get("coverage") is None or cov_row["coverage"] < min_coverage:
        coverage = cov_row.get("coverage") if cov_row else None
        threshold = cov_row.get("threshold") if cov_row else None
        failures.append("coverage at threshold %s %s < min_coverage %s" % (
            _fmt(threshold), _fmt(coverage), _fmt(min_coverage)))

    if accuracy_bar is not None:
        covered_accuracy = cov_row.get("accuracy_covered") if cov_row else None
        if covered_accuracy is None or covered_accuracy < accuracy_bar:
            failures.append("covered accuracy at threshold %s %s < %s %s - tolerance %s" % (
                _fmt(cov_row.get("threshold") if cov_row else None), _fmt(covered_accuracy),
                incumbent_name, _fmt(incumbent_acc["accuracy"]), _fmt(tolerance)))

    instability = sensitivity.get("rate")
    if instability is None:
        failures.append("instability not measured")
    elif instability > max_instability:
        failures.append("instability %s > max_instability %s" % (_fmt(instability), _fmt(max_instability)))

    if failures:
        return "BELOW BAR %s full gold: %s" % (field, "; ".join(failures))
    return "MEETS BAR %s full gold: n %d, judge %s >= %s %s - tolerance %s, coverage at threshold %s %s, covered accuracy %s, instability %s <= max_instability %s" % (
        field, judge_acc["n"], _fmt(judge_acc["accuracy"]), incumbent_name, _fmt(incumbent_acc["accuracy"]),
        _fmt(tolerance), _fmt(cov_row["threshold"]), _fmt(cov_row["coverage"]), _fmt(cov_row["accuracy_covered"]),
        _fmt(instability), _fmt(max_instability))


def render(stats: dict) -> str:
    roster = stats.get("roster") or {}
    promotion = roster.get("promotion") or {}
    tolerance = promotion.get("tolerance")
    judge_threshold = (roster.get("judge") or {}).get("threshold")
    max_instability = float(stats.get("max_instability", promotion.get("max_instability", 0.10)))
    fields = list(stats.get("fields") or DEFAULT_FIELDS)
    for field in (roster.get("vocab") or {}):
        if field != "lane" and field not in fields:
            fields.append(field)
    kinds = list(stats.get("kinds") or DEFAULT_KINDS)
    corpus = list(stats.get("corpus") or [])
    corpus_by_id = _rows_by_id(corpus)
    gold_bundle = stats.get("gold") or {}
    gold_all = gold_bundle.get("gold") or {}
    judge_rows = list(stats.get("judge") or [])
    labeler_a = list(stats.get("labeler_a") or [])
    labeler_b = list(stats.get("labeler_b") or [])
    arms = stats.get("arms") or {}
    incumbent_name = stats.get("incumbent_arm")

    lines = [
        "# Evaluation report",
        "",
        "Tolerance: %s" % _fmt(tolerance),
        "Promotion bar: all criteria must pass: n >= min_cases %s; incumbent arm selected and judge full-gold accuracy >= incumbent full-gold accuracy minus tolerance %s; coverage at judge threshold %s >= min_coverage %s; covered accuracy at judge threshold %s >= incumbent full-gold accuracy minus tolerance; option-order instability <= max_instability %s" % (
            _fmt(promotion.get("min_cases")), _fmt(tolerance), _fmt(judge_threshold),
            _fmt(promotion.get("min_coverage")), _fmt(judge_threshold), _fmt(max_instability)),
        "Split diagnostics: min_cases %s, min_coverage %s" % (_fmt(promotion.get("min_cases")), _fmt(promotion.get("min_coverage"))),
        "",
        "## Promotion",
    ]

    for field in fields:
        field_gold = gold_all.get(field) or {}
        pred = _pred_map(judge_rows, field)
        judge_full_acc = accuracy(pred, field_gold)
        incumbent_acc = None
        if incumbent_name and incumbent_name in arms:
            incumbent_acc = accuracy(_labeler_map(list(arms[incumbent_name]), field), field_gold)
        pred_conf = _pred_conf_map(judge_rows, field)
        cov_row = coverage_table(pred_conf, field_gold, [judge_threshold])[0] if judge_threshold is not None else None
        sensitivity = position_sensitivity([r for r in judge_rows if r.get("field") == field])
        lines.append(_promotion_verdict_line(field, judge_full_acc, incumbent_acc, incumbent_name, tolerance,
                                             cov_row, sensitivity, promotion, max_instability))

    lines.extend([
        "",
        "## Results",
    ])

    for field in fields:
        field_gold = gold_all.get(field) or {}
        field_basis = (gold_bundle.get("basis") or {}).get(field) or {}
        human_gold = _gold_for_basis(field_gold, field_basis, "human")
        pred = _pred_map(judge_rows, field)
        pred_conf = _pred_conf_map(judge_rows, field)
        a_map = _labeler_map(labeler_a, field)
        b_map = _labeler_map(labeler_b, field)
        lines.extend(["", "### %s" % field])
        lines.extend(_score_lines("judge full gold", pred, field_gold))
        lines.extend(_score_lines("judge human-basis subset", pred, human_gold))
        lines.append(_coverage_line(pred_conf, field_gold))
        lines.append(_coverage_line(pred_conf, human_gold).replace("coverage:", "human-basis coverage:", 1))
        lines.extend(_score_lines("labeler A human-basis subset", a_map, human_gold))
        if labeler_b:
            lines.extend(_score_lines("labeler B human-basis subset", b_map, human_gold))
        lines.append("informational: the human subset is the A/B disagreement set")
        for arm_name, arm_rows in sorted(arms.items()):
            arm_map = _labeler_map(list(arm_rows), field)
            lines.extend(_score_lines("%s full gold" % arm_name, arm_map, field_gold))
            lines.extend(_score_lines("%s human-basis subset" % arm_name, arm_map, human_gold))
        for kind in kinds:
            kind_gold = _gold_for_kind(field_gold, corpus_by_id, kind)
            acc = accuracy(pred, kind_gold)
            cov = coverage_table(pred_conf, kind_gold, [0.5, 0.75, 0.9])
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
        field_kappa = (gold_bundle.get("kappa") or {}).get(field)
        field_agreement = (gold_bundle.get("agreement") or {}).get(field)
        if field_kappa is None and labeler_b:
            a_map = _labeler_map(labeler_a, field)
            b_map = _labeler_map(labeler_b, field)
            ids = sorted(i for i in a_map if i in b_map)
            field_kappa = cohen_kappa([str(a_map[i]) for i in ids], [str(b_map[i]) for i in ids])
        lines.append("%s: kappa %s, agreement %s" % (field, _fmt(field_kappa), _fmt(field_agreement)))

    return "\n".join(lines) + "\n"
