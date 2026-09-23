"""Build Jev shadow votes: one Choice per classification decision, or narrow
evidence Nouls with the decision computed in code (``ale.decisions``).

Nothing here writes events or changes a label. Callers record the returned
vote as an additive ``shadow_vote`` and never apply it.
"""

from __future__ import annotations

import json
from typing import Dict, Optional

from .. import decisions as DECISIONS
from .judge import key_of, options_for


def question(roster: dict, key: str) -> Optional[str]:
    text = ((roster.get("judge") or {}).get("questions") or {}).get(key)
    return text if isinstance(text, str) and text.strip() else None


def state_json(**fields) -> str:
    """Structured, filtered state for the judge: named fields only, empty ones dropped."""
    return json.dumps({key: value for key, value in fields.items() if value not in (None, "", [])},
                      sort_keys=True)


def _vote(decision: str, options, choice, confidence, model, latency_ms, uncertain, **extra) -> dict:
    vote = {"decision": decision, "options": list(options), "choice": choice,
            "confidence": confidence, "model": model, "latency_ms": latency_ms,
            "uncertain": uncertain}
    vote.update(extra)
    return vote


def choice_vote(judge, decision: str, roster: dict, state: str, role: Optional[str] = None) -> dict:
    """Ask one Choice for a classification decision (role, sub, phase, ...)."""
    key = DECISIONS.CHOICE_QUESTION_KEYS[decision]
    try:
        offered = options_for(decision, roster, role=role)
    except (KeyError, ValueError) as exc:
        return _vote(decision, [], None, None, None, None, None, error="no_options: %s" % str(exc)[:80])
    keys = [key_of(option) for option in offered]
    text = question(roster, key)
    if text is None:
        return _vote(decision, keys, None, None, None, None, None, error="missing_question")
    try:
        answer = dict(judge.ask(decision, text, offered, state))
    except Exception as exc:
        answer = {"value": None, "confidence": None, "detail": {"error": str(exc)[:120]}}
    return from_choice_answer(decision, keys, answer)


def from_choice_answer(decision: str, keys, answer: dict) -> dict:
    detail = answer.get("detail") or {}
    confidence = answer.get("confidence")
    uncertain = None if confidence is None else DECISIONS.choice_uncertain(confidence)
    extra = {"error": str(detail["error"])[:120]} if detail.get("error") else {}
    if isinstance(detail.get("evidence"), dict):
        extra["answers"] = dict(detail["evidence"])
        extra["rule"] = detail.get("rule")
        uncertain = detail.get("uncertain")
    return _vote(decision, keys, answer.get("value"), confidence, answer.get("model"),
                 detail.get("latency_ms"), uncertain, **extra)


def evidence_vote(judge, decision: str, roster: dict, state: str, facts: Dict[str, object],
                  cache: Optional[dict] = None) -> dict:
    """Ask the decision's evidence Nouls and compute the decision with its rule table.

    ``cache`` shares answers for the same state across decisions (for example
    ``large_change`` feeds both lane and needs_monitor), so each question is
    asked once per state.
    """
    cache = {} if cache is None else cache
    answers: Dict[str, Optional[float]] = {}
    errors: Dict[str, str] = {}
    calls = []  # latency of each Jev call this vote made; cached answers were paid for earlier
    model = None
    for key in DECISIONS.EVIDENCE_QUESTIONS[decision]:
        if key not in cache:
            text = question(roster, key)
            if text is None:
                cache[key] = {"p": None, "model": None, "detail": {"error": "missing_question"}}
            else:
                try:
                    cache[key] = dict(judge.noul(key, text, state))
                except Exception as exc:
                    cache[key] = {"p": None, "model": None, "detail": {"error": str(exc)[:80]}}
                latency = (cache[key].get("detail") or {}).get("latency_ms")
                if DECISIONS._finite(latency):
                    calls.append(latency)
        answer = cache[key]
        answers[key] = answer.get("p")
        detail = answer.get("detail") or {}
        if detail.get("error"):
            errors[key] = str(detail["error"])[:60]
        model = model or answer.get("model")
    computed = DECISIONS.compute_decision(decision, answers, facts)
    extra = {"answers": answers, "rule": computed["rule"], "facts": dict(facts), "calls_latency_ms": calls}
    if errors:
        extra["error"] = json.dumps(errors, sort_keys=True)[:200]
    return _vote(decision, DECISIONS.FIXED_OPTIONS[decision], computed["choice"], computed["confidence"],
                 model, sum(calls) if calls else None, computed["uncertain"], **extra)
