from __future__ import annotations

import copy
import os
from typing import List, Optional, Tuple

from .judge import options_for
from .merge import merge
from .rules import rule_votes

FIELDS = ("role", "model_tier", "risk", "effort")


def _contained(candidate: str, root: str) -> bool:
    real_candidate = os.path.realpath(candidate)
    real_root = os.path.realpath(root)
    if real_candidate == real_root:
        return True
    try:
        common = os.path.commonpath([real_candidate, real_root])
    except ValueError:
        return False
    return common == real_root


def read_spec_text(spec_path: str, roots: List[str], limit: int = 4000) -> Tuple[str, Optional[str]]:
    try:
        if not spec_path:
            return "", None

        candidates = []
        for root in roots:
            candidate = spec_path if os.path.isabs(spec_path) else os.path.join(root, spec_path)
            candidates.append(candidate)

        found_but_outside = False
        for candidate in candidates:
            if not os.path.exists(candidate):
                continue
            if not any(_contained(candidate, root) for root in roots):
                found_but_outside = True
                continue
            try:
                with open(candidate, encoding="utf-8") as f:
                    return f.read()[:limit], None
            except (OSError, UnicodeDecodeError):
                return "", "spec_path unreadable: %s" % spec_path

        if found_but_outside:
            return "", "spec_path outside the project, not read: %s" % spec_path
        return "", None
    except Exception:
        return "", None


def _ask_judge(judge, field: str, roster: dict, state: str) -> dict:
    try:
        question = roster["judge"]["questions"][field]
        options = options_for(field, roster)
        vote = dict(judge.ask(field, question, options, state))
        vote["field"] = field
        return vote
    except Exception as exc:
        plugin = (roster.get("judge") or {}).get("plugin") or "command"
        return {"field": field, "value": None, "by": "judge:%s" % plugin, "confidence": None,
                "detail": {"error": str(exc)[:200]}}


def label_task(draft: dict, text: str, roster: dict, judge=None) -> Tuple[dict, List[dict]]:
    haystack = "%s\n%s" % (draft.get("title", ""), text or "")
    rvotes = rule_votes(draft, text, roster)
    threshold = roster["judge"]["threshold"]
    modes = roster["judge"]["modes"]

    all_votes: List[dict] = []
    merged_by_field = {}
    provenance = {"lane_reason": draft["provenance"]["lane_reason"]}

    for field in FIELDS:
        planner_vote = {"field": field, "value": draft["labels"][field], "by": "planner",
                         "confidence": None, "detail": {}}
        field_votes = [planner_vote] + [v for v in rvotes if v["field"] == field]
        mode = modes.get(field, "off")
        if judge is not None and mode != "off":
            field_votes.append(_ask_judge(judge, field, roster, haystack))

        merged = merge(field, field_votes, mode, threshold)
        merged_by_field[field] = merged
        provenance[field] = {
            "by": merged["by"],
            "confidence": merged["confidence"],
            "conflict": merged["conflict"],
            "votes": [{"by": v["by"], "value": v["value"], "confidence": v["confidence"]} for v in field_votes],
        }
        all_votes.extend(field_votes)

    final = copy.deepcopy(draft)
    for field in FIELDS:
        final["labels"][field] = merged_by_field[field]["value"]
    final["labels"]["lane"] = draft["labels"]["lane"]
    final["provenance"] = provenance

    return final, all_votes
