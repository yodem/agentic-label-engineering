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

    def add(field: str, value: str, matched: bool, detail: dict) -> None:
        votes.append({"field": field, "value": value if matched else None,
                      "by": "rule:%d" % len(votes), "confidence": 1.0 if matched else None,
                      "detail": detail})

    path_rules = [
        ("sub", "frontend/css", lambda p: p.endswith((".css", ".scss")) or p.rsplit("/", 1)[-1].startswith("tailwind"), "style files"),
        ("sub", "frontend/components", lambda p: p.endswith((".tsx", ".jsx")) and "components/" in p, "components/ TSX or JSX"),
        ("sub", "devops/ci", lambda p: p.rsplit("/", 1)[-1] == "dockerfile" or
         (p.endswith((".yml", ".yaml")) and (".github/" in p or "ci/" in p)), "Dockerfile or CI configuration"),
        ("sub", "devops/infra", lambda p: any(part in ("terraform", "k8s", "helm") for part in p.split("/")), "infrastructure path"),
        ("sub", "backend/data", lambda p: "migrations/" in p or p.endswith(".sql"), "migration or SQL path"),
        ("sub", "backend/api", lambda p: any(term in p for term in ("openapi", "routes", "controllers")), "API path"),
        ("sub", "test/unit", lambda p: "tests/unit/" in p or "test/unit/" in p, "unit test directory"),
        ("sub", "test/integration", lambda p: "tests/integration/" in p or "test/integration/" in p, "integration test directory"),
        ("sub", "test/e2e", lambda p: "tests/e2e/" in p or "test/e2e/" in p, "end-to-end test directory"),
    ]
    for field, value, matches, description in path_rules:
        add(field, value, any(matches(path) for path in paths), {"path_rule": description})

    text_rules = [
        ("phase", "review", r"(?i)\breview\b"),
        ("phase", "deploy", r"(?i)\b(deploy|release)\b"),
        ("sub", "debugging", r"(?i)\b(debug|fix|investigate)\b"),
        ("sub", "frontend/design", r"(?i)\b(design|mock|figma)\b"),
        ("phase", "design", r"(?i)\b(design|mock|figma)\b"),
    ]
    for field, value, pattern in text_rules:
        add(field, value, re.search(pattern, haystack) is not None, {"keyword": pattern})

    for index, rule in enumerate(roster.get("rules", [])):
        field, value, when = rule["field"], rule["value"], rule["when"]
        vocabulary = roster["vocab"].get(field, {})
        valid_values = set(vocabulary) if isinstance(vocabulary, dict) else set(vocabulary)
        if field == "sub":
            valid_values = {"%s/%s" % (role, sub) for role, subs in vocabulary.items() for sub in subs}
            valid_values.update(roster["vocab"].get("cross_sub", []))
        if value not in valid_values:
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
