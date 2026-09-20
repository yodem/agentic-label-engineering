from __future__ import annotations

import re
from typing import List


class RuleError(Exception):
    pass


def rule_votes(draft: dict, text: str, roster: dict) -> List[dict]:
    votes: List[dict] = []
    haystack = "%s\n%s" % (draft.get("title", ""), text or "")
    paths = list(draft["context"].get("pointers", [])) + list(draft["context"].get("allowed_paths", []))
    for index, rule in enumerate(roster.get("rules", [])):
        field, value, when = rule["field"], rule["value"], rule["when"]
        if value not in roster["vocab"][field]:
            raise RuleError("rule %d: %r is not in the %s vocabulary" % (index, value, field))
        if not when:
            raise RuleError("rule %d: empty condition" % index)
        fired, detail = True, {}
        if "path_prefix" in when:
            detail["path_prefix"] = when["path_prefix"]
            fired = fired and any(p.startswith(when["path_prefix"]) for p in paths)
        if "keyword" in when:
            detail["keyword"] = when["keyword"]
            try:
                fired = fired and re.search(when["keyword"], haystack) is not None
            except re.error as exc:
                raise RuleError("rule %d: bad regex: %s" % (index, exc))
        votes.append({"field": field, "value": value if fired else None, "by": "rule:%d" % index,
                      "confidence": 1.0 if fired else None, "detail": detail})
    return votes
