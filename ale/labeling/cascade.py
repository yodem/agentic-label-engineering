from __future__ import annotations

import copy
from typing import List, Optional, Tuple

from .judge import options_for
from .merge import merge
from .rules import rule_votes

FIELDS = ("role", "model_tier", "risk", "effort")


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
