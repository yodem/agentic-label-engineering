"""Pure aggregation for Jev shadow evidence. Statistics never change a decision."""

from __future__ import annotations

from collections import defaultdict

from .. import decisions as DECISIONS


BANDS = ("inside", "outside", "missing")


def _decision(row):
    return row.get("decision") or row.get("field")


def _choice(row):
    return row.get("choice", row.get("value"))


def band_of(vote: dict) -> str:
    """Place a vote relative to the uncertain band (see ``ale.decisions``).

    Evidence votes carry an explicit ``uncertain`` flag (a consulted Noul in
    [0.35, 0.65]); Choice votes are inside the band when confidence < 0.5.
    A vote with no choice, or no usable confidence, is ``missing``.
    """
    if _choice(vote) is None:
        return "missing"
    flag = vote.get("uncertain")
    if isinstance(flag, bool):
        return "inside" if flag else "outside"
    confidence = vote.get("confidence")
    if not DECISIONS._finite(confidence):
        return "missing"
    return "inside" if DECISIONS.choice_uncertain(confidence) else "outside"


def _median(values):
    values = sorted(values)
    if not values:
        return None
    middle = len(values) // 2
    if len(values) % 2:
        return values[middle]
    return (values[middle - 1] + values[middle]) / 2.0


def _key(row):
    return (row.get("run_id") or row.get("plan_id") or "", row.get("task_id"), _decision(row))


def summarize_shadow(votes: list, outcomes, adjudications, bar: dict, accepted_tasks=None) -> dict:
    """Summarize shadow votes per decision.

    ``outcomes`` and ``adjudications`` are lists of event rows (or dicts keyed
    by ``(run_id, task_id, decision)`` or ``(task_id, decision)``). Agreement
    compares each vote's choice with the recorded outcome of the same case.
    """
    grouped = defaultdict(list)
    for vote in votes or []:
        if _decision(vote):
            grouped[_key(vote)].append(vote)

    outcome_map = {}
    if isinstance(outcomes, dict):
        for key, value in outcomes.items():
            outcome_map[key] = value.get("choice", value.get("value")) if isinstance(value, dict) else value
    else:
        for row in outcomes or []:
            outcome_map[_key(row)] = _choice(row)

    # The latest adjudication of a case is its truth: an explicit `ale adjudicate`
    # written after the automatic one at acceptance replaces it.
    adjudicated_value = {}
    if isinstance(adjudications, dict):
        for key, value in adjudications.items():
            adjudicated_value[key] = value.get("choice", value.get("value")) if isinstance(value, dict) else value
    else:
        for row in adjudications or []:
            adjudicated_value[_key(row)] = _choice(row)
    adjudicated_keys = set(adjudicated_value)
    track_pending = accepted_tasks is not None
    label_decisions = {"role", "sub", "phase", "model_tier", "risk", "effort", "locality"}
    accepted_tasks = set(accepted_tasks or [])
    pending_by_decision = defaultdict(set)
    if track_pending:
        for key in grouped:
            run_id, task_id, decision = key
            if decision not in label_decisions or (
                    task_id not in accepted_tasks and (run_id, task_id) not in accepted_tasks):
                continue
            if key not in adjudicated_keys and (task_id, decision) not in adjudicated_keys:
                pending_by_decision[decision].add(key)

    by_decision = {}

    def item_for(decision):
        return by_decision.setdefault(decision, {
            "vote_count": 0, "adjudicated_count": 0, "disagreement_count": 0,
            "_agree": {band: [0, 0] for band in BANDS}, "_latency": [],
            "uncertain_band": {band: 0 for band in BANDS}})

    for key, items in sorted(grouped.items(), key=lambda pair: tuple(str(part) for part in pair[0])):
        run_id, task_id, decision = key
        item = item_for(decision)
        expected = outcome_map.get(key, outcome_map.get((task_id, decision)))
        item["vote_count"] += len(items)
        adjudicated_key = key if key in adjudicated_keys else (task_id, decision)
        if adjudicated_key in adjudicated_keys:
            expected = adjudicated_value[adjudicated_key]
            item["adjudicated_count"] += 1
            if _choice(items[-1]) != expected:
                item["disagreement_count"] += 1
        for vote in items:
            band = band_of(vote)
            item["uncertain_band"][band] += 1
            if expected is not None:
                item["_agree"][band][1] += 1
                item["_agree"][band][0] += 1 if _choice(vote) == expected else 0
            # Median over individual Jev calls: an evidence vote lists the calls it
            # made (shared answers are counted once); a Choice vote is one call.
            per_call = vote.get("calls_latency_ms")
            if isinstance(per_call, list):
                item["_latency"].extend(value for value in per_call if DECISIONS._finite(value))
            elif DECISIONS._finite(vote.get("latency_ms")):
                item["_latency"].append(vote["latency_ms"])

    # Option-order instability: the same case voted under different option orders.
    for (run_id, task_id, decision), items in grouped.items():
        orders = {tuple(vote.get("options") or []) for vote in items}
        if len(orders) > 1:
            item = item_for(decision)
            item["instability_total"] = item.get("instability_total", 0) + 1
            if len({_choice(vote) for vote in items}) > 1:
                item["instability_cases"] = item.get("instability_cases", 0) + 1

    minimum = int((bar or {}).get("min_cases", 100))
    threshold = (bar or {}).get("min_agreement")
    max_instability = (bar or {}).get("max_instability", .10)
    result = {}
    for decision, item in sorted(by_decision.items()):
        agree = item.pop("_agree")
        latencies = item.pop("_latency")

        def ratio(bands):
            hits = sum(agree[band][0] for band in bands)
            total = sum(agree[band][1] for band in bands)
            return (hits / float(total)) if total else None

        agreement = ratio(BANDS)
        instability = (item.pop("instability_cases", 0) / float(item.pop("instability_total"))
                       if item.get("instability_total") else 0.0)
        item.pop("instability_total", None)
        item.update({
            "agreement": agreement,
            "agreement_inside_band": ratio(("inside",)),
            "agreement_outside_band": ratio(("outside",)),
            "latency_ms_median": _median(latencies),
            "grey_zone": bool(DECISIONS.DECISIONS.get(decision, {}).get("grey_zone")),
            "progress": {"adjudicated": item["adjudicated_count"], "required": minimum,
                         "fraction": min(1.0, item["adjudicated_count"] / float(minimum))},
            "instability": instability,
        })
        pending = len(pending_by_decision.get(decision, ()))
        if track_pending and decision in label_decisions:
            item["pending_adjudication"] = pending
        item["bar_met"] = (item["adjudicated_count"] >= minimum and agreement is not None
                           and threshold is not None and agreement >= threshold
                           and instability <= max_instability and pending == 0)
        result[decision] = item
    return {"decisions": result,
            "bar": {"min_cases": minimum, "min_agreement": threshold, "max_instability": max_instability},
            "band": {"noul_uncertain": [DECISIONS.NOUL_UNCERTAIN_LOW, DECISIONS.NOUL_UNCERTAIN_HIGH],
                     "choice_uncertain_below": DECISIONS.CHOICE_UNCERTAIN_BELOW},
            "cases": len(grouped)}
