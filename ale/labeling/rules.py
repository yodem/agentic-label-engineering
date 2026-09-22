from __future__ import annotations

import re
from typing import List


class RuleError(Exception):
    pass


def rule_votes(draft: dict, text: str, roster: dict) -> List[dict]:
    votes: List[dict] = []
    haystack = "%s\n%s" % (draft.get("title", ""), text or "")
    paths = [str(path).replace("\\", "/").lower() for path in
             list(draft["context"].get("pointers", [])) + list(draft["context"].get("allowed_paths", []))]
    for index, rule in enumerate(roster.get("rules", [])):
        field, value, when = rule["field"], rule["value"], rule["when"]
        target_field = field
        if value.startswith("sub:"):
            target_field, value = "sub", value[len("sub:"):]
        elif value.startswith("phase:"):
            target_field, value = "phase", value[len("phase:"):]
        vocabulary = roster["vocab"].get(field, {})
        if target_field == "sub":
            vocabulary = roster["vocab"].get("sub", {})
        elif target_field == "phase":
            vocabulary = roster["vocab"].get("phase", [])
        valid_values = set(vocabulary) if isinstance(vocabulary, dict) else set(vocabulary)
        if target_field == "sub":
            valid_values = {"%s/%s" % (role, sub) for role, subs in vocabulary.items() for sub in subs}
            valid_values.update(roster["vocab"].get("cross_sub", []))
        if value not in valid_values:
            raise RuleError("rule %d: %r is not in the %s vocabulary" % (index, value, target_field))
        if not when:
            raise RuleError("rule %d: empty condition" % index)
        fired, detail = True, {}
        if "path_prefix" in when:
            detail["path_prefix"] = when["path_prefix"]
            prefix = when["path_prefix"]
            if prefix.startswith("re:"):
                try:
                    fired = fired and any(re.search(prefix[3:], path) is not None for path in paths)
                except re.error as exc:
                    raise RuleError("rule %d: bad path regex: %s" % (index, exc))
            else:
                fired = fired and any(p.startswith(prefix) for p in paths)
        if "keyword" in when:
            detail["keyword"] = when["keyword"]
            try:
                fired = fired and re.search(when["keyword"], haystack) is not None
            except re.error as exc:
                raise RuleError("rule %d: bad regex: %s" % (index, exc))
        votes.append({"field": target_field, "value": value if fired else None, "by": "rule:%d" % index,
                      "confidence": 1.0 if fired else None, "detail": detail})
    return votes
