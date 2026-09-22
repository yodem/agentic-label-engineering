from __future__ import annotations

import copy
import os
import stat
from typing import List, Optional, Tuple

from .judge import options_for
from .merge import merge
from .rules import rule_votes

FIELDS = ("role", "model_tier", "risk", "effort")
OPTIONAL_FIELDS = ("sub", "phase")


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
                if not stat.S_ISREG(os.stat(candidate).st_mode):
                    return "", "spec_path is not a regular file: %s" % spec_path
            except OSError:
                return "", "spec_path unreadable: %s" % spec_path
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

    for field in OPTIONAL_FIELDS:
        planner_value = draft["labels"].get(field)
        if field == "sub" and planner_value is not None:
            role_subs = roster["vocab"].get("sub", {}).get(draft["labels"]["role"], {})
            if (planner_value not in role_subs and
                    planner_value not in roster["vocab"].get("cross_sub", [])):
                planner_value = None
        planner_vote = {"field": field, "value": planner_value, "by": "planner",
                        "confidence": None, "detail": {}}
        field_votes = [planner_vote]
        for vote in rvotes:
            if vote["field"] != field:
                continue
            rule_vote = dict(vote)
            if field == "sub" and rule_vote["value"] is not None:
                rule_role, sub = rule_vote["value"].split("/", 1)
                role_subs = roster["vocab"].get("sub", {}).get(rule_role, {})
                cross_subs = roster["vocab"].get("cross_sub", [])
                if sub not in role_subs and sub not in cross_subs:
                    rule_vote["value"] = None
                else:
                    rule_vote["value"] = sub
            field_votes.append(rule_vote)

        fired = [vote for vote in field_votes if vote["by"].startswith("rule:") and vote["value"] is not None]
        if planner_value is not None or fired:
            if planner_value is None and fired:
                fired_values = {vote["value"] for vote in fired}
                if len(fired_values) == 1:
                    winner = fired[0]
                    merged = {"value": winner["value"], "by": winner["by"],
                              "confidence": 1.0, "conflict": False}
                else:
                    merged = {"value": None, "by": "planner", "confidence": None,
                              "conflict": True}
            else:
                merged = merge(field, field_votes, modes.get(field, "off"), threshold)
            merged_by_field[field] = merged
            if merged["value"] is not None or field in draft["labels"]:
                provenance[field] = {
                    "by": merged["by"],
                    "confidence": merged["confidence"],
                    "conflict": merged["conflict"],
                    "votes": [{"by": vote["by"], "value": vote["value"],
                               "confidence": vote["confidence"]} for vote in field_votes],
                }

    final = copy.deepcopy(draft)
    for field in FIELDS:
        final["labels"][field] = merged_by_field[field]["value"]
    for field in OPTIONAL_FIELDS:
        if field in merged_by_field:
            value = merged_by_field[field]["value"]
            if value is not None or field in draft["labels"]:
                final["labels"][field] = value
            else:
                final["labels"].pop(field, None)
    final["labels"]["lane"] = draft["labels"]["lane"]
    final["provenance"] = provenance

    return final, all_votes
