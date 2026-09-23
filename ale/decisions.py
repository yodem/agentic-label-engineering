"""The stable, auditable decision vocabulary used by ALE and its Jev shadow judge.

This module is pure: no I/O, no subprocesses, no clock. It declares

* the decision registry (twelve stable IDs; ``executor`` is deterministic and
  therefore never judged, leaving eleven judged decisions),
* which judged decisions are asked as one Choice and which are computed in code
  from narrow evidence questions (Jev book Rule 9.9: ask for evidence, compute
  the verdict in code),
* the rule tables that turn evidence answers plus code-computed facts into a
  decision, and
* the uncertain band used for routing and statistics.

Question text never lives here. Every question comes from the roster's
``judge.questions`` under the key named below; a missing question means the
decision abstains, never that a generic question is asked.
"""

from __future__ import annotations

from typing import Dict, Iterable, List, Optional


DECISIONS = {
    "lane": {"options_source": "labels.lane enum", "fires_at": "planner", "firing_site": "planner",
             "requires": ["lane_reason"], "deterministic": True, "kind": "deterministic"},
    "role": {"options_source": "vocab.role", "fires_at": "bake", "firing_site": "bake", "kind": "choice"},
    "sub": {"options_source": "vocab.sub", "fires_at": "bake", "firing_site": "bake", "kind": "choice"},
    "phase": {"options_source": "vocab.phase", "fires_at": "bake", "firing_site": "bake", "kind": "choice"},
    "model_tier": {"options_source": "vocab.model_tier", "fires_at": "bake", "firing_site": "bake",
                   "kind": "choice"},
    "risk": {"options_source": "vocab.risk", "fires_at": "bake", "firing_site": "bake", "kind": "choice"},
    "effort": {"options_source": "vocab.effort", "fires_at": "bake", "firing_site": "bake", "kind": "choice",
               "grey_zone": True},
    "locality": {"options_source": "vocab.locality (any|local)", "fires_at": "bake", "firing_site": "bake",
                 "kind": "evidence"},
    "executor": {"options_source": "routing[selected tier]", "fires_at": "dispatch", "firing_site": "dispatch",
                 "deterministic": True, "kind": "deterministic"},
    "rejection_action": {"options_source": "fixed fix|reopen|escalate", "fires_at": "verify rejection",
                         "firing_site": "verify rejection (run loop)", "kind": "evidence"},
    "monitor_verdict": {"options_source": "event.verdict", "fires_at": "monitor breach",
                        "firing_site": "monitor breach", "kind": "evidence"},
    "needs_monitor": {"options_source": "fixed yes|no", "fires_at": "init-run", "firing_site": "init-run",
                      "kind": "evidence"},
}

FIXED_OPTIONS = {
    "lane": ["inline", "workflow", "pane"],
    "locality": ["any", "local"],
    "rejection_action": ["fix", "reopen", "escalate"],
    "monitor_verdict": ["continue", "nudge", "fix", "escalate"],
    "needs_monitor": ["yes", "no"],
}

# Choice decisions ask one question from judge.questions[<decision>].
CHOICE_QUESTION_KEYS = {
    "role": "role", "sub": "sub", "phase": "phase",
    "model_tier": "model_tier", "risk": "risk", "effort": "effort",
}

# Evidence decisions ask one Noul per key, each from judge.questions[<key>].
EVIDENCE_QUESTIONS = {
    "lane": [],
    "needs_monitor": ["large_change", "needs_person", "external_side_effects"],
    "locality": ["locality"],
    "rejection_action": ["rejection_environment", "rejection_spec_conflict", "rejection_needs_human"],
    "monitor_verdict": ["monitor_reports_specific_failure", "monitor_agent_blocked", "monitor_unsafe"],
}

# Facts are computed from labels, the plan graph, or events. They are never asked.
EVIDENCE_FACTS = {
    "lane": ["effort", "role", "independent_tasks", "unattended"],
    "needs_monitor": ["risk"],
    "locality": [],
    "rejection_action": ["fix_count", "is_fix_task"],
    "monitor_verdict": ["monitor_wrote_files", "attempts_exhausted", "breach"],
}

LIVENESS_BREACHES = ("stuck", "lease_expired", "overrun", "input_required")

# Rule tables, evaluated top to bottom; the first matching row decides.
# "yes(k)" means the Noul probability for evidence key k is at least 0.5.
RULE_TABLES = {
    "lane": [
        ("effort_large", "effort is the largest roster effort (L, or XL if present) -> pane"),
        ("unattended", "unattended -> pane"),
        ("verification_role", "role in {test, review} -> workflow"),
        ("independent_tasks", "independent_tasks >= 2 -> workflow"),
        ("default", "otherwise -> inline"),
    ],
    "needs_monitor": [
        ("high_risk", "risk == high -> yes"),
        ("large_unattended", "yes(large_change) and no(needs_person) -> yes"),
        ("external_side_effects", "yes(external_side_effects) -> yes"),
        ("default", "otherwise -> no"),
    ],
    "locality": [
        ("needs_planner_machine", "yes(locality) -> local"),
        ("default", "otherwise -> any"),
    ],
    "rejection_action": [
        ("fixes_exhausted", "is_fix_task or fix_count >= 2 -> escalate"),
        ("needs_human", "yes(rejection_needs_human) -> escalate"),
        ("environment", "yes(rejection_environment) -> reopen"),
        ("spec_conflict", "yes(rejection_spec_conflict) -> reopen"),
        ("default", "otherwise -> fix"),
    ],
    "monitor_verdict": [
        ("monitor_wrote_files", "monitor_wrote_files -> escalate"),
        ("attempts_exhausted", "attempts_exhausted -> escalate"),
        ("unsafe", "yes(monitor_unsafe) -> escalate"),
        ("defect", "yes(monitor_reports_specific_failure) -> fix"),
        ("agent_blocked", "yes(monitor_agent_blocked) -> nudge"),
        ("liveness_breach", "breach in {stuck, lease_expired, overrun, input_required} -> nudge"),
        ("default", "otherwise -> continue"),
    ],
}

# Jev book (CandleKeep cmu8rrf5k3y4dln0ie7g8qwtk) Rule 9.9: a Noul in
# [0.35, 0.65] or a Choice/Score confidence below 0.5 is uncertain.
NOUL_UNCERTAIN_LOW = 0.35
NOUL_UNCERTAIN_HIGH = 0.65
CHOICE_UNCERTAIN_BELOW = 0.5


def decision_ids() -> List[str]:
    """All twelve registered IDs, including the deterministic executor."""
    return list(DECISIONS)


def is_judged(decision: str) -> bool:
    return decision in DECISIONS and not DECISIONS[decision].get("deterministic", False)


def judged_decision_ids() -> List[str]:
    """The eleven decisions Jev shadows; executor routing is deterministic from tier."""
    return [decision for decision in DECISIONS if is_judged(decision)]


def question_keys(decision: str) -> List[str]:
    if decision in CHOICE_QUESTION_KEYS:
        return [CHOICE_QUESTION_KEYS[decision]]
    return list(EVIDENCE_QUESTIONS.get(decision, []))


def required_question_keys() -> List[str]:
    keys: List[str] = []
    for decision in judged_decision_ids():
        for key in question_keys(decision):
            if key not in keys:
                keys.append(key)
    return keys


def options_for_decision(decision: str, roster: dict, *, role: str = None,
                         model_tier: str = None, label: dict = None) -> List[str]:
    if decision not in DECISIONS:
        raise KeyError(decision)
    if decision in FIXED_OPTIONS:
        return list(FIXED_OPTIONS[decision])
    vocab = roster.get("vocab", {})
    if decision == "role":
        return sorted(vocab.get("role", {}))
    if decision == "model_tier":
        return sorted(vocab.get("model_tier", {}))
    if decision == "risk":
        return sorted(vocab.get("risk", {}))
    if decision == "effort":
        return sorted(vocab.get("effort", {}))
    if decision == "phase":
        return list(vocab.get("phase", []))
    if decision == "sub":
        selected = role or ((label or {}).get("labels") or {}).get("role")
        values = list((vocab.get("sub", {}).get(selected, {}) or {}).keys())
        values.extend(vocab.get("cross_sub", []))
        return sorted(set(values))
    if decision == "executor":
        rows = [row for row in roster.get("routing", [])
                if not model_tier or row.get("model_tier") == model_tier]
        return sorted(set(row.get("executor") for row in rows if row.get("executor")))
    return []


def _finite(value) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    return value == value and value not in (float("inf"), float("-inf"))


def noul_uncertain(probability) -> bool:
    return (_finite(probability)
            and NOUL_UNCERTAIN_LOW <= float(probability) <= NOUL_UNCERTAIN_HIGH)


def choice_uncertain(confidence) -> bool:
    return _finite(confidence) and float(confidence) < CHOICE_UNCERTAIN_BELOW


class _MissingEvidence(Exception):
    pass


def compute_decision(decision: str, answers: Dict[str, Optional[float]], facts: Dict[str, object]) -> dict:
    """Apply the decision's rule table to evidence probabilities and facts.

    Returns ``{"choice", "rule", "consulted", "uncertain", "confidence"}``.
    A missing or non-finite answer that the table needs yields ``choice`` None
    with rule ``missing_evidence``: the table refuses, it never fails open.
    ``confidence`` is the smallest ``max(p, 1 - p)`` over the consulted
    answers (1.0 when only facts decided).
    """
    if decision not in RULE_TABLES:
        raise KeyError(decision)
    consulted: List[str] = []

    def yes(key: str) -> bool:
        value = answers.get(key)
        if not _finite(value) or not 0.0 <= float(value) <= 1.0:
            raise _MissingEvidence(key)
        if key not in consulted:
            consulted.append(key)
        return float(value) >= 0.5

    try:
        choice, rule = _apply_table(decision, yes, facts)
    except _MissingEvidence as exc:
        return {"choice": None, "rule": "missing_evidence", "missing": str(exc),
                "consulted": consulted, "uncertain": False, "confidence": None}
    margins = [max(float(answers[key]), 1.0 - float(answers[key])) for key in consulted]
    return {"choice": choice, "rule": rule, "consulted": consulted,
            "uncertain": any(noul_uncertain(answers[key]) for key in consulted),
            "confidence": min(margins) if margins else 1.0}


def _apply_table(decision: str, yes, facts: Dict[str, object]):
    if decision == "lane":
        if facts.get("effort") in ("L", "XL"):
            return "pane", "effort_large"
        if facts.get("unattended") is True:
            return "pane", "unattended"
        if facts.get("role") in ("test", "review"):
            return "workflow", "verification_role"
        if int(facts.get("independent_tasks") or 0) >= 2:
            return "workflow", "independent_tasks"
        return "inline", "default"
    if decision == "needs_monitor":
        if facts.get("risk") == "high":
            return "yes", "high_risk"
        if yes("large_change") and not yes("needs_person"):
            return "yes", "large_unattended"
        if yes("external_side_effects"):
            return "yes", "external_side_effects"
        return "no", "default"
    if decision == "locality":
        return ("local", "needs_planner_machine") if yes("locality") else ("any", "default")
    if decision == "rejection_action":
        if facts.get("is_fix_task") or int(facts.get("fix_count") or 0) >= 2:
            return "escalate", "fixes_exhausted"
        if yes("rejection_needs_human"):
            return "escalate", "needs_human"
        if yes("rejection_environment"):
            return "reopen", "environment"
        if yes("rejection_spec_conflict"):
            return "reopen", "spec_conflict"
        return "fix", "default"
    if decision == "monitor_verdict":
        if facts.get("monitor_wrote_files"):
            return "escalate", "monitor_wrote_files"
        if facts.get("attempts_exhausted"):
            return "escalate", "attempts_exhausted"
        if yes("monitor_unsafe"):
            return "escalate", "unsafe"
        if yes("monitor_reports_specific_failure"):
            return "fix", "defect"
        if yes("monitor_agent_blocked"):
            return "nudge", "agent_blocked"
        if facts.get("breach") in LIVENESS_BREACHES:
            return "nudge", "liveness_breach"
        return "continue", "default"
    raise KeyError(decision)


def independent_task_count(task_id: str, labels: Dict[str, dict]) -> int:
    """Tasks neither reachable from nor reaching ``task_id`` (flow.mjs lane rule 4)."""
    def deps(tid: str) -> Iterable[str]:
        return ((labels.get(tid) or {}).get("context") or {}).get("depends_on") or []

    def reaches(start: str, target: str) -> bool:
        pending, seen = list(deps(start)), set()
        while pending:
            current = pending.pop(0)
            if current == target:
                return True
            if current in seen:
                continue
            seen.add(current)
            pending.extend(deps(current))
        return False

    return sum(1 for other in labels
               if other != task_id and not reaches(task_id, other) and not reaches(other, task_id))


def shadow_vote(decision: str, task_id: str, options: Iterable[str], choice,
                confidence=None, model=None, latency_ms=None) -> dict:
    """Create the additive payload; it deliberately has no authority fields."""
    return {"decision": decision, "task_id": task_id, "options": list(options),
            "choice": choice, "confidence": confidence, "model": model,
            "latency_ms": latency_ms}


def outcome(decision: str, task_id: str, choice, *, source: str = "planner") -> dict:
    return {"decision": decision, "task_id": task_id, "choice": choice,
            "authority": "lead", "additive": True, "source": source}
